"""Structural check: private formatting helpers must live in _fmt.py."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HELPERS = {"_money", "_pct", "_row"}


def _functions(path: Path) -> tuple[set, ast.Module]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    return defs, tree


def main() -> int:
    errors = []
    report = ROOT / "calcreport.py"
    fmt = ROOT / "_fmt.py"
    for path in (report, fmt):
        if not path.exists():
            errors.append(f"{path.name} missing")
    if errors:
        for e in errors:
            print(e)
        return 1
    report_defs, report_tree = _functions(report)
    fmt_defs, _ = _functions(fmt)
    private = {n for n in report_defs if n.startswith("_")}
    if private:
        errors.append(
            f"calcreport.py still defines private helpers: {sorted(private)}")
    if "render_report" not in report_defs:
        errors.append("render_report missing from calcreport.py")
    imports_fmt = any(
        isinstance(n, ast.ImportFrom) and n.module == "_fmt"
        for n in report_tree.body)
    if not imports_fmt:
        errors.append("calcreport.py does not import from _fmt")
    missing = HELPERS - fmt_defs
    if missing:
        errors.append(f"_fmt.py missing helpers: {sorted(missing)}")
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
