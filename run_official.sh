#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export DISPLAY="${DISPLAY:-:0}"
exec python3 "$SCRIPT_DIR/endoscope.py" --official --video auto "$@"

