import os, sys, time, shutil
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/pahome2"
os.environ.pop("XDG_DATA_HOME", None)
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))

shutil.rmtree("/tmp/pahome2", ignore_errors=True)
# Flatpak-style location only — the classic ~/.local/share/kstars path does NOT exist.
root = Path("/tmp/pahome2/.var/app/org.kde.kstars/data/kstars/logs/2026-09-18")
root.mkdir(parents=True, exist_ok=True)
Path("/tmp/pahome2/.config").mkdir(parents=True, exist_ok=True)
log = root / "log_01-05-00.txt"

# Exactly KStars' pattern: "[yyyy-MM-dd h:mm:ss.zzz t INFO] [category] - msg"
def line(n, az, alt, tot, hour="1"):
    return (f"[2026-09-18 {hour}:05:0{n}.123 KST INFO] [org.kde.kstars.ekos.align] - "
            f"PAA Refresh({n}): Corrected az: {az} alt: {alt} total: {tot}\n")

log.write_text(
    "[2026-09-18 1:04:00.000 KST INFO] [org.kde.kstars.ekos.align] - Polar Alignment Assistant\n"
    + line(1, '-00° 12\' 34"', '+00° 03\' 21"', '00° 12\' 59"'))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets
import oat_helper as oat_tools
app = QtWidgets.QApplication([])
w = oat_tools.OATHelper()


def pump(t):
    e = time.time() + t
    while time.time() < e:
        app.processEvents(); time.sleep(0.01)


print("roots[1:4]:", [str(r) for r in w.ekos_log_roots()[1:4]])
found = w.latest_ekos_paa(full=True)
print("flatpak root found:", found is not None,
      "ts:", found[1].strftime("%H:%M:%S"), "az′:", round(found[2] * 60, 2))

# logging prerequisites: file logging off -> must fail, and must NOT demand Alignment
ok, msg = w.check_ekos_logging()
print("logging off ->", ok, "|", msg[:45])
Path("/tmp/pahome2/.config/kstarsrc").write_text(
    "[General]\nLogToFile=true\nLogToDefault=false\nDisableLogging=false\n")
ok, msg = w.check_ekos_logging()
print("file logging on (no AlignmentLogging) ->", ok, "|", msg[:52])

# watcher: baseline then a new Refresh must be accepted
w.autopa_running = True
w.paa_offsets = {}; w.paa_last_match = None
base = w.latest_ekos_paa(full=True)
w.autopa_last_signature = base[0]
with log.open("a") as fh:
    fh.write("noise line\n" + line(2, '-00° 02\' 04"', '+00° 01\' 11"', '00° 02\' 22"'))
nxt = w.latest_ekos_paa()
print("new refresh picked up:", nxt[0] != base[0],
      "az′:", round(nxt[2] * 60, 2), "alt′:", round(nxt[3] * 60, 2))

# a corrupt PAA line must not block later good lines (old code aborted the file)
with log.open("a") as fh:
    fh.write("[2026-09-18 1:06:00.000 KST INFO] [x] - PAA Refresh(3): Corrected az: ??? alt: ??? total: ???\n")
    fh.write(line(4, '-00° 00\' 30"', '+00° 00\' 10"', '00° 00\' 31"'))
last = w.latest_ekos_paa()
print("corrupt line tolerated, latest az′:", round(last[2] * 60, 2))

# diagnostics
w.diagnose_paa_log(); pump(1.5)
print("--- diagnose output ---")
print("\n".join(l.split("] ", 1)[-1] for l in w.log_box.toPlainText().splitlines()[-6:]))


shutdown_app(app, w, _server)
