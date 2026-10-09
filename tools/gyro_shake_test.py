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

6.0.6: every packet is also run through the app's GyroClipCompensator (the
same code the viewer uses), so one run shows the heading error both without
("firmware") and with ("compensated") the host-side repair.

    python3 tools/gyro_shake_test.py --csv shake.csv   # also keep the packets
"""

import argparse
import csv
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


def wrap_deg(d):
    """Normalise an angle to (-180, 180] degrees."""
    d = (d + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def heading_deg(a, b):
    """Rotation about world vertical from attitude b to attitude a, degrees,
    in (-180, 180]. A quaternion sign flip or a full turn must not show up
    as ~360 degrees of error."""
    return wrap_deg(math.degrees(endoscope._yaw_between(a, b)))


def drain(ser, parser, seconds, feed):
    """Keep reading packets so the serial buffer stays fresh. They are not
    measured, but still go to `feed`: the compensator needs every packet."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for rec in parser.feed(ser.read(512)):
            feed(rec)


def run_phase(ser, parser, seconds, label, sink, feed, get_ready=3):
    # Give the operator time to read the next instruction and get into
    # position before anything is recorded.
    print("", flush=True)
    print("NEXT / 下一步: " + label, flush=True)
    for n in range(get_ready, 0, -1):
        print("   get ready / 准备 ... {}".format(n), flush=True)
        drain(ser, parser, 1.0, feed)
    print("   GO / 开始!", flush=True)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(512)
        if chunk:
            for rec in parser.feed(chunk):
                feed(rec)
                sink(rec)
        left = int(end - time.monotonic()) + 1
        print("\r   {:2d} s ".format(left), end="", flush=True)
    print("\r          ", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Probe IMU shake test")
    ap.add_argument("--csv", metavar="FILE",
                    help="also save every packet (phase, flags, sequence, "
                         "time, accel, gyro, both quaternions) to this file")
    args = ap.parse_args()
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
    # Every packet, in order, goes through the viewer's clip compensator;
    # each record keeps the compensated quaternion it produced.
    comp = endoscope.GyroClipCompensator()
    log = []
    phase = ["calibrate"]

    def feed(rec):
        comp.update(rec)
        rec["compensated"] = comp.correct(rec["quaternion"])
        log.append((phase[0], rec))

    # Wait for the firmware's gyro calibration before measuring anything.
    print("Put the probe on the table and do not touch it ... / 把探头放在桌上，不要碰，等待校准 ...", flush=True)
    deadline = time.monotonic() + 30
    calibrated = False
    while time.monotonic() < deadline and not calibrated:
        for rec in parser.feed(ser.read(512)):
            feed(rec)
            calibrated = bool(rec["flags"] & FLAG_CALIBRATED)
    if not calibrated:
        print("Gyro calibration did not finish within 30 s "
              "(probe moving, or no data). Continuing anyway.")

    phase[0] = "still_before"
    run_phase(ser, parser, 4, "1/3  HOLD STILL (start position) / 保持不动（起始位置）",
              still_before.append, feed)
    # The start pose is the reference, as if ZERO were pressed here -- and
    # the app resets the compensator on ZERO, so do the same.
    comp.reset()
    if still_before:
        still_before[-1]["compensated"] = still_before[-1]["quaternion"]
    phase[0] = "shake"
    run_phase(ser, parser, 10, "2/3  SHAKE / TWIST IT the way that causes drift / "
              "像平时会漂移那样晃动", shake.append, feed)
    phase[0] = "still_after"
    run_phase(ser, parser, 6, "3/3  PUT IT BACK EXACTLY WHERE IT STARTED and hold still / "
              "放回原来的位置，保持不动", still_after.append, feed, get_ready=5)
    ser.close()
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["phase", "flags", "sequence", "timestamp_us",
                        "ax_g", "ay_g", "az_g", "gx_dps", "gy_dps", "gz_dps",
                        "qw", "qx", "qy", "qz", "cw", "cx", "cy", "cz"])
            for name, r in log:
                w.writerow([name, r["flags"], r["sequence"], r["timestamp_us"]]
                           + list(r["accel_g"]) + list(r["gyro_dps"])
                           + list(r["quaternion"]) + list(r["compensated"]))
        print("Packets saved to", args.csv)

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
    start, end = still_before[-1], still_after[-1]
    drift = quat_angle_deg(start["quaternion"], end["quaternion"])
    head_fw = heading_deg(end["quaternion"], start["quaternion"])
    head_comp = heading_deg(end["compensated"], start["compensated"])

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
    print("Attitude difference (firmware):   {:5.1f} deg   (start vs. put-back)".format(drift))
    print("Heading difference (firmware):    {:+5.1f} deg".format(head_fw))
    print("Heading difference (compensated): {:+5.1f} deg".format(head_comp))
    print("Clip compensation: {} run(s) repaired, {:.0f} deg of rotation put back{}".format(
        comp.events, comp.repaired_deg,
        ", UNCERTAIN (the app would ask for a re-ZERO)" if comp.uncertain else ""))
    print("=" * 60)
    if gyro_clip:
        print("-> The gyro saturated: rotation faster than 500 deg/s was lost, "
              "which leaves a permanent heading error.")
        print("-> 6.0.6 compensation: heading error {:.1f} -> {:.1f} deg.".format(
            abs(head_fw), abs(head_comp)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
