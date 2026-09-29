import os, socket, sys, threading, re, time

DEV = "LX200 OpenAstroTech"
# Optional trace of every Meade transaction, including bytes left on the wire.
WIRE_LOG = os.environ.get("WIRE_LOG")


class Mount:
    def __init__(s):
        s.ra = 0; s.dec = 0; s.trk = 0; s.xshd = 0; s.xshr = -560
        s.alt_moves = []; s.az_moves = []
        s.dec_low = 30.0; s.dec_up = 90.0; s.trim = 1.0   # travel down/up in degrees
        s.ra_spd = 1000.0; s.dec_spd = 314.2; s.rate = "M"
        s.lst = "065722"   # deliberately stale, like the mount in the field
        s.date = "09/18/26"; s.localtime = "00:35:47"; s.offset = "-09"
        s.lat = "+37*34"; s.lon = "-127*00"; s.lon_deg = -127.0; s.extra_reply = None; s.lst_manual = None
        s.tracking = True
        s.home_ra = None; s.home_polls = 0     # RA Hall AutoHome (:MHR / :XGAH#)
        # lx200_OpenAstroTech on ARM: getCommandChar() returns char(-1) on a
        # failed read, plain char is unsigned there, so the driver publishes
        # the byte 0xFF as the reply of an '&' command.
        # FAKE_CHAR_FAIL=1 starts in that state: every '&' read fails, as on
        # the Raspberry Pi build that made AutoHome fail in 0.6.4.
        s.char_fail = os.environ.get("FAKE_CHAR_FAIL") == "1"
        s.lock = threading.Lock()

    def cmd(s, c):
        c = c[1:-1]
        if c == "GX":
            state = "Homing" if s.home_ra == "IN PROGRESS" else ("Tracking" if s.tracking else "Idle")
            motion = "--T--" if s.tracking else "-----"
            return f"{state},{motion},{s.ra},{s.dec},{s.trk},071906,+900000,#"
        if c == "SHP":
            s.ra = s.dec = s.trk = 0; return "1"
        if c == "XGHD": return f"{s.xshd}#"
        if c == "XGHR": return f"{s.xshr}#"
        if c == "XGD": return f"{s.dec_spd}#"
        if c == "XGR": return f"{s.ra_spd}#"
        if c == "XGST": return "5.5#"
        if c == "XGAA": return "120|340#"
        if c == "GVN": return "V1.13.9#"
        if c == "GC": return f"{s.date}#"
        if c == "GG": return f"{s.offset}#"
        if c == "Gg": return f"{s.lon}#"
        if c == "Gt": return f"{s.lat}#"
        if c.startswith("SC"):
            s.date = c[2:]
            s.extra_reply = "                              #"   # firmware sends a second string
            return "1Updating Planetary Data#"
        if c.startswith("SL"): s.localtime = c[2:]; return "1"
        if c.startswith("SG"): s.offset = c[2:]; return "1"
        if c.startswith("St"): s.lat = c[2:]; return "1"
        if c.startswith("Sg"):
            s.lon = c[2:]
            body = c[2:].replace("*", " ").split()
            deg = float(body[0]) + (float(body[1]) / 60.0 if len(body) > 1 else 0.0)
            # This mount takes a signed value literally (east must be positive),
            # and maps an unsigned one with the 0-360-going-west rule.
            s.lon_deg = deg if c[2] in "+-" else 180.0 - deg
            s.lst_manual = None
            return "1"
        if c.startswith("SHL"):
            s.lst = c[3:9]
            s.lst_manual = c[3:9]
            return "1"
        if c == "XGDL": return f"{s.dec_low}|{s.dec_up}#"
        if c == "XGS": return f"{s.trim}#"
        if c == "XGT": return "60.1234#"
        if c == "XGN": return "0,No Wifi#"
        if c == "XGL":
            import datetime as _dt
            now = _dt.datetime.now(_dt.timezone.utc)
            days = now.timestamp() / 86400.0 + 2440587.5 - 2451545.0
            gmst = 18.697374558 + 24.06570982441908 * days
            lst = (gmst + s.lon_deg / 15.0) % 24.0
            h = int(lst); mnt = int((lst - h) * 60); sec = int(round((lst - h - mnt / 60.0) * 3600))
            if sec == 60: sec, mnt = 0, mnt + 1
            if mnt == 60: mnt, h = 0, (h + 1) % 24
            return f"{h:02d}{mnt:02d}{sec:02d}#"
        if c == "XGH": return "030008#"
        if c == "XGHS": return "N#"
        if c.startswith("XSDLL"):
            arg=c[5:]
            s.dec_low = float(arg) if arg else round(s.dec / 314.2, 2)
            return ""
        if c.startswith("XSDLU"):
            arg=c[5:]
            s.dec_up = float(arg) if arg else round(s.dec / 314.2, 2)
            return ""
        if c in ("XSDLl", "XSDLu"): s.dec_low = s.dec_up = 0.0; return ""
        if c.startswith("XSS"): s.trim = float(c[3:]); return ""
        if c.startswith("XST"): s.trk = int(c[3:]); return ""               # tracking stepper position
        if c.startswith("XSR"): s.ra_spd = float(c[3:]); return ""            # firmware: no reply
        if c.startswith("XSD") and not c.startswith("XSDL"): s.dec_spd = float(c[3:]); return ""
        if c.startswith("XSHD"): s.xshd = int(c[4:]); return ""
        if c.startswith("XSHR"): s.xshr = int(c[4:]); return ""
        if c == "hF": s.ra = 0; s.dec = 0; return ""                          # firmware: no reply
        if c.startswith("Q"): s.tracking = False; return ""
        if c.startswith("MHR"):
            s.home_ra = "IN PROGRESS"; s.home_polls = 3; return "1"          # "1" if the search started
        if c.startswith("MHD"): return "1"
        if c == "XGAH":
            if s.home_ra == "IN PROGRESS":
                s.home_polls -= 1
                if s.home_polls <= 0:
                    s.home_ra = "SUCCEEDED"; s.ra = 0; s.dec = 0
            return f"{s.home_ra or 'NOT INITIALIZED'}|NOT INITIALIZED#"
        if c.startswith("XGC"):
            body = c[3:]
            ra_h, dec_d = body.split("*")
            return f"{int(float(ra_h) * 15 * s.ra_spd)},{int(float(dec_d) * s.dec_spd)}#"
        if c.startswith("R") and len(c) == 2: s.rate = c[1]; return ""
        if c == "hP": s.ra = 0; s.dec = 0; s.tracking = False; return ""
        if c == "hU": s.tracking = True; return "1"
        if c in ("MT1", "MT0"): s.tracking = c == "MT1"; return "1"
        if c.startswith("ZZCHARFAIL"): s.char_fail = c.endswith("1"); return "1#"   # test hook
        if c.startswith("Z") or c == "I": return ""   # unknown family / :I# - firmware stays silent
        if c.startswith("XD"): time.sleep(0.2); return ""
        if c.startswith("MXd"): s.dec += int(c[3:]); return "1"
        if c.startswith("MXr"): s.ra += int(c[3:]); return "1"
        if c.startswith("MAL"): s.alt_moves.append(float(c[3:])); return ""
        if c.startswith("MAZ"): s.az_moves.append(float(c[3:])); return ""
        return "0#"


m = Mount()


class Wire:
    """How lx200_OpenAstroTech moves bytes, including what it leaves behind.

    '@' (executeMeadeCommandBlind) flushes the input, writes and never reads,
    so a firmware reply such as the "1" of :MX/:SG/:SHL stays in the buffer.
    '&' (getCommandChar) and ':' (getCommandString) do NOT flush: they read
    whatever is waiting first. ':' commands are routed the way the driver's
    executeMeadeCommand() does it (blind for F*/MA*/Mg*/XS*, one char for
    MX*/MT*/S* except SC, '#'-terminated otherwise).
    """
    def __init__(self):
        self.pending = ""

    @staticmethod
    def route(cmd):
        c = cmd
        if len(c) > 2:
            if c[1] == "F" and c[2] not in "pB": return "blind"
            if c[1] == "F" and c[2] == "B": return "char"
            if c[1] == "M" and c[2] == "A": return "blind"
            if c[1] == "M" and c[2] in "XT": return "char"
            if c[1] == "M" and c[2] in "gG": return "blind"
            if c[1] == "S" and c[2] != "C": return "char"
            if c[1] == "X" and c[2] == "S": return "blind"
        return "hash"

    def transact(self, prefix, cmd, reply):
        mode = {"@": "blind", "&": "char"}.get(prefix) or self.route(cmd)
        if mode == "blind":
            self.pending = reply          # flushed before the write, reply lands after
            return ""
        data = self.pending + reply
        if mode == "char":
            self.pending = data[1:]
            return data[:1]
        i = data.find("#")
        if i < 0:                         # read timed out: bytes consumed, nothing returned
            self.pending = ""
            return ""
        self.pending = data[i + 1:]
        return data[:i + 1]


wire = Wire()


def handle(conn):
    buf = b""

    def send(x):
        try:
            conn.sendall(x.encode())
        except Exception:
            pass

    def defs():
        send(f'<defTextVector device="{DEV}" name="Meade" state="Idle" perm="rw">'
             f'<defText name="OAT_MEADE_COMMAND" label="x"></defText></defTextVector>\n')
        send(f'<defNumberVector device="{DEV}" name="POLAR_ALT" state="Idle" perm="rw">'
             f'<defNumber name="OAT_POLAR_ALT" format="%.3f" min="-140" max="140" step="1">0</defNumber>'
             f'</defNumberVector>\n')
        send(f'<defNumberVector device="{DEV}" name="POLAR_AZ" state="Idle" perm="rw">'
             f'<defNumber name="OAT_POLAR_AZ" format="%.3f" min="-320" max="320" step="1">0</defNumber>'
             f'</defNumberVector>\n')
        send(f'<defNumberVector device="{DEV}" name="GEOGRAPHIC_COORD" state="Ok" perm="rw">'
             f'<defNumber name="LAT" format="%.6f" min="-90" max="90" step="0">37.566500</defNumber>'
             f'<defNumber name="LONG" format="%.6f" min="0" max="360" step="0">126.978000</defNumber>'
             f'<defNumber name="ELEV" format="%.1f" min="-200" max="10000" step="0">38</defNumber>'
             f'</defNumberVector>\n')
        send(f'<defTextVector device="{DEV}" name="TIME_UTC" state="Ok" perm="rw">'
             f'<defText name="UTC">2026-09-18T12:00:00</defText>'
             f'<defText name="OFFSET">+9</defText>'
             f'</defTextVector>\n')
        send(f'<defSwitchVector device="{DEV}" name="CONNECTION" state="Ok">'
             f'<defSwitch name="CONNECT">On</defSwitch><defSwitch name="DISCONNECT">Off</defSwitch>'
             f'</defSwitchVector>\n')

    def polar_reply(vector, element, value):
        # Driver behaviour: accept -> Busy, motors run, then Ok.
        send(f'<setNumberVector device="{DEV}" name="{vector}" state="Busy">'
             f'<oneNumber name="{element}">{value}</oneNumber></setNumberVector>\n')
        if vector == "POLAR_ALT":
            m.alt_moves.append(float(value))
        else:
            m.az_moves.append(float(value))

        def finish():
            time.sleep(0.6)
            send(f'<setNumberVector device="{DEV}" name="{vector}" state="Ok">'
                 f'<oneNumber name="{element}">0</oneNumber></setNumberVector>\n')
        threading.Thread(target=finish, daemon=True).start()

    while True:
        try:
            d = conn.recv(65536)
        except (ConnectionResetError, OSError):
            return
        if not d:
            return
        buf += d
        while True:
            buf = buf.lstrip()
            if buf.startswith(b"<getProperties"):
                if b"/>" not in buf: break
                buf = buf[buf.index(b"/>") + 2:]; defs(); continue
            if buf.startswith(b"<enableBLOB"):
                if b"</enableBLOB>" not in buf: break
                buf = buf[buf.index(b"</enableBLOB>") + 13:]; continue
            if buf.startswith(b"<newTextVector"):
                if b"</newTextVector>" not in buf: break
                i = buf.index(b"</newTextVector>") + 16
                xml = buf[:i].decode(); buf = buf[i:]
                vec = re.search(r'name="([^"]+)"', xml).group(1)
                if vec != "Meade":
                    send(f'<setTextVector device="{DEV}" name="{vec}" state="Ok"></setTextVector>\n')
                    continue
                cmdtxt = re.search(r"<oneText[^>]*>(.*?)</oneText>", xml).group(1).replace("&amp;", "&")
                time.sleep(0.03)
                prefix = cmdtxt[0]
                with m.lock:
                    fw_reply = m.cmd(":" + cmdtxt[1:])
                    if getattr(m, "extra_reply", None):   # :SC# answers twice - both land on the wire
                        fw_reply += m.extra_reply
                        m.extra_reply = None
                    before = wire.pending
                    res = wire.transact(prefix, ":" + cmdtxt[1:], fw_reply)
                    if WIRE_LOG:   # WIRE_LOG=/tmp/wire.log python3 tests/test_x.py
                        with open(WIRE_LOG, "a") as fh:
                            fh.write(f"{cmdtxt!r:28} stale={before!r:8} -> {res!r:34} left={wire.pending!r}\n")
                if prefix == "&" and m.char_fail and not cmdtxt.startswith("&ZZ"):
                    try:
                        conn.sendall(f'<setTextVector device="{DEV}" name="Meade" state="Ok">'
                                     f'<oneText name="OAT_MEADE_COMMAND">'.encode()
                                     + b"\xff" + b'</oneText></setTextVector>\n')
                    except Exception:
                        pass
                    continue
                send(f'<setTextVector device="{DEV}" name="Meade" state="Ok">'
                     f'<oneText name="OAT_MEADE_COMMAND">{res}</oneText></setTextVector>\n')
                continue
            if buf.startswith(b"<newSwitchVector"):
                if b"</newSwitchVector>" not in buf: break
                i = buf.index(b"</newSwitchVector>") + 18
                xml = buf[:i].decode(); buf = buf[i:]
                vec = re.search(r'name="([^"]+)"', xml).group(1)
                states = dict(re.findall(r'<oneSwitch name="([^"]+)"[^>]*>\s*(\w+)\s*</oneSwitch>', xml))
                if vec == "CONNECTION":
                    connected = states.get("CONNECT", "Off") == "On"
                    time.sleep(0.2)
                    send(f'<setSwitchVector device="{DEV}" name="CONNECTION" state="Ok">'
                         f'<oneSwitch name="CONNECT">{"On" if connected else "Off"}</oneSwitch>'
                         f'<oneSwitch name="DISCONNECT">{"Off" if connected else "On"}</oneSwitch>'
                         f'</setSwitchVector>\n')
                else:
                    send(f'<setSwitchVector device="{DEV}" name="{vec}" state="Ok"></setSwitchVector>\n')
                continue
            if buf.startswith(b"<newNumberVector"):
                if b"</newNumberVector>" not in buf: break
                i = buf.index(b"</newNumberVector>") + 18
                xml = buf[:i].decode(); buf = buf[i:]
                vec = re.search(r'name="([^"]+)"', xml).group(1)
                pairs = re.findall(r'<oneNumber name="([^"]+)"[^>]*>([^<]+)</oneNumber>', xml)
                if vec.startswith("POLAR_"):
                    polar_reply(vec, pairs[0][0], pairs[0][1].strip())
                else:
                    # echo every element back, the way a real driver does
                    body = "".join(f'<oneNumber name="{n}">{v.strip()}</oneNumber>' for n, v in pairs)
                    send(f'<setNumberVector device="{DEV}" name="{vec}" state="Ok">{body}</setNumberVector>\n')
                continue
            if buf:
                j = buf.find(b"<", 1)
                if j < 0: break
                buf = buf[j:]; continue
            break


# Never default to 7624: that is the real indiserver port, and a test suite
# that reaches a live mount would drive the actual motors.  The harness always
# passes a port of its own.
PORT = int(os.environ.get("FAKE_INDI_PORT") or (sys.argv[1] if len(sys.argv) > 1 else 17624))

srv = socket.socket()
srv.bind(("127.0.0.1", PORT))
srv.listen(5)
print(f"fake indi server listening on 127.0.0.1:{PORT}", flush=True)
while True:
    c, _ = srv.accept()
    threading.Thread(target=handle, args=(c,), daemon=True).start()
