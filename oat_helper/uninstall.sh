#!/bin/bash
set -uo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
COMMON="$SRC/../_install_common.sh"
[ -r "$COMMON" ] || COMMON="$SRC/_install_common.sh"
# Without -e a failed source does not abort: oat_kstars_dirs would become
# "command not found", the loop would iterate zero times and the script would
# still report success. Check explicitly instead.
if [ ! -r "$COMMON" ]; then
  echo "error: cannot find _install_common.sh next to or above $SRC" >&2
  echo "       run uninstall.sh from the repository, not from a copied oat_helper/ folder." >&2
  exit 1
fi
# shellcheck source=/dev/null
. "$COMMON"

# The KStars data directories are listed once, in oat_kstars_dirs(): a copy here
# would silently stop removing the extension from a path added to the installer.
while read -r KSDIR; do
  DEST="$KSDIR/extensions"
  [ -d "$DEST" ] || continue
  rm -f "$DEST/oat_helper" "$DEST/oat_helper.py" "$DEST/oat_helper.conf" "$DEST/oat_helper.svg" "$DEST/oat_helper".*.json
  rm -rf "$DEST/__pycache__"
done < <(oat_kstars_dirs)

echo "oat_helper extension removed. User settings and logs were kept."
