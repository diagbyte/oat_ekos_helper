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
`{placeholders}` must keep them unchanged. `python3 tests/check_catalogs.py`
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

## Tests

`tests/` has a fake INDI server implementing the OAT Meade dialect plus headless
PyQt tests:

```bash
QT_QPA_PLATFORM=offscreen python3 tests/test_full_flow.py
QT_QPA_PLATFORM=offscreen python3 tests/test_i18n.py
```

`test_full_flow.py` drives every user action in order and fails on any
unexpected exception or error line, so run it before opening a PR.

Two rules that keep the UI responsive:

- Never call `self.indi.meade()` from the GUI thread. Put it in the `job()`
  function handed to `run_async()`; the Meade channel is serialized and a poll
  may be holding it.
- UI strings are English literals in the code. Static widget text is translated
  automatically after the window is built; for text you set at runtime, wrap it
  in `_()`.

Please add or extend a test when you touch homing, the PAA watcher or anything
that writes to EEPROM.
