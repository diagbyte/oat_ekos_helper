"""The loaded profile has to be visible without going looking for it.

Every stored setting belongs to one mount, but nothing in the main window said
which mount that was - the name sat in a combo at the far right of a panel that
is collapsed by default. The mismatch warning then pointed at a control that
could not be found, and there was no way to tell at a glance whether the
Tracker's settings or the Explorer's were loaded.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/badge") if os.name == "nt" else Path("/tmp/badge")
CFG = HOME / ".config" / "oat-helper"

shutil.rmtree(HOME, ignore_errors=True)
for name, model in (("oat-pier", "OpenAstroTracker"), ("oae-field", "OpenAstroExplorer")):
    d = CFG / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps({
        "simple_mode": False, "model": model}), encoding="utf-8")
(CFG / "active_profile").write_text("oae-field", encoding="utf-8")

os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
                  LANG="en_US.UTF-8", FAKE_MOUNT_MODEL="OpenAstroExplorer")
sys.path.insert(0, str(ROOT / "oat_helper"))
sys.path.insert(0, str(ROOT / "tests"))
from _harness import ensure_indi_server, shutdown_app        # noqa: E402
server = ensure_indi_server()
from PyQt5 import QtWidgets                                   # noqa: E402
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
import oat_helper                                             # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()
end = time.time() + 8
while time.time() < end:
    app.processEvents(); time.sleep(0.01)

failures = []

# --- 1. the header names the profile and its mount -----------------------
label = w.profile_btn.text()
print("header badge      :", repr(label))
for part in ("oae-field", "OpenAstroExplorer"):
    if part not in label:
        failures.append(f"the badge must show {part!r}")
if not w.profile_btn.isVisible():
    failures.append("the badge must be visible without opening anything")

# --- 2. its menu offers every profile, and making one --------------------
w._fill_profile_menu()
entries = [a.text() for a in w.profile_menu.actions() if a.text()]
print("menu              :", entries)
for part in ("oat-pier", "oae-field"):
    if not any(part in e for e in entries):
        failures.append(f"the menu must list {part!r}")
for part in ("New profile", "Copy this profile"):
    if not any(part in e for e in entries):
        failures.append(f"the menu must offer {part!r}")
checked = [a.text() for a in w.profile_menu.actions() if a.isCheckable() and a.isChecked()]
print("marked as current :", checked)
if len(checked) != 1 or "oae-field" not in checked[0]:
    failures.append(f"exactly the loaded profile should be ticked, got {checked}")

# --- 3. picking another one records it for the next start ----------------
w.select_profile("oat-pier")
recorded = (CFG / "active_profile").read_text(encoding="utf-8").strip()
print("active_profile    :", repr(recorded))
if recorded != "oat-pier":
    failures.append(f"choosing a profile must be recorded, got {recorded!r}")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("profile badge: the loaded profile is on the header, with its mount")

shutdown_app(app, w, server)
