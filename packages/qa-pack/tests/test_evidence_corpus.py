"""Session-corpus evidence parity.

Replays the vendored ``tests/session-corpus`` store — copied verbatim
from devin-internals-spec ``fixtures/session-corpus`` — through the real
evidence providers, then extracts claims and audits them. Every
expectation comes from the corpus manifests: ``manifest.json`` binds the
session set per surface store, ``evidence-manifest.json`` the
per-session content layer (claims → verdicts, tool-call kinds, message
roles).

Verdict vocabulary (SESSION-EVIDENCE §8 vs verify.py): the manifest's
``verified`` maps 1:1 to the auditor's ``verified``. Its two
non-corroborated verdicts both land on the auditor's
``disputed``/``unverifiable`` pair — ``unverified`` means tool evidence
exists but does not corroborate, ``no-evidence`` means no tool call was
recorded — so the distinction is asserted on ``tool_calls`` presence,
never on the auditor's status name.

Manifest-vs-implementation drift is xfalled strictly (the vendored
manifest is verbatim — never edited to match) and reported upstream;
see ``_KNOWN_DRIFT``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from devin_qa_pack.evidence import EvidenceProvider, SessionEvidence
from devin_qa_pack.evidence.codex_cli import CodexCliProvider
from devin_qa_pack.evidence.copilot_cli import CopilotCliProvider
from devin_qa_pack.evidence.vscode_chat import VscodeChatProvider
from devin_qa_pack.report import audit_evidence
from devin_qa_pack.verify import (
    DISPUTED,
    UNVERIFIABLE,
    VERIFIED,
    VerifiedClaim,
    parse_tool_calls,
)

CORPUS = Path(__file__).resolve().parent / "session-corpus"
_MANIFEST = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
_EVIDENCE_MANIFEST = json.loads(
    (CORPUS / "evidence-manifest.json").read_text(encoding="utf-8")
)

_FIXTURE_SESSIONS = {
    (f["surface"], f["store_path"]): {s["native_id"] for s in f["sessions"]}
    for f in _MANIFEST["fixtures"]
}
_EXPECTED = {
    (s["surface"], s["native_id"]): s for s in _EVIDENCE_MANIFEST["sessions"]
}

# A HOME that cannot exist — env-discovered providers see it instead of
# the real one, so no real user store can leak into a corpus run. The
# corpus is synthetic; tests never touch real session data.
_NO_HOME = "/nonexistent-corpus-home"

# The auditor's two non-corroborated outcomes — what the manifest's
# ``unverified`` and ``no-evidence`` verdicts resolve to.
_NON_CORROBORATED = frozenset({DISPUTED, UNVERIFIABLE})

# Sessions whose evidence-manifest expectations the implementation does
# not meet today. The vendored manifest is verbatim — drift is reported
# to the corpus owners, not papered over here.
#
# - vscode-chat 40b09524 / d7ec266f: the manifest expects a ``user``
#   message role, but these sessions' ``requests`` carry no ``message``
#   text (metadata-only turns — d7ec266f's third request is the
#   truncated tail). Spec §6.5 maps only ``request.message`` text to
#   user messages, so the provider emits none.
_KNOWN_DRIFT = {
    ("vscode-chat", "40b09524-422d-534f-8e21-bdb5d16bd8ab"),
    ("vscode-chat", "d7ec266f-fbf0-5639-8e82-7ef445ba605d"),
}

_session_cache: dict[tuple[str, str], list[SessionEvidence]] = {}


def _wiring(
    surface: str, store_path: str
) -> tuple[EvidenceProvider, dict[str, str]]:
    """Provider + call env pointed at one vendored store root.

    Root overrides follow each provider's own convention: copilot-cli is
    env-discovered (``COPILOT_DIR``), codex-cli takes a ``root`` argument,
    vscode-chat takes explicit ``user_dirs``. The env map is complete —
    nothing from ``os.environ`` reaches the providers.
    """
    root = CORPUS / store_path
    if surface == "copilot-cli":
        return CopilotCliProvider(), {
            "COPILOT_DIR": str(root),
            "HOME": _NO_HOME,
        }
    if surface == "codex-cli":
        return CodexCliProvider(root=root), {}
    if surface == "vscode-chat":
        user_dirs = sorted(root.glob("*/User"))
        assert user_dirs, f"{store_path}: no */User dir in fixture"
        return VscodeChatProvider(user_dirs=user_dirs), {}
    raise AssertionError(f"no provider wiring for surface {surface!r}")


def _sessions(surface: str, store_path: str) -> list[SessionEvidence]:
    key = (surface, store_path)
    if key not in _session_cache:
        provider, env = _wiring(surface, store_path)
        assert provider.available(env), (
            f"{surface}: vendored store {store_path!r} not found"
        )
        _session_cache[key] = provider.sessions(env)
    return _session_cache[key]


def test_evidence_manifest_covers_the_manifest_session_set():
    """Every manifest.json session has exactly one evidence-manifest entry."""
    manifest_pairs = {
        (surface, native_id)
        for (surface, _), native_ids in _FIXTURE_SESSIONS.items()
        for native_id in native_ids
    }
    entries = _EVIDENCE_MANIFEST["sessions"]
    assert len(entries) == len(_EXPECTED)  # no duplicate (surface, id) keys
    assert set(_EXPECTED) == manifest_pairs


@pytest.mark.parametrize(
    "surface,store_path",
    [
        pytest.param(surface, store_path, id=f"{surface}:{store_path}")
        for surface, store_path in sorted(_FIXTURE_SESSIONS)
    ],
)
def test_fixture_yields_exactly_the_manifest_sessions(surface, store_path):
    """The provider surfaces every manifest id — no more (forked/duplicate
    rollouts stay merged), no less."""
    produced = _sessions(surface, store_path)
    assert {e.native_id for e in produced} == _FIXTURE_SESSIONS[
        (surface, store_path)
    ]
    assert all(e.surface == surface for e in produced)


def _assert_expected_claim(
    exp: dict, evidence: SessionEvidence, results: list[VerifiedClaim]
) -> None:
    kind, verdict = exp["kind"], exp["verdict"]
    sid = f"{evidence.surface}:{evidence.native_id}"
    matching = [r for r in results if r.claim.kind == kind]
    assert matching, f"{sid}: expected a {kind!r} claim — none extracted"
    statuses = {r.status for r in matching}
    if verdict == "verified":
        assert VERIFIED in statuses, (
            f"{sid}: expected a verified {kind} claim, got {sorted(statuses)}"
        )
        return
    assert statuses <= _NON_CORROBORATED, (
        f"{sid}: manifest verdict {verdict!r} but a {kind} claim resolved "
        f"{sorted(statuses)}"
    )
    if verdict == "no-evidence":
        assert not evidence.tool_calls, (
            f"{sid}: 'no-evidence' verdict but tool calls were recorded"
        )
    else:  # "unverified" — evidence exists but does not corroborate
        assert evidence.tool_calls, (
            f"{sid}: 'unverified' verdict but no tool calls recorded"
        )


def _session_params() -> list:
    params = []
    for fixture in _MANIFEST["fixtures"]:
        surface, store_path = fixture["surface"], fixture["store_path"]
        for session in fixture["sessions"]:
            native_id = session["native_id"]
            marks = []
            if (surface, native_id) in _KNOWN_DRIFT:
                marks.append(
                    pytest.mark.xfail(
                        reason=(
                            "evidence-manifest drift: expects 'user' role "
                            "for requests with no message text; provider "
                            "emits none (spec §6.5 maps only "
                            "request.message text)"
                        ),
                        strict=True,
                    )
                )
            params.append(
                pytest.param(
                    surface,
                    store_path,
                    native_id,
                    marks=marks,
                    id=f"{surface}:{native_id[:8]}",
                )
            )
    return params


@pytest.mark.parametrize("surface,store_path,native_id", _session_params())
def test_session_evidence_matches_the_manifest(
    surface: str, store_path: str, native_id: str
):
    expected = _EXPECTED[(surface, native_id)]
    evidence = next(
        (
            e
            for e in _sessions(surface, store_path)
            if e.native_id == native_id
        ),
        None,
    )
    assert evidence is not None, f"{surface}:{native_id}: no SessionEvidence"
    assert evidence.surface == surface
    # Canonical-ref sanity: non-empty and inside the vendored store —
    # never a real user path.
    assert evidence.ref
    assert Path(evidence.ref).resolve().is_relative_to(CORPUS)

    # expected_tool_call_kinds are the spec §4 semantic kinds — the
    # verifier's parsed kind (execute|write|other|unknown), not the
    # provider-sniffed raw kind on EvidenceToolCall.
    parsed = parse_tool_calls(evidence.tool_calls)
    assert {call.kind for call in parsed} >= set(
        expected["expected_tool_call_kinds"]
    )

    if not (
        expected["expected_claims"]
        or expected["expected_tool_call_kinds"]
        or expected["expected_message_roles"]
    ):
        # The fixture carries no content layer: presence-or-absence
        # consistency only — assert absence, never fabricated rows.
        assert evidence.messages == []
        assert evidence.tool_calls == []
    else:
        assert {m.role for m in evidence.messages} == set(
            expected["expected_message_roles"]
        )

    # claims → audit runs on every session (an empty expectation is a
    # no-crash check); verdicts are asserted where the manifest names them.
    audit = audit_evidence(evidence)
    for exp in expected["expected_claims"]:
        _assert_expected_claim(exp, evidence, audit.results)
