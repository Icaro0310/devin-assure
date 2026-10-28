"""``devin-metrics`` — thin CLI wrapper; all logic lives in the library.

Subcommands (read-only, no network, all accept ``--json``):

- ``summary``   headline numbers + per-model table + top-5 longest sessions
- ``projects``  per-project (``working_directory``) session/activity table
- ``commits``   session→commit correlation: 40-hex SHAs + repo URLs
                extracted from ``tool_call_state`` payloads
- ``daily``     per-day activity; ``--days N`` keeps the N most recent days
- ``digest``    ``--weekly`` composes summary+daily+watch into one report
                file (``digest-YYYY-Www.md``); incremental via the shared
                week-key cursor — a second run in the same week is a no-op
                unless ``--force``

Store locations default to the platform Devin data dir (``paths.py``);
``--data-dir`` overrides the root, ``--sessions-db``/``--acp-dir`` override
individual stores. A missing ``acp-messages`` dir degrades gracefully — a
warning on stderr and ``-`` in the cost columns. A missing ``sessions.db``
is fatal (exit 1): it is the only source of session rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from devin_internals.schema import SchemaError

from devin_metrics.aggregate import (
    by_day,
    by_project,
    commits_report,
    summarize,
    watch_report,
)
from devin_metrics.churn import churn_report, render_churn
from devin_metrics.collect import MetricsSnapshot, collect
from devin_metrics.dashboard.collect import collect_stats
from devin_metrics.dashboard.render import render_html
from devin_metrics.digest import run_digest, week_key_for
from devin_metrics.paths import (
    acp_messages_dir,
    default_acp_messages_dir,
    default_data_dir,
    sessions_db_path,
)
from devin_metrics.render import (
    dumps_json,
    render_commits_md,
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
        else acp_messages_dir(root) if args.data_dir
        else default_acp_messages_dir()
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


def cmd_commits(args: argparse.Namespace) -> int:
    report = commits_report(_snapshot(args))
    print(dumps_json(report) if args.json else render_commits_md(report))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """ME-2: advisory context guard — warns, never blocks.

    Cost is unverifiable in local stores (ME-3), so the guard watches
    ``context_tokens`` (peak prompt size per session) — the only real
    resource signal that persists. Thresholds are advisory: sessions that
    crossed them are listed, exit stays 0 unless --fail.
    """
    report = watch_report(
        _snapshot(args), args.session_warn, args.daily_warn)
    findings = report["findings"]
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"watch (advisory): {report['sessions_observed']} sessions "
              f"observed · {len(findings)} finding(s)")
        for f in findings:
            if f["kind"] == "session":
                print(f"  session {f['session_id'][:16]}… "
                      f"{f['context_tokens']:,} tok > {f['threshold']:,}")
            else:
                print(f"  day {f['date']}  {f['context_tokens']:,} tok "
                      f"> {f['threshold']:,}")
        if not findings:
            print("  all clear")
    return 1 if (findings and args.fail) else 0


def cmd_digest(args: argparse.Namespace) -> int:
    """Weekly digest — one markdown report per ISO week.

    Incremental via the shared ``metrics-digest`` cursor: the week key is
    the watermark, so a second run inside the same ISO week reports
    "already digested" and writes nothing unless ``--force``. ``--since``
    backfills an explicit week (YYYY-Www, a date inside it, or epoch ms).
    """
    try:
        week = week_key_for(args.since)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    res = run_digest(
        _snapshot(args), args.out_dir, week=week, days=args.days,
        session_warn=args.session_warn, daily_warn=args.daily_warn,
        force=args.force)
    if args.json:
        print(json.dumps({
            "week": res.week,
            "report": str(res.path),
            "written": res.written,
            "skipped_reason": res.skipped_reason,
        }, indent=2))
    elif res.written:
        print(f"wrote {res.path} — digest for {res.week}")
    else:
        print(f"{res.skipped_reason} — {res.path} (use --force to rerun)")
    return 0


def cmd_churn(args: argparse.Namespace) -> int:
    report = churn_report(
        args.graph,
        exclude_globs=tuple(args.exclude_glob),
        include_pseudo=args.include_pseudo,
    )
    print(json.dumps(report, indent=2) if args.json
          else render_churn(report))
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
                   help="per-project session/activity table").set_defaults(func=cmd_projects)
    p_daily = sub.add_parser("daily", parents=[common],
                             help="activity over time")
    p_daily.add_argument(
        "--days", type=int, default=None, metavar="N",
        help="keep the N most recent activity days (default: all)",
    )
    p_daily.set_defaults(func=cmd_daily)
    sub.add_parser(
        "commits", parents=[common],
        help="session→commit correlation (SHAs + repo URLs from "
             "tool_call_state)").set_defaults(func=cmd_commits)
    p_digest = sub.add_parser(
        "digest", parents=[common],
        help="compose summary+daily+watch into a report file")
    p_digest.add_argument(
        "--weekly", action="store_true", required=True,
        help="weekly report — one digest-YYYY-Www.md per ISO week")
    p_digest.add_argument(
        "--out-dir", metavar="DIR", default=".",
        help="directory for the report (default: current directory)")
    p_digest.add_argument(
        "--days", type=int, default=7, metavar="N",
        help="daily section keeps the N most recent activity days "
        "(default: %(default)s)")
    p_digest.add_argument(
        "--session-warn", type=int, default=400_000, metavar="N",
        help="watch: flag sessions with peak context > N tokens "
        "(default: %(default)s)")
    p_digest.add_argument(
        "--daily-warn", type=int, default=2_000_000, metavar="N",
        help="watch: flag days whose summed context > N tokens "
        "(default: %(default)s)")
    p_digest.add_argument(
        "--since", metavar="WEEK|DATE|EPOCH_MS",
        help="digest an explicit week instead of the current one "
        "(YYYY-Www, a date inside it, or epoch ms) — backfill mode")
    p_digest.add_argument(
        "--incremental", action="store_true",
        help="cursor mode — the default; a second run in the same week is "
        "a no-op (kept for explicitness in scheduled jobs)")
    p_digest.add_argument(
        "--force", action="store_true",
        help="re-digest even if this week was already digested")
    p_digest.set_defaults(func=cmd_digest)
    p_dash = sub.add_parser("dashboard", parents=[common],
                            help="render the static HTML dashboard")
    p_dash.add_argument(
        "--out", metavar="FILE", default="index.html",
        help="output file (default: index.html)",
    )
    p_dash.set_defaults(func=cmd_dashboard)
    p_churn = sub.add_parser(
        "churn", help="rework stats from a graph.db (devin-graph build first)")
    p_churn.add_argument("--graph", metavar="DB", default="graph.db",
                         help="path to a built graph.db (default ./graph.db)")
    p_churn.add_argument(
        "--exclude-glob", action="append", default=[], metavar="GLOB",
        help="drop file paths matching GLOB from the churn ranking "
             "(repeatable; matched on the whole path, '*' crosses '/')")
    p_churn.add_argument(
        "--include-pseudo", action="store_true",
        help="keep pseudo-paths (/dev/null, NUL/CON devices, shell-builtin "
             "streams) that the built-in denylist drops by default")
    p_churn.add_argument("--json", action="store_true")
    p_churn.set_defaults(func=cmd_churn)
    p_watch = sub.add_parser(
        "watch", parents=[common],
        help="advisory context guard — lists sessions/days over a token "
             "threshold; never blocks (ME-2)")
    p_watch.add_argument("--session-warn", type=int, default=400_000,
                         metavar="N", help="flag sessions with peak context "
                         "> N tokens (default: %(default)s)")
    p_watch.add_argument("--daily-warn", type=int, default=2_000_000,
                         metavar="N", help="flag days whose summed context "
                         "> N tokens (default: %(default)s)")
    p_watch.add_argument("--fail", action="store_true",
                         help="exit 1 when findings exist (CI mode)")
    p_watch.set_defaults(func=cmd_watch)
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
