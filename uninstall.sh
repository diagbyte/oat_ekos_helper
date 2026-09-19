#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$ROOT/oat_helper/uninstall.sh"
