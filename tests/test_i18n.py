import os,sys,time
from pathlib import Path
import shutil
os.environ["QT_QPA_PLATFORM"]="offscreen"; os.environ["HOME"]="/tmp/i18nhome"; sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'oat_helper'))
shutil.rmtree("/tmp/i18nhome", ignore_errors=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets
import oat_helper as oat_tools
app=QtWidgets.QApplication([]); 
def pump(w,t):
    e=time.time()+t
    while time.time()<e: app.processEvents(); time.sleep(0.01)
# English (default)
os.environ["LANG"]="en_US.UTF-8"
w=oat_tools.OATHelper(); w.resize(940,660); w.show(); pump(w,2.0)
print("EN tabs:", [w.tabs.tabText(i) for i in range(w.tabs.count())])
print("EN set home btn:", w.dec_manual_save_btn.text())
print("EN release btn:", w.release_btn.text())
w.grab().save("/tmp/i18n_en.png")
# switch to Korean live
idx=w.lang_box.findData("ko"); w.lang_box.setCurrentIndex(idx); pump(w,1.0)
print("KO tabs:", [w.tabs.tabText(i) for i in range(w.tabs.count())])
print("KO set home btn:", w.dec_manual_save_btn.text())
print("KO release btn:", w.release_btn.text())
print("KO group title:", w.findChildren(QtWidgets.QGroupBox)[2].title() if len(w.findChildren(QtWidgets.QGroupBox))>2 else "-")
w.grab().save("/tmp/i18n_ko.png")
print("log stays english:", w.log_box.toPlainText().splitlines()[-1][:60])


shutdown_app(app, w, _server)
