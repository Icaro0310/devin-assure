"""Eval case loading: JSON format, validation, ordering."""

from __future__ import annotations

import pytest
from conftest import write_eval
from devin_evals.cases import CaseError, load_cases

VALID = {
    "id": "case-a",
    "description": "checks the transcript",
    "session_ref": "Eval fixture session",
    "rubric": [
        {"grader": "contains", "text": "all tests pass"},
        {"grader": "tool_called", "name": "run_shell", "args_substr": "pytest"},
    ],
}


def test_loads_valid_case(evals_dir):
    write_eval(evals_dir, "a", VALID)
    (case,) = load_cases(evals_dir)
    assert case.id == "case-a"
    assert case.description == "checks the transcript"
    assert case.session_ref == "Eval fixture session"
    assert case.prompt_context is None
    assert [c.grader for c in case.checks] == ["contains", "tool_called"]
    assert case.checks[0].params == {"text": "all tests pass"}
    assert case.checks[1].params == {"name": "run_shell", "args_substr": "pytest"}


def test_id_defaults_to_file_stem(evals_dir):
    write_eval(evals_dir, "my-case", {**VALID, "id": None})
    del_id = {"description": "d", "session_ref": "s", "rubric": VALID["rubric"]}
    write_eval(evals_dir, "stem-name", del_id)
    ids = [c.id for c in load_cases(evals_dir)]
    assert "stem-name" in ids


def test_cases_sorted_by_filename(evals_dir):
    write_eval(evals_dir, "zzz", {**VALID, "id": "z"})
    write_eval(evals_dir, "aaa", {**VALID, "id": "a"})
    assert [c.id for c in load_cases(evals_dir)] == ["a", "z"]


def test_empty_dir_returns_empty_list(evals_dir):
    assert load_cases(evals_dir) == []


def test_missing_dir_raises(evals_dir):
    with pytest.raises(CaseError, match="no such"):
        load_cases(evals_dir / "nope")


def test_invalid_json_raises(evals_dir):
    (evals_dir / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CaseError, match="bad.json"):
        load_cases(evals_dir)


def test_non_object_raises(evals_dir):
    (evals_dir / "list.json").write_text("[1,2,3]", encoding="utf-8")
    with pytest.raises(CaseError, match="list.json"):
        load_cases(evals_dir)


def test_missing_rubric_raises(evals_dir):
    write_eval(evals_dir, "norubric", {"id": "x", "session_ref": "s"})
    with pytest.raises(CaseError, match="rubric"):
        load_cases(evals_dir)


def test_empty_rubric_raises(evals_dir):
    write_eval(evals_dir, "empty", {**VALID, "rubric": []})
    with pytest.raises(CaseError, match="rubric"):
        load_cases(evals_dir)


def test_unknown_grader_raises(evals_dir):
    write_eval(
        evals_dir, "bad", {**VALID, "rubric": [{"grader": "mind_reader"}]}
    )
    with pytest.raises(CaseError, match="mind_reader"):
        load_cases(evals_dir)


def test_missing_required_param_raises(evals_dir):
    write_eval(evals_dir, "bad", {**VALID, "rubric": [{"grader": "contains"}]})
    with pytest.raises(CaseError, match="text"):
        load_cases(evals_dir)


def test_prompt_context_optional(evals_dir):
    write_eval(
        evals_dir, "ctx", {**VALID, "session_ref": None, "prompt_context": "hi"}
    )
    (case,) = load_cases(evals_dir)
    assert case.session_ref is None
    assert case.prompt_context == "hi"


def test_non_json_files_ignored(evals_dir):
    write_eval(evals_dir, "a", VALID)
    (evals_dir / "notes.md").write_text("# not an eval", encoding="utf-8")
    assert len(load_cases(evals_dir)) == 1


# -- EV-4: rubric packs -----------------------------------------------------


def test_builtin_pack_expands(tmp_path):
    (tmp_path / "c.json").write_text(
        '{"id": "x", "packs": ["bugfix"],'
        ' "rubric": [{"grader": "contains", "text": "ok"}]}')
    cases = load_cases(tmp_path)
    assert len(cases[0].checks) == 4  # 3 pack checks + 1 local
    assert cases[0].checks[0].grader == "exit_code"  # pack first
    assert cases[0].checks[-1].grader == "contains"


def test_unknown_pack_raises(tmp_path):
    import pytest
    (tmp_path / "c.json").write_text('{"packs": ["nope"]}')
    with pytest.raises(CaseError, match="nope"):
        load_cases(tmp_path)


def test_packs_dir_shadows_builtin(tmp_path):
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "bugfix.json").write_text(
        '{"rubric": [{"grader": "no_secrets"}]}')
    (tmp_path / "c.json").write_text('{"packs": ["bugfix"]}')
    cases = load_cases(tmp_path, packs_dir=packs)
    assert len(cases[0].checks) == 1
    assert cases[0].checks[0].grader == "no_secrets"


def test_packs_must_be_string_list(tmp_path):
    import pytest
    (tmp_path / "c.json").write_text('{"packs": "bugfix"}')
    with pytest.raises(CaseError):
        load_cases(tmp_path)


def test_case_needs_some_check(tmp_path):
    import pytest
    (tmp_path / "c.json").write_text('{"id": "empty"}')
    with pytest.raises(CaseError, match="no checks"):
        load_cases(tmp_path)
