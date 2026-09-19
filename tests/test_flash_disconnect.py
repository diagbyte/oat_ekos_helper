"""The upload must own the serial port: the mount is disconnected first."""
import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/flash_disconnect"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/flash_disconnect", ignore_errors=True)
Path("/tmp/flash_disconnect/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/flash_disconnect/.config/oat-helper/config.json").write_text(
    json.dumps({"always_show_firmware_tab": True, "simple_mode": False}))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402

# yes to "disconnect the mount", no to the flash confirmation itself, so the
# test never actually runs PlatformIO
answers = [QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.No]
warnings = []
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: answers.pop(0) if answers else QtWidgets.QMessageBox.No)
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: (warnings.append(str(a[2])[:60]), QtWidgets.QMessageBox.Ok)[1])

import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


pump(3.0)
print("mount connected before:", w.indi.mount_is_connected())
assert w.indi.mount_is_connected() is True

proceed = w._flash_precheck()
pump(0.5)
print("precheck returned:", proceed, "(declining the final confirm is expected)")
print("mount connected after precheck:", w.indi.mount_is_connected())
print("reconnect scheduled:", w.reconnect_after_flash)
print("warnings:", warnings)
assert w.indi.mount_is_connected() is False, "the driver should have released the port"
assert w.reconnect_after_flash is True, "a successful upload must reconnect the mount"
assert not warnings, "no warning expected on the happy path"

w._reconnect_mount_after_flash(True)
pump(8.0)
print("mount connected after reconnect:", w.indi.mount_is_connected())
assert w.indi.mount_is_connected() is True

# a failed upload leaves the mount alone and says so
w.reconnect_after_flash = True
w._reconnect_mount_after_flash(False)
pump(0.5)
last = w.log_box.toPlainText().splitlines()[-1]
print("after a failed upload:", last.split("] ", 1)[-1][:70])
assert "left disconnected" in last

shutdown_app(app, w, _server)
