"""Thin CLI wrapper — all logic lives in the library modules.

``devin-qa-pack audit`` reads a sessions.db (explicit or auto-detected),
verifies deliverable claims against tool_call_state ground truth and
prints a verdict per session. Read-only. Exit codes: 0 = every audited
session PASS · 1 = audit ran, some session PARTIAL/UNVERIFIED · 2 = the
audit could not run (missing/unknown store, unknown session).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from devin_internals.parsers import SessionsStore
from devin_internals.parsers.sessions import Session
from devin_internals.schema import SchemaError

from devin_qa_pack.paths import default_sessions_db
from devin_qa_pack.report import (
    PASS,
    audit_all,
    audit_session,
    audits_payload,
    render_text,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devin-qa-pack",
        description="Audit Devin sessions: verify deliverable claims "
        "(tests, commits, files, pushes) against tool_call_state ground truth.",
    )
    sub = parser.add_subparsers(dest="command")
    audit = sub.add_parser("audit", help="verify claims per session")
    audit.add_argument(
        "--sessions-db",
        metavar="PATH",
        help="path to sessions.db (default: auto-detect Devin data dir)",
    )
    scope = audit.add_mutually_exclusive_group(required=True)
    scope.add_argument("--session", metavar="ID", help="audit one session "
                       "(exact id or unique prefix)")
    scope.add_argument("--all", action="store_true", help="audit all sessions")
    audit.add_argument("--limit", type=int, metavar="N",
                       help="max sessions with --all (most recent first)")
    audit.add_argument("--json", action="store_true", help="JSON output")
    return parser


def _find_session(store: SessionsStore, session_arg: str) -> Session | None:
    sessions = store.sessions()
    for s in sessions:
        if s.id == session_arg:
            return s
    matches = [s for s in sessions if s.id.startswith(session_arg)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(
            f"error: session prefix '{session_arg}' is ambiguous "
            f"({len(matches)} matches)",
            file=sys.stderr,
        )
        return None
    print(f"error: unknown session '{session_arg}'", file=sys.stderr)
    return None


def _cmd_audit(args: argparse.Namespace) -> int:
    db = Path(args.sessions_db).expanduser() if args.sessions_db else default_sessions_db()
    if not db.is_file():
        print(f"error: sessions.db not found: {db}", file=sys.stderr)
        return 2
    try:
        store = SessionsStore(db)
    except (SchemaError, OSError) as exc:
        print(f"error: cannot open {db}: {exc}", file=sys.stderr)
        return 2

    with store:
        if args.all:
            audits = audit_all(store, limit=args.limit)
        else:
            session = _find_session(store, args.session)
            if session is None:
                return 2
            audits = [audit_session(store, session)]

    if args.json:
        print(json.dumps(audits_payload(audits, db), indent=2))
    else:
        print(render_text(audits))
    return 0 if all(a.verdict == PASS for a in audits) else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "audit":
        return _cmd_audit(args)
    _build_parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
