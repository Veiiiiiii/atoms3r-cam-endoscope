#!/usr/bin/env bash
# Read-only report for the two Linux interfaces exposed by one physical device.
set -u

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
echo "User groups (must include video and dialout):"
id
