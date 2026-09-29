"""Shared helpers for the headless tests.

Each test owns its INDI server on a private port.  Reusing whatever happens to
listen on 7624 was actively dangerous: on the Raspberry Pi that runs the mount
a real indiserver is listening there, and test_full_flow.py auto-accepts every
dialog, so the suite would have driven the real motors and written EEPROM.
"""
import atexit
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_started = []


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _port_open(port, timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _point_config_at(port):
    """Make the app under test dial the fake server instead of port 7624.

    Every test sets HOME (and, on Windows, USERPROFILE) before importing
    oat_helper, and writes its own config.json first, so merging the port in
    here reaches the app without any test-only hook in the application code.
    """
    cfg_path = Path.home() / ".config" / "oat-helper" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["indi_port"] = port
    cfg["indi_host"] = "127.0.0.1"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")


def ensure_indi_server(wait=8.0):
    """Start tests/fake_indi_server.py on a private port and point the app at it."""
    port = _free_port()
    env = dict(os.environ, FAKE_INDI_PORT=str(port))
    process = subprocess.Popen([sys.executable, str(HERE / "fake_indi_server.py")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=env)
    _started.append(process)
    deadline = time.time() + wait
    while time.time() < deadline:
        if _port_open(port):
            _point_config_at(port)
            return process
        if process.poll() is not None:
            raise RuntimeError("fake_indi_server exited immediately")
        time.sleep(0.05)
    process.terminate()
    raise RuntimeError(f"fake_indi_server did not start listening on {port}")


def _kill_leftovers():
    """A test that raises before shutdown_app must not leak a server."""
    for process in _started:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:
                process.kill()


atexit.register(_kill_leftovers)


def shutdown_app(app=None, window=None, server=None):
    """Stop timers, close the socket and drain workers before exiting."""
    try:
        from PyQt5 import QtCore
        if window is not None:
            for name in ("autopa_timer", "home_timer", "safety_timer", "monitor_timer",
                         "pa_move_timeout", "pa_fallback_poll", "pa_ack_timer"):
                timer = getattr(window, name, None)
                if timer is not None:
                    timer.stop()
            try:
                window.indi.disconnect_server()
            except Exception:
                pass
        QtCore.QThreadPool.globalInstance().waitForDone(5000)
        if app is not None:
            app.processEvents()
    except Exception:
        pass
    if server is not None:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()
    _kill_leftovers()
