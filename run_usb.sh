#!/usr/bin/env bash
# Launch v6.0.1: recoverable UVC video plus CDC IMU on one USB-C cable.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/endoscope.py" --usb-composite "$@"
