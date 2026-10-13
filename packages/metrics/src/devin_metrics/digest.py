"""``digest --weekly`` — compose summary+daily+watch into one report file.

The metrics surface of the ecosystem incrementality spec
(``devin-powerups/docs/incrementality.md``). The cursor is the shared
``devin_internals.cursor.Cursor`` — ``metrics-digest-state.json`` keyed by
the digested ISO week (``2026-W42``). Envelope rules apply:

- the report is written atomically (tmp + ``os.replace``), never in place;
- the cursor commits *after* the report is durable — a crash re-digests,
  never skips;
- unreadable/corrupt state is an empty cursor — fail-open full run;
- ``DEVIN_CURSOR_DIR`` overrides the ``.devin/`` anchor (tests, offload).

All aggregation is delegated to :mod:`devin_metrics.aggregate` and rendering
to :mod:`devin_metrics.render` — this module is composition + envelope only.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from devin_internals.cursor import Cursor

from devin_metrics.aggregate import by_day, summarize, watch_report
from devin_metrics.collect import MetricsSnapshot
from devin_metrics.render import (
    render_daily_md,
    render_summary_md,
    render_watch_md,
)

CURSOR_NAME = "metrics-digest"
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")


def iso_week_key(dt: datetime) -> str:
    """``YYYY-Www`` ISO-8601 week key for ``dt`` (e.g. ``2026-W42``)."""
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_key_for(value: str | None, now: datetime | None = None) -> str:
    """Resolve the digest's target week.

    ``None`` → the current ISO week (``now`` injectable for tests);
    ``YYYY-Www`` normalizes and passes through; an ISO date or epoch-ms
    maps to the week containing it — the ``--since`` leg of the ecosystem
    pattern: an explicit baseline that overrides the wall clock.
    """
    now = now or datetime.now(timezone.utc)
    if not value:
        return iso_week_key(now)
    text = value.strip()
    m = _WEEK_RE.match(text)
    if m:
        return f"{m.group(1)}-W{int(m.group(2)):02d}"
    if text.isdigit():
        return iso_week_key(
            datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc))
    try:
        return iso_week_key(datetime.fromisoformat(text))
    except ValueError:
        raise ValueError(
            f"invalid week {value!r} — expected YYYY-Www, an ISO date "
            "or epoch milliseconds") from None


def report_name(week: str) -> str:
    """File name for a week's digest — ``digest-YYYY-Www.md``."""
    return f"digest-{week}.md"


@dataclass(frozen=True)
class DigestResult:
    week: str
    path: Path
    written: bool
    skipped_reason: str | None = None


def render_digest_md(
    *,
    week: str,
    snap: MetricsSnapshot,
    summary: dict[str, Any],
    daily_rows: list[dict[str, Any]],
    watch: dict[str, Any],
    generated_at: str,
) -> str:
    """One markdown report: summary + daily + watch under a week header."""
    parts = [
        f"# devin-metrics weekly digest — {week}",
        "",
        f"_generated {generated_at} · {len(snap.sessions)} sessions in store_",
        "",
        render_summary_md(summary),
        "",
        "## Daily activity",
        "",
        render_daily_md(daily_rows) if daily_rows else "_no activity days_",
        "",
        "## Watch (advisory context guard)",
        "",
        render_watch_md(watch),
        "",
        "---",
        "_local-only report · composed by `devin-metrics digest --weekly`_",
    ]
    return "\n".join(parts)


def write_atomic(path: Path, text: str) -> None:
    """tmp + ``os.replace`` — readers never see a partial report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run_digest(
    snap: MetricsSnapshot,
    out_dir: str | Path,
    *,
    week: str | None = None,
    days: int | None = 7,
    session_warn: int = 400_000,
    daily_warn: int = 2_000_000,
    force: bool = False,
    cursor: Cursor | None = None,
    now: datetime | None = None,
) -> DigestResult:
    """Write ``digest-<week>.md`` under ``out_dir``; skip if already done.

    The ``metrics-digest`` cursor records ``last_week`` (the ISO week key)
    plus a ``watermark`` (max ``last_activity_at`` observed at digest time)
    and ``last_digest_at``. A second run in the same week is a no-op —
    :attr:`DigestResult.written` is false — unless ``force``. The cursor
    commits only after the report is on disk.
    """
    now = now or datetime.now(timezone.utc)
    week = week or iso_week_key(now)
    out_dir = Path(out_dir)
    path = out_dir / report_name(week)
    cursor = cursor if cursor is not None else Cursor(CURSOR_NAME)

    if not force and cursor.get("last_week") == week:
        return DigestResult(
            week=week, path=path, written=False,
            skipped_reason=f"already digested {week}")

    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = render_digest_md(
        week=week,
        snap=snap,
        summary=summarize(snap),
        daily_rows=by_day(snap, days=days),
        watch=watch_report(snap, session_warn, daily_warn),
        generated_at=generated_at,
    )
    write_atomic(path, text)

    cursor.set("last_week", week)
    cursor.set("last_digest_at", generated_at)
    watermark = max(
        (int(s.last_activity_at or 0) for s in snap.sessions), default=0)
    if watermark:
        cursor.set("watermark", watermark)
    cursor.commit()
    return DigestResult(week=week, path=path, written=True)
