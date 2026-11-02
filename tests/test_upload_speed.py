"""The upload speed has to be settable without editing platformio.ini.

Upstream flashes the ESP32 environments at 460800, which several CP210x and
CH340 adapters never sync at - esptool reports "Failed to connect to ESP32: No
serial data received", with the port open and the chip simply not answering.
115200 is the rate the AVR environments use and the usual fallback.

Editing platformio.ini would be undone by the helper's own "Update to newest"
and conflict on every source update, so it travels in the environment, beside
the flag that keeps warnings non-fatal. Left empty it is not set at all and
the environment's own speed stands.

This is the upload speed, not monitor_speed: 19200 is the rate INDI talks LX200
to the firmware at once it is running, and setting that here would be a
different mistake.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/uploadspeed") if os.name == "nt" else Path("/tmp/uploadspeed")
CFG = HOME / ".config" / "oat-helper"

shutil.rmtree(HOME, ignore_errors=True)
profile = CFG / "profiles" / "oae-test"
profile.mkdir(parents=True, exist_ok=True)
(profile / "config.json").write_text(json.dumps({
    "simple_mode": False, "always_show_firmware_tab": True,
    "model": "OpenAstroExplorer"}), encoding="utf-8")
(CFG / "active_profile").write_text("oae-test", encoding="utf-8")

os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
                  LANG="en_US.UTF-8", FAKE_MOUNT_MODEL="OpenAstroExplorer")
os.environ.pop("PLATFORMIO_UPLOAD_SPEED", None)
sys.path.insert(0, str(ROOT / "oat_helper"))
sys.path.insert(0, str(ROOT / "tests"))
from _harness import ensure_indi_server, shutdown_app        # noqa: E402
server = ensure_indi_server()
from PyQt5 import QtWidgets                                   # noqa: E402
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
import oat_helper                                             # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()
end = time.time() + 8
while time.time() < end:
    app.processEvents(); time.sleep(0.01)

failures = []

# --- 1. empty means "leave platformio.ini's own speed alone" -------------
w.fw_speed.setCurrentText("")
built = w._pio_env()
print("unset  ->", repr(built.get("PLATFORMIO_UPLOAD_SPEED")))
if "PLATFORMIO_UPLOAD_SPEED" in built:
    failures.append("an empty field must not set the variable at all")
if "-Wno-error" not in built.get("PLATFORMIO_BUILD_SRC_FLAGS", ""):
    failures.append("the warning flag must still be carried")

# --- 2. a value reaches the environment ----------------------------------
w.fw_speed.setCurrentText("115200")
built = w._pio_env()
print("115200 ->", repr(built.get("PLATFORMIO_UPLOAD_SPEED")))
if built.get("PLATFORMIO_UPLOAD_SPEED") != "115200":
    failures.append(f"the speed must reach the environment: {built.get('PLATFORMIO_UPLOAD_SPEED')!r}")

# --- 3. nonsense is refused rather than passed to esptool ---------------
for bad in ("abc", "-1", "0"):
    w.fw_speed.setCurrentText(bad)
    got = w._pio_env().get("PLATFORMIO_UPLOAD_SPEED")
    print(f"{bad!r:>8} ->", repr(got))
    if got is not None:
        failures.append(f"{bad!r} should not be passed on")

# --- 4. it actually lands in the subprocess ------------------------------
w.fw_speed.setCurrentText("115200")
probe = [sys.executable, "-c",
         "import os;print('SEEN=' + str(os.environ.get('PLATFORMIO_UPLOAD_SPEED')))"]
code, out = w._run_streamed(probe, timeout=60, env=w._pio_env())
print("subprocess  ->", out.strip().splitlines()[-1] if out.strip() else "(nothing)")
if code != 0 or "SEEN=115200" not in out:
    failures.append(f"the subprocess did not receive it: {out[:200]}")

# --- 5. it is remembered, per profile ------------------------------------
w.save_config()
stored = json.loads(oat_helper.CONFIG_FILE.read_text(encoding="utf-8"))
print("saved       ->", repr(stored.get("firmware_upload_speed")))
if str(stored.get("firmware_upload_speed")) != "115200":
    failures.append(f"the speed must be saved: {stored.get('firmware_upload_speed')!r}")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("upload speed: settable without touching platformio.ini")

shutdown_app(app, w, server)
