#!/usr/bin/env bash
# Reproducible source build. ESP-IDF v5.1.4 must already be exported.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
FIRMWARE_DIR="${SCRIPT_DIR}/firmware"
RELEASE_DIR="${SCRIPT_DIR}/release"
MERGED_BIN="${RELEASE_DIR}/atoms3r_cam_uvc_imu_v6_0_3.bin"

if ! command -v idf.py >/dev/null 2>&1; then
    echo "idf.py not found. Source ESP-IDF v5.1.4/export.sh first." >&2
    exit 2
fi

cd "${FIRMWARE_DIR}"

# External components are pinned by repos.json and omitted from the release
# archive to keep it small. The fetcher safely skips every existing checkout.
python3 fetch_repos.py

# All dependencies are local after the fetch. Disabling the registry makes the
# same build work on restricted/offline networks and avoids unrelated optional
# dependencies declared by Arduino components.
export IDF_COMPONENT_MANAGER=0
# sdkconfig is already pinned to ESP32-S3. Avoiding `set-target` here preserves
# all release Kconfig selections instead of regenerating them on every build.
idf.py build

# Merge bootloader, partition table and application into one file that can be
# written at address 0x0 by esptool on Linux, macOS or Windows.
mkdir -p "${RELEASE_DIR}"
python "${IDF_PATH}/components/esptool_py/esptool/esptool.py" \
    --chip esp32s3 merge_bin \
    --flash_mode dio --flash_freq 80m --flash_size 8MB \
    -o "${MERGED_BIN}" \
    0x0 build/bootloader/bootloader.bin \
    0x8000 build/partition_table/partition-table.bin \
    0x10000 build/usb_webcam.bin

echo "Firmware build complete: ${MERGED_BIN}"
