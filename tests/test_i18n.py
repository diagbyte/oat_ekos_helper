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

# the toggles and dialogs are built at runtime, so they need explicit lookups
w.settings_btn.setChecked(True)
pump(w, 0.3)
expanded = w.settings_btn.text()
w.settings_btn.setChecked(False)
pump(w, 0.3)
collapsed = w.settings_btn.text()
print("settings toggle:", expanded, "/", collapsed)
assert "설정" in expanded and "설정" in collapsed, "the settings toggle must stay translated"

w.log_toggle.setChecked(False)
pump(w, 0.3)
print("log toggle:", w.log_toggle.text())
assert "로그" in w.log_toggle.text()
w.log_toggle.setChecked(True)

w.advanced_toggle.setChecked(True)
pump(w, 0.5)
print("advanced toggle:", w.advanced_toggle.text())
assert "고급" in w.advanced_toggle.text()
w.advanced_toggle.setChecked(False)

# A dialog built at runtime must be translated too. translate_widget_tree only
# reaches widgets that exist when it runs, so a QMessageBox nobody has opened is
# invisible to it - these have to be wrapped in _() by hand.
captured = {}
from PyQt5 import QtWidgets as _Qt
_Qt.QMessageBox.question = staticmethod(
    lambda parent, title, text, *a, **k: (captured.update(title=title, text=text),
                                          _Qt.QMessageBox.No)[1])
w.end_session()
print("end-session dialog title:", captured.get("title"))
print("end-session dialog body:", captured.get("text", "").splitlines()[:1])
assert captured, "end_session must ask before moving the mount"
assert any("\uac00" <= ch <= "\ud7a3" for ch in captured.get("title", "")), \
    "the dialog title must be translated"
assert any("\uac00" <= ch <= "\ud7a3" for ch in captured.get("text", "")), \
    "the dialog body must be translated"


shutdown_app(app, w, _server)
