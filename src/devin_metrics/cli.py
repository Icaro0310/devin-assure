"""``devin-metrics`` — thin CLI wrapper; all logic lives in the library.

Subcommands (read-only, no network, all accept ``--json``):

- ``summary``   headline numbers + per-model table + top-5 longest/costliest
- ``projects``  per-project (``working_directory``) cost/session table
- ``daily``     per-day activity; ``--days N`` keeps the N most recent days

Store locations default to the platform Devin data dir (``paths.py``);
``--data-dir`` overrides the root, ``--sessions-db``/``--acp-dir`` override
individual stores. A missing ``acp-messages`` dir degrades gracefully — a
warning on stderr and ``-`` in the cost columns. A missing ``sessions.db``
is fatal (exit 1): it is the only source of session rows.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

from devin_internals.schema import SchemaError

from devin_metrics.aggregate import by_day, by_project, summarize
from devin_metrics.collect import MetricsSnapshot, collect
from devin_metrics.dashboard.collect import collect_stats
from devin_metrics.dashboard.render import render_html
from devin_metrics.paths import (
    acp_messages_dir,
    default_data_dir,
    sessions_db_path,
)
from devin_metrics.render import (
    dumps_json,
    render_daily_md,
    render_projects_md,
    render_summary_md,
)


def _resolve(args: argparse.Namespace) -> tuple[Path, Path]:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    sessions_db = (
        Path(args.sessions_db).expanduser()
        if args.sessions_db
        else sessions_db_path(root)
    )
    acp_dir = (
        Path(args.acp_dir).expanduser()
        if args.acp_dir is not None
        else acp_messages_dir(root)
    )
    return sessions_db, acp_dir


def _snapshot(args: argparse.Namespace) -> MetricsSnapshot:
    sessions_db, acp_dir = _resolve(args)
    if not acp_dir.is_dir():
        print(
            f"warning: {acp_dir}: no such directory — "
            "cost/token columns will show '-'",
            file=sys.stderr,
        )
    return collect(sessions_db, acp_dir)


def cmd_summary(args: argparse.Namespace) -> int:
    summary = summarize(_snapshot(args))
    print(dumps_json(summary) if args.json else render_summary_md(summary))
    return 0


def cmd_projects(args: argparse.Namespace) -> int:
    rows = by_project(_snapshot(args))
    print(dumps_json(rows) if args.json else render_projects_md(rows))
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    rows = by_day(_snapshot(args), days=args.days)
    print(dumps_json(rows) if args.json else render_daily_md(rows))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    sessions_db, acp_dir = _resolve(args)
    if not acp_dir.is_dir():
        print(
            f"warning: {acp_dir}: no such directory — "
            "cost/token fields will be empty",
            file=sys.stderr,
        )
    stats = collect_stats(sessions_db, acp_dir)
    if args.json:
        import json

        print(json.dumps(stats, indent=2))
        return 0
    out = Path(args.out).expanduser()
    out.write_text(render_html(stats), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes) — open it in a browser")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devin-metrics",
        description="Local-only metrics from Devin's session stores.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir", metavar="DIR",
        help="Devin data dir (default: platform-specific, see docs/SPEC.md)",
    )
    common.add_argument(
        "--sessions-db", metavar="DB",
        help="path to sessions.db (default: <data-dir>/cli/sessions.db)",
    )
    common.add_argument(
        "--acp-dir", metavar="DIR",
        help="path to acp-messages dir (default: <data-dir>/User/acp-messages)",
    )
    common.add_argument("--json", action="store_true", help="JSON output")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("summary", parents=[common],
                   help="headline numbers").set_defaults(func=cmd_summary)
    sub.add_parser("projects", parents=[common],
                   help="per-project cost/session table").set_defaults(func=cmd_projects)
    p_daily = sub.add_parser("daily", parents=[common],
                             help="activity over time")
    p_daily.add_argument(
        "--days", type=int, default=None, metavar="N",
        help="keep the N most recent activity days (default: all)",
    )
    p_daily.set_defaults(func=cmd_daily)
    p_dash = sub.add_parser("dashboard", parents=[common],
                            help="render the static HTML dashboard")
    p_dash.add_argument(
        "--out", metavar="FILE", default="index.html",
        help="output file (default: index.html)",
    )
    p_dash.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SchemaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
