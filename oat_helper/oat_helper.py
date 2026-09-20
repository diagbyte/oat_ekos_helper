#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# OAT Helper - a KStars/Ekos extension for the OpenAstroTracker.
# Copyright (C) 2026 OAT Ekos Suite contributors
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.  It is distributed WITHOUT ANY WARRANTY; see the GNU General Public
# License for more details.  You should have received a copy of the license
# along with this program; if not, see <https://www.gnu.org/licenses/>.
#
# Unofficial community project - not affiliated with OpenAstroTech.
"""OAT Helper - one KStars/Ekos extension for the OpenAstroTracker.

Goals:
- Never open the OAT serial port directly. It talks to the running INDI server
  as a second INDI client.
- Reuse LX200 OpenAstroTech's existing AutoPA number properties.
- Add practical AutoHome controls, offset calibration and persistence.
- Provide an optional Ekos PAA log watcher compatible with the legacy
  autopa_v2.py workflow, with Linux parsing fixes and safety limits.
- Keep observing controls focused: firmware build/flash/configuration maintenance
  lives in the separate OAT Firmware Ekos extension.

This is a community-style helper, not an official OpenAstroTech/KStars release.
Test low-value moves first and keep mechanical travel limits in mind.
"""

import hashlib
import json
import logging
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
from xml.sax.saxutils import escape

from PyQt5 import QtCore, QtGui, QtWidgets

APP_NAME = "OAT Helper"
VERSION = "0.6.1"
DEFAULT_DEVICE = "LX200 OpenAstroTech"
CONFIG_DIR = Path.home() / ".config" / "oat-helper"
DATA_DIR = Path.home() / ".local" / "share" / "oat-helper"
LOG_DIR = DATA_DIR / "logs"
CONFIG_FILE = CONFIG_DIR / "config.json"
FIRMWARE_CONFIG = CONFIG_DIR / "Configuration_local.hpp"
# Earlier releases shipped two extensions (oat_tools / oat_firmware); their
# settings are migrated on first start.
LEGACY_CONFIGS = (Path.home() / ".config" / "oat-tools" / "config.json",
                  Path.home() / ".config" / "oat-firmware" / "config.json")
LEGACY_FIRMWARE_CONFIGS = (Path.home() / ".config" / "oat-firmware" / "Configuration_local.hpp",
                           Path.home() / ".config" / "oat-tools" / "firmware" / "Configuration_local.hpp")

APP_SLUG = "oat_helper"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Minimal i18n: English is the source language, translations live next to this
# script as "<app>.<lang>.json" ({english: translated}).  Log messages stay in
# English on purpose so they can be pasted into bug reports unchanged.
# ---------------------------------------------------------------------------
CATALOG = {}
CURRENT_LANG = "en"


def available_languages():
    here = Path(__file__).resolve().parent
    langs = ["en"]
    for path in sorted(here.glob(f"{APP_SLUG}.*.json")):
        code = path.name[len(APP_SLUG) + 1:-5]
        if code and code not in langs:
            langs.append(code)
    return langs



def detect_system_language():
    """Work out the UI language the desktop/KStars is using.

    Ekos starts this extension as a child process, so the environment is the
    one KStars runs with.  KDE sets its UI language in LANGUAGE (and in
    plasma-localerc) independently of LANG, so check that first and fall back
    to the POSIX locale variables.
    """
    candidates = []
    language = os.environ.get("LANGUAGE")
    if language:
        candidates.extend(part for part in language.split(":") if part)
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name)
        if value:
            candidates.append(value)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    for name, keys in (("plasma-localerc", ("LANGUAGE", "LANG")),
                       ("kdeglobals", ("Language", "LANGUAGE"))):
        path = config_home / name
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for key in keys:
            match = re.search(rf"^{key}\s*=\s*(\S+)", text, re.M)
            if match:
                candidates.extend(part for part in match.group(1).split(":") if part)
    for candidate in candidates:
        code = candidate.split(".")[0].split("_")[0].split("@")[0].strip().lower()
        if code and code not in ("c", "posix"):
            return code
    return "en"

def load_language(lang=None):
    """Load a translation catalog; 'auto' follows the system locale."""
    global CATALOG, CURRENT_LANG
    if not lang or lang == "auto":
        lang = detect_system_language()
    path = Path(__file__).resolve().parent / f"{APP_SLUG}.{lang}.json"
    try:
        CATALOG = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        CATALOG = {}
    CURRENT_LANG = lang if CATALOG else "en"
    return CURRENT_LANG


def _(text):
    """Translate a UI string (identity when no catalog entry exists)."""
    return CATALOG.get(text, text)


def translate_widget_tree(root):
    """Translate every already-built widget in place.

    Static UI text is written in English in the code, so a catalog lookup of
    the current text is enough - no per-widget bookkeeping and no need to wrap
    hundreds of constructor arguments.
    """
    if not CATALOG:
        return
    widgets = root.findChildren(QtWidgets.QWidget)
    widgets.append(root)
    pairs = (("text", "setText"), ("title", "setTitle"), ("toolTip", "setToolTip"),
             ("placeholderText", "setPlaceholderText"), ("windowTitle", "setWindowTitle"),
             ("format", "setFormat"))
    for widget in widgets:
        if isinstance(widget, (QtWidgets.QLineEdit, QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
            continue  # never touch user-entered content
        for getter, setter in pairs:
            get = getattr(widget, getter, None)
            put = getattr(widget, setter, None)
            if not callable(get) or not callable(put):
                continue
            try:
                current = get()
            except Exception:
                continue
            if isinstance(current, str) and current in CATALOG:
                try:
                    put(CATALOG[current])
                except Exception:
                    pass
        if isinstance(widget, QtWidgets.QComboBox):
            for i in range(widget.count()):
                if widget.itemText(i) in CATALOG:
                    widget.setItemText(i, CATALOG[widget.itemText(i)])
        if isinstance(widget, QtWidgets.QTabWidget):
            for i in range(widget.count()):
                if widget.tabText(i) in CATALOG:
                    widget.setTabText(i, CATALOG[widget.tabText(i)])


# Compact, high-contrast style.  The target is a Raspberry Pi over VNC, where
# vertical space is scarce: tight group boxes, de-emphasized help text, and a
# clear accent for the one primary action of each page.
STYLESHEET = """
QGroupBox {
    font-weight: 600;
    border: 1px solid palette(mid);
    border-radius: 6px;
    margin-top: 10px;
    padding: 6px 6px 4px 6px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
}
QLabel[hint="true"]  { color: palette(dark); font-size: 11px; }
QLabel[hint="warn"]  { color: #b45309; font-size: 11px; }
QLabel[role="value"] { font-weight: 700; }
QPushButton { padding: 3px 10px; }
QPushButton#primary {
    font-weight: 700;
    border: 1px solid #1d4ed8;
    border-radius: 5px;
    background: palette(button);
    color: #1d4ed8;
    padding: 5px 10px;
}
QPushButton#primary:hover  { background: #1d4ed8; color: white; }
QPushButton#danger { font-weight: 700; color: #b91c1c; }
QTabBar::tab { padding: 5px 10px; }
QScrollArea { border: none; }
"""

# Labels that show live state must keep their own colouring and are never
# converted into small grey help text.
_STATUS_LABEL_ATTRS = (
    "conn_status", "mount_status", "safe_ra_label", "fw_label", "home_status", "ha_status",
    "dec_fw_limit_label", "target_check_label", "track_speed_label",
    "site_angle_label",
    "dec_manual_status", "pa_status", "pa_pos", "ra_cal_label", "dec_cal_label",
    "ra_limit_label", "dec_limit_label", "axis_start_label",
    "wiz_conn_status", "wiz_ra_status", "wiz_dec_status", "wiz_paa_status",
    "wiz_ready_status",
)


class IndiClient(QtCore.QObject):
    """Small INDI XML client focused on the OAT properties we need."""

    connectionChanged = QtCore.pyqtSignal(bool, str)
    logMessage = QtCore.pyqtSignal(str)
    propertySeen = QtCore.pyqtSignal(str, str)
    meadeResult = QtCore.pyqtSignal(str)
    numberState = QtCore.pyqtSignal(str, str)
    deviceDetected = QtCore.pyqtSignal(str)
    deviceListChanged = QtCore.pyqtSignal(list)
    mountConnectionChanged = QtCore.pyqtSignal(bool)

    VECTOR_TAGS = (
        "defTextVector", "setTextVector", "defNumberVector", "setNumberVector",
        "defSwitchVector", "setSwitchVector", "defLightVector", "setLightVector",
        "message", "delProperty",
    )

    def __init__(self, host="127.0.0.1", port=7624, device=DEFAULT_DEVICE):
        super().__init__()
        self.host = host
        self.port = int(port)
        self.device = device
        self.sock: Optional[socket.socket] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.running = False
        self.write_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending_event: Optional[threading.Event] = None
        self.pending_result: Optional[str] = None
        # 0.4.2: every Meade transaction (send + wait for the driver's
        # setTextVector) is serialized.  Worker threads (Mount Monitor, safety
        # poll, AutoHome poll, SET HOME, jogs ...) used to race for the single
        # pending slot; the loser raised "Another Meade command is still
        # pending" *before* its command was transmitted.  SET HOME was the most
        # visible victim because it has no retry.
        self.meade_lock = threading.Lock()
        # Whether the connected LX200 OpenAstroTech driver acknowledges blind
        # '@' commands with a setTextVector (libindi >= 2.0.4 does).
        self.blind_ack_supported: Optional[bool] = None
        self._blind_ack_misses = 0
        self.seen_properties = set()
        # Every device name the server has announced, for the picker.
        self.known_devices = []
        self.last_number_states = {}
        self.last_number_values = {}
        self.last_text_values = {}
        self.last_switch_values = {}
        self.number_limits = {}
        self.device_connected = None
        # Dynamic property discovery.  Different INDI package revisions may keep
        # the OAT element names while changing/renaming the vector.
        self.meade_vector = None
        self.meade_element = None
        self.polar_alt_vector = None
        self.polar_alt_element = None
        self.polar_az_vector = None
        self.polar_az_element = None

    def configure(self, host, port, device):
        self.host = host.strip() or "127.0.0.1"
        self.port = int(port)
        self.device = device.strip() or DEFAULT_DEVICE

    def connect_server(self):
        if self.running:
            return True
        try:
            sock = socket.create_connection((self.host, self.port), timeout=3.0)
            sock.settimeout(1.0)
            self.sock = sock
            self.running = True
            self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self.reader_thread.start()
            self._send_raw('<getProperties version="1.7"/>\n')
            # We never need image blobs in this helper.
            self._send_raw('<enableBLOB>Never</enableBLOB>\n')
            self.connectionChanged.emit(True, f"INDI {self.host}:{self.port} connected")
            return True
        except Exception as exc:
            self.running = False
            self.sock = None
            self.connectionChanged.emit(False, f"INDI connection failed: {exc}")
            return False

    def disconnect_server(self):
        self.running = False
        s, self.sock = self.sock, None
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                s.close()
            except Exception:
                pass
        with self.pending_lock:
            if self.pending_event:
                self.pending_event.set()
        self.device_connected = None
        self._clear_oat_property_cache(None)
        self.connectionChanged.emit(False, "INDI disconnected")

    def _send_raw(self, xml_text: str):
        if not self.running or not self.sock:
            raise ConnectionError("INDI server is not connected")
        data = xml_text.encode("utf-8")
        with self.write_lock:
            self.sock.sendall(data)

    def _reader_loop(self):
        buf = ""
        try:
            while self.running and self.sock:
                try:
                    data = self.sock.recv(65536)
                    if not data:
                        raise ConnectionError("INDI server closed connection")
                    buf += data.decode("utf-8", errors="replace")
                    while True:
                        item, buf = self._extract_element(buf)
                        if item is None:
                            break
                        self._handle_element(item)
                except socket.timeout:
                    continue
        except Exception as exc:
            if self.running:
                self.logMessage.emit(f"INDI reader stopped: {exc}")
        finally:
            if self.running:
                QtCore.QTimer.singleShot(0, lambda: self.disconnect_server())

    @classmethod
    def _extract_element(cls, buf: str):
        starts = []
        for tag in cls.VECTOR_TAGS:
            idx = buf.find("<" + tag)
            if idx >= 0:
                starts.append((idx, tag))
        if not starts:
            # prevent unbounded junk accumulation
            return None, buf[-4096:] if len(buf) > 8192 else buf
        idx, tag = min(starts)
        if idx > 0:
            buf = buf[idx:]
        if tag in ("message", "delProperty"):
            end = buf.find("/>")
            if end < 0:
                close = f"</{tag}>"
                end = buf.find(close)
                if end < 0:
                    return None, buf
                end += len(close)
            else:
                end += 2
            return buf[:end], buf[end:]
        close = f"</{tag}>"
        end = buf.find(close)
        if end < 0:
            return None, buf
        end += len(close)
        return buf[:end], buf[end:]

    def _clear_oat_property_cache(self, name=None):
        """Forget deleted/redefined OAT properties so reconnects cannot use stale vectors."""
        names = None if not name else {str(name)}
        if names is None:
            self.seen_properties = {item for item in self.seen_properties if item[0] != self.device}
            self.last_number_states.clear()
            self.last_number_values.clear()
            self.last_text_values.clear()
            self.last_switch_values.clear()
            self.number_limits.clear()
            self.meade_vector = self.meade_element = None
            self.polar_alt_vector = self.polar_alt_element = None
            self.polar_az_vector = self.polar_az_element = None
            return

        for prop in names:
            self.seen_properties.discard((self.device, prop))
            self.last_number_states.pop(prop, None)
            self.last_number_values.pop(prop, None)
            self.last_text_values.pop(prop, None)
            self.last_switch_values.pop(prop, None)
            for key in [key for key in self.number_limits if key[0] == prop]:
                self.number_limits.pop(key, None)
            if self.meade_vector == prop:
                self.meade_vector = self.meade_element = None
            if self.polar_alt_vector == prop:
                self.polar_alt_vector = self.polar_alt_element = None
            if self.polar_az_vector == prop:
                self.polar_az_vector = self.polar_az_element = None

    def _handle_element(self, xml_text: str):
        try:
            elem = ET.fromstring(xml_text)
        except ET.ParseError:
            return
        tag = elem.tag
        dev = elem.attrib.get("device", "")
        name = elem.attrib.get("name", "")
        children = list(elem)
        child_names = {c.attrib.get("name", "") for c in children}

        # INDI removes most device properties during a driver/mount reconnect.
        # 0.2.7 ignored delProperty and could keep stale POLAR_* vector names.
        if tag == "delProperty":
            if dev == self.device:
                self._clear_oat_property_cache(name or None)
                if not name:
                    self.device_connected = None
                self.logMessage.emit(
                    f"INDI property removed: {dev}.{name or '*'}; cached OAT vectors invalidated")
            return

        if dev and name and dev not in self.known_devices:
            self.known_devices.append(dev)
            self.deviceListChanged.emit(list(self.known_devices))

        if dev and name:
            self.seen_properties.add((dev, name))
            self.propertySeen.emit(dev, name)

        # Discover by the stable INDI VECTOR names first.  Astroberry/libindi
        # packages do not always expose the same child element name as current
        # upstream, while the vectors (Meade, POLAR_ALT, POLAR_AZ) have remained
        # stable.  Prefer the upstream OAT_* child names when present, otherwise
        # use the first element in the vector.
        looks_oat = name in ("Meade", "POLAR_ALT", "POLAR_AZ") or bool(
            {"OAT_MEADE_COMMAND", "OAT_POLAR_ALT", "OAT_POLAR_AZ"} & child_names
        )
        if dev and looks_oat and dev != self.device and "OpenAstro" in dev:
            self.device = dev
            self.logMessage.emit(f"Auto-detected OAT INDI device: {dev}")
            self.deviceDetected.emit(dev)

        if dev != self.device:
            return

        if tag in ("defTextVector", "setTextVector"):
            vals = {}
            for c in children:
                vals[c.attrib.get("name", "")] = (c.text or "").strip()
            if vals:
                self.last_text_values[name] = vals

        if tag in ("defTextVector", "setTextVector") and (
            name == "Meade" or "OAT_MEADE_COMMAND" in child_names
        ):
            self.meade_vector = name
            preferred = next((c for c in children if c.attrib.get("name") == "OAT_MEADE_COMMAND"), None)
            child = preferred if preferred is not None else (children[0] if children else None)
            if child is not None:
                self.meade_element = child.attrib.get("name", "")
                result = (child.text or "").strip()
                self.meadeResult.emit(result)
                # A defTextVector is only a definition / initial value.  Only a
                # setTextVector can be the response to a command we just sent.
                if tag == "setTextVector":
                    with self.pending_lock:
                        if self.pending_event is not None:
                            self.pending_result = result
                            self.pending_event.set()

        if tag in ("defSwitchVector", "setSwitchVector"):
            vals = {}
            for c in children:
                vals[c.attrib.get("name", "")] = (c.text or "").strip()
            if vals:
                self.last_switch_values[name] = vals
            if name == "CONNECTION":
                connect_key = next((k for k in vals if k.upper() == "CONNECT"), None)
                disconnect_key = next((k for k in vals if k.upper() == "DISCONNECT"), None)
                connected = None
                if connect_key is not None:
                    connected = vals[connect_key].strip().lower() in ("on", "1", "true")
                elif disconnect_key is not None:
                    connected = not (vals[disconnect_key].strip().lower() in ("on", "1", "true"))
                if connected is not None and connected != self.device_connected:
                    self.device_connected = connected
                    self.mountConnectionChanged.emit(bool(connected))
                    self.logMessage.emit(
                        f"OAT mount CONNECTION={'Connected' if connected else 'Disconnected'}")
                    if not connected:
                        # CONNECTION itself remains defined, but operational
                        # vectors are about to be deleted/redefined.
                        for prop in (self.meade_vector, self.polar_alt_vector, self.polar_az_vector):
                            if prop:
                                self._clear_oat_property_cache(prop)

        if tag in ("defNumberVector", "setNumberVector"):
            if name == "POLAR_ALT" or "OAT_POLAR_ALT" in child_names:
                self.polar_alt_vector = name
                preferred = next((c for c in children if c.attrib.get("name") == "OAT_POLAR_ALT"), None)
                child = preferred if preferred is not None else (children[0] if children else None)
                if child is not None:
                    self.polar_alt_element = child.attrib.get("name", "")
            if name == "POLAR_AZ" or "OAT_POLAR_AZ" in child_names:
                self.polar_az_vector = name
                preferred = next((c for c in children if c.attrib.get("name") == "OAT_POLAR_AZ"), None)
                child = preferred if preferred is not None else (children[0] if children else None)
                if child is not None:
                    self.polar_az_element = child.attrib.get("name", "")
            state = elem.attrib.get("state", "")
            self.last_number_states[name] = state
            vals = {}
            for c in children:
                child_name = c.attrib.get("name", "")
                try:
                    vals[child_name] = float((c.text or "").strip())
                except Exception:
                    pass
                if tag == "defNumberVector" and child_name:
                    try:
                        lo = float(c.attrib["min"])
                        hi = float(c.attrib["max"])
                        self.number_limits[(name, child_name)] = (lo, hi)
                    except Exception:
                        pass
            if vals:
                # Merge: a driver may send a setNumberVector carrying only the
                # elements that changed, and replacing the dict would drop the
                # rest (losing LONG out of GEOGRAPHIC_COORD, for example).
                self.last_number_values.setdefault(name, {}).update(vals)
            # Only a setNumberVector is a post-definition state update and can
            # acknowledge a command we just sent. Definitions are cached but
            # must not satisfy the AutoPA ACK timer.
            if tag == "setNumberVector":
                self.numberState.emit(name, state)

    def has_property(self, name):
        return (self.device, name) in self.seen_properties

    def has_meade(self):
        return bool(self.meade_vector and self.meade_element)

    def has_polar_alt(self):
        return bool(self.polar_alt_vector and self.polar_alt_element)

    def has_polar_az(self):
        return bool(self.polar_az_vector and self.polar_az_element)

    def mount_is_connected(self):
        """True/False when CONNECTION was seen; None until the driver publishes it."""
        return self.device_connected

    def pa_number_limits(self, axis):
        if str(axis).upper() == "ALT":
            vector, element, fallback = self.polar_alt_vector, self.polar_alt_element, (-140.0, 140.0)
        else:
            vector, element, fallback = self.polar_az_vector, self.polar_az_element, (-320.0, 320.0)
        if vector and element:
            return self.number_limits.get((vector, element), fallback)
        return fallback

    def request_properties(self):
        if self.running:
            # Re-request all definitions; useful when Ekos connected the device a moment
            # after OAT Tools connected to indiserver.
            self._send_raw('<getProperties version="1.7"/>\n')

    def send_text(self, vector, element, value):
        xml = (
            f'<newTextVector device="{escape(self.device)}" name="{escape(vector)}">'
            f'<oneText name="{escape(element)}">{escape(str(value))}</oneText>'
            f'</newTextVector>\n'
        )
        self._send_raw(xml)

    def send_number(self, vector, element, value):
        xml = (
            f'<newNumberVector device="{escape(self.device)}" name="{escape(vector)}">'
            f'<oneNumber name="{escape(element)}">{float(value):.6f}</oneNumber>'
            f'</newNumberVector>\n'
        )
        self._send_raw(xml)

    def send_text_vector(self, vector, values):
        parts = [f'<newTextVector device="{escape(self.device)}" name="{escape(vector)}">']
        for element, value in values.items():
            parts.append(f'<oneText name="{escape(str(element))}">{escape(str(value))}</oneText>')
        parts.append('</newTextVector>\n')
        self._send_raw(''.join(parts))

    def send_number_vector(self, vector, values):
        parts = [f'<newNumberVector device="{escape(self.device)}" name="{escape(vector)}">']
        for element, value in values.items():
            parts.append(f'<oneNumber name="{escape(str(element))}">{float(value):.8f}</oneNumber>')
        parts.append('</newNumberVector>\n')
        self._send_raw(''.join(parts))

    def set_device_connected(self, connected):
        """Connect or disconnect the mount driver itself.

        Disconnecting makes the INDI driver release the serial port, which is
        exactly what an upload needs; the driver keeps running so Ekos can
        reconnect afterwards without restarting anything.
        """
        # send_switch_vector takes booleans; "Off" as a string is truthy and
        # would switch both members On.
        self.send_switch_vector("CONNECTION",
                                {"CONNECT": bool(connected), "DISCONNECT": not connected})

    def send_switch_vector(self, vector, values):
        parts = [f'<newSwitchVector device="{escape(self.device)}" name="{escape(vector)}">']
        for element, value in values.items():
            state = "On" if bool(value) else "Off"
            parts.append(f'<oneSwitch name="{escape(str(element))}">{state}</oneSwitch>')
        parts.append('</newSwitchVector>\n')
        self._send_raw(''.join(parts))

    def equatorial_eod(self):
        """Return cached (RA hours, DEC degrees) from EQUATORIAL_EOD_COORD when available."""
        vals = self.last_number_values.get("EQUATORIAL_EOD_COORD", {})
        if not vals:
            return None
        ra_key = next((k for k in vals if k.upper() in ("RA", "RAEODCOORD")), None)
        dec_key = next((k for k in vals if k.upper() in ("DEC", "DEEODCOORD")), None)
        if ra_key is None or dec_key is None:
            keys=list(vals)
            if len(keys) >= 2:
                ra_key, dec_key = keys[0], keys[1]
        try:
            return float(vals[ra_key]), float(vals[dec_key])
        except Exception:
            return None

    def meade(self, command: str, timeout=8.0) -> str:
        """Send via OAT_MEADE_COMMAND and wait for its result text.

        command prefixes supported by LX200 OpenAstroTech:
          ':' normal # terminated response
          '@' blind / no-response command
          '&' one-character response
        """
        if not self.has_meade():
            self.request_properties()
            deadline = time.time() + 3.0
            while time.time() < deadline and not self.has_meade():
                time.sleep(0.05)
            if not self.has_meade():
                raise RuntimeError(
                    "OAT_MEADE_COMMAND not found. Check that LX200 OpenAstroTech is Connected."
                )
        # Serialize the whole transaction.  Waiting on the lock releases the
        # GIL, so long jobs that poll :GX# with sleeps still interleave fairly
        # with the monitor/safety timers instead of raising on each other.
        if not self.meade_lock.acquire(timeout=float(timeout) + 15.0):
            raise TimeoutError(f"Meade channel busy; could not send {command}")
        try:
            ev = threading.Event()
            with self.pending_lock:
                self.pending_event = ev
                self.pending_result = None
            try:
                self.send_text(self.meade_vector, self.meade_element, command)
                if command.startswith("@"):
                    # '@' is the LX200 OpenAstroTech wrapper for a command that
                    # does not return a Meade payload (XSHR/XSHD/MXr/MXd/MHR/hF).
                    # libindi >= 2.0.4 still answers with an (empty) setTextVector.
                    # Consume that ack here so it can never be mistaken for the
                    # reply of the next ':' command (0.4.1 only slept 0.12 s and
                    # could feed "" into _parse_gx / the SET HOME verifier).
                    if self.blind_ack_supported is False:
                        time.sleep(0.12)
                        return ""
                    if ev.wait(1.5):
                        self.blind_ack_supported = True
                        self._blind_ack_misses = 0
                    else:
                        self._blind_ack_misses += 1
                        if self._blind_ack_misses >= 2 and self.blind_ack_supported is None:
                            self.blind_ack_supported = False
                            self.logMessage.emit(
                                "INDI driver does not acknowledge blind '@' Meade commands; "
                                "falling back to fixed delay (old lx200_OpenAstroTech?)")
                    return ""
                if not ev.wait(timeout):
                    raise TimeoutError(f"No reply for {command}")
                with self.pending_lock:
                    return self.pending_result or ""
            finally:
                with self.pending_lock:
                    self.pending_event = None
                    self.pending_result = None
        finally:
            self.meade_lock.release()

    def meade_busy(self):
        """True while another thread owns the Meade channel."""
        return self.meade_lock.locked()


class ProcessSignals(QtCore.QObject):
    """Line-by-line output from a long running build/upload."""
    line = QtCore.pyqtSignal(str)
    started = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(int)


class WorkerSignals(QtCore.QObject):
    result = QtCore.pyqtSignal(object)
    error = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()


class FunctionWorker(QtCore.QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class OATHelper(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        # Fit small VNC desktops (1280x720 and below) instead of assuming a
        # 720 px tall window always fits.  Everything below is scrollable, so a
        # short window only means more scrolling, never hidden controls.
        self.setMinimumSize(860, 420)
        self.threadpool = QtCore.QThreadPool.globalInstance()
        self.cfg = self.load_config()
        load_language(self.cfg.get("language", "auto"))
        self.indi = IndiClient(
            self.cfg.get("indi_host", "127.0.0.1"),
            self.cfg.get("indi_port", 7624),
            self.cfg.get("device", DEFAULT_DEVICE),
        )
        self.indi.connectionChanged.connect(self.on_connection_changed)
        self.indi.logMessage.connect(self.log)
        self.indi.propertySeen.connect(self.on_property_seen)
        self.indi.numberState.connect(self.on_number_state)
        self.indi.deviceDetected.connect(self.on_device_detected)
        self.indi.deviceListChanged.connect(self.on_device_list_changed)
        self.indi.mountConnectionChanged.connect(self.on_mount_connection_changed)

        self.autopa_timer = QtCore.QTimer(self)
        self.autopa_timer.setInterval(2500)
        self.autopa_timer.timeout.connect(self.autopa_tick)
        self.home_timer = QtCore.QTimer(self)
        self.home_timer.setInterval(1000)
        self.home_timer.timeout.connect(self.poll_home_state)

        self.autopa_running = False
        self.autopa_busy = False
        self.autopa_last_entry = None  # legacy diagnostic timestamp only
        self.autopa_last_signature = None
        self.autopa_adjustment_finished = datetime.now()
        self.autopa_was_moving = False
        self.autopa_prev_error_arcmin = None
        self.autopa_worse_count = 0

        # AutoPA motion interlock.  Never send another MAL/MAZ while an
        # earlier AutoPA move is still active.  This protects the OAT firmware
        # from overlapping relative-move commands and also serializes ALT->AZ.
        self.pa_motion_active = False
        self.pa_move_queue = []
        self.pa_active_axis = None
        self.pa_seen_busy = False
        self.pa_completion_scheduled = False
        self.pa_move_source = ""
        self.pa_axis_started_at = 0.0
        self.pa_motion_buttons = []
        self.pa_move_timeout = QtCore.QTimer(self)
        self.pa_move_timeout.setSingleShot(True)
        self.pa_move_timeout.setInterval(180000)
        self.pa_move_timeout.timeout.connect(self._pa_motion_timeout)
        self.pa_fallback_busy = False
        self.pa_fallback_poll = QtCore.QTimer(self)
        self.pa_fallback_poll.setInterval(400)
        self.pa_fallback_poll.timeout.connect(self._poll_pa_fallback)
        self.pa_ack_timer = QtCore.QTimer(self)
        self.pa_ack_timer.setSingleShot(True)
        self.pa_ack_timer.setInterval(3000)
        self.pa_ack_timer.timeout.connect(self._pa_ack_timeout)
        self.pa_number_update_seen = False
        self.pa_gx_seen_moving = False

        self.home_sequence = []
        self.home_busy = False

        self.ra_cal_active = False
        self.dec_cal_active = False
        self.ra_cal_jog = 0
        self.dec_cal_jog = 0
        self.ra_cal_old_offset = 0
        self.dec_cal_old_offset = 0

        # Sensorless DEC manual-home workflow.  Do not maintain a shadow
        # cumulative DEC position in OAT Tools: the firmware already exposes
        # the authoritative live DEC stepper coordinate in :GX#.
        self.dec_manual_active = False
        self.ra_steps_per_degree_live = None
        self.dec_steps_per_degree_live = None
        self.dec_jog_busy = False
        self.dec_home_move_busy = False
        self.mini_motion_busy = False
        self.ha_sync_busy = False
        self.home_adjust_busy = False
        self.set_home_busy = False
        # Firmware version gating: disable buttons the connected build cannot
        # serve instead of letting them silently return "0".
        self.firmware_version_text = ""
        self.firmware_version_num = 0
        self.dec_limits_firmware = None   # (lower_deg, upper_deg) from :XGDL#
        self.park_busy = False
        # DEC "odometer" for the sensorless axis.  :GX# DEC steps are relative
        # to the *last* zeroing event (power-on, RA AutoHome's setHome(false),
        # or SET HOME).  dec_zero_shift accumulates every zeroing the tool knows
        # about, so GX_dec + dec_zero_shift == DEC steps since power-on as long
        # as nothing else re-zeroed the axis.  Reset when the mount reconnects.
        self.dec_zero_shift = 0
        self.dec_odometer_valid = True
        self.home_dec_at_start = 0

        # Per-session shooting-preparation wizard state.  These flags only say
        # that the relevant preparation was completed in the current OAT Tools
        # session; they are deliberately not persisted across power cycles.
        self.wizard_ra_done = False
        self.wizard_dec_done = False
        self.wizard_pa_done = False
        self.wizard_paa_guidance_seen = False

        # Live safety/status polling. :XGST# is the OAT firmware extension for
        # remaining safe RA tracking time. DEC limit UI is intentionally not
        # duplicated here; firmware-side DEC limits remain authoritative.
        # Incremental Ekos log scanning state for the AutoPA watcher.
        self.paa_offsets = {}
        self.paa_last_match = None
        self.paa_pending_move = None
        self.safe_time_hours = None
        self.safety_poll_busy = False
        self.monitor_timer = QtCore.QTimer(self)
        self.monitor_timer.setInterval(3000)
        self.monitor_timer.timeout.connect(self.refresh_mount_monitor)
        self.imported_defines = {}
        self._fw_process = None
        self.process_signals = ProcessSignals()
        self.process_signals.started.connect(self._on_process_started)
        self.process_signals.line.connect(self._on_process_line)
        self.process_signals.finished.connect(self._on_process_finished)
        self.fw_elapsed_timer = QtCore.QTimer(self)
        self.fw_elapsed_timer.setInterval(1000)
        self.fw_elapsed_timer.timeout.connect(self._update_fw_elapsed)
        self.axis_start = None
        self.axis_commanded_deg = None
        self.axis_name = None
        self.axis_calculated_spd = None
        self.axis_calculated_axis = None

        self.setup_logging()
        self.migrate_legacy_files()
        self.build_ui()
        self.update_wizard_status()
        self.monitor_timer.start()
        QtCore.QTimer.singleShot(300, self.connect_indi)
        QtCore.QTimer.singleShot(600, self.refresh_firmware_environment)  # also probes git
        QtCore.QTimer.singleShot(900, self.update_local_banner)
        QtCore.QTimer.singleShot(1200, self.refresh_version_panel)
        if FIRMWARE_CONFIG.exists():
            QtCore.QTimer.singleShot(400, lambda: self._load_configuration_file(FIRMWARE_CONFIG, quiet=True))

    # ------------------------ config/logging ------------------------
    def load_config(self):
        defaults = {
            "language": "auto", "indi_host": "127.0.0.1", "indi_port": 7624, "device": DEFAULT_DEVICE,
            "ra_direction": "R", "dec_direction": "U", "home_range": 30,
            "accuracy_arcsec": 60.0, "alt_offset_arcmin": 0.0, "az_offset_arcmin": 0.0,
            "max_move_arcmin": 30.0,
            "dec_steps_per_degree_fallback": 314.1666667,
            "ra_limit_left_h": 5.0, "ra_limit_right_h": 7.0,
            "ra_physical_limit_h": 7.0, "dec_limit_down_deg": 0.0, "dec_limit_up_deg": 0.0,
            # Steps from the power-on DEC position to the last SET HOME position.
            # Optional manual override when KStars keeps its logs elsewhere.
            "ekos_log_dir": "",
            # "clear": SET HOME zeroes the firmware DEC homing offset so Ekos
            # Park stops at Home.  "eeprom": store the power-on->Home delta on
            # the mount (survives a tool reinstall, but
            # firmware Park then moves DEC by -offset, so use the tool's DEC Park).
            "dec_home_offset_mode": "clear",
            "autopa_wait_two_solutions": True,
            "autohome_ra_on_connect": False,
            "restore_dec_home_on_connect": False,
            "slew_rate": "M",
            # Shutdown ("release") position: where the axes are left before
            # power off so the camera's weight does not hang on the RA ring.
            # Degrees relative to Home; negative DEC lowers the lens.
            "release_dec_deg": -30.0, "release_ra_deg": 0.0,
            "release_stop_tracking": True,
            # Filled in once the working longitude encoding has been found.
            "longitude_command": "",
            # Simple mode shows only the three tabs a normal imaging session
            # needs; everything else stays available behind the Advanced toggle.
            "simple_mode": True,
            # Firmware maintenance (only usable on the machine holding the USB cable)
            "firmware_source_dir": "", "firmware_env": "mksgenlv21", "firmware_port": "/dev/ttyACM0",
            "firmware_ref": "develop", "firmware_last_fetch": "", "latest_release_tag": "",
            "factory_reset_command": ":XFR#",
            "expected_ra_spr": 400, "expected_dec_spr": 400, "expected_az_spr": 200,
            "expected_alt_spr": 200, "expected_autopa_version": 2,
            "custom_commands": [{"name": f"Custom {i}", "command": ""} for i in range(1, 5)],
            # Show the firmware tab even when no serial port is visible here.
            "always_show_firmware_tab": False,
            "drift_align_seconds": 60,
            "checklist": [
                "Tripod/base levelled", "Base aimed at true north",
                "Polar axis altitude set as shown above", "Cables routed with slack",
                "Power/battery checked", "Camera and guider connected", "Dew heater checked",
            ],
            "checklist_done": [],
            # Optional: set this by hand to also get a compass bearing.
            # It cannot be computed from the coordinates alone.
            "magnetic_declination_deg": None,
            "dec_home_offset_steps": None, "dec_home_saved_at": "",
            "dec_home_steps_per_degree": None,
            # Firmware Park (:hP#) slews to -DEC homing offset after reaching Home.
            # A stale XSHD value (older OAT Tools wrote one) makes Ekos Park leave
            # DEC away from Home, so SET HOME clears it unless disabled here.
            "clear_dec_homing_offset_on_set_home": True,
            # Remembered UI layout (VNC desktops differ a lot in size).
            "win_w": 0, "win_h": 0, "log_visible": True, "log_height": 110, "last_tab": 0,
        }
        try:
            if CONFIG_FILE.exists():
                old = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for key in defaults:
                    if key in old:
                        defaults[key] = old[key]
            else:
                # First start after the split extensions were merged: take what
                # each of them had, newest wins for keys they shared.
                for legacy in LEGACY_CONFIGS:
                    if not legacy.exists():
                        continue
                    try:
                        old = json.loads(legacy.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                    for key in defaults:
                        if key in old:
                            defaults[key] = old[key]
        except Exception:
            pass
        return defaults

    def migrate_legacy_files(self):
        """Carry over Configuration_local.hpp from the old oat_firmware setup."""
        if FIRMWARE_CONFIG.exists():
            return
        for legacy in LEGACY_FIRMWARE_CONFIGS:
            if legacy.exists():
                try:
                    shutil.copy2(legacy, FIRMWARE_CONFIG)
                    self.log(f"Migrated Configuration_local.hpp from {legacy}")
                except Exception as exc:
                    self.log(f"Could not migrate {legacy}: {exc}", logging.WARNING)
                break

    def save_config(self):
        self.cfg.update({
            "indi_host": self.host_edit.text().strip(),
            "indi_port": self.port_spin.value(),
            "device": self.device_edit.currentText().strip(),
            "ra_direction": self.ra_dir.currentData(),
            "dec_direction": self.dec_dir.currentData(),
            "home_range": self.home_range.value(),
            "accuracy_arcsec": self.accuracy.value(),
            "alt_offset_arcmin": self.alt_offset.value(),
            "az_offset_arcmin": self.az_offset.value(),
            "max_move_arcmin": self.max_move.value(),
        })
        if hasattr(self, "tabs"):
            self.cfg.update({
                "win_w": self.width(), "win_h": self.height(),
                "log_visible": bool(self.log_toggle.isChecked()),
                "log_height": self._log_height_for_config(),
                "last_tab": self.tabs.currentIndex(),
                "simple_mode": bool(self.cfg.get("simple_mode", True)),
            })
        if hasattr(self, "ra_limit_left"):
            self.cfg.update({
                "ra_limit_left_h": self.ra_limit_left.value(),
                "ra_limit_right_h": self.ra_limit_right.value(),
                "ra_physical_limit_h": self.ra_physical.value(),
                "dec_limit_down_deg": self.dec_limit_down.value(),
                "dec_limit_up_deg": self.dec_limit_up.value(),
            })
        if hasattr(self, "fw_source"):
            self.cfg.update({
                "firmware_source_dir": self.fw_source.text().strip(),
                "firmware_env": self.fw_env.currentText().strip(),
                "firmware_port": self.fw_port.text().strip(),
                "factory_reset_command": self.factory_cmd.text().strip(),
                "expected_ra_spr": self.expected_ra_spr.value(),
                "expected_dec_spr": self.expected_dec_spr.value(),
                "expected_az_spr": self.expected_az_spr.value(),
                "expected_alt_spr": self.expected_alt_spr.value(),
                "expected_autopa_version": self.expected_autopa_ver.value(),
                "custom_commands": [{"name": n.text().strip(), "command": c.text().strip()}
                                    for n, c in zip(self.custom_name_edits, self.custom_cmd_edits)],
            })
            if hasattr(self, "fw_ref"):
                ref = self.fw_ref.currentData() or self.fw_ref.currentText().strip()
                if ref:
                    self.cfg["firmware_ref"] = ref
        CONFIG_FILE.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")

    def setup_logging(self):
        self.logger = logging.getLogger("oat-helper")
        self.logger.setLevel(logging.DEBUG)
        if not self.logger.handlers:
            fp = LOG_DIR / f"oat_helper_{datetime.now():%Y%m%d_%H%M%S}.log"
            h = logging.FileHandler(fp, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.logger.addHandler(h)

    def log(self, msg, level=logging.INFO):
        self.logger.log(level, msg)
        if hasattr(self, "log_box"):
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_box.appendPlainText(f"[{ts}] {msg}")

    # ------------------------ UI ------------------------
    def change_language(self):
        """Switch UI language without restarting."""
        code = self.lang_box.currentData() or "auto"
        self.cfg["language"] = code
        load_language(code)
        translate_widget_tree(self)
        self.log(f"UI language set to '{code}' (log messages stay in English)")

    @staticmethod
    def usb_serial_present():
        """True when some serial device that could be the OAT exists here."""
        patterns = ("/dev/ttyACM*", "/dev/ttyUSB*", "/dev/ttyAMA*", "/dev/serial/by-id/*")
        for pattern in patterns:
            directory, _, glob = pattern.rpartition("/")
            try:
                if any(Path(directory).glob(glob)):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _set_status_dot(label, state, name):
        """A coloured dot plus one word; the full state goes in the tooltip.

        Spelling the state out ("INDI connected", "Mount: disconnected") ate a
        third of the status bar and pushed the buttons off narrow screens.
        """
        colour = {True: "#15803d", False: "#b91c1c", None: "#9ca3af"}[state]
        label.setText(f'<span style="color:{colour};font-size:15px">\u25cf</span> {name}')
        detail = {True: _("{name}: connected"), False: _("{name}: not connected"),
                  None: _("{name}: unknown")}[state].format(name=name)
        label.setToolTip(detail)

    def _mark_advanced(self, widget):
        """Tag a widget as advanced so simple mode can hide it."""
        if not hasattr(self, "advanced_widgets"):
            self.advanced_widgets = []
        self.advanced_widgets.append(widget)
        return widget

    def _apply_view_mode(self, simple=None):
        """Show either the three-tab session flow or everything."""
        if simple is None:
            simple = bool(self.cfg.get("simple_mode", True))
        self.cfg["simple_mode"] = bool(simple)
        for widget in getattr(self, "advanced_widgets", []):
            try:
                widget.setVisible(not simple)
            except RuntimeError:
                pass
        if simple and hasattr(self, "ra_cal_group"):
            self.ra_cal_group.hide()
        if hasattr(self, "tabs"):
            current = self.tabs.currentWidget()
            while self.tabs.count():
                self.tabs.removeTab(0)
            local = self.usb_serial_present() or bool(self.cfg.get("always_show_firmware_tab"))
            for widget, title, advanced in self.tab_specs:
                if advanced and simple:
                    continue
                if widget is getattr(self, "firmware_tab", None) and not local:
                    # Build/Flash need the USB cable; hide the page on a remote
                    # machine instead of letting it fail at the last step.
                    continue
                self.tabs.addTab(widget, title)
            index = self.tabs.indexOf(current)
            self.tabs.setCurrentIndex(index if index >= 0 else 0)
        translate_widget_tree(self)
        if hasattr(self, "advanced_toggle"):
            self.advanced_toggle.blockSignals(True)
            self.advanced_toggle.setChecked(not simple)
            self.advanced_toggle.setText(_("Advanced ▾") if not simple else _("Advanced ▸"))
            self.advanced_toggle.blockSignals(False)

    def _hint(self, text, warn=False):
        """Small, de-emphasized help text."""
        lbl = QtWidgets.QLabel(text)
        lbl.setWordWrap(True)
        lbl.setProperty("hint", "warn" if warn else "true")
        return lbl

    def _demote_help_labels(self, widget):
        """Render every wrapped explanatory label on a page as small help text.

        Long bold paragraphs used to dominate each tab and pushed the actual
        controls off the bottom of short VNC desktops.  Live status labels are
        excluded so they keep their green/amber/red colouring.
        """
        protected = {id(getattr(self, name)) for name in _STATUS_LABEL_ATTRS if hasattr(self, name)}
        for lbl in widget.findChildren(QtWidgets.QLabel):
            if id(lbl) in protected or not lbl.wordWrap() or lbl.property("hint"):
                continue
            lbl.setStyleSheet("")
            lbl.setProperty("hint", "true")

    def _as_page(self, widget):
        """Tighten a tab's layout and make it scrollable."""
        lay = widget.layout()
        if lay is not None:
            lay.setContentsMargins(8, 6, 8, 6)
            lay.setSpacing(6)
        self._demote_help_labels(widget)
        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QtWidgets.QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        area.setWidget(widget)
        return area

    def build_ui(self):
        self.setStyleSheet(STYLESHEET)
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        # --- compact status bar: one row, always visible ---------------------
        header = QtWidgets.QFrame()
        header.setFrameShape(QtWidgets.QFrame.StyledPanel)
        hb = QtWidgets.QHBoxLayout(header)
        hb.setContentsMargins(8, 4, 8, 4)
        hb.setSpacing(10)
        self.conn_status = QtWidgets.QLabel(); self.conn_status.setProperty("role", "value")
        self.mount_status = QtWidgets.QLabel(); self.mount_status.setProperty("role", "value")
        self._set_status_dot(self.conn_status, None, "INDI")
        self._set_status_dot(self.mount_status, None, "Mount")
        self.fw_label = QtWidgets.QLabel("FW: -"); self.fw_label.setProperty("role", "value")
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setToolTip(_("Connect to or disconnect from the INDI server"))
        self.connect_btn.clicked.connect(self.toggle_connection)
        self.settings_btn = QtWidgets.QToolButton()
        self.settings_btn.setText(_("⚙ Settings ▸"))
        self.settings_btn.setToolTip(_("INDI host and port, mount device name, UI language"))
        self.settings_btn.setCheckable(True)
        self.advanced_toggle = QtWidgets.QToolButton()
        self.advanced_toggle.setText(_("Advanced ▸"))
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolTip(
            "Turning this on shows the Controller, Monitor, Axis Calibration and Diagnostics tabs plus advanced items such as Park and offsets.\n"
            "It is not needed for normal imaging.")
        self.advanced_toggle.toggled.connect(lambda on: self._apply_view_mode(simple=not on))
        hb.addWidget(self.conn_status)
        hb.addWidget(self._vline())
        hb.addWidget(self.mount_status)
        hb.addWidget(self._vline())
        hb.addWidget(self.fw_label)
        hb.addStretch(1)
        hb.addWidget(self.advanced_toggle)
        hb.addWidget(self.settings_btn)
        hb.addWidget(self.connect_btn)
        outer.addWidget(header)

        # --- connection settings: hidden until needed ------------------------
        self.conn_settings = QtWidgets.QWidget()
        cg = QtWidgets.QHBoxLayout(self.conn_settings)
        cg.setContentsMargins(8, 0, 8, 0); cg.setSpacing(6)
        self.host_edit = QtWidgets.QLineEdit(self.cfg["indi_host"]); self.host_edit.setMaximumWidth(140)
        self.port_spin = QtWidgets.QSpinBox(); self.port_spin.setRange(1, 65535); self.port_spin.setValue(int(self.cfg["indi_port"])); self.port_spin.setMaximumWidth(90)
        # A combo filled from the devices the INDI server announces; still
        # editable so a name can be typed before anything is connected.
        self.device_edit = QtWidgets.QComboBox()
        self.device_edit.setEditable(True)
        self.device_edit.setMinimumWidth(200)
        self.device_edit.addItem(self.cfg["device"])
        self.device_edit.setCurrentText(self.cfg["device"])
        self.device_edit.activated.connect(lambda _i: self.change_device())
        cg.addWidget(QtWidgets.QLabel("Host")); cg.addWidget(self.host_edit)
        cg.addWidget(QtWidgets.QLabel("Port")); cg.addWidget(self.port_spin)
        cg.addWidget(QtWidgets.QLabel("Mount device")); cg.addWidget(self.device_edit, 1)
        rescan = QtWidgets.QPushButton("Rescan")
        rescan.setToolTip("Ask the INDI server for its device list again")
        rescan.clicked.connect(self.rescan_devices)
        cg.addWidget(rescan)
        self.lang_box = QtWidgets.QComboBox()
        self.lang_box.addItem(_("Language: auto"), "auto")
        for code, label in (("en", "English"), ("ko", "한국어")):
            if code == "en" or code in available_languages():
                self.lang_box.addItem(label, code)
        index = self.lang_box.findData(self.cfg.get("language", "auto"))
        self.lang_box.setCurrentIndex(max(0, index))
        self.lang_box.currentIndexChanged.connect(self.change_language)
        cg.addWidget(self.lang_box)
        self.conn_settings.setVisible(False)
        self.settings_btn.toggled.connect(self.conn_settings.setVisible)
        self.settings_btn.toggled.connect(
            lambda on: self.settings_btn.setText(_("⚙ Settings ▾") if on else _("⚙ Settings ▸")))
        outer.addWidget(self.conn_settings)

        # --- tabs and log share the remaining height, user-resizable ---------
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setChildrenCollapsible(False)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setDocumentMode(True)
        self.wizard_tab = self._as_page(self.make_wizard_tab())
        self.home_tab = self._as_page(self.make_home_tab())
        self.pa_tab = self._as_page(self.make_pa_tab())
        self.mini_tab = self._as_page(self.make_mini_tab())
        self.monitor_tab = self._as_page(self.make_monitor_tab())
        self.axis_tab = self._as_page(self.make_axis_cal_tab())
        self.diag_tab = self._as_page(self.make_diag_tab())
        self.fwconfig_tab = self._as_page(self.make_config_tab())
        self.firmware_tab = self._as_page(self.make_firmware_tab())
        self.tab_specs = [
            (self.wizard_tab, "Session setup", False),
            (self.home_tab, "Home", False),
            (self.pa_tab, "AutoPA", False),
            (self.mini_tab, "Controller", True),
            (self.monitor_tab, "Monitor", True),
            (self.axis_tab, "Axis cal", True),
            (self.diag_tab, "Diag", True),
            (self.fwconfig_tab, "FW config", True),
            (self.firmware_tab, "Firmware", True),
        ]
        for tab, name, _adv in self.tab_specs:
            self.tabs.addTab(tab, name)
        splitter.addWidget(self.tabs)

        log_wrap = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(log_wrap)
        lv.setContentsMargins(0, 0, 0, 0); lv.setSpacing(2)
        log_head = QtWidgets.QHBoxLayout(); log_head.setSpacing(6)
        self.log_toggle = QtWidgets.QToolButton()
        self.log_toggle.setText(_("▾ Log"))
        self.log_toggle.setCheckable(True); self.log_toggle.setChecked(True)
        clear_log = QtWidgets.QPushButton("Clear"); clear_log.setMaximumWidth(70)
        log_head.addWidget(self.log_toggle); log_head.addStretch(1); log_head.addWidget(clear_log)
        lv.addLayout(log_head)
        self.log_box = QtWidgets.QPlainTextEdit(); self.log_box.setReadOnly(True); self.log_box.setMaximumBlockCount(1000)
        self.log_box.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.log_box.setMinimumHeight(56)
        clear_log.clicked.connect(self.log_box.clear)
        lv.addWidget(self.log_box, 1)
        self.log_toggle.toggled.connect(self._on_log_toggled)
        splitter.addWidget(log_wrap)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self.splitter = splitter
        outer.addWidget(splitter, 1)

        # Restore the layout this user last used; a collapsed log gives the
        # tab pages ~110 px more, which matters on a 720 px VNC desktop.
        log_wrap.setMinimumHeight(self.log_toggle.sizeHint().height() + 6)
        try:
            self.tabs.setCurrentIndex(int(self.cfg.get("last_tab", 0)))
        except Exception:
            pass
        self._fit_to_screen()
        log_h = min(self._log_height_for_config(), max(80, self.height() // 3))
        splitter.setSizes([max(200, self.height() - log_h - 90), log_h])
        translate_widget_tree(self)
        self._apply_view_mode()
        if not bool(self.cfg.get("log_visible", True)):
            self.log_toggle.setChecked(False)

    def _log_height_for_config(self):
        """Clamp the remembered log height so it can never eat the whole window."""
        stored = int(self.cfg.get("log_height", 110) or 110)
        if hasattr(self, "splitter") and self.log_toggle.isChecked():
            sizes = self.splitter.sizes()
            if len(sizes) > 1 and sizes[1] > 40:
                stored = sizes[1]
        return int(max(80, min(stored, 320)))

    def _on_log_toggled(self, on):
        """Collapse/expand the log and give the freed height back to the tabs."""
        self.log_box.setVisible(on)
        self.log_toggle.setText(("▾ " if on else "▸ ") + _("Log"))
        if not hasattr(self, "splitter"):
            return
        sizes = self.splitter.sizes()
        total = sum(sizes) or self.height()
        if on:
            log_h = min(self._log_height_for_config(), max(80, total // 3))
            self.splitter.setSizes([max(160, total - log_h), log_h])
        else:
            if sizes[1] > 40:
                self.cfg["log_height"] = min(sizes[1], 320)
            head = self.log_toggle.sizeHint().height() + 6
            self.splitter.setSizes([max(160, total - head), head])

    @staticmethod
    def _vline():
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.VLine)
        line.setFrameShadow(QtWidgets.QFrame.Sunken)
        return line

    def _fit_to_screen(self):
        """Never open taller than the desktop (VNC sessions are often 720 px)."""
        want_w = int(self.cfg.get("win_w") or 0) or 1060
        want_h = int(self.cfg.get("win_h") or 0) or 660
        try:
            geo = QtWidgets.QApplication.primaryScreen().availableGeometry()
            max_w, max_h = geo.width() - 40, geo.height() - 60
        except Exception:
            max_w, max_h = want_w, want_h
        self.resize(max(860, min(want_w, max_w)), max(420, min(want_h, max_h)))

    def make_wizard_tab(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)

        intro = self._hint(
            "After power-on, work through (1) to (5) in order. Keep the OAT base pointing north and do not turn it by hand - "
            "AutoPA moves motors only during the correction step, and use Slew in KStars when pointing south.")
        v.addWidget(intro)

        box = QtWidgets.QGroupBox("Session setup")
        g = QtWidgets.QGridLayout(box)

        self.wiz_conn_status = QtWidgets.QLabel("○ Not checked")
        self.wiz_ra_status = QtWidgets.QLabel("○ Not run")
        self.wiz_dec_status = QtWidgets.QLabel("○ Not run")
        self.wiz_paa_status = QtWidgets.QLabel("○ Not run")
        self.wiz_ready_status = QtWidgets.QLabel("○ Not ready")

        b_conn = QtWidgets.QPushButton("Connect / check INDI")
        b_ra = QtWidgets.QPushButton("Run RA AutoHome")
        b_dec = QtWidgets.QPushButton("Home fine adjustment / SET HOME")
        b_dec.setObjectName("primary")
        b_paa_help = QtWidgets.QPushButton("View PAA guide")
        b_paa_wait = QtWidgets.QPushButton("Wait for auto correction")

        b_conn.clicked.connect(self.wizard_connect)
        b_ra.clicked.connect(self.wizard_start_ra)
        b_dec.clicked.connect(self.wizard_start_dec)
        b_paa_help.clicked.connect(self.show_paa_guidance)
        b_paa_wait.clicked.connect(self.wizard_start_autopa)

        rows = [
            ("① INDI connection", self.wiz_conn_status, b_conn),
            ("② Homing — RA AutoHome", self.wiz_ra_status, b_ra),
            ("③ Final Home — RA/DEC + SET HOME", self.wiz_dec_status, b_dec),
        ]
        for r,(name,status,button) in enumerate(rows):
            g.addWidget(QtWidgets.QLabel(name),r,0); g.addWidget(status,r,1); g.addWidget(button,r,2,1,2)

        g.addWidget(QtWidgets.QLabel("④ Ekos PAA + AutoPA"),3,0)
        g.addWidget(self.wiz_paa_status,3,1)
        g.addWidget(b_paa_help,3,2); g.addWidget(b_paa_wait,3,3)
        g.addWidget(QtWidgets.QLabel("⑤ Ready"),4,0); g.addWidget(self.wiz_ready_status,4,1,1,3)

        paa_hint = self._hint(
            "View PAA guide -> Wait for auto correction -> Slew to a southern star field -> In the Ekos Align Polar Alignment Assistant, "
            "measure with Auto Slew ON, then start Refresh. When a new Refresh result arrives, ALT/AZ are corrected automatically. "
            "Only the RA/DEC and AutoPA motors move here; do not touch the base.")
        g.addWidget(paa_hint,5,0,1,4)
        v.addWidget(box)

        # Pre-session checklist: the physical checks the software cannot verify.
        chk = QtWidgets.QGroupBox("Field checklist")
        cv = QtWidgets.QVBoxLayout(chk); cv.setSpacing(2)
        self.site_angle_label = QtWidgets.QLabel("Waiting for the site from Ekos...")
        self.site_angle_label.setWordWrap(True)
        self.site_angle_label.setStyleSheet("font-weight:700;font-size:13px")
        self.site_note_label = self._hint("")
        cv.addWidget(self.site_angle_label)
        cv.addWidget(self.site_note_label)
        self.checklist_boxes = []
        done_items = set(self.cfg.get("checklist_done") or [])
        for item in (self.cfg.get("checklist") or []):
            cb = QtWidgets.QCheckBox(str(item))
            cb.setChecked(str(item) in done_items)
            cb.toggled.connect(self._save_checklist_state)
            self.checklist_boxes.append(cb)
            cv.addWidget(cb)
        row = QtWidgets.QHBoxLayout()
        reset_chk = QtWidgets.QPushButton("Clear all"); reset_chk.clicked.connect(self.reset_checklist)
        edit_chk = QtWidgets.QPushButton("Edit items"); edit_chk.clicked.connect(self.edit_checklist)
        row.addWidget(reset_chk); row.addWidget(edit_chk); row.addStretch(1)
        cv.addLayout(row)
        v.addWidget(chk)
        v.addStretch(1)
        return w


    def site_angles(self):
        """Polar-axis angles for the current site.

        The RA (polar) axis altitude equals the observer's latitude, and the
        base has to face true north (south in the southern hemisphere).  A
        compass reads magnetic north, so the magnetic declination - which the
        user enters once for their site - is what turns that into a bearing
        they can actually walk out with.
        """
        geo = self.indi.last_number_values.get("GEOGRAPHIC_COORD") or {}
        lat_key = self._find_ci_key(geo, exact=["LAT"], contains=["LAT"])
        lon_key = self._find_ci_key(geo, exact=["LONG", "LON", "LONGITUDE"], contains=["LONG", "LON"])
        if lat_key is None or lon_key is None:
            return None
        lat = float(geo[lat_key])
        lon = float(geo[lon_key])
        if lon > 180.0:
            lon -= 360.0
        declination = self.cfg.get("magnetic_declination_deg")
        bearing = None
        if declination is not None:
            target = 0.0 if lat >= 0 else 180.0
            bearing = (target - float(declination)) % 360.0
        return {"lat": lat, "lon": lon, "altitude": abs(lat),
                "pole": "north" if lat >= 0 else "south",
                "declination": declination, "bearing": bearing}

    def update_site_angles(self):
        """Show the two numbers the mount has to be set up with.

        The polar axis altitude is the observer's latitude, and the base faces
        the celestial pole - true north, not the magnetic north a compass shows.
        """
        if not hasattr(self, "site_angle_label"):
            return
        info = self.site_angles()
        if not info:
            self.site_angle_label.setText(_("Waiting for the site from Ekos..."))
            self.site_note_label.setText(
                _("Connect the mount in Ekos; the site it reports is used to work out the angles."))
            return
        pole = _("true north") if info["pole"] == "north" else _("true south")
        self.site_angle_label.setText(
            _("Set the polar (RA) axis to {alt:.1f}° and aim the base at {pole}").format(
                alt=info["altitude"], pole=pole))
        lat = f"{abs(info['lat']):.3f}°{'N' if info['lat'] >= 0 else 'S'}"
        lon = f"{abs(info['lon']):.3f}°{'E' if info['lon'] >= 0 else 'W'}"
        note = _("Site {lat} {lon} (from Ekos) - the altitude is your latitude. "
                 "True north is not compass north.").format(lat=lat, lon=lon)
        if info["bearing"] is not None:
            # Only when the optional magnetic_declination_deg config key is set;
            # declination cannot be derived from latitude/longitude alone.
            note += " " + _("With your declination, true north is {bearing:.1f}° on a compass.").format(
                bearing=info["bearing"])
        self.site_note_label.setText(note)

    def _save_checklist_state(self):
        self.cfg["checklist_done"] = [cb.text() for cb in getattr(self, "checklist_boxes", []) if cb.isChecked()]

    def reset_checklist(self):
        for cb in getattr(self, "checklist_boxes", []):
            cb.setChecked(False)
        self._save_checklist_state()

    def edit_checklist(self):
        """Edit the checklist items.

        The boxes on screen are already translated, so the editor starts from
        what is actually shown rather than the English defaults kept in the
        config - otherwise a Korean UI opens an English editor.
        """
        shown = [box.text() for box in getattr(self, "checklist_boxes", []) if not box.isHidden()]
        current = "\n".join(shown or (self.cfg.get("checklist") or []))
        text, ok = QtWidgets.QInputDialog.getMultiLineText(
            self, _("Edit checklist"), _("Enter one item per line:"), current)
        if not ok:
            return
        items = [line.strip() for line in text.splitlines() if line.strip()]
        self.cfg["checklist"] = items
        self.cfg["checklist_done"] = []
        self.save_config()
        for box in getattr(self, "checklist_boxes", []):
            box.setChecked(False)
            box.setVisible(False)
        for box, item in zip(self.checklist_boxes, items):
            box.setText(item)
            box.setVisible(True)
        QtWidgets.QMessageBox.information(
            self, _("Edit checklist"), _("Saved."))

    def _set_wizard_label(self, label, text, good=False, warn=False):
        if not hasattr(self, label):
            return
        obj = getattr(self, label)
        obj.setText(_(text))
        if good:
            obj.setStyleSheet("font-weight:bold;color:green")
        elif warn:
            obj.setStyleSheet("font-weight:bold;color:#b36b00")
        else:
            obj.setStyleSheet("")

    def update_wizard_status(self):
        connected = bool(self.indi.running)
        self._set_wizard_label("wiz_conn_status", "✓ Connected" if connected else "○ Not connected", good=connected)
        self._set_wizard_label("wiz_ra_status", "✓ RA AutoHome done - saved RA correction applied" if self.wizard_ra_done else ("... RA AutoHome running" if self.home_busy and getattr(self, "home_active_axis", "") == "RA" else "○ Not run"), good=self.wizard_ra_done, warn=self.home_busy and not self.wizard_ra_done)
        dec_text = "✓ Final Home fixed - RA/DEC = 0" if self.wizard_dec_done else ("... adjusting DEC - finish with SET HOME" if self.dec_manual_active else "○ Not run")
        self._set_wizard_label("wiz_dec_status", dec_text, good=self.wizard_dec_done, warn=self.dec_manual_active and not self.wizard_dec_done)
        if self.wizard_pa_done:
            paa_text = "✓ PAA / AutoPA done"
        elif self.autopa_running:
            paa_text = "... waiting for a new PAA Refresh / correcting"
        elif self.wizard_paa_guidance_seen:
            paa_text = "○ Guide read - start waiting for automatic correction"
        else:
            paa_text = "○ Not run"
        self._set_wizard_label("wiz_paa_status", paa_text, good=self.wizard_pa_done, warn=self.autopa_running)
        ready = connected and self.wizard_ra_done and self.wizard_dec_done and self.wizard_pa_done
        self._set_wizard_label("wiz_ready_status", "✓ Ready to image - now Target Slew -> Plate Solve -> Guide -> Capture" if ready else "○ Complete Home and Polar Alignment", good=ready)

    def wizard_connect(self):
        if self.indi.running:
            self.log("Wizard: INDI already connected.")
            self.update_wizard_status(); return
        self.connect_indi()

    def wizard_start_ra(self):
        if not self.indi.running:
            self.log("Wizard: connect INDI first.", logging.WARNING); return
        self.wizard_ra_done = False
        self.update_wizard_status()
        self.start_home(["RA"])

    def wizard_start_dec(self):
        if not self.indi.running:
            self.log("Wizard: connect INDI first.", logging.WARNING); return
        if not self.wizard_ra_done:
            self.log("Wizard: completing RA AutoHome first is recommended.", logging.WARNING)
        if not self.dec_manual_active:
            self.start_dec_manual_home()
        if hasattr(self, "tabs") and hasattr(self, "home_tab"):
            self.tabs.setCurrentWidget(self.home_tab)

    def show_paa_guidance(self):
        self.wizard_paa_guidance_seen = True
        self.update_wizard_status()
        text = (
            "1) Leave the OAT base pointing north and do not rotate the whole OAT by hand. AutoPA moves it with motors during automatic correction only.\n\n"
            "2) In the KStars sky map, pick a star or star field actually visible through the southern window and press [Slew]. "
            "You issue the Slew command and the OAT moves RA/DEC automatically.\n\n"
            "3) In Ekos Guide, turn Loop on, check that several stars are visible and focus the guider.\n\n"
            "4) Open Ekos > Align > Polar Alignment Assistant. You do not need to see the pole. "
            "Turn Auto Slew on and start with a rotation of about 20°. For East/West, pick the side that is not blocked by a window frame or wall during the two RA rotations.\n\n"
            "5) Once the PAA measurement starts, Ekos rotates the RA axis twice and plate-solves three frames.\n\n"
            "6) Start Refresh in the PAA correction screen. If you already pressed [Wait for auto correction] in OAT Tools, "
            "reads the new Refresh result and moves AutoPA ALT/AZ automatically.\n\n"
            "7) When a new Refresh result is at or below the target accuracy, OAT Tools shows Ready.")
        QtWidgets.QMessageBox.information(self, _("Starting the Ekos PAA"), text)

    def wizard_start_autopa(self):
        if not self.indi.running:
            self.log("Wizard: connect INDI first.", logging.WARNING); return
        if not self.wizard_ra_done or not self.wizard_dec_done:
            self.log("Wizard: complete RA AutoHome -> DEC adjustment -> SET HOME first.", logging.WARNING); return
        if not self.wizard_paa_guidance_seen:
            self.show_paa_guidance()
        self.wizard_pa_done = False
        self.start_autopa_watch()
        self.update_wizard_status()

    def make_home_tab(self):
        """Compact, single-page Homing workflow.

        Normal use is deliberately linear and matches the physical workflow:
        sync time/location (HA) -> RA Hall AutoHome -> optional RA/DEC fine
        adjustment -> one final Set Home.  The final Set Home is firmware
        :SHP#, so the current positions of both axes become logical zero
        together.  No DEC homing offset is required for this sensorless DEC
        workflow.
        """
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        v.setSpacing(8)

        intro = self._hint(
            "Order: ⓪ update HA → ① RA AutoHome → ② fine adjustment if needed → ③ SET HOME. "
            "The RA/DEC at the moment of SET HOME become Home(0) together. RA AutoHome also ends with the firmware setHome(), so "
            "If you aligned DEC first, leave it as it is.")
        v.addWidget(intro)

        # --- 0. Time / location / HA ---------------------------------------
        ha_row = QtWidgets.QHBoxLayout(); ha_row.setSpacing(8)
        step0 = QtWidgets.QLabel("⓪ Time/site → HA"); step0.setStyleSheet("font-weight:600")
        self.ha_status = QtWidgets.QLabel("Waiting for the INDI site/time")
        self.ha_sync_btn = QtWidgets.QPushButton("Update HA + apply")
        self.ha_sync_btn.clicked.connect(self.sync_ha_time_location)
        self.clock_fix_btn = QtWidgets.QPushButton("Fix mount clock")
        self.clock_fix_btn.setToolTip(
            "Writes date, local time, UTC offset, site and sidereal time straight to the mount.\n"
            "Use it when the log reports a sidereal time mismatch; run SET HOME again afterwards.")
        self.clock_fix_btn.clicked.connect(self.repair_mount_clock)
        ha_row.addWidget(step0); ha_row.addWidget(self.ha_status, 1)
        ha_row.addWidget(self.clock_fix_btn); ha_row.addWidget(self.ha_sync_btn)
        v.addLayout(ha_row)

        # --- 1. RA AutoHome -------------------------------------------------
        auto = QtWidgets.QGroupBox("① RA AutoHome — Hall Sensor")
        ag = QtWidgets.QGridLayout(auto)
        ag.setHorizontalSpacing(8); ag.setVerticalSpacing(5)
        self.ra_dir = QtWidgets.QComboBox(); self.ra_dir.addItem("R / CCW", "R"); self.ra_dir.addItem("L / CW", "L")
        self.ra_dir.setCurrentIndex(max(0, self.ra_dir.findData(self.cfg["ra_direction"])))
        # Hidden compatibility widget: DEC Hall AutoHome is not used on this build.
        self.dec_dir = QtWidgets.QComboBox(); self.dec_dir.addItem("U / Up", "U"); self.dec_dir.addItem("D / Down", "D")
        self.dec_dir.setCurrentIndex(max(0, self.dec_dir.findData(self.cfg.get("dec_direction", "U")))); self.dec_dir.hide()
        self.home_range = QtWidgets.QSpinBox(); self.home_range.setRange(5, 75); self.home_range.setSuffix("°"); self.home_range.setValue(int(self.cfg["home_range"]))
        self.ra_home_btn = QtWidgets.QPushButton("Run RA AutoHome")
        self.ra_home_btn.clicked.connect(lambda: self.start_home(["RA"]))
        self.home_status = QtWidgets.QLabel("RA: idle")
        self.home_status.setStyleSheet("font-weight:600")

        ag.addWidget(QtWidgets.QLabel("Search direction"),0,0); ag.addWidget(self.ra_dir,0,1)
        ag.addWidget(QtWidgets.QLabel("Search range"),0,2); ag.addWidget(self.home_range,0,3)
        ag.addWidget(self.ra_home_btn,0,4)
        ag.addWidget(self.home_status,1,0,1,5)

        # RA Hall->mechanical-Home correction is persistent and AutoHome applies
        # it automatically.  Keep read/write controls compact on one row.
        # RA Hall->mechanical-Home correction is persistent and AutoHome applies
        # it automatically, so the manual read/write controls live in the
        # collapsible advanced section further down instead of the main flow.
        self.ra_offset = QtWidgets.QSpinBox(); self.ra_offset.setRange(-2000000, 2000000)
        self.dec_offset = QtWidgets.QSpinBox(); self.dec_offset.setRange(-2000000, 2000000); self.dec_offset.hide()
        v.addWidget(auto)

        # --- 2. Fine adjustment --------------------------------------------
        adjust = QtWidgets.QGroupBox("② Home fine adjustment — only when needed")
        jg = QtWidgets.QGridLayout(adjust)
        jg.setHorizontalSpacing(6); jg.setVerticalSpacing(5)
        self.dec_manual_status = QtWidgets.QLabel(
            "If the real mechanical Home differs after RA AutoHome, adjust RA/DEC and then press SET HOME.")
        self.dec_manual_status.setWordWrap(True)
        jg.addWidget(self.dec_manual_status,0,0,1,8)

        jg.addWidget(QtWidgets.QLabel("RA"),1,0)
        for col, deg in enumerate((-15, -5, -1, 1, 5, 15), start=1):
            arrow = "←" if deg < 0 else "→"
            b = QtWidgets.QPushButton(f"{arrow} {deg:+g}°")
            b.setToolTip(_("RA fine adjustment (firmware :XGR# degree conversion)"))
            b.clicked.connect(lambda _=False, x=deg: self.home_ra_jog_move(x))
            jg.addWidget(b,1,col)
        jg.addWidget(QtWidgets.QLabel("degree"),1,7)

        jg.addWidget(QtWidgets.QLabel("DEC"),2,0)
        for col, deg in enumerate((15, 5, 1), start=1):
            b = QtWidgets.QPushButton(f"↑ +{deg}°")
            b.clicked.connect(lambda _=False, x=deg: self.dec_manual_jog_move(+x))
            jg.addWidget(b,2,col)
        for col, deg in enumerate((1, 5, 15), start=4):
            b = QtWidgets.QPushButton(f"↓ -{deg}°")
            b.clicked.connect(lambda _=False, x=deg: self.dec_manual_jog_move(-x))
            jg.addWidget(b,2,col)
        v.addWidget(adjust)

        # --- 3. Final Home --------------------------------------------------
        final = QtWidgets.QGroupBox("③ Final Home")
        fg = QtWidgets.QGridLayout(final)
        self.dec_manual_save_btn = QtWidgets.QPushButton("SET HOME (current RA/DEC)")
        self.dec_manual_save_btn.setMinimumHeight(34)
        self.dec_manual_save_btn.setObjectName("primary")
        self.dec_manual_save_btn.clicked.connect(self.finish_dec_manual_home)
        self.dec_manual_gohome_btn = QtWidgets.QPushButton("GO TO HOME")
        self.dec_manual_gohome_btn.setMinimumHeight(34)
        self.dec_manual_gohome_btn.clicked.connect(self.mini_goto_home)
        self.stop_all_btn = QtWidgets.QPushButton("STOP")
        self.stop_all_btn.setObjectName("danger")
        self.stop_all_btn.clicked.connect(self.emergency_stop)
        fg.addWidget(self.dec_manual_save_btn,0,0,1,2)
        fg.addWidget(self.dec_manual_gohome_btn,0,2)
        fg.addWidget(self.stop_all_btn,0,3)
        self.dec_restore_btn = QtWidgets.QPushButton("Restore DEC Home")
        self.dec_restore_btn.setToolTip(
            "Re-applies the 'power-on position -> Home' DEC travel recorded at the last SET HOME, then runs SET HOME.\n"
            "Use this only right after power-on, when DEC is at the same physical position as last time (parked, for example).")
        self.dec_restore_btn.clicked.connect(self.restore_saved_dec_home)
        fg.addWidget(self.dec_restore_btn,1,0,1,2)
        park_btn = QtWidgets.QPushButton("PARK")
        park_btn.setToolTip(_("Firmware Park (:hP#) - move to Home, park and stop tracking."))
        park_btn.clicked.connect(self.park_mount)
        unpark_btn = QtWidgets.QPushButton("UNPARK")
        unpark_btn.setToolTip(_("Firmware Unpark (:hU#) - resume tracking."))
        unpark_btn.clicked.connect(self.unpark_mount)
        fg.addWidget(self._mark_advanced(park_btn),1,2,1,2)
        opt_box = QtWidgets.QWidget()
        opt = QtWidgets.QHBoxLayout(opt_box)
        opt.setContentsMargins(0,0,0,0)
        self.dec_offset_eeprom = QtWidgets.QCheckBox("Store the DEC Home offset on the mount EEPROM")
        self.dec_offset_eeprom.setChecked(self.cfg.get("dec_home_offset_mode") == "eeprom")
        self.dec_offset_eeprom.setToolTip(
            "When enabled, SET HOME stores the 'power-on -> Home' travel on the mount with :XSHD# (kept even if the tool is reinstalled).\n"
            "Turning it off clears the value to zero so firmware/Ekos Park stops exactly at Home.")
        self.dec_offset_eeprom.toggled.connect(
            lambda on: self.cfg.__setitem__("dec_home_offset_mode", "eeprom" if on else "clear"))
        self.autohome_on_connect = QtWidgets.QCheckBox("RA AutoHome on connect")
        self.autohome_on_connect.setChecked(bool(self.cfg.get("autohome_ra_on_connect")))
        self.autohome_on_connect.toggled.connect(lambda on: self.cfg.__setitem__("autohome_ra_on_connect", bool(on)))
        self.restore_on_connect = QtWidgets.QCheckBox("Restore DEC Home on connect")
        self.restore_on_connect.setChecked(bool(self.cfg.get("restore_dec_home_on_connect")))
        self.restore_on_connect.toggled.connect(lambda on: self.cfg.__setitem__("restore_dec_home_on_connect", bool(on)))
        opt.addWidget(self.dec_offset_eeprom); opt.addStretch(1)
        opt.addWidget(self.autohome_on_connect); opt.addWidget(self.restore_on_connect); opt.addWidget(unpark_btn)
        fg.addWidget(self._mark_advanced(opt_box),2,0,1,4)
        rel_box = QtWidgets.QWidget()
        rel = QtWidgets.QHBoxLayout(rel_box); rel.setContentsMargins(0,0,0,0); rel.setSpacing(6)
        self.release_dec = QtWidgets.QDoubleSpinBox(); self.release_dec.setRange(-90,90); self.release_dec.setDecimals(1)
        self.release_dec.setSuffix("°"); self.release_dec.setValue(float(self.cfg.get("release_dec_deg",-30.0)))
        self.release_dec.setToolTip(_(
            "DEC angle relative to Home, with the same sign as the DEC ↑/↓ buttons above: if ↑ raises the tube, "
            "use a negative value to lower it.\n"
            "Easiest way: jog DEC to the position you want, then press 'Save current position'."))
        self.release_ra = QtWidgets.QDoubleSpinBox(); self.release_ra.setRange(-90,90); self.release_ra.setDecimals(1)
        self.release_ra.setSuffix("°"); self.release_ra.setValue(float(self.cfg.get("release_ra_deg",0.0)))
        self.release_ra.setToolTip(_("RA angle relative to Home. Normally leave this at 0."))
        save_rel = QtWidgets.QPushButton("Save current position")
        save_rel.setToolTip(_("Records the current position as the shutdown position."))
        save_rel.clicked.connect(self.save_release_position_here)
        self.release_btn = QtWidgets.QPushButton("Move to shutdown position")
        self.release_btn.setMinimumHeight(30)
        self.release_btn.setToolTip(
            "Moves to Home and then lowers DEC by the configured angle so the camera's weight does not hang on the RA ring.\n"
            "The DEC Home restore value for the next session is also updated to the reverse of this move.")
        self.release_btn.clicked.connect(self.move_to_release_position)
        rel.addWidget(QtWidgets.QLabel("Shutdown position  DEC")); rel.addWidget(self.release_dec)
        rel.addWidget(QtWidgets.QLabel("RA")); rel.addWidget(self.release_ra)
        rel.addWidget(save_rel); rel.addWidget(self.release_btn, 1)
        fg.addWidget(rel_box,3,0,1,4)

        note = self._hint(
            "SET HOME is firmware :SHP# (the same routine as the LCD 'Set home pos?'). A sensorless DEC is reset when power is removed. "
            "Shutdown procedure: GO TO HOME -> 'Move to shutdown position' -> power off. In the next session, use 'Restore DEC Home' to "
            "return to Home, then run SET HOME.")
        note.setToolTip(
            "SET HOME sets the current RA/DEC to logical 0 at once, and success is verified by reading :GX# RA=0/DEC=0 rather than by the reply character.\n"
            "The DEC XSHD offset is not used, and any leftover value is cleared to zero so that Ekos Park stops exactly at this Home.\n"
            "GO TO HOME (:hF#) returns both axes to this Home(0) within the same power session.")
        fg.addWidget(note,4,0,1,4)
        v.addWidget(final)
        self._update_dec_restore_label()

        # --- Advanced RA correction calibration ----------------------------
        adv_btn = QtWidgets.QToolButton()
        adv_btn.setText(_("▸ Advanced RA Correction calibration"))
        adv_btn.setCheckable(True)
        adv_btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        v.addWidget(self._mark_advanced(adv_btn))

        self.ra_cal_group = QtWidgets.QGroupBox("RA Correction Calibration (Hall Sensor)")
        cg = QtWidgets.QGridLayout(self.ra_cal_group)
        self.ra_cal_label = QtWidgets.QLabel("RA: idle")
        self.dec_cal_label = QtWidgets.QLabel("DEC: no Hall sensor"); self.dec_cal_label.hide()
        read_btn = QtWidgets.QPushButton("Read Correction"); read_btn.clicked.connect(self.read_offsets)
        save_ra = QtWidgets.QPushButton("Save + verify Correction"); save_ra.clicked.connect(lambda: self.save_offset("RA"))
        cg.addWidget(QtWidgets.QLabel("RA Correction (step)"),0,0); cg.addWidget(self.ra_offset,0,1)
        cg.addWidget(read_btn,0,2,1,2); cg.addWidget(save_ra,0,4,1,2)
        ra_start = QtWidgets.QPushButton("Start - Correction=0 -> Hall centre")
        ra_start.clicked.connect(lambda: self.start_offset_cal("RA"))
        cg.addWidget(ra_start,1,0,1,3); cg.addWidget(self.ra_cal_label,1,3,1,3)
        for i, n in enumerate((-500, -100, -10, 10, 100, 500)):
            b = QtWidgets.QPushButton(f"{n:+d}")
            b.clicked.connect(lambda _=False, x=n: self.cal_jog(x))
            cg.addWidget(b,2,i)
        self.cal_save_btn = QtWidgets.QPushButton("Save as RA Correction + re-verify")
        self.cal_cancel_btn = QtWidgets.QPushButton("Cancel / restore the previous Correction")
        self.cal_save_btn.clicked.connect(self.finish_offset_cal)
        self.cal_cancel_btn.clicked.connect(self.cancel_offset_cal)
        cg.addWidget(self.cal_save_btn,3,0,1,4); cg.addWidget(self.cal_cancel_btn,3,4,1,2)
        self.ra_cal_group.setVisible(False)
        self._mark_advanced(self.ra_cal_group)
        adv_btn.toggled.connect(self.ra_cal_group.setVisible)
        adv_btn.toggled.connect(lambda checked: adv_btn.setText(("▾ " if checked else "▸ ") + "Advanced RA Correction calibration"))
        v.addWidget(self.ra_cal_group)

        v.addStretch(1)
        return w


    class _DmsValue:
        """Degrees/minutes/seconds entry that reports a single arcminute value."""

        def __init__(self, sign, degrees, minutes, seconds, preview, limit):
            self._sign, self._deg, self._min, self._sec = sign, degrees, minutes, seconds
            self._preview, self._limit = preview, limit
            for widget in (sign, degrees, minutes, seconds):
                signal = widget.currentIndexChanged if hasattr(widget, "currentIndexChanged") else widget.valueChanged
                signal.connect(self.refresh)
            self.refresh()

        def value(self):
            arcmin = self._deg.value() * 60.0 + self._min.value() + self._sec.value() / 60.0
            if self._sign.currentText().startswith("-"):
                arcmin = -arcmin
            return max(-self._limit, min(self._limit, arcmin))

        def set_arcmin(self, arcmin):
            total = abs(arcmin)
            self._sign.setCurrentIndex(1 if arcmin < 0 else 0)
            self._deg.setValue(int(total // 60))
            rest = total - int(total // 60) * 60
            self._min.setValue(int(rest))
            self._sec.setValue(round((rest - int(rest)) * 60, 1))

        def refresh(self):
            arcmin = self.value()
            text = f"= {arcmin:+.2f}′"
            raw = self._deg.value() * 60.0 + self._min.value() + self._sec.value() / 60.0
            if raw > self._limit:
                text += _(" (clamped to the {limit:.0f}′ travel limit)").format(limit=self._limit)
            self._preview.setText(text)

    def _make_dms_row(self, grid, row, label, limit):
        """Build a °/'/\" entry on one grid row and return its value holder."""
        sign = QtWidgets.QComboBox(); sign.addItems(["+", "-"]); sign.setMaximumWidth(50)
        degrees = QtWidgets.QSpinBox(); degrees.setRange(0, 20); degrees.setSuffix("°"); degrees.setMaximumWidth(70)
        minutes = QtWidgets.QSpinBox(); minutes.setRange(0, 59); minutes.setSuffix("′"); minutes.setMaximumWidth(70)
        seconds = QtWidgets.QDoubleSpinBox(); seconds.setRange(0, 59.9); seconds.setDecimals(1)
        seconds.setSuffix("″"); seconds.setMaximumWidth(80)
        preview = QtWidgets.QLabel(); preview.setStyleSheet("color:palette(dark)")
        grid.addWidget(QtWidgets.QLabel(label), row, 0)
        grid.addWidget(sign, row, 1); grid.addWidget(degrees, row, 2)
        grid.addWidget(minutes, row, 3); grid.addWidget(seconds, row, 4)
        grid.addWidget(preview, row, 5, 1, 2)
        return self._DmsValue(sign, degrees, minutes, seconds, preview, limit)

    def make_pa_tab(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        auto = QtWidgets.QGroupBox("Ekos PAA automatic correction")
        g = QtWidgets.QGridLayout(auto)
        self.accuracy = QtWidgets.QDoubleSpinBox(); self.accuracy.setRange(5, 3600); self.accuracy.setDecimals(0); self.accuracy.setSuffix('"'); self.accuracy.setValue(float(self.cfg["accuracy_arcsec"]))
        self.alt_offset = QtWidgets.QDoubleSpinBox(); self.alt_offset.setRange(-120,120); self.alt_offset.setDecimals(3); self.alt_offset.setSuffix("′"); self.alt_offset.setValue(float(self.cfg["alt_offset_arcmin"]))
        self.az_offset = QtWidgets.QDoubleSpinBox(); self.az_offset.setRange(-120,120); self.az_offset.setDecimals(3); self.az_offset.setSuffix("′"); self.az_offset.setValue(float(self.cfg["az_offset_arcmin"]))
        self.max_move = QtWidgets.QDoubleSpinBox(); self.max_move.setRange(0.5, 140); self.max_move.setDecimals(1); self.max_move.setSuffix("′/axis"); self.max_move.setValue(float(self.cfg["max_move_arcmin"]))
        self.wait_two = QtWidgets.QCheckBox("Wait for two measurements before the first correction")
        self.wait_two.setToolTip(_("Correct from the second consecutive PAA solution; the first solve after a slew is the noisiest."))
        self.wait_two.setChecked(bool(self.cfg.get("autopa_wait_two_solutions", True)))
        self.wait_two.toggled.connect(lambda on: self.cfg.__setitem__("autopa_wait_two_solutions", bool(on)))
        self.pa_start = QtWidgets.QPushButton("Start auto correction"); self.pa_start.setObjectName("primary")
        self.pa_stop = QtWidgets.QPushButton("Stop")
        self.pa_start.clicked.connect(self.start_autopa_watch); self.pa_stop.clicked.connect(self.stop_autopa_watch)
        self.pa_status = QtWidgets.QLabel("Stopped")
        g.addWidget(QtWidgets.QLabel("Target accuracy"),0,0); g.addWidget(self.accuracy,0,1)
        g.addWidget(QtWidgets.QLabel("ALT correction offset"),0,2); g.addWidget(self.alt_offset,0,3)
        g.addWidget(QtWidgets.QLabel("AZ correction offset"),1,0); g.addWidget(self.az_offset,1,1)
        g.addWidget(QtWidgets.QLabel("Max automatic move per cycle"),1,2); g.addWidget(self.max_move,1,3)
        g.addWidget(self.wait_two,2,0,1,4)
        pa_diag = QtWidgets.QPushButton("Diagnose PAA log")
        pa_diag.setToolTip(_("Prints the Ekos log paths/settings and the most recent PAA Refresh values to the log."))
        pa_diag.clicked.connect(self.diagnose_paa_log)
        g.addWidget(self.pa_start,3,0); g.addWidget(self.pa_stop,3,1); g.addWidget(pa_diag,3,2); g.addWidget(self.pa_status,3,3)
        note = QtWidgets.QLabel("Watches the newest 'PAA Refresh ... Corrected az ... alt ... total' line in the Ekos log. Verify the sign with a manual ±1' move first, then start automatic correction.")
        note.setWordWrap(True); g.addWidget(note,4,0,1,4)
        v.addWidget(auto)

        manual = QtWidgets.QGroupBox("AutoPA manual control (reuses the existing INDI POLAR_ALT / POLAR_AZ)")
        mg = QtWidgets.QGridLayout(manual)
        # Ekos states the polar error in degrees/minutes/seconds, so take the
        # same units here instead of making people convert 1°52'00" to 112'.
        self.alt_move = self._make_dms_row(mg, 0, "ALT", limit=140)
        self.az_move = self._make_dms_row(mg, 1, "AZ", limit=320)
        move_alt = QtWidgets.QPushButton("Move ALT"); move_az = QtWidgets.QPushButton("Move AZ")
        move_both = QtWidgets.QPushButton("Move ALT -> AZ")
        self.pa_motion_buttons.extend([move_alt, move_az, move_both])
        move_alt.clicked.connect(lambda: self.move_pa(self.alt_move.value(), None))
        move_az.clicked.connect(lambda: self.move_pa(None, self.az_move.value()))
        move_both.clicked.connect(lambda: self.move_pa(self.alt_move.value(), self.az_move.value()))
        mg.addWidget(move_alt,0,7); mg.addWidget(move_az,1,7); mg.addWidget(move_both,0,8,2,1)
        for row,(axis,label) in enumerate((("ALT","ALT quick"),("AZ","AZ quick")), start=2):
            mg.addWidget(QtWidgets.QLabel(label),row,0)
            for col,n in enumerate((-30,-5,-1,-0.5,0.5,1,5,30), start=1):
                b=QtWidgets.QPushButton(f"{n:+g}′")
                self.pa_motion_buttons.append(b)
                b.clicked.connect(lambda _=False, a=axis, x=n: self.move_pa(x if a=="ALT" else None, x if a=="AZ" else None))
                mg.addWidget(b,row,col)
        v.addWidget(manual)

        pos = QtWidgets.QGroupBox("AutoPA axis position / reference")
        pg = QtWidgets.QGridLayout(pos)
        self.pa_pos = QtWidgets.QLabel("AZ: - step | ALT: - step")
        read_pos = QtWidgets.QPushButton("Read current position"); read_pos.clicked.connect(self.read_pa_position)
        home_pa = QtWidgets.QPushButton("Go to AZ/ALT Zero (:MAAH#)"); home_pa.clicked.connect(self.pa_home)
        zero_pa = QtWidgets.QPushButton("Save AZ/ALT Zero (:hZ#)"); zero_pa.clicked.connect(self.pa_set_zero)
        self.pa_home_btn, self.pa_zero_btn = home_pa, zero_pa
        self.pa_motion_buttons.extend([home_pa, zero_pa])
        pg.addWidget(self.pa_pos,0,0,1,3); pg.addWidget(read_pos,1,0); pg.addWidget(home_pa,1,1); pg.addWidget(zero_pa,1,2)
        warn = QtWidgets.QLabel("Note: saving Zero is not something to press at every polar alignment. Use it only when redefining the physical reference point of the AutoPA hardware.")
        warn.setWordWrap(True); pg.addWidget(warn,2,0,1,3)
        v.addWidget(self._mark_advanced(pos)); v.addStretch(1)
        return w

    def make_mini_tab(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        info = self._hint(
            "Left/right is RA, up/down is DEC, diagonals move both axes. Degree-to-step conversion uses the firmware :XGR# / :XGD# values.")
        v.addWidget(info)

        box = QtWidgets.QGroupBox("RA / DEC direction control")
        g = QtWidgets.QGridLayout(box)
        g.setHorizontalSpacing(8); g.setVerticalSpacing(8)

        # Both axes are user-facing degrees. Each move reads the firmware's
        # active steps/degree value so motor angle, microstepping and reduction
        # are never guessed in OAT Tools.
        self.mini_ra_degrees = QtWidgets.QComboBox()
        for deg in (1, 5, 15):
            self.mini_ra_degrees.addItem(f"{deg}°", deg)
        self.mini_ra_degrees.setCurrentIndex(1)

        self.mini_dec_degrees = QtWidgets.QComboBox()
        for deg in (1, 5, 15):
            self.mini_dec_degrees.addItem(f"{deg}°", deg)
        self.mini_dec_degrees.setCurrentIndex(1)

        self.slew_rate_box = QtWidgets.QComboBox()
        for label, code in (("Slow (G)", "G"), ("Medium (C)", "C"), ("Fast (M)", "M"), ("Max (S)", "S")):
            self.slew_rate_box.addItem(label, code)
        idx = self.slew_rate_box.findData(self.cfg.get("slew_rate", "M"))
        self.slew_rate_box.setCurrentIndex(max(0, idx))
        self.slew_rate_box.currentIndexChanged.connect(
            lambda _i: self.set_slew_rate(self.slew_rate_box.currentData()))
        g.addWidget(QtWidgets.QLabel("RA move per press"), 0, 0)
        g.addWidget(self.mini_ra_degrees, 0, 1)
        g.addWidget(QtWidgets.QLabel("DEC move per press"), 0, 2)
        g.addWidget(self.mini_dec_degrees, 0, 3)
        g.addWidget(QtWidgets.QLabel("Slew rate"), 4, 0)
        g.addWidget(self.slew_rate_box, 4, 1)
        kb = self._hint("Keyboard: left/right RA, up/down DEC, W/S ALT, A/D AZ (while this tab is active)")
        g.addWidget(kb, 4, 2, 1, 2)

        pad = QtWidgets.QGridLayout()
        pad.setHorizontalSpacing(8); pad.setVerticalSpacing(8)
        directions = [
            (0, 0, "↖", -1, +1), (0, 1, "↑", 0, +1), (0, 2, "↗", +1, +1),
            (1, 0, "←", -1, 0),                          (1, 2, "→", +1, 0),
            (2, 0, "↙", -1, -1), (2, 1, "↓", 0, -1), (2, 2, "↘", +1, -1),
        ]
        for row, col, text, dx, dy in directions:
            b = QtWidgets.QPushButton(text)
            b.setMinimumSize(58, 44)
            b.setStyleSheet("font-size: 19px; font-weight: 600;")
            b.clicked.connect(lambda _=False, x=dx, y=dy: self.mini_direction_move(x, y))
            pad.addWidget(b, row, col)

        home = QtWidgets.QPushButton("HOME")
        home.setMinimumSize(58, 44)
        home.setStyleSheet("font-size: 12px; font-weight: 700;")
        home.setToolTip(_("Move to RA logical Home(0) + the saved DEC manual Home position"))
        home.clicked.connect(self.mini_goto_home)
        pad.addWidget(home, 1, 1)

        pad_wrap = QtWidgets.QWidget(); pad_wrap.setLayout(pad)
        g.addWidget(pad_wrap, 1, 0, 1, 4, QtCore.Qt.AlignHCenter)

        # Explicit field button beside the compact centre HOME key.
        go_home = QtWidgets.QPushButton("GO HOME — RA / DEC")
        go_home.setMinimumHeight(34)
        go_home.setObjectName("primary")
        go_home.setToolTip(_("Firmware Go To Home (:hF#) - return to the RA/DEC logical 0 fixed by the final SET HOME"))
        go_home.clicked.connect(self.mini_goto_home)
        g.addWidget(go_home, 2, 0, 1, 4)

        note = self._hint(
            "HOME uses firmware :hF# to move to the logical Home(0) fixed by the final SET HOME. "
            "The DEC up/down inversion follows the option on the Home tab.")
        g.addWidget(note, 3, 0, 1, 4)
        v.addWidget(box)

        pa = QtWidgets.QGroupBox("AutoPA fine movement")
        pg = QtWidgets.QHBoxLayout(pa); pg.setSpacing(6)
        self.mini_pa_step = QtWidgets.QDoubleSpinBox(); self.mini_pa_step.setRange(0.1,10); self.mini_pa_step.setValue(1.0); self.mini_pa_step.setSuffix("′")
        self.mini_pa_step.setMaximumWidth(80)
        altu=QtWidgets.QPushButton("ALT ↑"); altd=QtWidgets.QPushButton("ALT ↓"); azl=QtWidgets.QPushButton("AZ ←"); azr=QtWidgets.QPushButton("AZ →")
        altu.clicked.connect(lambda: self.move_pa(+self.mini_pa_step.value(), None))
        altd.clicked.connect(lambda: self.move_pa(-self.mini_pa_step.value(), None))
        azl.clicked.connect(lambda: self.move_pa(None, -self.mini_pa_step.value()))
        azr.clicked.connect(lambda: self.move_pa(None, +self.mini_pa_step.value()))
        pg.addWidget(QtWidgets.QLabel("Move size")); pg.addWidget(self.mini_pa_step)
        for b in (altu, altd, azl, azr): pg.addWidget(b)

        tr = QtWidgets.QGroupBox("Tracking")
        tg = QtWidgets.QGridLayout(tr); tg.setSpacing(6)
        on=QtWidgets.QPushButton("ON"); off=QtWidgets.QPushButton("OFF")
        on.clicked.connect(lambda: self.set_tracking(True)); off.clicked.connect(lambda: self.set_tracking(False))
        tg.addWidget(on,0,0); tg.addWidget(off,0,1)
        self.track_trim = QtWidgets.QDoubleSpinBox(); self.track_trim.setRange(0.5,1.5); self.track_trim.setDecimals(4)
        self.track_trim.setSingleStep(0.0005); self.track_trim.setValue(1.0); self.track_trim.setMaximumWidth(100)
        self.track_speed_label = QtWidgets.QLabel("Tracking speed -")
        read_trim = QtWidgets.QPushButton("Read"); read_trim.clicked.connect(self.read_tracking_trim)
        save_trim = QtWidgets.QPushButton("Save"); save_trim.clicked.connect(self.save_tracking_trim)
        tg.addWidget(QtWidgets.QLabel("Trim"),1,0); tg.addWidget(self.track_trim,1,1)
        tg.addWidget(read_trim,1,2); tg.addWidget(save_trim,1,3)
        tg.addWidget(self.track_speed_label,2,0,1,4)
        bottom = QtWidgets.QHBoxLayout(); bottom.setSpacing(6)
        bottom.addWidget(pa, 1); bottom.addWidget(tr, 0)
        v.addLayout(bottom)
        v.addWidget(self._hint(
            "A global Stop (:Q#) button is not included because some firmware builds have reported hangs/crashes."))
        v.addStretch(1)
        return w

    def make_monitor_tab(self):
        w=QtWidgets.QWidget(); v=QtWidgets.QVBoxLayout(w)
        info=QtWidgets.QLabel("Shows the live :GX# position together with the configured soft/physical limits. RA is in hours from Home, DEC in degrees.")
        info.setWordWrap(True); v.addWidget(info)
        self.ra_limit_label=QtWidgets.QLabel("RA: -")
        self.ra_limit_bar=QtWidgets.QProgressBar(); self.ra_limit_bar.setRange(0,1000); self.ra_limit_bar.setFormat("RA position %p%")
        self.safe_ra_label=QtWidgets.QLabel("RA tracking left: -")
        self.safe_ra_label.setToolTip(
            "Hours before the RA ring reaches its tracking limit (firmware :XGST#).\n"
            "It counts down while tracking and jumps when you slew to another target.\n"
            "At zero the firmware stops tracking by itself.")
        self.dec_limit_label=QtWidgets.QLabel("DEC: -")
        self.dec_limit_bar=QtWidgets.QProgressBar(); self.dec_limit_bar.setRange(0,1000); self.dec_limit_bar.setFormat("DEC position %p%")
        for lbl,bar in ((self.ra_limit_label,self.ra_limit_bar),(self.dec_limit_label,self.dec_limit_bar)):
            lbl.setStyleSheet("font-weight:bold"); v.addWidget(lbl); v.addWidget(bar)
            if lbl is self.ra_limit_label:
                self.safe_ra_label.setStyleSheet("font-weight:bold"); v.addWidget(self.safe_ra_label)
        g=QtWidgets.QGridLayout()
        self.ra_limit_left=QtWidgets.QDoubleSpinBox(); self.ra_limit_left.setRange(0,24); self.ra_limit_left.setValue(float(self.cfg.get("ra_limit_left_h",5)))
        self.ra_limit_right=QtWidgets.QDoubleSpinBox(); self.ra_limit_right.setRange(0,24); self.ra_limit_right.setValue(float(self.cfg.get("ra_limit_right_h",7)))
        self.ra_physical=QtWidgets.QDoubleSpinBox(); self.ra_physical.setRange(0,24); self.ra_physical.setValue(float(self.cfg.get("ra_physical_limit_h",7)))
        self.dec_limit_down=QtWidgets.QDoubleSpinBox(); self.dec_limit_down.setRange(-180,180); self.dec_limit_down.setValue(float(self.cfg.get("dec_limit_down_deg",0)))
        self.dec_limit_up=QtWidgets.QDoubleSpinBox(); self.dec_limit_up.setRange(-180,180); self.dec_limit_up.setValue(float(self.cfg.get("dec_limit_up_deg",0)))
        for i,(name,ctl) in enumerate((("RA Left (h)",self.ra_limit_left),("RA Right (h)",self.ra_limit_right),("RA Physical (h)",self.ra_physical),("DEC Down (°)",self.dec_limit_down),("DEC Up (°)",self.dec_limit_up))):
            g.addWidget(QtWidgets.QLabel(name),i//3,(i%3)*2); g.addWidget(ctl,i//3,(i%3)*2+1)
        v.addLayout(g)
        b=QtWidgets.QPushButton("Refresh now"); b.clicked.connect(self.refresh_mount_monitor)
        row=QtWidgets.QHBoxLayout(); row.addWidget(b); row.addStretch(1); v.addLayout(row)

        fw = QtWidgets.QGroupBox("Firmware DEC limits (:XGDL# / :XSDL*#)")
        fl = QtWidgets.QGridLayout(fw)
        self.dec_fw_limit_label = QtWidgets.QLabel("Firmware DEC limits: not checked")
        read_lim = QtWidgets.QPushButton("Read"); read_lim.clicked.connect(self.read_dec_limits)
        set_low = QtWidgets.QPushButton("Set lower limit here"); set_low.clicked.connect(lambda: self.set_dec_limit_here("L"))
        set_up = QtWidgets.QPushButton("Set upper limit here"); set_up.clicked.connect(lambda: self.set_dec_limit_here("U"))
        clr = QtWidgets.QPushButton("Reset to configuration values"); clr.clicked.connect(self.clear_dec_limits)
        self.dec_limit_travel_down = QtWidgets.QDoubleSpinBox(); self.dec_limit_travel_down.setRange(0, 180)
        self.dec_limit_travel_down.setDecimals(1); self.dec_limit_travel_down.setSuffix("°"); self.dec_limit_travel_down.setValue(90.0)
        self.dec_limit_travel_up = QtWidgets.QDoubleSpinBox(); self.dec_limit_travel_up.setRange(0, 180)
        self.dec_limit_travel_up.setDecimals(1); self.dec_limit_travel_up.setSuffix("°"); self.dec_limit_travel_up.setValue(90.0)
        apply_lim = QtWidgets.QPushButton("Apply travel limits"); apply_lim.setObjectName("primary")
        apply_lim.clicked.connect(self.apply_dec_limits)
        fl.addWidget(self.dec_fw_limit_label,0,0,1,3); fl.addWidget(read_lim,0,3)
        fl.addWidget(QtWidgets.QLabel("Travel down"),1,0); fl.addWidget(self.dec_limit_travel_down,1,1)
        fl.addWidget(QtWidgets.QLabel("up"),1,2); fl.addWidget(self.dec_limit_travel_up,1,3)
        fl.addWidget(apply_lim,2,0,1,4)
        fl.addWidget(set_low,3,0); fl.addWidget(set_up,3,1); fl.addWidget(clr,3,2)
        fl.addWidget(self._hint("The firmware stores how far the DEC ring may travel from Home, downwards and upwards, "
                                "and clamps every move - including GOTO - to that range. With Home at the pole, a "
                                "'down' limit of 30° means no target below DEC +60° can be reached. 90/90 allows full travel."),4,0,1,4)
        v.addWidget(fw)

        tgt = QtWidgets.QGroupBox("Target reachability check (:XGC#)")
        tl = QtWidgets.QGridLayout(tgt)
        self.target_ra = QtWidgets.QDoubleSpinBox(); self.target_ra.setRange(0,23.999); self.target_ra.setDecimals(4); self.target_ra.setSuffix(" h")
        self.target_dec = QtWidgets.QDoubleSpinBox(); self.target_dec.setRange(-90,90); self.target_dec.setDecimals(3); self.target_dec.setSuffix(" °")
        use_ekos = QtWidgets.QPushButton("Current Ekos coordinates"); use_ekos.clicked.connect(self.use_ekos_target)
        self.target_check_btn = QtWidgets.QPushButton("Check"); self.target_check_btn.setObjectName("primary")
        self.target_check_btn.clicked.connect(self.check_target_reachable)
        self.target_check_label = QtWidgets.QLabel("Checks whether the target exceeds the limits before slewing."); self.target_check_label.setWordWrap(True)
        tl.addWidget(QtWidgets.QLabel("RA"),0,0); tl.addWidget(self.target_ra,0,1)
        tl.addWidget(QtWidgets.QLabel("DEC"),0,2); tl.addWidget(self.target_dec,0,3)
        tl.addWidget(use_ekos,0,4); tl.addWidget(self.target_check_btn,0,5)
        tl.addWidget(self.target_check_label,1,0,1,6)
        v.addWidget(tgt)
        v.addStretch(1)
        return w

    def make_axis_cal_tab(self):
        w=QtWidgets.QWidget(); v=QtWidgets.QVBoxLayout(w)
        t=QtWidgets.QLabel("In Ekos, run Capture & Solve then Sync at each point before recording. Turning Tracking OFF during calibration is recommended. The EEPROM write command is never guessed; only the calculated value is shown.")
        t.setWordWrap(True); v.addWidget(t)
        g=QtWidgets.QGridLayout()
        self.axis_sel=QtWidgets.QComboBox(); self.axis_sel.addItems(["RA","DEC"])
        self.axis_move=QtWidgets.QDoubleSpinBox(); self.axis_move.setRange(1,45); self.axis_move.setValue(10); self.axis_move.setSuffix("°")
        self.axis_start_label=QtWidgets.QLabel("Start solve: -"); self.axis_result=QtWidgets.QPlainTextEdit(); self.axis_result.setReadOnly(True); self.axis_result.setMinimumHeight(90); self.axis_result.setMaximumHeight(140)
        rec=QtWidgets.QPushButton("① Record Start Solve"); move=QtWidgets.QPushButton("② Move by the given angle"); endb=QtWidgets.QPushButton("③ Record End Solve / calculate")
        rec.clicked.connect(self.axis_record_start); move.clicked.connect(self.axis_move_command); endb.clicked.connect(self.axis_record_end)
        g.addWidget(QtWidgets.QLabel("Axis"),0,0); g.addWidget(self.axis_sel,0,1); g.addWidget(QtWidgets.QLabel("Command angle"),0,2); g.addWidget(self.axis_move,0,3)
        g.addWidget(rec,1,0,1,2); g.addWidget(move,1,2); g.addWidget(endb,1,3)
        self.axis_apply_btn = QtWidgets.QPushButton("④ Apply to mount (:XSR#/:XSD#)")
        self.axis_apply_btn.clicked.connect(self.apply_axis_calibration)
        g.addWidget(self.axis_apply_btn,2,0,1,4)
        self.axis_restore_btn = QtWidgets.QPushButton("Restore previous steps/degree - nothing saved")
        self.axis_restore_btn.setToolTip(
            "Writes back the steps/degree the mount used before the last calibration was applied.")
        self.axis_restore_btn.clicked.connect(self.restore_axis_calibration)
        g.addWidget(self.axis_restore_btn,3,0,1,4)
        v.addLayout(g); v.addWidget(self.axis_start_label); v.addWidget(self.axis_result)
        self._update_axis_restore_button()
        drift = QtWidgets.QGroupBox("Drift alignment (:XD#)")
        dg = QtWidgets.QHBoxLayout(drift)
        self.drift_seconds = QtWidgets.QSpinBox(); self.drift_seconds.setRange(10,600)
        self.drift_seconds.setValue(int(self.cfg.get("drift_align_seconds",60))); self.drift_seconds.setSuffix("s")
        self.drift_btn = QtWidgets.QPushButton("Run drift alignment"); self.drift_btn.clicked.connect(self.run_drift_alignment)
        dg.addWidget(QtWidgets.QLabel("One-way time")); dg.addWidget(self.drift_seconds); dg.addWidget(self.drift_btn); dg.addStretch(1)
        v.addWidget(drift)
        back=QtWidgets.QLabel("For a precise check, measure both directions with +D, -2D, +D and compare the backlash difference. This version records each run so you can repeat and compare.")
        back.setWordWrap(True); v.addWidget(back); v.addStretch(1); return w


    def make_diag_tab(self):
        w=QtWidgets.QWidget(); v=QtWidgets.QVBoxLayout(w)
        note=QtWidgets.QLabel("Read-only diagnostics that are safe to use while observing. Arbitrary Meade commands, EEPROM/Factory Reset and firmware configuration/flashing live in the separate OAT Firmware extension.")
        note.setWordWrap(True); v.addWidget(note)
        row=QtWidgets.QHBoxLayout(); b=QtWidgets.QPushButton("Refresh OAT status"); b.setObjectName("primary"); b.clicked.connect(self.refresh_diagnostics)
        logs=QtWidgets.QPushButton("Open the log folder"); logs.clicked.connect(lambda: os.system(f'xdg-open "{LOG_DIR}" >/dev/null 2>&1 &'))
        row.addWidget(b); row.addWidget(logs); row.addStretch(1); v.addLayout(row)
        self.diag_text=QtWidgets.QPlainTextEdit(); self.diag_text.setReadOnly(True); self.diag_text.setMinimumHeight(160)
        self.diag_text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)); v.addWidget(self.diag_text,1)
        return w


    # ------------------------ async helpers ------------------------
    def run_async(self, fn, on_result=None, on_error=None, on_finished=None):
        worker = FunctionWorker(fn)
        if on_result: worker.signals.result.connect(on_result)
        worker.signals.error.connect(on_error or (lambda e: self.log(e, logging.ERROR)))
        if on_finished: worker.signals.finished.connect(on_finished)
        self.threadpool.start(worker)

    def meade_async(self, cmd, on_result=None, on_finished=None):
        self.run_async(lambda: self.indi.meade(cmd), on_result, None, on_finished)

    # ------------------------ safety / mini controller ------------------------
    @staticmethod
    def _parse_firmware_version(text):
        """'V1.13.9' -> 11309, so builds can be compared numerically."""
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(text))
        if not m:
            return 0
        major, minor, patch = (int(g) for g in m.groups())
        return major * 10000 + minor * 100 + patch

    def _fw_at_least(self, version_num):
        """True when the firmware is new enough (unknown version -> allow)."""
        return self.firmware_version_num == 0 or self.firmware_version_num >= version_num

    def refresh_firmware_version(self):
        def job():
            return str(self.indi.meade(":GVN#")).strip().rstrip("#")
        def done(text):
            self.firmware_version_text = text
            self.firmware_version_num = self._parse_firmware_version(text)
            if hasattr(self, "fw_label"):
                self.fw_label.setText(f"FW: {text or '-'}")
            self.log(f"OAT firmware version: {text or 'unknown'}")
            self._apply_version_gating()
            self.read_dec_limits()
        self.run_async(job, done, lambda e: self.log(f"Failed to read the firmware version: {e}", logging.WARNING))

    def _apply_version_gating(self):
        """Disable controls the connected firmware cannot serve."""
        gates = [
            ("ra_home_btn", 10921, "RA Hall AutoHome requires firmware V1.9.21 or newer"),
            ("target_check_btn", 10900, "Target position calculation (:XGC#) requires firmware V1.9.0 or newer"),
            ("pa_home_btn", 11306, "AZ/ALT Home move requires firmware V1.13.6 or newer"),
            ("pa_zero_btn", 11306, "Saving AZ/ALT Zero requires firmware V1.13.6 or newer"),
            ("drift_btn", 10900, "Drift alignment requires firmware V1.9.0 or newer"),
        ]
        for attr, need, reason in gates:
            widget = getattr(self, attr, None)
            if widget is None:
                continue
            ok = self._fw_at_least(need)
            widget.setEnabled(ok)
            if not ok:
                widget.setToolTip(reason)

    def run_on_connect_actions(self):
        """Optional automation right after a connect."""
        if not self.indi.running or self._motion_or_home_busy():
            return
        if self.cfg.get("restore_dec_home_on_connect") and self.cfg.get("dec_home_offset_steps") is not None:
            self.log("Automatic action on connect: restore saved DEC Home")
            self.restore_saved_dec_home(confirm=False)
        elif self.cfg.get("autohome_ra_on_connect"):
            self.log("Automatic action on connect: RA AutoHome")
            self.start_home(["RA"])


    # ------------------------ firmware limits / park / targets ------------------------
    def read_dec_limits(self):
        """Read the DEC travel the firmware allows (:XGDL#).

        The firmware answers "<down>|<up>" as *travel distances from Home in
        degrees*, not as signed bounds: "30.0|90.0" means the ring may move 30
        degrees down and 90 up, so with Home at the pole a GOTO cannot go below
        DEC +60 - it is clamped there, which looks exactly like a broken slew.
        """
        if not self.indi.running:
            return
        def job():
            raw = str(self.indi.meade(":XGDL#")).strip().rstrip("#")
            parts = [x for x in re.split(r"[|,]", raw) if x.strip()]
            values = [abs(float(x)) for x in parts]
            if len(values) == 1:
                return (None, values[0])
            return (values[0], values[1])
        def done(limits):
            self.dec_limits_firmware = limits
            down, up = limits
            if hasattr(self, "dec_fw_limit_label"):
                if down is None:
                    text = _("Firmware DEC limits: {up:.1f}° up").format(up=up)
                elif not down and not up:
                    text = _("Firmware DEC limits: not set")
                else:
                    text = _("Firmware DEC travel: {down:.1f}° down / {up:.1f}° up "
                             "(DEC {low:+.1f}° … {high:+.1f}° with Home at the pole)").format(
                        down=down, up=up, low=90.0 - down, high=min(90.0, 90.0 + up))
                self.dec_fw_limit_label.setText(text)
            if hasattr(self, "dec_limit_travel_down") and (down or up):
                self.dec_limit_travel_down.setValue(float(down or 0.0))
                self.dec_limit_travel_up.setValue(float(up or 0.0))
            # The monitor bar wants signed degrees around Home(0).
            if down is not None and hasattr(self, "dec_limit_down") and (down or up):
                self.dec_limit_down.setValue(-float(down))
                self.dec_limit_up.setValue(float(up))
            self.logger.debug("Firmware DEC travel: down=%s up=%s", down, up)
        self.run_async(job, done, lambda e: self.log(f"Failed to read the DEC limits: {e}", logging.WARNING))

    def apply_dec_limits(self):
        """Write explicit DEC travel limits.

        Without this you can only pin a limit to wherever DEC happens to be,
        which makes a too-narrow limit impossible to widen again.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        down = float(self.dec_limit_travel_down.value())
        up = float(self.dec_limit_travel_up.value())
        if QtWidgets.QMessageBox.question(
                self, _("Set DEC limit"),
                _("Allow the DEC ring to travel {down:.1f}° down and {up:.1f}° up from Home?\n"
                  "With Home at the pole that is DEC {low:+.1f}° … {high:+.1f}°; every move is clamped to it.").format(
                    down=down, up=up, low=90.0 - down, high=min(90.0, 90.0 + up)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes) != QtWidgets.QMessageBox.Yes:
            return
        def job():
            self.indi.meade(f"@XSDLL{down:.1f}#")
            time.sleep(0.25)
            self.indi.meade(f"@XSDLU{up:.1f}#")
            time.sleep(0.25)
            return True
        def done(_ok):
            self.log(f"✓ DEC travel limits set: {down:.1f}° down / {up:.1f}° up from Home.")
            self.read_dec_limits()
        self.run_async(job, done, lambda e: self.log(f"Failed to set the DEC limit: {e}", logging.ERROR))

    def set_dec_limit_here(self, which):
        """Store the current DEC position as the lower/upper firmware limit."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self._motion_or_home_busy():
            self.log("Set the limits after the move has finished.", logging.WARNING); return
        label = "Lower" if which == "L" else "Upper"
        if QtWidgets.QMessageBox.question(
                self, _("Set DEC limit"),
                f"The current DEC position as the firmware DEC {label} limit.\n"
                "The limits are relative to Home(0) and every later move is clamped to this range. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        def job():
            gx = self._parse_gx(self.indi.meade(":GX#"))
            spd = self._read_dec_steps_per_degree()
            deg = float(gx["dec_steps"]) / spd if spd else 0.0
            # No parameter => firmware uses the current position.
            self.indi.meade("@XSDLL#" if which == "L" else "@XSDLU#")
            time.sleep(0.25)
            return deg
        def done(deg):
            self.log(f"✓ DEC {label} limit to the current position ({deg:+.3f}°).")
            self.read_dec_limits()
        self.run_async(job, done, lambda e: self.log(f"Failed to set the DEC limit: {e}", logging.ERROR))

    def clear_dec_limits(self):
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        def job():
            self.indi.meade("@XSDLl#")
            time.sleep(0.2)
            self.indi.meade("@XSDLu#")
            time.sleep(0.2)
            return True
        def done(_):
            self.log("DEC limits were reset to the firmware configuration values (Configuration_local.hpp).")
            self.read_dec_limits()
        self.run_async(job, done, lambda e: self.log(f"Failed to clear the DEC limits: {e}", logging.ERROR))

    def check_target_reachable(self):
        """Ask the firmware where a target would put the steppers (:XGC#).

        On an OAT the travel is short, so knowing before the slew whether a target lands
        outside the DEC limits or beyond the RA safety hours is worth more than
        the slew itself.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        ra_hours = self.target_ra.value()
        dec_deg = self.target_dec.value()
        def job():
            raw = str(self.indi.meade(f":XGC{ra_hours:.3f}*{dec_deg:.3f}#")).strip().rstrip("#")
            parts = [x for x in re.split(r"[|,]", raw) if x.strip()]
            if len(parts) < 2:
                raise RuntimeError(f"Unexpected :XGC# reply: {raw!r}")
            ra_steps, dec_steps = int(float(parts[0])), int(float(parts[1]))
            ra_spd = self._read_ra_steps_per_degree()
            dec_spd = self._read_dec_steps_per_degree()
            gx = self._parse_gx(self.indi.meade(":GX#"))
            return ra_steps, dec_steps, ra_spd, dec_spd, gx
        def done(values):
            ra_steps, dec_steps, ra_spd, dec_spd, gx = values
            ra_h = (ra_steps / ra_spd) / 15.0 if ra_spd else 0.0
            dec_d = dec_steps / dec_spd if dec_spd else 0.0
            problems = []
            left = -abs(self.ra_limit_left.value()); right = abs(self.ra_limit_right.value())
            if ra_h < left or ra_h > right:
                problems.append(f"RA {ra_h:+.2f}h is outside the configured range ({left:+.2f}…{right:+.2f}h)")
            limits = self.dec_limits_firmware
            if limits and (limits[0] or limits[1]):
                # travel distances -> signed bounds around Home(0)
                lo = -abs(limits[0]) if limits[0] is not None else -90.0
                hi = abs(limits[1]) if limits[1] is not None else 90.0
                if dec_d < lo or dec_d > hi:
                    problems.append(
                        f"DEC {dec_d:+.2f}° from Home is outside the firmware travel limits "
                        f"({lo:+.2f}° … {hi:+.2f}°)")
            move_ra = (ra_steps - int(gx["ra_steps"])) / ra_spd / 15.0 if ra_spd else 0.0
            move_dec = (dec_steps - int(gx["dec_steps"])) / dec_spd if dec_spd else 0.0
            text = (f"RA {ra_hours:.3f}h / DEC {dec_deg:+.2f}° -> steps RA={ra_steps} DEC={dec_steps}  "
                    f"(from Home, RA {ra_h:+.2f}h, DEC {dec_d:+.2f}°, from the current position RA {move_ra:+.2f}h / DEC {move_dec:+.2f}° move)")
            if problems:
                self.target_check_label.setText(_("✗ ") + text + " — " + " / ".join(problems))
                self.target_check_label.setStyleSheet("font-weight:700;color:#b91c1c")
                self.log("Target not reachable: " + " / ".join(problems), logging.WARNING)
            else:
                self.target_check_label.setText(_("✓ ") + text)
                self.target_check_label.setStyleSheet("font-weight:700;color:#15803d")
                self.log("Target reachable: " + text)
        self.run_async(job, done, lambda e: self.log(f"Target check failed: {e}", logging.ERROR))

    def use_ekos_target(self):
        """Fill the target fields from the mount's current Ekos coordinates."""
        pos = self.indi.equatorial_eod()
        if not pos:
            self.log("EQUATORIAL_EOD_COORD could not be read. Check the Ekos mount connection.", logging.WARNING); return
        self.target_ra.setValue(float(pos[0])); self.target_dec.setValue(float(pos[1]))
        self.log(f"Loaded the current Ekos coordinates as the target: RA {pos[0]:.4f}h DEC {pos[1]:+.3f}°")

    def park_mount(self):
        """Firmware Park (:hP#), guarded."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self._motion_or_home_busy() or self.park_busy:
            self.log("Wait for the current move to finish before running Park.", logging.WARNING); return

        # Reading the homing offset is a mount round trip, so it happens in a
        # worker; blocking the GUI thread on the Meade channel would freeze the
        # window while any other poll holds it.
        def read_offset():
            try:
                return int(float(str(self.indi.meade(":XGHD#")).strip().rstrip("#")))
            except Exception:
                return 0
        self.run_async(read_offset, self._park_confirm,
                       lambda e: self.log(f"Park failed: {e}", logging.ERROR))

    def _park_confirm(self, offset):
        warn = ""
        if offset:
            warn = (f"\n\nNote: the firmware DEC homing offset is {offset:+d} step, so after reaching Home Park "
                    "moves DEC by that much again.")
        if QtWidgets.QMessageBox.question(
                self, _("Park"), "Moves the mount to Home, parks it and stops tracking." + warn + "\n\nContinue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        if self._motion_or_home_busy() or self.park_busy:
            self.log("Wait for the current move to finish before running Park.", logging.WARNING); return
        self.park_busy = True
        def job():
            self.indi.meade("@hP#")
            time.sleep(0.4)
            return self._wait_for_ra_dec_idle(wait_ra=True, wait_dec=True, timeout=180.0)
        def done(gx):
            self.log(f"✓ Park done - GX RA={gx['ra_steps']} DEC={gx['dec_steps']}")
        self.run_async(job, done, lambda e: self.log(f"Park failed: {e}", logging.ERROR),
                       lambda: setattr(self, "park_busy", False))

    def unpark_mount(self):
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        self.meade_async("&hU#", lambda r: self.log(f"✓ Unpark - tracking started (reply={r})"))

    def save_release_position_here(self):
        """Store the current position as the shutdown (release) position."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        def job():
            gx = self._parse_gx(self.indi.meade(":GX#"))
            ra_spd = self._read_ra_steps_per_degree()
            dec_spd = self._read_dec_steps_per_degree()
            dec_deg = int(gx["dec_steps"]) / dec_spd
            return int(gx["ra_steps"]) / ra_spd, dec_deg
        def done(values):
            ra_deg, dec_deg = values
            self.release_dec.setValue(round(dec_deg, 2))
            self.release_ra.setValue(round(ra_deg, 2))
            self.cfg["release_dec_deg"] = round(dec_deg, 3)
            self.cfg["release_ra_deg"] = round(ra_deg, 3)
            self.save_config()
            self.log(f"✓ Current position saved as the shutdown position - from Home, DEC {dec_deg:+.2f}°, RA {ra_deg:+.2f}°")
        self.run_async(job, done, lambda e: self.log(f"Failed to save the shutdown position: {e}", logging.ERROR))

    def move_to_release_position(self):
        """Leave the mount in the shutdown position before power off.

        Powering off at Home leaves the camera's weight cantilevered on the RA
        ring; dropping DEC by a set angle first takes that load off.  The move
        starts from Home so the stored power-on -> Home delta stays exact: it
        is rewritten as the reverse of this move, which is precisely where the
        DEC axis will be when the mount is switched on again.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self._motion_or_home_busy():
            self.log("Wait for the current move to finish.", logging.WARNING); return

        dec_deg = float(self.release_dec.value())
        ra_deg = float(self.release_ra.value())
        if abs(dec_deg) < 1e-6 and abs(ra_deg) < 1e-6:
            self.log("The shutdown position is set to Home(0,0), so there is nothing to move.", logging.WARNING); return

        limits = self.dec_limits_firmware
        if limits and (limits[0] or limits[1]):
            # :XGDL# reports how far the ring may travel down/up from Home,
            # not signed bounds.
            low = -abs(limits[0]) if limits[0] is not None else -90.0
            high = abs(limits[1]) if limits[1] is not None else 90.0
            if not (low <= dec_deg <= high):
                QtWidgets.QMessageBox.warning(
                    self, _("Shutdown position"),
                    f"Shutdown position DEC {dec_deg:+.1f}° is outside the firmware DEC limits ({low:+.1f}° … {high:+.1f}°).\n"
                    "The firmware would clamp the move and stop at the wrong place. Widen the limits or reduce the angle.")
                return

        at_home_first = False
        # Name the direction with the button the user already knows, so the
        # sign of the field is never a guess.
        direction = _("the DEC ↓ button") if dec_deg < 0 else _("the DEC ↑ button")
        answer = QtWidgets.QMessageBox.question(
            self, _("Move to shutdown position"),
            _("From Home, DEC {dec:+.1f}°, RA {ra:+.1f}°.\n"
              "That is {amount:.1f}° in the same direction as {direction}.\n\n"
              "Move to Home with GO TO HOME first?\n"
              "  Yes - go to Home first, then to the shutdown position (recommended)\n"
              "  No - move relatively from the current position").format(
                dec=dec_deg, ra=ra_deg, amount=abs(dec_deg), direction=direction),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel,
            QtWidgets.QMessageBox.Yes)
        if answer == QtWidgets.QMessageBox.Cancel:
            return
        at_home_first = (answer == QtWidgets.QMessageBox.Yes)

        self.cfg["release_dec_deg"] = dec_deg
        self.cfg["release_ra_deg"] = ra_deg
        self.dec_home_move_busy = True
        self.dec_manual_status.setText(_("Moving to the shutdown position..."))

        def job():
            if self.cfg.get("release_stop_tracking", True):
                self.indi.meade("&MT0#")
                time.sleep(0.2)
            if at_home_first:
                self.indi.meade("@hF#")
                time.sleep(0.3)
                self._wait_for_ra_dec_idle(wait_ra=True, wait_dec=True, timeout=180.0)
            start = self._wait_for_ra_dec_idle(wait_ra=True, wait_dec=True, timeout=30.0)
            started_at_home = int(start["dec_steps"]) == 0

            dec_spd = self._read_dec_steps_per_degree()
            dec_steps = self._dec_steps_for_degrees(dec_deg, dec_spd)
            moved_dec = 0
            if dec_steps:
                self.indi.meade(f"@MXd{dec_steps}#")
                end = self._wait_for_dec_idle(timeout=180.0)
                moved_dec = int(end["dec_steps"]) - int(start["dec_steps"])
                if abs(moved_dec - dec_steps) > max(4, abs(dec_steps) // 100):
                    raise RuntimeError(
                        f"The DEC move was clamped: requested {dec_steps:+d} step, actual {moved_dec:+d} step. "
                        "Check the firmware DEC limits.")
            if abs(ra_deg) > 1e-6:
                ra_spd = self._read_ra_steps_per_degree()
                ra_steps = self._ra_steps_for_degrees(ra_deg, ra_spd)
                if ra_steps:
                    self.indi.meade(f"@MXr{ra_steps}#")
                    self._wait_for_ra_dec_idle(wait_ra=True, timeout=180.0)
            return started_at_home, moved_dec

        def done(values):
            started_at_home, moved_dec = values
            self.dec_manual_status.setText(
                f"✓ Shutdown position reached (DEC {dec_deg:+.1f}°). You can power off now.")
            self.log(f"✓ Moved to the shutdown position - from Home, DEC {dec_deg:+.1f}°, RA {ra_deg:+.1f}°. "
                     "The camera's weight no longer hangs on the RA ring when you power off.")
            if started_at_home and moved_dec:
                # Next power-on starts here, so the stored power-on -> Home
                # delta is exactly the reverse of the move just made.
                self.cfg["dec_home_offset_steps"] = int(-moved_dec)
                self.cfg["dec_home_saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.cfg["dec_home_steps_per_degree"] = self.dec_steps_per_degree_live
                if self.cfg.get("dec_home_offset_mode") == "eeprom":
                    try:
                        self._write_offset_verified("DEC", int(moved_dec))
                    except Exception as exc:
                        self.log(f"Failed to save the EEPROM DEC offset: {exc}", logging.WARNING)
                self._update_dec_restore_label()
                self.log(f"The DEC Home restore value for the next session is now {-int(moved_dec):+d} step. "
                         "After the next power-on, press 'Restore DEC Home' to return to Home.")
            elif not started_at_home:
                self.log("The move did not start from Home, so the DEC Home restore value was left unchanged.", logging.WARNING)
            try:
                self.save_config()
            except Exception as exc:
                self.logger.warning("config save failed: %s", exc)

        def err(message):
            self.dec_manual_status.setText(_("✗ Move to shutdown position failed - check the log."))
            self.log(f"Move to shutdown position failed: {message}", logging.ERROR)

        self.run_async(job, done, err, lambda: setattr(self, "dec_home_move_busy", False))

    def _stored_dec_home_offset(self, allow_mount=False):
        """Power-on -> Home DEC delta.

        The mount copy (EEPROM mode) is only consulted when the caller runs in
        a worker thread; the GUI thread must never block on the Meade channel.
        """
        if allow_mount and self.cfg.get("dec_home_offset_mode") == "eeprom" and self.indi.running:
            try:
                raw = int(float(str(self.indi.meade(":XGHD#")).strip().rstrip("#")))
                if raw:
                    return -raw   # the firmware stores the negated delta
            except Exception as exc:
                self.logger.debug("XGHD read failed: %s", exc)
        value = self.cfg.get("dec_home_offset_steps")
        return None if value is None else int(value)

    def set_slew_rate(self, rate):
        """Firmware slew-rate selector: S(fastest) M C G(slowest)."""
        if not self.indi.running:
            return
        rate = str(rate).upper()
        if rate not in ("S", "M", "C", "G"):
            return
        self.cfg["slew_rate"] = rate
        self.meade_async(f"@R{rate}#", lambda _r: self.log(f"Slew rate: {rate} rate applied"))

    def read_tracking_trim(self):
        def job():
            return (str(self.indi.meade(":XGS#")).strip().rstrip("#"),
                    str(self.indi.meade(":XGT#")).strip().rstrip("#"))
        def done(values):
            factor, speed = values
            try:
                self.track_trim.setValue(float(factor))
            except Exception:
                pass
            self.track_speed_label.setText(f"Tracking speed {speed} Hz, trim {factor}")
        self.run_async(job, done, lambda e: self.log(f"Failed to read the tracking speed: {e}", logging.WARNING))

    def save_tracking_trim(self):
        value = float(self.track_trim.value())
        def job():
            self.indi.meade(f"@XSS{value:.4f}#")
            time.sleep(0.25)
            return str(self.indi.meade(":XGS#")).strip().rstrip("#")
        def done(readback):
            self.log(f"✓ Tracking trim saved: {value:.4f} (read back {readback})")
            self.read_tracking_trim()
        self.run_async(job, done, lambda e: self.log(f"Failed to save the tracking trim: {e}", logging.ERROR))

    def run_drift_alignment(self):
        """Firmware drift alignment (:XDnnn#) - blocking, east then west."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self._motion_or_home_busy():
            self.log("Wait for the current move to finish.", logging.WARNING); return
        seconds = int(self.drift_seconds.value())
        if QtWidgets.QMessageBox.question(
                self, _("Drift alignment"),
                f"Runs the firmware drift alignment with a {seconds}second setting.\n"
                f"The mount slews east, stops and slews west, taking about {2*seconds+2}seconds in total, and it cannot be interrupted.\n"
                "Is the guide camera ready to observe the drift?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        self.cfg["drift_align_seconds"] = seconds
        self.mini_motion_busy = True
        self.log(f"Drift alignment started ({seconds}second setting, about {2*seconds+2}seconds)")
        def job():
            self.indi.meade(f"@XD{seconds:03d}#")
            time.sleep(2 * seconds + 3)
            return self._wait_for_ra_dec_idle(wait_ra=True, wait_dec=True, timeout=60.0)
        def done(_gx):
            self.log("✓ Drift alignment finished - correct with AutoPA according to the drift you observed.")
        self.run_async(job, done, lambda e: self.log(f"Drift alignment failed: {e}", logging.ERROR),
                       lambda: setattr(self, "mini_motion_busy", False))

    def apply_axis_calibration(self):
        """Write the measured steps/degree to the mount (:XSR# / :XSD#).

        The measurement can easily produce nonsense - a jog in the opposite
        direction, a plate solve that did not update, an inverted axis - and a
        bad steps/degree makes every later GOTO travel the wrong distance or
        the wrong way. So the value is sanity checked against the one the mount
        is using, the old value is kept for one-click restore, and anything
        beyond a sane correction has to be confirmed explicitly.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        value = getattr(self, "axis_calculated_spd", None)
        axis = getattr(self, "axis_calculated_axis", None)
        if not value or axis not in ("RA", "DEC"):
            self.log("No calculated result to apply. Complete measurements (1) to (3) first.", logging.WARNING); return
        if value <= 0:
            QtWidgets.QMessageBox.warning(self, _("steps/degree"), _(
                "The measurement produced {value:.4f} steps/degree, which cannot be right. "
                "A negative or zero value usually means the axis moved the other way during the test. "
                "Check the direction, repeat the measurement, and do not apply this value.").format(value=value))
            return
        get_cmd = ":XGR#" if axis == "RA" else ":XGD#"

        def read_current():
            return float(str(self.indi.meade(get_cmd)).strip().rstrip("#"))

        def confirm(before):
            ratio = value / before if before else 0.0
            if before and abs(ratio - 1.0) > 0.20:
                answer = QtWidgets.QMessageBox.warning(self, _("steps/degree"), _(
                    "The measured {axis} value {value:.4f} differs from the one in the mount "
                    "({before:.4f}) by {percent:.0f}%.\n\n"
                    "A correct calibration is normally within a few percent. A jump this large usually means "
                    "the test move went the wrong way or the plate solve did not update, and applying it will "
                    "make GOTO travel the wrong distance.\n\nApply it anyway?").format(
                        axis=axis, value=value, before=before, percent=abs(ratio - 1.0) * 100.0),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
                if answer != QtWidgets.QMessageBox.Yes:
                    self.log(f"{axis} steps/degree not applied: {value:.4f} vs {before:.4f} in the mount "
                             f"({abs(ratio - 1.0) * 100.0:.0f}% change) was rejected.", logging.WARNING)
                    return
            if QtWidgets.QMessageBox.question(
                    self, _("steps/degree applied"),
                    _("Write {axis} steps/degree {value:.4f} to the mount EEPROM?\n"
                      "The mount currently uses {before:.4f}; that value is saved so it can be restored.").format(
                        axis=axis, value=value, before=before),
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
                return
            self.cfg[f"{axis.lower()}_steps_per_degree_backup"] = before
            self.save_config()
            self._write_axis_steps(axis, value, before)

        self.run_async(read_current, confirm,
                       lambda e: self.log(f"Could not read the current steps/degree: {e}", logging.ERROR))

    def _write_axis_steps(self, axis, value, before):
        """Write and verify one axis' steps/degree."""
        get_cmd = ":XGR#" if axis == "RA" else ":XGD#"
        set_cmd = f"@XSR{value:.4f}#" if axis == "RA" else f"@XSD{value:.4f}#"

        def job():
            self.indi.meade(set_cmd)
            after = None
            for _n in range(6):
                time.sleep(0.25)
                try:
                    after = float(str(self.indi.meade(get_cmd)).strip().rstrip("#"))
                except Exception:
                    continue
                if abs(after - value) < 0.5:
                    return after
            raise RuntimeError(f"{axis} steps/degree verification failed: wrote {value:.4f}, read back {after}")

        def done(after):
            ratio = 100.0 * value / before if before else 0.0
            self.log(f"✓ {axis} steps/degree applied: {before:.4f} -> {after:.4f} ({ratio:.2f}% of previous). "
                     f"Use 'Restore previous steps/degree' if a later GOTO travels the wrong distance.")
            self.axis_result.appendPlainText(f"Applied: {axis} steps/deg {before:.4f} -> {after:.4f}")
            self.ra_steps_per_degree_live = None
            self.dec_steps_per_degree_live = None
            self._update_axis_restore_button()

        self.run_async(job, done, lambda e: self.log(f"Failed to apply steps/degree: {e}", logging.ERROR))

    def _update_axis_restore_button(self):
        if not hasattr(self, "axis_restore_btn"):
            return
        available = [a for a in ("RA", "DEC")
                     if isinstance(self.cfg.get(f"{a.lower()}_steps_per_degree_backup"), (int, float))]
        self.axis_restore_btn.setEnabled(bool(available))
        if available:
            text = ", ".join(f"{a} {self.cfg[f'{a.lower()}_steps_per_degree_backup']:.4f}" for a in available)
            self.axis_restore_btn.setText(_("Restore previous steps/degree ({values})").format(values=text))
        else:
            self.axis_restore_btn.setText(_("Restore previous steps/degree - nothing saved"))

    def restore_axis_calibration(self):
        """Put the pre-calibration steps/degree back on the mount."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        pending = [(a, self.cfg.get(f"{a.lower()}_steps_per_degree_backup")) for a in ("RA", "DEC")]
        pending = [(a, v) for a, v in pending if isinstance(v, (int, float)) and v > 0]
        if not pending:
            self.log("No saved steps/degree to restore.", logging.WARNING); return
        text = ", ".join(f"{a} {v:.4f}" for a, v in pending)
        if QtWidgets.QMessageBox.question(
                self, _("steps/degree applied"),
                _("Restore the steps/degree the mount used before the last calibration ({values})?").format(values=text),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes) != QtWidgets.QMessageBox.Yes:
            return
        for axis, value in pending:
            self._write_axis_steps(axis, float(value), float(value))


    def _resync_meade(self, attempts=6):
        """Realign the Meade channel after a command that answers twice.

        :SC# (set date) replies with TWO '#'-terminated strings
        ("1Updating Planetary Data#" and 30 spaces + "#"). The passthrough
        reads one, so the spare reply shifts every later read by one - a date
        comes back as "109/19/26", the sidereal time reads as garbage, and the
        repair appears to do nothing. :GVN# is a safe anchor: its answer is
        recognisable, so reading until it looks like a version drains the
        leftovers.
        """
        for _ in range(attempts):
            try:
                reply = str(self.indi.meade(":GVN#")).strip().rstrip("#")
            except Exception:
                return False
            if re.search(r"\d+\.\d+\.\d+", reply):
                return True
            self.logger.debug("Draining a stale Meade reply: %r", reply)
        self.log("The mount's command channel could not be resynchronised; "
                 "disconnect and reconnect the mount if readings look wrong.", logging.WARNING)
        return False

    def _mount_clock_snapshot(self):
        """Everything the firmware uses to derive sidereal time."""
        read = lambda cmd: str(self.indi.meade(cmd)).strip().rstrip("#")
        return {"date": read(":GC#"), "time": read(":GL#"), "offset": read(":GG#"),
                "longitude": read(":Gg#"), "latitude": read(":Gt#"), "lst": self._read_mount_lst()}

    def write_mount_clock(self, lat, lon, lst_hours, offset_hours):
        """Set date, time, UTC offset, site and LST on the mount directly.

        The INDI driver does not always push these down, and the firmware only
        recomputes sidereal time from them - a stale value silently moves the RA
        home reference, which is what makes a GOTO flip and drive DEC the wrong
        way. Writing them explicitly is the repair.
        """
        now_local = datetime.now()
        # :SGsHH# is the Meade convention (hours to add to local time to get
        # UTC), so it is the negated site offset.
        self.indi.meade(f"@SG{-offset_hours:+03.0f}#")
        time.sleep(0.2)
        wanted_date = now_local.strftime("%m/%d/%y")
        current_date = str(self.indi.meade(":GC#")).strip().rstrip("#")
        if current_date != wanted_date:
            # Two replies come back; drain before anything else is read.
            self.indi.meade(f":SC{wanted_date}#")
            time.sleep(0.4)
            self._resync_meade()
        else:
            self.logger.debug("Mount date already %s, not rewriting it", current_date)
        self.indi.meade(f"@SL{now_local.strftime('%H:%M:%S')}#")
        time.sleep(0.2)
        lat_deg = int(abs(lat)); lat_min = int(round((abs(lat) - lat_deg) * 60))
        self.indi.meade(f"@St{'+' if lat >= 0 else '-'}{lat_deg:02d}*{lat_min:02d}#")
        time.sleep(0.2)
        # Signed longitudes are "negative going east" in this firmware.
        stored = self.cfg.get("longitude_command")
        if stored:
            self.indi.meade(stored)
        else:
            self.indi.meade(self._longitude_commands(lon)[0][1])
        time.sleep(0.3)
        hours = int(lst_hours)
        minutes = int((lst_hours - hours) * 60)
        seconds = int(round((lst_hours - hours - minutes / 60.0) * 3600))
        if seconds == 60:
            seconds, minutes = 0, minutes + 1
        if minutes == 60:
            minutes, hours = 0, (hours + 1) % 24
        self.indi.meade(f"@SHL{hours:02d}{minutes:02d}{seconds:02d}#")
        time.sleep(0.3)
        return self._mount_clock_snapshot()

    @staticmethod
    def _longitude_commands(lon_east):
        """Every longitude encoding an OAT build might expect.

        The Meade spec has two conventions (signed, east negative; and unsigned
        0-360 going west), and driver/firmware combinations disagree about which
        one they speak. Writing the wrong one leaves the mount computing
        sidereal time for the opposite side of the planet - a 7 hour error for
        Korea - while :Gg# still reads back plausibly.
        """
        candidates = []
        for label, value in (("signed east-negative", -lon_east),
                             ("signed as-is", lon_east),
                             ("unsigned west 0-360", (360.0 - lon_east) % 360.0),
                             ("unsigned as-is", lon_east % 360.0)):
            degrees = int(abs(value))
            minutes = int(round((abs(value) - degrees) * 60))
            if minutes == 60:
                minutes, degrees = 0, degrees + 1
            if label.startswith("signed"):
                candidates.append((label, f"@Sg{'-' if value < 0 else '+'}{degrees:03d}*{minutes:02d}#"))
            else:
                candidates.append((label, f"@Sg{degrees:03d}*{minutes:02d}#"))
        return candidates

    def _calibrate_longitude(self, lon_east, computed_lst):
        """Try each encoding and keep the one whose sidereal time is right."""
        for label, command in self._longitude_commands(lon_east):
            self.indi.meade(command)
            time.sleep(0.35)
            self._resync_meade()
            lst = self._read_mount_lst()
            if lst is None:
                continue
            drift = abs((lst - computed_lst + 12.0) % 24.0 - 12.0) * 60.0
            self.logger.debug("Longitude as %s (%s) -> LST %s, drift %.1f min",
                              label, command, self._hours_to_hms(lst), drift)
            if drift <= 5.0:
                return label, command, drift
        return None, None, None

    def repair_mount_clock(self):
        """Push the site and clock to the mount, then verify and report."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        info = self.site_angles()
        if not info:
            self.log("The observing site is unknown; connect the mount in Ekos first.", logging.WARNING); return
        now_local = datetime.now().astimezone()
        offset_hours = now_local.utcoffset().total_seconds() / 3600.0 if now_local.utcoffset() else 0.0

        def job():
            before = self._mount_clock_snapshot()
            now_utc = datetime.now(timezone.utc)
            days = now_utc.timestamp() / 86400.0 + 2440587.5 - 2451545.0
            gmst = 18.697374558 + 24.06570982441908 * days
            computed = (gmst + info["lon"] / 15.0) % 24.0
            after = self.write_mount_clock(info["lat"], info["lon"], computed, offset_hours)
            fixed_by = None
            lst = after.get("lst")
            drift = None if lst is None else abs((lst - computed + 12.0) % 24.0 - 12.0) * 60.0
            if drift is not None and drift > 5.0:
                # The site is right but the firmware still computes the wrong
                # sidereal time: the longitude encoding is the usual culprit.
                label, command, _d = self._calibrate_longitude(info["lon"], computed)
                if label:
                    fixed_by = label
                    self.cfg["longitude_command"] = command
                    # LST is derived from the site, so set it again afterwards.
                    hours = int(computed)
                    minutes = int((computed - hours) * 60)
                    seconds = int(round((computed - hours - minutes / 60.0) * 3600))
                    self.indi.meade(f"@SHL{hours:02d}{minutes:02d}{seconds:02d}#")
                    time.sleep(0.3)
                after = self._mount_clock_snapshot()
            return before, after, computed, fixed_by

        def done(values):
            before, after, computed, fixed_by = values
            if fixed_by:
                self.log(f"✓ Longitude encoding corrected: the mount wanted it written as '{fixed_by}'. "
                         "That is remembered for next time.")
            self.log(f"Mount clock written: date {after['date']}, local time {after['time']}, "
                     f"UTC offset {after['offset']}, longitude {after['longitude']}, latitude {after['latitude']}")
            if after["lst"] is None:
                self.log("The mount did not report its sidereal time; check the connection.", logging.WARNING)
                return
            drift = abs((after["lst"] - computed + 12.0) % 24.0 - 12.0) * 60.0
            if drift <= 5.0:
                self.log(f"✓ Mount sidereal time is now {self._hours_to_hms(after['lst'])} "
                         f"(expected {self._hours_to_hms(computed)}). Run SET HOME again so the RA reference "
                         "is stored against the correct time.")
                if hasattr(self, "ha_status"):
                    self.ha_status.setText(_("✓ Mount clock synced - run SET HOME again"))
            else:
                self.log(f"⚠ The mount still reports {self._hours_to_hms(after['lst'])} instead of "
                         f"{self._hours_to_hms(computed)} ({drift:.0f} min out). Every longitude encoding was "
                         f"tried. Values now on the mount: date {after['date']}, time {after['time']}, "
                         f"offset {after['offset']}, longitude {after['longitude']}, latitude {after['latitude']}. "
                         f"An error of {drift/60.0:.2f} h matches a longitude of about "
                         f"{(drift/60.0)*15:.0f}° in the wrong direction; check the site in KStars.",
                         logging.WARNING)
        self.run_async(job, done, lambda e: self.log(f"Could not write the mount clock: {e}", logging.ERROR))

    def _read_mount_lst(self, retry=True):
        """Sidereal time as the mount currently believes it (:XGL# -> hours).

        A reply that does not look like a time means the channel is out of step
        after a double-reply command, so resynchronise once and read again
        rather than reporting a nonsense drift.
        """
        try:
            raw = str(self.indi.meade(":XGL#")).strip().rstrip("#")
        except Exception as exc:
            self.logger.debug("XGL read failed: %s", exc)
            return None
        if retry and not re.fullmatch(r"\d{6}|\d{1,2}[:h]\d{2}([:m]\d{2})?", raw.strip()):
            self.logger.debug("Unexpected :XGL# reply %r; resynchronising", raw)
            self._resync_meade()
            return self._read_mount_lst(retry=False)
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 6:
            return int(digits[0:2]) + int(digits[2:4]) / 60.0 + int(digits[4:6]) / 3600.0
        match = re.match(r"(\d{1,2})[:h](\d{2})(?:[:m](\d{2}))?", raw)
        if match:
            hours, minutes, seconds = match.group(1), match.group(2), match.group(3) or "0"
            return int(hours) + int(minutes) / 60.0 + int(seconds) / 3600.0
        return None

    def mount_lst_drift_minutes(self):
        """How far the mount's LST is from the one computed for this site.

        Returns (drift_minutes, mount_lst, computed_lst) or None when either
        side is unavailable.
        """
        info = self.site_angles()
        mount_lst = self._read_mount_lst()
        if info is None or mount_lst is None:
            return None
        now_utc = datetime.now(timezone.utc)
        days = now_utc.timestamp() / 86400.0 + 2440587.5 - 2451545.0
        gmst = 18.697374558 + 24.06570982441908 * days
        computed = (gmst + info["lon"] / 15.0) % 24.0
        drift = abs((mount_lst - computed + 12.0) % 24.0 - 12.0) * 60.0
        return drift, mount_lst, computed

    def _motion_or_home_busy(self):
        """True while any tool-driven move/home/set-home sequence is active."""
        return bool(self.pa_motion_active or self.home_busy or self.mini_motion_busy
                    or self.dec_jog_busy or self.dec_home_move_busy or self.home_adjust_busy
                    or self.set_home_busy)

    def _wait_for_ra_dec_idle(self, wait_ra=False, wait_dec=False, timeout=90.0, poll=0.20):
        """Wait until requested RA/DEC relative moves are no longer active."""
        deadline = time.time() + float(timeout)
        last = None
        while time.time() < deadline:
            last = self._parse_gx(self.indi.meade(":GX#"))
            motion = last.get("motion", "")
            ra_moving = bool(wait_ra and len(motion) > 0 and motion[0] in ("r", "R"))
            dec_moving = bool(wait_dec and len(motion) > 1 and motion[1] in ("d", "D"))
            if not ra_moving and not dec_moving:
                return last
            time.sleep(float(poll))
        raise TimeoutError(
            f"Mini Controller move did not finish within {timeout:.0f}s; last GX={last}")

    def mini_direction_move(self, ra_direction, dec_direction):
        """Move with the Mini Controller direction pad.

        ra_direction: -1(left), 0, +1(right)
        dec_direction: -1(down), 0, +1(up)
        Diagonal buttons send the existing MXr/MXd relative moves back-to-back so
        both axes can move together. RA/DEC conversion always uses live :XGR#/:XGD#.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self.mini_motion_busy or self.home_busy or self.dec_home_move_busy or self.pa_motion_active:
            self.log("Another move command is being processed. Press again once it finishes.", logging.WARNING); return
        if dec_direction and self.dec_jog_busy:
            self.log("A DEC move is being processed. Press again once it finishes.", logging.WARNING); return

        ra_direction = int(ra_direction)
        dec_direction = int(dec_direction)
        ra_degrees = float(self.mini_ra_degrees.currentData()) * ra_direction if ra_direction else 0.0
        dec_degrees = float(self.mini_dec_degrees.currentData()) * dec_direction if dec_direction else 0.0

        self.mini_motion_busy = True
        if dec_direction:
            self.dec_jog_busy = True

        def job():
            gx_start = self._parse_gx(self.indi.meade(":GX#"))
            ra_spd = None
            ra_steps = 0
            dec_spd = None
            dec_motor_steps = 0
            if ra_direction:
                ra_spd = self._read_ra_steps_per_degree()
                ra_steps = self._ra_steps_for_degrees(ra_degrees, ra_spd)
                if ra_steps == 0:
                    raise RuntimeError(
                        f"RA {ra_degrees:+g}° converts to 0 step (XGR={ra_spd})")
            if dec_direction:
                dec_spd = self._read_dec_steps_per_degree()
                user_steps = self._dec_steps_for_degrees(dec_degrees, dec_spd)
                dec_motor_steps = user_steps
                if dec_motor_steps == 0:
                    raise RuntimeError(
                        f"DEC {dec_degrees:+g}° converts to 0 step (XGD={dec_spd})")

            # Existing firmware-native relative jog commands only; no new
            # command and no :Q# are introduced here.
            if ra_steps:
                self.indi.meade(f"@MXr{ra_steps}#")
            if dec_motor_steps:
                self.indi.meade(f"@MXd{dec_motor_steps}#")

            gx_end = self._wait_for_ra_dec_idle(
                wait_ra=bool(ra_steps), wait_dec=bool(dec_motor_steps), timeout=90.0)
            return gx_start, gx_end, ra_degrees, ra_steps, ra_spd, dec_degrees, dec_motor_steps, dec_spd

        def done(values):
            gx_start, gx_end, rd, rs, rspd, dd, ds, dspd = values
            labels=[]
            if rs:
                labels.append(f"RA {'←' if rd < 0 else '→'} {abs(rd):g}°")
            if ds:
                labels.append(f"DEC {'↑' if dd > 0 else '↓'} {abs(dd):g}°")
            self.log("Mini Controller: " + " + ".join(labels))
            self.logger.debug(
                "Mini pad move: RA=%+.3f deg/%+d motor step XGR=%s, "
                "DEC=%+.3f deg/%+d motor step XGD=%s, "
                "GX RA %+d->%+d DEC %+d->%+d",
                rd, rs, f"{rspd:.9f}" if rspd is not None else "-",
                dd, ds, f"{dspd:.9f}" if dspd is not None else "-",
                gx_start["ra_steps"], gx_end["ra_steps"],
                gx_start["dec_steps"], gx_end["dec_steps"])

        def err(message):
            self.log(f"Mini Controller move failed: {message}", logging.ERROR)

        def finished():
            self.mini_motion_busy = False
            if dec_direction:
                self.dec_jog_busy = False

        self.run_async(job, done, err, finished)

    def mini_goto_home(self):
        """Use firmware Go To Home for the final RA/DEC logical Home.

        The final Home is established once, after RA AutoHome and manual DEC
        adjustment, with firmware Set Home (:SHP#).  Therefore the firmware's
        normal Go Home (:hF#) is authoritative for both axes.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self._motion_or_home_busy():
            self.log("Wait for the current move/home operation to finish before running HOME.", logging.WARNING); return

        self.dec_home_move_busy = True

        def job():
            gx_start = self._wait_for_ra_dec_idle(wait_ra=True, wait_dec=True, timeout=10.0)
            # hF has no payload reply; verify completion from live GX instead.
            self.indi.meade("@hF#")
            # Give firmware a moment to raise the slewing flags before polling.
            time.sleep(0.3)
            gx_end = self._wait_for_ra_dec_idle(wait_ra=True, wait_dec=True, timeout=120.0)
            dec_final = int(gx_end["dec_steps"])
            # RA may include tracking compensation while Go Home is settling;
            # DEC must unambiguously land on logical zero.
            if dec_final != 0:
                raise RuntimeError(
                    f"Firmware Go Home finished but DEC is not logical 0: final={dec_final}. "
                    "Firmware DEC limit may have clamped the move. "
                    "(Note: Ekos Park is :hP# and moves DEC by a further -XSHD offset after reaching Home. "
                    "Check in Diagnostics whether SET HOME cleared the offset to zero.)")
            return gx_start, gx_end

        def done(values):
            gx_start, gx_end = values
            self.log("✓ Mini HOME done - firmware Go To Home (:hF#)")
            self.logger.debug(
                "Mini HOME via hF: RA %+d->%+d DEC %+d->%+d",
                gx_start["ra_steps"], gx_end["ra_steps"],
                gx_start["dec_steps"], gx_end["dec_steps"])

        def err(message):
            self.log(f"Mini HOME failed: {message}", logging.ERROR)

        def finished():
            self.dec_home_move_busy = False

        self.run_async(job, done, err, finished)

    def keyPressEvent(self, event):
        """Keyboard slewing while the Mini Controller tab is in front.

        Arrow keys drive RA/DEC and WASD drives ALT/AZ.
        """
        handled = False
        if hasattr(self, "tabs") and self.tabs.currentWidget() is getattr(self, "mini_tab", None):
            key = event.key()
            step = float(self.mini_pa_step.value())
            if key == QtCore.Qt.Key_Left:
                self.mini_direction_move(-1, 0); handled = True
            elif key == QtCore.Qt.Key_Right:
                self.mini_direction_move(+1, 0); handled = True
            elif key == QtCore.Qt.Key_Up:
                self.mini_direction_move(0, +1); handled = True
            elif key == QtCore.Qt.Key_Down:
                self.mini_direction_move(0, -1); handled = True
            elif key == QtCore.Qt.Key_W:
                self.move_pa(+step, None); handled = True
            elif key == QtCore.Qt.Key_S:
                self.move_pa(-step, None); handled = True
            elif key == QtCore.Qt.Key_A:
                self.move_pa(None, -step); handled = True
            elif key == QtCore.Qt.Key_D:
                self.move_pa(None, +step); handled = True
        if not handled:
            super().keyPressEvent(event)

    def set_tracking(self, enabled):
        cmd = "&MT1#" if enabled else "&MT0#"
        self.meade_async(cmd, lambda r: self.log(f"Tracking {'ON' if enabled else 'OFF'} request -> {r}"))

    # ------------------------ time / location / HA ------------------------
    @staticmethod
    def _hours_to_hms(hours):
        hours = float(hours) % 24.0
        total = int(round(hours * 3600.0)) % 86400
        h, rem = divmod(total, 3600)
        m, sec = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{sec:02d}"

    @staticmethod
    def _calculate_lst_hours(utc_dt, longitude_deg_east):
        """Calculate apparent-enough GMST/LST for setup diagnostics.

        Uses the standard Meeus-style GMST polynomial. Longitude is positive
        east (INDI GEOGRAPHIC_COORD convention). This is used for display and
        cross-checking; the mount itself recalculates HA after INDI writes the
        standard time/date/location properties.
        """
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=timezone.utc)
        else:
            utc_dt = utc_dt.astimezone(timezone.utc)
        jd = 2440587.5 + utc_dt.timestamp() / 86400.0
        t = (jd - 2451545.0) / 36525.0
        gmst_deg = (
            280.46061837
            + 360.98564736629 * (jd - 2451545.0)
            + 0.000387933 * t * t
            - (t * t * t) / 38710000.0
        ) % 360.0
        return ((gmst_deg + float(longitude_deg_east)) % 360.0) / 15.0

    @staticmethod
    def _find_ci_key(mapping, exact=None, contains=None):
        exact = {x.upper() for x in (exact or [])}
        contains = [x.upper() for x in (contains or [])]
        for key in mapping:
            ku = str(key).upper()
            if ku in exact:
                return key
        for key in mapping:
            ku = str(key).upper()
            if any(x in ku for x in contains):
                return key
        return None

    def _wait_standard_time_location_properties(self, timeout=3.0):
        self.indi.request_properties()
        deadline = time.time() + timeout
        while time.time() < deadline:
            geo = self.indi.last_number_values.get("GEOGRAPHIC_COORD")
            utc = self.indi.last_text_values.get("TIME_UTC")
            if geo and utc:
                return dict(geo), dict(utc)
            time.sleep(0.05)
        raise RuntimeError(
            "GEOGRAPHIC_COORD / TIME_UTC could not be found in INDI. "
            "Check that LX200 OpenAstroTech is Connected in Ekos."
        )

    def sync_ha_time_location(self):
        """Sync Pi clock + INDI location to OAT so firmware recalculates HA.

        This deliberately uses standard INDI TIME_UTC / GEOGRAPHIC_COORD
        properties instead of inventing a private HA command. The LX200/OAT
        driver translates these into the firmware's existing time/date/site
        setters; those setters call autoCalcHa().
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if getattr(self, "ha_sync_busy", False):
            self.log("An HA update is already running.", logging.WARNING); return
        self.ha_sync_busy = True
        if hasattr(self, "ha_sync_btn"):
            self.ha_sync_btn.setEnabled(False)
        if hasattr(self, "ha_status"):
            self.ha_status.setText(_("Checking time/site..."))

        def job():
            geo, utc_prop = self._wait_standard_time_location_properties(timeout=4.0)
            lat_key = self._find_ci_key(geo, exact=["LAT"], contains=["LAT"])
            lon_key = self._find_ci_key(geo, exact=["LONG", "LON", "LONGITUDE"], contains=["LONG", "LON"])
            elev_key = self._find_ci_key(geo, exact=["ELEV", "ELEVATION"], contains=["ELEV"])
            if lat_key is None or lon_key is None:
                raise RuntimeError(f"GEOGRAPHIC_COORD element could not be parsed: {sorted(geo)}")

            lat = float(geo[lat_key])
            lon = float(geo[lon_key])
            elev = float(geo[elev_key]) if elev_key is not None else 0.0

            utc_key = self._find_ci_key(utc_prop, exact=["UTC"], contains=["UTC"])
            offset_key = self._find_ci_key(utc_prop, exact=["OFFSET"], contains=["OFFSET"])
            if utc_key is None or offset_key is None:
                raise RuntimeError(f"TIME_UTC element could not be parsed: {sorted(utc_prop)}")

            now_local = datetime.now().astimezone()
            now_utc = now_local.astimezone(timezone.utc)
            # Keep the UTC offset KStars/INDI associated with the observing
            # site.  This is safer than assuming the Pi's OS timezone always
            # follows a portable setup. Fall back to the Pi timezone only if
            # the property is not numeric.
            try:
                offset_hours = float(str(utc_prop[offset_key]).strip())
            except Exception:
                offset_hours = now_local.utcoffset().total_seconds() / 3600.0 if now_local.utcoffset() else 0.0
            if abs(lat) < 0.0001 and abs(lon) < 0.0001:
                raise RuntimeError(
                    "The INDI observing site reads 0°,0°. Check the KStars Geographic Location first."
                )

            # Send the complete vectors. Preserve any future extra coordinate
            # elements and only replace the values that should be refreshed.
            geo_out = dict(geo)
            geo_out[lat_key] = lat
            geo_out[lon_key] = lon
            if elev_key is not None:
                geo_out[elev_key] = elev
            self.indi.send_number_vector("GEOGRAPHIC_COORD", geo_out)
            time.sleep(0.15)

            utc_out = dict(utc_prop)
            utc_out[utc_key] = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
            utc_out[offset_key] = f"{offset_hours:+g}"
            self.indi.send_text_vector("TIME_UTC", utc_out)
            time.sleep(0.7)
            # The driver sets the mount date as part of this, and :SC# answers
            # twice, so drop any spare reply before reading anything back.
            self._resync_meade()

            # Match the firmware's currently published Polaris constant so the
            # displayed HA is the value *that firmware itself expects* after
            # autoCalcHa(), not a separate astrometric model.
            lst_h = self._calculate_lst_hours(now_utc, lon)
            firmware_polaris_ra_h = 3.0 + 8.0 / 3600.0  # Configuration.hpp default 03:00:08
            ha_h = (lst_h - firmware_polaris_ra_h) % 24.0

            # The INDI driver does not push the sidereal time down, and the
            # firmware never recomputes _LST on its own: it only changes when a
            # client sets it.  A stale LST silently shifts the RA home
            # reference, which makes GOTO think a target is past the RA limit,
            # trigger a meridian flip and drive DEC the wrong way.  So write it
            # and read it back.
            hours = int(lst_h)
            minutes = int((lst_h - hours) * 60)
            seconds = int(round((lst_h - hours - minutes / 60.0) * 3600))
            if seconds == 60:
                seconds = 0
                minutes += 1
            if minutes == 60:
                minutes = 0
                hours = (hours + 1) % 24
            self.indi.meade(f"@SHL{hours:02d}{minutes:02d}{seconds:02d}#")
            time.sleep(0.3)
            mount_lst = self._read_mount_lst()
            mount_date = str(self.indi.meade(":GC#")).strip().rstrip("#")
            mount_offset = str(self.indi.meade(":GG#")).strip().rstrip("#")
            mount_longitude = str(self.indi.meade(":Gg#")).strip().rstrip("#")
            return {
                "lat": lat, "lon": lon, "elev": elev,
                "utc": now_utc, "offset": offset_hours,
                "lst": lst_h, "ha": ha_h,
                "mount_lst": mount_lst, "mount_date": mount_date, "mount_offset": mount_offset,
                "mount_longitude": mount_longitude,
            }

        def done(info):
            text = (
                f"Lat {info['lat']:.4f}°  Lon {info['lon']:.4f}° | "
                f"LST {self._hours_to_hms(info['lst'])} | "
                f"OAT HA {self._hours_to_hms(info['ha'])}"
            )
            if hasattr(self, "ha_status"):
                self.ha_status.setText(_("✓ ") + text)
            self.update_site_angles()
            self.log(
                "✓ HA updated automatically: UTC=%s, UTC offset=%+g, %s (based on firmware Polaris RA 03:00:08)" % (
                    info["utc"].strftime("%Y-%m-%d %H:%M:%S"), info["offset"], text
                )
            )
            mount_lst = info.get("mount_lst")
            if mount_lst is None:
                self.log("The mount did not report its sidereal time (:XGL#); "
                         "GOTO accuracy cannot be verified.", logging.WARNING)
            else:
                drift = abs((mount_lst - info["lst"] + 12.0) % 24.0 - 12.0) * 60.0
                self.log(f"Mount LST now {self._hours_to_hms(mount_lst)} "
                         f"(date {info.get('mount_date')}, UTC offset {info.get('mount_offset')}, "
                         f"longitude {info.get('mount_longitude')})")
                if drift > 5.0:
                    self.log(
                        f"⚠ The mount's sidereal time is still {drift:.0f} min out after the update; "
                        "writing date, time, UTC offset and site directly and trying again.", logging.WARNING)
                    self.repair_mount_clock()
                if hasattr(self, "ha_status"):
                    self.ha_status.setText(self.ha_status.text() +
                                           (_(" — mount LST OK") if drift <= 5.0 else _(" — fixing mount clock...")))

        def err(message):
            if hasattr(self, "ha_status"):
                self.ha_status.setText(_("✗ HA update failed - check the log"))
            self.log(f"Automatic HA update failed: {message}", logging.ERROR)

        def finished():
            self.ha_sync_busy = False
            if hasattr(self, "ha_sync_btn"):
                self.ha_sync_btn.setEnabled(True)

        self.run_async(job, done, err, finished)

    # ------------------------ connection ------------------------
    def connect_indi(self):
        self.save_config()
        self.indi.configure(self.host_edit.text(), self.port_spin.value(), self.device_edit.currentText())
        self.indi.connect_server()

    def toggle_connection(self):
        if self.indi.running:
            self.indi.disconnect_server()
        else:
            self.connect_indi()

    def on_connection_changed(self, connected, msg):
        self._set_status_dot(self.conn_status, bool(connected), "INDI")
        if not connected and hasattr(self, "mount_status"):
            self._set_status_dot(self.mount_status, None, "Mount")
        self.connect_btn.setText(_("Disconnect") if connected else _("Connect"))
        self.log(msg)
        self.update_wizard_status()
        if connected:
            QtCore.QTimer.singleShot(1200, self.check_oat_properties)
            QtCore.QTimer.singleShot(1600, self.refresh_firmware_version)
            QtCore.QTimer.singleShot(1800, self.update_site_angles)
            QtCore.QTimer.singleShot(2600, self.run_on_connect_actions)

    def on_property_seen(self, dev, name):
        pass

    def on_mount_connection_changed(self, connected):
        if hasattr(self, "mount_status"):
            self._set_status_dot(self.mount_status, bool(connected), "Mount")
        if connected:
            self.log("OAT mount reconnected; requesting fresh INDI property definitions.")
            # Treat a mount (re)connect as a possible power cycle: :GX# DEC is
            # zero at the physical power-on position again.
            self.dec_zero_shift = 0
            self.dec_odometer_valid = True
            self.wizard_ra_done = False
            self.wizard_dec_done = False
            self.update_wizard_status()
            QtCore.QTimer.singleShot(250, self.indi.request_properties)
            QtCore.QTimer.singleShot(900, self.check_oat_properties)
            QtCore.QTimer.singleShot(1400, self.refresh_firmware_version)
        else:
            self.log("OAT mount disconnected; cached AutoPA properties were invalidated.", logging.WARNING)
            if self.pa_motion_active:
                self._abort_pa_motion("OAT mount disconnected during AutoPA movement.")

    def on_device_list_changed(self, devices):
        """Keep the picker in sync with what the INDI server announces."""
        if not hasattr(self, "device_edit"):
            return
        current = self.device_edit.currentText()
        self.device_edit.blockSignals(True)
        for name in devices:
            if self.device_edit.findText(name) < 0:
                self.device_edit.addItem(name)
        self.device_edit.setCurrentText(current)
        self.device_edit.blockSignals(False)

    def rescan_devices(self):
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        self.indi.request_properties()
        QtCore.QTimer.singleShot(1200, lambda: self.log(
            "INDI devices: " + (", ".join(self.indi.known_devices) or "none announced yet")))

    def change_device(self):
        """Switch to another device on the same INDI server."""
        name = self.device_edit.currentText().strip()
        if not name or name == self.indi.device:
            return
        self.cfg["device"] = name
        self.indi.device = name
        self.indi._clear_oat_property_cache(None)
        self.indi.request_properties()
        self.log(f"Mount device set to {name!r}; requesting its properties.")
        QtCore.QTimer.singleShot(1500, self.check_oat_properties)
        QtCore.QTimer.singleShot(2000, self.refresh_firmware_version)

    def on_device_detected(self, dev):
        if hasattr(self, "device_edit"):
            self.device_edit.setCurrentText(dev)
        self.cfg["device"] = dev

    def on_number_state(self, name, state):
        polar_vectors = tuple(x for x in (self.indi.polar_alt_vector, self.indi.polar_az_vector) if x)
        if name not in polar_vectors or not state:
            return

        # Reflect INDI state, but more importantly use it as the AutoPA motion
        # completion handshake.  The OpenAstroTech driver marks POLAR_* Busy
        # immediately after MAL/MAZ and returns it to Ok when :GX# reports that
        # the respective motor stopped.
        self.pa_status.setText(f"{name}: {state}")
        if not self.pa_motion_active or not self.pa_active_axis:
            return

        active_vector = self.indi.polar_alt_vector if self.pa_active_axis == "ALT" else self.indi.polar_az_vector
        if name != active_vector:
            return

        norm = str(state).strip().lower()
        # Any post-send setNumberVector for the active vector is an INDI-level
        # acknowledgement. GX polling below independently verifies real motion.
        self.pa_number_update_seen = True
        self.pa_ack_timer.stop()
        self.logger.debug("AutoPA INDI ACK: axis=%s vector=%s state=%s", self.pa_active_axis, name, state)
        if norm == "busy":
            self.pa_seen_busy = True
            self.pa_status.setText(f"AutoPA {self.pa_active_axis} moving")
            return
        if norm == "alert":
            self._abort_pa_motion(f"INDI {self.pa_active_axis} state=Alert")
            return
        if norm in ("ok", "idle"):
            # Do not complete from state alone. The periodic :GX# poll cross-checks
            # that the corresponding motor is actually stopped.
            self.pa_status.setText(f"AutoPA {self.pa_active_axis} Checking status")

    def check_oat_properties(self, attempt=0):
        if not self.indi.running:
            return
        self.indi.request_properties()
        missing=[]
        if not self.indi.has_meade(): missing.append("Meade command vector")
        if not self.indi.has_polar_alt(): missing.append("POLAR_ALT vector")
        if not self.indi.has_polar_az(): missing.append("POLAR_AZ vector")
        if missing and attempt < 5:
            # INDI definitions can arrive several seconds after the TCP connection.
            QtCore.QTimer.singleShot(1200, lambda: self.check_oat_properties(attempt + 1))
            return
        if missing:
            self.log("Missing OAT INDI properties: " + ", ".join(missing) + f". Current device={self.indi.device!r}. "
                     "If Diagnostics still works, this is only a property-discovery mismatch; OAT Tools will keep retrying.", logging.WARNING)
        else:
            self.log(f"LX200 OpenAstroTech properties detected: "
                     f"Meade={self.indi.meade_vector}.{self.indi.meade_element}, "
                     f"ALT={self.indi.polar_alt_vector}.{self.indi.polar_alt_element}, "
                     f"AZ={self.indi.polar_az_vector}.{self.indi.polar_az_element}")
            self.read_offsets(); self.read_pa_position()

    # ------------------------ AutoHome ------------------------
    def read_offsets(self):
        def job():
            return int(float(str(self.indi.meade(":XGHR#")).strip().rstrip("#"))), int(float(str(self.indi.meade(":XGHD#")).strip().rstrip("#")))
        def done(vals):
            # Firmware stores the opposite sign of the user's Hall->Home
            # correction.  Keep that implementation detail out of the UI.
            ra_user = -vals[0]
            dec_native_user = -vals[1]
            dec_user = dec_native_user
            self.ra_offset.setValue(ra_user); self.dec_offset.setValue(dec_user)
            self.log(f"RA Home Correction read: {ra_user:+d} step")
            self.logger.debug("Firmware raw Home offsets: RA=%+d DEC=%+d (DEC hidden in normal UI)", vals[0], vals[1])
        self.run_async(job, done)

    def _write_offset_verified(self, axis, value, attempts=6):
        """Write a firmware-native Home offset and verify the EEPROM readback.

        XSHR/XSHD have no Meade response payload.  Send the write blindly,
        then retry XGHR/XGHD so a slow INDI/serial round-trip cannot turn a
        successful EEPROM write into a false failure.
        """
        value = int(value)
        set_cmd = f"@XSHR{value}#" if axis == "RA" else f"@XSHD{value}#"
        get_cmd = ":XGHR#" if axis == "RA" else ":XGHD#"
        self.indi.meade(set_cmd)
        last = None
        last_error = None
        for n in range(max(1, int(attempts))):
            time.sleep(0.22 if n == 0 else 0.16)
            try:
                raw = self.indi.meade(get_cmd)
                last = int(float(str(raw).strip().rstrip("#")))
                if last == value:
                    return last
            except Exception as exc:
                last_error = str(exc)
        detail = f"last read={last}" if last is not None else f"last error={last_error or 'no readback'}"
        raise RuntimeError(f"{axis} Home Offset write verify failed: wrote {value}, {detail}")

    def save_offset(self, axis):
        # UI values are always user-facing Hall->Home corrections.  Firmware
        # V1.13.9 stores the opposite sign because final = Hall_midpoint-offset.
        user_val = self.ra_offset.value() if axis == "RA" else self.dec_offset.value()
        native_user = int(user_val)
        if False:  # DEC display needs no flip: the firmware direction is authoritative
            native_user = -native_user
        firmware_val = -native_user
        def job():
            return self._write_offset_verified(axis, firmware_val)
        def done(readback):
            native_shown = -int(readback)
            shown = native_shown
            if axis == "RA": self.ra_offset.setValue(shown)
            else: self.dec_offset.setValue(shown)
            self.log(f"✓ {axis} Home Correction saved and verified: {shown:+d} step")
            self.logger.debug("%s firmware raw Home offset=%+d", axis, readback)
        self.run_async(job, done)

    @staticmethod
    def _parse_gx(gx_text):
        """Parse OAT :GX# and return the authoritative live stepper positions.

        Firmware V1.13.x returns:
          state,motion,RA_steps,DEC_steps,TRK_steps,RA_coord,DEC_coord,#
        """
        parts = [x.strip() for x in str(gx_text).strip().rstrip("#").split(",")]
        if len(parts) < 4:
            raise RuntimeError(f"Unexpected :GX# response: {gx_text!r}")
        try:
            ra_steps = int(float(parts[2]))
            dec_steps = int(float(parts[3]))
        except Exception as exc:
            raise RuntimeError(f"Invalid stepper positions in :GX# response: {gx_text!r}") from exc
        return {
            "state": parts[0],
            "motion": parts[1] if len(parts) > 1 else "",
            "ra_steps": ra_steps,
            "dec_steps": dec_steps,
            "raw": str(gx_text),
        }

    def _read_ra_steps_per_degree(self):
        """Read the active firmware RA slew steps/degree via :XGR#.

        This is the authoritative runtime value from the OAT firmware and
        therefore already reflects the configured mechanics, microstepping
        and any stored steps/degree calibration. OAT Tools never derives RA
        degrees from the motor's nominal step angle.
        """
        raw = str(self.indi.meade(":XGR#")).strip().rstrip("#")
        try:
            spd = float(raw)
        except Exception as exc:
            raise RuntimeError(f"Invalid :XGR# RA steps/degree response: {raw!r}") from exc
        if not math.isfinite(spd) or spd <= 0.0:
            raise RuntimeError(f"Invalid :XGR# RA steps/degree value: {spd!r}")
        self.ra_steps_per_degree_live = spd
        return spd

    def _ra_steps_for_degrees(self, degrees, steps_per_degree=None):
        """Convert user-facing RA degrees to firmware MXr slew steps."""
        spd = steps_per_degree if steps_per_degree is not None else self.ra_steps_per_degree_live
        if spd is None:
            raise RuntimeError("RA steps/degree is unknown; firmware :XGR# read is required")
        return int(round(float(degrees) * float(spd)))

    def _read_dec_steps_per_degree(self):
        """Read the active firmware DEC slew steps/degree via :XGD#.

        This is intentionally preferred over deriving the value from motor
        angle/pulley assumptions. :XGD# reflects the firmware's real runtime /
        EEPROM calibrated value, including DEC slew microstepping.
        """
        raw = str(self.indi.meade(":XGD#")).strip().rstrip("#")
        try:
            spd = float(raw)
        except Exception as exc:
            raise RuntimeError(f"Invalid :XGD# DEC steps/degree response: {raw!r}") from exc
        if not math.isfinite(spd) or spd <= 0.0:
            raise RuntimeError(f"Invalid :XGD# DEC steps/degree value: {spd!r}")
        self.dec_steps_per_degree_live = spd
        return spd

    def _dec_steps_for_degrees(self, degrees, steps_per_degree=None):
        """Convert user-facing DEC degrees to firmware MXd slew steps.

        Normal callers provide/read the active :XGD# value. The configured
        fallback exists only for a defensive last resort and is never inferred
        from the motor's 0.9° step angle alone.
        """
        spd = steps_per_degree
        if spd is None:
            spd = self.dec_steps_per_degree_live
        if spd is None:
            spd = float(self.cfg.get("dec_steps_per_degree_fallback", 314.1666667))
        return int(round(float(degrees) * float(spd)))

    def _wait_for_dec_idle(self, timeout=30.0, poll=0.20):
        """Wait for DEC motion to finish, then return the latest parsed :GX#."""
        deadline = time.time() + float(timeout)
        last = None
        while time.time() < deadline:
            last = self._parse_gx(self.indi.meade(":GX#"))
            motion = last.get("motion", "")
            # :GX# motion char #2 is DEC ('d'/'D' moving, '-' stopped).
            dec_moving = len(motion) > 1 and motion[1] in ("d", "D")
            if not dec_moving:
                return last
            time.sleep(float(poll))
        raise TimeoutError(f"DEC did not become idle within {timeout:.0f}s; last GX={last}")

    def dec_jog_degrees(self, user_degrees, require_manual=False, source="DEC"):
        """Shared DEC degree jog path for Manual Home and Mini Controller."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if require_manual and not self.dec_manual_active:
            self.log("Start the DEC manual home first.", logging.WARNING); return
        if self.dec_jog_busy or self.dec_home_move_busy or self.set_home_busy:
            self.log("A DEC move is being processed. Press again once it finishes.", logging.WARNING); return

        user_degrees = float(user_degrees)
        self.dec_jog_busy = True

        def job():
            # Refresh from firmware for every user jog. This makes a changed /
            # EEPROM-calibrated XGD value authoritative without restarting Tools.
            spd = self._read_dec_steps_per_degree()
            user_steps = self._dec_steps_for_degrees(user_degrees, spd)
            motor_steps = user_steps
            if motor_steps == 0:
                raise RuntimeError(f"DEC {user_degrees:+g}° converts to 0 step (XGD={spd})")
            gx_start = self._parse_gx(self.indi.meade(":GX#"))
            self.indi.meade(f"@MXd{motor_steps}#")
            gx_end = self._wait_for_dec_idle(timeout=90.0)
            return spd, motor_steps, gx_start["dec_steps"], gx_end["dec_steps"]

        def done(values):
            spd, motor_steps, start_pos, final_pos = values
            moved = int(final_pos) - int(start_pos)
            if abs(moved - motor_steps) > max(4, abs(motor_steps) // 100):
                # Firmware moveSteppersTo() clamps DEC to _decLowerLimit/_decUpperLimit,
                # which are counted from the *current* logical zero (power-on
                # position until SET HOME).  A clamped jog silently stops short.
                self.log(
                    f"DEC jog clamped by firmware DEC limit: requested {motor_steps:+d} step, "
                    f"moved {moved:+d} step. The DEC limits (:XSDLL/:XSDLU) are relative to the power-on position. "
                    "If Home is outside the limits, widen them or set them to 0 and configure them again after SET HOME.",
                    logging.WARNING)
            if self.dec_manual_active:
                self.dec_manual_status.setText(
                    "Adjusting - press 'SET HOME' once DEC is at its real Home position.")
            self.log(f"{source}: DEC {'↑' if user_degrees>0 else '↓'} {abs(user_degrees):g}° move sent")
            self.logger.debug(
                "%s DEC jog: user=%+.3f deg, XGD=%.9f step/deg, motor=%+d step, GX %+d -> %+d",
                source, user_degrees, spd, motor_steps, start_pos, final_pos)

        def err(message):
            self.log(f"DEC jog failed: {message}", logging.ERROR)

        def finished():
            self.dec_jog_busy = False

        self.run_async(job, done, err, finished)

    def start_dec_manual_home(self):
        """Enter the manual-adjustment phase on the unified Home page."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        self._mark_home_adjusting(
            "Ready to fine-adjust - align RA/DEC if needed and then press SET HOME.")
        # Refresh DEC steps/degree in the background so the first degree jog is
        # ready, while each actual jog still re-reads :XGD# authoritatively.
        self.run_async(self._read_dec_steps_per_degree)

    def _mark_home_adjusting(self, text=None):
        """Mark Final Home dirty after any manual RA/DEC adjustment."""
        self.dec_manual_active = True
        self.wizard_dec_done = False
        if hasattr(self, "dec_manual_status"):
            self.dec_manual_status.setText(text or "Fine-adjusting - press SET HOME once the position is right.")
        self.update_wizard_status()

    def home_ra_jog_move(self, user_degrees):
        """RA fine adjustment on the Home page, expressed in degrees."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self.home_busy or self.home_adjust_busy or self.mini_motion_busy or self.set_home_busy:
            self.log("Wait for the current RA/home move to finish before pressing again.", logging.WARNING); return

        user_degrees = float(user_degrees)
        self.home_adjust_busy = True
        self._mark_home_adjusting("Fine-adjusting RA/DEC - press SET HOME once the position is right.")

        def job():
            spd = self._read_ra_steps_per_degree()
            steps = self._ra_steps_for_degrees(user_degrees, spd)
            if steps == 0:
                raise RuntimeError(f"RA {user_degrees:+g}° converts to 0 step (XGR={spd})")
            gx_start = self._parse_gx(self.indi.meade(":GX#"))
            self.indi.meade(f"@MXr{steps}#")
            gx_end = self._wait_for_ra_dec_idle(wait_ra=True, timeout=90.0)
            return spd, steps, gx_start, gx_end

        def done(values):
            spd, steps, gx_start, gx_end = values
            self.log(f"Home: RA {'←' if user_degrees < 0 else '→'} {abs(user_degrees):g}° fine adjustment")
            self.logger.debug(
                "Home RA fine jog: user=%+.3f deg, XGR=%.9f step/deg, motor=%+d step, GX RA %+d->%+d",
                user_degrees, spd, steps, gx_start["ra_steps"], gx_end["ra_steps"])

        def err(message):
            self.log(f"RA fine adjustment failed: {message}", logging.ERROR)

        def finished():
            self.home_adjust_busy = False

        self.run_async(job, done, err, finished)


    def dec_manual_jog_move(self, user_degrees):
        # Unified Home page: there is no separate DEC 'start' step.  The first
        # DEC jog simply marks the final Home as dirty; the shared degree->step
        # path still reads firmware :XGD# for every movement.
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self.dec_jog_busy or self.dec_home_move_busy:
            self.log("A DEC move is being processed. Press again once it finishes.", logging.WARNING); return
        self._mark_home_adjusting("Fine-adjusting RA/DEC - press SET HOME once the position is right.")
        self.dec_jog_degrees(user_degrees, require_manual=False, source="Home")

    def _read_gx_dec_int(self):
        return int(self._parse_gx(self.indi.meade(":GX#"))["dec_steps"])

    def _clear_legacy_dec_homing_offset(self):
        """Zero a stale firmware DEC homing offset (XSHD) if present.

        Firmware V1.13.x uses the DEC homing offset only for Hall homing and
        for Park: after :hP# reaches Home(0) it slews DEC to -offset.  Older
        OAT Tools versions wrote XSHD for the manual DEC Home; that leftover
        makes Ekos Park stop away from the Home you just set.  Returns the
        value that was cleared (0 when nothing had to be done).
        """
        try:
            current = int(float(str(self.indi.meade(":XGHD#")).strip().rstrip("#")))
        except Exception as exc:
            self.logger.warning("XGHD read failed before SET HOME: %s", exc)
            return 0
        if current == 0:
            return 0
        self._write_offset_verified("DEC", 0)
        return current

    def _firmware_set_home(self, verify_timeout=3.0):
        """Execute firmware Set Home (:SHP#) and verify it via :GX#.

        :SHP# calls Mount::setHome(false) — the very same routine the LCD
        "Set home pos?" prompt uses — which zeroes the RA, DEC, TRK and GUIDE
        steppers at their current positions.  It answers a single '1' without
        a '#'.  With libindi >= 2.0.4 the ':' and '&' wrappers both route it
        through getCommandChar(); very old drivers reply with an error text
        even though the command was executed.  The *authoritative* success
        check is therefore the live :GX# readback, not the reply character.
        Returns (gx_before, reply, gx_after).
        """
        gx_before = self._wait_for_ra_dec_idle(wait_ra=True, wait_dec=True, timeout=30.0)

        def try_once(prefix):
            try:
                reply = str(self.indi.meade(prefix + "SHP#", timeout=8.0)).strip().rstrip("#")
            except Exception as exc:
                reply = f"<{exc}>"
            gx_after = None
            deadline = time.time() + float(verify_timeout)
            while time.time() < deadline:
                time.sleep(0.15)
                gx_after = self._parse_gx(self.indi.meade(":GX#"))
                if int(gx_after["ra_steps"]) == 0 and int(gx_after["dec_steps"]) == 0:
                    return reply, gx_after, True
            return reply, gx_after, False

        reply, gx_after, ok = try_once(":")
        if not ok:
            self.logger.warning("SHP via ':' not verified (reply=%r, GX=%s); retrying with '&'",
                                reply, gx_after and gx_after.get("raw"))
            reply, gx_after, ok = try_once("&")
        if not ok:
            raise RuntimeError(
                "Set Home verify failed: firmware did not re-zero both axes "
                f"(reply={reply!r}, GX={gx_after and gx_after.get('raw')!r})")
        return gx_before, reply, gx_after

    def finish_dec_manual_home(self):
        """Finalize the whole Homing workflow with firmware Set Home (:SHP#).

        Workflow:
          1) RA AutoHome finds Hall center and automatically applies saved RA
             homing correction from EEPROM.
          2) User manually places sensorless DEC at its physical Home.
          3) One final Set Home redefines the *current* RA and DEC positions as
             logical zero. This is the same firmware path used by LCD Set Home.

        No DEC XSHD/XGHD homing offset is written or required; a stale one is
        cleared so that firmware Park lands on this Home.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self._motion_or_home_busy():
            self.log("Wait for the current motor/home move to finish before pressing SET HOME.", logging.WARNING); return

        # :SHP# stores the mount's own idea of sidereal time as the RA home
        # reference. Doing that with a stale LST silently breaks every later
        # GOTO, so check before writing anything.
        drift = self.mount_lst_drift_minutes()
        if drift is not None and drift[0] > 5.0:
            minutes, mount_lst, computed = drift
            answer = QtWidgets.QMessageBox.warning(
                self, _("SET HOME"), _(
                    "The mount's sidereal time is {minutes:.0f} min away from the time computed for your site "
                    "(mount {mount}, expected {expected}).\n\n"
                    "SET HOME stores that value as the RA reference, so a GOTO may flip across the meridian and "
                    "drive DEC the wrong way.\n\n"
                    "Press No to fix the mount clock now (date, time, UTC offset, site and LST are written "
                    "directly), then run SET HOME again. Continue anyway?").format(
                        minutes=minutes, mount=self._hours_to_hms(mount_lst),
                        expected=self._hours_to_hms(computed)),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if answer != QtWidgets.QMessageBox.Yes:
                self.log(f"SET HOME cancelled: mount LST is {minutes:.0f} min off "
                         f"({self._hours_to_hms(mount_lst)} vs {self._hours_to_hms(computed)}). "
                         "Fixing the mount clock now.", logging.WARNING)
                self.repair_mount_clock()
                return

        self.set_home_busy = True
        self.dec_manual_save_btn.setEnabled(False)
        self.dec_manual_status.setText(_("Running SET HOME..."))

        eeprom_mode = self.cfg.get("dec_home_offset_mode") == "eeprom"

        def job():
            cleared = 0
            dec_before_offset = None
            if not eeprom_mode and self.cfg.get("clear_dec_homing_offset_on_set_home", True):
                cleared = self._clear_legacy_dec_homing_offset()
            spd = None
            try:
                spd = self._read_dec_steps_per_degree()
            except Exception:
                pass
            gx_before, reply, gx_after = self._firmware_set_home()
            if eeprom_mode and self.dec_odometer_valid:
                # Store the negated power-on -> Home delta, so :MXd{-offset}
                # drives to Home and :MXd{offset} back.
                delta = int(gx_before["dec_steps"]) + int(self.dec_zero_shift)
                if delta:
                    self._write_offset_verified("DEC", -delta)
                    dec_before_offset = delta
            return cleared, spd, gx_before, reply, gx_after, dec_before_offset

        def done(values):
            cleared, spd, gx_before, reply, _gx_after, stored_eeprom = values
            ra_before = int(gx_before["ra_steps"]); dec_before = int(gx_before["dec_steps"])
            # DEC odometer: the position that just became 0 is absorbed into the shift.
            dec_since_poweron = None
            if self.dec_odometer_valid:
                dec_since_poweron = dec_before + int(self.dec_zero_shift)
                self.dec_zero_shift = 0
                self.cfg["dec_home_offset_steps"] = int(dec_since_poweron)
                self.cfg["dec_home_saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.cfg["dec_home_steps_per_degree"] = spd
                try: self.save_config()
                except Exception as exc: self.logger.warning("config save failed: %s", exc)
                self._update_dec_restore_label()
            else:
                self.dec_zero_shift = 0
                self.dec_odometer_valid = True
            self.dec_manual_active = False
            self.wizard_dec_done = True
            self.dec_manual_status.setText(_("✓ SET HOME done - the current RA/DEC is Home(0)."))
            if cleared:
                self.log(f"A DEC homing offset left by an older version (XSHD={cleared:+d}) was cleared to zero. "
                         "Ekos Park now stops exactly at this DEC Home.", logging.WARNING)
            if stored_eeprom:
                self.log(f"The DEC Home offset was stored on the mount EEPROM (XSHD={-int(stored_eeprom):+d}). "
                         "Firmware Park (:hP#) moves DEC by this offset as well, so before powering off "
                         "use the 'Move DEC to power-off position' button.", logging.WARNING)
            self.log(f"✓ SET HOME done - firmware setHome() (same as LCD Set Home), SHP reply={reply}, GX RA=0 / DEC=0 verified")
            if dec_since_poweron is not None:
                self.log(f"DEC Home recorded: from the power-on position, {dec_since_poweron:+d} step saved (can be restored in the next session)")
            self.logger.debug(
                "Final Set Home via SHP: reply=%s, before RA=%+d DEC=%+d -> RA=0 DEC=0, dec_since_poweron=%s",
                reply, ra_before, dec_before, dec_since_poweron)
            self.update_wizard_status()

        def err(message):
            self.dec_manual_status.setText(_("✗ SET HOME failed - check the log."))
            self.log(f"SET HOME failed: {message}", logging.ERROR)

        def finished():
            self.set_home_busy = False
            self.dec_manual_save_btn.setEnabled(True)

        self.run_async(job, done, err, finished)

    def _update_dec_restore_label(self):
        if not hasattr(self, "dec_restore_btn"):
            return
        off = self.cfg.get("dec_home_offset_steps")
        if off is None:
            self.dec_restore_btn.setEnabled(False)
            self.dec_restore_btn.setText(_("Restore DEC Home — none saved"))
            return
        spd = self.cfg.get("dec_home_steps_per_degree")
        deg = f" ≈ {float(off)/float(spd):+.2f}°" if spd else ""
        self.dec_restore_btn.setEnabled(True)
        self.dec_restore_btn.setText(
            f"Restore DEC Home ({int(off):+d} step{deg}, {self.cfg.get('dec_home_saved_at','')})")

    def restore_saved_dec_home(self, confirm=True):
        """Replay the recorded power-on→Home DEC delta, then SET HOME.

        A sensorless DEC cannot be homed by the firmware after a power cycle;
        the LCD flow simply assumes the mount was left at Home.  This gives a
        comparable convenience for people who always power the mount on in the
        same DEC position (e.g. parked, or at a mechanical mark): the tool
        stores the DEC steps travelled from power-on to the last SET HOME and
        can replay them.  Guarded so it only runs when DEC has not moved since
        power-on as far as the tool can tell.
        """
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self._motion_or_home_busy():
            self.log("Wait for the current move/home operation to finish.", logging.WARNING); return
        off = self._stored_dec_home_offset()
        if off is None:
            self.log("No saved DEC Home record. Set DEC to Home first and run SET HOME.", logging.WARNING); return
        off = int(off)
        if not self.dec_odometer_valid or self.dec_zero_shift != 0:
            self.log("DEC has already moved or been reset in this session. Restoring is only possible right after power-on (or right after a reconnect).", logging.WARNING); return
        ans = QtWidgets.QMessageBox.Yes if not confirm else QtWidgets.QMessageBox.question(
            self, _("Restore DEC Home"),
            "This re-applies the 'power-on position -> Home' DEC travel recorded at the last SET HOME, then runs SET HOME.\n\n"
            f"Move size: {off:+d} step\n\n"
            "Use this only when DEC is at the same physical position as at the last power-on (parked, for example). "
            "A different position would set a wrong Home.\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if ans != QtWidgets.QMessageBox.Yes:
            return

        self.set_home_busy = True
        self.dec_restore_btn.setEnabled(False)
        self.dec_manual_status.setText(_("Moving to the saved DEC Home..."))

        def job():
            stored = self._stored_dec_home_offset(allow_mount=True)
            if stored is not None and stored != off:
                self.logger.debug("Using the mount's DEC offset %+d instead of the config value %+d", stored, off)
            move = int(stored if stored is not None else off)
            gx0 = self._wait_for_dec_idle(timeout=10.0)
            if int(gx0["dec_steps"]) != 0:
                raise RuntimeError(
                    f"GX DEC={gx0['dec_steps']} ≠ 0: DEC has already moved. Restoring is only possible right after power-on.")
            if move != 0:
                self.indi.meade(f"@MXd{move}#")
                gx1 = self._wait_for_dec_idle(timeout=120.0)
                moved = int(gx1["dec_steps"])
                if abs(moved - move) > max(4, abs(move) // 100):
                    raise RuntimeError(
                        f"DEC moved {moved:+d} of {move:+d} step (firmware DEC limit clamped?). SET HOME was not executed.")
            cleared = 0
            if self.cfg.get("clear_dec_homing_offset_on_set_home", True):
                cleared = self._clear_legacy_dec_homing_offset()
            gx_before, reply, gx_after = self._firmware_set_home()
            return cleared, reply

        def done(values):
            cleared, reply = values
            self.dec_zero_shift = 0
            self.dec_odometer_valid = True
            self.dec_manual_active = False
            self.wizard_dec_done = True
            self.dec_manual_status.setText(_("✓ Saved DEC Home restored and SET HOME done - the current RA/DEC is Home(0)."))
            if cleared:
                self.log(f"DEC homing offset (XSHD={cleared:+d}) was cleared to zero.", logging.WARNING)
            self.log(f"✓ Saved DEC Home restored ({off:+d} step move, SHP reply={reply}). "
                     "If you have not run RA AutoHome yet, running it now keeps the DEC Home.")
            self.update_wizard_status()

        def err(message):
            self.dec_manual_status.setText(_("✗ DEC Home restore failed - check the log."))
            self.log(f"DEC Home restore failed: {message}", logging.ERROR)

        def finished():
            self.set_home_busy = False
            self._update_dec_restore_label()

        self.run_async(job, done, err, finished)

    def move_dec_to_saved_home(self):
        """Move DEC only back to the final logical Home coordinate 0."""
        if not self.indi.running:
            self.log("An INDI connection is required.", logging.WARNING); return
        if self.dec_jog_busy or self.dec_home_move_busy or self.home_busy:
            self.log("Wait for the current move/home operation to finish before moving DEC to Home.", logging.WARNING); return

        self.dec_home_move_busy = True
        if hasattr(self, "dec_manual_status"):
            self.dec_manual_status.setText(_("Moving to DEC Home(0)..."))

        def job():
            gx_start = self._wait_for_dec_idle(timeout=10.0)
            current = int(gx_start["dec_steps"])
            delta = -current
            if delta != 0:
                self.indi.meade(f"@MXd{delta}#")
                gx_end = self._wait_for_dec_idle(timeout=90.0)
            else:
                gx_end = gx_start
            final = int(gx_end["dec_steps"])
            if final != 0:
                raise RuntimeError(
                    f"DEC Go To Home did not reach logical 0: final={final}. "
                    "Firmware DEC limit may have clamped the move.")
            return current, delta, final

        def done(values):
            current, delta, final = values
            if hasattr(self, "dec_manual_status"):
                self.dec_manual_status.setText(_("✓ Moved to the DEC Home(0) position."))
            self.log("✓ DEC Home move done - logical 0")
            self.logger.debug(
                "DEC Home move: current=%+d delta=%+d final=%+d", current, delta, final)

        def err(message):
            if hasattr(self, "dec_manual_status"):
                self.dec_manual_status.setText(_("✗ DEC Home move failed - check the log."))
            self.log(f"DEC Home move failed: {message}", logging.ERROR)

        def finished():
            self.dec_home_move_busy = False

        self.run_async(job, done, err, finished)

    def cancel_dec_manual_home(self):
        if not self.dec_manual_active:
            return
        self.dec_manual_active = False
        self.wizard_dec_done = False
        self.dec_manual_status.setText(_("Cancelled - EEPROM values were not changed and the physical position is unchanged."))
        self.log("DEC adjustment cancelled; logical Home was not changed.")
        self.update_wizard_status()

    def start_home(self, axes):
        if self.home_busy:
            self.log("AutoHome is already running.", logging.WARNING); return
        self.home_sequence = list(axes)
        self.home_busy = True
        self._start_next_home()

    def _start_next_home(self):
        if not self.home_sequence:
            self.home_busy = False; self.home_timer.stop(); self.log("AutoHome sequence finished."); self.read_offsets(); return
        axis = self.home_sequence.pop(0)
        rng = max(5, self.home_range.value())
        # MHR/MHD return a single character with no '#'.  Some Astroberry
        # libindi builds are unreliable when that reply is routed through the
        # generic Meade text property.  We do not actually need the character:
        # send the command blind ('@' => driver transmits ':' command) and use
        # GX/XGAH to verify progress/result.
        if axis == "RA": cmd = f"@MHR{self.ra_dir.currentData()}{rng}#"
        else: cmd = f"@MHD{self.dec_dir.currentData()}{rng}#"
        self.log(f"Starting {axis} AutoHome (no-reply mode): {cmd[1:]}")
        def job():
            # Hall homing ends with firmware setHome(false), which re-zeroes DEC
            # *without moving it*.  Remember where DEC was so the DEC odometer
            # (steps since power-on) stays valid and a DEC already sitting at
            # Home(0) is not declared invalid afterwards.
            try:
                dec_now = int(self._parse_gx(self.indi.meade(":GX#"))["dec_steps"])
            except Exception:
                dec_now = None
            self.indi.meade(cmd)
            return dec_now
        def done(dec_now):
            self.home_dec_at_start = dec_now
            self.home_active_axis = axis
            self.home_idle_polls = 0
            # Give firmware a short moment to enter Homing before polling.
            QtCore.QTimer.singleShot(350, self.home_timer.start)
        self.run_async(job, done)

    def poll_home_state(self):
        if not self.home_busy:
            self.home_timer.stop(); return

        axis = getattr(self, "home_active_axis", "")

        # Query mount status and homing state together.  Firmware V1.13.9 may
        # report WTF_RESULT before a valid homing result has been established;
        # GX='Homing' is the authoritative indication that the sequence is live.
        def job():
            gx = self.indi.meade(":GX#")
            try:
                xgah = self.indi.meade(":XGAH#")
            except Exception:
                xgah = "|"
            return gx, xgah

        def done(values):
            gx, result = values
            parts = str(result).split("|")
            ra = parts[0].strip() if len(parts)>0 else "?"
            dec = parts[1].strip() if len(parts)>1 else "?"
            if ra == "WTF_RESULT": ra = "NOT INITIALIZED"
            if dec == "WTF_RESULT": dec = "NOT INITIALIZED"
            mount_state = str(gx).split(",", 1)[0].strip()
            self.home_status.setText(f"Mount: {mount_state}   RA: {ra}   |   DEC: {dec}")

            active = ra if axis == "RA" else dec
            failures = ("CANT MOVE OFF SENSOR", "CANT FIND SENSOR BEGIN", "CANT FIND SENSOR END")

            if mount_state == "Homing" or active in (
                "IN PROGRESS", "MOVE_OFF", "MOVING_OFF", "STOP_AT_TIME",
                "WAIT_FOR_STOP", "START_FIND_START", "FINDING_START",
                "FINDING_START_REVERSE", "FINDING_END", "RANGE_FOUND"
            ):
                return
            if active == "SUCCEEDED":
                self.home_timer.stop(); self.log(f"✓ {axis} AutoHome succeeded")
                if axis == "RA":
                    self.wizard_ra_done = True
                    dec_at_start = self.home_dec_at_start
                    if dec_at_start is None:
                        self.dec_odometer_valid = False
                    else:
                        self.dec_zero_shift += int(dec_at_start)
                    if self.wizard_dec_done:
                        # RA Hall AutoHome ends with firmware setHome(false): DEC is
                        # re-zeroed where it currently is.  If DEC was already at
                        # Home(0) the Final Home is still correct; otherwise it moved.
                        if dec_at_start == 0:
                            self.log("RA AutoHome done - DEC was at Home(0), so the Final Home is still valid.")
                        else:
                            self.wizard_dec_done = False
                            self.log(
                                f"RA AutoHome re-zeroed DEC at its current position (GX DEC={dec_at_start}) and re-zeroed it to "
                                "Adjust DEC back to Home and run SET HOME again.",
                                logging.WARNING)
                    self.update_wizard_status()
                self._start_next_home(); return
            if active in failures:
                self.home_timer.stop(); self.log(f"✗ {axis} AutoHome failed: {active}", logging.ERROR)
                if axis == "RA": self.wizard_ra_done = False
                self.home_busy=False; self.home_sequence=[]; self.update_wizard_status(); return
            if active in ("NOT INITIALIZED", "NEVER RUN", "", "?"):
                # If the blind start command was rejected (e.g. AutoHome was not
                # compiled), GX never enters Homing.  Allow a couple of polls
                # before declaring it did not start.
                self.home_idle_polls = getattr(self, "home_idle_polls", 0) + 1
                if self.home_idle_polls >= 3:
                    self.home_timer.stop()
                    self.log(f"{axis} AutoHome did not enter Homing. Check Hall AutoHome firmware configuration.", logging.ERROR)
                    self.home_busy=False; self.home_sequence=[]
                return
            # Unknown result: do not move to the next axis automatically.
            self.home_timer.stop()
            self.log(f"{axis} AutoHome ended with unexpected state: {active!r} (GX={mount_state})", logging.ERROR)
            self.home_busy=False; self.home_sequence=[]

        self.run_async(job, done)

    def start_offset_cal(self, axis):
        if self.ra_cal_active or self.dec_cal_active or self.home_busy:
            self.log("Finish/cancel the current calibration/home first.", logging.WARNING); return
        get_cmd = ":XGHR#" if axis=="RA" else ":XGHD#"
        set_zero = "@XSHR0#" if axis=="RA" else "@XSHD0#"
        self.log(f"{axis} offset calibration: backing up current offset, setting 0, then homing to Hall center.")
        def job():
            old = int(float(str(self.indi.meade(get_cmd)).strip().rstrip("#")))
            # Use the same verified EEPROM path as a normal save.
            self._write_offset_verified(axis, 0)
            return old
        def done(old):
            if axis=="RA":
                self.ra_cal_active=True; self.ra_cal_old_offset=old; self.ra_cal_jog=0; self.ra_cal_label.setText(f"RA: previous correction {-old:+d}, total jog 0")
            else:
                self.dec_cal_active=True; self.dec_cal_old_offset=old; self.dec_cal_jog=0; self.dec_cal_label.setText(f"DEC: previous correction {-old:+d}, total jog 0")
            self.start_home([axis])
        self.run_async(job, done)

    def cal_jog(self, steps):
        axis = "RA" if self.ra_cal_active else ("DEC" if self.dec_cal_active else None)
        if not axis:
            self.log("Start the offset calibration first.", logging.WARNING); return
        if self.home_busy:
            self.log("Jog after AutoHome has finished.", logging.WARNING); return
        # :MXr/:MXd are movement commands and do not provide a reliable
        # one-character acknowledgement through the LX200 OpenAstroTech
        # generic Meade property.  0.1.3 incorrectly used '&' (one-char
        # response mode), which could consume an unrelated/garbled byte even
        # though the motor move itself had been accepted.  Send blind ('@')
        # just like XSHR/XSHD and count the jog once INDI accepts the command.
        cmd = f"@MXr{steps}#" if axis=="RA" else f"@MXd{steps}#"
        def done(_result):
            if axis=="RA":
                self.ra_cal_jog += steps
                total = self.ra_cal_jog
                self.ra_cal_label.setText(f"RA: total jog {total:+d} step")
            else:
                self.dec_cal_jog += steps
                total = self.dec_cal_jog
                self.dec_cal_label.setText(f"DEC: total jog {total:+d} step")
            self.log(f"{axis} jog {steps:+d} step sent (no-reply), total {total:+d}")
        self.meade_async(cmd, done)

    def finish_offset_cal(self):
        axis = "RA" if self.ra_cal_active else ("DEC" if self.dec_cal_active else None)
        if not axis:
            self.log("No active calibration.", logging.WARNING); return
        jog = self.ra_cal_jog if axis=="RA" else self.dec_cal_jog
        # Firmware V1.13.9 homes to: Hall_midpoint - stored_offset.
        # Therefore a manual jog of +N from Hall midpoint to the true mechanical
        # Home must be stored as -N (and vice versa).
        val = -jog
        def job():
            return self._write_offset_verified(axis, val)
        def done(readback):
            shown = -int(readback)
            self.log(f"✓ {axis} Home Correction saved: {shown:+d} step. Re-verifying with AutoHome.")
            self.logger.debug("%s calibration raw firmware offset=%+d", axis, readback)
            if axis=="RA":
                self.ra_offset.setValue(shown); self.ra_cal_active=False
                self.ra_cal_label.setText(f"RA: Home Correction {shown:+d} Save")
            else:
                self.dec_offset.setValue(shown); self.dec_cal_active=False
                self.dec_cal_label.setText(f"DEC: Home Correction {shown:+d} Save")
            self.start_home([axis])
        self.run_async(job, done)

    def cancel_offset_cal(self):
        axis = "RA" if self.ra_cal_active else ("DEC" if self.dec_cal_active else None)
        if not axis: return
        old = self.ra_cal_old_offset if axis=="RA" else self.dec_cal_old_offset
        def job():
            return self._write_offset_verified(axis, old)
        def done(readback):
            shown = -int(readback)
            if axis=="RA":
                self.ra_cal_active=False; self.ra_offset.setValue(shown); self.ra_cal_label.setText(f"RA: cancelled, correction {shown:+d} Restore")
            else:
                self.dec_cal_active=False; self.dec_offset.setValue(shown); self.dec_cal_label.setText(f"DEC: cancelled, correction {shown:+d} Restore")
            self.log(f"{axis} calibration cancelled; previous Home Correction {shown:+d} restored and verified.")
        self.run_async(job, done)

    # ------------------------ AutoPA ------------------------
    def _pa_axis_vector(self, axis):
        return self.indi.polar_alt_vector if axis == "ALT" else self.indi.polar_az_vector

    def _pa_axis_is_busy(self, axis):
        vector = self._pa_axis_vector(axis)
        if not vector:
            return False
        return str(self.indi.last_number_states.get(vector, "")).strip().lower() == "busy"

    def _set_pa_motion_controls(self, enabled):
        for button in getattr(self, "pa_motion_buttons", []):
            button.setEnabled(bool(enabled))

    def move_pa(self, alt, az, source="manual"):
        """Queue a safe AutoPA relative move.

        POLAR_ALT/POLAR_AZ commands are serialized.  A second manual or
        automatic command is rejected until the current axis returns Busy->Ok.
        This prevents overlapping MAL/MAZ commands from corrupting a move.
        """
        if not self.indi.running:
            self.log("INDI not connected", logging.ERROR)
            return False
        if self.indi.mount_is_connected() is False:
            self.log("LX200 OpenAstroTech mount is Disconnected; AutoPA move blocked.", logging.ERROR)
            return False

        moves = []
        if alt is not None and abs(float(alt)) > 1e-9:
            moves.append(("ALT", float(alt)))
        if az is not None and abs(float(az)) > 1e-9:
            moves.append(("AZ", float(az)))
        if not moves:
            self.log("AutoPA move is 0; nothing to do.")
            return True

        for axis, value in moves:
            lo, hi = self.indi.pa_number_limits(axis)
            if value < lo or value > hi:
                self.log(
                    f"{axis} move {value:+.3f}′ exceeds INDI/driver range {lo:+.1f}..{hi:+.1f}′; command blocked.",
                    logging.ERROR)
                return False

        if self.pa_motion_active or self._pa_axis_is_busy("ALT") or self._pa_axis_is_busy("AZ"):
            self.log("AutoPA is still moving. New move command ignored.", logging.WARNING)
            return False

        # Prefer INDI POLAR_* properties because they expose a real Busy/Ok
        # handshake.  Meade fallback is retained for older driver packages.
        for axis, _value in moves:
            if axis == "ALT" and not (self.indi.has_polar_alt() or self.indi.has_meade()):
                self.log("POLAR_ALT / Meade command property is unavailable.", logging.ERROR)
                return False
            if axis == "AZ" and not (self.indi.has_polar_az() or self.indi.has_meade()):
                self.log("POLAR_AZ / Meade command property is unavailable.", logging.ERROR)
                return False

        self.pa_motion_active = True
        self.pa_move_queue = list(moves)
        self.pa_move_source = source
        self.pa_active_axis = None
        self.pa_seen_busy = False
        self.pa_completion_scheduled = False
        self._set_pa_motion_controls(False)
        self._send_next_pa_axis()
        return True

    def _send_next_pa_axis(self):
        if not self.pa_motion_active:
            return
        if not self.pa_move_queue:
            self._finish_pa_motion()
            return

        axis, value = self.pa_move_queue.pop(0)
        self.pa_active_axis = axis
        self.pa_seen_busy = False
        self.pa_number_update_seen = False
        self.pa_gx_seen_moving = False
        self.pa_completion_scheduled = False
        self.pa_axis_started_at = time.monotonic()
        self.pa_move_timeout.start()
        self.pa_ack_timer.stop()

        try:
            if axis == "ALT" and self.indi.has_polar_alt():
                vector, element = self.indi.polar_alt_vector, self.indi.polar_alt_element
                self.indi.send_number(vector, element, value)
                self.log(f"ALT move requested via INDI {vector}.{element}: {value:+.3f} arcmin")
                self.pa_ack_timer.start()
                self.pa_fallback_poll.start()
            elif axis == "AZ" and self.indi.has_polar_az():
                vector, element = self.indi.polar_az_vector, self.indi.polar_az_element
                self.indi.send_number(vector, element, value)
                self.log(f"AZ move requested via INDI {vector}.{element}: {value:+.3f} arcmin")
                self.pa_ack_timer.start()
                self.pa_fallback_poll.start()
            elif self.indi.has_meade():
                cmd = f"@MAL{value:.3f}#" if axis == "ALT" else f"@MAZ{value:.3f}#"
                # Blind command, but the Meade channel may be held by a poll;
                # send it from a worker so the window never stalls.
                self.run_async(lambda c=cmd: self.indi.meade(c), None,
                               lambda e, a=axis: self._abort_pa_motion(f"{a} move failed: {e}"))
                self.log(f"{axis} move requested via Meade fallback: {value:+.3f} arcmin")
                self.pa_fallback_poll.start()
            else:
                raise RuntimeError(f"No AutoPA control property for {axis}")
            self.pa_status.setText(f"AutoPA {axis} command sent")
        except Exception as exc:
            self._abort_pa_motion(f"{axis} move failed: {exc}")

    @staticmethod
    def _gx_axis_moving(gx, axis):
        parts = str(gx).split(",")
        if len(parts) < 2:
            return None
        motors = parts[1]
        idx = 4 if axis == "ALT" else 3
        if len(motors) <= idx:
            return None
        return motors[idx] != "-"

    def _poll_pa_fallback(self):
        if self.pa_fallback_busy:
            return
        if not self.pa_motion_active or not self.pa_active_axis:
            self.pa_fallback_poll.stop()
            return
        # Always poll :GX# even with POLAR_* available. It is the firmware's
        # independent motor-state cross-check and catches stale INDI properties.
        axis = self.pa_active_axis
        self.pa_fallback_busy = True
        def done(gx):
            self.pa_fallback_busy = False
            moving = self._gx_axis_moving(gx, axis)
            if moving:
                self.pa_gx_seen_moving = True
                self.pa_seen_busy = True
                self.pa_ack_timer.stop()
                self.pa_status.setText(f"AutoPA {axis} moving (GX confirmed)")
                return
            elapsed = time.monotonic() - self.pa_axis_started_at
            vector_present = bool(self._pa_axis_vector(axis))
            acknowledged = self.pa_number_update_seen or self.pa_gx_seen_moving
            # Meade fallback has no INDI number-state ACK; an idle GX after the
            # guard interval is sufficient. POLAR_* requires either a fresh
            # number-state update or actual GX motion before we call it done.
            can_finish = (not vector_present and elapsed > 0.8) or (acknowledged and elapsed > 0.8)
            if moving is False and can_finish:
                self.pa_fallback_poll.stop()
                self.pa_ack_timer.stop()
                self.pa_move_timeout.stop()
                if not self.pa_completion_scheduled:
                    self.pa_completion_scheduled = True
                    QtCore.QTimer.singleShot(150, self._complete_pa_axis)
        def err(e):
            self.pa_fallback_busy = False
            self._abort_pa_motion(f"AutoPA GX poll failed: {e}")
        self.run_async(lambda: self.indi.meade(":GX#"), done, err)

    def _pa_ack_timeout(self):
        if not self.pa_motion_active or not self.pa_active_axis:
            return
        if self.pa_number_update_seen or self.pa_gx_seen_moving:
            return
        axis = self.pa_active_axis
        vector = self._pa_axis_vector(axis)
        self._abort_pa_motion(
            f"AutoPA {axis} command was not acknowledged within 3s (vector={vector!r}). "
            "INDI property may be stale after a mount reconnect; properties were re-requested.")
        try:
            self.indi.request_properties()
        except Exception:
            pass

    def _complete_pa_axis(self):
        if not self.pa_motion_active:
            return
        completed = self.pa_active_axis
        self.pa_active_axis = None
        self.pa_seen_busy = False
        self.pa_number_update_seen = False
        self.pa_gx_seen_moving = False
        self.pa_completion_scheduled = False
        self.pa_ack_timer.stop()
        self.pa_move_timeout.stop()
        self.log(f"AutoPA {completed} move finished (INDI/GX verified).")
        QtCore.QTimer.singleShot(150, self._send_next_pa_axis)

    def _finish_pa_motion(self):
        source = self.pa_move_source
        self.pa_motion_active = False
        self.pa_active_axis = None
        self.pa_move_queue = []
        self.pa_seen_busy = False
        self.pa_number_update_seen = False
        self.pa_gx_seen_moving = False
        self.pa_completion_scheduled = False
        self.pa_ack_timer.stop()
        self.pa_move_timeout.stop()
        self.pa_fallback_poll.stop()
        self.pa_fallback_busy = False
        self._set_pa_motion_controls(True)
        if source == "auto":
            self.autopa_adjustment_finished = datetime.now()
            self.pa_status.setText(_("Waiting for re-measurement"))
            self.log("AutoPA correction finished; waiting for a new Ekos PAA Refresh solution.")
        else:
            self.pa_status.setText(_("AutoPA move complete"))

    def _abort_pa_motion(self, reason):
        self.pa_motion_active = False
        self.pa_active_axis = None
        self.pa_move_queue = []
        self.pa_seen_busy = False
        self.pa_number_update_seen = False
        self.pa_gx_seen_moving = False
        self.pa_completion_scheduled = False
        self.pa_ack_timer.stop()
        self.pa_move_timeout.stop()
        self.pa_fallback_poll.stop()
        self.pa_fallback_busy = False
        self._set_pa_motion_controls(True)
        self.pa_status.setText(_("AutoPA move error"))
        self.log(reason, logging.ERROR)
        if self.autopa_running:
            self.stop_autopa_watch()
            self.pa_status.setText(_("AutoPA move error"))

    def _pa_motion_timeout(self):
        self._abort_pa_motion("AutoPA move timed out. New moves are blocked until the state is checked.")

    def read_pa_position(self):
        def done(result):
            p = result.split("|")
            if len(p)>=2: self.pa_pos.setText(f"AZ: {p[0].strip()} step | ALT: {p[1].strip()} step")
            self.log(f"AutoPA positions: {result}")
        self.meade_async(":XGAA#", done)

    def pa_home(self):
        if self.pa_motion_active:
            self.log("AutoPA is moving; Zero-home request ignored.", logging.WARNING)
            return
        self.meade_async("&MAAH#", lambda r: self.log(f"AutoPA home request reply: {r}"))

    def pa_set_zero(self):
        if self.pa_motion_active:
            self.log("AutoPA is moving; Zero-save request ignored.", logging.WARNING)
            return
        ans = QtWidgets.QMessageBox.warning(self, _("Save AutoPA Zero"),
            _("This sets the current AZ/ALT position to zero and saves it to persistent storage.\n"
              "Use it only when redefining the physical reference point. Continue?"),
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if ans != QtWidgets.QMessageBox.Yes: return
        self.meade_async("&hZ#", lambda r: (self.log(f"AutoPA zero saved, reply={r}"), self.read_pa_position()))

    @staticmethod
    def _parse_paa_angle(text):
        """Parse either KStars DMS text or legacy decimal degrees."""
        s = str(text).strip().replace("−", "-").replace("＋", "+")
        # Legacy decimal-degree logs.
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", s):
            return float(s)

        # Current KStars toDMSString(), e.g. -00° 12' 34.5\".
        m = re.search(
            r"(?P<sign>[+-]?)\s*(?P<deg>\d+(?:\.\d+)?)\s*[°ºd]\s*"
            r"(?:(?P<min>\d+(?:\.\d+)?)\s*['′m])?\s*"
            r"(?:(?P<sec>\d+(?:\.\d+)?)\s*[\"″s])?",
            s, re.I)
        if not m:
            # Also accept colon D:M:S if a downstream localization emits it.
            m2 = re.search(r"(?P<sign>[+-]?)\s*(?P<deg>\d+(?:\.\d+)?):(?P<min>\d+(?:\.\d+)?)(?::(?P<sec>\d+(?:\.\d+)?))?", s)
            if not m2:
                raise ValueError(f"Unsupported PAA angle: {text!r}")
            m = m2
        deg = float(m.group("deg"))
        minutes = float(m.groupdict().get("min") or 0.0)
        seconds = float(m.groupdict().get("sec") or 0.0)
        value = deg + minutes / 60.0 + seconds / 3600.0
        return -value if m.group("sign") == "-" else value

    @staticmethod
    def _paa_line_timestamp(line, fallback_mtime):
        # Full date+time when available.
        # KStars' message pattern is "yyyy-MM-dd h:mm:ss.zzz t", so the hour
        # has no leading zero before 10:00.
        m = re.search(r"(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)", line)
        if m:
            hh, rest = m.group(2).split(":", 1)
            raw = f"{m.group(1)} {int(hh):02d}:{rest}"
            fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in raw else "%Y-%m-%d %H:%M:%S"
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass
        # Some builds print only HH:MM:SS.mmm on each line.
        m = re.search(r"(?<!\d)(\d{2}:\d{2}:\d{2}(?:\.\d+)?)(?!\d)", line)
        if m:
            day = datetime.fromtimestamp(fallback_mtime).strftime("%Y-%m-%d")
            raw = f"{day} {m.group(1)}"
            fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in raw else "%Y-%m-%d %H:%M:%S"
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                pass
        return datetime.fromtimestamp(fallback_mtime)

    @staticmethod
    def _kstars_flag(text, key):
        m = re.search(rf"^{key}\s*=\s*(\S+)", text, re.M)
        if not m:
            return None
        return m.group(1).strip().lower() in ("true", "1", "yes", "on")

    def ekos_log_roots(self):
        """Every plausible KStars 'logs' directory, best candidate first.

        KStars writes to <AppLocalDataLocation>/logs/<date>/log_HH-mm-ss.txt.
        That location is NOT always ~/.local/share/kstars: under Flatpak it is
        ~/.var/app/org.kde.kstars/data/kstars and under Snap it is inside the
        snap's data dir, and XDG_DATA_HOME can move it anywhere.  Because Ekos
        starts this extension from <AppLocalDataLocation>/extensions, the
        directory this file lives in identifies the right tree exactly, so it
        is tried first and everything else is only a fallback.
        """
        roots, seen = [], set()

        def add(path):
            if not path:
                return
            path = Path(path).expanduser()
            key = str(path)
            if key not in seen:
                seen.add(key)
                roots.append(path)

        add(self.cfg.get("ekos_log_dir") or "")
        try:
            here = Path(__file__).resolve().parent
            if here.name == "extensions":
                add(here.parent / "logs")
        except Exception:
            pass
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            add(Path(xdg) / "kstars" / "logs")
        add(Path.home() / ".local" / "share" / "kstars" / "logs")
        add(Path.home() / ".var" / "app" / "org.kde.kstars" / "data" / "kstars" / "logs")
        add(Path.home() / "snap" / "kstars" / "current" / ".local" / "share" / "kstars" / "logs")
        return roots

    def _kstarsrc_path(self):
        cfg_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        candidates = [Path(cfg_home) / "kstarsrc",
                      Path.home() / ".config" / "kstarsrc",
                      Path.home() / ".var" / "app" / "org.kde.kstars" / "config" / "kstarsrc"]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    def check_ekos_logging(self):
        """Verify that Ekos will actually write the PAA lines AutoPA reads.

        The 'PAA Refresh ... Corrected az/alt' text is emitted with
        qCInfo(KSTARS_EKOS_ALIGN).  KStars' category filter only switches the
        *.debug rules, and info messages stay enabled, so the Alignment module
        checkbox is NOT required - what matters is that KStars logs to a file
        at all (Settings > Advanced: 'Log to File', and logging not disabled).
        Returns (ok, message).
        """
        try:
            text = self._kstarsrc_path().read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        disabled = self._kstars_flag(text, "DisableLogging")
        to_file = self._kstars_flag(text, "LogToFile")
        to_default = self._kstars_flag(text, "LogToDefault")
        # kcfg defaults: DisableLogging=false, LogToFile=false, LogToDefault=true.
        file_logging = bool(to_file) and not bool(disabled) and to_default is not True
        newest, root = self._newest_log_file()
        if not file_logging:
            return False, (
                "Ekos file logging is off. In KStars Settings -> Advanced -> Logs, "
                "select 'Log to File' and press Apply. AutoPA reads the "
                "'PAA Refresh ... Corrected az ... alt ...' line that Ekos writes to a file. "
                "(The line is written even without the Alignment module checkbox.)")
        if newest is None:
            return False, (
                "File logging is on but no log file was found. Locations checked: "
                + ", ".join(str(r) for r in self.ekos_log_roots()[:3])
                + ". Restart KStars, or if you use a different path, "
                  "set the logs folder in 'ekos_log_dir' in ~/.config/oat-helper/config.json.")
        age = time.time() - newest.stat().st_mtime
        return True, (f"Ekos file logging OK - {newest.name} (updated {age:.0f}s ago, {root})")

    def _newest_log_file(self):
        """Newest Ekos log file across all candidate roots, with its root."""
        best, best_root, best_mtime = None, None, -1.0
        for root in self.ekos_log_roots():
            try:
                for fp in root.glob("*/*.txt"):
                    m = fp.stat().st_mtime
                    if m > best_mtime:
                        best, best_root, best_mtime = fp, root, m
            except Exception:
                continue
        return best, best_root

    def diagnose_paa_log(self):
        """Report exactly what the AutoPA log watcher can and cannot see."""
        def job():
            lines = []
            ok, message = self.check_ekos_logging()
            lines.append(("✓ " if ok else "✗ ") + message)
            lines.append(f"kstarsrc: {self._kstarsrc_path()}")
            for root in self.ekos_log_roots():
                try:
                    files = sorted(root.glob("*/*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
                except Exception:
                    files = []
                mark = "in use" if files else "None"
                lines.append(f"[{mark}] {root}" + (f" - files {len(files)}files, newest {files[0].name}" if files else ""))
            found = self.latest_ekos_paa(full=True)
            if found:
                signature, ts, az, alt, fp, line = found
                lines.append(f"Latest PAA Refresh: {ts:%Y-%m-%d %H:%M:%S} — az={az*60:+.3f}′ alt={alt*60:+.3f}′")
                lines.append(f"Source: {line.strip()[:200]}")
                lines.append(f"File: {fp}")
            else:
                lines.append("No PAA Refresh line was found. Run a measurement and one Refresh in the Ekos Align "
                             "and it is recorded after one Refresh.")
            return "\n".join(lines)
        self.run_async(job, lambda text: [self.log(l) for l in text.splitlines()],
                       lambda e: self.log(f"PAA log diagnosis failed: {e}", logging.ERROR))

    PAA_LINE_RE = re.compile(
        r"PAA\s+Refresh(?:\([^)]*\))?.*?Corrected\s+az(?:imuth)?:\s*(?P<az>.*?)\s+"
        r"alt(?:itude)?:\s*(?P<alt>.*?)\s+total:", re.I)
    PAA_LEGACY_RE = re.compile(
        r"PAA.*?Corrected\s+az(?:imuth)?:?\s*\(?\s*(?P<az>-?\d+(?:\.\d+)?)"
        r".*?alt(?:itude)?:?\s*(?P<alt>-?\d+(?:\.\d+)?)", re.I)

    def _scan_paa_file(self, fp, full=False):
        """Scan one Ekos log file, reading only the bytes added since last time."""
        stat = fp.stat()
        key = str(fp)
        ino, last_off = self.paa_offsets.get(key, (None, None))
        if full or ino != stat.st_ino or last_off is None or stat.st_size < (last_off or 0):
            # First look at this file (or it was rotated/truncated): read a
            # bounded tail instead of the whole file.
            base = max(0, stat.st_size - 2 * 1024 * 1024)
        else:
            base = last_off
        with fp.open("rb") as fh:
            fh.seek(base)
            raw = fh.read()
        if base > 0 and (ino != stat.st_ino or last_off is None or full):
            nl = raw.find(b"\n")
            if nl >= 0:
                base += nl + 1
                raw = raw[nl + 1:]
        offset = base
        newest = None
        for raw_line in raw.splitlines(keepends=True):
            line_offset = offset
            offset += len(raw_line)
            if not raw_line.endswith(b"\n"):
                # Partial last line: re-read it next time.
                offset = line_offset
                break
            if b"PAA" not in raw_line or b"Corrected" not in raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            m = self.PAA_LINE_RE.search(line) or self.PAA_LEGACY_RE.search(line)
            if not m:
                continue
            try:
                az = self._parse_paa_angle(m.group("az"))
                alt = self._parse_paa_angle(m.group("alt"))
            except ValueError as exc:
                self.logger.debug("PAA angle parse failed (%s): %s", exc, line)
                continue
            ts = self._paa_line_timestamp(line, stat.st_mtime)
            digest = hashlib.sha1(raw_line.rstrip(b"\r\n")).hexdigest()[:12]
            newest = (f"{fp}:{stat.st_ino}:{line_offset}:{digest}", ts, az, alt, str(fp), line)
        self.paa_offsets[key] = (stat.st_ino, offset)
        return newest

    def latest_ekos_paa(self, full=False) -> Optional[Tuple[str, datetime, float, float, str, str]]:
        """Return the newest PAA Refresh solution seen so far.

        Only the bytes appended since the previous call are parsed, so the
        2.5 s watcher tick stays cheap on a Raspberry Pi even with large Ekos
        logs.  The most recent match is cached and returned until a newer one
        appears; 'new vs old' is decided by a file/offset/content signature
        rather than the clock, because KStars timestamps and the Pi system
        clock can disagree.
        """
        candidates = []
        for root in self.ekos_log_roots():
            try:
                candidates.extend(root.glob("*/*.txt"))
            except Exception:
                continue
        try:
            files = sorted(
                candidates,
                key=lambda p: p.stat().st_mtime_ns if p.exists() else 0,
                reverse=True)[:4 if not full else 8]
        except Exception:
            files = []
        for fp in reversed(files):  # oldest first so the newest match wins
            try:
                found = self._scan_paa_file(fp, full=full)
                if found is not None:
                    self.paa_last_match = found
            except Exception as exc:
                self.logger.debug("PAA log scan skipped %s: %s", fp, exc)
        return self.paa_last_match

    def start_autopa_watch(self):
        self.save_config()
        if not ((self.indi.has_polar_alt() and self.indi.has_polar_az()) or self.indi.has_meade()):
            self.indi.request_properties()
            self.log("AutoPA control property is not ready yet. INDI definitions were requested again; try starting once more in a moment.", logging.ERROR); return
        if self.pa_motion_active or self._pa_axis_is_busy("ALT") or self._pa_axis_is_busy("AZ"):
            self.log("AutoPA motor is already moving. Wait for it to finish before starting automatic correction.", logging.WARNING); return
        if not (self.indi.has_polar_alt() and self.indi.has_polar_az()):
            self.log("POLAR_ALT/AZ properties not detected; AutoPA watcher will use Meade MAL/MAZ fallback.", logging.WARNING)
        if self.indi.mount_is_connected() is False:
            self.log("LX200 OpenAstroTech mount is Disconnected. Connect the mount in Ekos first.", logging.ERROR); return
        self.autopa_running=True; self.autopa_busy=False; self.autopa_was_moving=False
        self.autopa_seen_solution=False
        self.autopa_prev_error_arcmin=None; self.autopa_worse_count=0
        self.autopa_last_entry=datetime.now(); self.autopa_adjustment_finished=datetime.now()
        self.paa_offsets = {}
        self.paa_last_match = None
        self.autopa_last_signature = None
        self.pa_status.setText(_("Checking the log..."))

        # The baseline scan touches several log files; keep it off the GUI
        # thread so the window does not freeze on a Raspberry Pi.
        def baseline_job():
            ok, message = self.check_ekos_logging()
            return ok, message, self.latest_ekos_paa(full=True)

        def baseline_done(result):
            ok, message, baseline = result
            self.log(message, logging.INFO if ok else logging.WARNING)
            self.autopa_last_signature = baseline[0] if baseline else None
            if baseline:
                self.logger.debug("AutoPA PAA baseline signature=%s file=%s", baseline[0], baseline[4])
                self.log(f"Baseline: the existing PAA Refresh ({baseline[1]:%H:%M:%S}) is ignored; processing starts from the next Refresh.")
            else:
                self.log("No previous PAA Refresh record. Processing starts as soon as you begin Refresh in Ekos.")
            self.pa_status.setText(_("Waiting for the PAA log") if ok else "Ekos file logging needs checking")
            self.autopa_timer.start()

        self.run_async(baseline_job, baseline_done,
                       lambda e: (self.log(f"AutoPA baseline failed: {e}", logging.ERROR),
                                  self.autopa_timer.start()))
        self.wizard_pa_done = False
        self.update_wizard_status()
        self.log(f"AutoPA watcher started. Accuracy={self.accuracy.value():.0f} arcsec, max/cycle={self.max_move.value():.1f} arcmin")

    def stop_autopa_watch(self):
        self.autopa_timer.stop(); self.autopa_running=False; self.autopa_busy=False
        if not self.pa_motion_active:
            self.pa_status.setText(_("Done") if self.wizard_pa_done else "Stopped")
        self.update_wizard_status()
        self.log("AutoPA watcher stopped." if not self.wizard_pa_done else "AutoPA watcher stopped after successful polar alignment.")

    def autopa_tick(self):
        if not self.autopa_running or self.autopa_busy or self.pa_motion_active:
            return
        self.autopa_busy=True

        def job():
            return self.latest_ekos_paa()

        def status_done(sol):
            if not sol:
                waited = (datetime.now() - self.autopa_last_entry).total_seconds() if self.autopa_last_entry else 0
                self.pa_status.setText(_("No PAA log value"))
                if waited > 60 and not getattr(self, "_paa_wait_warned", False):
                    self._paa_wait_warned = True
                    self.log("No PAA Refresh line has been found for 60 seconds. Use 'Diagnose PAA log' on the AutoPA tab to "
                             "check Ekos file logging and the log path.", logging.WARNING)
                self.autopa_busy=False; return
            self._paa_wait_warned = False
            signature,ts,az_deg,alt_deg,fp,line=sol
            if signature == self.autopa_last_signature:
                self.logger.debug("AutoPA waiting: latest PAA Refresh signature unchanged (%s)", signature)
                self.autopa_busy=False; return
            previous_signature = self.autopa_last_signature
            self.autopa_last_signature = signature
            if self.wait_two.isChecked() and not getattr(self, "autopa_seen_solution", False):
                # Wait for a second error calculation before moving anything;
                # the first solve after a slew is the noisiest.
                self.autopa_seen_solution = True
                self.pa_status.setText(_("Waiting for two measurements (1/2)"))
                self.log("The first PAA solution is used as a reference only. Correction starts from the next Refresh.")
                self.autopa_busy = False
                return
            # An Ekos capture+solve takes ~25 s, so the refresh line that
            # appears right after a correction was usually measured *before*
            # it. Acting on it applies the same correction twice: the axis
            # overshoots to the mirror image of the error, the next cycle
            # corrects back, and the run oscillates forever while the
            # "residual increased" guard blames the motor direction.
            finished = getattr(self, "autopa_adjustment_finished", None)
            if finished is not None and ts <= finished + timedelta(seconds=1):
                self.pa_status.setText(_("Waiting for a measurement taken after the correction"))
                self.log(f"Ignoring a PAA solution measured at {ts.strftime('%H:%M:%S')}, before the last "
                         f"correction finished at {finished.strftime('%H:%M:%S')}. Waiting for the next Refresh.")
                self.autopa_busy = False
                return

            self.autopa_last_entry = ts
            self.log(f"New PAA Refresh accepted: {Path(fp).name} @ {ts.strftime('%H:%M:%S.%f')[:-3]}")
            self.logger.debug("PAA source line: %s", line)

            raw_alt = alt_deg * 60.0
            raw_az = az_deg * 60.0
            # Offsets are treated as the desired PAA residual/bias.  Therefore
            # convergence and movement use (measured - configured offset).
            residual_alt = raw_alt - self.alt_offset.value()
            residual_az = raw_az - self.az_offset.value()
            residual_total = math.hypot(residual_alt, residual_az)
            measured_total = math.hypot(raw_alt, raw_az)

            alt_move = -residual_alt
            az_move = +residual_az

            self.log(
                f"PAA solution: az={az_deg:.6f}° ({raw_az:+.3f}′), "
                f"alt={alt_deg:.6f}° ({raw_alt:+.3f}′), measured={measured_total:.3f}′, "
                f"target residual={residual_total:.3f}′ -> move ALT={alt_move:+.3f}′ AZ={az_move:+.3f}′"
            )

            # Per-axis direction check: after a correction, an axis whose error
            # grew instead of shrinking is almost always inverted.  Naming the
            # axis is far more useful than a generic "error increased".
            pending = self.paa_pending_move
            if pending:
                prev_alt, prev_az, moved_alt, moved_az = pending
                self.paa_pending_move = None
                for name, before, after, moved in (
                        ("ALT", prev_alt, residual_alt, moved_alt),
                        ("AZ", prev_az, residual_az, moved_az)):
                    if abs(moved) < 0.05:
                        continue
                    if abs(after) > abs(before) + max(0.2, abs(before) * 0.1):
                        self.log(
                            f"{name} error grew from {abs(before):.2f}′ to {abs(after):.2f}′ after a "
                            f"{moved:+.2f}′ move, so that axis most likely runs backwards. Set "
                            f"{name}_INVERT_DIR in Configuration_local.hpp and reflash.", logging.WARNING)

            # Direction / runaway guard: stop after two consecutive meaningful
            # increases.  Ignore small solver noise (>=0.25' or 10%).
            prev = self.autopa_prev_error_arcmin
            if prev is not None:
                margin = max(0.25, prev * 0.10)
                if residual_total > prev + margin:
                    self.autopa_worse_count += 1
                    self.log(
                        f"AutoPA residual increased {prev:.3f}′ -> {residual_total:.3f}′ "
                        f"({self.autopa_worse_count}/2).",
                        logging.WARNING)
                elif residual_total < prev:
                    self.autopa_worse_count = 0
            self.autopa_prev_error_arcmin = residual_total

            if self.autopa_worse_count >= 2:
                self.pa_status.setText(_("Error grew - stopped automatically"))
                self.log("AutoPA stopped: PAA error increased twice. Check ALT/AZ direction inversion before retrying.", logging.ERROR)
                self.stop_autopa_watch(); return

            target=self.accuracy.value()/60.0
            if residual_total <= target:
                self.wizard_pa_done = True
                self.pa_status.setText(f"Done: {residual_total*60:.0f}″")
                self.log(f"✓ Polar alignment within target: residual {residual_total*60:.0f} arcsec")
                self.update_wizard_status()
                self.stop_autopa_watch(); return

            lim=self.max_move.value()
            if abs(alt_move)>lim or abs(az_move)>lim:
                self.pa_status.setText(_("Safety limit exceeded"))
                # Refusing outright made the first correction impossible - the
                # initial error is legitimately large - and pushed people to
                # raise the limit until it no longer protected anything. Move
                # by the limit instead and let the next cycle continue.
                alt_move = max(-lim, min(lim, alt_move))
                az_move = max(-lim, min(lim, az_move))
                self.log(f"Correction larger than the {lim:.1f}′ per-axis limit; moving "
                         f"ALT {alt_move:+.1f}′ / AZ {az_move:+.1f}′ this cycle and continuing.",
                         logging.WARNING)

            if self.move_pa(alt_move,az_move,source="auto"):
                self.pa_status.setText(f"Correcting ALT {alt_move:+.2f}′ → AZ {az_move:+.2f}′")
                self.paa_pending_move = (residual_alt, residual_az, alt_move, az_move)
            else:
                # The move was rejected (axis busy, mount disconnected, ...).
                # Do not swallow this solution: retry it on the next tick.
                self.autopa_last_signature = previous_signature
                self.pa_status.setText(_("AutoPA move pending"))
            self.autopa_busy=False

        def err(e):
            self.log(f"AutoPA watcher error: {e}", logging.ERROR); self.autopa_busy=False
        self.run_async(job,status_done,err)

    # ------------------------ 0.3.0 monitor / calibration / firmware ------------------------


    def refresh_mount_monitor(self):
        if not hasattr(self,'ra_limit_bar') or not self.indi.running: return
        # Never interleave three read commands into a critical sequence.  The
        # Meade channel is serialized now, but skipping keeps SET HOME / jog
        # verification latency low and the log free of monitor noise.
        if getattr(self, 'monitor_poll_busy', False) or self._motion_or_home_busy(): return
        # Only poll while the page is actually on screen: this window usually
        # sits behind KStars, and the numbers are read, not watched.
        if hasattr(self, 'tabs') and self.tabs.currentWidget() is not getattr(self, 'monitor_tab', None):
            return
        self.monitor_poll_busy = True
        def job():
            gx=self._parse_gx(self.indi.meade(':GX#')); ra_spd=float(str(self.indi.meade(':XGR#')).replace('#','').strip()); dec_spd=float(str(self.indi.meade(':XGD#')).replace('#','').strip())
            try: safe=float(str(self.indi.meade(':XGST#')).replace('#','').strip())
            except Exception: safe=None
            return gx,ra_spd,dec_spd,safe
        def done(data):
            gx,ra_spd,dec_spd,safe=data
            self._show_safe_tracking_time(safe)
            ra_deg=float(gx.get('ra_steps',0))/ra_spd if ra_spd else 0; dec_deg=float(gx.get('dec_steps',0))/dec_spd if dec_spd else 0; ra_h=ra_deg/15.0
            left=max(.001,self.ra_limit_left.value()); right=max(.001,self.ra_limit_right.value()); frac=max(0,min(1,(ra_h+left)/(left+right))); self.ra_limit_bar.setValue(round(frac*1000)); self.ra_limit_label.setText(f'RA: {ra_h:+.3f} h from Home  | limits -{left:.2f} h / +{right:.2f} h  | physical {self.ra_physical.value():.2f} h')
            down=self.dec_limit_down.value(); up=self.dec_limit_up.value()
            if up>down: dfrac=max(0,min(1,(dec_deg-down)/(up-down))); self.dec_limit_bar.setValue(round(dfrac*1000)); self.dec_limit_label.setText(f'DEC: {dec_deg:+.3f}° from Home | limits {down:+.1f}° .. {up:+.1f}°')
            else: self.dec_limit_bar.setValue(500); self.dec_limit_label.setText(f'DEC: {dec_deg:+.3f}° from Home | DEC limits not configured')
        def fin(): self.monitor_poll_busy = False
        self.run_async(job,done,lambda e:None,fin)

    def _show_safe_tracking_time(self, hours):
        """Hours of tracking left before the firmware stops on the RA limit."""
        if hours is None:
            self.safe_ra_label.setText(_("RA tracking left: unavailable"))
            self.safe_ra_label.setStyleSheet("font-weight:bold")
            return
        self.safe_time_hours = hours
        total = max(0, int(round(hours * 60.0)))
        h, m = divmod(total, 60)
        self.safe_ra_label.setText(_("RA tracking left: {hours:02d}:{minutes:02d}").format(hours=h, minutes=m))
        if total <= 5:
            colour = "#d32f2f"
        elif total <= 15:
            colour = "#d97706"
        else:
            colour = "#2e7d32"
        self.safe_ra_label.setStyleSheet(f"font-weight:bold;color:{colour}")

    def axis_record_start(self):
        pos=self.indi.equatorial_eod()
        if not pos: self.log('EQUATORIAL_EOD_COORD unavailable. Connect the Ekos mount, run Capture & Solve then Sync, and try again.',logging.ERROR); return
        self.axis_name=self.axis_sel.currentText(); self.axis_start=pos; self.axis_commanded_deg=None; self.axis_start_label.setText(f'Start solve: RA {pos[0]:.6f}h, DEC {pos[1]:+.6f}° ({self.axis_name})'); self.axis_result.clear()

    def axis_move_command(self):
        if not self.axis_start: self.log('Record Start Solve first.',logging.WARNING); return
        axis=self.axis_sel.currentText(); deg=float(self.axis_move.value()); self.axis_name=axis; self.axis_commanded_deg=deg
        def job():
            if axis=='RA': spd=self._read_ra_steps_per_degree(); self.indi.meade(f'@MXr{int(round(deg*spd))}#')
            else: spd=self._read_dec_steps_per_degree(); self.indi.meade(f'@MXd{int(round(deg*spd))}#')
            self._wait_for_ra_dec_idle(wait_ra=axis=='RA',wait_dec=axis=='DEC',timeout=180); return spd
        self.run_async(job,lambda spd:self.log(f'{axis} {deg:+.3f}° move complete (steps/deg={spd:.6f}). Run Capture & Solve then Sync in Ekos and record End Solve.'))

    @staticmethod
    def _wrap_hours(h):
        return ((h+12.0)%24.0)-12.0

    def axis_record_end(self):
        if not self.axis_start or not self.axis_commanded_deg: self.log('Record the start and complete the move first.',logging.WARNING); return
        end=self.indi.equatorial_eod()
        if not end: self.log('EQUATORIAL_EOD_COORD unavailable.',logging.ERROR); return
        axis=self.axis_name; start=self.axis_start; cmd=abs(float(self.axis_commanded_deg)); actual=abs(self._wrap_hours(end[0]-start[0])*15.0) if axis=='RA' else abs(end[1]-start[1])
        if actual < 1e-5: self.axis_result.setPlainText('Measured movement is ~0°. Check that Capture & Solve then Sync updated the coordinates.'); return
        def job(): return self._read_ra_steps_per_degree() if axis=='RA' else self._read_dec_steps_per_degree()
        def done(current):
            recommended=current*cmd/actual; err=(actual/cmd-1.0)*100.0
            self.axis_calculated_spd=recommended; self.axis_calculated_axis=axis
            self.axis_apply_btn.setText(f'④ {axis} steps/degree = {recommended:.4f} apply (:XSR#/:XSD#)')
            self.axis_result.setPlainText(f'{axis} Axis Verification\nStart: RA {start[0]:.6f}h DEC {start[1]:+.6f}°\nEnd:   RA {end[0]:.6f}h DEC {end[1]:+.6f}°\nCommanded: {cmd:.6f}°\nPlate-solved movement: {actual:.6f}°\nScale error: {err:+.3f}%\nCurrent steps/degree: {current:.8f}\nRecommended steps/degree: {recommended:.8f}\n\nPressing Apply writes the value with :XSR#/:XSD# and verifies it by reading back. Measure once more afterwards to confirm.')
        self.run_async(job,done)


    # ------------------------ firmware maintenance ------------------------
    def make_config_tab(self):
        w=QtWidgets.QWidget(); v=QtWidgets.QVBoxLayout(w)
        row=QtWidgets.QHBoxLayout(); imp=QtWidgets.QPushButton("Import Configuration_local.hpp"); imp.clicked.connect(self.import_configuration_file); edit=QtWidgets.QPushButton("Edit / save settings"); edit.clicked.connect(self.edit_configuration_file); restore=QtWidgets.QPushButton("Restore backup"); restore.clicked.connect(self.restore_configuration_backup); refresh=QtWidgets.QPushButton("Refresh inspector"); refresh.clicked.connect(self.refresh_config_inspector)
        for b in (imp,edit,restore,refresh): row.addWidget(b)
        row.addStretch(1); v.addLayout(row)
        exp=QtWidgets.QGroupBox("Expected motor profile"); eg=QtWidgets.QGridLayout(exp)
        self.expected_ra_spr=QtWidgets.QSpinBox(); self.expected_dec_spr=QtWidgets.QSpinBox(); self.expected_az_spr=QtWidgets.QSpinBox(); self.expected_alt_spr=QtWidgets.QSpinBox(); self.expected_autopa_ver=QtWidgets.QSpinBox()
        vals=[("RA steps/rev",self.expected_ra_spr,"expected_ra_spr",400),("DEC steps/rev",self.expected_dec_spr,"expected_dec_spr",400),("AZ steps/rev",self.expected_az_spr,"expected_az_spr",200),("ALT steps/rev",self.expected_alt_spr,"expected_alt_spr",200),("AutoPA version",self.expected_autopa_ver,"expected_autopa_version",2)]
        for i,(name,ctl,key,d) in enumerate(vals): ctl.setRange(1,5000); ctl.setValue(int(self.cfg.get(key,d))); eg.addWidget(QtWidgets.QLabel(name),i//3,(i%3)*2); eg.addWidget(ctl,i//3,(i%3)*2+1)
        v.addWidget(exp)
        self.config_text=QtWidgets.QPlainTextEdit(); self.config_text.setReadOnly(True); self.config_text.setMinimumHeight(140); self.config_text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)); v.addWidget(self.config_text,1)
        return w

    def make_diag_tab(self):
        w=QtWidgets.QWidget(); v=QtWidgets.QVBoxLayout(w)
        note=QtWidgets.QLabel("Advanced maintenance commands. Use write/EEPROM commands only when you have confirmed them in the firmware documentation."); note.setWordWrap(True); v.addWidget(note)
        row=QtWidgets.QHBoxLayout(); self.command_edit=QtWidgets.QLineEdit(":GX#"); send=QtWidgets.QPushButton("SEND"); send.clicked.connect(self.send_custom_command); row.addWidget(QtWidgets.QLabel("Meade command")); row.addWidget(self.command_edit,1); row.addWidget(send); v.addLayout(row)
        common=QtWidgets.QGroupBox("Frequently used commands"); cg=QtWidgets.QGridLayout(common)
        cmds=[("GX Status",":GX#"),("Product",":GVP#"),("Firmware",":GVN#"),("RA steps/°",":XGR#"),("DEC steps/°",":XGD#"),("AZ|ALT pos",":XGAA#"),("AutoHome",":XGAH#"),("RA Home offset",":XGHR#"),("Safe tracking",":XGST#"),("Go Home",":hF#"),("Set Home",":SHP#")]
        for i,(name,cmd) in enumerate(cmds):
            b=QtWidgets.QPushButton(name); b.clicked.connect(lambda _=False,c=cmd:self.run_diag_command(c)); cg.addWidget(b,i//4,i%4)
        v.addWidget(common)
        custom=QtWidgets.QGroupBox("Custom buttons"); gg=QtWidgets.QGridLayout(custom); self.custom_name_edits=[]; self.custom_cmd_edits=[]; saved=self.cfg.get("custom_commands",[]) or []
        for i in range(4):
            item=saved[i] if i<len(saved) else {"name":f"Custom {i+1}","command":""}; n=QtWidgets.QLineEdit(item.get("name",f"Custom {i+1}")); c=QtWidgets.QLineEdit(item.get("command","")); b=QtWidgets.QPushButton("Run"); b.clicked.connect(lambda _=False,idx=i:self.run_custom_slot(idx)); self.custom_name_edits.append(n); self.custom_cmd_edits.append(c); gg.addWidget(n,i,0); gg.addWidget(c,i,1); gg.addWidget(b,i,2)
        v.addWidget(custom)
        self.diag_text=QtWidgets.QPlainTextEdit(); self.diag_text.setReadOnly(True); self.diag_text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)); v.addWidget(self.diag_text,1)
        return w

    def make_firmware_tab(self):
        w=QtWidgets.QWidget(); v=QtWidgets.QVBoxLayout(w)
        # Build/Flash drive PlatformIO over the USB serial port, so this page
        # only does anything on the machine the OAT is plugged into.
        self.local_banner = QtWidgets.QLabel()
        self.local_banner.setWordWrap(True)
        self.local_banner.setStyleSheet(
            "border:1px solid #b45309; border-radius:5px; padding:5px 8px; color:#b45309; font-weight:600;")
        v.addWidget(self.local_banner)
        note=QtWidgets.QLabel("Firmware updates and Build/Flash are independent. If a local source exists you can edit only Configuration_local.hpp and rebuild/upload without a GitHub update. PlatformIO is installed into the user area when needed."); note.setWordWrap(True); v.addWidget(note)
        env=QtWidgets.QGroupBox("Build environment"); eg=QtWidgets.QGridLayout(env)
        self.fw_tool_status=QtWidgets.QLabel("PlatformIO: not checked")
        # git is what makes firmware updates incremental; without it every
        # update is a full ZIP download and release tags cannot be pinned.
        self.git_status=QtWidgets.QLabel("git: not checked"); self.git_status.setWordWrap(True)
        self.git_install_btn=QtWidgets.QPushButton("Copy install command")
        self.git_install_btn.setToolTip("Copies 'sudo apt-get install -y git' so you can paste it into a terminal.")
        self.git_install_btn.clicked.connect(self.copy_git_install_command)
        self.git_install_btn.setVisible(False)
        setup=QtWidgets.QPushButton("Setup Build Environment"); setup.clicked.connect(self.firmware_setup_environment)
        detect=QtWidgets.QPushButton("Refresh"); detect.clicked.connect(self.refresh_firmware_environment)
        eg.addWidget(self.fw_tool_status,0,0,1,3); eg.addWidget(setup,0,3); eg.addWidget(detect,0,4)
        eg.addWidget(self.git_status,1,0,1,3); eg.addWidget(self.git_install_btn,1,3,1,2)
        v.addWidget(env)
        f=QtWidgets.QGridLayout(); self.fw_source=QtWidgets.QLineEdit(self.cfg.get("firmware_source_dir","")); browse=QtWidgets.QPushButton("Browse"); browse.clicked.connect(self.select_firmware_source); self.fw_env=QtWidgets.QComboBox(); self.fw_env.setEditable(True); self.fw_env.addItems(["mksgenlv21","mksgenlv2","mksgenlv1","mega2560","ramps","esp32dev"]); self.fw_env.setCurrentText(self.cfg.get("firmware_env","mksgenlv21")); self.fw_port=QtWidgets.QLineEdit(self.cfg.get("firmware_port","/dev/ttyACM0")); self.fw_port.editingFinished.connect(self.update_local_banner); f.addWidget(QtWidgets.QLabel("Firmware source"),0,0); f.addWidget(self.fw_source,0,1,1,3); f.addWidget(browse,0,4); f.addWidget(QtWidgets.QLabel("PlatformIO env"),1,0); f.addWidget(self.fw_env,1,1); f.addWidget(QtWidgets.QLabel("Upload port"),1,2); f.addWidget(self.fw_port,1,3,1,2); v.addLayout(f)
        sg=QtWidgets.QGroupBox("Firmware version (git)"); sgl=QtWidgets.QGridLayout(sg)
        self.fw_version_label=QtWidgets.QLabel("Version not checked"); self.fw_version_label.setStyleSheet("font-weight:600"); self.fw_version_label.setWordWrap(True)
        self.fw_mount_label=QtWidgets.QLabel("Mount firmware: -")
        self.fw_release_label=QtWidgets.QLabel("Latest release: not checked")
        self.fw_ref=QtWidgets.QComboBox(); self.fw_ref.setMinimumWidth(260)
        self.fw_ref.addItem("develop - newest development (recommended)","develop")
        prep=QtWidgets.QPushButton("Prepare official source (first time only)"); prep.clicked.connect(self.firmware_prepare_source)
        chk=QtWidgets.QPushButton("Check for updates"); chk.clicked.connect(self.firmware_check_updates)
        upd=QtWidgets.QPushButton("Update to newest"); upd.clicked.connect(self.firmware_clone_update)
        sw=QtWidgets.QPushButton("Switch to the selected version"); sw.clicked.connect(self.firmware_switch_ref)
        rst=QtWidgets.QPushButton("Restore source"); rst.clicked.connect(self.firmware_reset_source)
        rfr=QtWidgets.QPushButton("Refresh version"); rfr.clicked.connect(self.refresh_version_panel)
        rel=QtWidgets.QPushButton("Check latest release"); rel.clicked.connect(self.check_latest_release)
        sgl.addWidget(self.fw_version_label,0,0,1,5)
        sgl.addWidget(self.fw_mount_label,1,0,1,3); sgl.addWidget(self.fw_release_label,1,3,1,2)
        sgl.addWidget(QtWidgets.QLabel("Version/branch"),2,0); sgl.addWidget(self.fw_ref,2,1,1,2); sgl.addWidget(sw,2,3); sgl.addWidget(rfr,2,4)
        sgl.addWidget(prep,3,0); sgl.addWidget(chk,3,1); sgl.addWidget(upd,3,2); sgl.addWidget(rst,3,3); sgl.addWidget(rel,3,4)
        hint=QtWidgets.QLabel("Checking for updates runs git fetch only and does not change the source. It fetches only the changes instead of re-downloading everything, "
                              "and you can pin a specific release tag (v1.13.9 and so on). Configuration_local.hpp is kept outside the "
                              "repository and is not lost on update.")
        hint.setWordWrap(True); hint.setStyleSheet("color:palette(dark);font-size:11px")
        sgl.addWidget(hint,4,0,1,5)
        v.addWidget(sg)
        bg=QtWidgets.QGroupBox("Local Build / Upload"); bh=QtWidgets.QHBoxLayout(bg); b1=QtWidgets.QPushButton("BUILD current settings"); b1.clicked.connect(self.firmware_build); b2=QtWidgets.QPushButton("FLASH current settings"); b2.clicked.connect(self.firmware_flash); b3=QtWidgets.QPushButton("BUILD → FLASH"); b3.setObjectName("primary"); b3.clicked.connect(self.firmware_build_flash); bh.addWidget(b1); bh.addWidget(b2); bh.addWidget(b3); bh.addStretch(1); v.addWidget(bg)
        prog=QtWidgets.QHBoxLayout(); prog.setSpacing(6)
        self.fw_progress=QtWidgets.QProgressBar(); self.fw_progress.setRange(0,100); self.fw_progress.setValue(0)
        self.fw_progress.setVisible(False); self.fw_progress.setMaximumHeight(16)
        self.fw_progress_label=QtWidgets.QLabel("")
        self.fw_cancel_btn=QtWidgets.QPushButton("Cancel"); self.fw_cancel_btn.setVisible(False)
        self.fw_cancel_btn.setObjectName("danger")
        self.fw_cancel_btn.clicked.connect(self.cancel_firmware_process)
        prog.addWidget(self.fw_progress,1); prog.addWidget(self.fw_progress_label); prog.addWidget(self.fw_cancel_btn)
        v.addLayout(prog)
        fr=QtWidgets.QGroupBox("Factory Reset / EEPROM Clear"); fg=QtWidgets.QGridLayout(fr); self.factory_cmd=QtWidgets.QLineEdit(self.cfg.get("factory_reset_command",":XFR#") or ":XFR#"); self.factory_cmd.setPlaceholderText(":XFR# (clear the whole EEPROM)"); self.factory_confirm=QtWidgets.QLineEdit(); self.factory_confirm.setPlaceholderText("Type RESET"); rb=QtWidgets.QPushButton("FACTORY RESET"); rb.setObjectName("danger"); rb.clicked.connect(self.factory_reset); fg.addWidget(QtWidgets.QLabel("Command"),0,0); fg.addWidget(self.factory_cmd,0,1,1,3); fg.addWidget(QtWidgets.QLabel("Confirm"),1,0); fg.addWidget(self.factory_confirm,1,1); fg.addWidget(rb,1,2,1,2); v.addWidget(fr)
        self.fw_output=QtWidgets.QPlainTextEdit(); self.fw_output.setReadOnly(True); self.fw_output.setMinimumHeight(140); self.fw_output.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)); v.addWidget(self.fw_output,1)
        return w

    def update_local_banner(self):
        """Say plainly that flashing only works on the machine holding the USB cable."""
        if not hasattr(self, "local_banner"):
            return
        text = _("Run this extension on the computer the OAT is plugged into: Build and Flash "
                 "drive PlatformIO over the USB serial port. Configuration editing, firmware "
                 "version management and diagnostics also work over the network.")
        port = self.fw_port.text().strip() if hasattr(self, "fw_port") else ""
        if port and not Path(port).exists():
            text += "  " + _("Upload port {port} does not exist on this machine.").format(port=port)
        self.local_banner.setText(text)

    def _configuration_backup_path(self):
        d=CONFIG_DIR/"backups"; d.mkdir(parents=True,exist_ok=True); return d/f"Configuration_local_{datetime.now():%Y%m%d_%H%M%S}.hpp"

    def _load_configuration_file(self,path,quiet=False):
        path=Path(path); text=path.read_text(encoding="utf-8",errors="replace"); defs={}
        for line in text.splitlines():
            m=re.match(r"\s*#define\s+([A-Za-z0-9_]+)\s+([^/]+?)\s*(?://.*)?$",line)
            if m: defs[m.group(1)]=m.group(2).strip()
        self.imported_defines=defs
        if path!=FIRMWARE_CONFIG: shutil.copy2(path,FIRMWARE_CONFIG)
        if not quiet: self.log(f"Configuration loaded: {path}")
        self.refresh_config_inspector()

    def import_configuration_file(self):
        fn,_=QtWidgets.QFileDialog.getOpenFileName(self,"Select Configuration_local.hpp",str(Path.home()),"Header (*.hpp *.h);;All files (*)")
        if fn:
            try: self._load_configuration_file(fn); self._sync_configuration_to_source()
            except Exception as exc: QtWidgets.QMessageBox.warning(self,_("Configuration"),str(exc))

    def edit_configuration_file(self):
        text=FIRMWARE_CONFIG.read_text(encoding="utf-8",errors="replace") if FIRMWARE_CONFIG.exists() else "// Configuration_local.hpp\n// Importing your existing OAT configuration file and editing only the needed #defines is recommended.\n"
        d=QtWidgets.QDialog(self); d.setWindowTitle(_("Edit Configuration_local.hpp")); d.resize(900,700); lay=QtWidgets.QVBoxLayout(d); ed=QtWidgets.QPlainTextEdit(); ed.setPlainText(text); ed.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)); lay.addWidget(ed,1); row=QtWidgets.QHBoxLayout(); save=QtWidgets.QPushButton("Back up and save"); cancel=QtWidgets.QPushButton("Cancel"); row.addStretch(1); row.addWidget(save); row.addWidget(cancel); lay.addLayout(row); cancel.clicked.connect(d.reject); save.clicked.connect(d.accept)
        if d.exec_()==QtWidgets.QDialog.Accepted:
            if FIRMWARE_CONFIG.exists(): shutil.copy2(FIRMWARE_CONFIG,self._configuration_backup_path())
            FIRMWARE_CONFIG.write_text(ed.toPlainText(),encoding="utf-8"); self._load_configuration_file(FIRMWARE_CONFIG,quiet=True); self._sync_configuration_to_source(); self.log("Configuration saved and synchronized to local firmware source when available.")

    def restore_configuration_backup(self):
        d=CONFIG_DIR/"backups"; fn,_=QtWidgets.QFileDialog.getOpenFileName(self,"Select a Configuration backup",str(d if d.exists() else CONFIG_DIR),"Header (*.hpp *.h);;All files (*)")
        if fn:
            if FIRMWARE_CONFIG.exists(): shutil.copy2(FIRMWARE_CONFIG,self._configuration_backup_path())
            shutil.copy2(fn,FIRMWARE_CONFIG); self._load_configuration_file(FIRMWARE_CONFIG,quiet=True); self._sync_configuration_to_source(); self.log(f"Configuration restored: {fn}")

    def _define_value(self,key,default=None):
        raw=self.imported_defines.get(key)
        if raw is None:return default
        raw=raw.strip().strip("()")
        try:return float(raw) if any(c in raw for c in ".eE") else int(raw,0)
        except Exception:return raw.strip('"')

    def refresh_config_inspector(self):
        lines=["Configuration_local.hpp inspector","-"*60]
        if self.imported_defines:
            keys=["RA_STEPPER_SPR","DEC_STEPPER_SPR","AZ_STEPPER_SPR","ALT_STEPPER_SPR","RA_MICROSTEPPING","DEC_MICROSTEPPING","AZ_MICROSTEPPING","ALT_MICROSTEPPING","RA_MOTOR_CURRENT_RATING","DEC_MOTOR_CURRENT_RATING","AZ_MOTOR_CURRENT_RATING","ALT_MOTOR_CURRENT_RATING","RA_OPERATING_CURRENT_SETTING","DEC_OPERATING_CURRENT_SETTING","AZ_OPERATING_CURRENT_SETTING","ALT_OPERATING_CURRENT_SETTING","AZ_MOTOR_HOLD_SETTING","AZ_ALWAYS_ON","AUTOPA_VERSION","RA_PULLEY_TEETH","DEC_PULLEY_TEETH","USE_RA_END_SWITCH"]
            for k in keys:
                if k in self.imported_defines: lines.append(f"{k:32} {self.imported_defines[k]}")
            checks=[("RA_STEPPER_SPR",self.expected_ra_spr.value()),("DEC_STEPPER_SPR",self.expected_dec_spr.value()),("AZ_STEPPER_SPR",self.expected_az_spr.value()),("ALT_STEPPER_SPR",self.expected_alt_spr.value()),("AUTOPA_VERSION",self.expected_autopa_ver.value())]
            lines.append("\nConsistency check")
            for k,expected in checks:
                got=self._define_value(k,None); lines.append(f"{'✓' if got==expected else '⚠'} {k}: firmware={got!r}, expected={expected}")
        else: lines.append("No Configuration_local.hpp imported.")
        self.config_text.setPlainText("\n".join(lines))
        if self.indi.running:
            def job():
                out=[]
                for k,c in [("Product",":GVP#"),("Firmware",":GVN#"),("RA steps/deg",":XGR#"),("DEC steps/deg",":XGD#"),("AZ|ALT pos",":XGAA#")]:
                    try: out.append((k,self.indi.meade(c)))
                    except Exception as e: out.append((k,f"ERROR {e}"))
                return out
            def done(items): self.config_text.appendPlainText("\nRuntime\n"+"\n".join(f"{k:16} {v}" for k,v in items))
            self.run_async(job,done)

    def _normalize_command(self,cmd):
        cmd=(cmd or "").strip()
        if not cmd: raise ValueError("Command is empty")
        if not cmd.startswith(":") and not cmd.startswith("@"): cmd=":"+cmd
        if not cmd.endswith("#"): cmd+="#"
        return cmd

    def run_diag_command(self,cmd): self.command_edit.setText(cmd); self.send_custom_command()

    def send_custom_command(self):
        try: cmd=self._normalize_command(self.command_edit.text())
        except Exception as exc: QtWidgets.QMessageBox.warning(self,_("Command"),str(exc)); return
        self.save_config(); self.diag_text.appendPlainText(f"{datetime.now():%H:%M:%S} TX {cmd}")
        self.run_async(lambda:self.indi.meade(cmd,timeout=10),lambda r:self.diag_text.appendPlainText(f"{datetime.now():%H:%M:%S} RX {r or '<no payload>'}"),lambda e:self.diag_text.appendPlainText(f"ERROR {e}"))

    def run_custom_slot(self,idx):
        cmd=self.custom_cmd_edits[idx].text().strip()
        if not cmd: return
        self.command_edit.setText(cmd); self.send_custom_command()

    def select_firmware_source(self):
        d=QtWidgets.QFileDialog.getExistingDirectory(self,"Select the OpenAstroTracker-Firmware source",self.fw_source.text() or str(Path.home()))
        if d: self.fw_source.setText(d); self.save_config(); self.refresh_firmware_environments()

    def _fw_log(self,text): self.fw_output.appendPlainText(str(text)); self.log(str(text))

    def _candidate_pio_paths(self):
        out=[]
        for name in ("pio","platformio"):
            p=shutil.which(name)
            if p: out.append(Path(p))
        for p in (Path.home()/".platformio/penv/bin/pio",Path.home()/".platformio/penv/bin/platformio"):
            if p.exists(): out.append(p)
        return out

    def _pio_exe(self,optional=False):
        p=self._candidate_pio_paths()
        if p:return str(p[0])
        if optional:return None
        raise RuntimeError("PlatformIO CLI not found")

    def _install_platformio(self):
        existing=self._pio_exe(optional=True)
        if existing:return existing,f"PlatformIO already installed: {existing}"
        installer=DATA_DIR/"get-platformio.py"; installer.parent.mkdir(parents=True,exist_ok=True); urllib.request.urlretrieve("https://raw.githubusercontent.com/platformio/platformio-core-installer/master/get-platformio.py",installer)
        r=subprocess.run([sys.executable,str(installer)],capture_output=True,text=True,timeout=900); out=r.stdout+r.stderr
        if r.returncode!=0: raise RuntimeError("PlatformIO installer failed\n"+out)
        exe=self._pio_exe(optional=True)
        if not exe: raise RuntimeError("PlatformIO installer completed but pio executable was not found.\n"+out)
        return exe,out

    def firmware_setup_environment(self):
        self.fw_output.clear(); self._fw_log("Checking/installing PlatformIO user environment...")
        def job():
            exe,out=self._install_platformio(); r=subprocess.run([exe,"--version"],capture_output=True,text=True,timeout=30); return exe,out,(r.stdout+r.stderr)
        def done(x): exe,out,ver=x; self._fw_log(out); self._fw_log(ver); self.fw_tool_status.setText(f"PlatformIO: {ver.strip() or exe}"); self.refresh_firmware_environments()
        self.run_async(job,done,lambda e:(self._fw_log(e),self.fw_tool_status.setText(_("PlatformIO: installation failed"))))


    GIT_INSTALL_COMMAND = "sudo apt-get install -y git"

    def refresh_git_status(self):
        """Report whether git is available, since it decides how updates work."""
        if not hasattr(self, "git_status"):
            return
        def job():
            exe = self._git_exe()
            if not exe:
                return None, ""
            try:
                r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
                return exe, (r.stdout + r.stderr).strip()
            except Exception as exc:
                return exe, str(exc)
        def done(result):
            exe, version = result
            if exe:
                self.git_status.setText(_("git: {version}").format(version=version or exe))
                self.git_status.setStyleSheet("")
                self.git_install_btn.setVisible(False)
            else:
                self.git_status.setText(
                    _("git: not installed - updates fall back to a full ZIP download and release tags "
                      "cannot be pinned. Install it with: {command}").format(command=self.GIT_INSTALL_COMMAND))
                self.git_status.setStyleSheet("color:#b45309;font-weight:600")
                self.git_install_btn.setVisible(True)
        self.run_async(job, done, lambda e: self.git_status.setText(_("git: check failed: {error}").format(error=e)))

    def copy_git_install_command(self):
        QtWidgets.QApplication.clipboard().setText(self.GIT_INSTALL_COMMAND)
        self.log(f"Copied to the clipboard: {self.GIT_INSTALL_COMMAND}")

    def refresh_firmware_environment(self):
        self.refresh_git_status()
        exe=self._pio_exe(optional=True)
        if not exe:self.fw_tool_status.setText(_("PlatformIO: not found (installed automatically on Build/Flash)"))
        else:
            try:
                r=subprocess.run([exe,"--version"],capture_output=True,text=True,timeout=10); self.fw_tool_status.setText(f"PlatformIO: {(r.stdout+r.stderr).strip()}")
            except Exception:self.fw_tool_status.setText(f"PlatformIO: {exe}")
        self.refresh_firmware_environments()

    def refresh_firmware_environments(self):
        src=Path(self.fw_source.text().strip()) if hasattr(self,"fw_source") and self.fw_source.text().strip() else None
        if not src or not (src/"platformio.ini").exists(): return
        try:
            txt=(src/"platformio.ini").read_text(encoding="utf-8",errors="replace"); envs=re.findall(r"^\s*\[env:([^\]]+)\]",txt,re.M); cur=self.fw_env.currentText(); self.fw_env.clear(); self.fw_env.addItems(envs); self.fw_env.setCurrentText(cur if cur in envs else (envs[0] if envs else cur))
        except Exception as e:self.log(f"PlatformIO env parse failed: {e}",logging.DEBUG)

    def _default_firmware_target(self): return DATA_DIR/"firmware"/"OpenAstroTracker-Firmware"

    def _firmware_source_valid(self,src): src=Path(src); return src.exists() and (src/"platformio.ini").exists()

    FIRMWARE_REPO = "https://github.com/OpenAstroTech/OpenAstroTracker-Firmware.git"

    @staticmethod
    def _git_exe(): return shutil.which("git")

    def _git(self, src, *args, timeout=300, check=False):
        git=self._git_exe()
        if not git: raise RuntimeError("git is not installed")
        r=subprocess.run([git,"-C",str(src)]+list(args),capture_output=True,text=True,timeout=timeout)
        if check and r.returncode!=0:
            raise RuntimeError(f"git {' '.join(args)} failed:\n{(r.stdout+r.stderr).strip()}")
        return r

    def _is_git_repo(self,src):
        src=Path(src)
        return bool(self._git_exe()) and (src/".git").exists()

    @staticmethod
    def _read_version_h(src):
        """Read the firmware's declared version, e.g. V1.13.9, from Version.h."""
        try:
            txt=(Path(src)/"Version.h").read_text(encoding="utf-8",errors="replace")
            m=re.search(r'#define\s+VERSION\s+"([^"]+)"',txt)
            return m.group(1) if m else ""
        except Exception:
            return ""

    def firmware_version_info(self,src=None):
        """Collect everything needed for the version panel."""
        src=Path(src or self.fw_source.text().strip() or self._default_firmware_target())
        info={"path":str(src),"exists":self._firmware_source_valid(src),"git":False,
              "ref":"","sha":"","date":"","dirty":False,"behind":0,"ahead":0,
              "upstream":"","version_h":self._read_version_h(src) if src.exists() else "","tags":[]}
        if not info["exists"] or not self._is_git_repo(src):
            return info
        info["git"]=True
        r=self._git(src,"describe","--tags","--exact-match")
        if r.returncode==0:
            info["ref"]=r.stdout.strip()+" (tag)"
        else:
            b=self._git(src,"rev-parse","--abbrev-ref","HEAD").stdout.strip()
            if b and b!="HEAD":
                info["ref"]=b+" (branch)"
            else:
                d=self._git(src,"describe","--tags","--always").stdout.strip()
                info["ref"]=f"detached @ {d}"
        info["sha"]=self._git(src,"rev-parse","--short","HEAD").stdout.strip()
        info["date"]=self._git(src,"log","-1","--format=%cd","--date=short").stdout.strip()
        info["dirty"]=bool(self._git(src,"status","--porcelain","--untracked-files=no").stdout.strip())
        up=self._git(src,"rev-parse","--abbrev-ref","--symbolic-full-name","@{u}")
        if up.returncode==0:
            info["upstream"]=up.stdout.strip()
            counts=self._git(src,"rev-list","--left-right","--count",f"{info['upstream']}...HEAD").stdout.split()
            if len(counts)==2:
                info["behind"],info["ahead"]=int(counts[0]),int(counts[1])
        else:
            # Detached (a tag): compare against the tracking branch we last used.
            base=(self.cfg.get("firmware_ref") or "develop")
            base=base if base.startswith("origin/") else f"origin/{base}"
            counts=self._git(src,"rev-list","--left-right","--count",f"{base}...HEAD").stdout.split()
            if len(counts)==2:
                info["upstream"]=base
                info["behind"],info["ahead"]=int(counts[0]),int(counts[1])
        tags=self._git(src,"tag","--sort=-v:refname").stdout.split()
        info["tags"]=tags[:40]
        return info

    def _clone_firmware(self,target):
        """Fresh git clone; blobless first so a Pi over a slow link is quick."""
        git=self._git_exe()
        if not git: raise RuntimeError("git is not installed")
        target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists(): shutil.rmtree(target)
        for extra in (["--filter=blob:none"],[]):
            r=subprocess.run([git,"clone"]+extra+[self.FIRMWARE_REPO,str(target)],
                             capture_output=True,text=True,timeout=900)
            if r.returncode==0:
                return f"git clone complete: {target}\n"+(r.stdout+r.stderr).strip()
            if target.exists(): shutil.rmtree(target,ignore_errors=True)
        raise RuntimeError("git clone failed:\n"+(r.stdout+r.stderr).strip())

    def _download_official_firmware_zip(self,target,ref="develop"):
        """ZIP fallback for machines without git (no incremental updates)."""
        target=Path(target); target.parent.mkdir(parents=True,exist_ok=True); last=None
        refs=[ref] if ref else []
        for cand in refs+["develop","master","main"]:
            for kind in ("heads","tags"):
                try:
                    url=f"https://github.com/OpenAstroTech/OpenAstroTracker-Firmware/archive/refs/{kind}/{cand}.zip"
                    with tempfile.TemporaryDirectory(prefix="oat-fw-") as td:
                        z=Path(td)/"fw.zip"; urllib.request.urlretrieve(url,z)
                        with zipfile.ZipFile(z) as zf: zf.extractall(td)
                        roots=[p for p in Path(td).iterdir() if p.is_dir() and p.name.startswith("OpenAstroTracker-Firmware-")]
                        if not roots: raise RuntimeError("Downloaded archive has no firmware root")
                        if target.exists(): shutil.rmtree(target)
                        shutil.copytree(roots[0],target)
                    return (f"via ZIP fallback, {cand} source was downloaded (git not installed). "
                            "Once git is installed, only the changes are fetched.")
                except Exception as e: last=e
        raise RuntimeError(f"Official firmware download failed: {last}")

    def _resolve_or_fetch_firmware_source(self,typed=""):
        typed=(typed or "").strip()
        if typed and self._firmware_source_valid(typed): return Path(typed),"Existing local firmware source kept unchanged."
        target=self._default_firmware_target()
        if self._firmware_source_valid(target): return target,"Existing cached official source kept unchanged."
        if self._git_exe():
            return target,self._clone_firmware(target)
        return target,self._download_official_firmware_zip(target,self.cfg.get("firmware_ref","develop"))

    def firmware_prepare_source(self):
        self.fw_output.clear(); typed=self.fw_source.text().strip()
        self._fw_log("Preparing firmware source. An existing valid local source is kept as is.")
        def done(x):
            src,msg=x; self.fw_source.setText(str(src)); self.save_config(); self._fw_log(msg)
            self._sync_configuration_to_source(); self.refresh_firmware_environments(); self.refresh_version_panel()
        self.run_async(lambda:self._resolve_or_fetch_firmware_source(typed),done,lambda e:self._fw_log(e))

    def check_latest_release(self):
        """Compare the local source with the newest GitHub release tag.

        Best-effort: the call never blocks build/flash.
        """
        def job():
            url="https://api.github.com/repos/OpenAstroTech/OpenAstroTracker-Firmware/releases/latest"
            req=urllib.request.Request(url,headers={"Accept":"application/vnd.github+json","User-Agent":f"{APP_NAME}/{VERSION}"})
            with urllib.request.urlopen(req,timeout=8) as resp:
                data=json.loads(resp.read().decode("utf-8","replace"))
            return data.get("tag_name",""), data.get("html_url","")
        def done(info):
            tag,url=info
            if not tag:
                self._fw_log("Could not read the latest release information."); return
            self.cfg["latest_release_tag"]=tag; self.save_config()
            current=(self._last_version_info or {}).get("version_h","") if hasattr(self,"_last_version_info") else ""
            same=current.lstrip("vV").lower()==tag.lstrip("vV").lower() if current else None
            if same:
                self._fw_log(f"Latest release {tag} - same as the current source.")
            else:
                self._fw_log(f"Latest release: {tag} (current source {current or '?'}). "
                             f"In 'Version/branch', {tag} to switch to it.  {url}")
            if hasattr(self,"fw_release_label"):
                self.fw_release_label.setText(f"Latest release: {tag}" + ("  (same as current)" if same else ""))
        self.run_async(job,done,lambda e:self._fw_log(f"Release check failed: {e}"))

    def refresh_version_panel(self):
        """Show current source version, mount firmware version and update state."""
        if not hasattr(self,"fw_version_label"): return
        def job(): return self.firmware_version_info()
        def done(info):
            self._last_version_info=info
            if not info["exists"]:
                self.fw_version_label.setText(_("No source - run 'Prepare official source' first.")); return
            if not info["git"]:
                self.fw_version_label.setText(
                    f"Source {info['version_h'] or '?'} (ZIP copy, not managed by git) - pressing 'Check for updates' switches it to a git repository.")
            else:
                dirty=" · locally modified" if info["dirty"] else ""
                behind=f" · behind by {info['behind']} commits behind" if info["behind"] else (" · up to date" if info["upstream"] else "")
                ahead=f" · local commits {info['ahead']}files" if info["ahead"] else ""
                self.fw_version_label.setText(
                    f"Source {info['version_h'] or '?'} · {info['ref']} @ {info['sha']} ({info['date']}){behind}{ahead}{dirty}")
                self._populate_ref_combo(info)
            if self.indi.running:
                def vjob(): return self.indi.meade(":GVN#")
                def vdone(v):
                    v=str(v).strip().rstrip("#")
                    src_v=(info.get("version_h") or "").lstrip("vV")
                    same=v.lstrip("vV").lower()==src_v.lower() if v and src_v else None
                    mark="=" if same else ("≠" if same is False else "?")
                    self.fw_mount_label.setText(f"Mount firmware: {v or '-'}   {mark}   Source: {info.get('version_h') or '-'}")
                self.run_async(vjob,vdone,lambda e:self.fw_mount_label.setText(f"Mount firmware: unavailable ({e})"))
            else:
                self.fw_mount_label.setText(_("Mount firmware: INDI not connected"))
        self.run_async(job,done,lambda e:self.fw_version_label.setText(f"Version information error: {e}"))

    def _populate_ref_combo(self,info):
        current=self.cfg.get("firmware_ref","develop")
        self.fw_ref.blockSignals(True)
        self.fw_ref.clear()
        self.fw_ref.addItem("develop - newest development (recommended)","develop")
        self.fw_ref.addItem("master - stable branch","master")
        for t in info.get("tags",[]):
            self.fw_ref.addItem(f"{t} - release tag",t)
        idx=self.fw_ref.findData(current)
        if idx<0:
            self.fw_ref.addItem(f"{current} - saved setting",current); idx=self.fw_ref.count()-1
        self.fw_ref.setCurrentIndex(idx)
        self.fw_ref.blockSignals(False)

    def firmware_check_updates(self):
        """git fetch only: never changes the working tree."""
        self.fw_output.clear(); typed=self.fw_source.text().strip()
        self._fw_log("Checking for updates (git fetch - the working tree is not modified)...")
        def job():
            src,msg=self._resolve_or_fetch_firmware_source(typed)
            out=[msg]
            if not self._is_git_repo(src):
                if not self._git_exe():
                    out.append("git is not installed, so incremental updates are unavailable. Run 'sudo apt-get install git' and try again.")
                    return str(src),"\n".join(out)
                out.append("The existing source is a ZIP copy, so a git repository is fetched now (once only).")
                out.append(self._clone_firmware(self._default_firmware_target()))
                src=self._default_firmware_target()
            r=self._git(src,"fetch","--tags","--prune","origin",timeout=600)
            out.append((r.stdout+r.stderr).strip() or "fetch complete (no changes)")
            return str(src),"\n".join(out)
        def done(x):
            src,out=x; self.fw_source.setText(src)
            self.cfg["firmware_last_fetch"]=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_config(); self._fw_log(out); self.refresh_version_panel(); self.refresh_firmware_environments()
        self.run_async(job,done,lambda e:self._fw_log(e))

    def firmware_switch_ref(self):
        """Check out the selected branch/tag and fast-forward when it is a branch."""
        ref=self.fw_ref.currentData() or self.fw_ref.currentText().split(" ")[0]
        typed=self.fw_source.text().strip()
        self.fw_output.clear(); self._fw_log(f"'{ref}'...")
        def job():
            src,msg=self._resolve_or_fetch_firmware_source(typed)
            if not self._is_git_repo(src):
                if not self._git_exe():
                    return str(src),msg+"\n"+self._download_official_firmware_zip(src,ref)
                msg+="\n"+self._clone_firmware(self._default_firmware_target()); src=self._default_firmware_target()
            out=[msg]
            dirty=self._git(src,"status","--porcelain","--untracked-files=no").stdout.strip()
            if dirty:
                out.append("Warning: the source has local modifications. Restore them with 'Restore source' or back them up before switching.")
                out.append(dirty)
                raise RuntimeError("\n".join(out))
            self._git(src,"fetch","--tags","--prune","origin",timeout=600)
            is_branch=self._git(src,"rev-parse","--verify",f"origin/{ref}").returncode==0
            if is_branch:
                r=self._git(src,"checkout","-B",ref,f"origin/{ref}",check=True)
                out.append((r.stdout+r.stderr).strip())
                r=self._git(src,"pull","--ff-only",check=True)
                out.append((r.stdout+r.stderr).strip())
            else:
                r=self._git(src,"checkout","--detach",ref,check=True)
                out.append((r.stdout+r.stderr).strip())
                out.append(f"Release tag {ref} and pinned it (detached HEAD).")
            return str(src),"\n".join(x for x in out if x)
        def done(x):
            src,out=x; self.fw_source.setText(src); self.cfg["firmware_ref"]=ref; self.save_config()
            self._fw_log(out); self._fw_log(f"✓ {ref} applied. Now run BUILD -> FLASH.")
            self._sync_configuration_to_source(); self.refresh_firmware_environments(); self.refresh_version_panel()
        self.run_async(job,done,lambda e:self._fw_log(str(e)))

    def firmware_clone_update(self):
        """Update to the newest commit of the currently selected branch."""
        ref=(self.fw_ref.currentData() if hasattr(self,"fw_ref") else None) or self.cfg.get("firmware_ref","develop")
        self.fw_output.clear(); self._fw_log(f"'{ref}' to the newest commit (incremental git pull)...")
        typed=self.fw_source.text().strip()
        def job():
            src,msg=self._resolve_or_fetch_firmware_source(typed)
            if not self._is_git_repo(src):
                if not self._git_exe():
                    return str(src),msg+"\n"+self._download_official_firmware_zip(src,ref)
                msg+="\n"+self._clone_firmware(self._default_firmware_target()); src=self._default_firmware_target()
            out=[msg]
            r=self._git(src,"fetch","--tags","--prune","origin",timeout=600); out.append((r.stdout+r.stderr).strip())
            if self._git(src,"rev-parse","--verify",f"origin/{ref}").returncode==0:
                r=self._git(src,"checkout","-B",ref,f"origin/{ref}",check=True); out.append((r.stdout+r.stderr).strip())
                r=self._git(src,"pull","--ff-only",check=True); out.append((r.stdout+r.stderr).strip())
            else:
                r=self._git(src,"checkout","--detach",ref,check=True); out.append((r.stdout+r.stderr).strip())
            return str(src),"\n".join(x for x in out if x)
        def done(x):
            src,out=x; self.fw_source.setText(src); self.save_config(); self._fw_log(out)
            self._sync_configuration_to_source(); self.refresh_firmware_environments(); self.refresh_version_panel()
            self._fw_log("Firmware source updated. Your settings live in Configuration_local.hpp outside the repository and are not overwritten.")
        self.run_async(job,done,lambda e:self._fw_log(str(e)))

    def firmware_reset_source(self):
        """Discard local modifications so the next build matches the chosen version."""
        src=self.fw_source.text().strip()
        if not self._is_git_repo(src):
            self._fw_log("Not a git repository, so it cannot be restored. Use 'Check for updates' to fetch a git source."); return
        if QtWidgets.QMessageBox.warning(
                self,_("Restore source"),
                "This discards all local changes in the firmware source and returns it to the current version.\n"
                "(Configuration_local.hpp is stored separately in the OAT Firmware settings folder and is preserved.)\nContinue?",
                QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:
            return
        def job():
            out=[]
            r=self._git(src,"reset","--hard",check=True); out.append((r.stdout+r.stderr).strip())
            r=self._git(src,"clean","-fd"); out.append((r.stdout+r.stderr).strip())
            return "\n".join(x for x in out if x)
        def done(out):
            self._fw_log(out); self._fw_log("✓ Source restored."); self._sync_configuration_to_source(); self.refresh_version_panel()
        self.run_async(job,done,lambda e:self._fw_log(str(e)))

    def _sync_configuration_to_source(self):
        src=Path(self.fw_source.text().strip()) if hasattr(self,"fw_source") and self.fw_source.text().strip() else None
        if src and self._firmware_source_valid(src) and FIRMWARE_CONFIG.exists(): shutil.copy2(FIRMWARE_CONFIG,src/"Configuration_local.hpp")

    def _prepare_firmware_tree(self,source_text=""):
        src,msg=self._resolve_or_fetch_firmware_source(source_text)
        if FIRMWARE_CONFIG.exists(): shutil.copy2(FIRMWARE_CONFIG,src/"Configuration_local.hpp")
        return src,msg


    # PlatformIO takes minutes; without live output there is no way to tell a
    # slow compile from a stalled upload.
    PROGRESS_RE = re.compile(r"(\d{1,3})\s*%")
    PHASE_HINTS = (("Compiling", "Compiling"), ("Archiving", "Archiving"), ("Linking", "Linking"),
                   ("Building .hex", "Building image"), ("Checking size", "Checking size"),
                   ("Uploading", "Uploading"), ("Writing", "Writing"), ("Reading", "Verifying"),
                   ("avrdude", "avrdude"), ("esptool", "esptool"))

    def _run_streamed(self, cmd, cwd=None, timeout=1800):
        """Run a command, forwarding every line to the GUI as it appears."""
        self.process_signals.started.emit(" ".join(str(c) for c in cmd))
        collected = []
        process = subprocess.Popen([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1, universal_newlines=True)
        self._fw_process = process
        deadline = time.time() + timeout
        try:
            for raw in process.stdout:
                line = raw.rstrip("\n")
                collected.append(line)
                self.process_signals.line.emit(line)
                if time.time() > deadline:
                    process.kill()
                    raise TimeoutError(f"Command exceeded {timeout}s: {' '.join(str(c) for c in cmd)}")
            code = process.wait(timeout=30)
        finally:
            self._fw_process = None
            self.process_signals.finished.emit(process.returncode if process.returncode is not None else -1)
        return code, "\n".join(collected)

    def cancel_firmware_process(self):
        """Stop a running build/upload."""
        process = getattr(self, "_fw_process", None)
        if process is None:
            self.log("No firmware process is running.", logging.WARNING); return
        process.terminate()
        self._fw_log("Cancel requested; terminating the PlatformIO process...")

    def _on_process_started(self, command):
        self.fw_progress.setRange(0, 0)          # busy until a percentage shows up
        self.fw_progress.setFormat("%p%")
        self.fw_progress.setVisible(True)
        self.fw_cancel_btn.setVisible(True)
        self._fw_started_at = time.monotonic()
        self._fw_phase = _("Starting")
        self.fw_progress_label.setText(self._fw_phase)
        self.fw_elapsed_timer.start()
        self.logger.debug("Running: %s", command)

    def _on_process_line(self, line):
        self.fw_output.appendPlainText(line)
        for needle, phase in self.PHASE_HINTS:
            if needle in line:
                self._fw_phase = phase
                break
        match = self.PROGRESS_RE.search(line)
        if match:
            percent = max(0, min(100, int(match.group(1))))
            self.fw_progress.setRange(0, 100)
            self.fw_progress.setValue(percent)
        self._update_fw_elapsed()

    def _on_process_finished(self, code):
        self.fw_elapsed_timer.stop()
        self.fw_progress.setRange(0, 100)
        self.fw_progress.setValue(100 if code == 0 else self.fw_progress.value())
        self.fw_cancel_btn.setVisible(False)
        elapsed = int(time.monotonic() - getattr(self, "_fw_started_at", time.monotonic()))
        self.fw_progress_label.setText(
            _("Finished in {seconds}s (exit code {code})").format(seconds=elapsed, code=code))

    def _update_fw_elapsed(self):
        elapsed = int(time.monotonic() - getattr(self, "_fw_started_at", time.monotonic()))
        minutes, seconds = divmod(elapsed, 60)
        self.fw_progress_label.setText(
            _("{phase} - running {minutes:d}:{seconds:02d}").format(
                phase=getattr(self, "_fw_phase", _("Working")), minutes=minutes, seconds=seconds))

    def _build_job(self,source_text,env):
        exe,pio=self._install_platformio()
        src,msg=self._prepare_firmware_tree(source_text)
        code,out=self._run_streamed([exe,"run","-e",env],cwd=src,timeout=1800)
        return code,msg+"\n"+pio+"\n"+out,str(src)

    def firmware_build(self):
        self.save_config(); self.fw_output.clear(); source=self.fw_source.text().strip(); env=self.fw_env.currentText().strip(); self._fw_log("BUILD: current local source + saved Configuration_local.hpp. No GitHub update.")
        def done(x): rc,out,src=x; self.fw_source.setText(src); self.save_config(); self._fw_log(out); self._fw_log("BUILD OK" if rc==0 else "BUILD FAILED"); self.refresh_firmware_environments()
        self.run_async(lambda:self._build_job(source,env),done,lambda e:self._fw_log(e))

    def _flash_precheck(self):
        """Make sure nothing else holds the serial port, offering to free it."""
        if not self.indi.running:
            QtWidgets.QMessageBox.warning(self, _("Flash blocked"), _(
                "For safety, connect to the INDI server first and confirm that the mount is Disconnected."))
            return False
        if self.indi.mount_is_connected() is not False:
            answer = QtWidgets.QMessageBox.question(self, _("Firmware Flash"), _(
                "The mount is connected, so the INDI driver holds the serial port and the upload would fail.\n\n"
                "Disconnect the mount now and continue?\n"
                "Ekos will show it as disconnected; it is reconnected automatically after a successful upload."),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.Yes)
            if answer != QtWidgets.QMessageBox.Yes:
                return False
            if not self._disconnect_mount_for_flash():
                return False
        return QtWidgets.QMessageBox.question(self, _("Firmware Flash"), _(
            "This flashes the current local firmware source with the saved Configuration_local.hpp. "
            "If you have an auto-reset suppression capacitor/jumper, have you disabled it?")) == QtWidgets.QMessageBox.Yes

    def _disconnect_mount_for_flash(self):
        """Ask the driver to disconnect and wait until it has let the port go."""
        self._fw_log("Disconnecting the mount so the serial port is free...")
        self.fw_progress_label.setText(_("Disconnecting the mount..."))
        QtWidgets.QApplication.processEvents()
        self.reconnect_after_flash = True
        try:
            self.indi.set_device_connected(False)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, _("Flash blocked"),
                                          _("Could not disconnect the mount: {error}").format(error=exc))
            self.reconnect_after_flash = False
            return False
        deadline = time.time() + 12.0
        while time.time() < deadline:
            QtWidgets.QApplication.processEvents()
            if self.indi.mount_is_connected() is False:
                time.sleep(1.0)   # give the driver a moment to close the port
                self._fw_log("Mount disconnected; the serial port is free.")
                self.fw_progress_label.setText(_("Mount disconnected"))
                return True
            time.sleep(0.2)
        QtWidgets.QMessageBox.warning(self, _("Flash blocked"), _(
            "The mount did not report Disconnected within 12 s. Disconnect it in Ekos and try again."))
        self.reconnect_after_flash = False
        return False

    def _reconnect_mount_after_flash(self, success):
        """Bring the mount back once the board has rebooted."""
        if not getattr(self, "reconnect_after_flash", False):
            return
        self.reconnect_after_flash = False
        if not success:
            self._fw_log("Upload failed, so the mount was left disconnected. Reconnect it in Ekos when ready.")
            return
        def reconnect():
            self._fw_log("Reconnecting the mount...")
            try:
                self.indi.set_device_connected(True)
            except Exception as exc:
                self._fw_log(f"Could not reconnect the mount: {exc}. Reconnect it in Ekos.")
        QtCore.QTimer.singleShot(6000, reconnect)

    def _flash_job(self,source_text,env,port):
        exe,pio=self._install_platformio()
        src,msg=self._prepare_firmware_tree(source_text)
        code,out=self._run_streamed([exe,"run","-e",env,"-t","upload","--upload-port",port],cwd=src,timeout=1800)
        return code,msg+"\n"+pio+"\n"+out,str(src)

    def firmware_flash(self):
        if not self._flash_precheck(): return
        self.save_config(); self.fw_output.clear(); source=self.fw_source.text().strip(); env=self.fw_env.currentText().strip(); port=self.fw_port.text().strip(); self._fw_log("FLASH: no GitHub update.")
        def done(x):
            rc,out,src=x; self.fw_source.setText(src); self.save_config(); self._fw_log(out)
            self._fw_log("FLASH OK - the mount is reconnected automatically." if rc==0 else "FLASH FAILED")
            self._reconnect_mount_after_flash(rc==0)
        self.run_async(lambda:self._flash_job(source,env,port),done,
                       lambda e:(self._fw_log(e), self._reconnect_mount_after_flash(False)))

    def firmware_build_flash(self):
        if not self._flash_precheck():return
        self.save_config(); self.fw_output.clear(); source=self.fw_source.text().strip(); env=self.fw_env.currentText().strip(); port=self.fw_port.text().strip(); self._fw_log("BUILD → FLASH: GitHub update is NOT performed.")
        def job():
            rc,out,src=self._build_job(source,env)
            if rc!=0:return rc,"BUILD FAILED\n"+out,src
            frc,fout,src2=self._flash_job(src,env,port); return frc,"BUILD OK\n"+out+"\n\nUPLOAD\n"+fout,src2
        def done(x): rc,out,src=x; self.fw_source.setText(src); self.save_config(); self._fw_log(out); self._fw_log("BUILD + FLASH OK" if rc==0 else "BUILD/FLASH FAILED"); self._reconnect_mount_after_flash(rc==0)
        self.run_async(job,done,lambda e:self._fw_log(e))

    def _snapshot_before_reset(self):
        fp=LOG_DIR/f"factory_reset_snapshot_{datetime.now():%Y%m%d_%H%M%S}.txt"; lines=[f"{APP_NAME} {VERSION} pre-factory-reset snapshot"]
        for label,cmd in [("Product",":GVP#"),("Firmware",":GVN#"),("GX",":GX#"),("RA offset",":XGHR#"),("RA spd",":XGR#"),("DEC spd",":XGD#"),("AZALT",":XGAA#")]:
            try: lines.append(f"{label}: {self.indi.meade(cmd)}")
            except Exception as e: lines.append(f"{label}: ERROR {e}")
        if FIRMWARE_CONFIG.exists():
            backup=self._configuration_backup_path(); shutil.copy2(FIRMWARE_CONFIG,backup); lines.append(f"Configuration backup: {backup}")
        fp.write_text("\n".join(lines),encoding="utf-8"); return fp

    def factory_reset(self):
        try:cmd=self._normalize_command(self.factory_cmd.text())
        except Exception as e:QtWidgets.QMessageBox.warning(self,_("Factory Reset"),str(e));return
        if self.factory_confirm.text().strip()!="RESET":QtWidgets.QMessageBox.warning(self,_("Factory Reset"),"Type RESET exactly in the Confirm field.");return
        if QtWidgets.QMessageBox.warning(self,_("FACTORY RESET"),"EEPROM calibration, home offsets and runtime settings may be erased. Continue?",QtWidgets.QMessageBox.Yes|QtWidgets.QMessageBox.No,QtWidgets.QMessageBox.No)!=QtWidgets.QMessageBox.Yes:return
        def job(): return self._snapshot_before_reset(),self.indi.meade(cmd,timeout=10)
        def done(x):self._fw_log(f"Pre-reset snapshot: {x[0]}\nFactory reset command sent. Response: {x[1] or '<no payload>'}\nPower-cycle/reconnect and verify Home/calibration.");self.factory_confirm.clear()
        self.run_async(job,done,lambda e:self._fw_log(e))
    # ------------------------ diagnostics/safety ------------------------
    def emergency_stop(self):
        self.stop_autopa_watch(); self.home_timer.stop(); self.home_busy=False; self.home_sequence=[]
        self.meade_async("@Q#", lambda _r: self.log("Emergency stop sent (:Q#). Tracking is also stopped.", logging.WARNING))

    def refresh_diagnostics(self):
        def parse_ver(text):
            m=re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
            return tuple(map(int,m.groups())) if m else (0,0,0)
        def job():
            out=[]
            try:
                product=self.indi.meade(":GVP#"); out.append(("Product",product))
                fw=self.indi.meade(":GVN#"); out.append(("Firmware",fw))
            except Exception as exc:
                return [("Connection",f"ERROR: {exc}")]
            # Firmware <= 1.13.20 can truncate :XGM# at the 64-byte MeadeResponse
            # buffer and omit '#', poisoning subsequent command/reply synchronization.
            # The upstream fix was merged Sep 1 2026 for the 1.13.21 development line.
            if parse_ver(fw) >= (1,13,21):
                try: out.append(("Hardware",self.indi.meade(":XGM#")))
                except Exception as exc: out.append(("Hardware",f"ERROR: {exc}"))
            else:
                out.append(("Hardware","SKIPPED: firmware <=1.13.20 has known :XGM# 64-byte truncation bug"))
            cmds=[("Mount status",":GX#"),("AutoHome",":XGAH#"),("RA offset",":XGHR#"),("DEC offset",":XGHD#"),
                  ("RA steps/deg",":XGR#"),("DEC steps/deg",":XGD#"),("AZ|ALT pos",":XGAA#"),
                  ("DEC limits",":XGDL#"),("Tracking speed",":XGT#"),("Tracking trim",":XGS#"),
                  ("LST",":XGL#"),("HA",":XGH#"),("Hemisphere",":XGHS#"),
                  ("Local date",":GC#"),("Local time",":GL#"),("UTC offset",":GG#"),
                  ("Longitude",":Gg#"),("Latitude",":Gt#"),("Network",":XGN#")]
            for label,cmd in cmds:
                try: out.append((label,self.indi.meade(cmd)))
                except Exception as exc: out.append((label,f"ERROR: {exc}"))
            return out
        def done(items):
            lines=[f"{k:14}: {v}" for k,v in items]
            for k,v in items:
                if k=="DEC offset":
                    try:
                        if int(float(str(v).strip().rstrip("#")))!=0:
                            lines.append("WARNING       : The DEC homing offset is not zero. Firmware Park (:hP#) moves DEC by -offset after reaching Home "
                                         "further (left over from an older OAT Tools). Running SET HOME clears it to zero.")
                    except Exception:
                        pass
            self.diag_text.setPlainText("\n".join(lines))
        self.run_async(job,done)

    def closeEvent(self, event):
        try: self.save_config(); self.autopa_timer.stop(); self.home_timer.stop(); self.indi.disconnect_server()
        except Exception: pass
        event.accept()


def main():
    app=QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win=OATHelper(); win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
