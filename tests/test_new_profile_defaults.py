"""A new profile should start with defaults that suit the mount it is for.

Nothing is blank - load_config starts from the defaults and only overwrites
what the stored file holds - but the defaults were the Tracker's. Making a
profile for an Explorer and pressing BUILD therefore ran straight into the
build-target warning about oaeboardv1, every time, on a profile the helper had
just been told was for an Explorer. The defaults were arguing with the check.

The upload port is deliberately not guessed: it depends on which socket the
adapter is in, the port list shows what is actually present, and a wrong one
fails loudly at upload rather than quietly producing the wrong firmware.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path("C:/tmp/newdefaults") if os.name == "nt" else Path("/tmp/newdefaults")
CFG = HOME / ".config" / "oat-helper"

shutil.rmtree(HOME, ignore_errors=True)
start = CFG / "profiles" / "oat-pier"
start.mkdir(parents=True, exist_ok=True)
(start / "config.json").write_text(json.dumps({
    "simple_mode": False, "always_show_firmware_tab": True,
    "model": "OpenAstroTracker"}), encoding="utf-8")
(CFG / "active_profile").write_text("oat-pier", encoding="utf-8")

os.environ.update(HOME=str(HOME), USERPROFILE=str(HOME), QT_QPA_PLATFORM="offscreen",
                  LANG="en_US.UTF-8", FAKE_MOUNT_MODEL="OpenAstroTracker")
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

# --- 1. the seed follows the mount the profile is for --------------------
for model, wanted in (("OpenAstroExplorer", "oaeboardv1"),
                      ("OpenAstroTracker", "mksgenlv21"),
                      ("OpenAstroMount", "mksgenlv21")):
    seed = w.defaults_for_model(model)
    got = seed.get("firmware_env")
    ok = got == wanted
    print(f"{'ok  ' if ok else 'FAIL'} {model:<20} firmware_env = {got!r}")
    if not ok:
        failures.append(f"{model}: env was {got!r}, wanted {wanted!r}")
    if seed.get("model") != model:
        failures.append(f"{model}: the seed must record the model")

# --- 2. a fresh Explorer profile does not trip the build-target check ----
# The end of the chain, and the point of the whole thing: the warning exists to
# catch a real disagreement, not to greet every new profile.
seed = w.defaults_for_model("OpenAstroExplorer")
warnings = w._firmware_target_warnings(
    profile="OpenAstroExplorer", env=seed["firmware_env"], defines={"OAE": 1})
print()
print("warnings on a fresh Explorer profile:", len(warnings))
for line in warnings:
    print("   -", line[:110])
if warnings:
    failures.append("a new Explorer profile with an OAE configuration should build cleanly")

# --- 3. creating one writes the seed to disk -----------------------------
created = w.create_profile("oae-field", "OpenAstroExplorer")
print()
print("created:", created)
written = CFG / "profiles" / "oae-field" / "config.json"
if not written.exists():
    failures.append("the profile must be written")
else:
    stored = json.loads(written.read_text(encoding="utf-8"))
    print("stored :", {k: stored.get(k) for k in ("model", "firmware_env")})
    if stored.get("firmware_env") != "oaeboardv1":
        failures.append(f"the seed must reach the file: {stored.get('firmware_env')!r}")
    if stored.get("model") != "OpenAstroExplorer":
        failures.append("the model must reach the file")
    # Measured values belong to one mount and cannot be guessed for another.
    if "dec_home_offset_steps" in stored:
        failures.append("a new profile must not pretend to know a DEC Home travel")

if failures:
    print()
    for line in failures:
        print("FAIL:", line)
    raise AssertionError(f"{len(failures)} problem(s)")
print()
print("new profile: defaults follow the mount it was made for")

shutdown_app(app, w, server)
