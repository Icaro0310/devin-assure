"""claims.py — extraction of deliverable claims from evidence messages."""

import json

from devin_internals.parsers import SessionsStore
from devin_qa_pack.claims import (
    COMMIT,
    COVERAGE,
    DELEGATION,
    EVAL,
    FILE,
    HTTP,
    PUSH,
    TESTS,
    TESTS_PASSED,
    extract_claims,
)
from devin_qa_pack.evidence import EvidenceMessage, normalize_role
from devin_qa_pack.evidence.devin_cli import _decode_message, session_evidence


def make_node(text, role="agent", node_id=1, sid="s", created_at=0):
    """An EvidenceMessage the way the devin-cli provider decodes it —
    the chat_message JSON layer is exercised here via ``_decode_message``
    so the raw-payload paths keep coverage."""
    del node_id, sid, created_at
    chat_message = (
        json.dumps({"role": role, "text": text})
        if isinstance(text, str)
        else text  # raw payload, for the non-JSON path
    )
    decoded_role, decoded_text = _decode_message(chat_message)
    return EvidenceMessage(
        role=normalize_role(decoded_role), text=decoded_text, ts_ms=None
    )


def test_extracts_all_claim_kinds(sessions_db):
    with SessionsStore(sessions_db) as store:
        session = next(
            s for s in store.sessions() if s.id == "sess-verified"
        )
        evidence = session_evidence(store, session)
    claims = extract_claims(evidence.messages, "sess-verified")
    by_kind = {c.kind: c for c in claims}
    assert by_kind[TESTS].detail == "pytest"
    assert by_kind[FILE].detail == "src/report.html"
    assert by_kind[COMMIT].detail == "a1b2c3d"
    assert PUSH in by_kind
    assert all(c.session_id == "sess-verified" for c in claims)
    assert all(c.node_id == 2 for c in claims)
    assert all(c.excerpt for c in claims)


def test_user_role_messages_are_not_scanned():
    nodes = [make_node("please make sure the tests pass", role="user")]
    assert extract_claims(nodes) == []


def test_generic_tests_claim_has_generic_detail():
    claims = extract_claims([make_node("All tests passed.")])
    assert [(c.kind, c.detail) for c in claims] == [(TESTS, "tests")]


def test_runner_named_in_claim_becomes_detail():
    claims = extract_claims([make_node("pytest is green now, 42 passed")])
    assert [(c.kind, c.detail) for c in claims] == [
        (TESTS, "pytest"),
        (TESTS_PASSED, "42"),
    ]


def test_claims_are_deduplicated_per_kind_and_detail():
    nodes = [
        make_node("tests passed", node_id=1),
        make_node("yes — tests passed again", node_id=2),
    ]
    assert len(extract_claims(nodes)) == 1


def test_non_json_chat_message_is_treated_as_raw_text():
    claims = extract_claims([make_node("{not json — tests passed")])
    assert [c.kind for c in claims] == [TESTS]


def test_garbage_payload_yields_no_claims():
    assert extract_claims([make_node("\x00\xff\xfe random bytes")]) == []


def test_commit_claim_requires_commit_context():
    # a bare hash with no "commit" wording is not a commit claim
    assert extract_claims([make_node("checksum a1b2c3d4e5")]) == []
    claims = extract_claims([make_node("work committed as a1b2c3d4e5")])
    assert [(c.kind, c.detail) for c in claims] == [(COMMIT, "a1b2c3d4e5")]


def test_file_claim_requires_a_write_verb():
    # paths mentioned without a write verb are references, not claims
    assert extract_claims([make_node("see src/report.html for details")]) == []
    claims = extract_claims([make_node("updated docs/usage.md")])
    assert [(c.kind, c.detail) for c in claims] == [(FILE, "docs/usage.md")]


# -- tests_passed claims -------------------------------------------------------


def test_counted_tests_claim_carries_the_count(sessions_db):
    with SessionsStore(sessions_db) as store:
        session = next(
            s for s in store.sessions() if s.id == "sess-counted"
        )
        evidence = session_evidence(store, session)
    claims = extract_claims(evidence.messages, "sess-counted")
    assert [(c.kind, c.detail) for c in claims] == [
        (TESTS, "pytest"),
        (TESTS_PASSED, "12"),
    ]


def test_passed_count_claim_carries_the_count():
    claims = extract_claims([make_node("All 47 tests passed.")])
    assert [(c.kind, c.detail) for c in claims] == [
        (TESTS, "tests"),
        (TESTS_PASSED, "47"),
    ]


def test_passed_count_claim_ignores_other_tallies():
    # only the passed count is claimed — skipped/failed tallies are not
    claims = extract_claims(
        [make_node("run done: 108 passed, 1 skipped, 2 failed.")]
    )
    counts = [
        (c.kind, c.detail) for c in claims if c.kind == TESTS_PASSED
    ]
    assert counts == [(TESTS_PASSED, "108")]


def test_bare_tests_claim_yields_no_count_claim():
    # "tests pass" without a number stays a generic (uncounted) claim
    claims = extract_claims([make_node("All tests passed.")])
    assert [c.kind for c in claims] == [TESTS]


def test_grouped_total_claim_normalizes_to_plain_count():
    # "1,234" is one claim of 1234 — the tail "234" is not a claim
    claims = extract_claims([make_node("1,234 tests passed.")])
    assert [(c.kind, c.detail) for c in claims] == [
        (TESTS, "tests"),
        (TESTS_PASSED, "1234"),
    ]


# -- http claims ---------------------------------------------------------------


def test_http_claim_returned_status():
    claims = extract_claims(
        [make_node("The API returned 200 for the health endpoint.")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(HTTP, "200")]


def test_http_claim_responded_with_status():
    claims = extract_claims(
        [make_node("the endpoint responded with a 404")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(HTTP, "404")]


def test_http_claim_bare_http_and_status_phrasing():
    assert [(c.kind, c.detail) for c in extract_claims(
        [make_node("checked it: HTTP 500")])] == [(HTTP, "500")]
    assert [(c.kind, c.detail) for c in extract_claims(
        [make_node("status code: 201")])] == [(HTTP, "201")]


def test_http_claim_deduplicates_same_code():
    claims = extract_claims(
        [make_node("the API returned 200 — HTTP 200, status was 200.")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(HTTP, "200")]


def test_http_claim_ignores_non_status_numbers():
    # "returned 42" is not a 3-digit status; no claim is extracted
    assert extract_claims(
        [make_node("the function returned 42 rows")]) == []


# -- delegation claims ---------------------------------------------------------


def test_delegation_claim_english_phrasings():
    for text in (
        "I delegated the repo scan to a subagent.",
        "Spawned a subagent to run the search.",
        "the lookup ran in parallel via subagent",
        "I launched 3 workers in parallel.",
        "handed off the docs pass to a worker",
    ):
        claims = extract_claims([make_node(text)])
        assert [(c.kind, c.detail) for c in claims] == [
            (DELEGATION, DELEGATION)
        ], text


def test_delegation_claim_portuguese_phrasings():
    for text in (
        "Deleguei a análise para um subagente.",
        "O subagente retornou os resultados.",
        "Delegado ao worker o relatório final.",
    ):
        claims = extract_claims([make_node(text)])
        assert [(c.kind, c.detail) for c in claims] == [
            (DELEGATION, DELEGATION)
        ], text


def test_delegation_claim_reporting_delegatee_alone():
    # a delegatee + a result verb is a delegation report too
    claims = extract_claims(
        [make_node("the subagent completed the exploration")]
    )
    assert [(c.kind, c.detail) for c in claims] == [
        (DELEGATION, DELEGATION)
    ]


def test_delegation_claim_needs_delegatee_or_pt_marker():
    # parallelization talk and self-references are not delegation claims
    assert extract_claims(
        [make_node("we could parallelize this with more processes")]
    ) == []
    assert extract_claims(
        [make_node("the agent handled it end to end")]
    ) == []


def test_delegation_claim_denials_do_not_claim():
    # "I did not delegate" would otherwise match and then audit disputed
    for text in (
        "I did not delegate to a worker.",
        "I never spawned a subagent for this.",
        "Não deleguei nada.",
    ):
        assert extract_claims([make_node(text)]) == [], text


def test_delegation_claim_ignores_plain_worker_reports():
    # "the worker process completed" is ordinary activity, not a
    # delegation — the worker+result-verb order is reserved for
    # subagents; worker claims need a delegation verb.
    assert extract_claims(
        [make_node("The worker process completed the build.")]
    ) == []
    assert extract_claims(
        [make_node("Workers finished the nightly job.")]
    ) == []
    # a delegation verb on a worker is still a claim
    claims = extract_claims(
        [make_node("the worker dispatched the retry job")]
    )
    assert [(c.kind, c.detail) for c in claims] == [
        (DELEGATION, DELEGATION)
    ]


def test_delegation_claims_deduplicate():
    nodes = [
        make_node("I delegated the scan to a subagent.", node_id=1),
        make_node("the subagent reported back clean.", node_id=2),
    ]
    assert len(extract_claims(nodes)) == 1


# -- eval claims ---------------------------------------------------------------


def test_eval_claim_score_phrasings():
    for text, detail in (
        ("the eval score was 0.92", "0.92"),
        ("eval score: 85%", "85"),
        ("evals: 18/20", "18/20"),
        ("the benchmark result was 0.87", "0.87"),
        ("scored 0.9 on the eval", "0.9"),
        ("eval accuracy reached 91%", "91"),
        ("a avaliação teve nota 0.9", "0.9"),
    ):
        claims = extract_claims([make_node(text)])
        assert [(c.kind, c.detail) for c in claims] == [
            (EVAL, detail)
        ], text


def test_eval_claim_deduplicates_same_score():
    claims = extract_claims(
        [make_node("eval score was 0.92 — scored 0.92 on the eval.")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(EVAL, "0.92")]


def test_eval_claim_requires_a_number():
    # "the eval passed" is a test-flavored assertion, not a score claim
    for text in (
        "the evals passed",
        "ran the eval suite",
        "eval results look good",
    ):
        assert extract_claims([make_node(text)]) == [], text


def test_eval_claim_ignores_user_messages():
    nodes = [make_node("did the eval score reach 0.9?", role="user")]
    assert extract_claims(nodes) == []


# -- coverage claims -----------------------------------------------------------


def test_coverage_claim_percent_phrasings():
    for text, detail in (
        ("coverage is at 87%", "87"),
        ("coverage: 85.2%", "85.2"),
        ("test coverage reached 90%", "90"),
        ("achieved 87.5% coverage", "87.5"),
        ("90% of code coverage", "90"),
        ("cobertura de 80%", "80"),
        ("atingimos 85% de cobertura", "85"),
    ):
        claims = extract_claims([make_node(text)])
        assert [(c.kind, c.detail) for c in claims] == [
            (COVERAGE, detail)
        ], text


def test_coverage_claim_requires_the_keyword():
    # a bare percent is not a coverage claim
    assert extract_claims([make_node("reduced runtime by 85%")]) == []
    # coverage without a percent carries nothing checkable
    assert extract_claims(
        [make_node("coverage looks good now")]) == []


def test_coverage_claims_deduplicate():
    nodes = [
        make_node("coverage is at 87%", node_id=1),
        make_node("yes, coverage at 87% confirmed", node_id=2),
    ]
    assert len(extract_claims(nodes)) == 1


# -- negated claims (adversarial) ------------------------------------------------


def test_negated_sentences_yield_no_claims():
    """A negated assertion must not become a claim — "did not X" is a
    denial, and a phantom claim would audit as disputed against work
    the agent never asserted."""
    for text in (
        "tests did not pass",
        "the tests were not green",
        "I did not commit a1b2c3d4e5",
        "didn't push to origin",
        "have not created src/report.html",
        "the API never responded with 404",
        "did not score 0.9 on the eval",
        "coverage is not 87%",
        "não commit a1b2c3d4e5",
        "nunca dei push para origin",
    ):
        assert extract_claims([make_node(text)]) == [], text


def test_negation_is_clause_scoped_not_global():
    """A negator voids only its own clause — a denied test claim in one
    clause must not suppress a real commit claim in the next."""
    claims = extract_claims(
        [make_node("did not run the tests, but committed a1b2c3d4e5")]
    )
    assert [(c.kind, c.detail) for c in claims] == [
        (COMMIT, "a1b2c3d4e5")
    ]


def test_denied_details_do_not_ride_an_affirmed_cue():
    """One affirmative cue must not launder denied details on the same
    line — each hash/path is claimed only inside its own affirmed
    clause."""
    claims = extract_claims(
        [make_node("I did not commit a1b2c3d; I committed b1b2c3d")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(COMMIT, "b1b2c3d")]
    claims = extract_claims(
        [make_node("did not create src/old.py; I updated src/new.py")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(FILE, "src/new.py")]


def test_denial_then_affirmation_still_claims():
    """A denied cue in an earlier clause must not swallow a later
    affirmation — the cue regex stops at the clause boundary."""
    claims = extract_claims(
        [make_node("I did not run pytest, but tests passed")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(TESTS, TESTS)]
    claims = extract_claims(
        [make_node("I did not push to origin; I pushed to origin")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(PUSH, PUSH)]


def test_not_only_is_affirmation_not_denial():
    """"not only"/"não só" assert, they do not negate."""
    claims = extract_claims([make_node("not only did tests pass")])
    assert [(c.kind, c.detail) for c in claims] == [(TESTS, TESTS)]
    claims = extract_claims(
        [make_node("not only did coverage reach 87%")]
    )
    assert [(c.kind, c.detail) for c in claims] == [(COVERAGE, "87")]
