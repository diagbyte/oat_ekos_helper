"""Progress reporting for build/flash, and the INDI device picker."""
import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/progress_test"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/progress_test", ignore_errors=True)
Path("/tmp/progress_test/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/progress_test/.config/oat-helper/config.json").write_text(
    json.dumps({"always_show_firmware_tab": True, "simple_mode": False}))

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

# --- device picker ---------------------------------------------------------
print("devices announced by INDI:", w.indi.known_devices)
items = [w.device_edit.itemText(i) for i in range(w.device_edit.count())]
print("picker items:", items)
assert "LX200 OpenAstroTech" in items, "the connected device should be offered"
assert w.device_edit.isEditable(), "typing a name must still be possible"

# --- streamed output and progress -----------------------------------------
script = Path("/tmp/progress_test/fake_build.py")
script.write_text(
    "import sys, time\n"
    "print('Compiling src/Mount.cpp.o', flush=True)\n"
    "time.sleep(0.2)\n"
    "print('Linking .pio/build/firmware.elf', flush=True)\n"
    "for pct in (25, 50, 75, 100):\n"
    "    print('Writing | ####### | %d%% 1.2s' % pct, flush=True)\n"
    "    time.sleep(0.15)\n"
    "sys.exit(0)\n")

seen = {"lines": 0, "values": []}
w.process_signals.line.connect(lambda _l: seen.__setitem__("lines", seen["lines"] + 1))


def run_build():
    code, out = w._run_streamed([sys.executable, str(script)], cwd="/tmp/progress_test", timeout=60)
    seen["code"] = code
    return code


w.run_async(run_build, None, lambda e: print("ERROR", e))
for _ in range(40):
    pump(0.1)
    seen["values"].append(w.fw_progress.value())

pump(1.5)
print("lines streamed:", seen["lines"], "| exit code:", seen.get("code"))
print("progress values seen:", sorted(set(v for v in seen["values"] if v)))
print("label:", w.fw_progress_label.text())
print("cancel button hidden after finish:", w.fw_cancel_btn.isHidden())
assert seen["lines"] >= 6, "every output line should reach the GUI"
assert 100 in seen["values"], "the percentage in the output should drive the bar"
assert "Finished" in w.fw_progress_label.text()

w.tabs.setCurrentIndex([w.tabs.tabText(i) for i in range(w.tabs.count())].index("Firmware"))
pump(0.5)
w.grab().save("/tmp/shot_progress.png")

shutdown_app(app, w, _server)
