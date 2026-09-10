# AtomS3R-CAM USB IMU protocol v1

The firmware exposes a standard CDC ACM interface beside UVC and transmits one
fixed little-endian record at 100 Hz. The protocol is telemetry-only: the host
must not send commands. Records never queue up: since v6.0.3 the sampling task
hands each record to the USB task through a one-slot mailbox where the newest
record replaces an unsent older one, so displayed orientation stays recent.

## Binary record (76 bytes)

| Offset | Size | Type | Field | Meaning |
|---:|---:|---|---|---|
| 0 | 4 | bytes | magic | ASCII `IMU6` |
| 4 | 1 | uint8 | version | `1` |
| 5 | 1 | uint8 | flags | Status bits below |
| 6 | 2 | uint16 | packet_size | Always `76` |
| 8 | 4 | uint32 | sequence | Increments every sample, including dropped USB writes |
| 12 | 8 | uint64 | timestamp_us | ESP monotonic microseconds since boot |
| 20 | 12 | float32[3] | accel_g | Native BMI270 X/Y/Z, units g |
| 32 | 12 | float32[3] | gyro_dps | Bias-corrected X/Y/Z in deg/s after calibration; raw before it |
| 44 | 12 | float32[3] | mag_ut | Native BMM150 X/Y/Z in µT, zero if unavailable |
| 56 | 16 | float32[4] | quaternion_wxyz | Normalized sensor-body-to-world attitude `(w,x,y,z)` |
| 72 | 4 | uint32 | crc32 | IEEE CRC-32 of bytes 0–71, compatible with Python `zlib.crc32` |

Flag bits:

| Bit | Mask | Meaning |
|---:|---:|---|
| 0 | `0x01` | BMI270 sample valid |
| 1 | `0x02` | BMM150 sample valid |
| 2 | `0x04` | Three-second stationary gyro calibration completed |
| 3 | `0x08` | Probe has been stationary for at least 0.5 s |
| 4 | `0x10` | Sensor fault: BMI270 reads have failed for ≥ 0.1 s (v6.0.3+) |
| 5 | `0x20` | Sensor recovered: bus + BMI270 re-init just succeeded; sent for ≈ 1 s (v6.0.3+) |

Bits 4–5 were reserved (always zero) before v6.0.3, so the record layout and
older hosts are unaffected. Hosts should treat bit 4 as "link healthy, sensor
faulty" — distinct from silence — and must discard any captured zero reference
when bit 5 first appears, because a recovery re-seeds the firmware fusion and
restarts yaw at zero.

## Coordinate contract

Accelerometer, gyro, and quaternion all use the same unmodified BMI270 XYZ
frame. This is deliberate: the factory web-demo helper exchanges accelerometer
X/Y without exchanging gyro X/Y, which cannot be used for correct fusion.

The application discovers which signed body axis points through the lens with
a required “tilt lens up” gesture. Video mirror/vertical-flip settings transform
pixels only and never alter this physical coordinate frame.
