"""SET HOME writes the mount clock first; mid-session clock writes warn.

SET HOME stores the mount's sidereal time as the RA reference and zeroes the
tracking steps, so the clock is written right before :SHP#. A clock/site write
later re-bases the RA reference but keeps the tracking steps, so after tracking
it shifts every coordinate - the HA / clock buttons warn about that.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/clkhome"
shutil.rmtree("/tmp/clkhome", ignore_errors=True)
Path("/tmp/clkhome/.config").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

warnings, questions = [], []
reply = {"question": QtWidgets.QMessageBox.Yes}
QtWidgets.QMessageBox.warning = staticmethod(
    lambda *a, **k: (warnings.append(str(a[2])[:90]), QtWidgets.QMessageBox.No)[1])
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: (questions.append(str(a[2])[:300]), reply["question"])[1])

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
        if until and until():
            return True
    return False


def log_since(mark):
    return [l.split("] ", 1)[-1] for l in w.log_box.toPlainText().splitlines()[mark:]]


def set_home_and_wait():
    mark = len(w.log_box.toPlainText().splitlines())
    w.finish_dec_manual_home()
    pump(25.0, until=lambda: not w.set_home_busy and any(
        "SET HOME" in l for l in log_since(mark)))
    pump(0.5)
    return log_since(mark)


pump(3.0)
assert w.cfg.get("sync_clock_before_set_home", True) and w.clock_before_home_chk.isChecked()

# ---- 1. SET HOME fixes a clock that is hours out, then stores Home ----------
w.indi.meade("@Sg-127*00#")                          # 7 h out, as found in the field
before = w.mount_lst_drift_minutes()[0]
lines = set_home_and_wait()
after = w.mount_lst_drift_minutes()[0]
print(f"drift before {before:.0f} min -> after SET HOME {after:.2f} min")
print("  ", next(l for l in lines if "Mount clock written before SET HOME" in l)[:95])
assert before > 60 and after < oat_helper.CLOCK_OK_MINUTES, (before, after)
assert any(l.startswith("✓ SET HOME done") for l in lines), lines
assert not warnings, warnings                        # no "clock is wrong" dialog any more

# ---- 2. without the option, even 3 min is refused (was 5 min) ---------------
w.clock_before_home_chk.setChecked(False)
lon = w.site_angles()["lon"]
# The fake mount derives LST from its longitude: 45' too far west = 3 min of LST.
w.indi.meade("@Sg+126*14#")
print(f"mount now {w.mount_lst_drift_minutes()[0]:.1f} min out")
warnings.clear()
w.finish_dec_manual_home(); pump(1.5)
print("option off, 3 min out ->", "refused" if warnings else "accepted")
assert warnings and "3.0 min away" in warnings[0], warnings
w.clock_before_home_chk.setChecked(True)
pump(8.0, until=lambda: w.mount_lst_drift_minutes()[0] < 1)   # the refusal starts a clock repair

# ---- 3. a site that cannot be read aborts SET HOME instead of guessing ------
saved_geo = dict(w.indi.last_number_values.get("GEOGRAPHIC_COORD") or {})
w.indi.last_number_values["GEOGRAPHIC_COORD"] = {}
w.indi.meade("@MXr100#")
lines = set_home_and_wait()
w.indi.last_number_values["GEOGRAPHIC_COORD"] = saved_geo
print("site unknown ->", next(l for l in lines if l.startswith("SET HOME failed"))[:80])
assert any(l.startswith("SET HOME failed") and "site is unknown" in l for l in lines), lines
assert int(w._parse_gx(w.indi.meade(":GX#"))["ra_steps"]) == 100, "SET HOME must not run"

# ---- 4. HA / clock buttons after tracking warn, and can be cancelled --------
speed = float(w.indi.meade(":XGT#").rstrip("#"))
w.indi.meade(f"@XST{int(speed * 600)}#")             # 10 min of tracking since SET HOME
secs = None
w.run_async(w._tracked_seconds_since_home, lambda v: globals().__setitem__("secs", v)); pump(1.0)
print(f"tracked since SET HOME: {secs / 60:.1f} min")
assert abs(secs - 600) < 2, secs

questions.clear(); reply["question"] = QtWidgets.QMessageBox.No
w.indi.meade("@Sg+121*59#")                          # 5 deg off = 20 min: something to fix
mark = len(w.log_box.toPlainText().splitlines())
w.ha_sync_btn.click(); pump(3.0)
print("HA button after 10 min, answer No :", log_since(mark)[-1][:80])
assert questions and "10.0 min" in questions[0] and "2.50°" in questions[0], questions
assert w.mount_lst_drift_minutes()[0] > 15, "cancelled - the clock must not be written"

questions.clear(); reply["question"] = QtWidgets.QMessageBox.Yes
mark = len(w.log_box.toPlainText().splitlines())
w.clock_fix_btn.click(); pump(10.0, until=lambda: w.mount_lst_drift_minutes()[0] < 1)
assert questions and any("Solve & Sync" in l for l in log_since(mark)), log_since(mark)
print("clock button after 10 min, answer Yes: written, Solve & Sync advised")

# after SET HOME the tracking steps are zero again: no question
w.indi.meade("@XST0#")
questions.clear()
w.ha_sync_btn.click(); pump(6.0)
assert not questions, questions
print("HA button right after SET HOME: no warning")

status = oat_helper.OATHelper._host_clock_status()
print("host clock status:", status)
assert status[0] in (True, False, None) and isinstance(status[1], str)

print("clock before SET HOME OK")
shutdown_app(app, w, _server)
