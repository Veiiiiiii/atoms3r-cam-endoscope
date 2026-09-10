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
    usbutils \
    git \
    xdg-user-dirs

# Group changes take effect at the next login; a reboot is the clearest step.
sudo usermod -aG video,dialout "${USER}"

# ModemManager (present on desktop Pi images) probes every new ttyACM device:
# for tens of seconds after plug-in it opens the port, writes AT commands and
# competes for the bytes. Split reads look like CRC errors and "IMU drops" in
# the app. This rule tells it the probe is not a modem. Harmless to re-run and
# harmless on systems without ModemManager.
sudo tee /etc/udev/rules.d/99-atoms3r-endoscope.rules >/dev/null <<'RULE'
# M5Stack AtomS3R-CAM UVC+IMU composite (Espressif VID): not a modem.
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ATTRS{idProduct}=="8000", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="usb", ATTR{idVendor}=="303a", ATTR{idProduct}=="8000", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="usb", ATTR{idVendor}=="303a", ATTR{idProduct}=="8000", TEST=="power/control", ATTR{power/control}="on"
RULE
sudo udevadm control --reload-rules 2>/dev/null || true

echo "Install complete. Reboot once, then run: ./run_usb.sh"

# Refresh both launchers to this exact checkout.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "${SCRIPT_DIR}"/*.sh
"${SCRIPT_DIR}/install_desktop.sh"
