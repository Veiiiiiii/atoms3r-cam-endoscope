# v6.0.4 operational recovery validation — 2026-09-11

## Recovered artifacts

- Raspberry Pi working-copy ZIP downloaded from GitHub Release and passed ZIP CRC validation.
- Final host `endoscope.py`: 202,504 bytes.
- Merged firmware `release/atoms3r_cam_uvc_imu_v6_0_4.bin`: 837,184 bytes.
- Firmware contains the expected `AtomS3R-CAM UVC+IMU v6.0.4` and `ATOMCAMV604` identity strings.
- Firmware hash matches the hash recorded by the earlier successful ESP-IDF build.

## Tests executed in the recovery workspace

- `python3 -m py_compile`: PASS for application and both Python test files.
- Host regression suite: PASS for packet/CRC recovery, link fault states, version parsing, fusion fallback, video flips and V4L2 capture ownership/reopen.
- Final-field-patch suite: PASS for fullscreen retry, manual ZERO, device epoch invalidation, heartbeat, DTR and invalid-packet timeout recovery.
- `bash -n`: PASS for every shipped shell script.
- ZIP CRC and clean-room extraction: PASS after packaging.

The firmware source-only static contract test is explicitly SKIPPED because the exact ESP-IDF `firmware/` tree was not present in the Pi recovery. This is not represented as a source-build pass.

## Hardware evidence

The user reported that video, long-running IMU, ZERO, fullscreen and requested controls all worked after the two final field patches. No quantified one-hour log, thermal drift dataset or medical-device validation was supplied. The exact physical result cannot be reproduced inside this hardware-free environment.

## Release corrections

The Pi archive contained the correct v6.0.4 binary at project root while the flash scripts expected it under `release/`; the operational package places the exact same binary under the required path. The older ambiguous `release/atoms3r_cam_uvc_imu_v6.bin`, temporary files and stale Arduino route were excluded.
