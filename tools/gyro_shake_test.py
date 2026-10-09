#!/usr/bin/env python3
"""Probe IMU shake test: does shaking the probe saturate its sensors and
leave the attitude off when it is put back where it started?

Run on the Pi with the Endoscope app CLOSED (it needs the serial port):

    python3 tools/gyro_shake_test.py

Follow the prompts: hold still, shake, put the probe back exactly where it
was, hold still. The report shows the peak rotation rate and acceleration
seen while shaking, how many samples hit the sensor limits (the firmware
configures the BMI270 for +/-500 deg/s and +/-4 g), and how far the reported
attitude ended up from where it started.
"""

import importlib.util
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "endoscope", os.path.join(HERE, os.pardir, "endoscope.py"))
endoscope = importlib.util.module_from_spec(spec)
spec.loader.exec_module(endoscope)

import serial  # noqa: E402  (python3-serial, installed by install_pi.sh)

GYRO_LIMIT_DPS = 500.0
ACCEL_LIMIT_G = 4.0
NEAR_LIMIT = 0.97          # within 3 % of full scale counts as clipped
FLAG_CALIBRATED = 1 << 2


def quat_angle_deg(a, b):
    """Total rotation between two unit quaternions, in degrees."""
    dot = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2.0 * math.acos(min(1.0, dot)))


def drain(ser, parser, seconds):
    """Keep reading (and discarding) packets so the serial buffer stays fresh."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        parser.feed(ser.read(512))


def run_phase(ser, parser, seconds, label, sink, get_ready=3):
    # Give the operator time to read the next instruction and get into
    # position before anything is recorded.
    print("", flush=True)
    print("NEXT / 下一步: " + label, flush=True)
    for n in range(get_ready, 0, -1):
        print("   get ready / 准备 ... {}".format(n), flush=True)
        drain(ser, parser, 1.0)
    print("   GO / 开始!", flush=True)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(512)
        if chunk:
            for rec in parser.feed(chunk):
                sink(rec)
        left = int(end - time.monotonic()) + 1
        print("\r   {:2d} s ".format(left), end="", flush=True)
    print("\r          ", flush=True)


def main():
    port = endoscope.find_usb_imu_port(None)
    if not port:
        print("Probe serial port not found. Is the probe plugged in?")
        return 1
    print("Probe IMU port:", port)
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 0.05
    ser.dtr = True
    try:
        ser.open()
    except serial.SerialException as exc:
        print("Cannot open the port ({}). Close the Endoscope app first.".format(exc))
        return 1
    parser = endoscope.UsbImuPacketParser()

    still_before, shake, still_after = [], [], []

    # Wait for the firmware's gyro calibration before measuring anything.
    print("Put the probe on the table and do not touch it ... / 把探头放在桌上，不要碰，等待校准 ...", flush=True)
    deadline = time.monotonic() + 30
    calibrated = False
    while time.monotonic() < deadline and not calibrated:
        for rec in parser.feed(ser.read(512)):
            calibrated = bool(rec["flags"] & FLAG_CALIBRATED)
    if not calibrated:
        print("Gyro calibration did not finish within 30 s "
              "(probe moving, or no data). Continuing anyway.")

    run_phase(ser, parser, 4, "1/3  HOLD STILL (start position) / 保持不动（起始位置）",
              still_before.append)
    run_phase(ser, parser, 10, "2/3  SHAKE / TWIST IT the way that causes drift / "
              "像平时会漂移那样晃动", shake.append)
    run_phase(ser, parser, 6, "3/3  PUT IT BACK EXACTLY WHERE IT STARTED and hold still / "
              "放回原来的位置，保持不动", still_after.append, get_ready=5)
    ser.close()

    if not (still_before and shake and still_after):
        print("Not enough IMU data received ({} / {} / {} packets).".format(
            len(still_before), len(shake), len(still_after)))
        return 1

    peak_gyro = [max(abs(r["gyro_dps"][i]) for r in shake) for i in range(3)]
    peak_acc = [max(abs(r["accel_g"][i]) for r in shake) for i in range(3)]
    gyro_clip = sum(1 for r in shake
                    if max(abs(v) for v in r["gyro_dps"]) >= GYRO_LIMIT_DPS * NEAR_LIMIT)
    acc_clip = sum(1 for r in shake
                   if max(abs(v) for v in r["accel_g"]) >= ACCEL_LIMIT_G * NEAR_LIMIT)
    drift = quat_angle_deg(still_before[-1]["quaternion"], still_after[-1]["quaternion"])

    print("=" * 60)
    print("Packets: still {} / shake {} / still {}".format(
        len(still_before), len(shake), len(still_after)))
    print("Peak rotation rate while shaking (deg/s): X {:.0f}  Y {:.0f}  Z {:.0f}"
          "   (sensor limit {:.0f})".format(*peak_gyro, GYRO_LIMIT_DPS))
    print("Peak acceleration while shaking (g):      X {:.1f}  Y {:.1f}  Z {:.1f}"
          "   (sensor limit {:.0f})".format(*peak_acc, ACCEL_LIMIT_G))
    print("Samples at the GYRO limit:  {} of {} ({:.0f} %)".format(
        gyro_clip, len(shake), 100.0 * gyro_clip / len(shake)))
    print("Samples at the ACCEL limit: {} of {} ({:.0f} %)".format(
        acc_clip, len(shake), 100.0 * acc_clip / len(shake)))
    print("Attitude difference start vs. put-back: {:.1f} deg".format(drift))
    print("=" * 60)
    if gyro_clip:
        print("-> The gyro saturated: rotation faster than 500 deg/s was lost, "
              "which leaves a permanent heading error.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
