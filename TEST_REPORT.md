# v6.2.0 validation — 2026-10-07

v6.2.0 = v6.1.0 (screen gyro) + UV fluorescence mode, branch `uv-mode-screengyro`, HEAD `b6965e9` (base import of 6.1.0 at `0372214`). Host tests run on Windows 11 / Python 3.12 / OpenCV 5.0.

## Test suites and results

- `py -3 -m py_compile endoscope.py test_*.py tools/*.py`: PASS.
- `test_uv_core.py`: PASS. 1,440 frame x parameter-set comparisons bit-identical against UVScope 1.1's own `core.py` at scale 1, across 4 sample videos x 9 parameter sets (plus scaling, `draw=False` and hostile-input edge cases); separately, an independent reviewer ran a 4,800-frame random fuzz comparison and found no mismatch.
- `test_uv_ui.py`: PASS. Includes the UV-off byte-identity check against commit `0372214` (pre-UV 6.1.0) at both 1024x600 and 800x480, plain and mirrored, 320x240 and 640x480 capture; plus mode toggle, persistence, bottom bar, drawer open/close/edit/scroll, presets, export, error fallback, Tcl-binding-leak, small/portrait geometry, UV-init-guard and quit-waits-for-export cases.
- `test_uv_presets.py`: PASS. Preset load/save/atomicity, backwards-clock ordering, NaN/garbage handling, fsync-vs-no-fsync paths (SAVE/DELETE fsync, ACTIVATE does not), directory fsync, atomic-write fallback narrowed to permission errors only, USB/export-dir search tiers, and UVScope-1.1 compatibility of exported JSON.
- The three unchanged 6.1.0 suites, all green: `test_endoscope_core.py` (host-side USB/link/fusion/flip regression; the ESP-IDF firmware source-contract case stays SKIPPED, as in 6.1.0, because the firmware source tree was not recovered), `test_v604.py` (fullscreen/ZERO/heartbeat/timeout recovery), `test_screen_gyro.py` (13-group screen-gyro contract).

## Reviews performed

Independent Claude verifiers reviewed each step as it landed (core-port parity, live-view integration, drawer/presets/export). OpenAI Codex cross-model reviews ran against the full diff from the 6.1.0 base: first `gpt-5.5` on `0372214..f9c4ed3`, then `gpt-6-astra` on the resulting fix commits.

- Fix round R1–R8 (commit `790276c`), from the first codex pass (3 P1 + 2 P2) plus an opus adversarial review (0 P1 + 8 P2), sonnet/high fix + independent re-verification: export moved off the Tk thread onto a daemon worker with an EXPORTING.../EXPORT BUSY toast (R1); USB export-dir search widened to the `<media>/*/*` tier (R2); `save_cfg` now writes atomically (R3); preset load no longer re-sorts by a clock that can run backwards (R4); a Tcl command-binding leak on every UV toggle/drawer rebuild was closed (R5); `uv_tuning_panel`'s default is read without `setdefault` so a release build's constant isn't overridden by a stale config value (R6); entering UV mode is refused on the legacy serial link (R7); drawer/bar geometry guards added for small and portrait screens (R8).
- Fix round S1–S11 (commit `142b326`), from a second codex `gpt-6-astra` pass on `790276c` (3 P1 + 6 P2) plus a sonnet verifier that found R1/R5/R8 incomplete: preset loading made exception-proof for any malformed JSON, with App init falling back to FACTORY/UV-off on any UV-init error instead of crashing startup (S1); config toggle saves no longer fsync (S2); the bottom bar is clamped/wrapped to two rows so it can never overlap ZERO or the open drawer (S3); the drawer track's drawn extent and hit rect now share the same source of truth (S4); a tap only registers if release lands within slop of the press point (S5); the atomic-write fallback and per-file/parent-directory fsync behavior were extended (S6/S7); USB export went back to per-file fsync so I/O errors are reported instead of silently succeeding (S8); quit during an export now waits for the worker before tearing down (S9); two more rebuild sites record their Tcl bindings (S10); the export path is now exception-safe end to end (S11).
- Round-3 fixes (commit `b6965e9`, from the three P2s codex's `gpt-6-astra` raised against `142b326`, no P1): the atomic-write-to-direct-write fallback now triggers only on an actual permission error (EACCES/EPERM), not on any failure to open the temp file, so a full-disk condition (ENOSPC) is reported rather than silently falling back; the directory-fsync helper now re-raises a genuine I/O error instead of swallowing every `OSError`, while still swallowing the "fsync-ing a directory isn't a thing here" errors (EINVAL/ENOTSUP/EOPNOTSUPP/EBADF/EISDIR); the fsync-count assertions in `test_uv_presets.py` were made platform-portable by counting per-file and per-directory fsync calls separately, since a durable write on Linux also fsyncs the directory entry (S7) while on Windows that part is a no-op.

## Round-4 fixes (4693455)

Two further fixes landed on top of the delivered `bb565f4` package, both with tests, gates re-run green: the UV tracker state is now reset on a MIRROR/FLIP/ROTATE orientation change instead of carrying stale detections across the flip; and `save_cfg` was made robust to non-finite (`NaN`/`inf`) values already present in a config file, instead of raising on write.

## Deliberate non-fixes (recorded, not forgotten)

- 1080p "boost is slow" (codex): capture is capped at 640x480 by `CAPTURE_LADDER`, so 1080p-rate processing is out of scope for this device.
- The tuning drawer is visible in this test build by owner decision (D10); a release build hides it with `"uv_tuning_panel": false` in `endoscope.json`.
- The "✓" prefix on the active preset and the tuned option-bar transparency were raised as readability notes, not correctness bugs; left as tuned, reported to the owner for a look.

## NOT verified in this workspace

- Real Pi 5 timing (host numbers above are Windows dev-box numbers, not the gate; `test_uv_core.py` prints them for reference only).
- Real touchscreen feel (button hit areas, drawer drag/scroll) — all touch tests here run against synthetic press/release events.
- Real USB stick export (mount discovery, write, `os.sync()`) — exercised only against a fake media root.
- Detection quality with a real camera under a real UV lamp — the parity tests compare against UVScope 1.1's own algorithm on the same sample videos, not against a live fluorescence scene.
- OpenCV 4.x bit-exactness on the Pi (this workspace runs OpenCV 5.0 on Windows).
- The 6.1.0 screen gyro itself (XIAO firmware, fusion accuracy, orientation sign, end-to-end wiring) has never run on hardware; this package carries it forward unchanged from 6.1.0.

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
