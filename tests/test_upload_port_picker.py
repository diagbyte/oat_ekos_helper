"""Offer the serial port, stable names first, and know the real PlatformIO envs.

A rig on one powered hub has several USB serial devices - mount, focuser,
sometimes a GPS - and /dev/ttyUSB0 is assigned in enumeration order, which
changes with the order the hub brings them up. /dev/serial/by-id/ is derived
from the chip's VID:PID:serial and survives a reboot, so it is what should be
offered first. Typing a path by hand still has to work, because a device that
is not plugged in right now has no entry to pick.

The environment list was wrong in both directions: it offered mega2560 and
esp32dev, which are not environments in platformio.ini (esp32dev is a board;
the environment is esp32), and left out oaeboardv1 - the only Explorer
environment, and the one needed for the first flash of an OAE.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/portpick") if os.name == "nt" else Path("/tmp/portpick")
CFG = HOME / ".config" / "oat-helper"

shutil.rmtree(HOME, ignore_errors=True)
(CFG / "profiles" / "OpenAstroTracker").mkdir(parents=True, exist_ok=True)
(CFG / "profiles" / "OpenAstroTracker" / "config.json").write_text(
    json.dumps({"simple_mode": False, "always_show_firmware_tab": True,
                "firmware_port": "/dev/ttyACM0"}), encoding="utf-8")

# A fake /dev tree: two identical CH340 adapters that by-id cannot tell apart,
# a CP210x that it can, and the raw tty names underneath.
FAKE = HOME / "dev"
(FAKE / "serial" / "by-id").mkdir(parents=True, exist_ok=True)
(FAKE / "serial" / "by-path").mkdir(parents=True, exist_ok=True)
for name in ("usb-Silicon_Labs_CP2102_USB_to_UART_0001-if00-port0",
             "usb-1a86_USB_Serial-if00-port0"):
    (FAKE / "serial" / "by-id" / name).write_text("", encoding="utf-8")
# Real by-path names carry colons (platform-xhci-hcd.0-usb-0:1.2:1.0-port0);
# a Windows filename cannot, and only the directory matters here.
for name in ("platform-xhci-hcd.0-usb-0.1.2-port0",
             "platform-xhci-hcd.0-usb-0.1.3-port0"):
    (FAKE / "serial" / "by-path" / name).write_text("", encoding="utf-8")
for name in ("ttyUSB0", "ttyUSB1", "ttyACM0", "ttyAMA0"):
    (FAKE / name).write_text("", encoding="utf-8")

os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
                  LANG="en_US.UTF-8", FAKE_MOUNT_MODEL="OpenAstroTracker")
sys.path.insert(0, str(ROOT / "oat_helper"))
sys.path.insert(0, str(ROOT / "tests"))
from _harness import ensure_indi_server, shutdown_app        # noqa: E402
server = ensure_indi_server()
from PyQt5 import QtWidgets                                   # noqa: E402
import oat_helper                                             # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()
end = time.time() + 6
while time.time() < end:
    app.processEvents(); time.sleep(0.01)

failures = []

# --- 1. the PlatformIO environments are the ones that exist ---------------
# Checked against platformio.ini on OpenAstroTech/OpenAstroTracker-Firmware:
# ramps, mksgenlv21, mksgenlv2, mksgenlv1, esp32, oaeboardv1, rp2040_jackw01,
# rp2040_skr_pico, native.
envs = [w.fw_env.itemText(i) for i in range(w.fw_env.count())]
print("environments offered:", envs)
if "oaeboardv1" not in envs:
    failures.append("oaeboardv1 missing - the Explorer cannot be flashed from the built-in list")
for bogus in ("mega2560", "esp32dev"):
    if bogus in envs:
        failures.append(f"{bogus!r} is not an environment in platformio.ini")
if "mksgenlv21" not in envs:
    failures.append("mksgenlv21 missing")

# --- 2. the port field offers what is plugged in, stable names first -----
found = oat_helper.OATHelper._serial_port_candidates(str(FAKE))
print("ports discovered    :", len(found))
for entry in found:
    print("   ", entry)
if not found:
    failures.append("no ports discovered from the fake tree")
else:
    # The fake tree is a Windows path in this run, so compare on a normalised
    # separator rather than assuming "/".
    flat = [p.replace("\\", "/") for p in found]
    by_id = [p for p in flat if "/serial/by-id/" in p]
    raw = [p for p in flat if "/serial/" not in p]
    if not by_id:
        failures.append("by-id names must be offered - they survive a reboot")
    if by_id and raw and flat.index(by_id[0]) > flat.index(raw[0]):
        failures.append("stable by-id names must come before ttyUSB0 and friends")
    if len(by_id) != 2:
        failures.append(f"expected both by-id entries, got {by_id}")
    # by-path is the fallback when two CH340s share a by-id name; it says which
    # hub socket the device is in, so it must be offered too.
    if not [p for p in flat if "/serial/by-path/" in p]:
        failures.append("by-path names must be offered for adapters without a serial number")
if len(found) != len(set(found)):
    failures.append(f"duplicates in the list: {found}")

# --- 3. a saved port that is not plugged in is still the current value ----
print("current port        :", repr(w.fw_port.currentText()))
if w.fw_port.currentText().strip() != "/dev/ttyACM0":
    failures.append(f"the saved port must survive: {w.fw_port.currentText()!r}")
if not w.fw_port.isEditable():
    failures.append("typing a path must stay possible - an unplugged device has no entry")

# --- 4. a typed path reaches the flash and the saved config ---------------
w.fw_port.setCurrentText("/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0")
w.save_config()
saved = json.loads(oat_helper.CONFIG_FILE.read_text(encoding="utf-8"))
print("saved port          :", repr(saved.get("firmware_port")))
if saved.get("firmware_port") != "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0":
    failures.append(f"the chosen port must be saved: {saved.get('firmware_port')!r}")

if failures:
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print("upload port: stable names offered first, typing still allowed")

shutdown_app(app, w, server)
