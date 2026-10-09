#!/usr/bin/env python3
"""Tests for the 6.0.6 gyro clip compensation (endoscope.GyroClipCompensator
and its wiring into UsbCompositeProbeLink / App), driven by the simulator in
tools/gyro_clip_sim.py (a Python port of the probe firmware's fusion).

Run: py -3 test_gyro_comp.py   (hardware-free; no display/camera/serial
needed. Like test_uv_core.py it uses the real numpy; fcntl and pyserial are
stubbed when absent so it also runs on Windows.)

Groups: IDENTITY (nothing clipped -> the firmware quaternion object itself),
SINGLE AXIS / MULTI AXIS (a known lost rotation is recovered, and moves the
aim indicator the right way), EDGES (endless clip, packet gaps), RESET (ZERO
and link generation), CONFIG (off = the 6.0.5 path; read without setdefault),
STATUS (CLIP+n° / uncertain toast) and SIMULATOR (fixed-seed statistics as a
regression bound).
"""
import importlib.util
import math
import sys
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("gyro_clip_sim",
                                               HERE / "tools" / "gyro_clip_sim.py")
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)
E = sim.load_endoscope()

VALID = E.USB_IMU_FLAG_VALID | E.USB_IMU_FLAG_CALIBRATED


def yaw_quat(deg):
    a = math.radians(deg) / 2
    return np.array([math.cos(a), 0.0, 0.0, math.sin(a)])


def flick_path(q_start, axis_world, out_deg, out_s, back_s, fs=1000.0):
    """1 kHz attitude path: still, a raised-cosine flick of out_deg about a
    WORLD axis in out_s, a pause, the same angle back in back_s, still."""
    t = np.arange(int((0.3 + out_s + 0.3 + back_s + 0.3) * fs) + 1) / fs
    ang = (sim._pulse_angle(t, 0.3, out_s, math.radians(out_deg))
           - sim._pulse_angle(t, 0.6 + out_s, back_s, math.radians(out_deg)))
    rot = sim.qn_exp(np.outer(ang, np.asarray(axis_world, float)))
    motion = sim.qn_mul(rot, np.broadcast_to(q_start, (len(t), 4)))
    return sim.ground_truth(q_start, motion, fs)


class Recorder(E.GyroClipCompensator):
    """Keeps every bridged stretch: (axis, [(t, measured, estimate)])."""

    def __init__(self):
        super().__init__()
        self.bridged = []

    def _solve(self, ax, start, end):
        super()._solve(ax, start, end)
        self.bridged.append((ax, [(self._buf[i][0], self._buf[i][1][ax],
                                   self._buf[i][5][ax])
                                  for i in range(start, end + 1)]))


def record(seq, t_us, gyro, q=(1.0, 0.0, 0.0, 0.0), flags=VALID):
    return {"flags": flags, "sequence": seq, "timestamp_us": t_us,
            "accel_g": (0.0, 0.0, 1.0), "gyro_dps": tuple(gyro),
            "mag_ut": (0.0, 0.0, 0.0), "quaternion": tuple(q)}


class Probe:
    """Minimal stand-in for the firmware: integrates the (clipped) packet
    gyro with the firmware's own step, no gravity term, 100 Hz."""

    def __init__(self, comp):
        self.comp, self.seq, self.q = comp, 0, (1.0, 0.0, 0.0, 0.0)

    def send(self, gyro, skip=0):
        self.seq += skip
        self.q = E._fw_gyro_step(self.q, gyro, 0.01 * (1 + skip))
        self.comp.update(record(self.seq, self.seq * 10000, gyro, self.q), 1)
        self.seq += 1


def az_deg(q_now, q_ref):
    return math.degrees(E.aim_angles(tuple(q_now), tuple(q_ref), 0)[0])


# ----------------------------------------------------------------- IDENTITY
def test_identity():
    rng = np.random.default_rng(11)
    for kind in ("shake", "flick", "twist"):
        trace = []
        res = sim.run_trial(rng, [E.GyroClipCompensator], kind, 420.0, "post",
                            trace=trace)
        assert res["clipped"] == 0, res["clipped"]
        comp = E.GyroClipCompensator()
        for rec, _ in trace:
            comp.update(rec, 1)
            q = rec["quaternion"]
            assert comp.correct(q) is q            # the same object, bit for bit
        assert not comp.active and comp.offset_deg == 0.0 and comp.events == 0
        assert res["identical"] == [True] and res["comp"][0] == res["fw"]
    print("PASS: IDENTITY -- calm shake/flick/twist (no sample within 1 % of "
          "500 deg/s): correct(q) returns the firmware quaternion object itself")


# ---------------------------------------------------- SINGLE / MULTI AXIS
def _flick_case(q_start, axis_world, label):
    rng = np.random.default_rng(5)
    # 60 deg in 120 ms (peak ~1000 deg/s about the axis), back in 600 ms.
    q_true, zero_k = flick_path(q_start, axis_world, 60.0, 0.12, 0.6)
    trace = []
    res = sim.play(q_true, zero_k, rng, [Recorder], "post", trace=trace,
                   lever=(0.0, 0.0, 0.0))
    comp = res["comps"][0]
    ideal = {rec["timestamp_us"]: g for rec, g in trace}
    axes = sorted({ax for ax, _ in comp.bridged})
    lost = put_back = 0.0
    for ax, rows in comp.bridged:
        for t, meas, est in rows:
            true = ideal[int(round(t * 1e6))][ax]
            lost += (true - meas) * 0.01
            put_back += (est - meas) * 0.01
    assert abs(res["fw"]) > 8.0, res["fw"]                 # the firmware lost it
    assert abs(res["comp"][0]) < 0.12 * abs(res["fw"]), res
    assert abs(put_back - lost) < 0.12 * abs(lost), (put_back, lost)
    # Direction check through the app's own aim maths (lens +X, level):
    # compensated azimuth is far closer to the truth (0, back at start).
    zero_true, zero_fw, zero_out = res["zero"]
    az_fw = az_deg(res["q_fw_end"], zero_fw)
    az_out = az_deg(comp.correct(res["q_fw_end"]), zero_out[0])
    assert abs(az_out) < 0.15 * abs(az_fw), (az_out, az_fw)
    print("PASS: {} -- clipped axes {}, firmware heading error {:+.1f} deg, "
          "compensated {:+.2f} deg; rotation lost {:.1f} deg, put back {:.1f}; "
          "aim azimuth {:+.1f} -> {:+.2f} deg".format(
              label, "".join("XYZ"[a] for a in axes), res["fw"], res["comp"][0],
              lost, put_back, az_fw, az_out))
    return axes


def test_single_axis():
    # Level probe, flick about world vertical = body Z only.
    axes = _flick_case(np.array([1.0, 0, 0, 0]), (0, 0, 1), "SINGLE AXIS")
    assert axes == [2], axes


def test_multi_axis():
    # Body (1,1,0)/sqrt2 held vertical: the same heading flick now splits
    # into ~700 deg/s on BOTH body X and Y -- both clip.
    b = np.array([1.0, 1.0, 0.0]) / math.sqrt(2)
    axis = np.cross(b, [0, 0, 1.0])
    axis /= np.linalg.norm(axis)
    q_start = sim.qn_exp(axis * math.acos(b[2]))
    axes = _flick_case(q_start, (0, 0, 1), "MULTI AXIS")
    assert axes == [0, 1], axes


# -------------------------------------------------------------------- EDGES
def test_edges():
    # A 0.5 s spin above range on Z: too long to bridge -> not reconstructed,
    # flagged uncertain, output untouched.
    comp = E.GyroClipCompensator()
    probe = Probe(comp)
    for v in [100 + 20 * i for i in range(20)] + [499.9] * 50 + [300 - 10 * i for i in range(20)]:
        probe.send((0, 0, v))
    assert comp.uncertain and comp.uncertain_events == 1
    assert not comp.active and comp.correct((1, 0, 0, 0)) == (1, 0, 0, 0)
    # A short clipped hump (true peak ~640 deg/s) bridged normally, then the
    # same with packets lost mid-clip: no context -> uncertain, nothing invented.
    true = [640 * math.sin(math.pi * i / 12) for i in range(13)]
    def hump(comp, gap):
        probe = Probe(comp)
        for i, v in enumerate([0] * 10 + true + [0] * 12):
            probe.send((0, 0, min(v, 499.9)), skip=3 if gap and i == 15 else 0)
    ok = E.GyroClipCompensator()
    hump(ok, False)
    lost = sum(max(0.0, v - 499.9) for v in true) * 0.01
    assert ok.active and ok.events == 1 and not ok.uncertain
    assert abs(ok.last_run_deg - lost) < 0.15 * lost, (ok.last_run_deg, lost)
    assert abs(ok.offset_deg - lost) < 0.15 * lost       # + = CCW about +Z
    gap = E.GyroClipCompensator()
    hump(gap, True)
    assert gap.uncertain and not gap.active
    # Packets lost during a 90 deg turn, then a STATIONARY packet: the gap
    # (rebase, keep the heading correction) comes before the stationary
    # re-level, which used to wipe the correction and leave it at ~-80 deg.
    comp = E.GyroClipCompensator()
    probe = Probe(comp)
    for v in [0] * 10 + true + [0] * 12:
        probe.send((0, 0, min(v, 499.9)))
    before = comp.offset_deg
    assert comp.active and before > 3.0, before
    q_turned = tuple(E.q_mul(yaw_quat(90.0), np.array(probe.q)))
    st = VALID | E.USB_IMU_FLAG_STATIONARY
    for k in range(4):
        comp.update(record(probe.seq + 30 + k, (probe.seq + 30 + k) * 10000,
                           (0, 0, 0), q_turned, flags=st), 1)
        assert abs(comp.offset_deg - before) < 1e-6, (k, comp.offset_deg, before)
    assert comp.active and not comp.uncertain
    # Records without rate data (old test doubles) or not yet calibrated are
    # simply not used.
    comp = E.GyroClipCompensator()
    comp.update({"flags": VALID, "sequence": 1, "timestamp_us": 1,
                 "quaternion": (1, 0, 0, 0)}, 1)
    comp.update(record(2, 20000, (0, 0, 499.9), flags=E.USB_IMU_FLAG_VALID), 1)
    assert not comp.active and comp._buf == []
    print("PASS: EDGES -- 0.5 s over-range spin left alone and flagged uncertain; "
          "short hump bridged ({:+.2f} deg put back, {:.2f} lost); packet gap "
          "mid-clip -> uncertain, nothing invented; uncalibrated/rateless "
          "records ignored".format(ok.last_run_deg, lost))


# ------------------------------------------------------- SHAKE TEST TOOL
def test_shake_headings():
    # tools/gyro_shake_test.py reports heading differences in (-180, 180]:
    # a quaternion sign flip or a full turn is 0, not ~360.
    spec = importlib.util.spec_from_file_location(
        "gyro_shake_test_mod", HERE / "tools" / "gyro_shake_test.py")
    shake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shake)
    for d, want in ((0, 0), (360, 0), (-180, 180), (180, 180), (190, -170),
                    (-190, 170), (720.5, 0.5)):
        assert abs(shake.wrap_deg(d) - want) < 1e-9, (d, shake.wrap_deg(d))
    q = yaw_quat(30.0)
    assert abs(shake.heading_deg(q, q)) < 1e-9
    assert abs(shake.heading_deg(-q, q)) < 1e-6          # same attitude, -q
    assert abs(shake.heading_deg(q, -q)) < 1e-6
    assert abs(shake.heading_deg(yaw_quat(350.0), yaw_quat(0.0)) + 10.0) < 1e-6
    assert abs(shake.heading_deg(yaw_quat(170.0), yaw_quat(-170.0)) + 20.0) < 1e-6
    print("PASS: SHAKE TEST -- heading differences wrapped to (-180, 180] "
          "(sign flip / full turn -> 0)")


# ----------------------------------------------------------- DETECTION
def test_detection():
    # Genuine fast motion that never saturates: Z ramps 0..490 dps, ten
    # samples at 497, ramps down. The old 495 dps threshold took the 497s for
    # a clip and invented ~15.8 deg of heading; nothing may be repaired now.
    comp = E.GyroClipCompensator()
    probe = Probe(comp)
    for v in list(range(0, 491, 70)) + [497.0] * 10 + list(range(490, -1, -70)) + [0] * 12:
        probe.send((0, 0, v))
    assert not comp.active and comp.events == 0 and not comp.uncertain
    assert comp.repaired_deg == 0.0 and comp.offset_deg == 0.0
    q = (1.0, 0.0, 0.0, 0.0)
    assert comp.correct(q) is q
    # Near full scale but still moving (not a plateau): no clip either.
    comp = E.GyroClipCompensator()
    probe = Probe(comp)
    for v in [300.0, 450.0, 498.6, 499.2, 499.7, 499.1, 498.8, 440.0, 300.0] + [0] * 12:
        probe.send((0, 0, v))
    assert not comp.active and comp.events == 0 and comp.repaired_deg == 0.0
    # A true plateau (bias 0.1 -> 499.9, as the packet reads) whose neighbours
    # happen to read 498.9 / 498.7 is still repaired: edges are released.
    comp = E.GyroClipCompensator()
    probe = Probe(comp)
    big = [898.0 * math.sin(math.pi * i / 16) for i in range(17)]
    big[3], big[13] = 498.9, 498.7              # real, just below full scale
    for v in [0] * 10 + [499.9 if 499.0 < v else v for v in big] + [0] * 12:
        probe.send((0, 0, v))
    assert comp.active and comp.events == 1 and comp.last_run_deg > 3.0
    # Negative saturation (-500.0 raw minus bias) behaves the same.
    comp = E.GyroClipCompensator()
    probe = Probe(comp)
    true = [640 * math.sin(math.pi * i / 12) for i in range(13)]
    for v in [0] * 10 + [-min(v, 500.2) for v in true] + [0] * 12:
        probe.send((0, 0, v))
    assert comp.active and comp.events == 1 and comp.last_run_deg < -3.0
    print("PASS: DETECTION -- 497 dps unclipped ramp and non-flat 498.6-499.7 "
          "runs left alone; flat plateau repaired (edge samples released), "
          "both signs")


# -------------------------------------------------------------------- RESET
def _clipped_records(n_still=12):
    """Records for a level probe yaw flick that clips (sim firmware), with the
    calibration phase skipped."""
    rng = np.random.default_rng(3)
    q_true, _ = flick_path(np.array([1.0, 0, 0, 0]), (0, 0, 1), 60.0, 0.12, 0.6)
    recs = [rec for _, rec, _ in sim.probe_packets(q_true, rng, "post",
                                                    lever=(0.0, 0.0, 0.0))]
    first = next(i for i, r in enumerate(recs) if r["flags"] & 4)
    return recs[first:]


def test_reset():
    recs = _clipped_records()
    # Generation change resets.
    comp = E.GyroClipCompensator()
    for rec in recs:
        comp.update(rec, 1)
    assert comp.active and abs(comp.offset_deg) > 3.0, comp.offset_deg
    comp.update(dict(recs[-1], sequence=recs[-1]["sequence"] + 1,
                     timestamp_us=recs[-1]["timestamp_us"] + 10000), 2)
    assert not comp.active and comp.offset_deg == 0.0 and comp.events >= 1

    # Through the real link: publish, then ZERO.
    link = E.UsbCompositeProbeLink()
    for rec in recs:
        link._publish(rec)
    h = link.health()
    assert h["clip"]["active"] and link.quat != link.fw_quat
    assert link.fw_quat is recs[-1]["quaternion"]
    a = E.App.__new__(E.App)
    a.link, a.q_ref = link, None
    a.toast = lambda *args, **kwargs: None
    assert a.capture_zero()
    assert a.q_ref is link.fw_quat and link.quat is link.fw_quat
    assert not link.clip_comp.active
    # A later device reboot (timestamp goes backwards) bumps the generation
    # and the compensator starts over with it.
    for rec in recs:
        link._publish(rec)
    assert link.clip_comp.active
    gen = link.generation
    link._publish(dict(recs[0], timestamp_us=5))
    assert link.generation == gen + 1 and not link.clip_comp.active
    # The older records of one serial read are fed too, in order.
    link2 = E.UsbCompositeProbeLink()
    for i in range(0, len(recs), 7):
        chunk = recs[i:i + 7]
        link2._publish(chunk[-1], chunk[:-1])
    assert abs(link2.clip_comp.offset_deg - comp_offset(recs)) < 1e-9
    print("PASS: RESET -- link generation change and ZERO drop the correction "
          "(ZERO takes its reference from the firmware quaternion); a device "
          "reboot resets it; batched reads feed every record in order")


def comp_offset(recs):
    comp = E.GyroClipCompensator()
    for rec in recs:
        comp.update(rec, 1)
    return comp.offset_deg


# ------------------------------------------------------------------- CONFIG
def test_config():
    recs = _clipped_records()

    def app_with(cfg, link):
        a = E.App.__new__(E.App)
        a.cfg, a.link = cfg, link
        a._gyro_clip_setup()
        return a

    # Absent key: module default (on), and the key is NOT written into cfg.
    link = E.UsbCompositeProbeLink()
    a = app_with({"config_rev": E.CONFIG_REV}, link)
    assert a.gyro_clip_comp is E.GYRO_CLIP_COMPENSATION_DEFAULT is True
    assert "gyro_clip_compensation" not in a.cfg and link.clip_comp is not None
    # Off (bool or string): the link publishes the record quaternion object.
    for off in (False, "off", 0):
        link = E.UsbCompositeProbeLink()
        a = app_with({"gyro_clip_compensation": off}, link)
        assert a.gyro_clip_comp is False and link.clip_comp is None
        assert a.cfg["gyro_clip_compensation"] is False
        for rec in recs:
            link._publish(rec)
            assert link.quat is rec["quaternion"]
        assert link.health()["clip"] is None
    # A damaged value falls back to the default and is normalised in cfg.
    for bad in ("maybe", float("nan"), [1], {"x": 1}):
        a = app_with({"gyro_clip_compensation": bad}, E.UsbCompositeProbeLink())
        assert a.gyro_clip_comp is True and a.cfg["gyro_clip_compensation"] is True
    # Other link types are untouched.
    for cls in (E.ProbeLink, E.OfficialProbeLink, E.SimLink):
        assert not hasattr(cls, "reset_clip_comp")
    fake = types.SimpleNamespace(quat=(1, 0, 0, 0))
    a = app_with({"gyro_clip_compensation": False}, fake)
    assert vars(fake) == {"quat": (1, 0, 0, 0)}
    print("PASS: CONFIG -- key absent -> on (default constant, never "
          "setdefault'ed into the config); false/'off'/0 -> link publishes the "
          "firmware quaternion object exactly like 6.0.5; junk -> default; "
          "other link types untouched")


# ------------------------------------------------------------------- STATUS
def test_status():
    clock = [1000.0]
    old_time = E.time
    toasts = []
    try:
        E.time = types.SimpleNamespace(monotonic=lambda: clock[0])
        a = E.App.__new__(E.App)
        a.cfg, a.link = {}, types.SimpleNamespace()
        a._gyro_clip_setup()
        a.toast = lambda text, color=None, ms=900: toasts.append((text, color))

        def bits(clip):
            out = []
            a._gyro_clip_status({"clip": clip}, out)
            return out
        base = {"active": True, "offset_deg": 12.0, "last_run_deg": 12.4,
                "last_run_samples": 6, "repaired_deg": 20.0, "uncertain": False,
                "events": 1, "uncertain_events": 0, "event_time": 999.0}
        assert bits(base) == ["CLIP+12°"] and not toasts
        assert bits(dict(base, last_run_deg=-7.6)) == ["CLIP-8°"]
        clock[0] = 1003.5
        assert bits(base) == [] and bits(None) == []
        unsure = dict(base, uncertain=True, uncertain_events=1, event_time=1003.5)
        bits(unsure)
        assert toasts == [("FAST SHAKE — HEADING MAY BE OFF, PRESS ZERO", E.WARN)]
        clock[0] = 1008.0                    # another one 4.5 s later: quiet
        bits(dict(unsure, uncertain_events=2))
        assert len(toasts) == 1
        clock[0] = 1014.0                    # 10.5 s after the first: warns
        bits(dict(unsure, uncertain_events=3))
        bits(dict(unsure, uncertain_events=3))   # same event: no repeat
        assert len(toasts) == 2
    finally:
        E.time = old_time
    print("PASS: STATUS -- CLIP+n° for {:.0f} s after a repaired run; the "
          "uncertain toast at most once per {:.0f} s".format(
              E.GYRO_CLIP_SHOW_S, E.GYRO_CLIP_WARN_EVERY_S))


# ---------------------------------------------------------------- SIMULATOR
def test_simulator():
    rows = sim.run_set([E.GyroClipCompensator], 36, 2026, peaks=(600.0, 1500.0))
    s = sim.summarise(rows)
    print("SIMULATOR: {} trials, heading error median {:.2f} -> {:.2f} deg, "
          "p95 {:.2f} -> {:.2f} deg, {} worse by >1 deg".format(
              s["n"], s["fw_med"], s["comp_med"], s["fw_p95"], s["comp_p95"],
              s["worse"]))
    # Regression bounds (this seed measured 6.8 -> 0.39 median, 56 -> 9.0 p95,
    # 1 worse).
    assert s["fw_med"] > 4.0 and s["fw_p95"] > 40.0      # the problem is real
    assert s["comp_med"] < 0.3 * s["fw_med"], s
    assert s["comp_p95"] < 0.4 * s["fw_p95"], s
    assert s["worse"] <= 4, s
    calm = sim.run_set([E.GyroClipCompensator], 6, 7, peaks=(150.0, 480.0))
    assert all(r["identical"][0] and r["comp"][0] == r["fw"]
               for _, _, _, r in calm)
    print("PASS: SIMULATOR -- compensation beats the firmware by the bound; "
          "no-clip trials bit-identical")


def main():
    test_identity()
    test_single_axis()
    test_multi_axis()
    test_edges()
    test_detection()
    test_shake_headings()
    test_reset()
    test_config()
    test_status()
    test_simulator()
    print("DONE: test_gyro_comp.py")


if __name__ == "__main__":
    main()
