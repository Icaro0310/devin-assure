"""Structural check: line-total math lives in one _line_total helper."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PUBLIC = ("subtotal", "total_with_discount", "total_with_tax")


def _calls(node: ast.AST) -> set:
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _subscript_mults(tree: ast.AST) -> list:
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Mult)
        and isinstance(n.left, ast.Subscript)
        and isinstance(n.right, ast.Subscript)
    ]


def main() -> int:
    path = ROOT / "cart.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    errors = []
    if "_line_total" not in funcs:
        errors.append("_line_total helper missing")
    for name in PUBLIC:
        node = funcs.get(name)
        if node is None:
            errors.append(f"{name} missing")
        elif "_line_total" not in _calls(node):
            errors.append(f"{name} does not call _line_total")
    sites = _subscript_mults(tree)
    if len(sites) > 1:
        errors.append(
            f"price*qty math still duplicated ({len(sites)} sites)")
    for e in errors:
        print(e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
