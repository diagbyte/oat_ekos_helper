"""A log clock that disagrees with this machine's must not stall the run.

The settle window used to compare `ts` - parsed out of the KStars log text -
against `finished`, a datetime.now() here. latest_ekos_paa's own docstring says
those clocks disagree (which is why novelty is decided by signature, not time),
and the README documents sharing the KStars logs folder from another machine.
So if the log-writing clock lagged by more than the settle value,
`ts <= finished + settle` held for every future solution: each tick logged
"still settling", `sol` stayed truthy so the 60 s watchdog never fired, and the
run hung for the rest of the night. Raising the tolerance from a hard-coded 1 s
to a 30 s default turned a one-cycle nuisance into a permanent stall.

The settle window is arrival-based now; the line's own timestamp is only used
while the two clocks are demonstrably in step.
"""
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/autopa_skew"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/autopa_skew", ignore_errors=True)
logdir = Path("/tmp/autopa_skew/.local/share/kstars/logs/2026-09-18")
logdir.mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_skew/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_skew/.config/oat-helper/config.json").write_text(
    '{"paa_source": "logfile"}', encoding="utf-8")
Path("/tmp/autopa_skew/.config/kstarsrc").write_text(
    "[General]\nLogToFile=true\nLogToDefault=false\n", encoding="utf-8")
LOG = logdir / "log_21-07-08.txt"
LOG.write_text("start\n", encoding="utf-8")

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()

# The log-writing machine's clock lags this one by ten minutes.
LAG = timedelta(minutes=10)


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
    stamp = (datetime.now() - LAG).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"[{stamp} KST INFO] [org.kde.kstars.ekos.align] - "
                 f"PAA Refresh({counter[0]}): Corrected az: {dms(az_arcmin)} "
                 f"alt: {dms(alt_arcmin)} total: 02° 00' 00\"\n")


MOVE_RE = re.compile(r"(ALT|AZ) move requested")


def moves(since=0):
    return [l for l in w.log_box.toPlainText().splitlines()[since:] if MOVE_RE.search(l)]


pump(3.0)
w.accuracy.setValue(60)
w.max_move.setValue(140)
w.wait_two.setChecked(False)
w.autopa_settle.setValue(3)          # short, so the test does not take a minute
w.autopa_timer.setInterval(500)

refresh(-40, -30)
w.start_autopa_watch()
pump(1.5)
refresh(-40, -30)
pump(9.0)                            # first correction runs to completion

finished = w.autopa_adjustment_finished
assert finished is not None, "the first correction should have completed"
print("first correction finished; log clock lags by", LAG)

# A genuinely new measurement, written once the settle window has passed.
while (datetime.now() - finished).total_seconds() < 4:
    pump(0.5)
mark = len(w.log_box.toPlainText().splitlines())
refresh(-20, -15)
pump(7.0)

later = moves(mark)
stalled = [l for l in w.log_box.toPlainText().splitlines()[mark:] if "still settling" in l]
print("moves after the lagged-clock measurement:", len(later))
print("stall messages:", len(stalled))
assert later, ("a lagging log clock must not stall the run: the settle window is "
               "measured in arrival time, not in log time")

# And it must say once that it stopped trusting the log timestamps.
noted = [l for l in w.log_box.toPlainText().splitlines() if "log clock is" in l]
print("clock-difference notice:", noted[-1].split('] ', 1)[-1][:90] if noted else "-")
assert noted, "the run must say that the log clock is out of step, not fail silently"
assert len(noted) == 1, f"the clock notice must be logged once, got {len(noted)}"

print("a skewed log clock no longer stalls AutoPA")
shutdown_app(app, w, _server)
