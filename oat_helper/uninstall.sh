#!/bin/bash
set -uo pipefail
for KSDIR in "$HOME/.local/share/kstars" "$HOME/.var/app/org.kde.kstars/data/kstars" "$HOME/snap/kstars/current/.local/share/kstars"; do
  DEST="$KSDIR/extensions"
  [ -d "$DEST" ] || continue
  rm -f "$DEST/oat_helper" "$DEST/oat_helper.py" "$DEST/oat_helper.conf" "$DEST/oat_helper.svg" "$DEST/oat_helper".*.json
  rm -rf "$DEST/__pycache__"
done
echo "oat_helper extension removed. User settings and logs were kept."
