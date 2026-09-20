"""Walk every widget in Korean and report anything still in English.

The catalog is applied by walking the widget tree, so a missing entry is
invisible until someone runs the UI in that language. This makes it fail
loudly instead.
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/i18n_cov"
os.environ["LANG"] = "ko_KR.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/i18n_cov", ignore_errors=True)
Path("/tmp/i18n_cov/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/i18n_cov/.config/oat-helper/config.json").write_text(
    json.dumps({"simple_mode": False, "always_show_firmware_tab": True, "language": "ko"}))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.resize(1000, 640)
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


pump(3.0)

HANGUL = re.compile(r"[\uac00-\ud7a3]")
# Technical text that stays English on purpose: commands, units, identifiers.
ALLOWED = re.compile(
    r"^[\s\d.,:+=°'\"′″%/*|↖↑↗←→↙↓↘-]*$"                # arrows, numbers, units
    r"|^HOME$|^AutoPA$"                                  # a product name, kept as is
    r"|^[\d.]+\s*(s|h|m|°|′|″)(/axis)?$"                   # spin box values with a unit
    r"|^:?X?[A-Za-z]{1,6}[#0-9]"                      # Meade commands
    r"|INDI|LX200|OAT|PlatformIO|GX|RA|DEC|ALT|AZ|HA|LST|PAA|EEPROM|FW|git|Ekos|KStars"
    r"|SET HOME|GO TO HOME|PARK|UNPARK|STOP|BUILD|FLASH|RESET|Host|Port|develop|master"
    r"|Configuration_local|hpp|Language|auto|English|한국어|Home|Mount|Settings|Advanced")

missing = []
for widget in w.findChildren(QtWidgets.QWidget):
    if isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
        continue
    for getter in ("text", "title", "toolTip", "placeholderText"):
        method = getattr(widget, getter, None)
        if not callable(method):
            continue
        try:
            value = method()
        except Exception:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if HANGUL.search(value) or ALLOWED.search(value):
            continue
        missing.append((type(widget).__name__, getter, value.replace("\n", " ")[:90]))

for index in range(w.tabs.count()):
    label = w.tabs.tabText(index)
    if not HANGUL.search(label) and not ALLOWED.search(label):
        missing.append(("QTabWidget", "tabText", label))

print(f"untranslated visible strings: {len(missing)}")
for kind, getter, value in missing:
    print(f"  {kind:16} {getter:16} {value!r}")

shutdown_app(app, w, _server)
sys.exit(1 if missing else 0)
