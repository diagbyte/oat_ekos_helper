"""The shutdown position is a position, so going there twice must not move twice.

Reported from a real session: pressing "Move to shutdown position" a second
time drove DEC past its travel and the motor lost steps.

The shutdown position is defined as "DEC x degrees from Home", but the move was
issued as :MXd<full angle>#, which is a *relative* step move. From Home that
lands on the right place; pressed again from the shutdown position it moves the
whole angle again and lands at twice the offset. The pre-flight limit check did
not catch it either, because it validated the configured angle against the
firmware limits rather than the destination the mount would actually reach.

:GX# reports DEC relative to Home(0), so the move is the difference between
where the axis is and where it should end up.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/shutdown_abs"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/shutdown_abs", ignore_errors=True)
Path("/tmp/shutdown_abs/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/shutdown_abs/.config/oat-helper/config.json").write_text(
    '{"simple_mode": false, "release_dec_deg": -33.0, "release_ra_deg": 0.0}', encoding="utf-8")

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


def dec_steps():
    return int(w._parse_gx(w.indi.meade(":GX#"))["dec_steps"])


pump(3.0)
w.indi.meade("@XSDLL-90#")          # widen the firmware travel so the fake does not clamp
pump(0.5)
w.read_dec_limits()
pump(1.5)

w.finish_dec_manual_home()          # Home(0) here
pump(5.0)
assert dec_steps() == 0, "SET HOME should have zeroed DEC"

spd = float(w.cfg.get("dec_home_steps_per_degree") or 314.1666667)
w.release_dec.setValue(-33.0)

# --- first press: Home -> shutdown position ------------------------------
w.move_to_release_position(confirm=False)
pump(9.0)
first = dec_steps()
print(f"after 1st press: DEC = {first:+d} steps = {first / spd:+.1f}°")
assert abs(first / spd - (-33.0)) < 1.0, f"the first move should land on -33°, got {first / spd:+.1f}°"

# --- second press: it is already there, so nothing may move --------------
# This is the reported case. A relative move sends the whole angle again and
# drives the axis to -66°, past its travel, and the motor loses steps.
w.move_to_release_position(confirm=False)
pump(9.0)
second = dec_steps()
print(f"after 2nd press: DEC = {second:+d} steps = {second / spd:+.1f}°")
assert second == first, (
    f"the shutdown position is a position: pressing twice must not move twice "
    f"({first / spd:+.1f}° -> {second / spd:+.1f}°)")

# --- and the recorded restore travel must not be corrupted ---------------
saved = w.cfg.get("dec_home_offset_steps")
print(f"saved restore travel: {saved} steps = {saved / spd:+.1f}°")
assert abs(saved / spd - 33.0) < 1.0, \
    f"the restore travel must stay the mirror of one shutdown move, got {saved / spd:+.1f}°"

print("shutdown position is idempotent")
shutdown_app(app, w, _server)
