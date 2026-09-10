# Reproduce / resume this build

Current build and physical-test status: WORK_STATUS.md and TEST_REPORT.md.
Do not reuse a hash from a lost earlier environment. Only the binary produced by
these actual sources may be named v6.0.4.

## Clean Linux build environment

Install Git, a C/C++ build toolchain, Python3 with venv, CMake, Ninja and the
ESP-IDF v5.1.4 prerequisites. Consult ESP-IDF's official getting-started guide
for the host distribution. Keep the code outside a path containing spaces.

```bash
git clone --branch v5.1.4 --recursive https://github.com/espressif/esp-idf.git esp-idf
cd esp-idf
git submodule update --init --recursive
./install.sh esp32s3
. ./export.sh
cd /path/to/atoms3r-cam-endoscope
bash build_firmware.sh
```

Full recursive checkout matters: even an unused MQTT component's CMake metadata
can require its submodule while dependencies are discovered. Do not bypass the
check or substitute current HEADs for the recorded historical commits.

In this task the tools were installed within the writable workspace using
IDF_TOOLS_PATH, with Python3.12, CMake3.31.10, Ninja1.13.0. The initial shallow
recursive clone fetched some submodules at current HEAD before historical
checkout failed; rerun recursive submodule update to align them. A plus sign
in `git submodule status` means the wrong revision and must be resolved.

Actual paths in this workspace:
- Project: /workspace/scratch/4413601f7d57/resume604/atoms3r-cam-endoscope
- IDF: /workspace/scratch/4413601f7d57/idf604
- Tools: /workspace/scratch/4413601f7d57/idf604-tools

```bash
export IDF_TOOLS_PATH=/workspace/scratch/4413601f7d57/idf604-tools
cd /workspace/scratch/4413601f7d57/idf604
git submodule update --init --recursive
. ./export.sh
cd /workspace/scratch/4413601f7d57/resume604/atoms3r-cam-endoscope
bash build_firmware.sh
```

The workspace initially extracted esp32ulp-elf binaries without executable bits;
restoring their existing executable permissions resolved export's false "tool
not installed" report. Do not patch ESP-IDF validation to bypass missing tools.

## Completion gates

1. Run both Python test files and production C++ math test from HANDOFF.md.
2. Full ESP-IDF build + merge must exit 0, with no missing component revisions.
3. Check flash layout, product version, serial ATOMCAMV604 and partition sizes.
4. Compute actual image hashes and update SHA256SUMS.txt, WORK_STATUS.md and
   TEST_REPORT.md; do not retain a checkpoint's build-PENDING state as release.
5. Archive sources, tests, dependency lock, scripts, docs and new binary. Exclude
   downloaded dependencies/build caches; preserve upstream notices. Validate ZIP.
6. Save the archive before reporting success. Then perform physical acceptance
   with the user's Pi/probe; build PASS does not imply hardware PASS.
