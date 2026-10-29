"""A profile that has not said which mount it is for is not the wrong profile.

"New profile..." made a directory and nothing else, so `model` was empty and
every reported model disagreed with it. That is not a mismatch - it is a
profile that has not been labelled yet - but it was treated as one, and the
pages that move the mount were disabled on a correctly set up machine, for
good. A profile with no model adopts the one the mount reports instead.

And when a mismatch IS real, it must not take STOP away. The only emergency
stop in the helper sits on the Home page; disabling that page while the mount
is slewing leaves nothing to press. The block now covers the controls that
replay stored per-mount numbers - the DEC Home travel, the shutdown angle -
which is what the guard was ever about, and leaves the mount reachable.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/stopguard") if os.name == "nt" else Path("/tmp/stopguard")
CFG = HOME / ".config" / "oat-helper"


def make(name, cfg_extra):
    shutil.rmtree(HOME, ignore_errors=True)
    d = CFG / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"simple_mode": False, "always_show_firmware_tab": True}
    cfg.update(cfg_extra)
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (CFG / "active_profile").write_text(name, encoding="utf-8")
    return d


def run(label, probe, product):
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


PROBE = """
    blocked = [w.tabs.tabText(i) for i in range(w.tabs.count()) if not w.tabs.isTabEnabled(i)]
    print("BLOCKED_TABS:", ",".join(blocked) if blocked else "(none)")
    print("MISMATCH:", bool(w.profile_mismatch))
    print("PROFILE_MODEL:", repr(w.profile_model()))
    print("STOP_ENABLED:", bool(w.stop_all_btn.isEnabled()))
    print("GOTO_HOME_ENABLED:", bool(w.dec_manual_gohome_btn.isEnabled()))
    print("RESTORE_DEC_ENABLED:", bool(w.dec_restore_btn.isEnabled()))
"""

failures = []


def check(label, out, wanted):
    values = dict(line.split(":", 1) for line in out.splitlines()
                  if ":" in line and line.split(":", 1)[0].isupper())
    print(f"--- {label}")
    for key, want in wanted.items():
        got = values.get(key, "<missing>").strip()
        ok = got == str(want)
        print(f"    {'ok  ' if ok else 'FAIL'} {key} = {got!r}")
        if not ok:
            failures.append(f"{label}: {key} was {got!r}, wanted {want!r}")
    return values


# --- 1. a profile made by "New profile..." has no model ------------------
# It is unlabelled, not wrong. Everything must stay usable, and the profile
# should take on the model the mount reports.
make("oae-field", {})
out = run("unlabelled", PROBE, "OpenAstroExplorer")
check("new profile, no model yet", out, {
    "MISMATCH": False, "BLOCKED_TABS": "(none)",
    "PROFILE_MODEL": "'OpenAstroExplorer'",
    "STOP_ENABLED": True, "GOTO_HOME_ENABLED": True})

saved = json.loads((CFG / "profiles" / "oae-field" / "config.json").read_text(encoding="utf-8"))
print("    stored model:", repr(saved.get("model")))
if saved.get("model") != "OpenAstroExplorer":
    failures.append("the adopted model should be written down, not re-derived every start")

# --- 2. a real mismatch still has to leave the mount reachable -----------
# STOP is the only emergency stop there is, and it lives on the Home page.
make("oat-pier", {"model": "OpenAstroTracker", "dec_home_offset_steps": 9426})
out = run("mismatch", PROBE, "OpenAstroExplorer")
check("the other mount is plugged in", out, {
    "MISMATCH": True,
    "STOP_ENABLED": True,
    "GOTO_HOME_ENABLED": True,
    "RESTORE_DEC_ENABLED": False})
values = dict(line.split(":", 1) for line in out.splitlines()
              if ":" in line and line.split(":", 1)[0].isupper())
blocked = values.get("BLOCKED_TABS", "").strip()
print(f"    blocked tabs: {blocked}")
if "Home" in blocked:
    failures.append("blocking the Home page hides the only STOP button there is")
if "AutoPA" in blocked:
    failures.append("the AutoPA page should stay reachable; its moves are solved, not replayed")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("mismatch: stored travel is blocked, the mount stays reachable")
