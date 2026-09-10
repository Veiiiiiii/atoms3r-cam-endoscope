# Changelog

## 6.0.4 — source changes; validation in TEST_REPORT.md

- Physical fullscreen target and one borderless fallback.
- Nonblocking IMU mailbox, USB-owner pump, heartbeat/valid-packet timeouts.
- Coherent 100 Hz ±500 dps sampling, single software bias estimator, stricter
  static calibration, 2-second static attitude hold; very slow yaw ambiguity documented.
- Atomic stationary ZERO and reboot/recovery reference invalidation.
- Accepted UVC submission busy flag; existing MJPEG colours and capture ownership retained.
- Refresh both desktop launchers; same-checkout ff-only updater; USB power rule.
- Full source, production math tests, host behavior tests, dependency lock and handoff.

Older entries below are historical, not current verified root-cause conclusions.


## 6.0.3 — 2026-09-08

Field report: video smooth, but the attitude indicator kept dropping —
"PROBE NOT READY" / a frozen arrow in CHECK, and "CONNECTING" plus a growing
"IMU drops" counter during RUN while live video continued. Root causes were in
the firmware; the host also masked them with misleading state names.

Firmware (`atoms3r_cam_uvc_imu_v6_0_3.bin`, USB serial `ATOMCAMV603`):

- BMI270 I2C read failures now self-heal. After 0.5 s of consecutive failures
  the firmware re-initialises the internal I2C bus (including the stuck-slave
  bus-clear) and the BMI270/BMM150, re-seeds fusion from gravity, and keeps
  retrying every 2 s. Previously one bad episode poisoned every later read and
  froze the attitude forever while packets kept flowing.
- New telemetry flag bits: `0x10` sensor fault (reads failing ≥ 0.1 s) and
  `0x20` sensor recovered (shown ≈ 1 s). Old hosts ignore them; the layout is
  unchanged. See PROTOCOL.md.
- All CDC transmit calls moved into the TinyUSB device task via a one-slot
  mailbox (`usb_device_cdc_submit`), removing the v6.0.1 cross-task endpoint
  claim. A progress watchdog now un-wedges a stuck transmit path: after 0.6 s
  without an IN completion the FIFO is cleared; after 1.5 s the CDC IN endpoint
  is stalled and un-stalled, which re-arms the DWC2 endpoint. Video endpoints
  are never touched.
- CDC transmit FIFO raised 256 → 512 bytes so a ~30 ms host pause no longer
  costs sequence gaps ("IMU drops").
- If the IMU is absent at boot, initialisation is retried at runtime instead of
  shipping dead packets forever.
- USB identity: bcdDevice 0x0203, product string `... v6.0.3`, serial
  `ATOMCAMV603` (the host reads the version out of the serial string).

Host (`endoscope.py` v6.0.3):

- Sensor trouble is named, not mislabelled: fault-flagged packets show
  `IMU FAULT — SELF-REPAIRING` instead of an eternal `CONNECTING`, and the
  SETUP line explains the probe is repairing its IMU by itself.
- A firmware sensor recovery bumps the link generation, so the existing
  restart logic forces a re-zero — a pre-recovery zero points elsewhere.
- Serial reopen after an error retries in 0.5 s while the port still exists;
  the old 1→8 s exponential back-off applies only when the port is gone. The
  reopen count and last error are shown (`IMU retry N`) and included in
  diagnostics.
- Firmware version is parsed from the USB serial string (`ATOMCAMV603`) and
  displayed, so a stale-firmware/new-host mix is visible on screen.
- `install_pi.sh` installs a udev rule marking the probe `ID_MM_DEVICE_IGNORE`
  so ModemManager on desktop Pi images cannot open the CDC port and steal
  telemetry bytes; `diagnose_usb.sh` reports ModemManager and port holders.

Verification: firmware compiled cleanly with the pinned ESP-IDF v5.1.4
toolchain and the merged image in `release/` is that exact build (see
SHA256SUMS.txt); all host regression tests pass (`python3
test_endoscope_core.py`), including new tests for the fault/recovered states
and serial-string version parsing. Not yet exercised on hardware — the
recovery paths' on-device behaviour is verified by design review and static
contract tests only.

## 6.0.2 — 2026-09-05

Host-only release; firmware unchanged (still `atoms3r_cam_uvc_imu_v6_0_1.bin`).
This entry is recorded retroactively in 6.0.3.

- Made live video smooth on the Pi: capture starts at 320×240 and a ladder
  (320×240 → 480×320 → 640×480) with automatic step-down reacts to a low
  measured FPS or repeated stalls, instead of insisting on VGA.
- Added `--video-size` to pin a capture size, and a 2.5 s frame watchdog that
  triggers UVC reopen.

## 6.0.1 — 2026-09-04

- Fixed a permanent UVC stall: capture, JPEG-conversion and oversized-frame
  failures now release the shared-data mutex on every return path.
- Raised the VGA MJPEG transfer buffer from 90 KiB to 128 KiB so detailed
  scenes do not repeatedly drop otherwise valid frames.
- Made the V4L2 reader thread the sole owner of `VideoCapture`; shutdown no
  longer frees a handle while OpenCV is blocked in `select()`.
- Added automatic UVC reopen after read timeout, USB unplug/replug or a changed
  auto-discovered `/dev/videoN` node.
- Prefer a video node that advertises MJPEG and reject UVC metadata nodes.
- Added visible `USB-C host app v6.0.1` and `UVC RECONNECTING` status so a v5
  host cannot be mistaken for the current USB-composite application.
- Bumped the USB device revision and serial string to make hosts refresh their
  cached descriptor after flashing.

## 6.0.0 — 2026-09-04

- Rebased camera transport on M5Stack's official UVC firmware source.
- Added a TinyUSB CDC ACM interface to the same USB composite device.
- Added 100 Hz firmware-side BMI270 sampling, stationary calibration, Mahony
  fusion, bias trim, sequence numbers and CRC-32 telemetry.
- Removed Wi-Fi startup from the production firmware path.
- Added Raspberry Pi CDC packet recovery and auto-discovery of `/dev/ttyACM*`.
- Added independent live `MIRROR L/R` and `FLIP U/D` controls; both persist and
  modify video pixels only.
- Added an axis-verification gate before `START` to prevent a stale/wrong lens
  axis from producing large static azimuth errors.
- Preserved stock UVC+Wi-Fi and older custom-serial modes only as explicit
  diagnostic fallbacks.
- Added reproducible offline dependency fetching, a build-tested merged image,
  and Linux/macOS plus Windows one-command flashing scripts.
