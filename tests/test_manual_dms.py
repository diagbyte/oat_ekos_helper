"""Ekos reports the polar error as degrees/minutes/seconds, so the manual
AutoPA move takes the same units: no converting 1°52'00" into 112' by hand.
"""
import os,sys,time,shutil,json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"]="offscreen"; os.environ["HOME"]="/tmp/dms"; os.environ["LANG"]="en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/dms", ignore_errors=True)
Path("/tmp/dms/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/dms/.config/oat-helper/config.json").write_text(json.dumps({"simple_mode": False}))
from _harness import ensure_indi_server, shutdown_app
srv=ensure_indi_server()
from PyQt5 import QtWidgets
import oat_helper
app=QtWidgets.QApplication([]); w=oat_helper.OATHelper(); w.resize(1000,640); w.show()
def pump(t):
    e=time.time()+t
    while time.time()<e: app.processEvents(); time.sleep(0.01)
pump(3.0)
# exactly what Ekos reports: -1° 52' 00"
w.alt_move.set_arcmin(-112.0)
print("ALT set from -112′ ->", w.alt_move.value(), "|", w.alt_move._preview.text())
w.az_move._sign.setCurrentIndex(0); w.az_move._deg.setValue(0); w.az_move._min.setValue(0); w.az_move._sec.setValue(30.0)
print("AZ 30 arcsec ->", round(w.az_move.value(), 4), "|", w.az_move._preview.text())
w.az_move._deg.setValue(6); w.az_move._min.setValue(17); w.az_move._sec.setValue(0)
print("AZ 6d17m ->", round(w.az_move.value(), 3), "|", w.az_move._preview.text())
w.alt_move._deg.setValue(19); w.alt_move._min.setValue(0); w.alt_move._sec.setValue(0); w.alt_move._sign.setCurrentIndex(0)
print("ALT 19d (over limit) ->", w.alt_move.value(), "|", w.alt_move._preview.text())
tabs=[w.tabs.tabText(i) for i in range(w.tabs.count())]
w.tabs.setCurrentIndex(tabs.index("AutoPA")); pump(0.5); w.grab().save("/tmp/shot_dms.png")
assert w.alt_move.value() == 140, "an entry beyond the travel limit must be clamped"
w.alt_move.set_arcmin(-112.0)
assert (w.alt_move._sign.currentText(), w.alt_move._deg.value(), w.alt_move._min.value()) == ("-", 1, 52), \
    "-112' should read back as -1\u00b0 52'"
w.az_move.set_arcmin(0.5)
assert abs(w.az_move.value() - 0.5) < 1e-6, "sub-arcminute entry must survive the round trip"

shutdown_app(app, w, srv)
