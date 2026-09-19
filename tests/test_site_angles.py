import os
import sys
import time
import shutil
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/site_test"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
shutil.rmtree("/tmp/site_test", ignore_errors=True)
Path("/tmp/site_test/.config").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper as oat_tools  # noqa: E402

# --- language detection ---------------------------------------------------
for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
    os.environ.pop(key, None)
print("no env ->", oat_tools.detect_system_language())
os.environ["LANG"] = "ko_KR.UTF-8"
print("LANG=ko_KR.UTF-8 ->", oat_tools.detect_system_language())
os.environ["LANGUAGE"] = "de:en"
print("LANGUAGE=de:en (KDE wins) ->", oat_tools.detect_system_language())
os.environ.pop("LANGUAGE")
os.environ["LANG"] = "C"
Path("/tmp/site_test/.config/plasma-localerc").write_text("[Translations]\nLANGUAGE=ko\n")
print("plasma-localerc LANGUAGE=ko ->", oat_tools.detect_system_language())
Path("/tmp/site_test/.config/plasma-localerc").unlink()
os.environ["LANG"] = "en_US.UTF-8"

# --- site angles ----------------------------------------------------------
app = QtWidgets.QApplication([])
w = oat_tools.OATHelper()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


pump(3.0)
print("site raw:", w.site_angles())
print("label:", w.site_angle_label.text().replace("\n", " | "))
w.cfg["magnetic_declination_deg"] = -8.0   # optional config key, no UI
w.update_site_angles()
pump(0.5)
print("with optional declination -8:", w.site_angle_label.text().replace("\n", " | "))
print("checklist items:", [b.text() for b in w.checklist_boxes][:3])
idx = w.lang_box.findData("ko")
w.lang_box.setCurrentIndex(idx)
pump(1.0)
w.update_site_angles()
pump(0.3)
print("korean:", w.site_angle_label.text().replace("\n", " | "))


shutdown_app(app, w, _server)
