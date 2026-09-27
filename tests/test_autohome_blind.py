"""RA AutoHome must work when every one-character ('&') read fails.

On some Raspberry Pi INDI builds lx200_OpenAstroTech's getCommandChar()
fails for every '&' command. 0.6.4 sent :MHR as '&' and AutoHome stopped
working ("No reply byte for &MHRR30#"). The helper sends it blind and
verifies with :GX#/:XGAH#.
"""
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/ahome"
shutil.rmtree("/tmp/ahome", ignore_errors=True)
Path("/tmp/ahome/.config").mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()


def pump(seconds, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
        if until and until():
            return True
    return False


pump(3.0)
sent = []
real = w.indi.meade
w.indi.meade = lambda c, **k: (sent.append(c), real(c, **k))[1]

for label, fail in (("normal driver", "0"), ("every '&' read fails", "1")):
    real(f":ZZCHARFAIL{fail}#")
    sent.clear()
    mark = len(w.log_box.toPlainText().splitlines())
    w.start_home(["RA"])
    ok = pump(40.0, until=lambda: "AutoHome sequence finished" in w.log_box.toPlainText().split("\n", mark)[-1])
    log = "\n".join(w.log_box.toPlainText().splitlines()[mark:])
    mhr = [c for c in sent if "MHR" in c]
    print(f"{label:22s}: sent {mhr}, succeeded={'✓ RA AutoHome succeeded' in log}")
    assert ok and "✓ RA AutoHome succeeded" in log, log
    assert mhr and all(c.startswith("@") for c in mhr), mhr
    assert "No reply byte" not in log, log
real(":ZZCHARFAIL0#")
w.indi.meade = real

print("AutoHome without '&' reads OK")
shutdown_app(app, w, _server)
