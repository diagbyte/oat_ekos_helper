"""0.6.4: serial channel hygiene, command routing and UI clean-up.

The fake server models how lx200_OpenAstroTech really moves bytes (see Wire
in fake_indi_server.py): a one-character reply sent blind stays in the buffer
and is glued to the next read, and :SC# answers twice.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/h064"
shutil.rmtree("/tmp/h064", ignore_errors=True)
Path("/tmp/h064/.config").mkdir(parents=True, exist_ok=True)
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


def buttons(widget):
    return [b.text() for b in widget.findChildren(QtWidgets.QPushButton)]


pump(3.0)

# ---- 1. one-character commands are sent blind, and nothing is left behind --
# (0.6.4 sent them as '&'; on some Raspberry Pi builds every '&' read fails,
# which broke AutoHome. 0.6.5 sends them blind and flushes before the next read.)
import re as _re
src = Path(oat_helper.__file__).read_text(encoding="utf-8")
assert not _re.search(r"""meade(?:_async)?\(\s*f?["']&""", src), "the helper must not rely on '&' reads"
w.indi.meade("@SHL065722#")                 # firmware answers "1", the driver never reads it
date = w.indi.meade(":GC#")
print("read after a blind one-char command:", repr(date))
assert date.count("/") == 2 and len(date.rstrip("#")) == 8, "the unread '1' was glued to the next reply"
w.indi.meade("@MXr0#"); w.indi.meade("@MXd0#")
assert w.indi.meade(":XGDL#").startswith("30.0|"), "two blind one-char commands must not shift the channel"
print("blind one-char commands leave the channel clean: OK")

# :SC# answers twice; after the resync the next read must be ITS OWN reply.
assert w.indi.meade(":SC01/01/20#").startswith("1Updating")
assert w._resync_meade() is True
limits = w.indi.meade(":XGDL#")
print("read after :SC# + resync:", repr(limits))
assert "|" in limits, "channel still shifted by one reply after _resync_meade()"
assert w.indi.meade(":XGR#").rstrip("#").replace(".", "", 1).isdigit()

# ---- 2. prefix routing for typed commands -----------------------------------
norm = w._normalize_command
cases = {":hF#": "@hF#", "hP": "@hP#", ":Q#": "@Q#", ":XSR1258.6#": "@XSR1258.6#",
         ":MT1#": "@MT1#", "MXr100": "@MXr100#", ":SHP#": "@SHP#", ":SG-09#": "@SG-09#",
         ":SC09/27/26#": ":SC09/27/26#", ":GX#": ":GX#", "XGR": ":XGR#", ":XFR#": ":XFR#",
         "@GVN#": "@GVN#", "&GX#": "&GX#", "&MT1#": "&MT1#", ":Mgw0500#": "@Mgw0500#", ":RS#": "@RS#"}
for typed, want in cases.items():
    got = norm(typed)
    assert got == want, (typed, got, want)
print("typed command routing:", len(cases), "cases OK")

# ---- 3. DEC limit 'here' refuses the wrong side and Home --------------------
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
sent = []
real_meade = w.indi.meade
w.indi.meade = lambda c, **k: (sent.append(c), real_meade(c, **k))[1]


def try_limit(which):
    sent.clear()
    w.set_dec_limit_here(which); pump(1.2)
    return [c for c in sent if c.startswith("@XSDL")]


assert try_limit("L") == [] and try_limit("U") == [], "a limit at Home(0) must be refused"
real_meade("@MXd-3142#")                                  # DEC to the 'down' side
assert try_limit("U") == [], "an 'up' limit on the 'down' side must be refused"
assert try_limit("L") == ["@XSDLL#"], "a 'down' limit on the 'down' side must be sent"
real_meade("@MXd6284#")                                   # now on the 'up' side
assert try_limit("L") == [] and try_limit("U") == ["@XSDLU#"]
w.indi.meade = real_meade
print("DEC limit here: wrong side / Home refused, right side sent: OK")

# ---- 4. UI clean-up ---------------------------------------------------------
diag = buttons(w.diag_tab)
assert "Refresh OAT status" in diag and "Open the log folder" in diag, diag
assert "Go Home" not in diag and "Set Home" not in diag, diag
assert "DEC limits" in diag
print("diag tab: read-only refresh restored, Go Home / Set Home quick buttons gone")

assert w.factory_cmd.isReadOnly() and w.factory_cmd.text() == ":XFR#"
w.save_config()
cfg = json.loads(Path("/tmp/h064/.config/oat-helper/config.json").read_text())
assert "factory_reset_command" not in cfg
print("factory reset command fixed to :XFR#")

# The Controller tab duplicated Ekos' own Mount tab (direction pad, keyboard
# slew, slew rate, tracking toggle) and is gone. Its one control with no Ekos
# equivalent, the sidereal rate trim, sits with the other calibration.
tabs = [w.tabs.tabText(i) for i in range(w.tabs.count())]
assert "Controller" not in tabs, tabs
axis = buttons(w.axis_tab)
assert "Read" in axis and "Save" in axis, axis
assert hasattr(w, "track_trim") and hasattr(w, "track_speed_label")
print("no Controller tab; tracking trim is on Axis cal")

fw = buttons(w.firmware_tab)
assert "Refresh version" not in fw and "Check latest release" not in fw, fw
assert "Refresh" in fw and "Check for updates" in fw
wiz = buttons(w.wizard_tab)
assert "Start auto correction" in wiz and "Wait for auto correction" not in wiz, wiz
mon = buttons(w.monitor_tab)
assert "Set 'down' limit here" in mon and "Set 'up' limit here" in mon, mon
print("firmware / wizard / monitor button clean-up: OK")

print("0.6.4 OK")
shutdown_app(app, w, _server)
