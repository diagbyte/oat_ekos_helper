import os,sys,time
from pathlib import Path
import shutil
os.environ["QT_QPA_PLATFORM"]="offscreen"; os.environ["HOME"]="/tmp/relhome"; sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'oat_helper'))
shutil.rmtree("/tmp/relhome", ignore_errors=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets
QtWidgets.QMessageBox.question=staticmethod(lambda *a,**k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning=staticmethod(lambda *a,**k: QtWidgets.QMessageBox.Ok)
import oat_helper as oat_tools
app=QtWidgets.QApplication([]); w=oat_tools.OATHelper(); w.resize(940,660); w.show()
def pump(t):
    e=time.time()+t
    while time.time()<e: app.processEvents(); time.sleep(0.01)
pump(3.0)
print("fw:", w.firmware_version_text, "| dec limits:", w.dec_limits_firmware)
print("release default:", w.release_dec.value(), "visible in simple mode:", w.release_btn.isVisible())
# widen DEC limits so -30 is allowed
w.indi.meade("@XSDLL45#"); time.sleep(0.2); w.read_dec_limits(); pump(1.5)
print("limits now:", w.dec_limits_firmware)
# set home first so Home = 0
w.finish_dec_manual_home(); pump(4.0)
print("at home GX:", w.indi.meade(":GX#").strip())
# move to release position
w.move_to_release_position(); pump(8.0)
gx=w._parse_gx(w.indi.meade(":GX#"))
spd=float(w.indi.meade(":XGD#").strip().rstrip("#"))
print("after release: DEC steps =", gx["dec_steps"], "=", round(gx["dec_steps"]/spd,2), "deg")
print("stored dec_home_offset_steps:", w.cfg["dec_home_offset_steps"],
      "(=", round(w.cfg["dec_home_offset_steps"]/spd,2), "deg)")
print("restore label:", w.dec_restore_btn.text())
print("status:", w.dec_manual_status.text())
# simulate next session: restore should bring DEC back to home
w.dec_zero_shift=0; w.dec_odometer_valid=True
print("--- log tail ---")
for l in w.log_box.toPlainText().splitlines()[-5:]: print("  ", l.split("] ",1)[-1][:110])
w.tabs.setCurrentIndex(1); pump(0.4); w.grab().save("/tmp/release_home.png")


shutdown_app(app, w, _server)
