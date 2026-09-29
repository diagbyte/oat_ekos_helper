"""One button must run GO TO HOME and then the shutdown move, in that order.

Skipping the shutdown move is what costs you the next session: it is the move
that records the power-on -> Home DEC travel that "Restore saved DEC Home"
replays. Two separate buttons meant the second one got forgotten, so they are
chained behind one button named after the intent.

The chain has to wait for Go To Home to clear dec_home_move_busy before the
shutdown move starts, or the second move trips its own "wait for the current
move to finish" guard and silently does nothing.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/end_session"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
shutil.rmtree("/tmp/end_session", ignore_errors=True)
Path("/tmp/end_session/.config/oat-helper").mkdir(parents=True, exist_ok=True)
Path("/tmp/end_session/.config/oat-helper/config.json").write_text(
    '{"simple_mode": false, "release_dec_deg": -30.0, "release_ra_deg": 0.0}', encoding="utf-8")

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


def lines():
    return w.log_box.toPlainText().splitlines()


pump(3.0)
# Widen the firmware DEC limits so the -30 degree shutdown move is not clamped.
w.indi.meade("@XSDLL-60#")
pump(1.0)
w.read_dec_limits()
pump(2.0)

mark = len(lines())
w.end_session()
pump(14.0)

new = lines()[mark:]
home_done = [i for i, l in enumerate(new) if "Mini HOME done" in l]
shutdown_done = [i for i, l in enumerate(new) if "Moved to the shutdown position" in l]
for line in new:
    print("   ", line.split("] ", 1)[-1][:100])

assert home_done, "end_session must run GO TO HOME"
assert shutdown_done, ("end_session must go on to the shutdown position - if the chain fires "
                       "before dec_home_move_busy clears, the second move is refused silently")
assert home_done[0] < shutdown_done[0], "Home must complete before the shutdown move starts"

# The whole point: the reverse travel is recorded for the next session.
print("saved DEC Home offset:", w.cfg.get("dec_home_offset_steps"))
assert w.cfg.get("dec_home_offset_steps"), \
    "the shutdown move must record the travel 'Restore saved DEC Home' replays"

print("end_session ran Home -> shutdown and recorded the restore value")
shutdown_app(app, w, _server)
