# v6.2.0 (= v6.1.0 + UV mode) — 2026-10-07

- All six host test suites green on branch `uv-mode-screengyro` (`b6965e9`): `test_uv_core.py`, `test_uv_ui.py`, `test_uv_presets.py`, `test_endoscope_core.py`, `test_v604.py`, `test_screen_gyro.py`. Details: `TEST_REPORT.md`.
- UV-off path byte-identical to 6.1.0 (commit `0372214`): YES, see `test_uv_ui.py`.
- Cross-model review: codex (gpt-5.5, then gpt-6-astra) across three fix rounds (R1-R8, S1-S11, round-3); no unresolved P1 findings.
- Pi 5 hardware timing, real touchscreen, real USB export, detection under a real UV lamp, and the 6.1.0 screen gyro itself: NOT VERIFIED on hardware (the screen gyro has never run on a device).
- This package is a host-validated software build, not a hardware-field-validated one. See `TEST_REPORT.md` "NOT verified in this workspace".

# v6.0.4 field-recovered operational release

- Final Raspberry Pi host source recovered from the user's working device: YES.
- Correct merged v6.0.4 firmware image recovered and placed under `release/`: YES.
- Firmware SHA-256 and embedded `6.0.4` / `ATOMCAMV604` strings checked: YES.
- Python syntax and shell syntax checked in the recovered package: YES.
- Hardware field result: user reported all requested functions working after the final ZERO and fullscreen patches.
- One-hour quantified endurance, thermal drift and medical-device validation: NOT RECORDED.
- Exact ESP-IDF firmware source tree: NOT PRESENT in the Pi recovery; see `HANDOFF.md`.

This package is operationally deployable. It must not be represented as a fully source-reproducible firmware release until the original checkpoint04 `firmware/` directory is recovered and rebuilt.
