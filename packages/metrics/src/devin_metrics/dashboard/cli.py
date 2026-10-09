"""``devin-dashboard`` — thin CLI wrapper; all logic lives in the library.

Subcommands (read-only, no network):

- ``build``  collect stats → render → write a single ``index.html``
- ``data``   dump the stats dict (``--json`` for the raw JSON)

Store locations default to the platform Devin data dir (``collect.py``);
``--data-dir`` overrides the root, ``--sessions-db``/``--acp-dir`` override
individual stores. A missing ``acp-messages`` dir degrades gracefully — a
warning on stderr and ``null`` cost fields. A missing ``sessions.db`` is
fatal (exit 1): it is the only source of session rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from devin_internals.schema import SchemaError

from devin_metrics.dashboard.collect import (
    collect_stats,
    default_acp_dir,
    default_data_dir,
    default_sessions_db,
)
from devin_metrics.dashboard.render import render_html
from devin_metrics.paths import default_acp_messages_dir


def _resolve(args: argparse.Namespace) -> tuple[Path, Path | None]:
    root = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir()
    sessions_db = (
        Path(args.sessions_db).expanduser()
        if args.sessions_db
        else default_sessions_db(root)
    )
    acp_dir = (
        Path(args.acp_dir).expanduser()
        if args.acp_dir is not None
        else default_acp_dir(root) if args.data_dir
        else default_acp_messages_dir()
    )
    return sessions_db, acp_dir


def _stats(args: argparse.Namespace) -> dict:
    sessions_db, acp_dir = _resolve(args)
    if acp_dir is not None and not acp_dir.is_dir():
        print(
            f"warning: {acp_dir}: no such directory — "
            "cost/token fields will be empty",
            file=sys.stderr,
        )
    return collect_stats(sessions_db, acp_dir)


def cmd_build(args: argparse.Namespace) -> int:
    stats = _stats(args)
    out = Path(args.out).expanduser()
    out.write_text(render_html(stats), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size:,} bytes) — open it in a browser")
    return 0


def cmd_data(args: argparse.Namespace) -> int:
    stats = _stats(args)
    if args.json:
        print(json.dumps(stats, indent=2))
        return 0
    s = stats["summary"]
    src = stats["source"]
    cost = "unknown" if s["cost_usd_total"] is None else f"${s['cost_usd_total']:.4f}"
    print(
        f"{s['sessions']} sessions · {s['messages']} messages · "
        f"{s['tool_calls']} tool calls · cost {cost} · "
        f"{len(stats['projects'])} project(s) · {len(stats['models'])} model(s) · "
        f"schema v{src['schema_version']}"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devin-dashboard",
        description="Static local dashboard over Devin's session stores.",
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

    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser(
        "build", parents=[common], help="render the dashboard HTML file"
    )
    p_build.add_argument(
        "--out", metavar="FILE", default="index.html",
        help="output file (default: index.html)",
    )
    p_build.set_defaults(func=cmd_build)
    p_data = sub.add_parser(
        "data", parents=[common], help="dump the stats dict"
    )
    p_data.add_argument("--json", action="store_true", help="JSON output")
    p_data.set_defaults(func=cmd_data)
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
