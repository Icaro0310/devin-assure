"""The canonical evidence model — one session shape for every surface.

An *evidence provider* reads one agent surface's native store (read-only)
and yields :class:`SessionEvidence`: normalized ``messages`` (the claim
and prompt source) plus ``tool_calls`` (the ground truth claims are
verified against). The audit pipeline consumes only this model — store
quirks live inside the providers, never in claims/verify/intent.

Providers shipped here:

- :mod:`devin_qa_pack.evidence.devin_cli` — Devin CLI ``sessions.db``.
- :mod:`devin_qa_pack.evidence.copilot_cli` — Copilot CLI
  ``session-state/<uuid>/events.jsonl`` (+ legacy flat ``*.jsonl``).
- :mod:`devin_qa_pack.evidence.codex_cli` — Codex CLI ``~/.codex``
  ``state_<N>.sqlite`` threads + ``rollout-*.jsonl`` sessions.
- :mod:`devin_qa_pack.evidence.vscode_chat` — VS Code ``chatSessions``
  ``*.json``/``*.jsonl`` stores.
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class EvidenceMessage:
    """One normalized message: the claim and prompt source.

    ``source_id`` is the surface's own node/message id when one exists
    (devin-cli ``message_nodes.node_id``); ``None`` leaves the claim to
    cite the positional index, which is all position-only surfaces have.
    """

    role: str  # "user" | "assistant" | "tool" | "system" (normalized)
    text: str
    ts_ms: float | None
    source_id: int | str | None = None


@dataclass(frozen=True)
class EvidenceToolCall:
    """One tool call with its decoded payload(s).

    ``payload`` is the call as issued, ``update_payload`` the latest
    recorded update (result, exit state) — the tool_call_json /
    tool_call_update_json pair on surfaces that split them. An empty
    ``payload`` with a ``None`` update means the row was absent or
    undecodable: still counted, so verify treats it as unreadable ground
    truth rather than absent.
    """

    tool_call_id: str | None
    kind: str  # provider-sniffed kind/event type — informational
    payload: dict[str, Any]
    update_payload: dict[str, Any] | None
    ts_ms: float | None


@dataclass
class SessionEvidence:
    """One session's metadata plus content, in the audit-ready shape.

    ``native_id`` is the surface's own session id; ``ref`` identifies the
    store the evidence came from (db path, events file). ``working_dir``
    is the real on-disk path when the store records one. ``warnings``
    collects non-fatal parse issues — a warning never fails the session.
    """

    surface: str
    native_id: str
    ref: str
    started_at: str | None
    working_dir: str | None
    messages: list[EvidenceMessage] = field(default_factory=list)
    tool_calls: list[EvidenceToolCall] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class EvidenceProvider(Protocol):
    """Read-only reader of one surface's native store."""

    surface: str

    def available(self, env: Mapping[str, str] | None = None) -> bool:
        """True when the surface's store exists to be read."""
        ...

    def sessions(
        self, env: Mapping[str, str] | None = None
    ) -> list[SessionEvidence]:
        """All sessions in the store (metadata + content); ``[]`` when absent."""
        ...


def normalize_role(role: Any) -> str:
    """Map any surface role spelling onto user|assistant|tool|system.

    Unknown roles become ``assistant`` — matching the historical rule
    that only user-role turns are excluded from claim scanning.
    """
    raw = str(role).strip().lower() if role is not None else ""
    if raw in ("user", "human"):
        return "user"
    if raw in ("tool", "function", "tool_call", "tool_result"):
        return "tool"
    if raw == "system":
        return "system"
    return "assistant"


def epoch_ms(value: Any) -> float | None:
    """Normalize a source timestamp to epoch ms (``None`` when absent).

    Numbers use the ``< 1e12`` discriminator: below it the unit is epoch
    *seconds* (×1000), at/above it is already milliseconds. Strings are
    parsed as ISO 8601, then as a plain number; anything else → ``None``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or value <= 0:
            return None
        return float(value * 1000) if value < 1e12 else float(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            dt = datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith("Z") else text
            )
        except ValueError:
            try:
                return epoch_ms(float(text))
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return float(int(dt.timestamp() * 1000))
    return None


def iso_from_ms(ts_ms: float | None) -> str | None:
    """Epoch ms → ISO-8601 ``Z`` string (``None`` stays ``None``)."""
    if ts_ms is None:
        return None
    return (
        datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open a source SQLite db read-only: ``mode=ro`` retried with
    ``immutable=1``; a constructed-but-failed handle is closed first."""
    exc: sqlite3.Error | None = None
    for query in ("mode=ro", "mode=ro&immutable=1"):
        try:
            con = sqlite3.connect(f"file:{path.as_posix()}?{query}", uri=True)
        except sqlite3.Error as error:
            exc = error
            continue
        try:
            con.row_factory = sqlite3.Row
            con.execute("SELECT 1").fetchone()  # force the lazy open
            return con
        except sqlite3.Error as error:
            exc = error
            con.close()
    raise exc if exc is not None else sqlite3.Error(f"{path}: unreadable")


def env_or_default(env: Mapping[str, str] | None) -> dict[str, str]:
    """``env`` as a plain dict, defaulting to ``os.environ``."""
    return dict(os.environ) if env is None else dict(env)


def _home(env: Mapping[str, str]) -> Path:
    return Path(env.get("HOME") or Path.home())


def copilot_roots(env: Mapping[str, str]) -> list[Path]:
    """Copilot roots in scan order: ``$COPILOT_DIR``, ``$COPILOT_HOME``,
    ``~/.copilot`` — every existing root is merged."""
    roots = [Path(env[v]) for v in ("COPILOT_DIR", "COPILOT_HOME") if env.get(v)]
    roots.append(_home(env) / ".copilot")
    return list(dict.fromkeys(roots))


def codex_root(env: Mapping[str, str]) -> Path:
    """The Codex home: ``$CODEX_HOME`` else ``~/.codex``."""
    return Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else (
        _home(env) / ".codex"
    )


def vscode_user_dirs(env: Mapping[str, str]) -> list[Path]:
    """Candidate ``<...>/User`` dirs holding ``chatSessions`` stores:
    ``<config>/<Variant>/User`` for both VS Code variants plus the remote
    ``~/.vscode-server/data/User``."""
    home = _home(env)
    if env.get("APPDATA"):
        base = Path(env["APPDATA"])
    elif os.name == "nt":
        base = home / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = home / ".config"
    return [
        *(base / variant / "User" for variant in ("Code", "Code - Insiders")),
        home / ".vscode-server" / "data" / "User",
    ]


class Surface:
    """The surface names the CLI/MCP accept (``AUTO`` = query all)."""

    DEVIN_CLI = "devin-cli"
    COPILOT_CLI = "copilot-cli"
    CODEX_CLI = "codex-cli"
    VSCODE_CHAT = "vscode-chat"
    AUTO = "auto"
    ALL = (DEVIN_CLI, COPILOT_CLI, CODEX_CLI, VSCODE_CHAT)
    CHOICES = (*ALL, AUTO)


_PROVIDER_MODULES = {
    Surface.DEVIN_CLI: "devin_cli",
    Surface.COPILOT_CLI: "copilot_cli",
    Surface.CODEX_CLI: "codex_cli",
    Surface.VSCODE_CHAT: "vscode_chat",
}
SURFACES = tuple(_PROVIDER_MODULES)


def provider_for(
    surface: str, sessions_db: str | Path | None = None
) -> EvidenceProvider:
    """The provider for one surface name (raises ``ValueError`` on typo).

    ``sessions_db`` only affects the devin-cli provider — the other
    surfaces discover their own stores.
    """
    import importlib

    module = _PROVIDER_MODULES.get(surface)
    if module is None:
        raise ValueError(f"unknown surface '{surface}'")
    mod = importlib.import_module(f"devin_qa_pack.evidence.{module}")
    cls = mod.PROVIDER_CLASS
    if surface == Surface.DEVIN_CLI:
        return cls(sessions_db)
    return cls()


def default_providers(
    sessions_db: str | Path | None = None,
) -> list[EvidenceProvider]:
    """One provider per known surface, in scan order (devin-cli first).

    ``sessions_db`` pins the devin-cli store explicitly; the other
    providers auto-detect from ``env`` at call time.
    """
    return [provider_for(surface, sessions_db) for surface in SURFACES]


def providers_for(
    surface: str, sessions_db: str | Path | None = None
) -> list[EvidenceProvider]:
    """Providers behind a ``--surface`` choice (``auto`` → all)."""
    if surface == Surface.AUTO:
        return default_providers(sessions_db)
    return [provider_for(surface, sessions_db)]


def collect_evidences(
    surface: str = Surface.AUTO,
    *,
    sessions_db: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[list[SessionEvidence], list[str]]:
    """``(evidences, warnings)`` across the selected surfaces.

    Discovery is tolerant: a surface whose store is absent contributes
    no sessions and no warning (source-not-found semantics); a store
    that exists but fails mid-read contributes a warning, never an
    exception.
    """
    env_map = env_or_default(env)
    evidences: list[SessionEvidence] = []
    warnings: list[str] = []
    for provider in providers_for(surface, sessions_db):
        try:
            if not provider.available(env_map):
                continue
            evidences.extend(provider.sessions(env_map))
        except Exception as exc:  # noqa: BLE001 - one bad store must not sink the audit
            warnings.append(
                f"{provider.surface}: cannot read store ({exc})"
            )
    return evidences, warnings


def match_evidence(
    evidences: list[SessionEvidence], query: str
) -> SessionEvidence | list[SessionEvidence] | None:
    """Resolve ``--session`` across surfaces: exact native id, exact
    ``surface:native`` id, or unique prefix within (optionally)
    ``surface:``-scoped candidates.

    Returns the single matching evidence, ``None`` when no candidate
    matches, or the *list* of candidates when the query is ambiguous —
    including several exact native-id hits on different surfaces, which
    must never resolve silently to the first one.
    """
    surface: str | None = None
    sid = query
    if ":" in query:
        head, _, tail = query.partition(":")
        if head in SURFACES:
            surface, sid = head, tail
    pool = [
        e
        for e in evidences
        if surface is None or e.surface == surface
    ]
    exact = [
        e
        for e in pool
        if e.native_id == sid
        or f"{e.surface}:{e.native_id}" == query
        or e.ref == query
    ]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        # Same native id retained across workspaces (or surfaces): the
        # canonical ref — which embeds the workspace hash — is still
        # unique, so a ref-prefix query disambiguates before we give up.
        refined = [e for e in exact if e.ref.startswith(query)]
        if len(refined) == 1:
            return refined[0]
        return exact  # genuinely ambiguous — never resolve silently
    matches = [
        e
        for e in pool
        if e.native_id.startswith(sid)
        or e.ref.startswith(query)
        or (surface is None
            and f"{e.surface}:{e.native_id}".startswith(query))
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches
    return None
