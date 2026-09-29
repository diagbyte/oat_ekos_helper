"""The pushed D-Bus source must share every guard with the log-file source.

KStars registers Ekos Align at /KStars/Ekos/Align and its interface exports
newLog(QString) - the only signal there that carries the PAA numbers
(polarResultUpdated / updatedErrorsChanged live on PolarAlignmentAssistant,
which is never put on the bus). Reading that signal removes the log file, the
LogToFile setting, the log-directory search and the timestamp parsing: the value
arrives when Ekos computes it, so "now" IS the measurement time.

What must not happen is both sources running: the same measurement would arrive
under two signatures and the correction would be applied twice.
"""
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/autopa_dbus"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/autopa_dbus", ignore_errors=True)
Path("/tmp/autopa_dbus/.config").mkdir(parents=True, exist_ok=True)

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


MOVE_RE = re.compile(r"(ALT|AZ) move requested.*?:\s*([+-][\d.]+) arcmin")


def commanded(since=0):
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
w.autopa_settle.setValue(0)

# --- 1. no session bus here, so it must say so and fall back ---------------
w.start_autopa_watch()
pump(2.0)
log = w.log_box.toPlainText()
fell_back = any(s in log for s in ("read from the log file", "read from the log "
                                   "file instead", "using the log file"))
print("source in use:", w.paa_source, "| label:", w.paa_source_label.text())
print("fallback explained in the log:", fell_back)
assert w.paa_source == "logfile", "without a session bus the log file must be used"
assert fell_back, "the fallback must say why, not fail silently"

# --- 2. the pushed handler applies the same rules --------------------------
# Exactly what KStars emits on newLog for a refresh iteration.
EKOS_LINE = ('PAA Refresh(3): Corrected az: -00° 40\' 00.0" '
             'alt: -00° 30\' 00.0" total: 00° 50\' 00.0"')

w.paa_source = "dbus"                 # as _start_paa_dbus() would have set it
w.autopa_adjustment_finished = None
mark = len(w.log_box.toPlainText().splitlines())
w._on_ekos_align_log(EKOS_LINE)
pump(8.0)

moves = dict(commanded(mark))
print("pushed az=-40' alt=-30' -> commanded:", moves)
assert moves.get("ALT") == 30.0, f"ALT must correct against the error, got {moves.get('ALT')}"
assert moves.get("AZ") == 40.0, f"AZ must correct against the error, got {moves.get('AZ')}"

# --- 2b. the SAME measurement delivered twice must correct only once ------
# A monotonic counter as the signature would pass the shared duplicate guard
# every time, so one Refresh delivered twice (a resubscribe, a duplicated match
# rule) would be corrected twice and the axis would overshoot to the mirror of
# the error. The signature has to come from the content, like the log path's.
mark = len(w.log_box.toPlainText().splitlines())
w._on_ekos_align_log(EKOS_LINE)
pump(4.0)
print("moves from the duplicate delivery:", commanded(mark))
assert not commanded(mark), "the same PAA line delivered twice must not correct twice"
print("duplicate delivery suppressed")

# --- 3. non-PAA chatter on the same signal must be ignored ----------------
mark = len(w.log_box.toPlainText().splitlines())
for noise in ("Solver completed in 3.2 seconds.",
              "Capturing image...",
              "Polar Alignment Assistant is starting."):
    w._on_ekos_align_log(noise)
pump(1.0)
assert not commanded(mark), "only PAA Refresh lines may trigger a move"
print("non-PAA log lines ignored")

# --- 4. the polled source must stand down while D-Bus is live -------------
# Put a genuine, unseen PAA Refresh in the log where the poller would find it.
# If both sources ran, this same measurement would be applied a second time.
logdir = Path("/tmp/autopa_dbus/.local/share/kstars/logs/2026-09-18")
logdir.mkdir(parents=True, exist_ok=True)
Path("/tmp/autopa_dbus/.config/kstarsrc").write_text(
    "[General]\nLogToFile=true\nLogToDefault=false\n", encoding="utf-8")
(logdir / "log_21-05-06.txt").write_text(
    "[2026-09-18 1:05:06.123 KST INFO] [org.kde.kstars.ekos.align] - "
    "PAA Refresh(4): Corrected az: -01° 00' 00.0\" alt: -01° 00' 00.0\" "
    "total: 01° 25' 00.0\"\n", encoding="utf-8")

w.paa_offsets = {}
w.paa_last_match = None
w.autopa_last_signature = None
w.autopa_adjustment_finished = None
w.autopa_busy = False
before = len(w.log_box.toPlainText().splitlines())
w.autopa_tick()
pump(3.0)
polled = [l for l in w.log_box.toPlainText().splitlines()[before:]
          if "New PAA Refresh accepted" in l]
print("poller accepted anything:", len(polled), "| moves:", commanded(before))
assert not polled, "the log poller must not consume a solution while D-Bus is the source"
assert not commanded(before), "the log poller must not also move while D-Bus is live"
print("log poller stands down while D-Bus is the source")

print("D-Bus source shares the guards and stays exclusive")
# --- 5. a solution that cannot be acted on yet is retried, not dropped ----
# A pushed solution has no second chance: the poller would re-read the same line
# from its cache, but D-Bus delivers once. Refusing it used to park the status
# on "AutoPA move pending" until Ekos happened to send another Refresh.
w.autopa_running = True
w.paa_source = "dbus"
w.autopa_adjustment_finished = None
w.autopa_started_at = None
w.autopa_last_signature = None
w.paa_deferred = None
w.pa_motion_active = True            # as if an earlier correction were still running
mark = len(w.log_box.toPlainText().splitlines())
SECOND_LINE = ("PAA Refresh(9): Corrected az: -00° 30' 00.0\" "
               "alt: -00° 20' 00.0\" total: 00° 36' 00.0\"")
w._on_ekos_align_log(SECOND_LINE)
pump(0.5)
assert w.paa_deferred is not None, "a solution arriving mid-move must be held, not dropped"
assert not commanded(mark), "nothing may move while the earlier move is active"
print("solution held while a move was active")

w.pa_motion_active = False           # the earlier move finishes
w.autopa_timer.setInterval(300)
w.autopa_timer.start()
pump(6.0)
w.autopa_timer.stop()
retried = commanded(mark)
print("moves after the retry tick:", retried)
assert retried, "the held solution must be retried once the axis is free"
assert w.paa_deferred is None, "a retried solution must be cleared"
print("deferred solution retried")

# --- 6. silence on the bus must be reported, not sat on silently ----------
w.pa_motion_active = False
w.paa_deferred = None
w._dbus_quiet_warned = False
w._dbus_last_paa = datetime.now() - timedelta(seconds=w.DBUS_QUIET_WARN_SECONDS + 60)
w._dbus_housekeeping()
quiet = [l for l in w.log_box.toPlainText().splitlines() if "No PAA result has arrived over D-Bus" in l]
print("quiet warning:", bool(quiet))
assert quiet, "a dead subscription must be reported, not left waiting all night"
w._dbus_housekeeping()
again = [l for l in w.log_box.toPlainText().splitlines() if "No PAA result has arrived over D-Bus" in l]
assert len(again) == 1, "the quiet warning must be logged once, not every tick"
print("dbus silence reported once")

shutdown_app(app, w, _server)
