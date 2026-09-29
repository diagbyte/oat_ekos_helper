"""Closing the window must not walk away from a running flash.

A PlatformIO upload writes the microcontroller's flash over the serial port.
Before this, closeEvent() accepted unconditionally: the window vanished, the
subprocess was orphaned and kept writing with nothing watching it, and the
progress/Cancel UI was gone. Half-written flash is exactly how a board is
bricked, so closing now asks first and kills the process when confirmed.

closeEvent also used to stop only two of the seven timers - the test harness
had to stop the rest by hand, which is the tell.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/close_flash"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/close_flash", ignore_errors=True)
Path("/tmp/close_flash/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/close_flash/.config/oat-helper/config.json").write_text(
    '{"simple_mode": false, "always_show_firmware_tab": true}', encoding="utf-8")

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtGui, QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

asked = {"count": 0, "answer": QtWidgets.QMessageBox.No}
QtWidgets.QMessageBox.question = staticmethod(
    lambda *a, **k: (asked.__setitem__("count", asked["count"] + 1), asked["answer"])[1])

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


pump(3.0)

# A stand-in for `pio run -t upload`: long enough to still be running below.
script = Path("/tmp/close_flash/slow_flash.py")
script.write_text("import time\n"
                  "print('Writing | ##### | 40% 1.0s', flush=True)\n"
                  "time.sleep(30)\n", encoding="utf-8")

w.run_async(lambda: w._run_streamed([sys.executable, str(script)],
                                    cwd="/tmp/close_flash", timeout=60))
pump(1.5)
process = w._fw_process
assert process is not None and process.poll() is None, "the fake flash should be running"
print("flash running, pid", process.pid)

# --- declining the question must keep the window open ----------------------
asked["answer"] = QtWidgets.QMessageBox.No
event = QtGui.QCloseEvent()
w.closeEvent(event)
print("asked:", asked["count"], "| event accepted:", event.isAccepted())
assert asked["count"] == 1, "closing during a flash must ask"
assert not event.isAccepted(), "declining must cancel the close"
assert w._fw_process is not None and w._fw_process.poll() is None, \
    "declining must leave the flash running"

# --- confirming must close AND not orphan the process ----------------------
asked["answer"] = QtWidgets.QMessageBox.Yes
event2 = QtGui.QCloseEvent()
w.closeEvent(event2)
pump(1.0)
print("asked:", asked["count"], "| event accepted:", event2.isAccepted(),
      "| process exited:", process.poll() is not None)
assert event2.isAccepted(), "confirming must close the window"
assert process.poll() is not None, "confirming must terminate the flash, not orphan it"

# --- every timer must be stopped ------------------------------------------
running = [name for name in ("autopa_timer", "home_timer", "monitor_timer", "pa_move_timeout",
                             "pa_fallback_poll", "pa_ack_timer", "fw_elapsed_timer")
           if getattr(w, name, None) is not None and getattr(w, name).isActive()]
print("timers still active after close:", running)
assert not running, f"closeEvent left timers running: {running}"

print("close during flash is guarded")
shutdown_app(app, w, _server)
