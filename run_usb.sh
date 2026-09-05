#!/usr/bin/env bash
# Launch v6: recoverable UVC video plus CDC IMU on one USB-C cable.
# This is what the Endoscope desktop icon runs. Fullscreen by default.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# A .desktop launcher can start with an empty environment on some Pi images,
# and Tk then fails with "no display name". Filling these in is harmless when
# they are already correct.
export DISPLAY="${DISPLAY:-:0}"
if [[ -z "${XAUTHORITY:-}" && -f "${HOME}/.Xauthority" ]]; then
    export XAUTHORITY="${HOME}/.Xauthority"
fi

LOG="${HOME}/.cache/endoscope-last-run.log"
mkdir -p "$(dirname "${LOG}")"

# endoscope.py takes a run lock and ends any previous instance itself, so
# double-clicking the icon twice is safe and the second launch wins.
python3 "${SCRIPT_DIR}/endoscope.py" --usb-composite "$@" 2>&1 | tee "${LOG}"
