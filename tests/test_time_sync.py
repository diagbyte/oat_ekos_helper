"""The mount's sidereal time is what GOTO is computed against.

A stale LST shifts the RA home reference, so a target ends up looking past the
RA limit: the firmware then flips across the meridian, inverts the DEC target
and stops at the DEC travel limit. The tool has to push the LST and verify it.
"""
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/time_sync"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/time_sync", ignore_errors=True)
Path("/tmp/time_sync/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/time_sync/.config/oat-helper/config.json").write_text(json.dumps({"simple_mode": False}))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402

answers = []
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: (answers.append(str(a[2])[:80]), QtWidgets.QMessageBox.No)[1])

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
# put the mount's clock 7 hours out, the way it was found in the field
# (a server left running by an earlier test may already hold a good value)
w.indi.meade("@Sg-127*00#")   # the convention the mount does not want: 7 h out
print("mount LST before sync:", w.indi.meade(":XGL#").strip(), "(stale on purpose)")
drift = w.mount_lst_drift_minutes()
print("drift detected: %.0f min" % drift[0])
assert drift[0] > 5, "the stale clock should be spotted"

# SET HOME must refuse while the clock is wrong
w.finish_dec_manual_home()
pump(1.5)
print("SET HOME blocked:", bool(answers))
print("  warning:", answers[0][:70] if answers else "-")
assert answers, "SET HOME must warn before storing a wrong RA reference"

# the HA button pushes the computed LST to the mount
w.sync_ha_time_location()
pump(6.0)
after = w.indi.meade(":XGL#").strip()
print("mount LST after sync:", after)
drift_after = w.mount_lst_drift_minutes()
print("drift after sync: %.2f min" % drift_after[0])
assert drift_after[0] < 5, "the tool must leave the mount with the right sidereal time"

lines = [l.split("] ", 1)[-1] for l in w.log_box.toPlainText().splitlines()]
print("verification logged:", any("Mount LST now" in l for l in lines))
print("status:", w.ha_status.text()[-30:])
assert any("Mount LST now" in l for l in lines)

# and SET HOME is allowed again
answers.clear()
w.finish_dec_manual_home()
pump(5.0)
print("SET HOME after sync blocked:", bool(answers))
print("set home status:", w.dec_manual_status.text()[:60])
assert not answers

# the mismatch must be repairable, not just reported
w.indi.meade("@Sg-127*00#")
drift_bad = w.mount_lst_drift_minutes()
print("clock knocked out again: %.0f min" % drift_bad[0])
w.repair_mount_clock()
pump(6.0)
drift_fixed = w.mount_lst_drift_minutes()
print("after 'Fix mount clock': %.2f min" % drift_fixed[0])
print("clock on the mount:", {k: v for k, v in w._mount_clock_snapshot().items() if k != "lst"})
assert drift_fixed[0] < 5, "the repair must leave the mount with the right sidereal time"
assert "SET HOME" in w.ha_status.text()

shutdown_app(app, w, _server)
