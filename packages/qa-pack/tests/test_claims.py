"""claims.py — extraction of deliverable claims from evidence messages."""

import json

from devin_internals.parsers import SessionsStore
from devin_qa_pack.claims import (
    COMMIT,
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
