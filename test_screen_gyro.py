#!/usr/bin/env python3
"""Contract tests for the screen-mounted IMU azimuth correction (SPEC_SCREEN_GYRO.md).

Orchestrator-authored, frozen. codex implements to make these pass and MUST NOT
edit them. codex ADDS the remaining SPEC §7 groups (fusion device-time #2, link
manager #8, port exclusion #9, config/CLI #10, shutdown #11, firmware contract
#12) in this file as additional test_* functions and wires them into main().

Run: python <scratchpad>/run_tests_win.py <repo> test_screen_gyro.py
(hardware-free; load_app stubs serial/cv2/tkinter/PIL; the shim fakes fcntl).
"""
import inspect
import json
import math
import tempfile
import time
import types
import zlib
from pathlib import Path

from test_endoscope_core import load_app


def make_screen_packet(app, sequence=5, flags=None, ts=123456,
                       accel=(0.0, 0.0, 1.0), gyro=(0.1, -0.2, 0.3)):
    """Build exactly the 48-byte SIMU record the XIAO firmware emits."""
    if flags is None:
        flags = app.SCREEN_IMU_FLAG_VALID
    without_crc = app.SCREEN_IMU_PACKET.pack(
        app.SCREEN_IMU_MAGIC, app.SCREEN_IMU_VERSION, flags,
        app.SCREEN_IMU_PACKET.size, sequence, ts, *accel, *gyro, 0)
    crc = zlib.crc32(without_crc[:-4]) & 0xffffffff
    return without_crc[:-4] + crc.to_bytes(4, "little")


def _rz(beta):
    """Quaternion for a rotation about world +Z by beta (CCW positive)."""
    return (math.cos(beta / 2.0), 0.0, 0.0, math.sin(beta / 2.0))


# ---- 1. parser -----------------------------------------------------------

def test_screen_packet_parser(app):
    assert app.SCREEN_IMU_PACKET.size == 48
    assert app.SCREEN_IMU_MAGIC == b"SIMU"
    parser = app.ScreenImuPacketParser()
    valid = make_screen_packet(app, sequence=5)

    # A corrupted record, split reads, and leading noise must all recover at the
    # next magic word, exactly like the camera CDC parser.
    corrupt = bytearray(valid)
    corrupt[20] ^= 0x80                      # flip a byte inside accel_x
    assert parser.feed(b"boot noise\x00" + bytes(corrupt[:30])) == []
    mid = parser.feed(bytes(corrupt[30:]) + valid[:12])
    assert mid == []
    records = parser.feed(valid[12:])
    assert len(records) == 1
    rec = records[0]
    assert rec["sequence"] == 5
    assert rec["flags"] & app.SCREEN_IMU_FLAG_VALID
    assert abs(rec["accel"][2] - 1.0) < 1e-6
    assert abs(rec["gyro"][0] - 0.1) < 1e-6
    assert parser.bad_packets == 1

    # Non-finite telemetry with a correct CRC must be rejected, not fused.
    nan = make_screen_packet(app, sequence=6, accel=(float("nan"), 0.0, 1.0))
    out = parser.feed(nan)
    assert all(math.isfinite(v) for r in out for v in r["accel"])


# ---- 2. wrap_pi ----------------------------------------------------------

def test_wrap_pi(app):
    pi = math.pi
    assert abs(app.wrap_pi(0.0)) == 0.0
    assert app.wrap_pi(0.1) == 0.1                 # ordinary values untouched
    assert app.wrap_pi(-0.1) == -0.1
    assert abs(app.wrap_pi(pi) - pi) < 1e-12       # -pi maps to +pi, +pi stays
    assert abs(app.wrap_pi(-pi) - pi) < 1e-12
    assert abs(app.wrap_pi(2 * pi)) < 1e-12
    assert abs(app.wrap_pi(3 * pi) - pi) < 1e-12
    assert abs(app.wrap_pi(1.5 * pi) - (-0.5 * pi)) < 1e-12


# ---- 3. screen heading + sign -------------------------------------------

def test_screen_heading(app):
    # Pure yaw by alpha about +Z reads back as alpha.
    for alpha in (0.0, 0.4, -0.9, 1.5):
        assert abs(app.screen_heading(_rz(alpha)) - alpha) < 1e-9


# ---- helpers for the App-method contract --------------------------------

class FakeScreenLink:
    def __init__(self, snap):
        self._snap = snap
        self.present = True
    def snapshot_locked(self):
        return dict(self._snap)


def _app_with_screen(app, *, quat, generation, scr_q_ref, scr_zero_gen,
                     state="online", calibrating=False, sign=1, fresh=True):
    import time
    a = app.App.__new__(app.App)
    imu_time = time.monotonic() - (0.0 if fresh else 10.0)
    a.screen_link = FakeScreenLink(dict(
        quat=quat, state=state, calibrating=calibrating,
        imu_time=imu_time, generation=generation, still=True))
    a.scr_q_ref = scr_q_ref
    a.scr_zero_gen = scr_zero_gen
    a.screen_sign = sign
    return a


# ---- 4. correction ACTIVE ------------------------------------------------

def test_screen_az_correction_active(app):
    # Screen rotated LEFT (CCW, +Z) by 0.4 from its zero -> phi=+0.4, corr=+0.4.
    a = _app_with_screen(app, quat=_rz(0.4), generation=3,
                         scr_q_ref=_rz(0.0), scr_zero_gen=3)
    assert abs(a._screen_az_correction() - 0.4) < 1e-6

    # Screen rotated RIGHT (CW) by 0.4 -> phi=-0.4, corr=-0.4. With a fixed camera
    # (az_abs=0) the arrow then reads -0.4 (camera to the LEFT of the screen).
    a = _app_with_screen(app, quat=_rz(-0.4), generation=3,
                         scr_q_ref=_rz(0.0), scr_zero_gen=3)
    corr = a._screen_az_correction()
    assert abs(corr + 0.4) < 1e-6
    assert abs(app.wrap_pi(0.0 + corr) - (-0.4)) < 1e-6

    # screen_sign=-1 mirrors the correction (mounting handedness).
    a = _app_with_screen(app, quat=_rz(0.4), generation=3,
                         scr_q_ref=_rz(0.0), scr_zero_gen=3, sign=-1)
    assert abs(a._screen_az_correction() + 0.4) < 1e-6


# ---- 5. passthrough is EXACT (the no-op invariant) -----------------------

def test_screen_az_correction_passthrough_exact(app):
    # No screen link at all.
    a = app.App.__new__(app.App)
    a.screen_link = None
    a.scr_q_ref = None
    a.scr_zero_gen = None
    a.screen_sign = 1
    assert a._screen_az_correction() == 0.0

    base = dict(quat=_rz(0.4), generation=3, scr_q_ref=_rz(0.0), scr_zero_gen=3)
    # generation mismatch (screen reset since ZERO)
    a = _app_with_screen(app, **{**base, "scr_zero_gen": 4})
    assert a._screen_az_correction() == 0.0
    # stale sample
    a = _app_with_screen(app, **base, fresh=False)
    assert a._screen_az_correction() == 0.0
    # not yet calibrated
    a = _app_with_screen(app, **base, calibrating=True)
    assert a._screen_az_correction() == 0.0
    # not online
    a = _app_with_screen(app, **base, state="connecting")
    assert a._screen_az_correction() == 0.0
    # no screen reference captured
    a = _app_with_screen(app, **{**base, "scr_q_ref": None})
    assert a._screen_az_correction() == 0.0

    # And applying a 0.0 correction leaves any azimuth bit-identical.
    for az in (0.0, 0.123456789, -1.5, math.pi, -math.pi):
        corr = 0.0
        assert (app.wrap_pi(az + corr) if corr else az) == az


# ---- 6. capture_zero isolation ------------------------------------------

def test_capture_zero_screen_isolation(app):
    link = app.UsbCompositeProbeLink()
    flags = app.USB_IMU_FLAG_VALID | app.USB_IMU_FLAG_CALIBRATED
    link._publish(dict(timestamp_us=100, sequence=1, flags=flags,
                       quaternion=(1, 0, 0, 0)))

    def fresh_app(screen_link):
        a = app.App.__new__(app.App)
        a.link = link
        a.q_ref = (0, 1, 0, 0)
        a.toast = lambda *x, **k: None
        a.screen_link = screen_link
        a.scr_q_ref = None
        a.scr_zero_gen = None
        a.screen_sign = 1
        return a

    # Healthy screen: camera ZERO succeeds AND the screen ref is captured.
    good = _app_with_screen(app, quat=_rz(0.7), generation=9,
                            scr_q_ref=None, scr_zero_gen=None).screen_link
    a = fresh_app(good)
    assert a.capture_zero() is True
    assert a.q_ref == (1, 0, 0, 0)                 # camera unchanged behaviour
    assert a.scr_q_ref == _rz(0.7)
    assert a.scr_zero_gen == 9

    # A screen-link exception must NOT change the camera ZERO result.
    class Boom:
        present = True
        def snapshot_locked(self):
            raise RuntimeError("screen link died mid-ZERO")
    a = fresh_app(Boom())
    assert a.capture_zero() is True
    assert a.q_ref == (1, 0, 0, 0)
    assert a.scr_q_ref is None                      # only the screen ref cleared


# ---- 2. device-time screen fusion ---------------------------------------

def test_screen_fusion_device_time(app):
    fusion = app.ScreenMahonyFusion()
    t_us = 10_000_000

    # Calibrate from a batch of records carrying device timestamps. Host time
    # never participates in the integration clock.
    packets = b"".join(make_screen_packet(
        app, sequence=i, ts=t_us + i * 10_000,
        gyro=(0.0, 0.0, 0.0)) for i in range(300))
    records = app.ScreenImuPacketParser().feed(packets)
    assert len(records) == 300
    for rec in records:
        fusion.update(rec["accel"], rec["gyro"], rec["timestamp_us"] / 1e6)
    assert fusion.calibrated

    # One second at 90 dps, still delivered as a single decoded batch.
    packets = b"".join(make_screen_packet(
        app, sequence=300 + i, ts=t_us + (300 + i) * 10_000,
        gyro=(0.0, 0.0, 90.0)) for i in range(100))
    for rec in app.ScreenImuPacketParser().feed(packets):
        fusion.update(rec["accel"], rec["gyro"], rec["timestamp_us"] / 1e6)
    yaw = app.screen_heading(fusion.q)
    assert math.radians(84.0) < yaw < math.radians(96.0), yaw
    assert abs(sum(v * v for v in fusion.q) - 1.0) < 1e-6

    # Duplicates, backwards time and a long gap all take the nominal 0.01 s
    # fallback; they cannot inject a giant integration step.
    before = app.screen_heading(fusion.q)
    for stamp in (13.99, 13.50, 99.0):
        fusion.update((0.0, 0.0, 1.0), (0.0, 0.0, 90.0), stamp)
    delta = app.wrap_pi(app.screen_heading(fusion.q) - before)
    assert 0.02 < delta < 0.08, delta
    assert abs(sum(v * v for v in fusion.q) - 1.0) < 1e-6
    assert app.MahonyFusion.DT_MAX == 0.5
    assert app.MahonyFusion.DT_FALLBACK == 0.1


# ---- 8. serial manager recovery and epochs ------------------------------

def test_screen_link_manager_epochs_and_failures(app):
    class CountingFusion(app.ScreenMahonyFusion):
        def __init__(self):
            self.reset_count = 0
            super().__init__()
        def reset(self):
            self.reset_count += 1
            super().reset()

    link = app.ScreenImuLink()
    link.fusion = CountingFusion()
    baseline_resets = link.fusion.reset_count

    def record(seq, stamp):
        return {"flags": app.SCREEN_IMU_FLAG_VALID, "sequence": seq,
                "timestamp_us": stamp, "accel": (0.0, 0.0, 1.0),
                "gyro": (0.0, 0.0, 0.0)}

    assert link._accept_record(record(1, 100_000))
    assert link.generation == 1
    assert link.fusion.reset_count == baseline_resets + 1
    assert link._accept_record(record(2, 110_000))
    assert link.generation == 1

    # One backwards timestamp is one epoch, and the following packet is not a
    # second bump. Marking a reopen creates exactly one more bump on first VALID.
    assert link._accept_record(record(3, 50_000))
    assert link._accept_record(record(4, 60_000))
    assert link.generation == 2
    link._awaiting_epoch = True
    assert link._accept_record(record(10, 5_000))
    assert link._accept_record(record(12, 15_000))
    assert link.generation == 3
    assert link.fusion.reset_count == baseline_resets + 3
    assert link.dropped_packets == 1

    old_serial, old_finder, old_time = (
        app.serial.Serial, app.find_screen_imu_port, app.time)
    try:
        # Silence and arbitrary garbage never refresh the valid-packet clock.
        for garbage in (b"", b"not-a-SIMU-record"):
            clock = [100.0]
            managed = app.ScreenImuLink()

            class QuietPort:
                in_waiting = 0
                def open(self):
                    pass
                def read(self, _size):
                    clock[0] += 0.5
                    return garbage
                def close(self):
                    managed.running = False

            app.serial.Serial = QuietPort
            app.find_screen_imu_port = lambda _preferred=None: "/dev/xiao"
            app.time = types.SimpleNamespace(
                monotonic=lambda: clock[0], sleep=lambda _seconds: None)
            managed.running = True
            managed._manager()
            assert managed.state == "offline"
            assert managed.imu_reconnects == 1
            assert "no valid packet" in managed.last_imu_error
            assert managed.imu_time == 0.0

        # An actual read exception is contained and published as offline.
        managed = app.ScreenImuLink()

        class BrokenPort:
            in_waiting = 0
            def open(self):
                pass
            def read(self, _size):
                raise OSError("USB unplugged")
            def close(self):
                managed.running = False

        app.serial.Serial = BrokenPort
        app.time = types.SimpleNamespace(monotonic=lambda: 1.0,
                                         sleep=lambda _seconds: None)
        managed.running = True
        managed._manager()
        assert managed.state == "offline"
        assert managed.imu_reconnects == 1
        assert "USB unplugged" in managed.last_imu_error
    finally:
        app.serial.Serial, app.find_screen_imu_port, app.time = (
            old_serial, old_finder, old_time)


# ---- 9. two-device discovery exclusion ---------------------------------

def test_two_device_port_exclusion(app):
    atom = "/dev/serial/by-id/usb-M5Stack_AtomS3R-CAM-if04-port0"
    xiao = "/dev/serial/by-id/usb-Seeed_XIAO_nRF52840_Sense-if00"
    atom_tty, xiao_tty = "/dev/ttyACM0", "/dev/ttyACM1"
    old_glob = app.glob.glob
    old_realpath = app.os.path.realpath
    old_exists = app.os.path.exists
    old_screen_id = app._is_screen_imu_port
    try:
        mapping = {atom: atom_tty, xiao: xiao_tty,
                   atom_tty: atom_tty, xiao_tty: xiao_tty}
        app.os.path.realpath = lambda p: mapping.get(str(p), str(p))
        app.os.path.exists = lambda p: str(p) in mapping
        app._is_screen_imu_port = lambda p: any(
            token in str(p).lower() for token in ("seeed", "xiao", "nrf52")) \
            or app.os.path.realpath(p) == xiao_tty

        for order in ((atom, xiao), (xiao, atom)):
            def fake_glob(pattern, order=order):
                if pattern == "/dev/serial/by-id/*AtomS3R*":
                    return [atom]
                if pattern == "/dev/serial/by-id/*atom*":
                    return []
                if pattern == "/dev/serial/by-id/*":
                    return list(order)
                if pattern == "/dev/ttyACM*":
                    return [xiao_tty, atom_tty]
                return []
            app.glob.glob = fake_glob
            assert app.find_usb_imu_port() == atom
            assert app.find_screen_imu_port() == xiao
            assert app.find_screen_imu_port(atom_tty) is None

        # With no Atom by-id link, fallback ranking must skip the positive XIAO.
        def fallback_glob(pattern):
            if pattern in ("/dev/serial/by-id/*AtomS3R*",
                           "/dev/serial/by-id/*atom*",
                           "/dev/serial/by-id/*"):
                return []
            if pattern == "/dev/ttyACM*":
                return [xiao_tty, atom_tty]
            return []
        app.glob.glob = fallback_glob
        assert app.find_usb_imu_port() == atom_tty
    finally:
        app.glob.glob = old_glob
        app.os.path.realpath = old_realpath
        app.os.path.exists = old_exists
        app._is_screen_imu_port = old_screen_id


# ---- 10. config round-trip and CLI precedence ---------------------------

def test_screen_config_round_trip_and_cli(app):
    with tempfile.TemporaryDirectory() as tmp:
        old_config = app.CONFIG
        app.CONFIG = str(Path(tmp) / "endoscope.json")
        try:
            initial = {"config_rev": app.CONFIG_REV, "axis": 2,
                       "unrelated": {"keep": "me"},
                       "screen_imu": {"enabled": True,
                                      "port": "/dev/screen", "sign": -1}}
            Path(app.CONFIG).write_text(json.dumps(initial), encoding="utf-8")
            viewer = app.App.__new__(app.App)
            viewer.cfg = viewer.load_cfg()
            viewer.axis_idx = 2
            viewer.rot180 = False
            viewer.video_flip_h = False
            viewer.video_flip_v = False
            viewer.el_sign = 1.0
            viewer.awb = False
            viewer.swap_rb = False
            viewer.tune_saved = None
            viewer.pi_gains = [1.0, 1.0, 1.0]
            viewer.pi_chan = [0, 1, 2]
            viewer.pi_bright = 0.0
            viewer.pi_sat = 1.0
            viewer.pi_black = [0.0, 0.0, 0.0]
            viewer.pi_white = [255.0, 255.0, 255.0]
            viewer.pi_gamma = [1.0, 1.0, 1.0]
            viewer.pi_matrix = None
            viewer.screen_imu_cfg = dict(initial["screen_imu"])
            viewer.save_cfg()
            saved = json.loads(Path(app.CONFIG).read_text(encoding="utf-8"))
            assert saved["screen_imu"] == initial["screen_imu"]
            assert saved["unrelated"] == {"keep": "me"}
            assert viewer.load_cfg()["screen_imu"]["port"] == "/dev/screen"
        finally:
            app.CONFIG = old_config

    omitted = types.SimpleNamespace(screen_imu_port=None,
                                    screen_imu_sign=None,
                                    no_screen_imu=False)
    assert app._screen_imu_settings({}, omitted) == {
        "enabled": True, "port": None, "sign": 1}
    cfg = {"screen_imu": {"enabled": True, "port": "/dev/config", "sign": 1}}
    explicit = types.SimpleNamespace(screen_imu_port="/dev/cli",
                                     screen_imu_sign=-1,
                                     no_screen_imu=False)
    assert app._screen_imu_settings(cfg, explicit) == {
        "enabled": True, "port": "/dev/cli", "sign": -1}
    disabled = types.SimpleNamespace(screen_imu_port=None,
                                     screen_imu_sign=None,
                                     no_screen_imu=True)
    assert app._screen_imu_settings(cfg, disabled)["enabled"] is False
    main_source = inspect.getsource(app.main)
    for option in ("--screen-imu-port", "--no-screen-imu", "--screen-imu-sign"):
        assert option in main_source


# ---- 11. bounded, idempotent shutdown -----------------------------------

def test_screen_link_shutdown_idempotent(app):
    link = app.ScreenImuLink()
    closes, joins = [], []

    class Port:
        def close(self):
            closes.append(True)

    class Thread:
        def join(self, timeout=None):
            joins.append(timeout)
        def is_alive(self):
            return False

    link.ser = Port()
    link.thread = Thread()
    link.running = True
    link.stop()
    link.stop()
    assert len(closes) == 1
    assert joins == [1.5]
    cleanup = inspect.getsource(app.main)
    assert 'getattr(app, "screen_link", None)' in cleanup
    assert "screen_link.stop()" in cleanup


# ---- 12. XIAO firmware wire contract ------------------------------------

def test_screen_firmware_contract():
    sketch = (Path(__file__).resolve().parent /
              "screen_imu/screen_imu.ino").read_text(encoding="utf-8")
    assert "struct __attribute__((packed)) ScreenImuRecordV1" in sketch
    assert "sizeof(ScreenImuRecordV1) == 48" in sketch
    assert "'S'" in sketch and "'I'" in sketch and "'M'" in sketch and "'U'" in sketch
    assert "kSampleRateHz = 100" in sketch and "kSamplePeriodUs" in sketch
    assert "accelRange = 4" in sketch and "gyroRange = 500" in sketch
    assert "accelSampleRate = 104" in sketch and "gyroSampleRate = 104" in sketch
    assert "uint64_t extended_micros()" in sketch
    assert "UINT64_C(1) << 32" in sketch
    assert "Serial.write" in sketch and "48" in sketch
    # Defense-in-depth: pin the two firmware details most able to silently break
    # the feature on real hardware if a future edit regressed them.
    assert "0xEDB88320" in sketch             # IEEE CRC-32 polynomial (== zlib.crc32)
    assert "PIN_LSM6DS3TR_C_POWER" in sketch  # explicit IMU power-rail enable


def test_screen_cli_not_persisted(app):
    """A one-time CLI override must NOT be written to the config file.

    Regression for the footgun where App.__init__ baked CLI-merged settings into
    self.cfg["screen_imu"] and save_cfg persisted them, so a single
    --no-screen-imu (or --screen-imu-sign) became sticky on disk.
    """
    with tempfile.TemporaryDirectory() as tmp:
        old_config = app.CONFIG
        app.CONFIG = str(Path(tmp) / "endoscope.json")
        try:
            a = app.App.__new__(app.App)
            # cfg holds the CONFIG-LEVEL screen settings; screen_imu_cfg is the
            # CLI-merged runtime view. save_cfg must persist the former.
            a.cfg = {"config_rev": app.CONFIG_REV, "axis": 0,
                     "screen_imu": {"enabled": True, "port": None, "sign": 1}}
            a.screen_imu_cfg = {"enabled": False, "port": "/dev/cli", "sign": -1}
            a.axis_idx = 0
            a.rot180 = a.video_flip_h = a.video_flip_v = False
            a.el_sign = 1.0
            a.awb = a.swap_rb = False
            a.tune_saved = None
            a.pi_gains = [1.0, 1.0, 1.0]
            a.pi_chan = [0, 1, 2]
            a.pi_bright = 0.0
            a.pi_sat = 1.0
            a.pi_black = [0.0, 0.0, 0.0]
            a.pi_white = [255.0, 255.0, 255.0]
            a.pi_gamma = [1.0, 1.0, 1.0]
            a.pi_matrix = None
            a.save_cfg()
            saved = json.loads(Path(app.CONFIG).read_text(encoding="utf-8"))
            assert saved["screen_imu"] == {"enabled": True, "port": None,
                                           "sign": 1}, saved["screen_imu"]
        finally:
            app.CONFIG = old_config

    # A neutral (no-CLI) args object yields exactly the config-level settings.
    cfg = {"screen_imu": {"enabled": True, "port": "/dev/config", "sign": -1}}
    assert app._screen_imu_settings(cfg, object()) == {
        "enabled": True, "port": "/dev/config", "sign": -1}


def main():
    app = load_app()
    test_screen_packet_parser(app)
    test_wrap_pi(app)
    test_screen_heading(app)
    test_screen_az_correction_active(app)
    test_screen_az_correction_passthrough_exact(app)
    test_capture_zero_screen_isolation(app)
    test_screen_fusion_device_time(app)
    test_screen_link_manager_epochs_and_failures(app)
    test_two_device_port_exclusion(app)
    test_screen_config_round_trip_and_cli(app)
    test_screen_cli_not_persisted(app)
    test_screen_link_shutdown_idempotent(app)
    test_screen_firmware_contract()
    print("PASS: screen-gyro contract (13 groups: parser, device-time fusion, "
          "heading/wrap, correction, ZERO isolation, manager/epochs, port "
          "exclusion, config/CLI, CLI-not-persisted, shutdown, firmware)")


if __name__ == "__main__":
    main()
