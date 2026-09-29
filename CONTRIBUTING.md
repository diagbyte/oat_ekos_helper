# Contributing

Thanks for looking at this. It is a small project, so the process is informal.

## Reporting problems

Include:

- KStars version, INDI/libindi version, OAT firmware version (shown in the
  status bar as `FW:`)
- What you pressed and what happened
- The relevant part of the OAT Helper log (log messages are in English on
  purpose, so paste them as they are), and `~/.local/share/oat-helper/logs/`
  for detail

For AutoPA problems, press **Diagnose PAA log** on the AutoPA tab first and
include that output — it reports the log paths, the Ekos logging settings and
the last PAA values that were parsed.

## Translations

One file per language, per extension:

```bash
cp oat_helper/oat_helper.ko.json oat_helper/oat_helper.de.json
```

Keys are the English source strings; translate the values only. Keys with
`{placeholders}` must keep them unchanged.

Firmware and mount operation names stay in English inside the translated text -
`SET HOME`, `Park`, `Home`, `Flash`, `RA`/`DEC`, `Configuration_local.hpp` - so
the log, the UI and the firmware documentation all use one vocabulary. That is
why `Park` and `SET HOME` have no Korean entry: as whole dialog titles they are
already correct. `check_catalogs.py` lists what is untranslated as a count, not
a failure. `python3 tests/check_catalogs.py`
reports keys that no longer exist in the source. Missing keys
fall back to English, so a partial translation is fine. Log messages are not
translated by design.

## Code

- One file, Python 3 + PyQt5, no build step. Users copy it into the KStars
  extensions directory, so keep it dependency-free.
- Never open the OAT serial port directly. Everything goes through the running
  INDI server as a second client, so Ekos keeps ownership of the port.
- Commands that move the mount need a guard: check the busy flags, verify the
  result with `:GX#`, and log both the command and the outcome.
- Check firmware support with `_fw_at_least()` rather than assuming a command
  exists — the `:h` and `:T` families differ between firmware versions.
- AutoPA corrections are `-error` on **both** axes. KStars documents the PAA
  error signs as correction directions (`ekos/align/polaralign.h`), and the
  value it logs as `Corrected az/alt` is the remaining error, not the move
  already applied. Physical motor reversal belongs in `ALT_INVERT_DIR` /
  `AZ_INVERT_DIR` in `Configuration_local.hpp` — a tool-side flip only hides a
  misconfiguration, which is how the AZ sign stayed wrong from 0.6.0 to 0.6.7.
- Never unpack into `_`: it is the translation function, and shadowing it turns
  the next `_("...")` in that scope into `TypeError: 'str' object is not
  callable` — on an error path, where nobody sees the traceback.

## Tests

`tests/` has a fake INDI server implementing the OAT Meade dialect plus headless
PyQt tests:

```bash
python3 tests/run_all.py            # everything, one summary
python3 tests/run_all.py autopa     # just the tests whose name matches
```

`test_full_flow.py` drives every user action in order and fails on any
unexpected exception or error line, so run the suite before opening a PR. CI runs
the same command.

Each test starts its own fake INDI server on a private ephemeral port and points
the app at it. Keep it that way: an earlier harness reused whatever listened on
7624, and on the machine that runs the mount that is a real `indiserver` — the
suite auto-accepts every dialog and would have driven the actual motors and
written EEPROM. Never hardcode 7624 in a test, and write log fixtures with
`encoding="utf-8"` rather than the machine's locale encoding.

Two rules that keep the UI responsive:

- Never call `self.indi.meade()` from the GUI thread. Put it in the `job()`
  function handed to `run_async()`; the Meade channel is serialized and a poll
  may be holding it.
- UI strings are English literals in the code. Static widget text is translated
  automatically after the window is built; anything set at runtime - toggle
  labels, dialog titles and bodies, combo items added later - must be wrapped in
  `_()`. `tests/test_i18n_coverage.py` runs the UI in Korean and fails on
  anything left in English, but it can only see text that exists at start-up:
  a dialog nobody opened is invisible to it. `tests/test_translation_hygiene.py`
  catches unwrapped `QMessageBox` bodies statically, and
  `python3 tests/check_catalogs.py` reports catalog keys that no longer exist.

Please add or extend a test when you touch homing, the PAA watcher or anything
that writes to EEPROM.
