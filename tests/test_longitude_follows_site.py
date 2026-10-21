"""The remembered longitude encoding must follow the site, not freeze it.

_calibrate_longitude() works out which :Sg form a build understands and the
result was stored as the whole command - "@Sg+126*59#" - with the site's own
degrees baked into it. write_mount_clock() then replayed that string verbatim,
so after moving to a different site the mount was told the *old* longitude while
every other part of the clock used the new one. The mount's sidereal time is
derived from longitude, so the result is a GOTO that points somewhere else
entirely, and SET HOME refusing because the clock is out.

What is worth remembering is the encoding, not the coordinates: the command has
to be rebuilt from whatever longitude the site currently reports.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/lon_site"
os.environ["LANG"] = "en_US.UTF-8"
os.environ["FAKE_LEGACY_LONGITUDE"] = "1"      # a build that needs the probe at all
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/lon_site", ignore_errors=True)
Path("/tmp/lon_site/.config/oat-helper").mkdir(parents=True, exist_ok=True)

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def set_site(lat, lon_east):
    """Replace what Ekos reports, the way a site change would."""
    w.indi.last_number_values["GEOGRAPHIC_COORD"] = {
        "LAT": lat, "LONG": lon_east % 360.0}


pump(3.0)

# --- site A: let the helper work out the encoding this build wants ---------
SITE_A = 126.978          # Seoul
set_site(37.5665, SITE_A)
w.repair_mount_clock()
pump(12.0)
drift_a = w.mount_lst_drift_minutes()
print(f"site A  lon {SITE_A:+.3f}  drift {drift_a[0]:.2f} min")
assert drift_a[0] < 5.0, f"the clock repair should work at site A, got {drift_a[0]:.1f} min"

# --- site B: a genuinely different longitude ------------------------------
# 30 degrees west of site A is two hours of sidereal time: replaying site A's
# baked-in command instead of writing site B leaves exactly that error.
SITE_B = SITE_A - 30.0
set_site(37.5665, SITE_B)
lon = w.site_angles()["lon"]
assert abs(lon - SITE_B) < 0.01, f"the site should now read {SITE_B}, got {lon}"

offset = 9.0
w.write_mount_clock(37.5665, lon, w._computed_lst_hours(lon), offset)
pump(2.0)

drift_b = w.mount_lst_drift_minutes()
print(f"site B  lon {SITE_B:+.3f}  drift {drift_b[0]:.2f} min")
print("  mount longitude now:", w.indi.meade(":Gg#").strip())
assert drift_b[0] < 5.0, (
    f"the clock must be written for the current site, not the one the encoding "
    f"was learned at - {drift_b[0]:.0f} min out")

print("the remembered encoding follows the site")
shutdown_app(app, w, _server)
