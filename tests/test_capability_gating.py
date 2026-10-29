"""Don't offer a control for hardware the firmware says is not there.

One Pi drives both mounts by swapping the board, so the helper shows the same
pages to very different hardware. :XGM# is the firmware's own answer to what it
was built with - AutoPA steppers, hall sensors, GPS, focuser - and it is
documented to grow new fields over time, so it is read as a set of tokens
rather than by position.

Two things it is not. It reports the BUILD, not the hardware: AZ_STEPPER_TYPE
and ALT_STEPPER_TYPE default to STEPPER_TYPE_NONE unless Configuration_local.hpp
sets them, so a mount whose motors are fitted still reports NO_AZ_ALT if the
build left them out - and in that state the firmware would not drive them
either, so hiding the page is right and the reason has to say where to look.
And it cannot be trusted before 1.13.21, where the reply is truncated at the
64-byte response buffer; not knowing is not the same as knowing there is
nothing, so nothing is gated there.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/capgate") if os.name == "nt" else Path("/tmp/capgate")
CFG = HOME / ".config" / "oat-helper"

MODERN = "V1.13.22"
OLD = "V1.13.20"
TRACKER = "Mega,NEMA|16|400,NEMA|16|400,NO_GPS,NO_AZ_ALT,NO_GYRO,NO_LCD,NO_FOC,NO_HSAH,NO_ENDSW"
LOADED = "ESP32,NEMA|20|400,NEMA|20|400,GPS,AUTO_AZ_ALT,GYRO,NO_LCD,FOC,HSAH,ENDSW_RA_DEC"


def run_case(label, caps, version, probe):
    """Start the app against a mount reporting `caps` and ask it `probe`."""
    shutil.rmtree(HOME, ignore_errors=True)
    (CFG / "profiles" / "OpenAstroTracker").mkdir(parents=True, exist_ok=True)
    (CFG / "profiles" / "OpenAstroTracker" / "config.json").write_text(
        json.dumps({"simple_mode": False, "always_show_firmware_tab": True}), encoding="utf-8")

    env = dict(os.environ)
    env.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
               PYTHONIOENCODING="utf-8", LANG="en_US.UTF-8",
               FAKE_MOUNT_MODEL="OpenAstroTracker", FAKE_MOUNT_CAPS=caps,
               FAKE_FIRMWARE_VERSION=version)
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
{textwrap.indent(textwrap.dedent(probe), ' ' * 8)}
        shutdown_app(app, w, server)
    """)
    result = subprocess.run([sys.executable, "-c", body], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=180)
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr[-900:])
        raise AssertionError(f"{label}: child failed ({result.returncode})")
    return result.stdout


PROBE = """
    caps = sorted(getattr(w, "mount_caps", set()))
    print("CAPS:", ",".join(caps) if caps else "(none)")
    print("AUTOHOME_ENABLED:", bool(w.ra_home_btn.isEnabled()))
    print("ONCONNECT_ENABLED:", bool(w.autohome_on_connect.isEnabled()))
    print("AUTOHOME_TIP:", w.ra_home_btn.toolTip().replace("\\n", " ")[:120])
    # The hall-sensor correction calibration measures where the sensor trips
    # relative to true home. With no sensor there is nothing to read.
    print("RA_CAL_ENABLED:", bool(w.ra_cal_group.isEnabled()))
    autopa = [i for i in range(w.tabs.count()) if w.tabs.tabText(i) == "AutoPA"]
    # The page stays open whatever the firmware says, so the reason can be
    # read; the groups that drive the motors carry the state.
    print("AUTOPA_TAB_ENABLED:", bool(autopa) and w.tabs.isTabEnabled(autopa[0]))
    print("AUTOPA_MOVES_ENABLED:", bool(w.pa_manual_group.isEnabled()))
    tip = (w.tabs.tabToolTip(autopa[0]) if autopa else "").split(".")[0]
    print("AUTOPA_TAB_TIP:", tip[:90])
"""

failures = []


def expect(label, out, **wanted):
    values = dict(line.split(":", 1) for line in out.splitlines() if ":" in line and line[0].isupper())
    print(f"--- {label}")
    for key, want in wanted.items():
        got = values.get(key, "<missing>").strip()
        ok = got == str(want)
        print(f"    {'ok  ' if ok else 'FAIL'} {key} = {got!r}")
        if not ok:
            failures.append(f"{label}: {key} was {got!r}, expected {want!r}")
    return values


# --- 1. a plain Tracker: no AutoPA motors, no hall sensor ----------------
out = run_case("tracker", TRACKER, MODERN, PROBE)
values = expect("plain Tracker on 1.13.22", out,
                AUTOHOME_ENABLED="False", ONCONNECT_ENABLED="False", AUTOPA_TAB_ENABLED="True",
                AUTOPA_MOVES_ENABLED="False", RA_CAL_ENABLED="False")
tip = values.get("AUTOHOME_TIP", "")
print(f"    tooltip: {tip.strip()[:110]}")
if "HSAH" not in tip:
    failures.append("the tooltip should name the token the firmware reported")
if "Configuration_local" not in tip and "firmware" not in tip.lower():
    failures.append("the tooltip should point at the firmware build, not just say 'unsupported'")

# --- 2. a fully equipped mount: nothing gated ----------------------------
out = run_case("loaded", LOADED, MODERN, PROBE)
expect("AutoPA + hall sensor on 1.13.22", out,
       AUTOHOME_ENABLED="True", ONCONNECT_ENABLED="True", AUTOPA_TAB_ENABLED="True",
       AUTOPA_MOVES_ENABLED="True", RA_CAL_ENABLED="True")

# --- 3. firmware too old to trust :XGM# ----------------------------------
# The same mount that reported nothing above must keep every control here:
# the reply is truncated on this firmware, so the helper does not know.
out = run_case("old", TRACKER, OLD, PROBE)
values = expect("same answer on 1.13.20", out,
                AUTOHOME_ENABLED="True", ONCONNECT_ENABLED="True", AUTOPA_TAB_ENABLED="True",
                AUTOPA_MOVES_ENABLED="True")
if values.get("CAPS", "").strip() != "(none)":
    failures.append("capabilities must not be recorded from a firmware that truncates them")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("capability gating: controls follow what the firmware says it was built with")
