# v6.0.4 validation — 2026-09-09

This record describes THIS restored workspace, not the lost earlier build.

## Build: PASS

ESP-IDF v5.1.4 with recursive submodules aligned (DEPENDENCY_LOCK.json).
CMake3.31.10, Ninja1.13.0, Python3.12. Full build 1064/1064 completed and merge
exited 0. Inherited deprecated I2S/I2C API warnings remain, with no compile errors.
Actual full build log: evidence/build604.log. Earlier dependency errors are
preserved in evidence for provenance; they were resolved before this build.

- App size 0xbc640 (771648 bytes), 63% of 2MiB partition free.
- Merged image atoms3r_cam_uvc_imu_v6_0_4.bin: 837184 bytes (0xcc640).
- SHA256 `1661001147a39bcc75665b43ebfd25d3ee7d4f635894d9c7695a9754534daeae`.
- Boot 0x0, partitions 0x8000, app 0x10000; magic values checked.
- Merged application bytes exactly match the generated application bin.
- New USB serial ATOMCAMV604 present; app image metadata separately inspected
  in evidence/application-image-info.txt.
- DIO/80MHz/8MB settings retained from the established firmware build.

## Software: PASS

Evidence: evidence/software-tests.txt. Python syntax and baseline host regressions,
new fullscreen/refused-WM/windowed cases, stationary/fresh ZERO gating, firmware
timestamp rollback, heartbeat/DTR timeout on absent AND garbage serial input.
Native C++17 tests include actual production imu_math.h: startup bias, rejection
of uniform motion and slow tilt, static timing, sample-gap reset, 90-degree
out/back integration and acceleration gating. All root shell scripts pass bash -n.

## Not tested physically

The exact Pi window manager, USB electrical/endpoint timing, repeated close/open,
one-hour simultaneous video/IMU endurance, sensor temperature drift and measured
return-angle accuracy. Static holding may suppress extremely slow pure yaw.
CDC last-resort restart interrupts both USB interfaces and requires a new ZERO.
Build/tests alone do not prove every reported fault is eliminated. Follow field
acceptance in HANDOFF.md before treating this as hardware-qualified.
