# v6.0.5 validation — 2026-10-07

v6.0.5 = field-approved v6.0.4 (`38500b5`) + UV fluorescence mode, branch `uv-mode`, HEAD `ef50c4f`. The UV commits were cherry-picked (`-x`) onto the 6.0.4 base from `uv-mode-screengyro` and verified by interdiff against that branch's originals: the cherry-picked hunks are identical except for version strings and one documented `save_cfg` adaptation (6.0.4's config path differs from 6.1.0/6.2.0's); no screen-gyro code is present on this branch. Host tests run on Windows 11 / Python 3.12 / OpenCV 5.0.

## Test suites and results

- `py -3 -m py_compile endoscope.py test_*.py tools/*.py`: PASS.
- `test_uv_core.py`: PASS. Same UVScope 1.1 core-parity suite as v6.2.0 (bit-identical comparisons across sample videos and parameter sets, plus scaling, `draw=False` and hostile-input edge cases).
- `test_uv_ui.py`: PASS. Includes the UV-off byte-identity check against `38500b5` (pre-UV 6.0.4), confirming UV mode is fully inert when toggled off; plus mode toggle, persistence, bottom bar, drawer open/close/edit/scroll, presets, export and error-fallback cases.
- `test_uv_presets.py`: PASS. Preset load/save/atomicity, backwards-clock ordering, NaN/garbage handling, fsync paths, directory fsync, atomic-write fallback narrowed to permission errors only, and USB/export-dir search tiers.
- `test_endoscope_core.py`: PASS (host-side USB/link/fusion/flip regression; firmware source-contract case stays SKIPPED, as in 6.0.4, because the firmware source tree was not recovered).
- `test_v604.py`: PASS (fullscreen/ZERO/heartbeat/timeout recovery).
- No screen-gyro suite exists on this branch (`test_screen_gyro.py` is not applicable to 6.0.5).

## Reviews performed

OpenAI Codex (`gpt-6-astra`) reviewed the full diff `38500b5..uv-mode`: no P1 findings.

- Round-4 fixes (commits `dfa35f3`, `ef50c4f`): the UV tracker state is now reset on a MIRROR/FLIP orientation change instead of carrying stale detections across the flip; `save_cfg` was made robust to non-finite (`NaN`/`inf`) values already present in a config file instead of raising on write; stray `__pycache__` bytecode left over from the 6.0.4 import was untracked from the repository.

## NOT verified in this workspace

- Real Pi 5 timing (host numbers above are Windows dev-box numbers, not the gate; `test_uv_core.py` prints them for reference only).
- Real touchscreen feel (button hit areas, drawer drag/scroll) — all touch tests here run against synthetic press/release events.
- Real USB stick export (mount discovery, write, `os.sync()`) — exercised only against a fake media root.
- Detection quality with a real camera under a real UV lamp — the parity tests compare against UVScope 1.1's own algorithm on the same sample videos, not against a live fluorescence scene.
- OpenCV 4.x bit-exactness on the Pi (this workspace runs OpenCV 5.0 on Windows).

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
