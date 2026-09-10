#!/usr/bin/env bash
# Read-only report for the two Linux interfaces exposed by one physical device.
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "Installed host application:"
# Reading the constant from the exact script that run_usb.sh launches catches
# the field failure where an app v5.0 copy was started beside v6 firmware.
grep -m1 '^APP_VER = ' "${SCRIPT_DIR}/endoscope.py" 2>/dev/null \
    || echo "  endoscope.py missing or version not found"
echo

echo "USB devices containing Atom/Espressif:"
lsusb 2>/dev/null | grep -Ei 'atom|m5|303a' || echo "  none found"
echo
echo "Video nodes:"
ls -l /dev/video* 2>/dev/null || echo "  none found"
echo
echo "CDC serial nodes:"
ls -l /dev/ttyACM* /dev/serial/by-id/* 2>/dev/null || echo "  none found"
echo
echo "V4L2 devices:"
v4l2-ctl --list-devices 2>/dev/null || echo "  unavailable or no camera"
echo
echo "Capture formats (the selected Atom node must advertise MJPG):"
found_video=0
for node in /dev/video*; do
    [[ -e "${node}" ]] || continue
    found_video=1
    echo "[${node}]"
    # Metadata-only nodes normally expose no MJPG and must not be opened as
    # video. The v6.0.1 host ranks these same format reports automatically.
    v4l2-ctl --device "${node}" --list-formats-ext 2>/dev/null \
        | grep -E "^\s*\[[0-9]+\]|MJPG|MJPEG|Size:|Interval:" \
        | head -24 || echo "  no capture formats (probably metadata)"
done
[[ "${found_video}" -eq 1 ]] || echo "  no /dev/video* nodes found"
echo
echo "User groups (must include video and dialout):"
id
echo

echo "Other processes holding the probe's ports (should be empty while the app is closed):"
sudo fuser -v /dev/ttyACM* /dev/video* 2>&1 | grep -v "^$" || echo "  none"
echo
echo "ModemManager (should be ignoring the probe; install_pi.sh adds the udev rule):"
systemctl is-active ModemManager 2>/dev/null || echo "  not installed (good)"
ls -l /etc/udev/rules.d/99-atoms3r-endoscope.rules 2>/dev/null \
    || echo "  udev rule missing — run ./install_pi.sh once"

echo
echo "Expected for v6.0.4: APP_VER 6.0.4, one MJPG video node, /dev/ttyACM*,"
echo "and the by-id link containing ATOMCAMV604 after flashing the new firmware."

# The rule is device-specific. Replug/reboot after installation applies it.
echo "USB power policy (expected: on):"
for dev in /sys/bus/usb/devices/*; do
    [[ -f "${dev}/idVendor" && -f "${dev}/idProduct" ]] || continue
    if [[ "$(cat "${dev}/idVendor")" == 303a && "$(cat "${dev}/idProduct")" == 8000 ]]; then
        echo "${dev}: $(cat "${dev}/power/control" 2>/dev/null || echo unavailable)"
    fi
done
