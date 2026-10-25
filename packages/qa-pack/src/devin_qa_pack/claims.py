"""Extract deliverable claims from a session's evidence messages.

A *claim* is an assertion the agent made about delivered work: tests
passed, a commit exists, a file was created/changed, work was pushed,
work was delegated to a subagent, an eval score or a coverage number
was reported. Only agent-role messages are scanned
— a user prompt quoting the same words is not a delivery claim,
and neither is tool output or a system prompt, which are evidence
channels, not assertions.

The input is the canonical :class:`EvidenceMessage` — store-specific
payload decoding (``chat_message`` JSON for the Devin store) happens in
the evidence providers, so this module only ever sees plain role+text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from devin_qa_pack.evidence import EvidenceMessage, normalize_role

TESTS = "tests"
TESTS_PASSED = "tests_passed"
COMMIT = "commit"
FILE = "file"
PUSH = "push"
HTTP = "http"
URL = "url"
DELEGATION = "delegation"
EVAL = "eval"
COVERAGE = "coverage"

CLAIM_KINDS = (
    TESTS, TESTS_PASSED, COMMIT, FILE, PUSH, HTTP, URL, DELEGATION,
    EVAL, COVERAGE,
)

_RUNNER_TOKENS = (
    "cargo test",
    "go test",
    "npm test",
    "pytest",
    "unittest",
    "vitest",
    "jest",
    "mocha",
    "tox",
    "nose",
)

# Clause-boundary-safe gap: a cue regex must never span a clause break —
# "did not run pytest, but tests passed" contains a denial and an
# affirmation; a gap that swallows the comma lets the earlier negator
# cancel the later claim.
_GAP = r"[^.\n,;!?—–]"
_TESTISH = r"(?:tests?|test suite|pytest|unittest|jest|vitest|mocha|tox|nose)"
_TESTS_RE = re.compile(
    rf"\b{_TESTISH}\b{_GAP}*?\b(?:pass(?:ed|es|ing)?|green|succeed(?:ed|ing)?|ok)\b"
    rf"|\b\d+\s+(?:{_TESTISH}\s+)?passed\b",
    re.IGNORECASE,
)
# "47 tests passed" / "108 passed, 1 skipped" — the captured group is the
# claimed pass count; like an `http` claim it checks *output values*, so
# it needs a recorded "N passed" in tool-call output to verify. Grouped
# totals ("1,234 tests passed") normalize to the plain integer — the tail
# of a comma group must never read as a smaller claim.
_PASSED_COUNT_RE = re.compile(
    r"(?<![\w,])(\d{1,3}(?:,\d{3})+|\d+)\s+(?:tests?\s+)?pass(?:ed|es)?\b",
    re.IGNORECASE,
)
_COMMIT_WORD_RE = re.compile(r"\bcommit(?:s|ted|ting)?\b", re.IGNORECASE)
_HEX_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_PUSH_RE = re.compile(
    r"\bgit\s+push\b"
    rf"|\bpush(?:ed|ing)?\b{_GAP}*\b(?:origin|upstream|remote|main|master|branch)\b",
    re.IGNORECASE,
)
_FILE_VERB_RE = re.compile(
    r"\b(?:created?|wrote|written|writing|added|adding|updated?|updating|"
    r"modified|changed|edited|generated|saved|deleted|removed|renamed|moved)\b",
    re.IGNORECASE,
)
_PATH_TOKEN_RE = re.compile(
    r"(?<![\w/.-])(?:[A-Za-z]:[\\/])?[A-Za-z0-9_-]+(?:[\\/][\w.@-]+)*"
    r"\.[A-Za-z0-9]{2,10}\b"
)

# "the API returned 200" / "endpoint responded with 404" / "HTTP 500" /
# "status code: 200" — the captured group is the claimed status code.
_URL_RE = re.compile(r"https://[\w.-]+(?::\d+)?(?:/[\w./?%&=~#+-]*)?")

# Delegation claims: a delegatee noun (subagent/worker) beside a
# delegation or result verb — "delegated the scan to a subagent",
# "spawned worker-2", "ran in parallel via subagent", "the subagent
# reported back". PT phrasings need no delegatee: a past-tense
# delegation verb ("deleguei", "delegado") or the delegatee noun
# itself ("subagente") is already the claim.
_DELEGATEE = r"(?:sub[-\s]?agente?s?|workers?)"
_DELEGATION_VERB = (
    r"delegat\w+|spawn\w+|launch\w+|dispatch\w+|offload\w+|"
    r"kick(?:ed|s)?\s+off|hand(?:ed|s)?\s+off|"
    r"ran|runs?|running|start\w+|sent|send(?:ing)?|us(?:ed|ing)"
)
_DELEGATION_RESULT = (
    r"complet\w+|finish\w+|return\w+|report\w+|found|handl\w+|"
    r"retorn\w+|conclu\w+|entreg\w+"
)
_DELEGATION_RE = re.compile(
    rf"\b(?:{_DELEGATION_VERB})\b{_GAP}{{0,60}}?\b{_DELEGATEE}\b"
    rf"|\bsub[-\s]?agente?s?\b{_GAP}{{0,60}}?\b(?:{_DELEGATION_VERB}|"
    rf"{_DELEGATION_RESULT})\b"
    # a bare ``worker`` reporting a result verb is ambiguous — "the
    # worker process completed the build" is ordinary activity, not a
    # delegation; require a delegation verb in that order instead
    rf"|\bworkers?\b{_GAP}{{0,60}}?\b(?:{_DELEGATION_VERB})\b"
    rf"|\b(?:deleguei|delegou|delegaram|delegad[oa]s?)\b"
    rf"|\bsub[-\s]?agentes?\b",
    re.IGNORECASE,
)

_CLAUSE_BREAK = r"[,;.!?:()\[\]—–]"

# Coordinators join assertions inside a punctuation-delimited clause.
# One ends a quantity negator's scope ("no failures and tests passed"
# still claims the tests) and opens a new conjunct a post-cue frame
# cannot reach back across ("tests passed and I will push" still
# claims the tests). "nor"/"neither" are negative coordinators — they
# extend a denial, so they are deliberately absent.
_COORDINATOR_RE = re.compile(
    r"\b(?:and|but|or|yet|so|then|e|mas|ou|por[eé]m)\b",
    re.IGNORECASE,
)

# Clausal denials — "not"/"never"/"nor" scope over the whole clause,
# including across coordinators ("did not test and deploy" asserts
# neither); PT adds "nem", "nunca", "jamais", "não". "not only"/"não
# só" are affirmations, not denials — "not only did tests pass" still
# claims the tests.
_CLAUSAL_NEGATION_RE = re.compile(
    r"\b(?:never|cannot|cant|nor|neither|nem|nunca|jamais)\b"
    r"|\bnot\b(?!\s+only\b)"
    r"|\bnão\b(?!\s+só\b)"
    r"|n['’]t\b",
    re.IGNORECASE,
)

# Quantity denials — "no", "none", "without", "zero" (PT "nenhum(a)",
# "sem") bind the noun they precede, not every later assertion in the
# clause: "no errors but coverage is 87%" still claims the coverage.
_QUANTITY_NEGATION_RE = re.compile(
    r"\b(?:no|none|without|zero|nenhum(?:a|s)?|sem)\b",
    re.IGNORECASE,
)

# Frames that make a clause not an assertion: intent/modality ("I will
# push", "I'm going to commit", "vou fazer push"), conditions ("if
# tests pass"), hedges ("probably pushed", "tests seem ok", "acho que
# passou") and reported speech ("the user said tests passed", "the
# spec requires a commit"). A cue under one describes a plan, a
# hypothetical or someone else's words — extracting it fabricates a
# claim the audit then disputes against work that was never asserted.
_NONASSERTION_RE = re.compile(
    # modals & future markers — "will push", "I'll commit", "we can
    # deploy", "tests would pass"
    r"\b(?:will|would|could|should|shall|must|may|might|can|gonna|"
    r"wanna|ought)\b|['’]ll\b"
    # purpose/obligation/request frames — "<verb> [<someone>] to":
    # "going to push", "need to run", "supposed to pass", "have to
    # deploy", "in order to", "asked me to push", "told us to commit",
    # "want the tests to pass"
    r"|\b(?:going|about|plan\w*|need\w*|want\w*|intend\w*|hope\w*|"
    r"tr(?:y|ies|ying)|aim\w*|mean\w*|expect\w*|supposed|prepar\w*|"
    r"have|has|had|used|ought|remember\w*|forg\w*|fail\w*|neglect\w*|"
    r"un(?:able|willing|likely)|ask\w*|tell\w*|order\w*|instruct\w*|"
    r"request\w*|direct\w*|in\s+order)"
    r"\s+(?:\w+\s+){0,4}to\b"
    # instructions — "make sure tests pass" is a directive, not a
    # report ("I made sure tests passed" still claims)
    r"|\bmake\s+sure\b"
    # permission/precursor frames — "let me push", "let's commit"
    r"|\blet\s+(?:me|us)\b|\blet['’]?s\b"
    # conditionals — "if tests pass", "unless they fail", "whether it
    # passed" are hypotheticals, not reports
    r"|\b(?:if|unless|whether)\b"
    # hedges — "probably pushed", "tests seem ok", "I think coverage
    # is 87%", "acho que passou"
    r"|\b(?:probably|possibly|maybe|perhaps|hopefully|apparently|"
    r"presumably|likely)\b"
    r"|\bseem\w*\b|\bappear\w*\b|\blooks?\s+like\b|\bsounds?\s+like\b"
    r"|\b(?:i|we)\s+(?:think|believe|guess|suppose|suspect|assume|"
    r"imagine|reckon|hope|feel)\b"
    # reported speech — "the user said tests passed", "the task
    # requires a commit": the words are someone else's, not the
    # agent's delivery claim
    r"|\b(?:user|task|spec|docs?|instructions?|ticket|issue|prompt|"
    r"requirements?)\s+(?:said|says?|asked?|told|wrote|reads?|states?|"
    r"requires?|requests?|mentions?|wants?|expects?)\b"
    # PT — futures ("vou", "irá", "pretendo", "planejo", "preciso",
    # "quero", "devo", "tenho que") and hedges ("acho", "parece",
    # "talvez", "provavelmente")
    r"|\b(?:vou|vamos|vai|ir[aá]|pretend\w*|planej\w*|quer\w*|"
    r"precis\w*|devo|deve|te(?:nho|m)\s+que)\b"
    r"|\b(?:acho|acredito|imagino|suponho|parece\w*|talvez|"
    r"provavelmente|possivelmente|aparentemente)\b",
    re.IGNORECASE,
)


def _match_suppressed(line: str, match: re.Match[str]) -> bool:
    """Whether the cue in ``match`` fails to assert a claim.

    Three clause-scoped frames mean "no claim" — a frame inside one
    clause never cancels a real claim in the next ("did not test, but
    committed abc123" still claims the commit):

    - a denial — "tests did not pass", "no coverage at 87%",
      "ficou sem push";
    - a non-assertion frame — intent ("I will push"), condition
      ("if tests pass"), hedge ("probably committed") or reported
      speech ("the user said tests pass"); the frame may trail the
      cue inside its clause ("tests pass if run");
    - a question — a clause closed by "?" asks, it does not assert
      ("did the tests pass?").

    A post-cue denial is *not* a frame — "committed a1b2c3d not
    b2b3c4d" still claims the first hash; the trailing "not" negates
    the second one.

    Coordinators bound both scopes differently per direction. A
    quantity negator stops at the next coordinator — "no failures and
    tests passed" still claims the tests — while clausal negators keep
    the whole clause. A modal or intent frame keeps the whole clause
    before the cue ("I will push and commit" plans both) but stops at
    the first coordinator after it — "tests passed and I will push"
    plans only the push.
    """
    prefix = line[: match.start()]
    # only the clause nearest the cue can negate it
    clause = re.split(_CLAUSE_BREAK, prefix)[-1]
    if _CLAUSAL_NEGATION_RE.search(clause + match.group(0)):
        return True
    conjunct = re.split(_COORDINATOR_RE, clause)[-1]
    if _QUANTITY_NEGATION_RE.search(conjunct + match.group(0)):
        return True
    # the frame check covers the cue's whole clause — a trailing
    # conditional/hedge ("tests pass if run") still voids the claim —
    # but a coordinator after the cue opens a new conjunct whose
    # frames govern that conjunct, not this one
    framed = (
        clause
        + match.group(0)
        + re.split(
            _COORDINATOR_RE,
            re.split(_CLAUSE_BREAK, line[match.end() :])[0],
        )[0]
    )
    if _NONASSERTION_RE.search(framed):
        return True
    # "?" closing the cue's clause marks a question, not a claim
    nxt = re.search(_CLAUSE_BREAK, line[match.end() :])
    return bool(nxt and nxt.group(0) == "?")

_HTTP_CLAIM_RES = (
    re.compile(
        r"\b(?:returned|responded|replied|answered|came\s+back)\b"
        rf"{_GAP}*?\b([1-5]\d{{2}})\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bHTTP\s+(?:status\s*(?:code)?\s*)?([1-5]\d{2})\b",
               re.IGNORECASE),
    re.compile(
        r"\bstatus\s*(?:code)?\s*(?:of|was|is|:|=)\s*([1-5]\d{2})\b",
        re.IGNORECASE,
    ),
)

# Eval-score claims: "the eval score was 0.92", "evals: 18/20 passed",
# "scored 85% on the benchmark" — the captured group is the claimed
# score literal (decimal, percent or N/M tally); like an `http` claim it
# checks *output values*, so it needs a recorded score in tool-call
# output to verify. A `%` suffix reads on the percent scale ("85%" →
# 85) — it never silently rescales to a 0-1 fraction.
_EVAL_WORD = r"(?:evals?|evaluations?|benchmarks?|avalia[çc][ãa]o(?:es)?)"
_EVAL_NUM = r"(?:\d+(?:\.\d+)?%?|\d+\s*/\s*\d+)"
_EVAL_CLAIM_RES = (
    # eval-word + metric word + value: "eval score was 0.92",
    # "the benchmark result: 18/20", "avaliação nota 0.9"
    re.compile(
        rf"\b{_EVAL_WORD}\b{_GAP}{{0,30}}?\b"
        rf"(?:score|scored|result|accuracy|nota|pontua[çc][ãa]o)\w*\b"
        rf"{_GAP}{{0,25}}?({_EVAL_NUM})(?![\w/%])",
        re.IGNORECASE,
    ),
    # bare "eval: 0.92" / "benchmark = 85%"
    re.compile(
        rf"\b{_EVAL_WORD}\b\s*[:=]\s*({_EVAL_NUM})(?![\w/%])",
        re.IGNORECASE,
    ),
    # score-verb + value + eval-word: "scored 0.92 on the eval",
    # "obteve 85% na avaliação"
    re.compile(
        rf"\b(?:scored?|earned|achieved|reached|obteve|atingiu)\b"
        rf"{_GAP}{{0,25}}?({_EVAL_NUM})(?![\w/%])"
        rf"{_GAP}{{0,30}}?\b{_EVAL_WORD}\b",
        re.IGNORECASE,
    ),
)

# Coverage claims: "coverage is at 87%", "coverage: 85.2%", "90% code
# coverage", PT "cobertura de 80%" — the captured group is the claimed
# percent (the `coverage`/`cobertura` keyword is always present, so a
# bare "85%" elsewhere is never a claim).
_COV_NUM = r"\d{1,3}(?:\.\d+)?"
_COVERAGE_CLAIM_RES = (
    re.compile(
        rf"\b(?:coverage|cobertura)\b[^.\n%,;!?—–]{{0,40}}?({_COV_NUM})\s*%",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b({_COV_NUM})\s*%\s+(?:[\w-]+\s+){{0,2}}(?:coverage|cobertura)\b",
        re.IGNORECASE,
    ),
)

_SKIP_ROLES = {"user", "human", "tool", "system"}
_MAX_EXCERPT = 160


@dataclass(frozen=True)
class Claim:
    """One deliverable claim found in an agent message.

    ``detail`` carries the checkable part: the test-runner name (or
    ``"tests"`` when generic), the commit hash, the claimed file path, or
    ``"push"``/``"delegation"`` for the detail-less action kinds.
    ``excerpt`` is the source line, truncated — evidence, not full row
    content.
    """

    kind: str
    detail: str
    session_id: str
    node_id: int | str
    excerpt: str


def _excerpt(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= _MAX_EXCERPT else line[: _MAX_EXCERPT - 1] + "…"


def _normalize_path(token: str) -> str:
    return token.replace("\\", "/").rstrip(".,;:!?)]}>")


def _norm_number(literal: str) -> str:
    """Canonical numeric claim/evidence literal: ``"85%"`` → ``"85"``,
    ``"18 / 20"`` → ``"18/20"``, ``"0.90"`` → ``"0.9"``. A percent reads
    on the percent scale — it never rescales to a 0-1 fraction, so an
    ``85%`` claim only corroborates a recorded 85, not a recorded 0.85."""
    lit = re.sub(r"\s*/\s*", "/", literal.strip().rstrip("%").strip())
    if "/" in lit:
        return lit
    try:
        return f"{float(lit):g}"
    except ValueError:
        return lit


def _runner_in(line: str) -> str | None:
    for token in _RUNNER_TOKENS:  # multi-word runners first
        for m in re.finditer(rf"\b{re.escape(token)}\b", line, re.IGNORECASE):
            # a runner named in a suppressed clause ("did not run
            # pytest", "I will run pytest") must not become the
            # claim's detail
            if not _match_suppressed(line, m):
                return token
    return None


def _claims_in_line(line: str) -> Iterable[tuple[str, str]]:
    if any(
        not _match_suppressed(line, m) for m in _TESTS_RE.finditer(line)
    ):
        yield TESTS, _runner_in(line) or TESTS
    for m in _PASSED_COUNT_RE.finditer(line):
        if not _match_suppressed(line, m):
            yield TESTS_PASSED, m.group(1).replace(",", "")
    if any(
        not _match_suppressed(line, m)
        for m in _COMMIT_WORD_RE.finditer(line)
    ):
        # each detail must live in an affirmed clause too — "did not
        # commit a1b2c3d; committed b1b2c3d" claims only the second hash
        for m in _HEX_RE.finditer(line):
            if not _match_suppressed(line, m):
                yield COMMIT, m.group(0).lower()
    if any(not _match_suppressed(line, m) for m in _PUSH_RE.finditer(line)):
        yield PUSH, PUSH
    if any(
        not _match_suppressed(line, m)
        for m in _FILE_VERB_RE.finditer(line)
    ):
        for m in _PATH_TOKEN_RE.finditer(line):
            if not _match_suppressed(line, m):
                yield FILE, _normalize_path(m.group(0))
    for rx in _HTTP_CLAIM_RES:
        for m in rx.finditer(line):
            if not _match_suppressed(line, m):
                yield HTTP, m.group(1)
    if any(
        not _match_suppressed(line, m)
        for m in re.finditer(
            r"\b(?:deployed|live|hosted|available|published|running)\b",
            line,
            re.IGNORECASE,
        )
    ):
        for m in _URL_RE.finditer(line):
            if not _match_suppressed(line, m):
                yield URL, m.group(0).rstrip(".,);'\"")
    if any(
        not _match_suppressed(line, m)
        for m in _DELEGATION_RE.finditer(line)
    ):
        yield DELEGATION, DELEGATION
    for rx in _EVAL_CLAIM_RES:
        for m in rx.finditer(line):
            if not _match_suppressed(line, m):
                yield EVAL, _norm_number(m.group(1))
    for rx in _COVERAGE_CLAIM_RES:
        for m in rx.finditer(line):
            if not _match_suppressed(line, m):
                yield COVERAGE, _norm_number(m.group(1))


def _collect(
    session_id: str,
    node_id: int | str,
    text: str,
    claims: list[Claim],
    seen: set[tuple[str, str]],
) -> None:
    for line in text.splitlines():
        for kind, detail in _claims_in_line(line):
            if not detail or (kind, detail) in seen:
                continue
            seen.add((kind, detail))
            claims.append(
                Claim(
                    kind=kind,
                    detail=detail,
                    session_id=session_id,
                    node_id=node_id,
                    excerpt=_excerpt(line),
                )
            )


def extract_claims(
    messages: Iterable[EvidenceMessage], session_id: str = ""
) -> list[Claim]:
    """All claims across ``messages``, deduplicated by ``(kind, detail)``.

    ``Claim.node_id`` cites the surface's real node id
    (``EvidenceMessage.source_id``, e.g. the devin-cli
    ``message_nodes.node_id``) when the provider supplies one; otherwise
    the message's positional index — same convention as
    :func:`claims_from_pairs`.
    """
    claims: list[Claim] = []
    seen: set[tuple[str, str]] = set()
    for position, message in enumerate(messages, 1):
        if message.role in _SKIP_ROLES:
            continue
        node_id = (
            message.source_id
            if message.source_id is not None
            else position
        )
        _collect(session_id, node_id, message.text, claims, seen)
    return claims


def claims_from_pairs(
    pairs: Iterable[tuple[str | None, str]],
    session_id: str,
) -> list[Claim]:
    """Claims over pre-decoded ``(role, text)`` pairs — the adapter input."""
    return extract_claims(
        (
            EvidenceMessage(
                role=normalize_role(role), text=text, ts_ms=None
            )
            for role, text in pairs
        ),
        session_id,
    )
