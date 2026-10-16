"""devin-qa-pack as an MCP server: the ``audit`` verdict exposed as a tool.

One tool, ``qa_audit`` — verifies a session's deliverable claims (tests,
commits, files, pushes) against tool_call_state ground truth and returns
the same JSON payload as ``devin-qa-pack audit --json``. Read-only:
the audit never writes to the store it inspects.

The logic lives in :func:`do_audit`, unit-testable without a running
server or the ``mcp`` package. ``build_server()`` wraps it — needs the
``mcp`` extra: ``pip install 'devin-qa-pack[mcp]'``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from devin_internals.parsers import SessionsStore
from devin_internals.schema import SchemaError

from devin_qa_pack.evidence import (
    Surface,
    collect_evidences,
    match_evidence,
)
from devin_qa_pack.paths import default_sessions_db
from devin_qa_pack.report import (
    audit_all,
    audit_evidence,
    audit_session,
    audit_source,
    audits_payload,
    filter_evidences,
)


def _find_session(store: SessionsStore, session_arg: str):
    """Exact id first, then unique prefix — same rule as the CLI."""
    sessions = store.sessions()
    for s in sessions:
        if s.id == session_arg:
            return s
    matches = [s for s in sessions if s.id.startswith(session_arg)]
    if len(matches) == 1:
        return matches[0]
    return None


def do_audit(
    session_id: str = "",
    audit_all_sessions: bool = False,
    limit: int = 0,
    sessions_db: str = "",
    source: str = "local",
    surface: str = "",
    online: bool = False,
    allow_domains: list[str] | None = None,
    claim_limit: int | None = None,
) -> dict:
    """Audit session(s) and return the ``audit --json`` payload as a dict.

    Mirrors ``devin-qa-pack audit``: ``source="local"`` reads a sessions.db
    (explicit or auto-detected), ``source="mcp"`` audits a cloud session
    through the hosted Devin MCP (needs ``DEVIN_API_KEY``), and
    ``surface`` selects evidence providers — ``"auto"`` (the default when
    no ``sessions_db`` is pinned) queries every detected store and
    namespaces ids as ``surface:native_id``. Failures that the CLI
    reports as exit code 2 surface here as ``{"error": ...}`` — the tool
    boundary never raises.
    """
    domains = tuple(allow_domains or ())
    kw = {"online": online, "allow_domains": domains}

    if source == "mcp":
        if not session_id:
            return {"error": "usage",
                    "detail": "source 'mcp' needs a cloud session_id"}
        from devin_qa_pack.adapters import mcp
        try:
            view = mcp.load(session_id)
        except mcp.McpError as exc:
            return {"error": "mcp", "detail": str(exc)}
        audits = [audit_source(view, claim_limit, **kw)]
        return audits_payload(audits, f"mcp:{mcp.DEFAULT_MCP_URL}")

    if session_id and audit_all_sessions:
        return {"error": "conflicting_scope",
                "detail": "session_id and audit_all_sessions are mutually "
                "exclusive (CLI: --session / --all)"}

    provider_mode = bool(surface) or not sessions_db
    if provider_mode:
        if surface and surface not in Surface.CHOICES:
            return {"error": "usage",
                    "detail": f"unknown surface '{surface}' "
                    f"(one of {', '.join(Surface.CHOICES)})"}
        evidences, warnings = collect_evidences(
            surface or Surface.AUTO, sessions_db=sessions_db or None
        )
        if session_id:
            found = match_evidence(evidences, session_id)
            if isinstance(found, list):
                refs = ", ".join(
                    e.ref or f"{e.surface}:{e.native_id}" for e in found
                )
                return {"error": "ambiguous_session",
                        "detail": f"session '{session_id}' is "
                        f"ambiguous: {refs}"}
            if found is None:
                return {"error": "unknown_session",
                        "detail": f"unknown session '{session_id}'"}
            evidences = [found]
        else:
            evidences = filter_evidences(
                evidences, limit=limit or None
            )
        if not evidences:
            # Mirror the CLI's fail-closed rule: an audit that matched
            # nothing must report an error, not an empty success.
            return {"error": "no_sessions",
                    "detail": "no sessions found on the queried "
                    "surface(s)"}
        audits = [audit_evidence(e, claim_limit, **kw) for e in evidences]
        label = (
            str(Path(sessions_db).expanduser())
            if sessions_db
            else "surfaces: auto"
            if not surface or surface == Surface.AUTO
            else f"surface: {surface}"
        )
        payload = audits_payload(audits, label)
        if warnings:
            payload["warnings"] = warnings
        return payload

    db = Path(sessions_db).expanduser() if sessions_db else (
        default_sessions_db())
    if db is None or not db.is_file():
        return {"error": "no_store",
                "detail": f"sessions.db not found: {db}"}
    try:
        store = SessionsStore(db)
    except (SchemaError, OSError) as exc:
        return {"error": "bad_store", "detail": f"cannot open {db}: {exc}"}
    with store:
        if session_id:
            session = _find_session(store, session_id)
            if session is None:
                return {"error": "unknown_session",
                        "detail": f"unknown session '{session_id}'"}
            audits = [audit_session(store, session, claim_limit, **kw)]
        else:
            audits = audit_all(store, limit=limit or None, **kw)
    return audits_payload(audits, db)


def _err(error: Exception) -> dict:
    return {"error": type(error).__name__, "detail": str(error)[:500]}


def _make_app(name: str):
    """Return an MCP server app across SDK versions.

    mcp 2.x renamed FastMCP -> MCPServer; both expose the same .tool()
    decorator and .run(transport='stdio'). Support whichever is installed.
    """
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer
        return MCPServer(name)
    except ImportError:
        pass
    try:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP
        return FastMCP(name)
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "The MCP server needs the 'mcp' extra: "
            "pip install 'devin-qa-pack[mcp]'"
        ) from e


def build_server():
    """Build the MCP server app with every devin-qa-pack tool registered."""
    server = _make_app("devin-qa-pack")

    @server.tool()
    def qa_audit(
        session_id: str = "",
        audit_all_sessions: bool = False,
        limit: int = 0,
        sessions_db: str = "",
        source: str = "local",
        surface: str = "",
        online: bool = False,
        allow_domains: list[str] | None = None,
        claim_limit: int | None = None,
    ) -> dict:
        """Verify what an agent session claimed it did (tests run, commits,
        files written, pushes) against its own tool-call evidence. Same JSON
        as ``devin-qa-pack audit --json``: per-session verdict PASS /
        PARTIAL / UNVERIFIED plus every claim's status. Read-only.
        ``source="mcp"`` audits a cloud session via the hosted Devin MCP
        (requires DEVIN_API_KEY); default ``source="local"`` reads a
        sessions.db (explicit ``sessions_db`` or auto-detected).
        ``surface`` selects an evidence provider: ``devin-cli``,
        ``copilot-cli``, ``codex-cli``, ``vscode-chat`` or ``auto``
        (every detected store; ids come back ``surface:native_id``).
        """
        try:
            return do_audit(
                session_id=session_id,
                audit_all_sessions=audit_all_sessions,
                limit=limit,
                sessions_db=sessions_db,
                source=source,
                surface=surface,
                online=online,
                allow_domains=allow_domains,
                claim_limit=claim_limit,
            )
        except Exception as error:  # noqa: BLE001 — tool boundary must not raise
            return _err(error)

    return server


def main() -> None:
    try:
        server = build_server()
    except ImportError as exc:
        # Missing the 'mcp' extra — one-line install hint on stderr,
        # not a traceback (MCP hosts surface the log raw).
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    # Startup failures at .run() are NOT the missing-extra case: let
    # them keep their traceback so the real failure stays debuggable.
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
