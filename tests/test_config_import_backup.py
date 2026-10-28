"""Replacing a configuration keeps the one it replaced.

Configuration_local.hpp belongs to the active profile, so importing a file
while the wrong profile is loaded overwrites that mount's configuration with
another mount's. Editing already took a copy first; importing - which replaces
the whole file, and is the easier mistake to make - did not, and neither did
restoring, which can be pointed at the wrong backup.

The startup load reads the file in place and must not copy anything, or every
launch would leave another backup behind.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/cfgbackup") if os.name == "nt" else Path("/tmp/cfgbackup")
CFG = HOME / ".config" / "oat-helper"
PROFILE = CFG / "profiles" / "oat-pier"

shutil.rmtree(HOME, ignore_errors=True)
PROFILE.mkdir(parents=True, exist_ok=True)
(PROFILE / "config.json").write_text(json.dumps({
    "simple_mode": False, "always_show_firmware_tab": True,
    "model": "OpenAstroTracker"}), encoding="utf-8")
TRACKER = "// the Tracker's own settings\n#define RA_STEPPER_SPR 400\n"
(PROFILE / "Configuration_local.hpp").write_text(TRACKER, encoding="utf-8")
(CFG / "active_profile").write_text("oat-pier", encoding="utf-8")

# The file someone would import while the wrong profile is loaded.
INCOMING = HOME / "from-the-explorer.hpp"
INCOMING.write_text("#define OAE\n#define RA_STEPPER_SPR 200\n", encoding="utf-8")

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
end = time.time() + 8
while time.time() < end:
    app.processEvents(); time.sleep(0.01)

BACKUPS = PROFILE / "backups"
failures = []


def backups():
    return sorted(BACKUPS.glob("*.hpp")) if BACKUPS.is_dir() else []


# --- 1. starting up must not leave a backup behind -----------------------
# The app reads the file in place at launch; copying there would add one on
# every single start.
print("backups after startup :", len(backups()))
if backups():
    failures.append(f"startup should leave no backup, found {[b.name for b in backups()]}")

# --- 2. importing over a configuration keeps the old one -----------------
w._load_configuration_file(INCOMING)
after = backups()
print("backups after import  :", [b.name for b in after])
if len(after) != 1:
    failures.append(f"importing must take a copy first, found {len(after)}")
else:
    kept = after[0].read_text(encoding="utf-8")
    print("   kept content       :", kept.splitlines()[0] if kept else "(empty)")
    if kept != TRACKER:
        failures.append("the backup must hold what was there BEFORE the import")
live = (PROFILE / "Configuration_local.hpp").read_text(encoding="utf-8")
if "OAE" not in live:
    failures.append("the import must still happen")

# --- 3. restoring also keeps what it replaces ----------------------------
# Pointing Restore at the wrong backup is the same mistake in reverse.
time.sleep(1.1)        # the backup name carries a timestamp in seconds
before_restore = len(backups())
w._restore_configuration_from(after[0]) if hasattr(w, "_restore_configuration_from") else None
if hasattr(w, "_restore_configuration_from"):
    now = backups()
    print("backups after restore :", len(now))
    if len(now) != before_restore + 1:
        failures.append("restoring must keep the configuration it replaces")
    live = (PROFILE / "Configuration_local.hpp").read_text(encoding="utf-8")
    if live != TRACKER:
        failures.append("restoring must put the chosen backup in place")
else:
    failures.append("restore should go through one shared path that takes a copy")

# --- 4. the build warning says what to do about it -----------------------
warnings = w._firmware_target_warnings(profile="OpenAstroTracker", env="oaeboardv1",
                                       defines={"OAE": 1})
print()
print("build-target warnings :", len(warnings))
for line in warnings:
    print("   -", line[:116])
joined = " ".join(warnings)
for hint in ("Settings", "profile"):
    if hint not in joined:
        failures.append(f"the warning should say what to do: {hint!r} missing")
if "import" not in joined.lower():
    failures.append("the warning should say the file belongs to the profile, "
                    "so importing it here replaces the other mount's")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("configuration: nothing is replaced without keeping a copy")

# Each configuration load refreshes the inspector, which asks the mount for its
# motor settings in a worker. Let those finish while the socket is still up;
# tearing it down under them aborts the process at interpreter shutdown.
end = time.time() + 3
while time.time() < end:
    app.processEvents(); time.sleep(0.01)
shutdown_app(app, w, server)
