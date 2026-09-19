"""Exercise every user-facing action and fail on unexpected errors.

The fake INDI server must be running:  python3 tests/fake_indi_server.py &
"""
import os
import sys
import time
import shutil
import traceback
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/torture"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
shutil.rmtree("/tmp/torture", ignore_errors=True)
Path("/tmp/torture/.config").mkdir(parents=True, exist_ok=True)
Path("/tmp/torture/.config/kstarsrc").write_text("[General]\nLogToFile=true\nLogToDefault=false\n")
logdir = Path("/tmp/torture/.local/share/kstars/logs/2026-09-18")
logdir.mkdir(parents=True, exist_ok=True)
(logdir / "log_21-00-00.txt").write_text("start\n")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets, QtCore, QtGui  # noqa: E402

# auto-accept every dialog so the whole flow runs unattended
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)

import oat_helper as oat_tools  # noqa: E402

UNHANDLED = []
sys.excepthook = lambda *exc: UNHANDLED.append("".join(traceback.format_exception(*exc)))

app = QtWidgets.QApplication([])
w = oat_tools.OATHelper()
w.resize(1000, 640)
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def step(name, fn, settle=2.0):
    before = len(w.log_box.toPlainText().splitlines())
    try:
        fn()
    except Exception:
        UNHANDLED.append(f"{name} raised:\n{traceback.format_exc()}")
    pump(settle)
    new = w.log_box.toPlainText().splitlines()[before:]
    bad = [l for l in new if "failed" in l.lower() or "error" in l.lower() or "Traceback" in l]
    print(f"{'ok ' if not bad else 'ERR'} {name}")
    for line in bad:
        print("      ", line.split("] ", 1)[-1][:110])
    return bad


pump(3.5)
problems = []
problems += step("connect + firmware version", lambda: None, 1.0)
problems += step("HA / time sync", w.sync_ha_time_location, 3.0)
problems += step("read DEC limits", w.read_dec_limits)
problems += step("widen DEC lower limit", lambda: w.indi.meade("@XSDLL-60#"), 1.0)
problems += step("re-read DEC limits", w.read_dec_limits)
problems += step("DEC jog +5", lambda: w.dec_manual_jog_move(5), 3.0)
problems += step("RA jog +1", lambda: w.home_ra_jog_move(1), 3.0)
problems += step("SET HOME", w.finish_dec_manual_home, 5.0)
problems += step("GO TO HOME", w.mini_goto_home, 4.0)
problems += step("target check (reachable)",
                 lambda: (w.target_ra.setValue(3.0), w.target_dec.setValue(20.0), w.check_target_reachable()), 3.0)
problems += step("slew rate", lambda: w.slew_rate_box.setCurrentIndex(3), 1.5)
problems += step("tracking trim read", w.read_tracking_trim)
problems += step("tracking trim save", w.save_tracking_trim, 2.5)
problems += step("tracking on/off", lambda: (w.set_tracking(True), w.set_tracking(False)), 2.0)
problems += step("keyboard slew",
                 lambda: (w.tabs.setCurrentWidget(w.mini_tab),
                          w.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Right,
                                                          QtCore.Qt.NoModifier))), 3.0)
problems += step("mini pad diagonal", lambda: w.mini_direction_move(-1, -1), 4.0)
problems += step("unpark", w.unpark_mount, 1.5)
problems += step("park", w.park_mount, 5.0)
problems += step("shutdown position", lambda: (w.release_dec.setValue(-30.0), w.move_to_release_position()), 8.0)
# simulate a power cycle: the firmware zeroes the axes wherever they stand
problems += step("simulated power cycle", lambda: (w.indi.meade("&SHP#"),
                                                   setattr(w, "dec_zero_shift", 0),
                                                   setattr(w, "dec_odometer_valid", True)), 2.0)
problems += step("restore saved DEC Home", lambda: w.restore_saved_dec_home(confirm=False), 8.0)
problems += step("DEC power-off position", w.dec_park_for_power_off, 8.0)
problems += step("PAA log diagnosis", w.diagnose_paa_log, 3.0)
problems += step("AutoPA start", w.start_autopa_watch, 3.0)
problems += step("AutoPA stop", w.stop_autopa_watch, 1.0)
problems += step("manual ALT/AZ move", lambda: w.move_pa(1.0, -1.0), 5.0)
problems += step("read AutoPA position", w.read_pa_position, 2.0)
problems += step("axis calibration apply",
                 lambda: (setattr(w, "axis_calculated_spd", 1005.0), setattr(w, "axis_calculated_axis", "RA"),
                          w.apply_axis_calibration()), 4.0)
problems += step("drift alignment", lambda: (w.drift_seconds.setValue(10), w.run_drift_alignment()), 3.0)
problems += step("diagnostics", w.refresh_diagnostics, 4.0)
problems += step("monitor refresh", w.refresh_mount_monitor, 3.0)
problems += step("checklist edit state", lambda: w.checklist_boxes[0].setChecked(True), 0.5)
problems += step("language ko", lambda: w.lang_box.setCurrentIndex(w.lang_box.findData("ko")), 1.5)
problems += step("advanced mode", lambda: w.advanced_toggle.setChecked(True), 1.5)
problems += step("simple mode", lambda: w.advanced_toggle.setChecked(False), 1.5)
problems += step("language en", lambda: w.lang_box.setCurrentIndex(w.lang_box.findData("en")), 1.5)
problems += step("emergency stop", w.emergency_stop, 2.0)
problems += step("save config", w.save_config, 1.0)

pump(2.0)
print()
print("unexpected exceptions:", len(UNHANDLED))
for item in UNHANDLED[:3]:
    print(item[:600])
print("steps with errors:", len(problems))
shutdown_app(app, w, _server)

sys.exit(1 if (UNHANDLED or problems) else 0)
