"""Thin CLI wrapper — all logic lives in the library modules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from devin_internals.schema import SchemaError

from devin_evals import __version__
from devin_evals.cases import CaseError, load_cases
from devin_evals.runner import run_evals

_OK, _FAILED, _USAGE = 0, 1, 2


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        cases = load_cases(args.evals)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    if not cases:
        print(f"no eval cases in {args.evals}")
        return _OK
    for c in cases:
        ref = c.session_ref or "(no session_ref)"
        checks = f"{len(c.checks)} check{'s' if len(c.checks) != 1 else ''}"
        print(f"{c.id}\t{checks}\t{ref}\t{c.description}")
    return _OK


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        report = run_evals(args.evals, args.sessions_db, out_dir=args.out)
    except (CaseError, SchemaError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    for c in report["cases"]:
        n = len(c["checks"])
        n_ok = sum(1 for ch in c["checks"] if ch["passed"])
        suffix = f"{n_ok}/{n} checks" if n else c["detail"]
        print(f"{c['status'].upper():5}  {c['id']}  ({suffix})")
    s = report["summary"]
    print(
        f"score: {s['passed']}/{s['total']} cases passed"
        f" — report written to {Path(args.out).resolve()}"
    )
    return _FAILED if (s["failed"] or s["errored"]) else _OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devin-evals",
        description="Grade agent sessions against deterministic rubrics "
        "(offline replay over Devin's sessions.db). Read-only.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="replay eval cases against a sessions.db")
    run.add_argument("--evals", required=True, help="directory of *.json eval cases")
    run.add_argument("--sessions-db", required=True, help="path to sessions.db")
    run.add_argument(
        "--out",
        default="eval-report",
        help="output directory for report.json/report.md (default: eval-report)",
    )
    run.set_defaults(func=_cmd_run)

    ls = sub.add_parser("list", help="list eval cases in a directory")
    ls.add_argument("--evals", required=True, help="directory of *.json eval cases")
    ls.set_defaults(func=_cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
