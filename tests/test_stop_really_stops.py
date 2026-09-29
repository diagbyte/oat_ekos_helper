"""Nothing may command a move after Stop or Emergency Stop.

Two separate holes, both ending in "the mount moves after the user said stop":

1. emergency_stop() sent :Q# and called stop_autopa_watch(), but never cleared
   pa_motion_active or pa_move_queue. A two-axis correction queues [ALT, AZ] and
   hands the second axis to QTimer.singleShot(150, _send_next_pa_axis), which
   tests only `if not self.pa_motion_active`. That flag was still True, so the
   AZ axis started driving ~150 ms AFTER the emergency stop.

2. autopa_tick checks autopa_running, then hands a log scan to a worker that can
   take hundreds of ms on a Pi. QRunnables cannot be cancelled, so after Stop the
   result still landed in _apply_paa_solution - which checked neither
   autopa_running nor autopa_busy - and called move_pa(). The D-Bus entry point
   _on_ekos_align_log did check autopa_running; the shared consumer did not,
   while its docstring claimed every guard lived there.
"""
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/stop_stops"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/stop_stops", ignore_errors=True)
logdir = Path("/tmp/stop_stops/.local/share/kstars/logs/2026-09-18")
logdir.mkdir(parents=True, exist_ok=True)
Path("/tmp/stop_stops/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/stop_stops/.config/oat-helper/config.json").write_text(
    '{"simple_mode": false, "paa_source": "logfile"}', encoding="utf-8")
Path("/tmp/stop_stops/.config/kstarsrc").write_text(
    "[General]\nLogToFile=true\nLogToDefault=false\n", encoding="utf-8")
LOG = logdir / "log_21-06-07.txt"
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


MOVE_RE = re.compile(r"(ALT|AZ) move requested")


def moves(since=0):
    return [l for l in w.log_box.toPlainText().splitlines()[since:] if MOVE_RE.search(l)]


pump(3.0)
w.accuracy.setValue(60)
w.max_move.setValue(140)
w.wait_two.setChecked(False)
w.autopa_settle.setValue(0)

# ---- 1. Emergency Stop must not let the queued second axis start ----------
# Queue a two-axis correction directly: move_pa is the single entry point both
# the manual buttons and the watcher use.
assert w.move_pa(20.0, 20.0, source="auto"), "the two-axis move should be accepted"
assert w.pa_motion_active, "a motion should now be active"
pump(0.3)
first = len(moves())
print("axes commanded before the emergency stop:", first)

w.emergency_stop()
pump(4.0)                     # well past the 150 ms hand-off to the next axis

after = moves()
print("axes commanded in total after the emergency stop:", len(after))
print("pa_motion_active:", w.pa_motion_active, "| queue:", w.pa_move_queue)
assert len(after) == first, (
    f"emergency_stop must not let another axis start: {len(after) - first} extra move(s) sent")
assert not w.pa_motion_active, "emergency_stop must clear pa_motion_active"
assert not w.pa_move_queue, "emergency_stop must drop the queued axes"
print("emergency stop really stops")

# ---- 2. A solution already in flight must not move after Stop -------------
w.autopa_running = True       # as start_autopa_watch would have left it
w.autopa_adjustment_finished = None
w.autopa_last_signature = None
mark = len(w.log_box.toPlainText().splitlines())

# Exactly the tuple _scan_paa_file produces, handed over as the worker would.
solution = ("sig-in-flight", datetime.now(), -0.5, -0.5, str(LOG), "in-flight line")
w.stop_autopa_watch()         # the user presses Stop while the worker is running
w._apply_paa_solution(solution)
pump(3.0)

late = moves(mark)
print("moves issued by the in-flight solution after Stop:", len(late))
assert not late, f"a solution that landed after Stop must be dropped, got {late}"
print("a late solution is dropped")

shutdown_app(app, w, _server)
