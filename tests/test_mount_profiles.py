"""Two mounts on one computer must not share stored state.

OAT and OAE run the same firmware through the same INDI driver, so the helper
works with either - but almost everything it remembers is about one specific
mount: the DEC Home travel, the shutdown angle, the travel limits, which
PlatformIO environment and port to flash, which :Sg encoding the build wants.
Running both from one config silently carries one mount's numbers onto the
other, and a DEC Home offset from the wrong mount is a move into the hardware.

So the whole config lives under profiles/<model>/, keyed by what :GVP# reports,
and `active_profile` is the only thing outside. Nothing is shared: a shared
section is a decision about which keys can never diverge, and that decision is
wrong the moment one of them does.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("/tmp/profiles_home") if os.name != "nt" else Path("C:/tmp/profiles_home")
CFG = HOME / ".config" / "oat-helper"


def run_child(script, model="OpenAstroTracker"):
    """Import the helper in a fresh process - the paths resolve at import."""
    env = dict(os.environ)
    env.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
               PYTHONIOENCODING="utf-8", LANG="en_US.UTF-8", FAKE_MOUNT_MODEL=model)
    body = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, r"{ROOT / 'oat_helper'}")
        import oat_helper
        {textwrap.indent(textwrap.dedent(script), '        ').strip()}
    """)
    result = subprocess.run([sys.executable, "-c", body], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=120)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr[-800:])
        raise AssertionError(f"child failed ({result.returncode})")
    return result.stdout.strip()


# --- 1. an existing flat config migrates into a profile -------------------
shutil.rmtree(HOME, ignore_errors=True)
CFG.mkdir(parents=True, exist_ok=True)
(CFG / "config.json").write_text(json.dumps({
    "dec_home_offset_steps": 9426, "release_dec_deg": -33.0,
    "firmware_env": "mksgenlv21", "magnetic_declination_deg": -8.0}), encoding="utf-8")
(CFG / "Configuration_local.hpp").write_text(
    "#define RA_STEPPER_SPR 400\n", encoding="utf-8")
(CFG / "backups").mkdir(exist_ok=True)
(CFG / "backups" / "old.hpp").write_text("// kept\n", encoding="utf-8")

out = run_child("""
    print("profile:", oat_helper.PROFILE)
    print("config :", oat_helper.CONFIG_FILE)
    print("hpp    :", oat_helper.FIRMWARE_CONFIG)
""")
print(out)

moved = CFG / "profiles" / "OpenAstroTracker"
assert (moved / "config.json").exists(), "the flat config must move into a profile"
assert (moved / "Configuration_local.hpp").exists(), "Configuration_local.hpp must move too"
assert (moved / "backups" / "old.hpp").exists(), "backups must move with it"
kept = json.loads((moved / "config.json").read_text(encoding="utf-8"))
assert kept["dec_home_offset_steps"] == 9426, kept
assert kept["release_dec_deg"] == -33.0, kept
assert not (CFG / "config.json").exists(), "the flat config should not be left behind"
print("migration: existing settings preserved under the default profile")

# --- 2. a second profile starts from defaults, not from the first ---------
(CFG / "active_profile").write_text("OpenAstroExplorer", encoding="utf-8")
out = run_child("""
    cfg = oat_helper.OATHelper.load_config(None)
    print("dec_home_offset_steps:", cfg.get("dec_home_offset_steps"))
    print("release_dec_deg      :", cfg.get("release_dec_deg"))
    print("firmware_env         :", cfg.get("firmware_env"))
""", model="OpenAstroExplorer")
print(out)
assert "dec_home_offset_steps: None" in out, (
    "a new profile must not inherit the other mount's DEC Home travel - replaying "
    "it would drive DEC by a distance measured on different hardware")
assert "release_dec_deg      : -30.0" in out, "a new profile should hold the defaults"
print("isolation: a new profile starts clean")

# --- 3. the two profiles keep separate files ------------------------------
oae = CFG / "profiles" / "OpenAstroExplorer"
oae.mkdir(parents=True, exist_ok=True)
(oae / "Configuration_local.hpp").write_text("#define OAE\n", encoding="utf-8")
oat_hpp = (moved / "Configuration_local.hpp").read_text(encoding="utf-8")
oae_hpp = (oae / "Configuration_local.hpp").read_text(encoding="utf-8")
print("OAT hpp:", oat_hpp.strip(), "| OAE hpp:", oae_hpp.strip())
assert "OAE" not in oat_hpp, "flashing the OAT with the OAE config is the failure this prevents"
assert "OAE" in oae_hpp
print("isolation: Configuration_local.hpp is per profile")

# --- 4. connecting the wrong mount for the loaded profile is blocked -------
# The profile is chosen at startup, so plugging in the other mount leaves the
# settings describing hardware that is not there. Commanding anything then is
# how one mount's DEC Home travel gets driven on the other.
import time as _time

os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
                  LANG="en_US.UTF-8", FAKE_MOUNT_MODEL="OpenAstroExplorer")
(CFG / "active_profile").write_text("OpenAstroTracker", encoding="utf-8")
# The Firmware page hides itself where there is no serial device, and this
# machine has none; show it so the block can be checked.
_oat_cfg = CFG / "profiles" / "OpenAstroTracker" / "config.json"
_kept = json.loads(_oat_cfg.read_text(encoding="utf-8"))
_kept["always_show_firmware_tab"] = True
_oat_cfg.write_text(json.dumps(_kept), encoding="utf-8")

sys.path.insert(0, str(ROOT / "oat_helper"))
sys.path.insert(0, str(ROOT / "tests"))
from _harness import ensure_indi_server, shutdown_app        # noqa: E402
_server = ensure_indi_server()
from PyQt5 import QtWidgets                                   # noqa: E402
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
import oat_helper                                             # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()
end = _time.time() + 12
while _time.time() < end:
    app.processEvents(); _time.sleep(0.01)

# Advanced mode rebuilds the tab widget, so check the block survives that too -
# otherwise switching views hands back the pages it just disabled.
w.advanced_toggle.setChecked(True)
end = _time.time() + 2
while _time.time() < end:
    app.processEvents(); _time.sleep(0.01)

log = w.log_box.toPlainText()
warned = [l for l in log.splitlines() if "OpenAstroExplorer" in l and "OpenAstroTracker" in l]
blocked = [w.tabs.tabText(i) for i in range(w.tabs.count()) if not w.tabs.isTabEnabled(i)]
print("profile loaded:", oat_helper.PROFILE, "| mount reports: OpenAstroExplorer")
print("mismatch reported:", bool(warned))
print("tabs disabled    :", blocked)
assert warned, "a mount that is not the loaded profile must be called out"
# The check runs on a startup timer and again on the connection hook, so one
# power-up reaches it twice. A repeat is not news - and for a mismatch it would
# mean a second modal dialog over the first.
assert len(warned) == 1, f"the mismatch should be reported once, got {len(warned)}"
for name in ("Home", "AutoPA", "Firmware"):
    assert name in blocked, f"{name} must be blocked on a profile mismatch, got {blocked}"
print("guard: the wrong mount cannot be commanded")

shutdown_app(app, w, _server)
print("mount profiles keep the two mounts apart")
