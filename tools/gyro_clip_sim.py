#!/usr/bin/env python3
"""Offline simulator for the 6.0.6 gyro clip compensation.

Generates ground-truth hand motions (wrist shakes, flick-and-return, fast
twists; peaks 600-1500 deg/s on mixed axes) that END EXACTLY WHERE THEY
STARTED, turns them into what the probe's BMI270 would report at 100 Hz with
its +/-500 deg/s and +/-4 g ranges, runs a Python port of the probe firmware
(firmware/main/service/imu_math.h + service_usb_imu.cpp: gyro calibration,
StillDetector, slow bias learning, Mahony Kp=2 Ki=0 with the 0.85-1.15 g
accel gate, fusion frozen while stationary) to get the packets the host sees,
and feeds those packets to endoscope.GyroClipCompensator.

Reported per trial: the heading error after putting the probe back, i.e. the
rotation about world vertical between "start" and "end" that the output
claims although the true motion has none. "firmware" is the uncorrected
packet quaternion, "compensated" is GyroClipCompensator.correct() of it.

    py -3 tools/gyro_clip_sim.py              # full table (a few minutes)
    py -3 tools/gyro_clip_sim.py --trials 30  # quicker

No hardware, no display, no config file. test_gyro_comp.py runs a small fixed
-seed subset of this as a regression bound.
"""
import argparse
import importlib.util
import math
import os
import struct
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
G0 = 9.80665
D2R = math.pi / 180.0
LSB_DPS = 500.0 / 32768.0          # BMI270 at +/-500 deg/s: 65.536 LSB per deg/s


def load_endoscope():
    """Import ../endoscope.py without a display, camera or serial device."""
    if "fcntl" not in sys.modules:
        try:
            import fcntl  # noqa: F401  (Linux only)
        except ImportError:
            fake = types.ModuleType("fcntl")
            fake.LOCK_EX, fake.LOCK_NB, fake.LOCK_UN = 2, 4, 8
            fake.flock = lambda *a, **k: None
            sys.modules["fcntl"] = fake
    if "serial" not in sys.modules:
        try:
            import serial  # noqa: F401
        except ImportError:
            fake = types.ModuleType("serial")
            fake.Serial = object
            sys.modules["serial"] = fake
    spec = importlib.util.spec_from_file_location(
        "endoscope_gyro_sim", os.path.join(HERE, os.pardir, "endoscope.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


# ------------------------------------------------ firmware port (imu_math.h)

class FwCalibration:
    """imu_math::GyroCalibration (300 still samples, spread <= 0.6 deg/s)."""

    def __init__(self):
        self.count = 0
        self.ready = False
        self.sum = [0.0, 0.0, 0.0]
        self.anchor = [0.0, 0.0, 0.0]
        self.lo = [0.0, 0.0, 0.0]
        self.hi = [0.0, 0.0, 0.0]
        self.bias = [0.0, 0.0, 0.0]
        self.calibration_bias = [0.0, 0.0, 0.0]

    def reset_window(self):
        self.count = 0
        self.sum = [0.0, 0.0, 0.0]

    def add(self, g, a):
        if self.ready:
            return True
        an = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
        gn = math.sqrt(g[0] ** 2 + g[1] ** 2 + g[2] ** 2)
        da = math.sqrt(sum((a[i] - self.anchor[i]) ** 2 for i in range(3)))
        if gn > 1.0 or abs(an - 1.0) > 0.06 or (self.count and da > 0.025):
            self.reset_window()
            return False
        if self.count == 0:
            self.anchor = list(a)
            self.lo = list(g)
            self.hi = list(g)
        else:
            self.lo = [min(self.lo[i], g[i]) for i in range(3)]
            self.hi = [max(self.hi[i], g[i]) for i in range(3)]
        self.sum = [self.sum[i] + g[i] for i in range(3)]
        self.count += 1
        if self.count < 300:
            return False
        if max(self.hi[i] - self.lo[i] for i in range(3)) <= 0.6:
            self.bias = [s / self.count for s in self.sum]
            self.calibration_bias = list(self.bias)
            self.ready = True
        self.reset_window()
        return self.ready


class FwMahony:
    """imu_math::MahonyFusion: 6-axis, Kp = 2, Ki = 0, |a| gate 0.85-1.15 g."""
    KP = 2.0
    KI = 0.0

    def __init__(self):
        self.q = [1.0, 0.0, 0.0, 0.0]
        self.integral = [0.0, 0.0, 0.0]

    def seed_from_gravity(self, a):
        n = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
        if n < 0.1:
            return
        ax, ay, az = a[0] / n, a[1] / n, a[2] / n
        roll = math.atan2(ay, az)
        pitch = math.atan2(-ax, math.sqrt(ay * ay + az * az))
        cr, sr = math.cos(roll / 2), math.sin(roll / 2)
        cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
        self.q = [cr * cp, sr * cp, cr * sp, -sr * sp]
        self.integral = [0.0, 0.0, 0.0]
        self._normalise()

    def update(self, a, g, dt):
        if not 0.0 < dt <= 0.1:
            return
        gx, gy, gz = g[0] * D2R, g[1] * D2R, g[2] * D2R
        an = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
        q = self.q
        if 0.85 <= an <= 1.15:
            ax, ay, az = a[0] / an, a[1] / an, a[2] / an
            vx = 2.0 * (q[1] * q[3] - q[0] * q[2])
            vy = 2.0 * (q[0] * q[1] + q[2] * q[3])
            vz = q[0] * q[0] - q[1] * q[1] - q[2] * q[2] + q[3] * q[3]
            ex, ey, ez = ay * vz - az * vy, az * vx - ax * vz, ax * vy - ay * vx
            self.integral = [self.integral[0] + self.KI * ex * dt,
                             self.integral[1] + self.KI * ey * dt,
                             self.integral[2] + self.KI * ez * dt]
            gx += self.KP * ex + self.integral[0]
            gy += self.KP * ey + self.integral[1]
            gz += self.KP * ez + self.integral[2]
        h = 0.5 * dt
        qw, qx, qy, qz = q
        self.q = [qw + (-qx * gx - qy * gy - qz * gz) * h,
                  qx + (qw * gx + qy * gz - qz * gy) * h,
                  qy + (qw * gy - qx * gz + qz * gx) * h,
                  qz + (qw * gz + qx * gy - qy * gx) * h]
        self._normalise()

    def _normalise(self):
        n = math.sqrt(sum(v * v for v in self.q))
        self.q = [1.0, 0.0, 0.0, 0.0] if n < 0.1 else [v / n for v in self.q]


class FwStillDetector:
    """imu_math::StillDetector: 2 s under 0.18 deg/s and 0.03 g, anchored."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.since = -1
        self.previous = -1
        self.anchor = [0.0, 0.0, 0.0]

    def update(self, a, g, now_us):
        an = math.sqrt(a[0] ** 2 + a[1] ** 2 + a[2] ** 2)
        gn = math.sqrt(g[0] ** 2 + g[1] ** 2 + g[2] ** 2)
        if gn >= 0.18 or abs(an - 1.0) >= 0.03:
            self.reset()
            return False
        if self.previous >= 0 and (now_us <= self.previous
                                   or now_us - self.previous > 100000):
            self.reset()
        self.previous = now_us
        if self.since < 0:
            self.since = now_us
            self.anchor = list(a)
        if math.sqrt(sum((a[i] - self.anchor[i]) ** 2 for i in range(3))) > 0.012:
            self.reset()
            return False
        return now_us - self.since >= 2000000


class Firmware:
    """service_usb_imu.cpp usb_imu_task, one call per 100 Hz tick; returns the
    record exactly as endoscope.UsbImuPacketParser would decode it."""

    def __init__(self):
        self.calibration = FwCalibration()
        self.fusion = FwMahony()
        self.still = FwStillDetector()
        self.seeded = False
        self.last_us = 0
        self.sequence = 0

    def tick(self, now_us, accel, raw_gyro):
        flags = 1                                   # kFlagImuValid
        gyro = list(raw_gyro)
        cal = self.calibration
        if not cal.ready:
            if cal.add(raw_gyro, accel) and not self.seeded:
                self.fusion.seed_from_gravity(accel)
                self.seeded = True
                self.last_us = now_us
        if cal.ready:
            flags |= 4                              # kFlagCalibrated
            bias = list(cal.bias)
            gyro = [raw_gyro[i] - bias[i] for i in range(3)]
            stationary = self.still.update(accel, gyro, now_us)
            if stationary:
                flags |= 8                          # kFlagStationary
                bias = [bias[i] + 0.0002 * gyro[i] for i in range(3)]
                base = cal.calibration_bias
                bias = [min(base[i] + 0.5, max(base[i] - 0.5, bias[i]))
                        for i in range(3)]
                cal.bias = bias
            dt = (now_us - self.last_us) / 1e6 if self.last_us > 0 else 0.01
            if not 0.0 < dt <= 0.1:
                dt = 0.01
            if not stationary:
                self.fusion.update(accel, gyro, dt)
            self.last_us = now_us
        q = [f32(v) for v in self.fusion.q]
        n = math.sqrt(sum(v * v for v in q))
        record = {"flags": flags, "sequence": self.sequence,
                  "timestamp_us": now_us,
                  "accel_g": tuple(f32(v) for v in accel),
                  "gyro_dps": tuple(f32(v) for v in gyro),
                  "mag_ut": (0.0, 0.0, 0.0),
                  "quaternion": tuple(v / n for v in q)}
        self.sequence += 1
        return record


# ----------------------------------------------------- ground truth (numpy)

def qn_mul(a, b):
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack([aw * bw - ax * bx - ay * by - az * bz,
                     aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw], -1)


def qn_conj(q):
    return q * np.array([1.0, -1.0, -1.0, -1.0])


def qn_exp(rv):
    ang = np.linalg.norm(rv, axis=-1, keepdims=True)
    s = np.where(ang > 1e-12, np.sin(ang / 2) / np.maximum(ang, 1e-12), 0.5)
    return np.concatenate([np.cos(ang / 2), rv * s], -1)


def qn_log(q):
    q = np.where(q[..., :1] < 0, -q, q)
    v = q[..., 1:]
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    ang = 2 * np.arctan2(n, q[..., :1])
    return np.where(n > 1e-12, v / np.maximum(n, 1e-12) * ang, 2 * v)


def qn_rot(q, v):
    vq = np.concatenate([np.zeros(v.shape[:-1] + (1,)), v], -1)
    return qn_mul(qn_mul(q, vq), qn_conj(q))[..., 1:]


def unit(rng):
    v = rng.normal(size=3)
    return v / np.linalg.norm(v)


def wrist_axis(rng):
    """Mostly about the probe's X/Y (shaking the shaft), sometimes any axis."""
    u = unit(rng)
    if rng.random() < 0.6:
        u[2] *= 0.3
        u /= np.linalg.norm(u)
    return u


def envelope(t, T, ramp=0.25):
    env = np.ones_like(t)
    m = t < ramp
    env[m] = np.sin(0.5 * math.pi * t[m] / ramp) ** 2
    m = t > T - ramp
    env[m] = np.sin(0.5 * math.pi * (T - t[m]) / ramp) ** 2
    return env


def motion_shake(rng, fs):
    """Oscillating wrist shake: 1-3 mixed-axis components, 2.5-6 Hz, with a
    2nd harmonic so the swings are not symmetric."""
    T = rng.uniform(2.0, 7.0)
    t = np.arange(int(T * fs) + 1) / fs
    env = envelope(t, T)
    phi = np.zeros((len(t), 3))
    for _ in range(rng.integers(1, 4)):
        f = rng.uniform(2.5, 6.0)
        w = (np.sin(2 * math.pi * f * t + rng.uniform(0, 2 * math.pi))
             + rng.uniform(0.0, 0.6) * np.sin(4 * math.pi * f * t
                                              + rng.uniform(0, 2 * math.pi)))
        phi += np.outer(env * w * rng.uniform(0.5, 1.0), wrist_axis(rng))
    return t, phi


def _pulse_angle(t, t0, dur, angle):
    """Angle profile of a raised-cosine rate pulse: 0 -> angle over dur."""
    u = np.clip((t - t0) / dur, 0.0, 1.0)
    return angle * (u - np.sin(2 * math.pi * u) / (2 * math.pi))


def motion_flicks(rng, fs, twist=False):
    """Flick-and-return: a fast turn (clips) and a slower way back (mostly
    does not) -- the asymmetric case that leaves a heading error. `twist`
    makes them large turns about one axis (pronation/supination)."""
    n = rng.integers(2, 7)
    t_cur = 0.3
    events = []
    for _ in range(n):
        axis = unit(rng) if twist else wrist_axis(rng)
        ang = math.radians(rng.uniform(60, 150) if twist else rng.uniform(20, 70))
        ang *= 1 if rng.random() < 0.5 else -1
        out_dur = rng.uniform(0.06, 0.20)
        back_dur = out_dur * rng.uniform(1.0, 3.0)
        hold = rng.uniform(0.05, 0.4)
        events.append((axis, ang, t_cur, out_dur, t_cur + out_dur + hold, back_dur))
        t_cur += out_dur + hold + back_dur + rng.uniform(0.1, 0.5)
    T = t_cur + 0.3
    t = np.arange(int(T * fs) + 1) / fs
    phi = np.zeros((len(t), 3))
    for axis, ang, t0, d0, t1, d1 in events:
        theta = _pulse_angle(t, t0, d0, ang) - _pulse_angle(t, t1, d1, ang)
        phi += np.outer(theta, axis)
    return t, phi


def make_motion(rng, kind, peak_dps, q_start, fs):
    gen = {"shake": motion_shake,
           "flick": motion_flicks,
           "twist": lambda r, f: motion_flicks(r, f, twist=True)}[kind]
    t, phi = gen(rng, fs)
    T = t[-1]
    # Slow wander: the operator moves the probe around and brings it back.
    wa = unit(rng) * math.radians(rng.uniform(0, 60))
    phi = phi + np.outer(np.sin(math.pi * t / T) ** 2
                         * np.sin(2 * math.pi * rng.uniform(0.15, 0.5) * t), wa)
    # Physiological tremor, a few deg/s at 8-12 Hz.
    f = rng.uniform(8, 12)
    phi = phi + np.outer(envelope(t, T) * math.radians(rng.uniform(0.05, 0.3))
                         * np.sin(2 * math.pi * f * t), unit(rng))
    # Scale the motion so its fastest body-axis rate is the requested peak.
    for _ in range(3):
        q = qn_mul(np.broadcast_to(q_start, (len(t), 4)), qn_exp(phi))
        pk = np.degrees(np.abs(qn_log(qn_mul(qn_conj(q[:-1]), q[1:])) * fs)).max()
        phi = phi * (peak_dps / pk)
    return qn_mul(np.broadcast_to(q_start, (len(t), 4)), qn_exp(phi))


def random_start(rng, max_tilt_deg=60.0):
    yaw = rng.uniform(-math.pi, math.pi)
    a = rng.uniform(0, 2 * math.pi)
    tilt = np.array([math.cos(a), math.sin(a), 0.0]) * math.radians(
        rng.uniform(0, max_tilt_deg))
    return qn_mul(qn_exp(tilt), np.array([math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)]))


def _lowpass(x, fs, fc):
    """2nd-order Butterworth (bilinear), applied along axis 0."""
    k = math.tan(math.pi * fc / fs)
    norm = 1 / (1 + math.sqrt(2) * k + k * k)
    b0 = k * k * norm
    b1, b2 = 2 * b0, b0
    a1 = 2 * (k * k - 1) * norm
    a2 = (1 - math.sqrt(2) * k + k * k) * norm
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = x[0]            # start settled on the first value
    for i in range(len(x)):
        xi = x[i]
        yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, xi, y1, yi
        y[i] = yi
    return y


SENSORS = ("post", "lpf", "pre")


def heading_error_deg(q0_true, q1_true, q0_out, q1_out):
    """Rotation about world vertical between the output's start->end change
    and the true start->end change, degrees in (-180, 180]."""
    et = qn_mul(np.asarray(q1_true), qn_conj(np.asarray(q0_true)))
    eo = qn_mul(np.asarray(q1_out), qn_conj(np.asarray(q0_out)))
    e = qn_mul(eo, qn_conj(et))
    d = math.degrees(2 * math.atan2(e[3], e[0]))
    return (d + 180.0) % 360.0 - 180.0


def ground_truth(q_start, motion, fs=1000.0, rest_before=4.6, rest_after=4.5):
    """Still at q_start (the firmware calibrates and settles), the motion, then
    still at q_start again. Returns (1 kHz attitude path, index of the ZERO
    moment just before the motion)."""
    n_pre, n_post = int(rest_before * fs), int(rest_after * fs)
    q_true = np.concatenate([np.broadcast_to(q_start, (n_pre, 4)), motion[1:],
                             np.broadcast_to(q_start, (n_post, 4))])
    return q_true, n_pre - n_pre % int(fs / 100)


def probe_packets(q_true, rng, sensor="post", fs=1000.0, lever=None):
    """Yield (k, record, ideal_gyro) at 100 Hz for a 1 kHz attitude path: the
    BMI270 readings (range-clipped, quantised, noisy) run through the firmware
    port. ideal_gyro is the same reading without clipping or noise, minus the
    firmware's calibration bias -- the rate the compensator should recover."""
    n = len(q_true)
    rate_dps = np.degrees(np.vstack([np.zeros((1, 3)), qn_log(qn_mul(
        qn_conj(q_true[:-1]), q_true[1:])) * fs]))
    # Accelerometer: the sensor sits 5-25 cm from the wrist pivot, so a shake
    # adds several g of centripetal/tangential acceleration (and clips at 4 g).
    r = unit(rng) * rng.uniform(0.05, 0.25) if lever is None else np.asarray(lever)
    pos = qn_rot(q_true, np.broadcast_to(r, (n, 3)))
    acc = np.zeros_like(pos)
    acc[1:-1] = (pos[2:] - 2 * pos[1:-1] + pos[:-2]) * fs * fs
    f_body = qn_rot(qn_conj(q_true), acc / G0 + np.array([0.0, 0.0, 1.0]))
    bias = rng.uniform(-0.4, 0.4, 3)
    raw = ideal = rate_dps + bias
    hi_lim = 32767 * LSB_DPS
    if sensor == "pre":          # saturates before the sensor's own filter
        raw = np.clip(raw, -500.0, hi_lim)
    if sensor == "lpf":          # 40 Hz low-pass, then sampled, then clipped
        raw = ideal = _lowpass(raw, fs, 40.0)
        f_body = _lowpass(f_body, fs, 40.0)
    step = int(fs / 100)
    fw = Firmware()
    for k in range(step, n, step):
        if sensor == "lpf":
            g, a, g_ideal = raw[k].copy(), f_body[k].copy(), ideal[k]
        else:                    # 10 ms average, then clipped
            g = raw[k - step + 1:k + 1].mean(0)
            a = f_body[k - step + 1:k + 1].mean(0)
            g_ideal = ideal[k - step + 1:k + 1].mean(0)
        g = g + rng.normal(0, 0.05, 3)
        g = np.clip(np.round(g / LSB_DPS) * LSB_DPS, -500.0, hi_lim)
        a = np.clip(a + rng.normal(0, 0.002, 3), -4.0, 4.0)
        rec = fw.tick(int(k * 1e6 / fs), [float(v) for v in a],
                      [float(v) for v in g])
        yield k, rec, tuple(float(g_ideal[i] - fw.calibration.bias[i])
                            for i in range(3))


def play(q_true, zero_k, rng, comp_factories, sensor="post", fs=1000.0,
         trace=None, lever=None):
    """Run a ground-truth path through the probe and the compensators. The
    operator ZEROes at zero_k; the result compares the end with that."""
    comps = [make() for make in comp_factories]
    zero = None
    clipped = 0
    for k, rec, ideal in probe_packets(q_true, rng, sensor, fs, lever):
        clipped += any(abs(v) >= 495.0 for v in rec["gyro_dps"])
        if trace is not None:
            trace.append((rec, ideal))
        for c in comps:
            c.update(rec, 1)
        if k == zero_k:
            zero = (q_true[k].copy(), rec["quaternion"],
                    [c.correct(rec["quaternion"]) for c in comps])
    q_fw = rec["quaternion"]
    return {"fw": heading_error_deg(zero[0], q_true[k], zero[1], q_fw),
            "comp": [heading_error_deg(zero[0], q_true[k], zero[2][i],
                                       c.correct(q_fw))
                     for i, c in enumerate(comps)],
            "identical": [c.correct(q_fw) is q_fw for c in comps],
            "uncertain": [c.uncertain_events for c in comps],
            "comps": comps, "q_true_end": q_true[k], "q_fw_end": q_fw,
            "zero": zero, "clipped": int(clipped)}


def run_trial(rng, comp_factories, kind="shake", peak_dps=1000.0,
              sensor="post", fs=1000.0, trace=None):
    """One randomised session: random start attitude, one motion of `kind`
    whose fastest body-axis rate is peak_dps, back to the start."""
    q_start = random_start(rng)
    motion = make_motion(rng, kind, peak_dps, q_start, fs)
    q_true, zero_k = ground_truth(q_start, motion, fs)
    return play(q_true, zero_k, rng, comp_factories, sensor, fs, trace)


def run_set(comp_factories, trials, seed, kinds=("shake", "flick", "twist"),
            peaks=(600.0, 1500.0), sensors=SENSORS):
    """Randomised trials; returns a list of (kind, sensor, peak, result)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(trials):
        kind = kinds[i % len(kinds)]
        sensor = sensors[(i // len(kinds)) % len(sensors)]
        peak = rng.uniform(*peaks)
        out.append((kind, sensor, peak, run_trial(rng, comp_factories, kind,
                                                  peak, sensor)))
    return out


def summarise(rows, idx=0):
    fw = np.array([abs(r["fw"]) for _, _, _, r in rows])
    cp = np.array([abs(r["comp"][idx]) for _, _, _, r in rows])
    worse = int(np.sum(cp > fw + 1.0))
    unc = int(sum(1 for _, _, _, r in rows if r["uncertain"][idx]))
    return {"n": len(rows), "fw_med": float(np.median(fw)),
            "fw_p95": float(np.percentile(fw, 95)),
            "comp_med": float(np.median(cp)),
            "comp_p95": float(np.percentile(cp, 95)),
            "worse": worse, "uncertain": unc}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trials", type=int, default=90,
                    help="trials per peak band (default 90)")
    ap.add_argument("--seed", type=int, default=606)
    args = ap.parse_args()
    endoscope = load_endoscope()
    make = endoscope.GyroClipCompensator

    def line(label, s):
        print("{:<26} {:>4}  {:>7.2f} {:>7.2f}   {:>7.2f} {:>7.2f}   {:>5} {:>5}".format(
            label, s["n"], s["fw_med"], s["fw_p95"], s["comp_med"], s["comp_p95"],
            s["worse"], s["uncertain"]))

    print("heading error after returning to the start pose, degrees")
    print("{:<26} {:>4}  {:>15}   {:>15}   {:>5} {:>5}".format(
        "", "n", "firmware", "compensated", "worse", "unc."))
    print("{:<26} {:>4}  {:>7} {:>7}   {:>7} {:>7}".format(
        "", "", "median", "p95", "median", "p95"))
    every = []
    for lo, hi in ((600, 900), (900, 1200), (1200, 1500)):
        rows = run_set([make], args.trials, args.seed + lo, peaks=(lo, hi))
        every += rows
        line("peak {}-{} deg/s".format(lo, hi), summarise(rows))
    for kind in ("shake", "flick", "twist"):
        line("  all peaks, " + kind, summarise([r for r in every if r[0] == kind]))
    for sensor in SENSORS:
        line("  all peaks, sensor " + sensor,
             summarise([r for r in every if r[1] == sensor]))
    line("ALL", summarise(every))
    # Below the range: nothing clips, so the output must be the firmware's.
    calm = run_set([make], max(9, args.trials // 3), args.seed, peaks=(150, 480))
    same = sum(1 for _, _, _, r in calm if r["identical"][0])
    clip_free = sum(1 for _, _, _, r in calm if r["clipped"] == 0)
    print("no-clip trials (peak 150-480): {} of {} bit-identical to firmware "
          "({} had no clipped sample)".format(same, len(calm), clip_free))
    print("worse = compensated error more than 1 deg above firmware; "
          "unc. = trials where the compensator raised 'uncertain'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
