# OAT Helper

A KStars/Ekos extension for the [OpenAstroTracker](https://openastrotech.com/)
(OAT/OAM), aimed at Linux users who run their mount from KStars/Ekos — on a
Raspberry Pi, Astroberry, StellarMate or a desktop.

Everything needed during a session lives inside Ekos: homing, a shutdown
position, firmware maintenance, and automatic polar-alignment correction driven
by Ekos' own PAA measurements - including the parts a sensorless-DEC OAT needs
every night.

> **Unofficial community project.** Not affiliated with or endorsed by
> OpenAstroTech. It commands real hardware — read the safety notes below.

## What it does

**Observing** — the three tabs you normally see.

- Session setup wizard and field checklist, with the polar-axis altitude
  computed from the site Ekos reports (Seoul 37.57°N → tilt the RA axis 37.6°)
- HA/time sync that writes and verifies the mount's sidereal time (a stale one
  makes GOTO flip across the meridian), RA Hall AutoHome, manual DEC home,
  SET HOME, GO TO HOME
- Shutdown ("release") position so the camera's weight does not rest on the RA ring
- Saved DEC Home restore — no re-aiming DEC after a power cycle
- AutoPA: reads Ekos' Polar Alignment Assistant refresh results — straight from
  KStars over D-Bus, or from the log file — and drives the ALT/AZ motors
  automatically, with a settle window, direction, per-axis and runaway guards
- Sidereal rate trim (slewing, direction buttons and slew rate are Ekos' own
  Mount tab, so they are not duplicated here)
- Mount monitor with the firmware's own DEC limits, target reachability check
- Plate-solve axis calibration that can write steps/degree back to the mount,
  plus the one-off motor direction checks
- Read-only diagnostics

The window opens with three tabs; the rest is behind the **Advanced** toggle.

**Firmware maintenance** — behind the Advanced toggle, and only on the machine
the OAT is plugged into (the tab hides itself elsewhere).

- `Configuration_local.hpp` import / edit / backup / restore
- git-based firmware version management: incremental updates, release-tag
  pinning, source restore, latest-release check
- PlatformIO auto-install, build, flash — offers to disconnect the mount so the
  serial port is free and reconnects it afterwards, with live output, a progress
  bar and a Cancel button
- Guarded factory reset

## Requirements

- KStars/Ekos **3.7.3 or newer** (the Extensions feature)
- INDI driver **LX200 OpenAstroTech** (libindi 2.0.4+ recommended)
- OpenAstroTracker firmware **v1.13.x** (tested against v1.13.9; features are
  gated by the version reported by `:GVN#`)
- Python 3 and PyQt5 (`sudo apt-get install python3-pyqt5`)
- Qt's D-Bus bindings (`PyQt5.QtDBus`, shipped inside `python3-pyqt5`) so
  AutoPA can read the PAA results directly from KStars; without them AutoPA
  falls back to parsing the Ekos log file
- `git`, for firmware version management — optional, but without it updates
  re-download the whole source and release tags cannot be pinned. The Firmware
  tab reports whether it is installed.

## Install

```bash
git clone https://github.com/<you>/oat-ekos-helper.git
cd oat-ekos-helper
chmod +x install.sh
./install.sh
```

Restart KStars completely, then start `oat_helper` from
**Ekos Summary → Extensions**. If you used the earlier `oat_tools` /
`oat_firmware` extensions, the installer removes them and their settings are
migrated on first start.

The installer detects native, Flatpak and Snap KStars data directories, installs
the launcher as the only executable file (KStars treats every executable in its
extensions folder as an extension), and prints a self-check for each one.

## Where to run it

Ekos starts an extension as a child process, so **the extension always runs on
the machine KStars runs on**. It then talks to the mount over INDI's network
protocol as a second client, which means the mount itself can be somewhere else.

| Setup | Observing tabs | Firmware tab |
| --- | --- | --- |
| KStars + INDI + mount all on the Pi (VNC to it) | yes | yes |
| KStars on a laptop, INDI + mount on the Pi | yes — set Host to the Pi | hidden (no serial port here) |
| Script started by hand, no KStars | yes, with a note below | only where the USB cable is |

**Remote INDI.** Open **Connection** and set Host to the machine running
`indiserver` (default `127.0.0.1`, port 7624). Everything that commands the
mount — homing, SET HOME, Park, shutdown position, AutoPA moves — works across
the network.

**AutoPA talks to KStars directly.** The Polar Alignment Assistant values
arrive over the session D-Bus bus (`org.kde.kstars.Ekos.Align`), so nothing has
to be configured: no Ekos file logging, no log path. The AutoPA tab shows which
source is in use.

If D-Bus is unavailable — no session bus, no `python3-pyqt5.qtdbus`, or Ekos'
Align module was never opened — it falls back to parsing the KStars log file and
says so in the log. That path needs Ekos file logging on. If you start
`oat_helper.py` by hand on a different machine than KStars, share the KStars
`logs` folder and point `ekos_log_dir` in `~/.config/oat-helper/config.json`
at it.

**One correction per measurement.** An Ekos capture+solve takes about 25 s, so
the refresh result that appears right after a correction still describes the
error from *before* the move. "Settle after a correction" (30 s by default)
makes AutoPA wait that out rather than applying the same correction twice and
oscillating.

**Flashing is local only.** Build and Flash drive PlatformIO over the USB serial
port, so the Firmware tab is hidden unless this machine has a serial device
(override with `always_show_firmware_tab` in the config). The tab also carries a
banner and warns when the configured upload port does not exist here. Editing
`Configuration_local.hpp`, managing firmware versions and reading diagnostics
work from anywhere.

**Settings are per machine.** `~/.config/oat-helper/` lives on the machine
running the extension, so a laptop and a Pi keep separate copies. If you switch
between them, turn on **Store the DEC Home offset on the mount EEPROM** on the
Home tab: the value then lives on the mount and both machines agree.

The UI language follows the desktop the extension runs on, for the same reason.

## A normal session

1. Power on, connect the mount in Ekos
2. `Update HA automatically + apply to OAT`
3. `Run RA AutoHome`
4. `Restore saved DEC Home` — or aim DEC by hand — then `SET HOME`
5. Measure in Ekos PAA, then `Start PAA automatic correction`. Check the first
   move goes the right way — if an axis runs backwards, set `ALT_INVERT_DIR` /
   `AZ_INVERT_DIR` in `Configuration_local.hpp`, not a tool-side option
6. Image. When finished: **End session (Home → shutdown position)** → power off

Step 6 records the reverse of the move, so step 4 brings DEC straight back to
Home in the next session.

The window opens in **simple mode** with three tabs. Everything else is behind
the **Advanced** button in the status bar.

## Languages

The UI ships in English and Korean; log messages are always English so they can
be pasted into issues unchanged.

The language is detected from the environment KStars runs in, in this order:
`LANGUAGE` (what KDE sets for its UI language), `LC_ALL`/`LC_MESSAGES`/`LANG`,
then `plasma-localerc` / `kdeglobals`. So if KStars is in Korean, the extension
follows. You can override it in **Connection** without restarting.

Adding a language is one file: copy `oat_helper/oat_helper.ko.json` to
`oat_helper.<code>.json` and translate the values. Missing entries fall back to
English.

## Safety

- The extensions move motors. Keep clear of the mount and watch the first moves.
- DEC travel is limited by the firmware; set the DEC limits before relying on
  automatic moves.
- Flashing firmware is blocked while the mount is connected in Ekos, but
  a wrong `Configuration_local.hpp` can still damage hardware.
- Factory reset erases EEPROM calibration. It asks for confirmation twice.

## Development

`tests/` contains a fake INDI server that speaks the OAT Meade dialect and
headless PyQt tests for homing, the PAA watcher, AutoPA correction direction,
firmware management, view modes and i18n:

```bash
python3 tests/run_all.py            # everything, one summary
python3 tests/run_all.py autopa     # just the AutoPA tests
```

Each test starts its own fake INDI server on a private port, so nothing has to
be running first — and the suite can never reach a real `indiserver` on 7624.
GitHub Actions runs the same command on every push.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
