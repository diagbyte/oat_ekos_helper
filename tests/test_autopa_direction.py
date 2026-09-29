"""AutoPA must drive each axis against the error, on both axes.

KStars documents the PAA error signs as correction directions
(kstars/ekos/align/polaralign.h): "positive altitude error: reduce altitude,
positive azimuth error: point telescope more to the left". The value logged as
"PAA Refresh(n): Corrected az/alt" is the *remaining* error
(polaralignmentassistant.cpp passes processRefreshCoords()'s azE/altE straight
into the log string), so the correction is -error on BOTH axes and any
physical motor reversal belongs in ALT_INVERT_DIR / AZ_INVERT_DIR in the
firmware's Configuration_local.hpp.

0.6.0 removed the "Invert AZ correction" checkbox but kept the un-inverted AZ
default from the first commit, so AZ was driven *away* from the pole every
cycle: the axis walked one way until the run was stopped by hand.
"""
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/autopa_dir"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/autopa_dir", ignore_errors=True)
logdir = Path("/tmp/autopa_dir/.local/share/kstars/logs/2026-09-18")
logdir.mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_dir/.config").mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_dir/.config/oat-helper").mkdir(parents=True, exist_ok=True)
# Pin the source: with "auto", a dev box running KStars with the Align
# module open would take the D-Bus path and never read these fixtures.
Path("/tmp/autopa_dir/.config/oat-helper/config.json").write_text(
    '{"paa_source": "logfile"}', encoding="utf-8")
Path("/tmp/autopa_dir/.config/kstarsrc").write_text(
    "[General]\nLogToFile=true\nLogToDefault=false\n", encoding="utf-8")
LOG = logdir / "log_21-03-04.txt"
LOG.write_text("start\n", encoding="utf-8")

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def dms(arcmin):
    sign = "-" if arcmin < 0 else "+"
    value = abs(arcmin) / 60.0
    deg = int(value)
    minutes = int((value - deg) * 60)
    seconds = ((value - deg) * 60 - minutes) * 60
    return f"{sign}{deg:02d}° {minutes:02d}' {seconds:04.1f}\""


counter = [0]


def refresh(az_arcmin, alt_arcmin):
    counter[0] += 1
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp} KST INFO] [org.kde.kstars.ekos.align] - "
                 f"PAA Refresh({counter[0]}): Corrected az: {dms(az_arcmin)} "
                 f"alt: {dms(alt_arcmin)} total: 02° 00' 00\"\n")


MOVE_RE = re.compile(r"(ALT|AZ) move requested.*?:\s*([+-][\d.]+) arcmin")


def commanded(since=0):
    """(axis, arcmin) for every move the app actually sent, in order."""
    out = []
    for line in w.log_box.toPlainText().splitlines()[since:]:
        m = MOVE_RE.search(line)
        if m:
            out.append((m.group(1), float(m.group(2))))
    return out


pump(3.0)
w.accuracy.setValue(60)
w.max_move.setValue(140)
w.wait_two.setChecked(False)
w.autopa_settle.setValue(0)          # timing is test_autopa_settle's job
w.autopa_timer.setInterval(500)

# --- both errors negative -> both corrections positive -------------------
refresh(-112, -105)
w.start_autopa_watch()
pump(1.5)
refresh(-112, -105)
pump(9.0)

moves = dict(commanded())
print("errors az=-112' alt=-105' -> commanded:", moves)
assert moves.get("ALT") == 105.0, f"ALT must move against the error, got {moves.get('ALT')}"
assert moves.get("AZ") == 112.0, f"AZ must move against the error, got {moves.get('AZ')}"

# --- both errors positive -> both corrections negative -------------------
mark = len(w.log_box.toPlainText().splitlines())
refresh(+40, +30)
pump(9.0)

moves2 = dict(commanded(mark))
print("errors az=+40'  alt=+30'  -> commanded:", moves2)
assert moves2.get("ALT") == -30.0, f"ALT must move against the error, got {moves2.get('ALT')}"
assert moves2.get("AZ") == -40.0, f"AZ must move against the error, got {moves2.get('AZ')}"

print("both axes correct against the error")
shutdown_app(app, w, _server)
