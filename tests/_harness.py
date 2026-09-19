"""Shared helpers for the headless tests.

Each test owns its INDI server: relying on one started by hand makes the suite
fail in confusing ways (commands come back as "OAT_MEADE_COMMAND not found")
when the background process is gone.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 7624


def _port_open(timeout=0.5):
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_indi_server(wait=8.0):
    """Start tests/fake_indi_server.py unless something already listens."""
    if _port_open():
        return None
    process = subprocess.Popen([sys.executable, str(HERE / "fake_indi_server.py")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + wait
    while time.time() < deadline:
        if _port_open():
            return process
        if process.poll() is not None:
            raise RuntimeError("fake_indi_server exited immediately")
        time.sleep(0.2)
    process.terminate()
    raise RuntimeError("fake_indi_server did not start listening")


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
