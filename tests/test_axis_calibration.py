"""Axis calibration must measure the mechanical rotation, not the sky RA.

With Tracking OFF (what the tab used to recommend) the pointing is fixed to
the ground, so the plate-solved RA grows by the sidereal time between the two
solves. The old code used |dRA| and was off by ~2.5 % per minute for a 10 deg
move, in a direction that depends on the move direction, so every "recommended"
value moved the mount further away from the truth.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/axishome"
shutil.rmtree("/tmp/axishome", ignore_errors=True)
Path("/tmp/axishome/.config").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

SID = oat_helper.OATHelper.SIDEREAL_RATIO
rot = oat_helper.OATHelper._axis_rotation_deg


def close(a, b, tol=1e-6):
    return abs(a - b) < tol


# ---- pure geometry ---------------------------------------------------------
# RA +10 deg, tracking OFF, 60 s between solves: sky RA also grew by 60 s.
start = (1.0, 20.0)
end = (1.0 + 10.0 / 15.0 + 60.0 / 3600.0 * SID, 20.0)
deg, how = rot("RA", start, end, 60.0, False)
print(f"RA +10, OFF, 60 s : {deg:.6f}°  ({how})")
assert close(deg, 10.0), deg
assert abs(abs(oat_helper.OATHelper._wrap_hours(end[0] - start[0])) * 15 - 10.0) > 0.2  # old |dRA| was wrong

# RA -10 deg against the sky, 90 s.
end = (1.0 - 10.0 / 15.0 + 90.0 / 3600.0 * SID, 20.0)
deg, _ = rot("RA", start, end, 90.0, False)
print(f"RA -10, OFF, 90 s : {deg:.6f}°")
assert close(deg, 10.0), deg

# Across 0h: 23.9h -> 0.6h+
end = (((23.9 + 10.0 / 15.0 + 30.0 / 3600.0 * SID) % 24.0), 20.0)
deg, _ = rot("RA", (23.9, 20.0), end, 30.0, False)
print(f"RA across 0h      : {deg:.6f}°")
assert close(deg, 10.0), deg

# Tracking ON: the sky RA difference is the move.
deg, how = rot("RA", start, (1.0 + 10.0 / 15.0, 20.0), 120.0, True)
print(f"RA +10, ON, 120 s : {deg:.6f}°  ({how})")
assert close(deg, 10.0), deg

# DEC plain and across the pole (85 -> pole -> 85 on the other side).
deg, _ = rot("DEC", (2.0, 20.0), (2.0, 30.0), 50.0, False)
assert close(deg, 10.0), deg
deg, how = rot("DEC", (2.0, 85.0), (14.0, 85.0), 50.0, False)
print(f"DEC over the pole : {deg:.6f}°  ({how})")
assert close(deg, 10.0), deg

# ---- the tab flow against the fake mount -----------------------------------
app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()


def pump(seconds):
    t_end = time.time() + seconds
    while time.time() < t_end:
        app.processEvents()
        time.sleep(0.01)


pump(3.0)
eod = {"pos": None}
w.indi.equatorial_eod = lambda: eod["pos"]

# Tracking OFF, RA +10 deg, 75 s between the two solves.
w.set_tracking(False); pump(1.2)
w.axis_sel.setCurrentText("RA")
eod["pos"] = (3.0, 10.0)
w.axis_record_start(); pump(1.0)
print("start label:", w.axis_start_label.text())
assert w.axis_start_label.text().endswith("tracking OFF"), w.axis_start_label.text()
w.axis_start_time -= 75.0
w.axis_commanded_deg = 10.0
elapsed = time.time() - w.axis_start_time          # ~75 s simulated + real test time
eod["pos"] = (3.0 + 10.0 / 15.0 + elapsed / 3600.0 * SID, 10.0)
w.axis_record_end(); pump(1.2)
text = w.axis_result.toPlainText()
print(text.splitlines()[4]); print(text.splitlines()[5])
scale = float(text.split("Scale error: ")[1].split("%")[0])
assert abs(scale) < 0.005, text                    # old code: about +3.1 %
assert "hour-angle difference" in text, text

# Tracking switched ON between the solves -> refused, not a bogus value.
w.axis_record_start(); pump(1.0)
w.axis_commanded_deg = 10.0
w.set_tracking(True); pump(1.2)
eod["pos"] = (3.5, 10.0)
w.axis_record_end(); pump(1.2)
refused = w.axis_result.toPlainText()
print("state change   :", refused.splitlines()[0])
assert refused.startswith("Tracking was OFF at the start solve and ON now."), refused

print("axis calibration OK")
shutdown_app(app, w, _server)
