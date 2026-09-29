#!/usr/bin/env python3
"""Run every test in this directory and report one summary.

Each test is a standalone script that exits non-zero on failure, so there is
nothing to install and no test framework to learn:

    python3 tests/run_all.py            # everything
    python3 tests/run_all.py autopa     # only tests whose name contains "autopa"

Tests are run one at a time on purpose: each starts its own fake INDI server and
drives a shared global Qt application, so running them in parallel would have
them fight over both.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HOME_RE = re.compile(r"""os\.environ\[\s*["']HOME["']\s*\]\s*=\s*["']([^"']+)["']""")


def environment_for(test):
    """The env a test needs: offscreen Qt, UTF-8, and its own HOME."""
    env = dict(os.environ)
    env.setdefault("LANG", "en_US.UTF-8")
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONIOENCODING"] = "utf-8"
    match = HOME_RE.search(test.read_text(encoding="utf-8"))
    if match and os.name == "nt":
        # The tests set HOME, but Path.home() on Windows reads USERPROFILE, so
        # the app and the fixtures would look in two different places.
        home = "C:" + match.group(1).replace("/", "\\")
        env["HOME"] = env["USERPROFILE"] = home
    return env


def main():
    patterns = sys.argv[1:]
    tests = sorted(HERE.glob("test_*.py")) + [HERE / "check_catalogs.py"]
    if patterns:
        tests = [t for t in tests if any(p in t.name for p in patterns)]
    if not tests:
        print("no tests matched", patterns)
        return 1

    failures = []
    for test in tests:
        started = time.monotonic()
        result = subprocess.run([sys.executable, str(test)], cwd=str(ROOT),
                                env=environment_for(test), capture_output=True,
                                text=True, encoding="utf-8", errors="replace")
        elapsed = time.monotonic() - started
        ok = result.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {test.name:<34} {elapsed:5.1f}s", flush=True)
        if not ok:
            failures.append((test.name, result.returncode,
                             (result.stdout or "") + (result.stderr or "")))

    print()
    print(f"{len(tests) - len(failures)}/{len(tests)} passed")
    for name, code, output in failures:
        print(f"\n----- {name} (exit {code}) " + "-" * 24)
        print("\n".join(output.splitlines()[-30:]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
