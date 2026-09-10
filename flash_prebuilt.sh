#!/usr/bin/env bash
# Flash the merged ESP-IDF image; see TEST_REPORT.md for validation limits.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${SCRIPT_DIR}/release/atoms3r_cam_uvc_imu_v6_0_4.bin"
PORT="${1:-}"

if [[ -z "${PORT}" ]]; then
    echo "Usage: $0 /dev/ttyACM0" >&2
    echo "Hold reset ~2 s until the green LED lights before running this." >&2
    exit 2
fi
if [[ ! -f "${IMAGE}" ]]; then
    echo "Firmware image not found: ${IMAGE}" >&2
    exit 2
fi

# Erasing first prevents a stale factory partition/configuration from being
# mistaken for v6.0.1. M5Burner/EasyLoader can restore the factory demo later.
python3 -m esptool --chip esp32s3 --port "${PORT}" erase_flash

# Explicitly match this release bootloader flash settings.
python3 -m esptool --chip esp32s3 --port "${PORT}" --baud 921600 \
    write_flash -z --flash_mode dio --flash_freq 80m --flash_size 8MB \
    0x0 "${IMAGE}"

echo "Flash complete. Unplug and reconnect the USB-C cable."
echo "v6 replaces the download port with UVC+CDC: hold reset ~2 s to reflash."
