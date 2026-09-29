"""An exception in a callback must not make the extension vanish.

PyQt5 >= 5.5 sends an unhandled exception raised inside a slot to qFatal(),
which calls abort(). Every run_async() result handler and every QTimer slot is
such a slot, so one unexpected None mid-session took the whole window down with
no message, no log line and no traceback - Ekos just shows the extension gone.

The suite could never catch it: test_full_flow.py installs its own
sys.excepthook to collect tracebacks, and a non-default hook is exactly what
stops PyQt5 aborting. So this test runs a child process with no hook of its
own, the way Ekos starts the extension.
"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHILD = textwrap.dedent("""
    import os, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, r"{helper}")
    from PyQt5 import QtCore, QtWidgets
    import oat_helper

    # Exactly what main() does before showing the window.
    oat_helper.install_crash_guard()

    app = QtWidgets.QApplication([])

    def boom():
        raise RuntimeError("deliberate failure inside a timer slot")

    QtCore.QTimer.singleShot(50, boom)
    QtCore.QTimer.singleShot(1200, lambda: (print("SURVIVED", flush=True), app.quit()))
    app.exec_()
    sys.exit(0)
""").format(helper=str(ROOT / "oat_helper"))

env = dict(os.environ)
env["HOME"] = env["USERPROFILE"] = env.get("HOME", "/tmp/crashguard")
env["QT_QPA_PLATFORM"] = "offscreen"
env["PYTHONIOENCODING"] = "utf-8"

result = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env, timeout=120)

print("child exit code:", result.returncode)
print("child stdout:", (result.stdout or "").strip()[:200])
if result.returncode != 0:
    print("child stderr:", (result.stderr or "").strip()[-400:])

assert "SURVIVED" in (result.stdout or ""), (
    "a raising slot killed the process: PyQt5 aborted instead of reporting it")
assert result.returncode == 0, f"child aborted with {result.returncode}"
assert "deliberate failure" in (result.stderr or "") + (result.stdout or ""), (
    "the exception must still be reported, not swallowed silently")

print("a raising callback is reported and survived")
