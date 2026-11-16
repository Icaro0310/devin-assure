"""Semantic audit of ``done`` task-ledger claims — devin-assure consumer surface.

Distinct from :mod:`devin_qa_pack.ledger_audit` (open-loop *health* of the
ledger): this re-verifies the *done claims themselves* — every task the
ledger marks ``done`` — in two layers:

1. **Deterministic anchors.** ``commit:SHA``/``file:path`` evidence
   tokens are resolved against ``--repo`` — repeatable, because task
   ledgers span repos: an anchor is ``resolved`` on the first
   candidate that contains it and ``missing`` only when *no*
   candidate disproves it (a single-repo ``missing`` on a
   multi-repo ledger would be a false VIOLATION). ``commit:`` uses
   ``git cat-file -e <sha>^{commit}``. ``file:`` keeps the ledger's
   EPHEMERAL rule for *absolute* temp-dir paths (volatile loose
   evidence, never counted missing); a *relative* ``file:`` resolves
   against each candidate repo and stays verifiable even when a repo
   lives under ``/tmp`` (worktrees) — repo content is not volatile
   evidence. ``url:``/``pr:`` tokens stay UNVERIFIED offline
   (``needs_online``; ``--online`` HEAD-checks ``url:`` best-effort —
   a network failure is never a contradiction — while ``pr:`` always
   needs the forge API).
2. **Semantic judge (optional).** With the ``judge`` extra
   (``poordjaevin``) each task gets one batched Noul pass — one
   question per declared-met acceptance criterion plus one
   contradiction probe — mapped to a verdict by a deterministic
   fail-safe rule. The backend resolves like poordjaevin's own CLI:
   ``POORDJAEVIN_BACKEND`` selects it (default ``acp`` — Devin's own
   session, the free model). Judge unavailable or abstaining leaves
   the semantic layer UNVERIFIED — never VIOLATION on doubt.

Verdicts (this module's own namespace, not the session-audit ones):
``PASS`` / ``PARTIAL`` / ``UNVERIFIED`` / ``VIOLATION``.

- ``VIOLATION`` — a ``commit:``/``file:`` anchor is MISSING in the
  repo, or the judge denies a criterion / confirms a contradiction
  with usable confidence.
- ``PASS`` — every declared-met criterion is covered (``met``/``waived``)
  and supported: anchors resolve where resolvable plus judge confirms
  (judge on), or anchors resolve (deterministic ``--no-judge`` mode).
- ``PARTIAL`` — part supported: anchors resolve but ``met``/``waived``
  coverage is partial, or the judge confirms while coverage is partial.
- ``UNVERIFIED`` — no usable evidence, anchors not resolvable offline,
  or the judge abstains/is unavailable (fail-safe).

Exit codes: 0 = every audited task PASS · 1 = any
PARTIAL/UNVERIFIED/VIOLATION · 2 = could not run. A missing
``devin-memory`` optional dep reports the install hint and exits 0 —
a missing integration is not an audit failure (same contract as
``ledger-audit``).
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import ipaddress
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from devin_qa_pack import __version__

PASS = "PASS"
PARTIAL = "PARTIAL"
UNVERIFIED = "UNVERIFIED"
VIOLATION = "VIOLATION"

# Same abstain floor as the devin-devkit skill-catalog judge: an answer
# whose confidence lands inside poordjaevin's review band counts as an
# abstention, and abstention maps to UNVERIFIED — never to a decision.
JUDGE_ABSTAIN = 0.6

_PREFIX_RE = re.compile(r"^(commit|file|url|pr):")
# "commit:a,commit:b" glued by a comma is two tokens; a comma NOT
# followed by a known prefix stays inside the value (paths/URLs may
# legitimately contain one).
_GLUE_RE = re.compile(r",(?=(?:commit|file|url|pr):)")
_STRIP_CHARS = "\"'()[]{};,."
_GIT_TIMEOUT = 15
_URL_TIMEOUT = 10


def _import_ledger():
    """``devin_memory.ledger.Ledger`` or ``None`` when not installed."""
    try:
        from devin_memory.ledger import Ledger
        return Ledger
    except ImportError:
        return None


def _noul(statement: str):
    """Build a poordjaevin ``Noul`` — lazy so tests can stub it and a
    missing poordjaevin never breaks import."""
    from poordjaevin import Noul

    return Noul(statement)


# ---------------------------------------------------------------------------
# deterministic layer: evidence anchors
# ---------------------------------------------------------------------------


def _tokenize(evidence: str) -> tuple[list[dict[str, Any]], int]:
    """Split evidence into ``commit:``/``file:``/``url:``/``pr:`` anchor
    dicts plus a count of unstructured (prefix-less) tokens.

    Tokenization mirrors ``devin_memory.ledger.core.cmd_audit`` —
    whitespace split, edge punctuation stripped — plus the
    comma-glued-prefix split so ``commit:a,commit:b`` resolves both
    hashes instead of producing one malformed (falsely MISSING) token.
    """
    anchors: list[dict[str, Any]] = []
    unstructured = 0
    for raw in evidence.split():
        tok = raw.strip(_STRIP_CHARS)
        if not tok:
            continue
        for piece in _GLUE_RE.split(tok):
            if not piece:
                continue
            if _PREFIX_RE.match(piece):
                kind, _, val = piece.partition(":")
                anchors.append(
                    {
                        "token": piece,
                        "kind": kind,
                        "value": val,
                        "status": "pending",
                        "needs_online": False,
                    }
                )
            else:
                unstructured += 1
    return anchors, unstructured


def _under_tmp(p: Path, raw: str = "") -> bool:
    """Volatile-temp evidence — copy of
    ``devin_memory.ledger.core._under_tmp``: a ``/tmp/...`` prefix marks
    ephemeral evidence even where it is not absolute, and the resolved
    path is also compared against the platform tempdir."""
    if raw == "/tmp" or raw.startswith("/tmp/"):
        return True
    try:
        r = str(p.resolve())
        tmp = str(Path(tempfile.gettempdir()).resolve())
    except OSError:
        return False
    return r == tmp or r.startswith(tmp + os.sep)


def _is_git_repo(repo: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-dir"],
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT,
        )
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _norm_repos(repo: Any) -> list[Path]:
    """``--repo`` is repeatable: a str/Path becomes a one-element list,
    a list is used as-is, ``None``/empty falls back to ``."``."""
    if repo is None:
        return [Path(".")]
    if isinstance(repo, (str, Path)):
        return [Path(repo)]
    repos = [Path(r) for r in repo]
    return repos or [Path(".")]


def _resolve_anchors(
    anchors: list[dict[str, Any]],
    repos: list[tuple[Path, bool]],
    online: bool,
) -> None:
    """Resolve each anchor in place against every candidate repo —
    ``resolved`` on the first hit (the repo is recorded), ``missing``
    only when *no* candidate disproves it. Fail-safe: an environment
    failure (no git, not a repo, timeout, network down) degrades to
    ``unverified``, never to a contradiction."""
    for a in anchors:
        kind, val = a["kind"], a["value"]
        if not val:
            a["status"] = "unverified"  # "commit:" with no value
            continue
        if kind == "commit":
            _resolve_commit(a, repos, val)
        elif kind == "file":
            _resolve_file(a, repos, val)
        elif kind == "url":
            if online:
                a["status"] = "resolved" if _url_alive(val) else "unverified"
            else:
                a["status"] = "unverified"
                a["needs_online"] = True
        else:  # pr: — needs the forge API; never resolved here
            a["status"] = "unverified"
            a["needs_online"] = True


def _commit_status(repo: Path, sha: str) -> str:
    """``resolved``/``missing``/``unverified`` for one repo — only called
    for confirmed git repos."""
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
            capture_output=True,
            check=False,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unverified"
    # rc 0 = object exists; anything else (128 bad object, 129 usage)
    # means the cited commit is not in this repo — a real contradiction.
    return "resolved" if r.returncode == 0 else "missing"


def _resolve_commit(
    a: dict[str, Any], repos: list[tuple[Path, bool]], sha: str
) -> None:
    saw_git = False
    saw_env_error = False
    for repo, is_git in repos:
        if not is_git:
            continue
        saw_git = True
        st = _commit_status(repo, sha)
        if st == "resolved":
            a["status"] = "resolved"
            a["repo"] = str(repo)
            return
        if st == "unverified":
            saw_env_error = True
    if saw_env_error or not saw_git:
        a["status"] = "unverified"
    else:
        # Every git candidate lacks the object — a real contradiction.
        a["status"] = "missing"


def _resolve_file(
    a: dict[str, Any], repos: list[tuple[Path, bool]], val: str
) -> None:
    p = Path(val)
    # "Absolute-looking" on any platform: ``Path.is_absolute()`` misses
    # ``/tmp/x`` on Windows (no drive → not absolute there), so also
    # accept a leading separator. Relative tokens must NOT hit the
    # ephemeral check — ``Path.resolve()`` would anchor them at the
    # process CWD, and a qa-pack run inside a ``/tmp`` worktree would
    # falsely mark repo files EPHEMERAL.
    looks_absolute = p.is_absolute() or val.startswith("/")
    # Not absolutes: ``D:docs/x`` (drive-relative) and ``\\docs\\x``
    # (root-relative to the current drive) — on Windows they anchor at
    # the current directory/drive, so they must fall through to the
    # repo-join below instead of checking a bogus standalone path.
    # Volatile-temp evidence is EPHEMERAL on every platform: a
    # ``/tmp/...`` token written on Linux stays volatile even when the
    # audit runs on Windows.
    if looks_absolute and _under_tmp(p, val):
        a["status"] = "ephemeral"
        return
    if looks_absolute:
        a["status"] = _exists_status(p)
        return
    # Relative path resolves against each candidate repo and stays
    # verifiable even when a repo lives under the temp dir (e.g. /tmp
    # worktrees): the file is repo content, not loose volatile
    # evidence, so it is checked with plain exists(). A candidate that
    # does not exist on disk is not usable — it degrades to
    # unverified rather than falsely disproving the anchor.
    saw_unverified = False
    saw_candidate = False
    for repo, _is_git in repos:
        if not repo.is_dir():
            saw_unverified = True
            continue
        saw_candidate = True
        st = _exists_status(repo / val)
        if st == "resolved":
            a["status"] = "resolved"
            a["repo"] = str(repo)
            return
        if st == "unverified":
            saw_unverified = True
    if saw_unverified or not saw_candidate:
        a["status"] = "unverified"
    else:
        a["status"] = "missing"


def _exists_status(p: Path) -> str:
    try:
        return "resolved" if p.exists() else "missing"
    except OSError:
        return "unverified"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Ledger URLs must not pivot the audit into an internal service
    via a 30x hop — the redirect target itself is never requested."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _url_alive(url: str) -> bool:
    """Best-effort HEAD check for ``--online``. Any failure — DNS,
    timeout, 4xx/5xx, non-http scheme — is UNVERIFIED, never missing:
    a network error does not prove the evidence is false. Destinations
    are restricted: http(s) only, the host must resolve exclusively to
    globally-routable addresses (loopback/private/link-local refused),
    and redirects are never followed."""
    try:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        for info in socket.getaddrinfo(parts.hostname, None):
            if not ipaddress.ip_address(info[4][0]).is_global:
                return False
        req = urllib.request.Request(
            url,
            method="HEAD",
            headers={"User-Agent": f"devin-qa-pack/{__version__}"},
        )
        opener = urllib.request.build_opener(_NoRedirect)
        with opener.open(req, timeout=_URL_TIMEOUT) as r:
            return 200 <= r.status < 400
    except urllib.error.HTTPError as exc:
        return 200 <= exc.code < 400
    except Exception:  # noqa: BLE001 - offline audit must never crash on net
        return False


def _anchor_state(
    anchors: list[dict[str, Any]], unstructured: int
) -> str:
    """``contradicted`` > ``supported`` > ``unverifiable`` > ``none``."""
    if any(a["status"] == "missing" for a in anchors):
        return "contradicted"
    if any(a["status"] == "resolved" for a in anchors):
        return "supported"
    if anchors or unstructured:
        return "unverifiable"
    return "none"


# ---------------------------------------------------------------------------
# criteria coverage (met / waived)
# ---------------------------------------------------------------------------


def _met_set(met_raw: Any) -> set[int]:
    out: set[int] = set()
    for i in met_raw or []:
        try:
            out.add(int(i))
        except (TypeError, ValueError):
            continue
    return out


def _coverage(accept: list[str], met: set[int], waived: str) -> str:
    """``full`` every criterion met or waived · ``partial`` some ·
    ``none`` nothing covered (also when the task has no criteria —
    vacuous truth is not evidence)."""
    if not accept:
        return "none"
    covered = sum(
        1 for i in range(1, len(accept) + 1) if i in met or waived
    )
    if covered == 0:
        return "none"
    return "full" if covered == len(accept) else "partial"


def _criteria(
    accept: list[str], met: set[int], waived: str
) -> list[dict[str, Any]]:
    rows = []
    for i, text in enumerate(accept, 1):
        is_met = i in met
        is_waived = (not is_met) and bool(waived)
        rows.append(
            {
                "index": i,
                "text": text,
                "met": is_met,
                "waived": is_waived,
                "covered": is_met or is_waived,
                "judge": None,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# semantic layer: optional poordjaevin judge
# ---------------------------------------------------------------------------


def _build_judge() -> tuple[Any, str | None, str | None]:
    """``(client, backend_name, detail)`` — ``client`` is ``None`` when
    the judge is unavailable. Backend selection follows poordjaevin's
    own convention (``poordjaevin.mcp_server._select_backend``):
    ``POORDJAEVIN_BACKEND`` picks it, unset defaults to ``acp`` — the
    free Devin session model, never a paid/local-by-default backend."""
    try:
        from poordjaevin import Client
    except ImportError:
        return (
            None,
            None,
            (
                "poordjaevin not installed "
                "(pip install 'devin-qa-pack[judge]')"
            ),
        )
    backend = None
    backend_name = "local_nli(default)"
    try:
        from poordjaevin.mcp_server import _select_backend

        backend = _select_backend()
        backend_name = (
            type(backend).__name__ if backend is not None else "local_nli"
        )
    except ImportError:
        pass  # older poordjaevin without _select_backend → Client default
    except Exception as exc:  # noqa: BLE001 - unresolvable backend = unavailable
        return None, None, f"backend selection failed: {exc}"
    try:
        from poordjaevin.policy import Policy

        # The ACP backend is the sanctioned free model of this
        # ecosystem (same Devin credentials as the rest of the
        # tooling) — remote use is deliberate here, not incidental.
        return (
            Client(
                backend=backend,
                abstain_threshold=JUDGE_ABSTAIN,
                policy=Policy(allow_remote=True),
            ),
            backend_name,
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return None, None, f"client build failed: {exc}"


def _judge_state_text(
    title: str,
    accept: list[str],
    met: set[int],
    waived: str,
    waived_by: str,
    evidence: str,
) -> str:
    lines = [
        "Task ledger entry marked DONE.",
        f"Title: {title}",
        "",
        "Acceptance criteria:",
    ]
    lines += [f"{i}. {c}" for i, c in enumerate(accept, 1)] or ["(none)"]
    met_idx = sorted(i for i in met if 1 <= i <= len(accept))
    lines += [
        "",
        "Evidence recorded at done:",
        evidence.strip() or "(none)",
        "",
        "Criteria declared met: "
        + (", ".join(str(i) for i in met_idx) or "none"),
        "Waived: " + (waived or "none")
        + (f" (authorized by {waived_by})" if waived_by else ""),
    ]
    return "\n".join(lines)


def _answer_info(a: Any) -> dict[str, Any]:
    """Normalize a NoulAnswer-ish object. Missing, non-numeric,
    non-finite or out-of-range fields — and a non-bool ``value`` —
    count as unusable, i.e. abstained: garbage never decides."""
    if a is None:
        return {
            "value": None,
            "prob": None,
            "confidence": None,
            "abstained": True,
            "usable": False,
        }
    try:
        prob = float(getattr(a, "prob", None))
        conf = float(getattr(a, "confidence", None))
    except (TypeError, ValueError):
        prob = conf = None
    usable = (
        prob is not None
        and conf is not None
        and math.isfinite(prob)
        and math.isfinite(conf)
        and 0.0 <= prob <= 1.0
    )
    value = getattr(a, "value", None)
    if usable and not isinstance(value, bool):
        usable = False
        value = None
    abstained = bool(getattr(a, "abstained", False)) or not usable
    return {
        "value": value,
        "prob": round(prob, 4) if prob is not None else None,
        "confidence": round(conf, 4) if conf is not None else None,
        "abstained": abstained,
        "usable": usable,
    }


def _judge_task(
    title: str,
    accept: list[str],
    met: set[int],
    waived: str,
    waived_by: str,
    evidence: str,
    judge_ctx: dict[str, Any],
) -> tuple[str, str | None, dict[str, Any]]:
    """One batched ``client.ask`` per task: a Noul per declared-met
    criterion plus one contradiction probe. Returns
    ``(state, detail, results)`` where state is
    ``confirms``/``denies``/``abstains``/``unavailable``."""
    if not judge_ctx["enabled"]:
        return "off", None, {}
    client = judge_ctx["client"]
    if client is None:
        return (
            "unavailable",
            judge_ctx["detail"] or "judge backend unavailable",
            {},
        )
    asked = sorted(i for i in met if 1 <= i <= len(accept))
    state_text = _judge_state_text(
        title, accept, met, waived, waived_by, evidence
    )
    questions = {
        f"c{i}": _noul(
            f"Acceptance criterion {i} was fulfilled: {accept[i - 1]}"
        )
        for i in asked
    }
    questions["contradiction"] = _noul(
        "The recorded evidence contradicts the task's objective or "
        "shows the claimed work was not actually done"
    )
    try:
        answers = client.ask(state_text, questions)
    except Exception as exc:  # noqa: BLE001 - any backend failure is unavailable
        # Circuit breaker: a dead backend fails every remaining task
        # the same way (bridge down, quota) — do not pay N timeouts.
        judge_ctx["dead"] = True
        judge_ctx["client"] = None
        judge_ctx["detail"] = (
            f"judge backend error: {type(exc).__name__}: {exc}"
        )
        return "unavailable", judge_ctx["detail"], {}

    results: dict[str, Any] = {"criteria": {}, "contradiction": None}
    any_abstain = False
    any_deny = False
    for i in asked:
        info = _answer_info(
            answers.get(f"c{i}") if isinstance(answers, dict) else None
        )
        results["criteria"][i] = info
        if info["abstained"]:
            any_abstain = True
        elif info["value"] is False:
            any_deny = True
    cinfo = _answer_info(
        answers.get("contradiction") if isinstance(answers, dict) else None
    )
    results["contradiction"] = cinfo
    if cinfo["abstained"]:
        any_abstain = True
    elif cinfo["value"] is True:
        any_deny = True

    if any_deny:
        jstate = "denies"
    elif any_abstain:
        jstate = "abstains"
    else:
        jstate = "confirms"
    return jstate, None, results


# ---------------------------------------------------------------------------
# verdict combination (fail-safe)
# ---------------------------------------------------------------------------


def _verdict(
    anchor_state: str,
    coverage: str,
    judge_state: str,
    judge_on: bool,
) -> str:
    if anchor_state == "contradicted" or judge_state == "denies":
        return VIOLATION
    if judge_on:
        if judge_state == "confirms":
            if coverage == "full":
                return PASS
            if coverage == "partial":
                return PARTIAL
        # abstains / unavailable: no semantic decision → UNVERIFIED,
        # never PASS without the judge's confirmation and never
        # VIOLATION on doubt.
        return UNVERIFIED
    # deterministic (--no-judge) mode
    if anchor_state == "supported":
        if coverage == "full":
            return PASS
        if coverage == "partial":
            return PARTIAL
    return UNVERIFIED


# ---------------------------------------------------------------------------
# task selection + orchestration
# ---------------------------------------------------------------------------


def _find_task(tasks: dict[str, dict], arg: str):
    """``(task, error)`` — exact id or unique ``DL-xxxx`` prefix."""
    if arg in tasks:
        return tasks[arg], None
    matches = [t for tid, t in tasks.items() if tid.startswith(arg)]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, (
            f"task prefix '{arg}' is ambiguous ({len(matches)} matches)"
        )
    return None, f"unknown task '{arg}'"


def _audit_task(
    t: dict[str, Any],
    repos: list[tuple[Path, bool]],
    online: bool,
    judge_ctx: dict[str, Any],
) -> dict[str, Any]:
    evidence = str(t.get("evidence") or "")
    accept = [str(c) for c in (t.get("accept") or [])]
    met = _met_set(t.get("met"))
    waived = str(t.get("waived") or "")
    waived_by = str(t.get("waived_by") or "")

    anchors, unstructured = _tokenize(evidence)
    _resolve_anchors(anchors, repos, online)
    anchor_state = _anchor_state(anchors, unstructured)
    coverage = _coverage(accept, met, waived)
    criteria = _criteria(accept, met, waived)

    judge_state, judge_detail, judge_results = _judge_task(
        str(t.get("title") or ""),
        accept,
        met,
        waived,
        waived_by,
        evidence,
        judge_ctx,
    )
    for c in criteria:
        c["judge"] = judge_results.get("criteria", {}).get(c["index"])

    verdict = _verdict(
        anchor_state, coverage, judge_state, judge_ctx["enabled"]
    )
    return {
        "id": str(t.get("id") or ""),
        "title": str(t.get("title") or ""),
        "status": str(t.get("status") or ""),
        "verdict": verdict,
        "coverage": coverage,
        "anchor_state": anchor_state,
        "unstructured_tokens": unstructured,
        "anchors": anchors,
        "criteria": criteria,
        "judge": {
            "state": judge_state,
            "backend": judge_ctx["backend"] if judge_ctx["enabled"] else None,
            "detail": judge_detail,
            "results": judge_results or None,
        },
        "evidence": evidence,
        "met": sorted(met),
        "waived": waived,
        "waived_by": waived_by,
    }


def run(
    ledger_dir: str | None = None,
    repo: str | list[str] | None = ".",
    task_id: str | None = None,
    limit: int | None = None,
    judge: bool = True,
    online: bool = False,
) -> dict[str, Any]:
    """Audit ``done`` tasks of the ledger. Returns the report dict —
    ``{"error", "install"}`` when ``devin-memory`` is missing,
    ``{"error", "usage_error": True}`` when the audit could not run."""
    Ledger = _import_ledger()
    if Ledger is None:
        return {
            "error": "devin-memory not installed",
            "install": "pip install 'devin-qa-pack[ledger]'",
        }
    if limit is not None and limit < 1:
        return {
            "error": "--limit must be >= 1",
            "usage_error": True,
        }
    ledger = Ledger(ledger_dir) if ledger_dir else Ledger()
    try:
        tasks = ledger.tasks()
    except SystemExit as exc:  # core.load() die()s on a corrupt ledger
        return {
            "error": f"cannot read ledger (exit {exc.code})",
            "usage_error": True,
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"cannot read ledger: {exc}", "usage_error": True}

    if task_id:
        found, err = _find_task(tasks, task_id)
        if err:
            return {"error": err, "usage_error": True}
        assert found is not None
        if found.get("status") != "done":
            return {
                "error": (
                    f"{found.get('id')} is {found.get('status')}, not "
                    "done — audit-ledger only audits done tasks"
                ),
                "usage_error": True,
            }
        selected = [found]
    else:
        done = [t for t in tasks.values() if t.get("status") == "done"]
        done.sort(key=lambda t: str(t.get("updated") or ""), reverse=True)
        selected = done[:limit] if limit is not None else done
    if not selected:
        return {
            "error": "no done tasks to audit"
            + (" matching --task" if task_id else ""),
            "usage_error": True,
        }

    repo_paths = _norm_repos(repo)
    repos = [(rp, _is_git_repo(rp)) for rp in repo_paths]

    client, backend_name, judge_detail = (
        _build_judge() if judge else (None, None, None)
    )
    judge_ctx: dict[str, Any] = {
        "enabled": judge,
        "client": client,
        "backend": backend_name,
        "detail": judge_detail,
        "dead": False,
    }

    rows = [
        _audit_task(t, repos, online, judge_ctx)
        for t in selected
    ]
    counts = {v: 0 for v in (PASS, PARTIAL, UNVERIFIED, VIOLATION)}
    for r in rows:
        counts[r["verdict"]] += 1
    return {
        "version": __version__,
        "ledger_dir": str(getattr(ledger, "dir", ledger_dir or "")),
        "repo": str(repo_paths[0]),
        "repos": [str(rp) for rp in repo_paths],
        "judge": {
            "enabled": judge,
            # judge_ctx, not the initial client — a mid-run backend
            # failure clears the reference via the circuit breaker.
            "available": judge_ctx["client"] is not None,
            "backend": backend_name,
            "detail": judge_ctx["detail"] if judge else None,
        },
        "tasks": rows,
        "summary": {"total": len(rows), **counts},
    }


# ---------------------------------------------------------------------------
# rendering + exit codes
# ---------------------------------------------------------------------------


def render_text(rep: dict[str, Any]) -> str:
    if "error" in rep:
        line = f"error: {rep['error']}"
        if rep.get("install"):
            line += f" — install: {rep['install']}"
        return line
    s = rep["summary"]
    lines = [
        (
            f"audit-ledger: {s['total']} done task(s) — "
            f"PASS={s['PASS']} PARTIAL={s['PARTIAL']} "
            f"UNVERIFIED={s['UNVERIFIED']} VIOLATION={s['VIOLATION']}"
        )
    ]
    j = rep.get("judge") or {}
    if j.get("enabled"):
        if j.get("available"):
            lines.append(
                f"judge: on (backend={j.get('backend') or 'default'})"
            )
        else:
            lines.append(
                f"judge: unavailable ({j.get('detail') or 'unknown'})"
            )
    for t in rep["tasks"]:
        lines.append(_render_task(t))
    return "\n".join(lines)


def _render_task(t: dict[str, Any]) -> str:
    head = f"{t['verdict']:<11} {t['id']}  {t['title'][:70]}"
    details: list[str] = []
    for a in t["anchors"]:
        if a["status"] == "missing":
            details.append(f"    MISSING {a['token']}")
    pending_net = [
        a["token"]
        for a in t["anchors"]
        if a["status"] == "unverified" and a.get("needs_online")
    ]
    if pending_net and not any(
        a["status"] == "missing" for a in t["anchors"]
    ):
        shown = ", ".join(pending_net[:3])
        if len(pending_net) > 3:
            shown += ", …"
        details.append(f"    unverified-offline: {shown}")
    if t.get("coverage") == "partial":
        details.append(
            "    coverage partial — met/waived does not cover every "
            "acceptance criterion"
        )
    j = t.get("judge") or {}
    jstate = j.get("state")
    if jstate == "denies":
        for i, info in (j.get("results") or {}).get(
            "criteria", {}
        ).items():
            if (
                info
                and info.get("value") is False
                and not info.get("abstained")
            ):
                details.append(f"    judge denies criterion {i}")
        contra = (j.get("results") or {}).get("contradiction") or {}
        if contra.get("value") is True and not contra.get("abstained"):
            details.append("    judge: evidence contradicts the task")
    elif jstate in ("abstains", "unavailable"):
        detail = j.get("detail")
        details.append(
            f"    judge {jstate}" + (f" — {detail}" if detail else "")
        )
    return "\n".join([head] + details)


def exit_code(rep: dict[str, Any]) -> int:
    if "error" in rep:
        return 2 if rep.get("usage_error") else 0
    s = rep["summary"]
    if s["total"] == 0:
        return 2
    if s[PARTIAL] or s[UNVERIFIED] or s[VIOLATION]:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="devin-qa-pack audit-ledger",
        description="Semantic audit of done task-ledger claims: "
        "evidence anchors resolved against --repo plus an optional "
        "poordjaevin judge over the acceptance criteria.",
    )
    ap.add_argument(
        "--ledger-dir",
        metavar="PATH",
        default=None,
        help="override ledger store path (default: XDG)",
    )
    ap.add_argument(
        "--repo",
        action="append",
        default=None,
        metavar="PATH",
        help="git repo(s) used to resolve commit:/file: anchors — "
        "repeatable for multi-repo ledgers (default: .)",
    )
    ap.add_argument(
        "--task",
        metavar="DL-xxxx",
        default=None,
        help="audit one task (exact id or unique prefix); must be done",
    )
    ap.add_argument(
        "--limit",
        type=int,
        metavar="N",
        default=None,
        help="max done tasks audited, most recently updated first",
    )
    ap.add_argument(
        "--no-judge",
        action="store_true",
        help="deterministic anchors only — skip the poordjaevin "
        "semantic layer",
    )
    ap.add_argument(
        "--online",
        action="store_true",
        help="allow live HEAD checks of url: evidence (opt-in; "
        "pr: still needs the forge API)",
    )
    ap.add_argument("--json", action="store_true", help="JSON output")
    a = ap.parse_args(argv)
    rep = run(
        ledger_dir=a.ledger_dir,
        repo=a.repo,
        task_id=a.task,
        limit=a.limit,
        judge=not a.no_judge,
        online=a.online,
    )
    if a.json or "error" in rep:
        print(json.dumps(rep, indent=2, sort_keys=True))
    else:
        print(render_text(rep))
    return exit_code(rep)


if __name__ == "__main__":
    raise SystemExit(main())
