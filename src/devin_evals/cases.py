"""Eval case format: ``evals/<name>.json``.

JSON rather than YAML — a deliberate choice (KICKOFF-M1 env notes): the
harness stays stdlib-only at runtime, and eval files are generated/diffed
often enough that a strict, ubiquitous parser beats YAML's ergonomics.

Schema::

    {
      "id": "string, optional — defaults to the file stem",
      "description": "string",
      "session_ref": "session id OR title in sessions.db (either this or
                      prompt_context should be present)",
      "prompt_context": "inline context for live-mode cases (M2); cases with
                         no session_ref are reported as SKIP in offline replay",
      "packs": ["bugfix"],   // optional: built-in rubric packs whose checks
                             // run BEFORE this case's own rubric
      "rubric": [
        {"grader": "<name>", ...grader params...}
      ]
    }

Validation is eager: malformed files, unknown graders, and missing required
grader params all raise :class:`CaseError` naming the offending file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devin_evals.graders import GRADERS


class CaseError(ValueError):
    """A ``*.json`` eval file is malformed or references an unknown grader."""


@dataclass(frozen=True)
class Check:
    grader: str
    params: dict[str, Any]


@dataclass(frozen=True)
class EvalCase:
    id: str
    description: str
    session_ref: str | None
    prompt_context: str | None
    checks: tuple[Check, ...]
    source: str  # file name the case came from


def _err(path: Path, msg: str) -> CaseError:
    return CaseError(f"{path.name}: {msg}")


PACKS_RESOURCE = "packs"


def builtin_packs() -> dict[str, Path]:
    """Built-in rubric packs shipped as package data, by pack id."""
    from importlib.resources import files

    out = {}
    for entry in files("devin_evals").joinpath(PACKS_RESOURCE).iterdir():
        if entry.name.endswith(".json"):
            out[entry.name[:-5]] = Path(str(entry))
    return out


def _check_list(raw_rubric, path: Path, where: str) -> list[Check]:
    checks: list[Check] = []
    for i, raw in enumerate(raw_rubric):
        at = f"{where}[{i}]"
        if not isinstance(raw, dict):
            raise _err(path, f"{at} must be an object")
        grader = raw.get("grader")
        if not isinstance(grader, str):
            raise _err(path, f'{at}: "grader" must be a string')
        spec = GRADERS.get(grader)
        if spec is None:
            raise _err(
                path,
                f"{at}: unknown grader {grader!r} "
                f"(known: {', '.join(sorted(GRADERS))})",
            )
        params = {k: v for k, v in raw.items() if k != "grader"}
        for key in spec.required:
            if key not in params:
                raise _err(path, f'{at}: grader {grader!r} requires "{key}"')
        checks.append(Check(grader=grader, params=params))
    return checks


def _pack_checks(pack_id: str, path: Path,
                 packs_dir: Path | None) -> list[Check]:
    """Resolve a pack name to its rubric checks.

    A user dir (``--packs-dir``) wins over the built-ins, so a team can
    shadow ``bugfix`` with its own definition.
    """
    candidates = []
    if packs_dir is not None:
        candidates.append(packs_dir / f"{pack_id}.json")
    builtin = builtin_packs().get(pack_id)
    if builtin is not None:
        candidates.append(builtin)
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise _err(path, f"pack {pack_id!r}: invalid JSON: {exc}")
            rubric = data.get("rubric")
            if not isinstance(rubric, list):
                raise _err(path, f'pack {pack_id!r}: "rubric" must be a list')
            return _check_list(rubric, path, f"pack {pack_id!r} rubric")
    raise _err(path, f"unknown rubric pack {pack_id!r}")


def _load_one(path: Path, packs_dir: Path | None = None) -> EvalCase:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _err(path, f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _err(path, "top level must be a JSON object")

    rubric = data.get("rubric")
    if rubric is None:
        rubric = []
    if not isinstance(rubric, list):
        raise _err(path, '"rubric" must be a list of checks')

    packs = data.get("packs", [])
    if not isinstance(packs, list) or not all(
        isinstance(p, str) for p in packs
    ):
        raise _err(path, '"packs" must be a list of pack names')

    checks: list[Check] = []
    for pack_id in packs:
        checks += _pack_checks(pack_id, path, packs_dir)
    checks += _check_list(rubric, path, "rubric")
    if not checks:
        raise _err(
            path, 'case has no checks — provide "rubric" and/or "packs"')

    case_id = data.get("id") or path.stem
    if not isinstance(case_id, str):
        raise _err(path, '"id" must be a string')
    session_ref = data.get("session_ref")
    prompt_context = data.get("prompt_context")
    for field_name, value in (
        ("session_ref", session_ref),
        ("prompt_context", prompt_context),
        ("description", data.get("description", "")),
    ):
        if value is not None and not isinstance(value, str):
            raise _err(path, f'"{field_name}" must be a string')

    return EvalCase(
        id=case_id,
        description=data.get("description") or "",
        session_ref=session_ref,
        prompt_context=prompt_context,
        checks=tuple(checks),
        source=path.name,
    )


def load_cases(
    evals_dir: str | Path, packs_dir: str | Path | None = None
) -> list[EvalCase]:
    """Load every ``*.json`` case in ``evals_dir``, sorted by file name.

    ``packs_dir`` overrides/extends the built-in rubric packs when a case
    names one in ``"packs"``.
    """
    d = Path(evals_dir)
    if not d.is_dir():
        raise CaseError(f"{d}: no such evals directory")
    pd = Path(packs_dir) if packs_dir is not None else None
    return [_load_one(p, pd) for p in sorted(d.glob("*.json"))]
