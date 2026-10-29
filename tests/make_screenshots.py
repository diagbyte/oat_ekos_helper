#!/usr/bin/env python3
"""Regenerate the README screenshots from the real UI.

    python3 tests/make_screenshots.py

Drives the actual OATHelper window against tests/fake_indi_server.py, so the
images cannot drift from the code: re-run it after a UI change and commit the
result. Nothing touches a real mount - the fake server listens on a private
port and the window uses a throwaway HOME.

It needs a display. QT_QPA_PLATFORM=offscreen renders the layout but, depending
on the Qt build, may report zero font families and produce images with no text
at all, so this deliberately does not set it - run it on a desktop session (or
under `xvfb-run -a python3 tests/make_screenshots.py`).
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.gettempdir()) / "oat_helper_screenshots"

shutil.rmtree(HOME, ignore_errors=True)
CFG = HOME / ".config" / "oat-helper"
# Two profiles, so the header badge and its menu have something to show. The
# names are only illustrations - a profile is called whatever its owner calls
# it, and the mount it is for lives in the `model` field.
SHOWN = '{"simple_mode": true, "always_show_firmware_tab": true,'         ' "magnetic_declination_deg": -8.0, "log_visible": true, "model": "%s"}'
for shown_name, shown_model in (("oat-pier", "OpenAstroTracker"),
                                ("oae-field", "OpenAstroExplorer")):
    profile = CFG / "profiles" / shown_name
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "config.json").write_text(SHOWN % shown_model, encoding="utf-8")
(CFG / "active_profile").write_text("oat-pier", encoding="utf-8")
(HOME / ".config" / "kstarsrc").write_text(
    "[General]\nLogToFile=true\nLogToDefault=false\n", encoding="utf-8")

os.environ["HOME"] = str(HOME)
os.environ["USERPROFILE"] = str(HOME)      # Path.home() reads this on Windows
os.environ["LANG"] = "en_US.UTF-8"

sys.path.insert(0, str(REPO / "oat_helper"))
sys.path.insert(0, str(REPO / "tests"))

from _harness import ensure_indi_server, shutdown_app  # noqa: E402

server = ensure_indi_server()

from PyQt5 import QtGui, QtWidgets  # noqa: E402
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
if not QtGui.QFontDatabase().families():
    print("This Qt platform plugin has no fonts, so the images would come out blank.")
    print("Run it on a desktop session, or under 'xvfb-run -a'.")
    shutdown_app(app, None, server)
    raise SystemExit(1)

window = oat_helper.OATHelper()
window.resize(1040, 680)
window.show()

OUT = REPO / "images"
OUT.mkdir(parents=True, exist_ok=True)


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def shot(name, tab, settle=1.5):
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    if tab not in titles:
        print(f"  skipped {name}: no '{tab}' tab")
        return
    window.tabs.setCurrentIndex(titles.index(tab))
    pump(settle)
    path = OUT / f"{name}.png"
    window.grab().save(str(path))
    print(f"  {path.relative_to(REPO)}  {path.stat().st_size / 1024:.0f} KB")


pump(4.0)
shot("home", "Home")
shot("autopa", "AutoPA")

window.advanced_toggle.setChecked(True)      # the rest is behind Advanced
pump(1.5)
window.refresh_mount_monitor()
shot("monitor", "Monitor", settle=2.5)
shot("firmware", "Firmware")


def shot_profile_menu(name="profiles"):
    """The header badge with its menu open.

    A menu is its own top-level window, so grabbing the main window misses it;
    the two are painted into one image instead, the menu where it drops.
    """
    from PyQt5 import QtCore, QtGui
    badge = window.profile_btn
    window.refresh_profile_badge()
    window._fill_profile_menu()
    menu = window.profile_menu
    menu.adjustSize()
    pump(0.3)

    header_h = badge.mapTo(window, QtCore.QPoint(0, badge.height())).y() + 6
    header = window.grab(QtCore.QRect(0, 0, window.width(), header_h))
    body = menu.grab()
    left = badge.mapTo(window, QtCore.QPoint(0, 0)).x()

    canvas = QtGui.QPixmap(window.width(), header_h + body.height() + 8)
    canvas.fill(QtGui.QColor("#f0f0f0"))
    painter = QtGui.QPainter(canvas)
    painter.drawPixmap(0, 0, header)
    painter.drawPixmap(left, header_h + 2, body)
    painter.end()

    path = OUT / f"{name}.png"
    canvas.save(str(path))
    print(f"  {path.relative_to(REPO)}  {path.stat().st_size / 1024:.0f} KB")


shot_profile_menu()

shutdown_app(app, window, server)
print("done")
