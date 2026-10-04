"""Thin CLI wrapper — all logic lives in the library modules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from devin_internals.schema import SchemaError

from devin_evals import __version__
from devin_evals.cases import CaseError, builtin_packs, load_cases
from devin_evals.corpus import generate_corpus, verify_corpus
from devin_evals.runner import run_evals


def _cmd_abrun(args: argparse.Namespace) -> int:
    """EV-5: A/B two fresh sessions, then grade both with the rubric."""
    import json as _json
    from devin_evals.abrun import run_ab
    from devin_evals.runner import evaluate_case
    from devin_internals.parsers.sessions import SessionsStore
    from devin_evals.cases import load_cases
    from devin_evals.judge import judge_available
    if args.dry_run:
        print(_json.dumps({
            "dry_run": True, "task": args.task,
            "variant_a": args.variant_a, "variant_b": args.variant_b,
            "would_create": [f"ab-run:{args.tag}:a", f"ab-run:{args.tag}:b"],
            "note": "real run consumes tokens; sessions are labelled for "
                    "janitor",
        }, indent=2))
        return _OK
    ok, msg = judge_available()
    if not ok:
        print(f"error: {msg}", file=sys.stderr)
        return _USAGE
    res = run_ab(args.task, args.variant_a, args.variant_b,
                 repo=args.repo, tag=args.tag, timeout_s=args.timeout)
    print(_json.dumps(res, indent=2))
    if not res.get("ok"):
        return _FAILED
    # grade both sessions against the case rubric
    try:
        cases = load_cases(args.evals, packs_dir=args.packs_dir)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    case = next((c for c in cases if c.id == args.case), cases[0] if cases else None)
    if case is None:
        print("error: no eval case to grade against", file=sys.stderr)
        return _USAGE
    scores = {}
    with SessionsStore(args.sessions_db) as store:
        for variant, r in res["variants"].items():
            sid = r.get("session_id")
            if not sid:
                scores[variant] = {"error": "no session id captured"}
                continue
            outcome = evaluate_case(case.__class__(**{**case.__dict__,
                                                     "session_ref": sid}),
                                    store)
            scores[variant] = {"session_id": sid,
                               "status": outcome["status"],
                               "score": outcome["score"]}
    print(_json.dumps({"case": case.id, "scores": scores}, indent=2))
    return _OK


def _cmd_judge(args: argparse.Namespace) -> int:
    """EV-1: opt-in LLM judge — fail-closed without DEVIN_BRIDGE_CMD."""
    import json as _json
    from devin_evals.judge import build_prompt, judge_available, run_judge
    ok, msg = judge_available()
    if not ok:
        print(f"error: {msg}", file=sys.stderr)
        return _USAGE
    try:
        cases = load_cases(args.evals, packs_dir=args.packs_dir)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    case = next((c for c in cases if c.id == args.case), None)
    if case is None:
        print(f"error: no case {args.case!r} in {args.evals}", file=sys.stderr)
        return _USAGE
    evidence = {"note": "offline evidence assembly — the judge sees "
                        "the case description + your question"}
    res = run_judge(
        build_prompt(case.id, case.description, evidence, args.question),
        cwd=args.cwd, case_id=case.id)
    print(_json.dumps({"case": case.id, "deterministic": False, **res},
                      indent=2))
    if not res.get("ok"):
        return _FAILED
    return _OK if res["verdict"] == "pass" else _FAILED

_OK, _FAILED, _USAGE = 0, 1, 2


def _cmd_packs(args: argparse.Namespace) -> int:
    packs = builtin_packs()
    if not packs:
        print("no built-in packs")
        return _OK
    import json as _json
    for pack_id, p in sorted(packs.items()):
        data = _json.loads(p.read_text(encoding="utf-8"))
        checks = len(data.get("rubric", []))
        print(f"{pack_id}\t{checks} checks\t{data.get('description', '')}")
    return _OK


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        cases = load_cases(args.evals, packs_dir=args.packs_dir)
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
        report = run_evals(args.evals, args.sessions_db, out_dir=args.out,
                           packs_dir=args.packs_dir)
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


def _cmd_corpus_generate(args: argparse.Namespace) -> int:
    try:
        manifest = generate_corpus(
            args.out, seed=args.seed, generator=args.generator)
    except (CaseError, SchemaError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    n = len(manifest["cases"])
    gaps = len(manifest["known_gaps"])
    print(f"corpus: {n} golden case(s) ({gaps} documented grader gap(s)) "
          f"via generator={manifest['generator']} -> {Path(args.out).resolve()}")
    print(f"verify with: devin-evals corpus verify --corpus {args.out}")
    return _OK


def _cmd_corpus_verify(args: argparse.Namespace) -> int:
    try:
        report = verify_corpus(
            args.corpus, strict=args.strict, out_dir=args.out)
    except (CaseError, SchemaError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    for c in report["cases"]:
        tag = {"match": "MATCH", "gap": "GAP", "mismatch": "MISMATCH"}[
            c["result"]]
        print(f"{tag:8}  {c['id']}  "
              f"(expected={c['expected_status']} actual={c['actual_status']})")
    gaps = [c for c in report["cases"] if c["result"] == "gap"]
    if gaps:
        print("known grader gaps (documented, tolerated):")
        for c in gaps:
            print(f"  {c['defect'] or c['id']}: {c['known_gap']}")
    s = report["summary"]
    print(f"corpus: {s['matched']}/{s['total']} expectations matched, "
          f"{s['gaps']} known gap(s), {s['mismatched']} mismatch(es)")
    return _OK if s["ok"] else _FAILED


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
        "--packs-dir",
        help="directory of custom rubric packs (shadows the built-ins)",
    )
    run.add_argument(
        "--out",
        default="eval-report",
        help="output directory for report.json/report.md (default: eval-report)",
    )
    run.set_defaults(func=_cmd_run)

    ls = sub.add_parser("list", help="list eval cases in a directory")
    ls.add_argument("--evals", required=True, help="directory of *.json eval cases")
    ls.add_argument("--packs-dir", help="directory of custom rubric packs")
    ls.set_defaults(func=_cmd_list)

    pk = sub.add_parser(
        "packs", help="list the built-in rubric packs (bugfix, feature, refactor)")
    pk.set_defaults(func=_cmd_packs)

    ju = sub.add_parser(
        "judge",
        help="EV-1: opt-in LLM judge for one case (non-deterministic; "
        "needs DEVIN_BRIDGE_CMD — sessions are labelled judge:<id> for "
        "janitor to reap)")
    ju.add_argument("case", help="eval case id to judge")
    ju.add_argument("--evals", required=True,
                    help="directory of *.json eval cases")
    ju.add_argument("--packs-dir", help="directory of custom rubric packs")
    ju.add_argument("--cwd", default=".",
                    help="working dir for the judge session (default: .)")
    ju.add_argument("--question", required=True,
                    help="the judgment question, e.g. 'did the session "
                    "address the user request?'")
    ju.set_defaults(func=_cmd_judge)

    ab = sub.add_parser(
        "ab-run",
        help="EV-5/G3: run a task in two fresh bridge sessions (variant A "
        "vs B), then grade both — consumes real tokens, opt-in, "
        "fail-closed without DEVIN_BRIDGE_CMD")
    ab.add_argument("--task", required=True,
                    help="the task prompt sent to both sessions")
    ab.add_argument("--variant-a", default="",
                    help="prefix for session A (e.g. no-skill baseline)")
    ab.add_argument("--variant-b", default="",
                    help="prefix for session B (e.g. skill context)")
    ab.add_argument("--repo", required=True,
                    help="working dir for both sessions")
    ab.add_argument("--tag", default="ab", help="label tag (default: ab)")
    ab.add_argument("--evals", help="eval cases dir for post-run grading")
    ab.add_argument("--case", help="case id to grade (default: first case)")
    ab.add_argument("--packs-dir", help="directory of custom rubric packs")
    ab.add_argument("--sessions-db",
                    help="sessions.db for grading (default: autodetect)")
    ab.add_argument("--timeout", type=int, default=45 * 60,
                    help="per-session timeout in seconds")
    ab.add_argument("--dry-run", action="store_true",
                    help="print what would run; creates no sessions")
    ab.set_defaults(func=_cmd_abrun)

    co = sub.add_parser(
        "corpus",
        help="golden-case corpus of labeled synthetic defect sessions (EV-3)")
    csub = co.add_subparsers(dest="corpus_command", required=True)

    cg = csub.add_parser(
        "generate",
        help="materialize synthetic sessions.db files + eval cases into a dir")
    cg.add_argument(
        "--out", default="corpus",
        help="output directory for the corpus (default: corpus/)")
    cg.add_argument(
        "--seed", type=int, default=0xDEE4,
        help="deterministic seed (default: 0xDEE4)")
    cg.add_argument(
        "--generator", choices=("auto", "dream", "vendored"),
        default="auto",
        help="session source: import devin_dream when available, else the "
        "vendored copy (default: auto)")
    cg.set_defaults(func=_cmd_corpus_generate)

    cv = csub.add_parser(
        "verify",
        help="replay a generated corpus and report expected-vs-actual")
    cv.add_argument(
        "--corpus", default="corpus",
        help="corpus directory produced by `corpus generate` (default: corpus/)")
    cv.add_argument(
        "--strict", action="store_true",
        help="fail the gate on documented grader gaps too, not just mismatches")
    cv.add_argument(
        "--out",
        help="optional output directory for verify-report.json")
    cv.set_defaults(func=_cmd_corpus_verify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
