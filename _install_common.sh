#!/bin/bash
# Shared helpers for the OAT Ekos extension installers.
#
# KStars discovers extensions by listing *executable* files in its extensions
# directory and pairing each one with "<name>.conf".  Any other executable file
# in that directory (for example an oat_tools.py installed with mode 755) is
# treated as an extension candidate, has no matching .conf, and makes the whole
# discovery pass fail — the Extensions dropdown then stays empty.  Python
# modules must therefore be installed non-executable.

oat_kstars_dirs() {
  # Print every KStars data directory that exists on this machine.
  local found=0 d
  for d in "$HOME/.local/share/kstars" \
           "$HOME/.var/app/org.kde.kstars/data/kstars" \
           "$HOME/snap/kstars/current/.local/share/kstars"; do
    if [ -d "$d" ]; then echo "$d"; found=1; fi
  done
  # Nothing found: fall back to the native path and create it.
  if [ "$found" -eq 0 ]; then echo "$HOME/.local/share/kstars"; fi
}

oat_check_kstars_version() {
  local v
  # Safe under 'set -e -o pipefail': an absent kstars binary yields an empty value.
  v="$( { kstars --version 2>/dev/null || true; } | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)"
  [ -z "$v" ] && return 0
  local need=3.7.3
  if [ "$(printf '%s\n%s\n' "$need" "$v" | sort -V | head -1 || true)" != "$need" ]; then
    echo "  ! KStars $v detected - Ekos Extensions needs $need or newer."
    echo "    It will not appear in the Extensions list until you upgrade."
  else
    echo "  KStars $v detected - Extensions supported"
  fi
}

oat_ensure_pyqt5() {
  if python3 -c 'from PyQt5 import QtWidgets' >/dev/null 2>&1; then
    echo "  PyQt5 OK"
    return 0
  fi
  echo "  PyQt5 is missing. Trying to install it (needs sudo)."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y python3-pyqt5 || {
      echo "  ! Could not install PyQt5 automatically. Install it manually: sudo apt-get install python3-pyqt5"; return 1; }
  else
    echo "  ! No apt-get here. Install PyQt5 (python3-pyqt5) with your distribution's package manager."; return 1
  fi
}

oat_repair_extension_dir() {
  # Remove the executable bit from anything that is not a launcher, so a
  # previously broken install cannot keep KStars discovery from working.
  local dest="$1" f base fixed=0
  for f in "$dest"/*; do
    [ -f "$f" ] || continue
    [ -x "$f" ] || continue
    base="$(basename "$f")"
    case "$base" in
      *.py|*.pyc|*.conf|*.svg|*.png|*.jpg|*.gif|*.bmp|*.json|*.md|*.txt)
        chmod 644 "$f"; echo "  fixed: removed the executable bit from $base (it would break extension discovery)"; fixed=1 ;;
    esac
  done
  rm -rf "$dest/__pycache__" 2>/dev/null || true
  return 0
}

oat_report_extension_dir() {
  local dest="$1" exe conf
  echo
  echo "Installed to: $dest"
  for exe in "$dest"/*; do
    [ -f "$exe" ] && [ -x "$exe" ] || continue
    case "$(basename "$exe")" in *.conf|*.svg|*.png|*.jpg|*.gif|*.bmp) continue ;; esac
    conf="$exe.conf"
    if [ -r "$conf" ] && grep -q '^minimum_kstars_version=[0-9]\+\.[0-9]\+\.[0-9]\+' "$conf"; then
      echo "  ✓ $(basename "$exe")  (conf OK - it will appear in the Extensions list)"
    else
      echo "  ✗ $(basename "$exe")  (no matching .conf or bad format - this file blocks extension discovery)"
    fi
  done
}
