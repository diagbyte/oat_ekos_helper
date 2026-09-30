"""Restoring the saved DEC Home is valid once, right after power-on.

The stored value is not a position - it is the DEC travel from wherever the
mount powered on to Home, replayed blind from the current position. That is
only correct while DEC is still standing where it powered on.

Its guards were meant to enforce that, but they check "the odometer is clean
and :GX# DEC reads 0" - and SET HOME sets exactly that state: :SHP# zeroes the
DEC coordinate, and finish_dec_manual_home() resets dec_zero_shift and
dec_odometer_valid. So after any SET HOME the guards pass again, and pressing
"Restore DEC Home" replays the whole travel from Home and runs SET HOME at the
end of it, silently redefining Home a shutdown-move away from the real one.

Verified on the fake mount before the fix:
    "Saved DEC Home restored (+9426 step move ...)"  with DEC already at Home.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/restore_once"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/restore_once", ignore_errors=True)
Path("/tmp/restore_once/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/restore_once/.config/oat-helper/config.json").write_text(
    '{"simple_mode": false, "release_dec_deg": -30.0, "release_ra_deg": 0.0}', encoding="utf-8")

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def lines(since=0):
    return w.log_box.toPlainText().splitlines()[since:]


pump(3.0)
w.indi.meade("@XSDLL-90#")            # widen the firmware DEC travel for the test
pump(0.5)
w.read_dec_limits()
pump(1.5)

# --- build a saved DEC Home the normal way --------------------------------
w.finish_dec_manual_home()            # SET HOME here
pump(5.0)
w.release_dec.setValue(-30.0)
w.move_to_release_position(confirm=False)   # records the reverse travel
pump(9.0)
saved = w.cfg.get("dec_home_offset_steps")
print("saved DEC Home travel:", saved)
assert saved, "the shutdown move should have recorded a restore value"

# --- power cycle: the mount reconnects and the firmware zeroes DEC here ---
# Use the real handler rather than poking flags, so the test exercises the same
# reset path a reconnect takes.
w.on_mount_connection_changed(True)
w.indi.meade("@SHP#")
pump(1.0)

# --- 1. the intended use: right after power-on, it must run ---------------
mark = len(lines())
w.restore_saved_dec_home(confirm=False)
pump(9.0)
restored = [l for l in lines(mark) if "Saved DEC Home restored" in l]
print("first restore ran:", bool(restored))
assert restored, "restoring right after power-on is the whole point; it must work"

# --- 2. the bug: pressing it again, with DEC now at Home ------------------
# SET HOME has just zeroed the DEC coordinate and the odometer, so every guard
# reads exactly as it does at power-on. Replaying the travel from here walks
# DEC a full shutdown move away and calls that Home.
mark = len(lines())
w.restore_saved_dec_home(confirm=False)
pump(9.0)
again = lines(mark)
for line in again:
    print("   |", line.split("] ", 1)[-1][:100])

moved = [l for l in again if "Saved DEC Home restored" in l]
refused = [l for l in again if "already" in l.lower() or "power-on" in l.lower()]
print("moved again:", bool(moved), "| refused:", bool(refused))
assert not moved, ("Restore must not replay the travel once Home is established: "
                   "it moves DEC a shutdown move away and calls that Home")
assert refused, "refusing silently is not enough - it must say why"

# --- 3. a real power cycle makes it available again ----------------------
w.on_mount_connection_changed(True)
pump(1.0)
w.indi.meade("@SHP#")
pump(0.5)
mark = len(lines())
w.restore_saved_dec_home(confirm=False)
pump(9.0)
print("available again after a reconnect:", bool([l for l in lines(mark) if "Saved DEC Home restored" in l]))
assert [l for l in lines(mark) if "Saved DEC Home restored" in l], \
    "a mount reconnect is a possible power cycle, so restoring must be possible again"

print("restore is once per power cycle")
shutdown_app(app, w, _server)
