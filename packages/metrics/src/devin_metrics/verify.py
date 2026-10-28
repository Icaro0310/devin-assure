"""Integrity verification for recorded commit data — ``commits --verify``.

``commits`` derives session→commit refs from ``tool_call_state``
payloads at read time; ``--verify`` self-audits that derivation so a
tampered or torn store cannot pass silently:

- ``sha_format`` — every recorded sha is a full 40-hex string.
- ``duplicate_refs`` — no ``(session_id, tool_call_id, sha)`` triple
  appears twice; the extractor already dedups on that key, so a
  duplicate means the recorded data was altered outside it.
- ``orphan_refs`` — every ref's ``session_id`` exists in ``sessions``;
  a ref pointing at a deleted or foreign session is a broken
  reference, and the tamper case this command exists to catch.
- ``counts`` — the headline numbers ``commits_report`` publishes
  (``commit_refs``, ``unique_shas``, ``sessions_with_commits``) equal
  the values recomputed straight from the recorded refs.
- ``rollups`` — per-session ``n_commits``/commit lists and the
  per-repo rollup recompute identically; ``top_sessions`` is exactly
  ``sessions[:5]``.
- ``digest`` — a second, independent extraction pass over the store
  reproduces the recorded refs' canonical digest.

Overall ``status`` is ``clean`` only when every check passes;
otherwise ``mismatch`` with each offending detail listed explicitly.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

from devin_internals.commits import CommitRef, commit_references
from devin_internals.parsers import SessionsStore

from devin_metrics.aggregate import commits_report
from devin_metrics.collect import MetricsSnapshot

_SHA_RE = re.compile(r"[0-9a-f]{40}")


def _canonical(refs: list[CommitRef]) -> str:
    return "\n".join(
        sorted(
            f"{r.session_id}|{r.tool_call_id}|{r.field}|{r.sha}|"
            f"{r.repo_url or ''}"
            for r in refs
        )
    )


def _digest(refs: list[CommitRef]) -> str:
    return hashlib.sha256(_canonical(refs).encode("utf-8")).hexdigest()


def _second_pass(sessions_db: str | Path) -> list[CommitRef]:
    """Independent re-extraction — opens the store read-only, like
    ``collect()`` but without sharing its cursor."""
    with SessionsStore(Path(sessions_db).expanduser()) as store:
        fn = getattr(store, "iter_tool_call_state", None)
        rows = (
            fn(batch_size=200)
            if fn is not None
            else store.tool_call_state()
        )
        return list(commit_references(rows))


def _check(
    name: str, problems: list[str], ok_detail: str
) -> dict[str, Any]:
    if problems:
        return {
            "name": name,
            "status": "fail",
            "detail": f"{len(problems)} problem(s)",
            "mismatches": problems,
        }
    return {
        "name": name,
        "status": "pass",
        "detail": ok_detail,
        "mismatches": [],
    }


def _rollup_problems(
    refs: list[CommitRef], report: dict[str, Any]
) -> list[str]:
    """Recompute the per-session/per-repo rollups and diff them against
    what ``commits_report`` published."""
    problems: list[str] = []
    # Mirror commits_report's semantics exactly: a session's own
    # ``repos`` are the URLs its refs carried, while a sha's ``repos``
    # is the global linkage — a bare sha borrowed from another session
    # never leaks repo context into this session's row.
    by_session: dict[str, set[str]] = {}
    session_repos: dict[str, set[str]] = {}
    sha_repos: dict[str, set[str]] = {}
    for r in refs:
        by_session.setdefault(r.session_id, set()).add(r.sha)
        if r.repo_url:
            session_repos.setdefault(r.session_id, set()).add(r.repo_url)
            sha_repos.setdefault(r.sha, set()).add(r.repo_url)

    rows = {s["id"]: s for s in report["sessions"]}
    if set(rows) != set(by_session):
        problems.append(
            "session list differs from recomputed: "
            f"report={sorted(rows)} recomputed={sorted(by_session)}"
        )
    for sid, shas in by_session.items():
        row = rows.get(sid)
        if row is None:
            continue  # already flagged by the set diff above
        if row["n_commits"] != len(shas):
            problems.append(
                f"{sid}: n_commits report={row['n_commits']} "
                f"recomputed={len(shas)}"
            )
        expected_commits = [
            {"sha": sha, "repos": sorted(sha_repos.get(sha, ()))}
            for sha in sorted(shas)
        ]
        if row["commits"] != expected_commits:
            problems.append(
                f"{sid}: commits list differs from recomputed"
            )
        expected_session_repos = sorted(session_repos.get(sid, ()))
        if row["repos"] != expected_session_repos:
            problems.append(
                f"{sid}: repos report={row['repos']} "
                f"recomputed={expected_session_repos}"
            )

    repo_shas: dict[str, set[str]] = {}
    for sha, repos_for_sha in sha_repos.items():
        for repo in repos_for_sha:
            repo_shas.setdefault(repo, set()).add(sha)
    expected_repos = [
        {
            "repo": repo,
            "unique_shas": len(shas),
            "sessions": sum(
                1 for repos in session_repos.values() if repo in repos
            ),
        }
        for repo, shas in repo_shas.items()
    ]
    expected_repos.sort(key=lambda r: (-r["unique_shas"], r["repo"]))
    if report["repos"] != expected_repos:
        problems.append("repos rollup differs from recomputed")
    if report["top_sessions"] != report["sessions"][:5]:
        problems.append("top_sessions is not sessions[:5]")
    return problems


def verify_commits(
    snap: MetricsSnapshot, sessions_db: str | Path
) -> dict[str, Any]:
    """Verify the recorded commit data in ``snap`` (collected from
    ``sessions_db``) and return a JSON-ready report.

    The second extraction pass re-opens ``sessions_db`` — a store
    mutated or truncated between collection and verification surfaces
    as a ``digest`` mismatch.
    """
    refs = list(snap.commits)
    checks: list[dict[str, Any]] = []

    bad_sha = [
        f"{r.session_id}/{r.tool_call_id}: sha {r.sha!r} is not 40-hex"
        for r in refs
        if not _SHA_RE.fullmatch(r.sha)
    ]
    checks.append(
        _check("sha_format", bad_sha, f"{len(refs)} ref(s), all 40-hex")
    )

    counts = Counter(
        (r.session_id, r.tool_call_id, r.sha) for r in refs
    )
    dupes = sorted(
        f"{sid}/{tc}: {sha}" for (sid, tc, sha), n in counts.items()
        if n > 1
    )
    checks.append(
        _check("duplicate_refs", dupes,
               "no duplicated (session, tool call, sha) refs")
    )

    known = {s.id for s in snap.sessions}
    orphans = sorted({r.session_id for r in refs} - known)
    checks.append(
        _check(
            "orphan_refs",
            [f"{sid}: ref(s) point at a session not in the store"
             for sid in orphans],
            "every ref's session exists in sessions",
        )
    )

    report = commits_report(snap)
    count_problems = []
    for key, recomputed in (
        ("commit_refs", len(refs)),
        ("unique_shas", len({r.sha for r in refs})),
        ("sessions_with_commits", len({r.session_id for r in refs})),
    ):
        if report[key] != recomputed:
            count_problems.append(
                f"{key}: report={report[key]} recomputed={recomputed}"
            )
    checks.append(
        _check("counts", count_problems,
               "headline counts recompute identically")
    )

    checks.append(
        _check(
            "rollups",
            _rollup_problems(refs, report),
            "session/repo rollups recompute identically",
        )
    )

    second = _second_pass(sessions_db)
    digest, digest2 = _digest(refs), _digest(second)
    digest_problems = (
        []
        if digest == digest2
        else [
            (
                f"recorded digest {digest[:12]}… != recomputed "
                f"{digest2[:12]}… ({len(second)} ref(s) in second pass)"
            )
        ]
    )
    checks.append(
        _check(
            "digest",
            digest_problems,
            f"second extraction pass reproduces {digest[:12]}…",
        )
    )

    failed = [c for c in checks if c["status"] == "fail"]
    return {
        "status": "mismatch" if failed else "clean",
        "commit_refs": len(refs),
        "unique_shas": len({r.sha for r in refs}),
        "digest": digest,
        "checks": checks,
        "mismatches": sum(len(c["mismatches"]) for c in failed),
    }


def render_verify(report: dict[str, Any]) -> str:
    """Human summary — silent on passing checks, explicit on failures."""
    n = len(report["checks"])
    head = (
        f"commits verify: {report['commit_refs']} ref(s)"
        f" · digest {report['digest'][:12]}…"
    )
    failed = [c for c in report["checks"] if c["status"] == "fail"]
    if not failed:
        return f"{head} · clean — all {n} checks passed"
    lines = [f"{head} · MISMATCH — {len(failed)}/{n} check(s) failed"]
    for c in failed:
        lines.append(f"  FAIL {c['name']}: {c['detail']}")
        for m in c["mismatches"]:
            lines.append(f"       - {m}")
    return "\n".join(lines)
