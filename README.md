# AtomS3R-CAM Endoscope v6.0.4

Field-recovered operational package for one-cable USB-C UVC/MJPEG video and CDC IMU telemetry. It includes the final Raspberry Pi host source, the verified prebuilt v6.0.4 merged firmware image, flashing/install scripts, tests and handoff documentation.

Start with `../START_HERE_先看这里.md`. Chinese installation instructions are in `INSTALL_V6_0_4_中文.md`; engineering provenance and limitations are in `HANDOFF.md`.

The exact ESP-IDF firmware source tree was not present in the recovered Pi checkout. The prebuilt image is complete and flashable; see the source-recovery section of `HANDOFF.md` before attempting a firmware rebuild.

This checkout's `uv-mode` branch (v6.0.5, test version) adds an optional UV fluorescence live-view mode on top of the above, with its own toggle bar, engineer tuning drawer, presets and USB export; UV mode off remains byte-identical to v6.0.4 (this branch does not include the screen-gyro feature — see branch `uv-mode-screengyro` for v6.2.0 = 6.1.0 + the same UV mode). See `UV_MODE_GUIDE_CN.md` (Chinese) for operator and engineer instructions.
