#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$ROOT"/*.sh "$ROOT"/oat_helper/*.sh "$ROOT"/oat_helper/oat_helper 2>/dev/null || true
bash "$ROOT/oat_helper/install.sh"
