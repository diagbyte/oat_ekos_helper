"""An Ekos capture+solve takes ~25 s, so the refresh line that lands right
after a correction was measured before it. Acting on that line applies the same
correction twice, the axis overshoots to the mirror of the error, and the run
oscillates (-104 -> +104 -> -106 -> +109 ...) while the direction guard blames
the motors. This reproduces that timing and checks it is ignored.
"""
import os
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/autopa_stale"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/autopa_stale", ignore_errors=True)
logdir = Path("/tmp/autopa_stale/.local/share/kstars/logs/2026-09-18")
logdir.mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_stale/.config").mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_stale/.config/kstarsrc").write_text("[General]\nLogToFile=true\nLogToDefault=false\n")
LOG = logdir / "log_21-01-02.txt"
LOG.write_text("start\n")

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
    return f"{sign}{deg:02d}\u00b0 {minutes:02d}' {seconds:04.1f}\""


counter = [0]


def refresh(az_arcmin, alt_arcmin, when=None):
    counter[0] += 1
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with LOG.open("a") as fh:
        fh.write(f"[{stamp} KST INFO] [org.kde.kstars.ekos.align] - "
                 f"PAA Refresh({counter[0]}): Corrected az: {dms(az_arcmin)} alt: {dms(alt_arcmin)} "
                 f"total: 02\u00b0 00' 00\"\n")


pump(3.0)
w.accuracy.setValue(60)
w.max_move.setValue(140)
w.wait_two.setChecked(False)
w.autopa_timer.setInterval(500)

refresh(-112, -105)          # the solution that starts the run
w.start_autopa_watch()
pump(1.5)
refresh(-112, -105)
pump(8.0)                    # let the correction run to completion

finished = w.autopa_adjustment_finished
print("correction finished at:", finished.strftime("%H:%M:%S") if finished else None)

# the field case: Ekos writes a line whose measurement predates the correction
refresh(-112, -105, when=finished - timedelta(seconds=20))
pump(4.0)

log = w.log_box.toPlainText()
ignored = [l for l in log.splitlines() if "Ignoring a PAA solution" in l]
print("stale line ignored:", bool(ignored))
print(" ", ignored[-1].split("] ", 1)[-1][:100] if ignored else "-")
assert ignored, "a measurement older than the correction must not be applied again"

# a genuinely new measurement is still acted on
refresh(-20, -18)
pump(6.0)
accepted = [l for l in w.log_box.toPlainText().splitlines() if "New PAA Refresh accepted" in l]
print("fresh solutions accepted:", len(accepted))
assert len(accepted) >= 2, "measurements taken after the correction must still be used"

moves = [l for l in w.log_box.toPlainText().splitlines() if "move requested" in l]
print("moves issued:", len(moves))
for line in moves[-2:]:
    print("  ", line.split("] ", 1)[-1][:80])

shutdown_app(app, w, _server)
