#!/bin/bash
# Remove the OAT Helper extension from every KStars data directory on this
# machine. User settings and logs under ~/.config and ~/.local/share are kept.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=_install_common.sh
. "$ROOT/_install_common.sh"

# The KStars data directories are listed once, in oat_kstars_dirs(): a copy here
# would silently stop removing the extension from a path added to the installer.
while read -r KSDIR; do
  DEST="$KSDIR/extensions"
  [ -d "$DEST" ] || continue
  rm -f "$DEST/oat_helper" "$DEST/oat_helper.py" "$DEST/oat_helper.conf" \
        "$DEST/oat_helper.svg" "$DEST/oat_helper".*.json
  rm -rf "$DEST/__pycache__"
done < <(oat_kstars_dirs)

echo "oat_helper extension removed. User settings and logs were kept."
