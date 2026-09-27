"""Tracking ON/OFF and Unpark are verified with :GX#, not the reply byte.

lx200_OpenAstroTech returns char(-1) from getCommandChar() when the one-byte
read fails. Plain char is unsigned on ARM (Raspberry Pi), so the driver then
publishes the byte 0xFF as the reply of an '&' command and the log used to
show "Tracking ON request -> \ufffd". The fake server can reproduce that.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/trkhome"
shutil.rmtree("/tmp/trkhome", ignore_errors=True)
Path("/tmp/trkhome/.config").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def last_lines(n=4):
    return [l.split("] ", 1)[-1] for l in w.log_box.toPlainText().splitlines()[-n:]]


pump(3.0)

# 1. Normal driver: reply "1", state verified.
w.set_tracking(False); pump(1.5)
off = last_lines(1)[0]
print("OFF :", off)
assert off.startswith("✓ Tracking OFF - verified by :GX#"), off
assert "ignored" not in off, off

w.set_tracking(True); pump(1.5)
on = last_lines(1)[0]
print("ON  :", on)
assert on.startswith("✓ Tracking ON - verified by :GX#"), on

# 2. ARM driver read failure: the reply is the byte 0xFF.
assert w.indi.meade(":ZZCHARFAIL1#") == "1"
raw = w.indi.meade("&MT0#")
print("raw '&' reply with 0xFF:", repr(raw))
assert raw == "", raw                     # normalized, no U+FFFD any more
pump(0.3)
assert any("No reply byte for &MT0#" in l for l in last_lines(3)), last_lines(3)

w.set_tracking(True); pump(1.5)
lines = last_lines(3)
print("ON with 0xFF:", lines[-1])
assert lines[-1].startswith("✓ Tracking ON - verified by :GX#"), lines
assert "\ufffd" not in "\n".join(lines), lines

w.unpark_mount(); pump(1.5)
print("UNPARK      :", last_lines(1)[0])
assert last_lines(1)[0].startswith("✓ Unpark - tracking started"), last_lines(1)

w.indi.meade(":ZZCHARFAIL0#")

# 3. A request the mount did not honour is reported, not claimed as success.
w._read_tracking_state = lambda settle=0.3: (False, {"state": "Idle", "motion": "-----"})
w.set_tracking(True); pump(1.5)
bad = last_lines(1)[0]
print("MISMATCH    :", bad)
assert bad.startswith("✗ Tracking ON requested, but :GX# reports OFF"), bad

print("tracking verification OK")
shutdown_app(app, w, _server)
