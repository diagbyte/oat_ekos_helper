#!/usr/bin/env python3
"""Validate the translation catalogs.

Checks that every catalog key still exists as a source string, so a reworded
message in the code shows up as a stale key instead of silently falling back to
English forever.

It also reports how many _()-wrapped strings have no entry yet. That is a count,
not a failure: a missing key falls back to English on purpose, so a partial
translation is a supported state (see CONTRIBUTING.md). The number is printed so
the gap is discoverable instead of having to be hunted for.
"""
import ast
import json
import sys
from pathlib import Path

# Keys contain °, ′ and ✓, which a non-UTF-8 console (cp949 on a Korean Windows
# box) cannot encode: without this the checker dies reporting its first finding.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
FAILED = False

for app in ("oat_helper",):
    source_text = (ROOT / app / f"{app}.py").read_text(encoding="utf-8")
    tree = ast.parse(source_text)
    literals = {node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    # Everything the code actually asks to be translated.
    wrapped = {node.args[0].value for node in ast.walk(tree)
               if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
               and node.func.id == "_" and node.args
               and isinstance(node.args[0], ast.Constant)
               and isinstance(node.args[0].value, str)
               and any(ch.isalpha() for ch in node.args[0].value)}
    for catalog_path in sorted((ROOT / app).glob(f"{app}.*.json")):
        lang = catalog_path.name[len(app) + 1:-5]
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        stale = [key for key in catalog if key not in literals and key not in source_text]
        empty = [key for key, value in catalog.items() if not str(value).strip()]
        untranslated = sorted(key for key in wrapped if key not in catalog)
        print(f"{catalog_path.name}: {len(catalog)} entries, "
              f"{len(stale)} stale, {len(empty)} empty, "
              f"{len(untranslated)} not translated yet (falls back to English)")
        for key in untranslated[:10]:
            print(f"   untranslated ({lang}): {key!r}")
        for key in stale[:10]:
            print(f"   stale key ({lang}): {key!r}")
        for key in empty[:10]:
            print(f"   empty value ({lang}): {key!r}")
        if stale or empty:
            FAILED = True

sys.exit(1 if FAILED else 0)
