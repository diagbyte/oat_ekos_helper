#!/bin/bash
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
COMMON="$SRC/../_install_common.sh"
[ -r "$COMMON" ] || COMMON="$SRC/_install_common.sh"
# shellcheck source=/dev/null
. "$COMMON"

echo "== Installing OAT Helper =="
oat_check_kstars_version
oat_ensure_pyqt5 || true

# Older releases shipped two separate extensions; remove them so the Extensions
# list does not show three entries.
rm -f "$HOME/.local/share/applications/oat-tools.desktop" \
      "$HOME/.local/share/applications/oat-firmware-manager.desktop"

while read -r KSDIR; do
  DEST="$KSDIR/extensions"
  mkdir -p "$DEST"
  for OLD in oat_tools oat_firmware; do
    if [ -e "$DEST/$OLD" ]; then
      rm -f "$DEST/$OLD" "$DEST/$OLD.py" "$DEST/$OLD.conf" "$DEST/$OLD.svg" "$DEST/$OLD".*.json
      echo "  removed the previous $OLD extension (its settings are migrated on first start)"
    fi
  done
  # Only the launcher may be executable: KStars treats every executable file
  # in this folder as an extension, and an unpaired one breaks discovery.
  install -m 755 "$SRC/oat_helper"      "$DEST/oat_helper"
  install -m 644 "$SRC/oat_helper.py"   "$DEST/oat_helper.py"
  install -m 644 "$SRC/oat_helper.conf" "$DEST/oat_helper.conf"
  install -m 644 "$SRC/oat_helper.svg"  "$DEST/oat_helper.svg"
  for cat in "$SRC"/oat_helper.*.json; do
    [ -e "$cat" ] && install -m 644 "$cat" "$DEST/$(basename "$cat")"
  done
  oat_repair_extension_dir "$DEST"
  oat_report_extension_dir "$DEST"
done < <(oat_kstars_dirs)

echo
echo "Quit KStars/Ekos completely and start it again."
echo "In Ekos Summary > Extensions, select 'oat_helper' and press Start."
echo "The mount driver must be 'LX200 OpenAstroTech'."
echo "Firmware build/flash appears only on the machine the OAT is plugged into."
