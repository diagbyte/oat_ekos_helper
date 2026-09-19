# OAT Helper

A KStars/Ekos extension for the [OpenAstroTracker](https://openastrotech.com/)
(OAT/OAM), aimed at Linux users who run their mount from KStars/Ekos — on a
Raspberry Pi, Astroberry, StellarMate or a desktop.

`OATControl`, the official desktop tool, is Windows only. This extension covers
the same ground from inside Ekos, and adds the parts a sensorless-DEC OAT needs
every session: homing, a shutdown position, and automatic polar-alignment
correction driven by Ekos' own PAA measurements.

> **Unofficial community project.** Not affiliated with or endorsed by
> OpenAstroTech, and not related to their Windows tool OATControl. It commands
> real hardware — read the safety notes below.

## What it does

**Observing** — the three tabs you normally see.

- Session setup wizard and field checklist, with the polar-axis altitude
  computed from the site Ekos reports (Seoul 37.57°N → tilt the RA axis 37.6°)
- HA/time sync that writes and verifies the mount's sidereal time (a stale one
  makes GOTO flip across the meridian), RA Hall AutoHome, manual DEC home,
  SET HOME, GO TO HOME
- Shutdown ("release") position so the camera's weight does not rest on the RA ring
- Saved DEC Home restore — no re-aiming DEC after a power cycle
- AutoPA: watches Ekos' Polar Alignment Assistant refresh results and drives the
  ALT/AZ motors automatically, with direction, per-axis and runaway guards
- Mini controller (buttons or keyboard), slew-rate selection, tracking trim
- Mount monitor with the firmware's own DEC limits, target reachability check
- Plate-solve axis calibration that can write steps/degree back to the mount
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

**AutoPA reads a log file.** The Polar Alignment Assistant values come from the
KStars log, which KStars writes on its own machine. Since the extension runs
there too, this is automatic. If you start `oat_tools.py` by hand on a different
machine than KStars, share the KStars `logs` folder and point
`ekos_log_dir` in `~/.config/oat-helper/config.json` at it.

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
5. Measure in Ekos PAA, then `Start PAA automatic correction`
6. Image. When finished: `GO TO HOME` → `Move to shutdown position` → power off

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
headless PyQt tests for homing, the PAA log watcher, firmware management, view
modes and i18n:

```bash
QT_QPA_PLATFORM=offscreen python3 tests/test_full_flow.py
```

Each test starts its own fake INDI server, so nothing has to be running first.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
