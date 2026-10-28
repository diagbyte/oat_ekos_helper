"""Show which configuration is about to be flashed, on the page that flashes it.

Configuration_local.hpp was managed on one tab and flashed from another, and
its path appeared nowhere, so with a profile per mount there was no way to tell
what was about to be uploaded. The two are now one page, and it names the file.

Merging had to not take two things away. The old Firmware tab hid itself when
no serial device was present, while the config tab stayed - editing a
configuration over the network is useful and documented. And a profile mismatch
disabled the whole Firmware tab, which would now also block editing. Both are
now button-level: the page stays, and only building and uploading stop. That is
the more accurate rule anyway - flashing does not move the mount, the build
target check already warns about the wrong firmware, and editing the
configuration is part of sorting a mismatch out.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/fwmerge") if os.name == "nt" else Path("/tmp/fwmerge")
CFG = HOME / ".config" / "oat-helper"


def setup(profile="oae-field", model="OpenAstroExplorer", extra=None):
    shutil.rmtree(HOME, ignore_errors=True)
    d = CFG / "profiles" / profile
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"simple_mode": False, "always_show_firmware_tab": True, "model": model}
    cfg.update(extra or {})
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (d / "Configuration_local.hpp").write_text(
        "#define OAE\n#define RA_STEPPER_SPR 400\n", encoding="utf-8")
    (CFG / "active_profile").write_text(profile, encoding="utf-8")
    return d


def run(label, probe, product="OpenAstroExplorer", show_firmware="1"):
    env = dict(os.environ)
    env.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
               PYTHONIOENCODING="utf-8", LANG="en_US.UTF-8", FAKE_MOUNT_MODEL=product)
    body = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, r"{ROOT / 'oat_helper'}")
        sys.path.insert(0, r"{ROOT / 'tests'}")
        from _harness import ensure_indi_server, shutdown_app
        server = ensure_indi_server()
        from PyQt5 import QtWidgets
        QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
        import oat_helper
        app = QtWidgets.QApplication([])
        w = oat_helper.OATHelper(); w.show()
        end = time.time() + 12
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        TABS = [w.tabs.tabText(i) for i in range(w.tabs.count())]
{textwrap.indent(textwrap.dedent(probe), ' ' * 8)}
        shutdown_app(app, w, server)
    """)
    r = subprocess.run([sys.executable, "-c", body], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, timeout=180)
    if r.returncode != 0:
        print(r.stdout)
        print(r.stderr[-900:])
        raise AssertionError(f"{label}: child failed ({r.returncode})")
    return r.stdout


failures = []


def report(label, out, checks):
    values = dict(line.split(":", 1) for line in out.splitlines()
                  if ":" in line and line.split(":", 1)[0].isupper())
    print(f"--- {label}")
    for key, want in checks.items():
        got = values.get(key, "<missing>").strip()
        ok = (want in got) if isinstance(want, str) and want.startswith("~") is False and \
            not isinstance(want, bool) and key.endswith("_HAS") else got == str(want)
        print(f"    {'ok  ' if ok else 'FAIL'} {key} = {got!r}")
        if not ok:
            failures.append(f"{label}: {key} was {got!r}, wanted {want!r}")
    return values


PROBE = """
    print("TABS:", ",".join(TABS))
    print("CONFIG_SHOWN:", w.config_path_label.text().strip()[:150])
    print("BUILD_ENABLED:", bool(w.build_btn.isEnabled()))
    print("FLASH_ENABLED:", bool(w.flash_btn.isEnabled()))
    print("IMPORT_ENABLED:", bool(w.config_import_btn.isEnabled()))
    print("EDIT_ENABLED:", bool(w.config_edit_btn.isEnabled()))
    fw = [i for i in range(w.tabs.count()) if w.tabs.tabText(i) == "Firmware"]
    print("FW_TAB_ENABLED:", bool(fw) and w.tabs.isTabEnabled(fw[0]))
"""

# --- 1. one tab, and it names the file -----------------------------------
setup()
out = run("merged", PROBE)
values = report("the right mount, everything available", out, {
    "BUILD_ENABLED": True, "FLASH_ENABLED": True,
    "IMPORT_ENABLED": True, "EDIT_ENABLED": True, "FW_TAB_ENABLED": True})
tabs = values.get("TABS", "")
print(f"    tabs: {tabs.strip()}")
if "FW config" in tabs:
    failures.append("the separate FW config tab should be gone")
if "Firmware" not in tabs:
    failures.append("the Firmware tab must still be there")
shown = values.get("CONFIG_SHOWN", "")
print(f"    path: {shown.strip()[:120]}")
for part in ("oae-field", "Configuration_local.hpp"):
    if part not in shown:
        failures.append(f"the page must name the file it will flash: {part!r} missing")

# --- 2. a different mount than the profile describes ---------------------
# Editing and importing stay reachable; they are how the mix-up gets sorted.
setup()
out = run("mismatch", PROBE, product="OpenAstroTracker")
report("profile mismatch", out, {
    "BUILD_ENABLED": False, "FLASH_ENABLED": False,
    "IMPORT_ENABLED": True, "EDIT_ENABLED": True, "FW_TAB_ENABLED": True})

# --- 3. no serial device here --------------------------------------------
# The old Firmware tab vanished; the config tab did not. The merged page has to
# stay so a configuration can still be edited over the network.
setup(extra={"always_show_firmware_tab": False})
out = run("remote", PROBE)
values = report("no USB serial on this machine", out, {
    "IMPORT_ENABLED": True, "EDIT_ENABLED": True, "FW_TAB_ENABLED": True})
if "Firmware" not in values.get("TABS", ""):
    failures.append("the page must stay reachable without a serial device - "
                    "editing a configuration over the network is the documented case")
if values.get("FLASH_ENABLED", "").strip() != "False":
    failures.append("uploading needs the cable, so it must be off here")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("firmware page: one tab, and it says what it will flash")
