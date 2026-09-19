#!/usr/bin/env python3
"""Validate the translation catalogs.

Checks that every catalog key still exists as a source string, so a reworded
message in the code shows up as a stale key instead of silently falling back to
English forever.
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILED = False

for app in ("oat_helper",):
    source_text = (ROOT / app / f"{app}.py").read_text(encoding="utf-8")
    literals = {node.value for node in ast.walk(ast.parse(source_text))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    for catalog_path in sorted((ROOT / app).glob(f"{app}.*.json")):
        lang = catalog_path.name[len(app) + 1:-5]
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        stale = [key for key in catalog if key not in literals and key not in source_text]
        empty = [key for key, value in catalog.items() if not str(value).strip()]
        print(f"{catalog_path.name}: {len(catalog)} entries, "
              f"{len(stale)} stale, {len(empty)} empty")
        for key in stale[:10]:
            print(f"   stale key ({lang}): {key!r}")
        for key in empty[:10]:
            print(f"   empty value ({lang}): {key!r}")
        if stale or empty:
            FAILED = True

sys.exit(1 if FAILED else 0)
