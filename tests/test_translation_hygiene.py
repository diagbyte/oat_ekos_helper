#!/usr/bin/env python3
"""Static checks for the two translation mistakes the UI cannot show you.

1. `_` is the translation function. Unpacking into it - `fn, _ = getOpenFileName()`
   - makes it a local str for the whole scope, so a later `_("...")` raises
   `TypeError: 'str' object is not callable`. That only fires on an error path,
   so the import-configuration failure dialog crashed instead of reporting the
   real problem, and nothing in an Ekos-launched extension shows the traceback.

2. A QMessageBox body written as a bare literal stays English in every other
   language. test_i18n_coverage.py walks the widget tree, so it can only see
   text that exists at start-up - never a dialog that has not been opened.

Needs no PyQt5 and no INDI server: pure AST, safe to run anywhere.
"""
import ast
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "oat_helper" / "oat_helper.py"
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
problems = []


def own_scope(node):
    """Direct children of a function, not descending into nested functions."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from own_scope(child)


# --- 1. `_` shadowed in a scope that also translates -------------------------
for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
    stores, calls = [], []
    for node in own_scope(func):
        if isinstance(node, ast.Name) and node.id == "_" and isinstance(node.ctx, ast.Store):
            stores.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
            calls.append(node.lineno)
    if stores and calls:
        problems.append(f"{func.name}() assigns `_` at line(s) {stores} and calls _() at {calls}: "
                        f"the translation function is shadowed - rename the throwaway variable")


# --- 2. dialog bodies that never reach the catalog ---------------------------
DIALOGS = {"question", "warning", "information", "critical"}


def literal_text(node):
    """Text of a literal / implicit concatenation / f-string / .format() chain."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(p.value for p in node.values if isinstance(p, ast.Constant))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal_text(node.left), literal_text(node.right)
        if left is not None or right is not None:
            return (left or "") + (right or "")
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"):
        return literal_text(node.func.value)
    return None


def translated(node):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
        return True
    # _("a").format(...) and _("a") + x + _("b") both count as translated
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return translated(node.func.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return translated(node.left) or translated(node.right)
    return False


for node in ast.walk(tree):
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in DIALOGS):
        continue
    base = node.func.value
    if getattr(base, "attr", getattr(base, "id", "")) != "QMessageBox":
        continue
    for index, arg in enumerate(node.args):
        if translated(arg):
            continue
        text = literal_text(arg)
        if text and len(text.strip()) > 3 and any(ch.isalpha() for ch in text):
            problems.append(f"line {node.lineno}: QMessageBox.{node.func.attr} argument {index} "
                            f"is an untranslated literal: {text.strip()[:60]!r}")

# --- 3. status text written after the window is built ------------------------
# translate_widget_tree() translates a widget's CURRENT text, so it only reaches
# text that exists when it runs. Anything a later setText writes is invisible to
# it and to test_i18n_coverage.py, which walks the tree at start-up. A bare
# literal here stays English in every other language - on a Korean desktop the
# AutoPA status field was the one English field on its tab.
BUILDERS = {m.name for cls in tree.body if isinstance(cls, ast.ClassDef)
            for m in cls.body if isinstance(m, ast.FunctionDef)
            and (m.name.startswith("make_") or m.name in ("build_ui", "_make_dms_row"))}

for cls in tree.body:
    if not isinstance(cls, ast.ClassDef):
        continue
    for method in cls.body:
        if not isinstance(method, ast.FunctionDef) or method.name in BUILDERS:
            continue
        for node in ast.walk(method):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setText" and node.args):
                continue
            arg = node.args[0]
            # A whole bare sentence. f-strings and .format() calls carry data and
            # are checked by eye; an unwrapped plain literal never can be right.
            for branch in (arg.body, arg.orelse) if isinstance(arg, ast.IfExp) else (arg,):
                if not (isinstance(branch, ast.Constant) and isinstance(branch.value, str)):
                    continue
                value = branch.value
                if sum(ch.isalpha() for ch in value) > 3 and not value.startswith("<"):
                    problems.append(
                        f"line {node.lineno}: {method.name}() writes untranslated status text "
                        f"{value[:50]!r} - wrap it in _()")

print(f"translation hygiene problems: {len(problems)}")
for problem in problems:
    print("  ", problem)
sys.exit(1 if problems else 0)
