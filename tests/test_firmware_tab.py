"""Firmware maintenance page of the merged extension.

It is hidden when the machine has no serial port; this test forces it on and
exercises the version panel, the configuration editor and the banner.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = "/tmp/fwtab"
os.environ["LANG"] = "en_US.UTF-8"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "oat_helper"))
shutil.rmtree("/tmp/fwtab", ignore_errors=True)
Path("/tmp/fwtab/.config/oat-helper").mkdir(parents=True, exist_ok=True)

# a throwaway firmware repo so the git version panel has something real to read
repo = Path("/tmp/fwtab/repo")
repo.mkdir(parents=True, exist_ok=True)
run = lambda *a: subprocess.run(a, cwd=repo, capture_output=True, text=True)
run("git", "init", "-q", "-b", "develop", ".")
run("git", "config", "user.email", "t@t")
run("git", "config", "user.name", "t")
(repo / "Version.h").write_text('#define VERSION "V1.13.9"\n')
(repo / "platformio.ini").write_text("[env:mksgenlv21]\n[env:mega2560]\n")
(repo / ".gitignore").write_text("Configuration_local*\n")
run("git", "add", "-A")
run("git", "commit", "-qm", "v1")
run("git", "tag", "v1.13.9")

Path("/tmp/fwtab/.config/oat-helper/config.json").write_text(json.dumps({
    "always_show_firmware_tab": True,
    "firmware_source_dir": str(repo),
    "simple_mode": False,
}))

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ensure_indi_server, shutdown_app  # noqa: E402
_server = ensure_indi_server()

from PyQt5 import QtWidgets  # noqa: E402
QtWidgets.QMessageBox.question = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes)
QtWidgets.QMessageBox.warning = staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Ok)
import oat_helper  # noqa: E402

app = QtWidgets.QApplication([])
w = oat_helper.OATHelper()
w.resize(1000, 640)
w.show()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


pump(3.5)
tabs = [w.tabs.tabText(i) for i in range(w.tabs.count())]
print("tabs:", tabs)
assert "Firmware" in tabs, "firmware tab should be shown when forced on"
print("banner:", w.local_banner.text()[:80])
print("version panel:", w.fw_version_label.text())
print("platformio:", w.fw_tool_status.text())
print("git:", w.git_status.text()[:90])
assert w.git_status.text() != "git: not checked", "git status should be probed at startup"
print("git install button shown:", not w.git_install_btn.isHidden())

# the missing-git branch: the panel must say so and offer the install command
oat_helper.OATHelper._git_exe = staticmethod(lambda: None)
w.refresh_git_status()
pump(1.5)
print("without git:", w.git_status.text()[:95])
print("install button offered:", not w.git_install_btn.isHidden())
assert not w.git_install_btn.isHidden(), "install hint should appear when git is missing"
print("pio envs:", [w.fw_env.itemText(i) for i in range(w.fw_env.count())][:4])
print("factory reset default:", w.factory_cmd.text())

# configuration editor round trip
cfg = Path("/tmp/fwtab/Configuration_local.hpp")
cfg.write_text("#define RA_STEPPER_TYPE STEPPER_TYPE_NEMA17\n#define DEC_STEPPER_TYPE STEPPER_TYPE_NEMA17\n")
w._load_configuration_file(cfg, quiet=True)
pump(0.5)
print("defines imported:", len(w.imported_defines), "| RA:", w._define_value("RA_STEPPER_TYPE"))
w.refresh_config_inspector()
pump(1.5)
print("inspector lines:", len(w.config_text.toPlainText().splitlines()))

# saving must keep both the observing and firmware settings in one file
w.save_config()
saved = json.loads(Path(oat_helper.CONFIG_FILE).read_text())
print("saved keys:", sorted(k for k in saved if k.startswith(("firmware", "expected", "simple", "release"))))

w.tabs.setCurrentIndex(tabs.index("Firmware"))
pump(0.5)
w.grab().save("/tmp/shot_firmware_tab.png")

try:
    from PyQt5 import QtCore as _QtCore
    for _timer in ("autopa_timer", "home_timer", "safety_timer", "monitor_timer",
                   "pa_move_timeout", "pa_fallback_poll", "pa_ack_timer"):
        _t = getattr(w, _timer, None)
        if _t is not None:
            _t.stop()
    w.indi.disconnect_server()
    _QtCore.QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
except Exception:
    pass

shutdown_app(app, w, _server)
