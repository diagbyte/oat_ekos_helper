"""A profile is a named setup that says which mount model it is for.

Keying a profile by its model name allowed exactly one profile per model
forever: no second Tracker, and no way to keep a working configuration beside
the one being changed. The name now carries whatever the owner called it, and
the model it belongs to is a field inside, so :GVP# matches on the field.

Matching is a lookup, not a guess. One profile for the reported model is an
answer; two are a question, and the helper names them and changes nothing
rather than picking. A profile that predates the field falls back to its
directory name, which is where the model used to live.
"""
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/profnames") if os.name == "nt" else Path("/tmp/profnames")
CFG = HOME / ".config" / "oat-helper"


def make_profile(name, model=None, extra=None):
    d = CFG / "profiles" / name
    d.mkdir(parents=True, exist_ok=True)
    cfg = {"simple_mode": False, "always_show_firmware_tab": True}
    if model is not None:
        cfg["model"] = model
    cfg.update(extra or {})
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (d / "Configuration_local.hpp").write_text(f"// {name}\n", encoding="utf-8")
    return d


def run(label, active, probe, product="OpenAstroExplorer"):
    (CFG / "active_profile").write_text(active, encoding="utf-8")
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
        QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
        QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.No)
        import oat_helper
        app = QtWidgets.QApplication([])
        w = oat_helper.OATHelper(); w.show()
        end = time.time() + 12
        while time.time() < end:
            app.processEvents(); time.sleep(0.01)
        LOG = w.log_box.toPlainText()
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


def want(label, out, must=(), must_not=()):
    print(f"--- {label}")
    for line in out.splitlines():
        if line.startswith(("MODEL:", "BLOCKED:", "SUGGEST:")):
            print("   ", line[:130])
    for text in must:
        if text not in out:
            failures.append(f"{label}: expected {text!r}")
            print(f"    FAIL expected {text!r}")
    for text in must_not:
        if text in out:
            failures.append(f"{label}: did not expect {text!r}")
            print(f"    FAIL did not expect {text!r}")


PROBE = """
    print("MODEL:", w.profile_model())
    # A mismatch blocks the controls that replay a stored distance, not the
    # pages - the only STOP in the helper lives on the Home page.
    print("BLOCKED:", "replay" if not w.dec_restore_btn.isEnabled() else "(none)")
    print("STOP:", "usable" if w.stop_all_btn.isEnabled() else "UNREACHABLE")
    # The advice sits at the end of a long line, so search the whole log rather
    # than printing a truncated copy of it.
    for name in ("oae-field", "oae-test", "oat-pier"):
        if name in LOG:
            print("SUGGEST:", name)
    tail = [l for l in LOG.splitlines() if "Select" in l or "These profiles" in l or "No profile" in l]
    for line in tail[:2]:
        print("SUGGEST: advice ->", line.split(". ")[-1][:110])
"""

# --- 1. a named profile with a model field matches ------------------------
shutil.rmtree(HOME, ignore_errors=True)
make_profile("oae-field", "OpenAstroExplorer")
make_profile("oat-pier", "OpenAstroTracker")
out = run("named match", "oae-field", PROBE)
want("a name of your own, model in a field", out,
     must=["MODEL: OpenAstroExplorer", "BLOCKED: (none)"])

# --- 2. wrong profile active, exactly one other fits ---------------------
out = run("one candidate", "oat-pier", PROBE)
want("active profile is for the other mount, one candidate", out,
     must=["oae-field"])
if "BLOCKED: (none)" in out:
    failures.append("one candidate: replaying the stored travel must stay blocked until the switch")
if "STOP: UNREACHABLE" in out:
    failures.append("one candidate: STOP must stay reachable")

# --- 3. two profiles fit: name them, change nothing ----------------------
make_profile("oae-test", "OpenAstroExplorer")
out = run("two candidates", "oat-pier", PROBE)
want("two candidates", out, must=["oae-field", "oae-test"])
if "BLOCKED: (none)" in out:
    failures.append("two candidates: replaying the stored travel must stay blocked")
if "STOP: UNREACHABLE" in out:
    failures.append("two candidates: STOP must stay reachable")

# --- 4. a profile from before the field falls back to its name -----------
shutil.rmtree(HOME, ignore_errors=True)
make_profile("OpenAstroExplorer", model=None)
out = run("legacy", "OpenAstroExplorer", PROBE)
want("directory named after the model, no field", out,
     must=["MODEL: OpenAstroExplorer", "BLOCKED: (none)"])

# --- 5. cloning carries the setup, under a new name ----------------------
shutil.rmtree(HOME, ignore_errors=True)
make_profile("oae-field", "OpenAstroExplorer",
             extra={"dec_home_offset_steps": 9426, "firmware_env": "oaeboardv1"})
out = run("clone", "oae-field", """
    ok = w.clone_profile("oae-test")
    print("CLONED:", ok)
""")
clone = CFG / "profiles" / "oae-test"
print("--- clone")
print("    exists:", clone.is_dir())
if not clone.is_dir():
    failures.append("clone: the new profile directory must exist")
else:
    cfg = json.loads((clone / "config.json").read_text(encoding="utf-8"))
    print("    model:", cfg.get("model"), "| dec_home:", cfg.get("dec_home_offset_steps"),
          "| env:", cfg.get("firmware_env"))
    # Same mount, so its measured travel comes along - unlike a NEW profile,
    # which starts from defaults because that is a different mount.
    if cfg.get("dec_home_offset_steps") != 9426:
        failures.append("clone: the same mount's DEC Home travel should come with it")
    if cfg.get("model") != "OpenAstroExplorer":
        failures.append("clone: the model must come with it")
    if not (clone / "Configuration_local.hpp").exists():
        failures.append("clone: Configuration_local.hpp must come with it")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("profiles: your name outside, the model inside")
