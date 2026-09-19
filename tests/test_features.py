import os, sys, time, shutil
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/newfeat"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'oat_helper'))
shutil.rmtree("/tmp/newfeat", ignore_errors=True)
Path("/tmp/newfeat/.config").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
import oat_helper as oat_tools
app = QtWidgets.QApplication([])
w = oat_tools.OATHelper()


def pump(t):
    e = time.time() + t
    while time.time() < e:
        app.processEvents(); time.sleep(0.01)


pump(3.5)
print("1. firmware version:", w.firmware_version_text, "->", w.firmware_version_num,
      "| gate >=10921:", w._fw_at_least(10921), "| gate >=11306:", w._fw_at_least(11306))
print("   AZ/ALT home button enabled (needs 11306):", w.pa_home_btn.isEnabled())
print("2. DEC limits read:", w.dec_limits_firmware, "|", w.dec_fw_limit_label.text())

# move DEC then set the upper limit from the current position
w.dec_manual_jog_move(5); pump(2.5)
w.set_dec_limit_here("U"); pump(2.5)
print("3. after 'set upper limit here':", w.dec_fw_limit_label.text())

# target reachability
w.target_ra.setValue(3.0); w.target_dec.setValue(60.0)
w.check_target_reachable(); pump(2.0)
print("4. target check:", w.target_check_label.text()[:110])
w.target_dec.setValue(-80.0)
w.check_target_reachable(); pump(2.0)
print("   out-of-limit case:", w.target_check_label.text()[:110])

# slew rate + tracking trim
w.slew_rate_box.setCurrentIndex(0); pump(1.0)
w.read_tracking_trim(); pump(1.5)
print("5. slew rate cfg:", w.cfg["slew_rate"], "| tracking:", w.track_speed_label.text())
w.track_trim.setValue(1.0025); w.save_tracking_trim(); pump(2.0)

# EEPROM DEC home offset mode + SET HOME
w.dec_offset_eeprom.setChecked(True)
w.finish_dec_manual_home(); pump(5.0)
print("6. set home w/ eeprom mode -> XGHD:", w.indi.meade(":XGHD#").strip(),
      "| stored offset:", w._stored_dec_home_offset())

# park / unpark
w.park_mount(); pump(3.0)
w.unpark_mount(); pump(1.5)

# axis calibration apply
w.axis_calculated_spd = 1010.5; w.axis_calculated_axis = "RA"
w.apply_axis_calibration(); pump(3.0)
print("7. RA steps/deg now:", w.indi.meade(":XGR#").strip())

# checklist
print("8. checklist items:", len(w.checklist_boxes), "| first:", w.checklist_boxes[0].text())
w.checklist_boxes[0].setChecked(True)
print("   saved done:", w.cfg["checklist_done"])

# keyboard control
from PyQt5 import QtCore, QtGui
w.tabs.setCurrentWidget(w.mini_tab); pump(0.3)
w.keyPressEvent(QtGui.QKeyEvent(QtCore.QEvent.KeyPress, QtCore.Qt.Key_Right, QtCore.Qt.NoModifier))
pump(2.0)
print("9. keyboard RA move issued:", any("RA" in l and "move" in l.lower() for l in w.log_box.toPlainText().splitlines()[-6:]))

print("--- errors in log ---")
for l in w.log_box.toPlainText().splitlines():
    if "실패" in l or "ERROR" in l or "error" in l:
        print("  ", l.split("] ", 1)[-1][:120])


shutdown_app(app, w, _server)
