# Changelog

## 0.6.7

- **AZ was corrected in the wrong direction.** `az_move` was `+residual_az`
  while ALT was `-residual_alt`, so every cycle drove the azimuth axis *away*
  from the pole: the axis walked one way until the run was stopped by hand, and
  the per-axis guard blamed the motors. KStars documents the PAA error signs as
  correction directions (`ekos/align/polaralign.h`: "positive altitude error:
  reduce altitude, positive azimuth error: point telescope more to the left"),
  and the value logged as `PAA Refresh(n): Corrected az/alt` is the *remaining*
  error - `polaralignmentassistant.cpp` passes `processRefreshCoords()`'s
  `azE`/`altE` straight into the log string - so the correction is `-error` on
  both axes. The asymmetry dates from the first commit, when "Invert AZ
  correction" existed to paper over it; 0.6.0 removed that checkbox and left the
  un-inverted default behind. Physical motor reversal belongs in
  `ALT_INVERT_DIR` / `AZ_INVERT_DIR` in `Configuration_local.hpp`, never here.
  `tests/test_autopa_direction.py` pins the sign on both axes, in both
  directions.
- **The same measurement was applied twice every cycle.** 0.6.2 rejected a
  solution whose timestamp predated the finished correction - but Ekos logs a
  PAA Refresh when the *plate solve completes*, ~25 s after the shutter opened,
  so the line always lands *after* the move and that guard almost never fired in
  the field. The image it describes was still taken before the move, so the
  correction was applied a second time, the axis overshot to the mirror of the
  error, and the run oscillated. New **"Settle after a correction"** on the
  AutoPA tab (30 s by default): nothing measured inside that window is used, and
  the log says why. `tests/test_autopa_settle.py` covers it.
  `autopa_adjustment_finished` now starts unset, so the settle window can never
  delay the *first* correction of a run.
- **The PAA values come straight from KStars over D-Bus.** Ekos registers its
  Align module at `/KStars/Ekos/Align` and that interface exports
  `newLog(QString)` - the only signal there that carries the PAA numbers
  (`polarResultUpdated` / `updatedErrorsChanged` live on
  `PolarAlignmentAssistant`, a plain QObject that is never put on the bus). The
  watcher subscribes to it, which removes the log file, the `LogToFile` setting,
  the log-directory search, the 2 MB tail scan every 2.5 s and the timestamp
  parsing: the value arrives when Ekos computes it, so "now" *is* the
  measurement time. Falls back to the log file - with a log line saying which
  and why - when QtDBus, the session bus or the Align interface is missing.
  Exactly one source is ever live: two would deliver the same measurement under
  two signatures and correct twice, which is the failure this watcher exists to
  prevent. "PAA values from" on the AutoPA tab can force the old log-file path.
- **Closing the window during a build/flash asked nothing and orphaned the
  process.** It kept writing the microcontroller's flash with no progress bar
  and no Cancel, and a half-written flash is how a board stops starting.
  `closeEvent` now asks, terminates the process when confirmed, and stops all
  seven timers instead of two - the test harness having to stop the rest by hand
  was the tell.
- **The import-configuration error dialog crashed instead of reporting the
  error.** `fn, _ = QFileDialog.getOpenFileName(...)` rebinds `_`, the
  translation function, to the filter string for the whole scope, so the
  `except` branch's `_("Configuration")` raised
  `TypeError: 'str' object is not callable` - and an Ekos-launched extension
  shows no traceback. `tests/test_translation_hygiene.py` now fails on any scope
  that both assigns `_` and calls it.
- **Ten dialog bodies were never translated.** The confirmations for factory
  reset, Park, the DEC travel limits, the shutdown position, restoring the
  firmware source and restoring DEC Home, plus the whole "Starting the Ekos PAA"
  walkthrough, were bare literals - English in every other language.
  `test_i18n_coverage.py` walks the widget tree, so it can only see text that
  exists at start-up, never a dialog nobody opened; the new hygiene test catches
  these statically instead. The five stale Korean keys are gone and the catalog
  validator is clean again.

### Fewer controls, three that were missing

Removed, because Ekos already does them and did them better:

- **The Controller tab's slewing.** The direction pad, the keyboard slew, the
  slew-rate selector and the ALT/AZ nudge buttons all duplicated Ekos' own Mount
  and AutoPA controls - `TELESCOPE_SLEW_RATE` comes from `LX200Telescope`, which
  this driver inherits, so Ekos has had that dropdown all along. What stays on
  that tab is the sidereal rate trim (`:XGS#`/`:XSS#`), which Ekos has no
  control for at all.
- **Drift alignment (`:XD#`)** - Ekos has its own, and AutoPA supersedes it.
- **Tracking ON/OFF buttons** - Ekos' Mount tab. `set_tracking()` stays: the
  shutdown move uses it.
- **Custom Meade buttons** - the free-text command box sits right next to them.
- **The field checklist** - no functional dependency; that page now carries just
  the two numbers the mount is physically set up with (polar axis altitude and
  the bearing), which is what it was really for.

Kept after checking, against the first instinct to cut them: the *expected motor
profile* (the config inspector compares `Configuration_local.hpp` against it, and
a 0.9° NEMA17 build legitimately differs from a 1.8° one, so hardcoding it would
produce false warnings), Park/Unpark and the target reachability check (Ekos
cannot know the OAT's RA travel limits).

Added:

- **The RA limit is shown as a time of day**, not only as hours remaining:
  "RA tracking left: 02:35 (until 02:37)". The OAT cannot flip across the
  meridian, so that is the hard end of the session - and "until 02:37" is what
  actually answers "can I queue four hours?" at 11pm.
- **One "End session (Home → shutdown position)" button.** These were two
  buttons, and the second was the one people skipped - but it is the move that
  records the DEC travel "Restore saved DEC Home" replays, so skipping it costs
  the *next* session. `tests/test_end_session.py` covers the chain, including
  that the shutdown move waits for Go To Home to release its busy flag.
- **A reminder to recalibrate guiding when AutoPA finishes.** The ALT/AZ moves
  change the mount axes relative to the guide camera, and Ekos reuses a stored
  calibration by default (`Options::reuseGuideCalibration`), so nothing else
  would have said it.

### Review follow-ups

- **Emergency Stop did not stop a queued axis.** `:Q#` halted the motors, but
  `emergency_stop()` never cleared `pa_motion_active` or `pa_move_queue`, and a
  two-axis correction hands its second axis to
  `QTimer.singleShot(150, _send_next_pa_axis)`, which only tests that flag. The
  AZ axis therefore started driving again about 150 ms *after* the emergency
  stop. It now drops the queue and clears the motion state before sending `:Q#`.
- **A solution in flight could move the mount after Stop.** `autopa_tick`
  checked `autopa_running` and then handed a log scan to a worker; a QRunnable
  cannot be cancelled, so after Stop the result still reached
  `_apply_paa_solution`, which checked neither flag, and called `move_pa()`. The
  D-Bus entry point did check it - the shared consumer now does too, which is
  what its docstring already claimed. `tests/test_stop_really_stops.py` covers
  both.
- `closeEvent` stands the watcher, the queued axes and the timers down *before*
  opening its modal question: a `QMessageBox` spins the event loop, so a
  correction could be commanded while the user decided whether to close.
- `_abort_pa_motion` now stamps `autopa_adjustment_finished` like
  `_finish_pa_motion` does. An abort is exactly the case where an axis moved an
  unknown amount, and it was the one path where the settle window stayed inert.
- **"Ekos log file only" was a one-way door.** `_start_paa_dbus()` checked its
  `_dbus_connected` latch before reading the setting, and nothing ever reset the
  latch or unsubscribed, so once a run had connected the combo did nothing for
  the rest of the process while the label still read "D-Bus". The setting is read
  first now, and `stop_autopa_watch` drops the subscription.
- **The D-Bus duplicate guard was dead code.** Its signature was a monotonic
  counter, so `signature == autopa_last_signature` could never match and one
  measurement delivered twice would be corrected twice - the overshoot this
  release exists to prevent. It is derived from the message content now, as the
  log path's already was.
- The "Settle after a correction" value was never written back to
  `config.json`, so `start_autopa_watch`'s own `save_config()` reset it to 30 on
  every start. `paa_pending_move` is also reset per run: keeping the previous
  run's residuals made the per-axis check compare against a different pointing
  and advise reflashing a correctly-wired axis.
- `_run_streamed` decodes as UTF-8 instead of the locale encoding. Under `LANG=C`
  PlatformIO's output raised `UnicodeDecodeError` in the reader, which nulled
  `_fw_process` *without* killing the flasher - so the new close guard saw "no
  process" while avrdude was still writing.
- An unhandled exception in any callback used to abort the process: PyQt5 sends
  it to `qFatal()`, so the window vanished mid-session with no message and no log
  line. `install_crash_guard()` records it instead. The suite could not have
  caught this - `test_full_flow.py` installs its own `sys.excepthook`, and a
  non-default hook is exactly what prevents the abort - so
  `tests/test_crash_guard.py` runs a child process with no hook, the way Ekos
  starts the extension.
- `oat_helper/uninstall.sh` failed loudly when `_install_common.sh` is missing.
  Under `set -uo pipefail` (no `-e`) the failed source did not abort:
  `oat_kstars_dirs` became "command not found", the loop ran zero times, and the
  script still printed "extension removed" and exited 0.
- The three AutoPA tests pin `paa_source` to `logfile`. With the new `auto`
  default, a development machine running KStars with the Align module open would
  take the D-Bus path and never read the log fixtures those tests are built on.

- **Pressing Start could freeze the window for up to 25 s.** `QDBusInterface`'s
  constructor issues a blocking Introspect aimed at KStars, which is
  single-threaded, so starting the watcher while Ekos was mid plate-solve left the
  window unpainted with no Stop button - on a Pi over VNC that reads as a crash.
  It asks the bus daemon (`isServiceRegistered`) instead, which is never busy
  solving. Only `ImportError` used to be caught, so a broken
  `DBUS_SESSION_BUS_ADDRESS` escaped the Start handler leaving `autopa_running`
  True with no timer and no subscription; every failure now falls back to the log
  file and says why.
- **The settle window is measured in arrival time, not log time.** It compared a
  timestamp parsed out of the KStars log against `datetime.now()` here.
  `latest_ekos_paa`'s own docstring says those clocks disagree - which is why
  novelty is decided by signature - and the README documents sharing the log
  folder from another machine. A log clock lagging by more than the settle value
  made `ts <= finished + settle` true for every future solution: the run hung on
  "still settling" all night, and `sol` stayed truthy so the 60 s watchdog never
  fired. The line's own timestamp is still used, but only while the two clocks
  are within 5 minutes of each other, and the mismatch is reported once.
  `tests/test_autopa_clock_skew.py` covers it.
- **A run now has a floor.** `autopa_adjustment_finished` used to double as one,
  rejecting any line older than the button press; setting it to `None` (so the
  settle window could not delay the first correction) removed that. A dedicated
  `autopa_started_at` restores it, which also closes the hole where Stop then
  Start inside a solve window cleared a genuine timestamp. On the baseline-scan
  error path the poller could otherwise act on a PAA Refresh from hours ago.
- **The D-Bus source has retry and a liveness watchdog.** Its branch returned
  before `autopa_timer.start()`, so a refused move (axis busy, mount briefly
  disconnected) dropped the solution with nothing to retry it - a pushed solution
  has no second chance, unlike a log line the poller re-reads from cache - and
  nothing noticed if Ekos died, because the "no PAA value" warning only fires on
  the polling path. The timer runs in both modes now; in D-Bus mode it retries a
  held solution and reports silence instead of scanning the log, so the two
  sources still never both consume a measurement.
- **Runtime status text reaches the catalog.** `translate_widget_tree` looks up a
  widget's *current* text, so anything a later `setText` writes is invisible to
  it and to `test_i18n_coverage.py`, which walks the tree at start-up. On a
  Korean desktop the AutoPA status field was the one English field on its tab.
  `test_translation_hygiene.py` now fails on an unwrapped `setText` literal as
  well as an unwrapped dialog body.
- **Upgrade note, logged once: if you set `AZ_INVERT_DIR` as a workaround,
  revert it.** 0.6.4-0.6.6 drove AZ the wrong way and, when the residual grew,
  advised exactly that. A mount that followed the advice is now inverted twice
  and drives away from the pole for two cycles - each longer because of the
  settle window - before the runaway guard stops the run. The per-axis advice
  itself now says so too.

### Tests and CI

- **The suite could drive a real mount.** `ensure_indi_server()` reused whatever
  was listening on 127.0.0.1:7624 - which on the Raspberry Pi that runs the
  mount is a real `indiserver`. `test_full_flow.py` auto-accepts every dialog
  and runs SET HOME, GO TO HOME, Park, jogs, AutoPA moves and
  `apply_axis_calibration` (which writes steps/degree to EEPROM). Each test now
  starts its own fake server on a private ephemeral port and points the app at
  it; the fake server refuses to default to 7624, and a test that dies before
  `shutdown_app` no longer leaks a server for the next one to inherit.
- Log fixtures are written as UTF-8 rather than in the machine's locale
  encoding: on a cp949 console the `°` became an undecodable byte, so no PAA
  line parsed and three tests failed for a reason that had nothing to do with
  the code.
- `python3 tests/run_all.py` runs everything (optionally filtered by name) and
  prints one summary. GitHub Actions runs it, pyflakes over the whole tree, and
  a syntax check of the installer scripts on every push and pull request.
- New: `test_autopa_direction.py`, `test_autopa_settle.py`,
  `test_autopa_dbus.py`, `test_close_during_flash.py`,
  `test_translation_hygiene.py`.

### Housekeeping

- `oat_helper/uninstall.sh` reads the KStars directory list from
  `oat_kstars_dirs()` instead of keeping its own copy, which had already drifted
  into a silent "uninstall misses a path" bug waiting to happen.
- A `.gitignore`, so `__pycache__` stops showing up as untracked noise in a repo
  whose installer deletes it.
- The user-visible strings that still said "OAT Tools" now say "OAT Helper", and
  step 6 of the PAA walkthrough is a complete sentence again.

## 0.6.6

- **SET HOME writes the mount clock first.** The mount keeps no time across a
  power cycle (the date restarts at 2021-01-01 and the last session's HA is
  reloaded from EEPROM), and SET HOME stores the mount's sidereal time as the
  RA reference while zeroing the tracking steps. So right before `:SHP#` the
  date, local time, UTC offset, site and LST are written from this computer
  and read back; if the longitude encoding is the problem it is corrected on
  the way. This covers every SET HOME path (Home tab, wizard, Restore DEC
  Home). If the site is unknown or the mount is still more than 1 min out,
  SET HOME stops instead of storing a wrong reference. Home tab option:
  "Write the mount clock (HA) automatically before SET HOME" (on by default).
- **Tolerance 5 min -> 1 min.** With the automatic write turned off, SET HOME
  still checks the mount clock, but now refuses anything over 1 min (5 min
  was 1.25° of RA accepted silently).
- **HA / clock buttons warn after tracking.** Any clock or site write makes
  the firmware re-base its RA reference on the new sidereal time while the
  tracking steps since SET HOME stay, so they are counted twice. "Update HA +
  apply" and "Fix mount clock" read the tracking steps (`:GX#` / `:XGT#`)
  first; after more than 30 s of tracking they say how far every coordinate
  will shift (1 min = 15') and advise SET HOME or Solve & Sync, and can be
  cancelled.
- A warning is logged before SET HOME when this computer's clock is neither
  NTP-synchronised nor backed by a hardware RTC.
- `:GX#` parsing also returns the tracking stepper position. The fake mount
  accepts `:XST` (tracking position); `tests/test_clock_before_set_home.py`
  covers the above.

## 0.6.5

- **AutoHome stopped working in 0.6.4 ("No reply byte for &MHRR30#").** 0.6.4
  sent every one-character command (`:MHR`, `:MX`, `:SHL` ...) as `&`, so the
  driver read the answer with `getCommandChar()`. On some Raspberry Pi INDI
  builds that read fails for every `&` command - the same failure behind
  "Tracking ON request -> �" and the reason AutoHome had been sent blind
  before. One-character commands, including tracking, Unpark, SET HOME and
  the AutoPA home/zero, are sent blind (`@`) again; their effect is checked
  with `:GX#`/`:XGAH#`/`:XGAA#`, never with the reply character.
- **The unread "1" is flushed instead.** `IndiClient` remembers when a blind
  command left a one-character answer on the wire and, before the next `:`
  or `&` read, sends the blind no-op `@Z#` so the driver flushes its input.
  Blind commands in a row need no extra flush. Typed commands follow the
  same rule (`:MT1#` goes out as `@MT1#`; type `&` explicitly to read it).
- SET HOME tries `@SHP#` first and falls back to `:SHP#`, verified by `:GX#`.
- Tests: the fake mount models RA AutoHome (`:MHR`, `:XGAH#`, GX "Homing")
  and `FAKE_CHAR_FAIL=1` starts it with every `&` read failing, as on the
  affected Pi. `tests/test_autohome_blind.py` runs AutoHome in both modes;
  the whole suite passes in both, with no "No reply byte" line and no read
  that carries a stale byte (`WIRE_LOG`).

## 0.6.4

- **Replies were read one command late.** The firmware answers `:MX`, `:MH`,
  `:SG`, `:SL`, `:St`, `:Sg` and `:SHL` with a single `1` and no `#`. The
  helper sent them blind (`@`); `lx200_OpenAstroTech` never reads a blind
  reply, and neither `getCommandString()` nor `getCommandChar()` flushes
  before writing, so that `1` was glued to the next answer: the date read as
  `109/27/26`, the sidereal time as `1063648`, the version as `1V1.13.9`.
  They are sent as `&` now, so the driver consumes the byte. (The "garbled
  byte" that once made `&` look unreliable for AutoHome and jogs was the ARM
  0xFF read failure handled in 0.6.3.)
- **`_resync_meade()` reported success while the channel stayed shifted.**
  After `:SC#` (two replies) each read consumed one stale reply and left its
  own behind, so the "version" it recognised was the previous `:GVN#` answer
  and the next read (e.g. `:XGDL#`) returned the version. It now starts with
  a blind no-op (`@Z#`: the driver flushes its input before a blind write,
  and the firmware ignores the unknown `:Z` family) and then verifies.
- **Typed Meade commands pick the right prefix.** `:hF#`, `:Q#`, `:XS...`
  and other commands the firmware does not answer are sent as `@` (a `:`
  made the driver wait for its timeout while holding the serial port);
  one-character answers (`MT`, `MX`, `SHP`, `S...` except `SC`) as `&`. An
  explicit `@`/`&` is kept.
- **DEC "limit here" refuses positions that would break the limit.** The
  firmware stores the *signed* current step position (and its absolute value
  in EEPROM). Pinned on the wrong side of Home it blocked the way back to Home
  and DEC guide pulses and changed meaning after a reboot; at Home(0) it
  cleared the limit. The buttons are now "Set 'down' / 'up' limit here" and
  only act on that side of Home.
- **Diag tab restored.** `make_diag_tab()` was defined twice; the second
  definition replaced the first, so "Refresh OAT status" (the read-only
  mount report) and "Open the log folder" never appeared. They are back, the
  one-click "Set Home" (`:SHP#`, no confirmation) and the duplicate "Go Home"
  (`:hF#` sent with `:`) quick buttons are gone, and "DEC limits" (`:XGDL#`)
  was added.
- **Factory reset** always sends `:XFR#`, the firmware's only reset command;
  the editable, saved command field is now read-only.
- **Button clean-up.** The Mini Controller had two HOME buttons calling the
  same function; the centre key stays, with the correct tooltip, and the note
  no longer refers to the DEC inversion option removed in 0.6.0. The firmware
  tab's four refresh/check buttons are two ("Refresh" also refreshes the
  version panel, "Check for updates" also checks the latest release). The
  wizard's "Wait for auto correction" starts AutoPA and is now called "Start
  auto correction".
- `tests/fake_indi_server.py` models the driver's serial handling (bytes left
  by blind commands, `:SC#`'s second reply, unflushed reads) and the
  firmware's real replies (`:XSR`/`:XSD`/`:hF` answer nothing). With it the
  0.6.3 code shows the shifted reads; `tests/test_064_cleanup.py` covers the
  changes above. `WIRE_LOG=/tmp/wire.log` traces every transaction.

## 0.6.3

- **"Tracking ON request -> �" on a Raspberry Pi.** The garbled character
  is not data from the mount. `lx200_OpenAstroTech` returns `char(-1)` from
  `getCommandChar()` when the one-byte reply of an `&` command could not be
  read; plain `char` is unsigned on ARM, so the `val != -1` check passes and
  the driver publishes the byte 0xFF. Such a reply is now treated as "no
  reply" for every `&` command (with a log line saying the command may still
  have run), instead of being shown or compared as a real answer.
- **Tracking ON/OFF and Unpark are verified with `:GX#`.** The result is read
  from the TRK motion flag (`--T--`) after the command, so the log reports the
  real state and warns when the mount did not follow the request, whatever the
  reply byte was.
- **Axis calibration kept "recommending" values that were never right.** The
  tab told you to turn Tracking OFF, then measured RA as the difference of the
  two plate-solved RA values. With tracking off the pointing is fixed to the
  ground, so its sky RA grows by the sidereal time between the solves: about
  2.5 % per minute for a 10° move, positive or negative depending on the move
  direction. Every applied value therefore moved steps/degree by an amount
  that depended on how quickly you clicked. RA is now measured as the
  hour-angle difference when tracking is off (the elapsed time is taken
  between the two record clicks) and as the sky RA difference when it is on;
  the tracking state is read from `:GX#` at both solves and a run where it
  changed is refused. A DEC move that crosses the pole (RA jumps 12 h) is
  measured as 180° - |d1| - |d2| instead of ~0°. The hint now explains this
  and asks to take up backlash before the start solve.
  `tests/test_axis_calibration.py` covers it (the old formula gives +3.2 %
  for a 76 s run).
- `tests/fake_indi_server.py` models tracking (`:MT1#`/`:MT0#`, `:hU#`,
  `:hP#` and the `:GX#` motion flag) and can reproduce the ARM 0xFF reply;
  `tests/test_tracking_verify.py` covers both paths (0.6.2 logs the U+FFFD).

## 0.6.2

- **AutoPA read every PAA value truncated to whole arc-minutes.** KStars writes
  the "PAA Refresh" line through `qCInfo() << QString`, so the file contains
  `34\"` (QDebug-escaped) instead of `34"`. The parser never matched the
  seconds, so -12' 34" became -12', and any residual under 1' per axis read
  as 0 - AutoPA under-corrected every cycle and could report "within target"
  while more than an arc-minute was left. The escape is now removed before
  parsing, and `tests/test_paa_log_parsing.py` uses the real quoted format
  (it failed on 0.6.1).
- **DEC travel limits defaulted to 90°/90°, which stops GOTO at DEC 0°.** With
  Home at the pole both DEC directions *lower* the declination (one per side
  of the meridian), so 90° reaches the equator, 120° DEC -30°, 135° DEC -45°.
  The hint claimed "90/90 allows full travel" and the dialogs showed an upper
  bound of +90° + up. Defaults are now 135°/135° (the firmware's OAE/OAM
  default), the text and dialogs show the lowest DEC on each side, and 0 is
  refused: despite the protocol docs, `:XSDLL0#`/`:XSDLU0#` make the firmware
  store the *current position* (`Mount::setDecLimitPosition`), so use
  "Reset to configuration values" to clear a limit. Since firmware 1.13.16 a
  DEC limit also blocks DEC guide pulses in that direction.

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
