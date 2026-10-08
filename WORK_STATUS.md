# v6.0.5 — UV mode on the field-approved 6.0.4

- UV fluorescence mode cherry-picked onto field-approved v6.0.4 (`38500b5`) on branch `uv-mode`; UV-off path is byte-identical to 6.0.4.
- Gate suites (`test_uv_core`, `test_uv_ui`, `test_uv_presets`, `test_endoscope_core`, `test_v604`) all PASS; see `TEST_REPORT.md`.
- Codex `gpt-6-astra` review of `38500b5..uv-mode`: no P1; round-4 fixes applied (tracker reset on orientation change, `save_cfg` robust to non-finite values, stray bytecode untracked).
- Not verified on hardware: see `TEST_REPORT.md`.

# v6.0.4 field-recovered operational release

- Final Raspberry Pi host source recovered from the user's working device: YES.
- Correct merged v6.0.4 firmware image recovered and placed under `release/`: YES.
- Firmware SHA-256 and embedded `6.0.4` / `ATOMCAMV604` strings checked: YES.
- Python syntax and shell syntax checked in the recovered package: YES.
- Hardware field result: user reported all requested functions working after the final ZERO and fullscreen patches.
- One-hour quantified endurance, thermal drift and medical-device validation: NOT RECORDED.
- Exact ESP-IDF firmware source tree: NOT PRESENT in the Pi recovery; see `HANDOFF.md`.

This package is operationally deployable. It must not be represented as a fully source-reproducible firmware release until the original checkpoint04 `firmware/` directory is recovered and rebuilt.
