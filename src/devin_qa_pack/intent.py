"""QA-4 — prompt intent vs. diff coverage.

Compares what the *user asked for* (the session's first user
``chat_message``) with what the agent *actually touched* (paths seen in
``tool_call_state`` payloads) and flags two heuristic findings:

- ``possibly_missed`` — paths the prompt explicitly named that no tool
  call ever referenced.
- ``scope_drift`` — touched paths that share no directory, filename or
  repo anchor with anything the prompt named.

This is deliberately conservative and honest about its limits:

- Prompt path extraction is regex-based. Only *strong* references — a
  filename with a known extension, an absolute path, or an explicitly
  prefixed ``./`` ``../`` ``~/`` path — can appear in
  ``possibly_missed``. Weaker signals (bare directory mentions, dotted
  module names like ``devin_qa_pack.intent``, repo/package names) only
  widen the scope used for drift detection, so noise such as "and/or"
  or "read/write" cannot produce a missed-path flag.
- "Touched" means *any path referenced by a tool-call payload* —
  writes, reads and shell commands all count. That trades precision
  for fewer false "missed" flags: ``cat foo.py`` still counts as the
  agent engaging with ``foo.py``.
- Coverage is by path identity, not semantics: suffix match,
  contiguous path-segment match, or basename match for bare filenames.
  A prompt naming ``foo.py`` is treated as covered by any touched
  ``*/foo.py``.
- Drift is only computed when the prompt produced at least one scope
  anchor; a prompt naming no paths at all yields ``skipped`` rather
  than flagging every touched file.
- Paths inside ``.git``/``.hg``/``.svn`` internals are ignored.

Status: ``aligned`` (analysis ran, nothing flagged), ``flagged`` (at
least one missed path or drift path), ``skipped`` (not computable —
``reason`` says why). Everything is labelled ``heuristic`` in the
payload.
"""

from __future__ import annotations

import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from devin_internals.parsers.sessions import MessageNode, ToolCallState

from devin_qa_pack.claims import _decode_message

ALIGNED = "aligned"
FLAGGED = "flagged"
SKIPPED = "skipped"

_USER_ROLES = {"user", "human"}
_MAX_EXCERPT = 160

# Known file extensions — a bare token carrying one is a *strong*
# (file-grade) reference. Mirrors devin-graph's extract.py list plus
# common additions.
_KNOWN_EXTS = frozenset(
    "py pyi js jsx ts tsx mjs cjs json toml yaml yml md txt rst rs go c h "
    "cc cpp cxx hpp java kt kts rb sh bash zsh fish bat ps1 sql html htm "
    "css scss less sass xml csv tsv cfg ini env lock ipynb pdf png jpg jpeg "
    "gif svg webp ico mp4 mov zip gz tar whl jar gradle properties proto "
    "thrift lua pl pm php swift dart scala clj ex exs erl hs ml fs vb cs "
    "tf tfvars hcl dockerfile mk cmake vue svelte jsx".split()
)

# Well-known extensionless filenames.
_EXTLESS_FILENAMES = {
    "makefile", "dockerfile", "containerfile", "license", "licence",
    "gemfile", "procfile", "vagrantfile", "justfile", "rakefile",
    "brewfile", "cmakelists", "owners", "changelog", "notice", "authors",
}

# Slash-joined words that are phrases, not paths ("and/or", "read/write").
_SLASH_WORDS = {
    "and", "or", "vs", "etc", "yes", "no", "n/a", "true", "false",
    "read", "write", "input", "output", "io", "dev", "prod", "on", "off",
    "up", "down", "in", "out", "src", "async", "await", "either",
    "w/", "w/o",
}

# Token that looks like a path: contains a separator, or is a bare
# filename with a known extension (mirrors devin-graph extract.py).
_PATH_TOKEN_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\.{1,2}/|~?/|/)?[\w@+~.-]+(?:[\\/][\w@+~.-]+)+"
    r"|[\w@+~.-]+\.[A-Za-z0-9]{1,10}\b"
)
# Trailing punctuation trimmed from regex tokens. Leading chars are
# trimmed more carefully (quotes/parens only) so dotfiles keep their dot:
# ``.git/config`` must not degrade into ``git/config``.
_TOKEN_STRIP = "\"'`.,;:()[]{}<>"
_TOKEN_LSTRIP = "\"'`(<[{"

# Dotted module-ish names: ``devin_qa_pack.intent`` → devin_qa_pack/intent.
_DOTTED_RE = re.compile(r"\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*){1,4}\b")

# Identifier-ish words that may match repo/package names.
_WORD_RE = re.compile(r"\b[A-Za-z][\w.-]{2,}\b")

# Payload keys whose string value(s) are file/dir paths (devin-graph).
_PATH_KEYS = {
    "path", "paths", "file", "files", "filename", "file_path", "filepath",
    "abs_path", "absolute_path", "target_file", "cwd", "directory",
    "workdir", "working_directory", "folder",
}
# Payload keys whose string value is a shell-like command to mine.
_COMMAND_KEYS = {"command", "cmd", "script", "shell_command", "input"}

# VCS-internal path segments never reported as drift.
_VCS_SEGMENTS = {".git", ".hg", ".svn"}


@dataclass(frozen=True)
class PromptRef:
    """One path-ish thing the prompt named.

    ``strong`` marks file-grade references (known extension, absolute or
    ``./``-prefixed path) — only those can be reported as missed. Weak
    references (dirs, modules, repo names) only widen the scope anchors.
    """

    raw: str
    path: str  # normalized: "/" separators, dots→slashes for modules
    kind: str  # "file" | "dir" | "module" | "repo"
    strong: bool


@dataclass
class IntentAudit:
    session_id: str
    status: str  # aligned | flagged | skipped
    reason: str | None = None
    prompt_node_id: int | None = None
    prompt_excerpt: str | None = None
    referenced: list[PromptRef] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)
    possibly_missed: list[str] = field(default_factory=list)
    scope_drift: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# prompt extraction
# ---------------------------------------------------------------------------


def first_user_prompt(
    nodes: Iterable[MessageNode],
) -> MessageNode | None:
    """The first user-role ``message_nodes`` entry with non-empty text."""
    for node in nodes:
        role, text = _decode_message(node.chat_message)
        if role in _USER_ROLES and text.strip():
            return node
    return None


def _normalize(raw: str) -> str | None:
    """Canonical path: ``/`` separators, no ``.`` segments, no quotes."""
    p = raw.strip().strip("\"'").strip()
    if not p or p == ".":
        return None
    p = p.replace("\\", "/")
    p = posixpath.normpath(p)
    if p == "." or p == "~":
        return None
    return p or None


def _is_absolute(p: str) -> bool:
    return p.startswith("/") or re.match(r"^[A-Za-z]:/", p) is not None


def _basename(p: str) -> str:
    return p.rsplit("/", 1)[-1]


def _has_known_ext(name: str) -> bool:
    return (
        "." in name
        and name.rsplit(".", 1)[-1].lower() in _KNOWN_EXTS
    )


def _explicitly_prefixed(raw: str) -> bool:
    return raw.startswith(("./", "../", "~/", "/")) or bool(
        re.match(r"^[A-Za-z]:[\\/]", raw)
    )


def _classify_token(raw: str) -> PromptRef | None:
    """Turn one raw path-looking token into a :class:`PromptRef`."""
    tok = raw.lstrip(_TOKEN_LSTRIP).rstrip(_TOKEN_STRIP)
    if not tok or "://" in tok or tok.startswith("//"):
        return None
    if tok.lstrip("./").startswith("-"):  # a flag, not a path
        return None
    p = _normalize(tok)
    if not p or p == "~":
        return None
    segs = [s for s in p.split("/") if s and s != ".."]
    if not segs:
        return None
    base = segs[-1]
    low_segs = {s.lower() for s in segs}
    if low_segs and low_segs <= _SLASH_WORDS:  # "and/or", "read/write"
        return None
    if _is_absolute(p) or _explicitly_prefixed(raw.strip()):
        kind = "file" if _has_known_ext(base) else "dir"
        return PromptRef(raw=tok, path=p, kind=kind, strong=True)
    if _has_known_ext(base) or base.lower() in _EXTLESS_FILENAMES:
        return PromptRef(raw=tok, path=p, kind="file", strong=True)
    if "/" in p:
        # Extensionless relative path — weak dir/module-grade signal.
        return PromptRef(raw=tok, path=p, kind="dir", strong=False)
    return None


def _dotted_refs(text: str) -> Iterator[PromptRef]:
    """``pkg.sub.module`` mentions → weak ``pkg/sub/module`` refs."""
    for m in _DOTTED_RE.finditer(text):
        raw = m.group(0)
        parts = raw.split(".")
        if _has_known_ext(raw):  # already a filename like foo.py
            continue
        if len(parts) < 2:
            continue
        yield PromptRef(
            raw=raw, path="/".join(parts), kind="module", strong=False
        )


def _repo_refs(text: str, repo_names: Iterable[str]) -> Iterator[PromptRef]:
    """Prompt words equal to a workspace/repo basename."""
    names = {n for n in repo_names if n}
    if not names:
        return
    for w in _WORD_RE.finditer(text):
        if w.group(0) in names:
            yield PromptRef(
                raw=w.group(0), path=w.group(0), kind="repo", strong=False
            )


def extract_prompt_refs(
    text: str, repo_names: Iterable[str] = ()
) -> list[PromptRef]:
    """All path-ish references in the prompt, deduplicated by ``path``."""
    refs: list[PromptRef] = []
    seen: set[str] = set()

    def _add(ref: PromptRef | None) -> None:
        if ref is None or ref.path in seen:
            return
        seen.add(ref.path)
        refs.append(ref)

    for m in _PATH_TOKEN_RE.finditer(text):
        # skip tokens that are part of a URL (preceded by scheme://)
        if re.search(r"[A-Za-z][\w+.-]*:/+$", text[: m.start()]):
            continue
        _add(_classify_token(m.group(0)))
    for ref in _dotted_refs(text):
        _add(ref)
    for ref in _repo_refs(text, repo_names):
        _add(ref)
    return refs


# ---------------------------------------------------------------------------
# touched-path extraction (mirrors devin-graph extract.py, unstable payloads)
# ---------------------------------------------------------------------------


def _parse_payload(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _walk_strings(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for v in node.values():
            yield from _walk_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_strings(v)
    elif isinstance(node, str):
        yield node


def _iter_keyed(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k).lower(), v
            yield from _iter_keyed(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_keyed(v)


def _paths_from_command(text: str) -> set[str]:
    found = set()
    for m in _PATH_TOKEN_RE.finditer(text):
        tok = m.group(0).lstrip(_TOKEN_LSTRIP).rstrip(_TOKEN_STRIP)
        if re.search(r"[A-Za-z][\w+.-]*:/+$", text[: m.start()]):
            continue
        if (not tok or "://" in tok or tok.startswith("//")
                or tok.lstrip("./").startswith("-")):
            continue
        norm = _normalize(tok)
        if norm:
            found.add(norm)
    return found


def _paths_from_payload(payload: dict) -> set[str]:
    found: set[str] = set()
    for key, value in _iter_keyed(payload):
        if key in _PATH_KEYS:
            if isinstance(value, str):
                norm = _normalize(value)
                if norm and "://" not in norm:
                    found.add(norm)
            elif isinstance(value, (list, dict)):
                for s in _walk_strings(value):
                    norm = _normalize(s)
                    if norm and "://" not in norm:
                        found.add(norm)
        elif key in _COMMAND_KEYS and isinstance(value, str):
            found |= _paths_from_command(value)
    return found


def _resolve(path: str, cwd: str | None) -> str:
    """Anchor relative touched paths to the session working directory."""
    if _is_absolute(path) or not cwd:
        return path
    return posixpath.normpath(f"{cwd}/{path}")


def extract_touched_paths(
    states: Iterable[ToolCallState], cwd: str | None
) -> set[str]:
    """Every path referenced by any tool-call payload, cwd-resolved."""
    base = _normalize(cwd) if cwd else None
    touched: set[str] = set()
    for state in states:
        payload: dict = {}
        for raw in (state.tool_call_update_json, state.tool_call_json):
            data = _parse_payload(raw)
            if data:
                payload = {**payload, **data}
        for p in _paths_from_payload(payload):
            touched.add(_resolve(p, base))
    return {
        t for t in touched
        if not _VCS_SEGMENTS & set(t.split("/"))
    }


# ---------------------------------------------------------------------------
# coverage + scope
# ---------------------------------------------------------------------------


def _subseq(outer: list[str], inner: list[str]) -> bool:
    """``inner`` occurs contiguously inside ``outer``."""
    if not inner or len(inner) > len(outer):
        return False
    return any(
        outer[i : i + len(inner)] == inner
        for i in range(len(outer) - len(inner) + 1)
    )


def _covered(ref_path: str, touched: Iterable[str]) -> bool:
    """Is the prompt-referenced path reflected in the touched set?"""
    psegs = ref_path.split("/")
    for t in touched:
        if t == ref_path or t.endswith("/" + ref_path):
            return True
        if ref_path.endswith("/" + t):  # ref carries a repo prefix
            return True
        tsegs = t.split("/")
        if _subseq(tsegs, psegs) or _subseq(psegs, tsegs):
            return True
        if len(psegs) == 1 and tsegs and tsegs[-1] == ref_path:
            return True
    return False


def _scope_anchors(
    refs: Iterable[PromptRef],
) -> tuple[set[tuple[str, ...]], set[str]]:
    """``(dir segment tuples, filenames)`` the prompt's scope spans."""
    dirs: set[tuple[str, ...]] = set()
    names: set[str] = set()
    for ref in refs:
        segs = tuple(s for s in ref.path.split("/") if s and s != "..")
        if not segs:
            continue
        if ref.kind == "repo":
            dirs.add(segs)
            names.add(ref.path)
            continue
        if ref.kind == "file":
            names.add(segs[-1])
            span = segs[:-1]
        else:  # dir/module — the ref itself may be a directory
            span = segs
        for i in range(1, len(span) + 1):
            dirs.add(span[:i])
    return dirs, names


def _in_scope(
    touched_path: str,
    cwd: str | None,
    dirs: set[tuple[str, ...]],
    names: set[str],
) -> bool:
    segs = touched_path.split("/")
    if cwd:
        # the project root itself or anything above it is never drift
        if touched_path == cwd or cwd.startswith(touched_path + "/"):
            return True
    if segs and segs[-1] in names:
        return True
    return any(_subseq(segs, list(d)) for d in dirs)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def _excerpt(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= _MAX_EXCERPT else (
        line[: _MAX_EXCERPT - 1] + "…"
    )


def _repo_names(session: Any) -> list[str]:
    """Basenames of the working dir + ``workspace_dirs`` entries."""
    names: list[str] = []
    wd = _normalize(getattr(session, "working_directory", "") or "")
    if wd:
        names.append(_basename(wd))
    raw = getattr(session, "workspace_dirs", None)
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = None
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    norm = _normalize(item)
                    if norm:
                        names.append(_basename(norm))
    return names


def audit_intent(
    session: Any,
    nodes: Iterable[MessageNode],
    states: Iterable[ToolCallState],
) -> IntentAudit:
    """Prompt intent vs. touched-path coverage for one session.

    Never raises on odd input — uncomputable cases return ``SKIPPED``
    with a ``reason``.
    """
    nodes = list(nodes)
    states = list(states)
    audit = IntentAudit(session_id=session.id, status=SKIPPED)

    prompt = first_user_prompt(nodes)
    if prompt is None:
        audit.reason = "no user message found in message_nodes"
        return audit
    _, text = _decode_message(prompt.chat_message)
    audit.prompt_node_id = prompt.node_id
    audit.prompt_excerpt = _excerpt(text)

    refs = extract_prompt_refs(text, _repo_names(session))
    audit.referenced = refs
    if not refs:
        audit.reason = "prompt names no paths, modules or repo names"
        return audit

    if not states:
        audit.reason = "no tool_call_state rows — nothing to compare"
        return audit

    cwd = _normalize(getattr(session, "working_directory", "") or "")
    touched = extract_touched_paths(states, cwd)
    audit.touched = sorted(touched)
    audit.notes.append(
        "heuristic: touched = any path referenced by a tool-call "
        "payload (writes, reads and commands all count)"
    )
    if not touched:
        audit.notes.append(
            "tool calls recorded but no paths could be extracted "
            "from their payloads — coverage is unreliable"
        )

    audit.possibly_missed = sorted(
        ref.raw
        for ref in refs
        if ref.strong and not _covered(ref.path, touched)
    )

    dirs, names = _scope_anchors(refs)
    if dirs or names:
        audit.scope_drift = sorted(
            t for t in touched if not _in_scope(t, cwd, dirs, names)
        )
        audit.notes.append(
            "heuristic: scope anchors come only from paths/modules/repo "
            "names in the prompt — drift means 'no prompt-named anchor', "
            "not 'wrong file'"
        )

    audit.status = (
        FLAGGED if (audit.possibly_missed or audit.scope_drift) else ALIGNED
    )
    return audit


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def intent_dict(audit: IntentAudit) -> dict[str, Any]:
    return {
        "session_id": audit.session_id,
        "status": audit.status,
        "heuristic": True,
        "reason": audit.reason,
        "prompt_node_id": audit.prompt_node_id,
        "prompt_excerpt": audit.prompt_excerpt,
        "referenced": [
            {
                "path": r.path,
                "raw": r.raw,
                "kind": r.kind,
                "strong": r.strong,
            }
            for r in audit.referenced
        ],
        "touched": audit.touched,
        "possibly_missed": audit.possibly_missed,
        "scope_drift": audit.scope_drift,
        "notes": audit.notes,
    }


def render_intent(audit: IntentAudit) -> str:
    """Text rendering for the ``intent`` subcommand."""
    if audit.status == SKIPPED:
        return f"SKIPPED {audit.session_id} — {audit.reason}"
    head = (
        f"{audit.status.upper()}  {audit.session_id} — "
        f"{len(audit.possibly_missed)} possibly missed, "
        f"{len(audit.scope_drift)} scope drift "
        f"({len(audit.touched)} touched path(s), heuristic)"
    )
    lines = [head]
    if audit.prompt_excerpt:
        lines.append(f'  prompt: "{audit.prompt_excerpt}"')
    for p in audit.possibly_missed:
        lines.append(f"  [missed] {p} — named in prompt, never touched")
    for p in audit.scope_drift:
        lines.append(f"  [drift ] {p} — outside prompt-named scope")
    return "\n".join(lines)
