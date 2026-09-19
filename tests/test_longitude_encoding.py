"""The mount computes sidereal time from the longitude it was given.

Driver and firmware disagree about which Meade longitude convention they
speak, and the wrong one puts the site on the other side of the planet -
a 7 hour sidereal error for Korea, which makes GOTO flip across the
meridian. The repair has to find the encoding that actually works.
"""
import os,sys,time,shutil
from pathlib import Path
os.environ["QT_QPA_PLATFORM"]="offscreen"; os.environ["HOME"]="/tmp/clockfix"; os.environ["LANG"]="en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/clockfix", ignore_errors=True); Path("/tmp/clockfix/.config").mkdir(parents=True, exist_ok=True)
from _harness import ensure_indi_server, shutdown_app
srv=ensure_indi_server()
from PyQt5 import QtWidgets
QtWidgets.QMessageBox.question=staticmethod(lambda *a,**k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning=staticmethod(lambda *a,**k: QtWidgets.QMessageBox.No)
import oat_helper
app=QtWidgets.QApplication([]); w=oat_helper.OATHelper(); w.show()
def pump(t):
    e=time.time()+t
    while time.time()<e: app.processEvents(); time.sleep(0.01)
pump(3.0)
d=w.mount_lst_drift_minutes()
print("before: mount LST %s, drift %.0f min" % (w._hours_to_hms(d[1]), d[0]))
w.repair_mount_clock(); pump(12.0)
d2=w.mount_lst_drift_minutes()
print("after : mount LST %s, drift %.1f min" % (w._hours_to_hms(d2[1]), d2[0]))
print("remembered encoding:", w.cfg.get("longitude_command"))
for l in w.log_box.toPlainText().splitlines()[-3:]:
    print("  ", l.split("] ", 1)[-1][:110])

assert d[0] > 60, "the fake mount should start with the wrong longitude convention"
assert d2[0] < 5, "the repair must find an encoding that yields the right sidereal time"
assert w.cfg.get("longitude_command"), "the working encoding should be remembered"
shutdown_app(app,w,srv)
