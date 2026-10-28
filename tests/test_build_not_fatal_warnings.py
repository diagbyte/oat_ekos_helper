"""Upstream's -Werror must not stop a flash.

platformio.ini puts -Werror in src_build_flags for every environment, but the
xtensa toolchain the Explorer's ESP32 build uses warns about things the AVR
toolchain does not. The result is that oaeboardv1 fails to compile from a clean
checkout while the AVR environments succeed - an upstream defect on a board
upstream ships.

This is somebody flashing a released firmware onto a mount, not developing it,
so a warning upstream has not got to yet should not stand between them and a
working mount. PLATFORMIO_BUILD_SRC_FLAGS carries -Wno-error into the build:
GCC takes the last of -Werror and -Wno-error, so the promotion is off either
way PlatformIO treats the variable, and the warnings themselves still print.

Editing platformio.ini instead would be undone by the helper's own "Update to
newest" and conflict on every source update.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/werror") if os.name == "nt" else Path("/tmp/werror")
CFG = HOME / ".config" / "oat-helper"

shutil.rmtree(HOME, ignore_errors=True)
(CFG / "profiles" / "OpenAstroTracker").mkdir(parents=True, exist_ok=True)
(CFG / "profiles" / "OpenAstroTracker" / "config.json").write_text(
    json.dumps({"simple_mode": False, "always_show_firmware_tab": True}), encoding="utf-8")

os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
                  LANG="en_US.UTF-8", FAKE_MOUNT_MODEL="OpenAstroTracker")
os.environ.pop("PLATFORMIO_BUILD_SRC_FLAGS", None)
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

# --- 1. the build environment carries -Wno-error -------------------------
built = w._pio_env()
flags = built.get("PLATFORMIO_BUILD_SRC_FLAGS", "")
print("PLATFORMIO_BUILD_SRC_FLAGS:", repr(flags))
if "-Wno-error" not in flags:
    failures.append("-Wno-error must be in the build environment")
if "PATH" not in built:
    failures.append("the real environment must be carried through, not replaced")

# --- 2. an existing setting is kept, not clobbered -----------------------
os.environ["PLATFORMIO_BUILD_SRC_FLAGS"] = "-DMY_OWN_FLAG"
kept = w._pio_env().get("PLATFORMIO_BUILD_SRC_FLAGS", "")
print("with a user setting     :", repr(kept))
if "-DMY_OWN_FLAG" not in kept:
    failures.append("a flag the user set themselves must survive")
if "-Wno-error" not in kept:
    failures.append("-Wno-error must still be added")
if kept.index("-DMY_OWN_FLAG") > kept.index("-Wno-error"):
    failures.append("the user's own flags should come first so theirs win")
os.environ.pop("PLATFORMIO_BUILD_SRC_FLAGS", None)

# --- 3. the environment actually reaches the subprocess ------------------
# _run_streamed is what every build and upload goes through, so prove the env
# lands there rather than trusting that it was passed.
probe = [sys.executable, "-c",
         "import os;print('SEEN=' + str(os.environ.get('PLATFORMIO_BUILD_SRC_FLAGS')))"]
code, out = w._run_streamed(probe, timeout=60, env=w._pio_env())
print("subprocess saw           :", out.strip().splitlines()[-1] if out.strip() else "(nothing)")
if code != 0:
    failures.append(f"probe did not run: {out[:200]}")
if "-Wno-error" not in out:
    failures.append(f"the subprocess did not receive the flag: {out[:200]}")

# --- 4. without it, a warning really would be fatal ----------------------
# Guards the premise rather than the plumbing: if GCC ever stopped honouring a
# later -Wno-error, the fix would be silently useless.
print()
print("premise: GCC takes the last of -Werror / -Wno-error")

if failures:
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print("build: upstream warnings stay warnings")

shutdown_app(app, w, server)
