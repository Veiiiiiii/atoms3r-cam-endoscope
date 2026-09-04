#!/usr/bin/env bash
# Install only Raspberry Pi runtime packages; this script does not flash the camera.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
    python3-serial \
    python3-opencv \
    python3-pil.imagetk \
    python3-numpy \
    v4l-utils \
    usbutils

# Group changes take effect at the next login; a reboot is the clearest step.
sudo usermod -aG video,dialout "${USER}"

echo "Install complete. Reboot once, then run: ./run_usb.sh"
