"""devin-evals as an MCP server: the ``run`` report exposed as a tool.

One tool, ``evals_run`` — replays deterministic rubric cases against a
recorded sessions.db and returns the same report dict the ``run``
command writes to ``report.json``. Read-only and offline: nothing is
written to the store, no sessions are spawned, no LLM is called.

The logic lives in :func:`do_run`, unit-testable without a running
server or the ``mcp`` package. ``build_server()`` wraps it — needs the
``mcp`` extra: ``pip install 'devin-evals[mcp]'``.
"""

from __future__ import annotations

from devin_internals.schema import SchemaError

from devin_evals.cases import CaseError
from devin_evals.runner import run_evals


def do_run(
    evals_dir: str,
    sessions_db: str,
    packs_dir: str = "",
) -> dict:
    """Replay eval cases and return the ``run`` report dict.

    Same payload as ``report.json``: ``summary`` ({total, passed, failed,
    skipped, errored, score}) plus per-case results with status
    pass/fail/skip/error and per-check details. ``out_dir`` stays unset
    so nothing is written — the report exists only in memory. Failures
    the CLI reports as exit code 2 (bad evals dir, missing or unreadable
    sessions.db) surface here as ``{"error": ...}``.
    """
    try:
        return run_evals(
            evals_dir,
            sessions_db,
            packs_dir=packs_dir or None,
        )
    except CaseError as exc:
        return {"error": "bad_evals", "detail": str(exc)[:500]}
    except SchemaError as exc:
        return {"error": "bad_store", "detail": str(exc)[:500]}
    except OSError as exc:
        return {"error": "io", "detail": str(exc)[:500]}


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
            "pip install 'devin-evals[mcp]'"
        ) from e


def build_server():
    """Build the MCP server app with every devin-evals tool registered."""
    server = _make_app("devin-evals")

    @server.tool()
    def evals_run(
        evals_dir: str,
        sessions_db: str,
        packs_dir: str = "",
    ) -> dict:
        """Replay a directory of deterministic eval cases against a
        recorded sessions.db and return the report — same JSON as
        ``report.json``: ``summary`` {total, passed, failed, skipped,
        errored, score} plus per-case status and rubric check results.
        Read-only and offline: writes nothing, spawns nothing, calls no
        LLM. ``packs_dir`` optionally adds custom rubric packs that
        shadow the built-ins.
        """
        try:
            return do_run(
                evals_dir=evals_dir,
                sessions_db=sessions_db,
                packs_dir=packs_dir,
            )
        except Exception as error:  # noqa: BLE001 — tool boundary must not raise
            return _err(error)

    return server


def main() -> None:
    build_server().run(transport="stdio")


if __name__ == "__main__":
    main()
