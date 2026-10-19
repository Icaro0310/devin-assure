"""Install a scheduled ``devin-qa-pack audit`` sweep job.

Backends and registry format come from ``devin_install_scheduler`` (the
shared scheduling foundation also used by devin-state's janitor/backup —
cron, Task Scheduler, or the elapsed config-registry fallback ticked by
powerups' ``UserPromptSubmit`` hook).

The scheduled command is a **read-only audit sweep**: ``python -m
devin_qa_pack.cli audit --all --surface <S> --sessions-db
<data-dir>/cli/sessions.db --out <data-dir>/qa/audit-sweep.json`` using
the current interpreter, so it works from a venv/pipx install. It
writes only under the chosen data dir's ``qa/`` folder (the sweep
payload plus appended ``verdicts.jsonl`` lines — the same ledger
``session-end`` feeds) and never into sessions.db or any other agent
store, so the job does NOT require Devin closed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from devin_install_scheduler import install_daily as _install_daily

from devin_qa_pack.evidence import Surface
from devin_qa_pack.paths import default_data_dir

JOB_NAME = "devin-qa-pack-audit-daily"
OUT_NAME = "audit-sweep.json"


def _audit_command(surface: str, data_dir: str | None) -> str:
    """The scheduled command — read-only ``audit --all`` sweep.

    Both the devin-cli store and the output root are pinned to absolute
    paths resolved at install time: a scheduler's environment (cron's
    bare ``HOME``, a Task Scheduler entry) is minimal, so the job must
    not depend on ``DEVIN_DATA_DIR``/``XDG_DATA_HOME`` being set when it
    fires. ``--sessions-db`` pins only the devin-cli provider — the
    other surfaces still auto-discover under ``--surface auto``.
    """
    root = Path(data_dir).expanduser() if data_dir else default_data_dir()
    out = root / "qa" / OUT_NAME
    # Every interpolated value lands inside a double-quoted command that
    # cron runs via /bin/sh and Task Scheduler feeds to cmd. Inside
    # double quotes, $.../backticks still expand under sh and %VAR%
    # under cmd — a metacharacter in --data-dir (or a poisoned
    # DEVIN_DATA_DIR) would inject commands into the user's crontab.
    # Refuse instead of quoting around it (shlex output isn't portable
    # to schtasks).
    unsafe = re.compile(r'["`$%\r\n]')
    for value in (
        str(sys.executable),
        str(root / "cli" / "sessions.db"),
        str(out),
        surface,
    ):
        if unsafe.search(value):
            raise ValueError(
                "path cannot be embedded in a scheduled command: "
                f"{value!r} — pick a --data-dir without shell "
                "metacharacters"
            )
    return (
        f'"{sys.executable}" -m devin_qa_pack.cli audit --all '
        f'--surface {surface} --sessions-db "{root / "cli" / "sessions.db"}" '
        f'--out "{out}"'
    )


def install_daily(
    surface: str = Surface.AUTO,
    data_dir: str | None = None,
    config_dir: str | None = None,
    backend: str = "auto",
) -> dict:
    """Register the daily audit sweep job; returns what was done."""
    return _install_daily(
        JOB_NAME,
        _audit_command(surface, data_dir),
        config_dir=config_dir,
        backend=backend,
    )
