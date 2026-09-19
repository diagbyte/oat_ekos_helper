"""`:SC#` (set date) answers twice, which used to shift every later read.

The symptom in the field was a date coming back as "109/19/26" and a sidereal
time that jumped around, so the clock repair looked like it did nothing.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/resync_test"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/resync_test", ignore_errors=True)
Path("/tmp/resync_test/.config").mkdir(parents=True, exist_ok=True)

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.No)

import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


pump(3.0)

# a wrong date forces the repair to send :SC#, the command that replies twice
w.indi.meade("@SC01/01/20#")
pump(0.5)
w._resync_meade()
w.indi.meade("@SHL065722#")
print("before : date=%s lst=%s drift=%.0f min" % (
    w.indi.meade(":GC#").strip(), w.indi.meade(":XGL#").strip(),
    w.mount_lst_drift_minutes()[0]))

w.repair_mount_clock()
pump(8.0)
snapshot = w._mount_clock_snapshot()
drift = w.mount_lst_drift_minutes()[0]
print("after  : date=%s offset=%s longitude=%s" % (
    snapshot["date"], snapshot["offset"], snapshot["longitude"]))
print("drift  : %.2f min" % drift)

version = w.indi.meade(":GVN#").strip().rstrip("#")
print("version reads clean:", version)

assert not snapshot["date"].startswith("1"), "a stale reply leaked into the date"
assert len(snapshot["date"]) == 8, f"unexpected date {snapshot['date']!r}"
assert drift < 5, "the clock repair must land"
assert version.lstrip("Vv")[0].isdigit(), "the channel is still out of step"

shutdown_app(app, w, _server)
