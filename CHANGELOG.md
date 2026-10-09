# Changelog

## 6.0.6-beta — host-side repair of heading lost to gyro saturation (branch `gyro-fix`)

- Built on 6.0.5 (`uv-mode`, tag `v6.0.5-stable`). Firmware unchanged.
- Problem: the probe's BMI270 runs at ±500 deg/s; a fast shake or flick clips,
  the rotation above the limit never reaches the firmware fusion, and heading
  (unobservable with 6 axes) stays wrong — 18.9° in the owner's
  `tools/gyro_shake_test.py` run (150 of 1003 samples clipped).
- New `GyroClipCompensator` in `endoscope.py` (USB-C composite link only):
  bridges each clipped stretch of each gyro axis from the 8 unclipped samples
  either side (least 4th difference, capped at 2000 deg/s), integrates a shadow
  attitude with the firmware's own step and the repaired rates, and applies
  only its heading difference to the firmware quaternion
  (`R_z(heading) * q_firmware`). Pitch/roll stay the firmware's. With nothing
  clipped the output is the firmware quaternion object itself. ZERO and a link
  generation change reset it.
- Live view: `CLIP+n°` in the status bar for 3 s after a repaired run; a WARN
  toast `FAST SHAKE — HEADING MAY BE OFF, PRESS ZERO` (at most every 10 s) when
  a run was too long / hit the cap / lost packets, or more than 90° of rotation
  had to be put back since ZERO.
- `endoscope.json` key `"gyro_clip_compensation"` (default true via
  `GYRO_CLIP_COMPENSATION_DEFAULT`, read without setdefault) — false restores
  the exact 6.0.5 behaviour.
- Simulator `tools/gyro_clip_sim.py` (Python port of the firmware fusion):
  270 randomised shakes/flicks/twists at 600–1500 deg/s, heading error median
  8.3° → 0.9°, 95th percentile 95° → 16°; no-clip runs bit-identical.
- `tools/gyro_shake_test.py` now also prints `Heading difference (firmware)`
  and `Heading difference (compensated)`; `--csv FILE` keeps the packets.
- Tests: new `test_gyro_comp.py`; `test_uv_presets.py` now expects
  `"Endoscope " + APP_VER` instead of a hard-coded version.

## 6.0.5 — UV fluorescence mode on the field-approved 6.0.4 (test version)

- This release is the field-approved v6.0.4 (the build actually running on the
  owner's Pi, commit `38500b5`) plus UV fluorescence mode ONLY. Screen gyro
  (the 6.1.0 feature) is **not included** on this branch. A build with both —
  v6.2.0 = 6.1.0 (screen gyro) + this same UV mode — exists on branch
  `uv-mode-screengyro`; the UV code itself is semantically identical between
  the two.
- New `UV MODE` button (left column, under FLIP U/D) switches the live view into
  UV fluorescence analysis: detected regions boxed/labelled, optional boost
  (saturation/brightness/edge-feather) and a warmth-corrected blue-cut filter.
  A semi-transparent bottom bar (`BOOST` `SMART BOX` `FILTER` `EXIT UV`) lets the
  operator flip each stage independently without hiding the picture.
- Engineer tuning drawer (small arrow tab on the right edge, UV mode only):
  touch-scrollable rows for every FILTER/DETECTION/BOOST/BOX parameter, a preset
  switcher (FACTORY + saved presets, newest first), `SAVE` (auto-named
  `HH:MM DD-MM`), `DELETE` (two-tap confirm), and `EXPORT` to a USB stick
  (`<stick>/Endoscope_UV_presets/`) or `~/Endoscope_UV_presets/` otherwise.
  Hidden entirely in a production build via `"uv_tuning_panel": false` in
  `endoscope.json`.
- Detection/boost/filter core (`UVParams`/`UVProcessor`/`BoxTracker`) is ported
  from UVScope 1.1's `core.py` with bit-exact parity at scale 1 (see
  `test_uv_core.py`); pixel-unit parameters (merge/open/feather px, box
  thickness) scale by `analysis/display short side / 360` so the UVScope-tuned
  look holds at any camera/display resolution. Colour thresholds and ratios are
  unchanged.
- Toggle states and the active preset persist across restarts
  (`~/.config/endoscope.json`, `~/.config/endoscope_uv_presets.json`); the app
  always boots in normal (non-UV) mode regardless of what was on before.
- Invariant: with UV mode OFF, endoscope.py's RUN-stage frame path is
  byte-identical to field-approved v6.0.4 (commit `38500b5`) — same pixels,
  same widgets/positions except the added `UV MODE` button. Verified by
  `test_uv_ui.py`'s UV-OFF identity check against that commit.
- No new dependencies: numpy + cv2 (already required by install_pi.sh) and the
  stdlib are all this uses; the single-file endoscope.py deployment is unchanged.
- Files added: `test_uv_core.py`, `test_uv_ui.py`, `test_uv_presets.py`,
  `tools/uv_preview.py`, `tools/uv_bench.py`, `UV_MODE_GUIDE_CN.md`.
- Still needs on-device confirmation before this is a production release: real
  Raspberry Pi 5 frame timing (host numbers are a Windows dev-box estimate),
  real touch behaviour for the bottom bar and tuning drawer, a real USB export
  round-trip onto an actual stick, and detection-quality tuning under a real UV
  lamp with this camera (current thresholds were tuned against a YouTube UV
  reference video, not a live UV source).

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
