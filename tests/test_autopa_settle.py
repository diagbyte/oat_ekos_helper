"""A measurement must be CAPTURED after the correction, not merely logged after it.

Ekos logs a "PAA Refresh" line when the plate solve *finishes*, roughly 25 s
after the shutter opened. The line that lands a few seconds after a correction
therefore shows the error as it was *before* the move. Acting on it applies the
same correction twice: the axis overshoots to the mirror of the error, the next
cycle corrects back, and the run oscillates while the direction guard blames
the motors.

0.6.2 only rejected lines timestamped *before* the correction finished, which
in the field almost never happens - the solve completes afterwards - so every
run still consumed one stale solution per cycle. The settle window is the fix:
nothing measured inside it is used.
"""
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/autopa_settle"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/autopa_settle", ignore_errors=True)
logdir = Path("/tmp/autopa_settle/.local/share/kstars/logs/2026-09-18")
logdir.mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_settle/.config").mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_settle/.config/oat-helper").mkdir(parents=True, exist_ok=True)
# Pin the source: with "auto", a dev box running KStars with the Align
# module open would take the D-Bus path and never read these fixtures.
Path("/tmp/autopa_settle/.config/oat-helper/config.json").write_text(
    '{"paa_source": "logfile"}', encoding="utf-8")
Path("/tmp/autopa_settle/.config/kstarsrc").write_text(
    "[General]\nLogToFile=true\nLogToDefault=false\n", encoding="utf-8")
LOG = logdir / "log_21-04-05.txt"
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


def accepted():
    return [l for l in w.log_box.toPlainText().splitlines() if "New PAA Refresh accepted" in l]


pump(3.0)
w.accuracy.setValue(60)
w.max_move.setValue(140)
w.wait_two.setChecked(False)
w.autopa_settle.setValue(8)          # pretend a capture+solve takes 8 s
w.autopa_timer.setInterval(500)

refresh(-40, -30)
w.start_autopa_watch()
pump(1.5)
refresh(-40, -30)
pump(9.0)                            # first correction runs to completion

finished = w.autopa_adjustment_finished
before = len(accepted())
print("correction finished at:", finished.strftime("%H:%M:%S"))

# Ekos logs the next solve 2 s later - but that image was taken BEFORE the move.
refresh(-40, -30)
pump(4.0)
ignored = [l for l in w.log_box.toPlainText().splitlines() if "still settling" in l]
print("logged 2s after the correction -> accepted:", len(accepted()) - before,
      "| settle message:", bool(ignored))
assert len(accepted()) == before, "a solve logged inside the settle window must not be used"
assert ignored, "the settle rejection must say why"

# Past the settle window, a new measurement is genuinely fresh and must be used.
while (datetime.now() - finished).total_seconds() < 9:
    pump(0.5)
refresh(-20, -15)
pump(6.0)
print("after the settle window -> accepted:", len(accepted()) - before)
assert len(accepted()) > before, "measurements taken after the settle window must still be used"

print("settle window honoured")
shutdown_app(app, w, _server)
