import os,sys,time
from pathlib import Path
import shutil
os.environ["QT_QPA_PLATFORM"]="offscreen"; os.environ["HOME"]="/tmp/simplehome"; sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'oat_helper'))
shutil.rmtree("/tmp/simplehome", ignore_errors=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets
import oat_helper as oat_tools
app=QtWidgets.QApplication([]); w=oat_tools.OATHelper(); w.resize(940,660); w.show()
def pump(t):
    e=time.time()+t
    while time.time()<e: app.processEvents(); time.sleep(0.01)
pump(2.5)
print("simple tabs:", [w.tabs.tabText(i) for i in range(w.tabs.count())])
print("park btn visible:", w.advanced_widgets[0].isVisible(), "| hidden advanced count:",
      sum(1 for x in w.advanced_widgets if not x.isVisible()), "/", len(w.advanced_widgets))
for i,name in enumerate(["wizard","home","pa"]):
    w.tabs.setCurrentIndex(i); pump(0.4)
    page=w.tabs.widget(i)
    print(f"  {name:7} viewport={page.viewport().height()} content={page.widget().sizeHint().height()}")
w.tabs.setCurrentIndex(1); pump(0.3); w.grab().save("/tmp/simple_home.png")
w.advanced_toggle.setChecked(True); pump(1.0)
print("advanced tabs:", [w.tabs.tabText(i) for i in range(w.tabs.count())])
print("advanced visible now:", sum(1 for x in w.advanced_widgets if x.isVisible()), "/", len(w.advanced_widgets))
w.grab().save("/tmp/adv_home.png")
w.save_config(); w.advanced_toggle.setChecked(False); pump(0.6); w.save_config()
import json,pathlib
print("saved simple_mode:", json.loads(pathlib.Path(oat_tools.CONFIG_FILE).read_text())["simple_mode"])


shutdown_app(app, w, _server)
