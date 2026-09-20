# Changelog

## 0.6.1

- **Translation gaps closed.** Text built at runtime never went through the
  widget-tree pass, so the ⚙ Settings, Log and Advanced toggles fell back to
  English as soon as they were clicked, and the checklist editor opened with the
  English defaults instead of what was on screen. Those now translate, the
  editor starts from the visible items, and 30 further labels, group titles and
  tooltips (Build environment, Expected motor profile, Meade command, Tracking,
  Custom buttons, the axis-calibration labels ...) gained Korean text.
- `tests/test_i18n_coverage.py` walks every widget with the UI in Korean and
  fails on anything still in English, so a new string cannot slip through
  untranslated again.

## 0.6.0

- **Direction overrides removed.** "Invert DEC", "Invert ALT correction" and
  "Invert AZ correction" are gone. Motor direction belongs to the firmware
  (`DEC_INVERT_DIR`, `ALT_INVERT_DIR`, `AZ_INVERT_DIR` in
  Configuration_local.hpp) and is set once when the mount is built, so a
  tool-side flip only hid a misconfiguration - and the DEC one never applied to
  GOTO, Park or Go To Home anyway. When a correction makes an error grow, the
  log now names the firmware setting to change instead of a checkbox.
- Wording no longer describes features as matching or being compatible with
  another tool; the legacy Korean patch notes under `docs/` were superseded by
  this changelog and removed.

## 0.5.9

- **The direction overrides moved off the session pages.** "Invert DEC",
  "Invert ALT correction" and "Invert AZ correction" now sit together on the
  Axis cal page under "Motor direction (check once, then leave alone)", since
  nobody flips them mid-session. The DEC one is labelled "Invert DEC jog
  buttons" because that is all it does - GOTO, Park and Go To Home use the
  firmware direction - and each tooltip names the real setting
  (DEC_INVERT_DIR / ALT_INVERT_DIR / AZ_INVERT_DIR in Configuration_local.hpp).

## 0.5.8

- **Manual AutoPA moves take degrees/minutes/seconds.** Ekos states the polar
  error that way ("Corrected az: -01° 52' 00\""), so entering it no longer
  means converting to arcminutes in your head. The row shows the arcminute
  equivalent as you type and says when a value exceeds the axis travel limit.
  Quick buttons now cover ±30' as well, for the first coarse correction.

## 0.5.7

- **Removed the duplicate DEC shutdown control.** "Move DEC to power-off
  position" and "Move to shutdown position" did the same thing from the same
  stored value; only the shutdown-position row remains, and it still updates
  the DEC Home restore value for the next session.
- Note on the LCD: the firmware exposes no command for the display.
  `MeadeCommandProcessor` has no brightness handler at all, and
  `LcdMenu::setBacklightBrightness()` is only reached from the mount's own
  CAL > Brightness menu and from the EEPROM value read at boot. The setting is
  stored in EEPROM but nothing can write that address remotely, so dimming or
  switching the display off has to be done on the mount itself.

## 0.5.6

- **AutoPA no longer acts on a measurement taken before its own correction.**
  An Ekos capture+solve takes around 25 s, so the refresh line that appears just
  after a move was measured before it. The watcher accepted it, applied the same
  correction a second time and overshot to the mirror image of the error
  (-104' -> +104' -> -106' -> +109' ...), which never converged and made the
  direction guard report a reversed axis that was in fact correct. Solutions
  older than the end of the last correction are now skipped with a note.
- **An oversized correction is clamped instead of refused.** Blocking the move
  made the first, legitimately large correction impossible and pushed people to
  raise the safety limit until it protected nothing; the axis now moves by the
  limit and the next cycle continues.

## 0.5.5

- **The longitude encoding is now calibrated against the mount.** Even with the
  site, date, time and offset all correct, the firmware can end up computing
  sidereal time for the opposite side of the planet, because the Meade spec has
  two longitude conventions (signed with east negative, and unsigned 0-360 going
  west) and driver/firmware builds disagree about which they speak. For Korea
  that is a 7 hour error, which is what made GOTO flip across the meridian and
  drive DEC into the travel limit. 'Fix mount clock' now writes each candidate
  encoding, reads `:XGL#` back after each, keeps the one whose sidereal time
  matches, and remembers it.
- The final warning now names the size of the error in degrees of longitude
  instead of only saying "still wrong".

## 0.5.4

- **The date command answers twice and was desynchronising the channel.**
  `:SC#` replies with `1Updating Planetary Data#` *and* a second string of
  spaces. The passthrough reads one, so every later reply was shifted by one:
  the date came back as `109/19/26`, the sidereal time read as garbage, and the
  clock repair reported "still wrong" while actually writing correct values.
  The channel is now drained after the date is set (and after the driver pushes
  time/site), using `:GVN#` as an anchor, and the date is only rewritten when it
  really differs. A reply to `:XGL#` that is not a time triggers one resync and
  retry.

## 0.5.2

Field fixes after a session where GOTO drove DEC the wrong way and stopped at a
limit while manual jogs were fine.

- **The sidereal time is now written to the mount.** The HA button computed the
  LST, logged it and left the mount with whatever it had. The firmware never
  recomputes `_LST` on its own, and `:SHP#` stores it as the RA home reference,
  so a stale value made the firmware place targets past the RA limit, flip
  across the meridian, invert the DEC target and clamp at the DEC travel limit.
  The tool now sends `:SHLHHMMSS#`, reads `:XGL#` back and reports the mount's
  date, UTC offset and longitude alongside it.
- **SET HOME refuses to run on a wrong clock**: if the mount's LST is more than
  5 minutes from the computed one it warns, and declining repairs the clock
  instead of leaving you stuck.
- **'Fix mount clock' writes the whole chain**: date (`:SC`), local time
  (`:SL`), UTC offset (`:SG`), latitude/longitude (`:St`/`:Sg`) and sidereal
  time (`:SHL`), then reads them back. If the LST is still wrong afterwards the
  log prints what the mount now holds, so the offending value is visible instead
  of a bare "mismatch". The HA button runs it automatically when its own check
  fails.
- **DEC limits were read with the wrong units.** `:XGDL#` reports how far the
  ring may travel down/up from Home, not signed bounds. The monitor, the target
  check and the shutdown-position move all treated "30.0" as +30° instead of
  "30° of downward travel". The limits can now also be typed in and applied, so
  a limit set too tight can be widened without moving the axis there.
- **Axis calibration is guarded**: a negative or zero steps/degree is refused, a
  change over 20% needs explicit confirmation, and the previous value can be
  restored in one click.
- Partial INDI number updates no longer wipe the rest of a vector (a
  `GEOGRAPHIC_COORD` update carrying only LAT used to drop the longitude).
- The shutdown position states which way it will move ("30° in the same
  direction as the DEC ↓ button") instead of leaving the sign to guesswork, and
  points at 'Save current position' as the way to record it without thinking
  about signs at all.

## 0.5.0 — first public release

- Single extension named `oat_helper`: the previous `oat_tools` and
  `oat_firmware` are merged, since KStars can only run one extension at a time.
  The installer removes the old pair and their settings are migrated on first
  start. Firmware maintenance sits behind the Advanced toggle and hides itself
  on machines without a serial port.

- English/Korean UI (catalog based, switchable at runtime); log messages are
  always English so they can be pasted into issues unchanged.
- Language detection follows KDE's UI language (`LANGUAGE`, `plasma-localerc`,
  `kdeglobals`) before the POSIX locale variables, so the extension matches
  KStars instead of the shell locale.
- README documents which machine each extension has to run on (remote INDI is
  fine; flashing is local only), and the firmware tab shows the same warning in
  the window plus a check that the configured upload port exists here.
- Field checklist shows the polar-axis altitude (= site latitude) and which
  pole to face, computed from the coordinates Ekos reports. A compass bearing
  is added only if the optional `magnetic_declination_deg` config key is set,
  because declination cannot be derived from the coordinates alone.
- Repository layout, GPL-3.0 licence, headless test suite and CI.
- Review pass before release: no mount command runs on the GUI thread any more
  (Park's offset read, the stored DEC offset lookup and the AutoPA Meade
  fallback moved to workers), dialog text and wizard status labels are
  translated, and `tests/test_full_flow.py` walks 38 user actions end to end.

Everything below was developed before the first public release and is kept for
context; the detailed notes live in `docs/`.

### Observing

- **Homing** — RA Hall AutoHome, manual DEC home, SET HOME verified by reading
  `:GX#` back rather than by the reply character, GO TO HOME, saved DEC Home
  restore, shutdown ("release") position that also updates the restore value.
- **AutoPA** — watches the Ekos PAA refresh results (log paths auto-detected for
  native/Flatpak/Snap KStars), moves ALT/AZ through the INDI POLAR_* properties
  with a Busy/Ok handshake and a `:GX#` cross-check, per-axis inversion advice,
  runaway guard, two-solution wait, log diagnosis button.
- **Mount** — the axis calibration refuses a negative or zero steps/degree,
  asks for explicit confirmation when the measurement differs from the mount's
  value by more than 20%, keeps the previous value and can restore it in one
  click. Firmware DEC limits read/write, target reachability check via
  `:XGC#`, Park/Unpark, slew-rate selection, tracking trim, drift alignment,
  plate-solve axis calibration that can write steps/degree to the mount,
  keyboard slewing, firmware version gating.
- **UI** — compact status bar (coloured dots for INDI and mount, firmware
  version); the mount device is picked from the devices the INDI server
  announces instead of typed; the RA tracking-time readout lives on the Monitor tab next to the
  RA bar, which only polls while that page is on screen. Simple mode with three
  tabs, everything else behind Advanced;
  scrollable tabs, collapsible log and remembered window layout for VNC.

### Firmware maintenance

- git based firmware version management: incremental update, release-tag
  pinning, source restore, latest-release check. The Build environment panel
  shows whether git is installed and offers the install command when it is not.
- `Configuration_local.hpp` import/edit/backup/restore, PlatformIO auto-install,
  build and flash with a connected-mount guard, guarded factory reset (`:XFR#`).
- Build and flash stream their output live with a progress bar, the current
  phase, an elapsed clock and a Cancel button, so a slow compile is no longer
  indistinguishable from a stalled upload.
- Flashing offers to disconnect the mount first (the INDI driver holds the
  serial port, so an upload against a connected mount fails) and reconnects it
  once the board has rebooted.

### Fixes worth calling out

- Meade commands are serialized; concurrent polling used to make SET HOME fail
  before the command was ever transmitted.
- Blind (`@`) command acknowledgements are consumed, so they can no longer be
  mistaken for the reply to the next command.
- A stale DEC homing offset is cleared (or stored deliberately) so firmware
  Park stops where you expect.
- Installers no longer install `*.py` as executable — KStars treats every
  executable in its extensions folder as an extension, and the unpaired file
  made the whole extension list come up empty.
