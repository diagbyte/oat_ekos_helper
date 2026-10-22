"""The PlatformIO environment picks the board; a define picks the product.

MeadeCommandProcessor returns OpenAstroMount for OAM, OpenAstroExplorer for
OAE and OpenAstroTracker otherwise, so :GVP# follows a define in
Configuration_local.hpp - not the env. Build an OAE board with the oaeboardv1
env but no OAE define and you get a firmware that calls itself an
OpenAstroTracker and applies OAT travel limits to OAE hardware.

Nothing here touches the mount. That is deliberate: a board with no firmware on
it cannot answer :GVP#, and that is exactly when this check is useful - before
the first flash. It warns and lets the flash go ahead, because a board that
cannot identify itself is a board that still has to be flashed.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/fwtarget") if os.name == "nt" else Path("/tmp/fwtarget")
CFG = HOME / ".config" / "oat-helper"

import shutil
shutil.rmtree(HOME, ignore_errors=True)
(CFG / "profiles" / "OpenAstroExplorer").mkdir(parents=True, exist_ok=True)
(CFG / "active_profile").write_text("OpenAstroExplorer", encoding="utf-8")
(CFG / "profiles" / "OpenAstroExplorer" / "config.json").write_text(
    json.dumps({"simple_mode": False, "always_show_firmware_tab": True}), encoding="utf-8")

os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
                  LANG="en_US.UTF-8", FAKE_MOUNT_MODEL="OpenAstroExplorer")
sys.path.insert(0, str(ROOT / "oat_helper"))
sys.path.insert(0, str(ROOT / "tests"))
from _harness import ensure_indi_server, shutdown_app        # noqa: E402
server = ensure_indi_server()
from PyQt5 import QtWidgets                                   # noqa: E402
# The profile guard puts up a modal when the mount is not the loaded profile,
# and a modal in an offscreen test takes the process down with it.
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
import oat_helper                                             # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()
end = time.time() + 6
while time.time() < end:
    app.processEvents(); time.sleep(0.01)

assert oat_helper.PROFILE == "OpenAstroExplorer", oat_helper.PROFILE
failures = 0


def check(label, expect_warning, must_mention=()):
    global failures
    warnings = w._firmware_target_warnings()
    got = bool(warnings)
    ok = got == expect_warning
    missing = [m for m in must_mention if not any(m in x for x in warnings)]
    if missing:
        ok = False
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {len(warnings)} warning(s)")
    for line in warnings:
        print("        -", line[:118])
    if missing:
        print("        missing mention of:", missing)
    if not ok:
        failures += 1


# --- the first OAE flash, before anything is set up ----------------------
w.fw_env.clear(); w.fw_env.addItem("mksgenlv21"); w.fw_env.setCurrentText("mksgenlv21")
w.imported_defines = {}
check("OAE profile, AVR env, no config imported", True, ("oaeboardv1",))

# --- env fixed, define still missing ------------------------------------
w.fw_env.clear(); w.fw_env.addItem("oaeboardv1"); w.fw_env.setCurrentText("oaeboardv1")
w.imported_defines = {"RA_STEPPER_SPR": 400}
check("OAE profile, OAE env, no OAE define", True, ("OAE", "OpenAstroTracker"))

# --- both right ----------------------------------------------------------
w.imported_defines = {"RA_STEPPER_SPR": 400, "OAE": 1}
check("OAE profile, OAE env, OAE define", False)

# --- the define belongs to the other mount -------------------------------
w.imported_defines = {"OAM": 1}
check("OAE profile but OAM define", True, ("OAM",))

print()
# --- an OAT profile with an OAE env is the mirror image ------------------
# PROFILE is fixed at import, so check the comparison itself rather than
# restarting the app for the other direction.
mirror = w._firmware_target_warnings(profile="OpenAstroTracker", env="oaeboardv1",
                                     defines={"OAE": 1})
print("OAT profile, OAE env, OAE define ->", len(mirror), "warning(s)")
for line in mirror:
    print("        -", line[:118])
assert mirror, "an OAT profile flashing an OAE build must be called out"

clean = w._firmware_target_warnings(profile="OpenAstroTracker", env="mksgenlv21",
                                    defines={"RA_STEPPER_SPR": 400})
print("OAT profile, AVR env, no define   ->", len(clean), "warning(s)")
assert not clean, f"a plain OAT build is the default and must be quiet: {clean}"

# --- a board that cannot identify itself must still be flashable ---------
# The check never asks the mount anything, so it works with no firmware on the
# board at all; and it reports warnings rather than refusing.
w.imported_defines = {}
w.fw_env.setCurrentText("oaeboardv1")
asked = []


def say_yes(*args, **kwargs):
    asked.append(args[2] if len(args) > 2 else "")
    return QtWidgets.QMessageBox.Yes


QtWidgets.QMessageBox.question = staticmethod(say_yes)
proceed = w._firmware_target_precheck()
print()
print("unidentified board: precheck returned", proceed)
assert proceed is True, "saying yes to the warning must let the upload go ahead"
assert asked, "the warning must be offered, not silently skipped"

if failures:
    raise AssertionError(f"{failures} case(s) wrong")
print("firmware target check: env and product define are compared before building")

shutdown_app(app, w, server)
