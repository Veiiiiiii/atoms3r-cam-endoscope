#!/usr/bin/env python3
"""Live-view UV mode tests (UV-PORT-PLAN.md §3.1, §3.3-§3.5, §3.8, §3.9):
the UV MODE button, the semi-transparent bottom bar, toggles and their
persistence, the frame pipeline, error fallback and tracker resets.

Run: py -3 test_uv_ui.py   (needs a display for real Tk; prints SKIPPED and
exits 0 when there is none). No camera/serial device is used: a static-frame
fake link feeds the real App, and endoscope.CONFIG always points at a temp
file -- the operator's ~/.config/endoscope.json is never touched.

The UV-OFF identity check loads the field-approved 6.1.0 endoscope.py
straight out of git (commit 0372214) and compares the exact array handed to
Image.fromarray against this file's App for the same synthetic frame.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import cv2
import numpy as np
import PIL.Image

HERE = Path(__file__).resolve().parent
PRISTINE_610 = "0372214"
TMP = Path(tempfile.mkdtemp(prefix="test_uv_ui_"))


# --------------------------------------------------------------- loaders
def _stub_platform_modules():
    if "fcntl" not in sys.modules:
        try:
            import fcntl  # noqa: F401  (present on the Pi/Linux, never on Windows)
        except ImportError:
            fake_fcntl = types.ModuleType("fcntl")
            fake_fcntl.LOCK_EX = 2
            fake_fcntl.LOCK_NB = 4
            fake_fcntl.LOCK_UN = 8
            fake_fcntl.flock = lambda *a, **k: None
            sys.modules["fcntl"] = fake_fcntl
    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = object
    sys.modules.setdefault("serial", fake_serial)


def load_module(path, name):
    """Load an endoscope.py the way test_uv_core.py does: real cv2/numpy/Tk,
    stubbed fcntl/serial, and CONFIG redirected to a temp file at once."""
    _stub_platform_modules()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG = str(TMP / (name + ".json"))
    # The tuning drawer's presets file and EXPORT never touch the real home
    # folder or a real USB stick either (6.1.0 has neither, hence getattr).
    if hasattr(module, "UV_PRESETS_FILE"):
        module.UV_PRESETS_FILE = str(TMP / (name + "_uv_presets.json"))
        module.UV_MEDIA_ROOT = str(TMP / "no_media")
        module.uv_find_export_dir = lambda **kw: (str(TMP / (name + "_export")), False)
    return module


def load_pristine_610():
    """6.1.0's endoscope.py from git, or None if git/the commit is absent
    (e.g. a deployed package without .git)."""
    try:
        src = subprocess.run(["git", "show", PRISTINE_610 + ":endoscope.py"],
                             cwd=str(HERE), capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    path = TMP / "endoscope_610.py"
    path.write_bytes(src)
    return load_module(path, "endoscope_610")


class ImageSpy:
    """Stands in for the module's PIL.Image: records every array handed to
    Image.fromarray (the exact pixels that reach the screen)."""

    def __init__(self):
        self.arrays = []

    def __getattr__(self, name):
        return getattr(PIL.Image, name)

    def fromarray(self, arr, *a, **k):
        self.arrays.append(np.array(arr, copy=True))
        return PIL.Image.fromarray(arr, *a, **k)


class StaticLink:
    """The parts of UsbCompositeProbeLink the App reads, serving one frame
    that the test swaps by hand. Always healthy and fresh unless told not."""
    is_uvc = True
    is_usb_composite = True

    def __init__(self, frame):
        self.frame = frame
        self.seq = 1
        self.generation = 1
        self.frame_time = time.monotonic()
        self.raw_swap = False

    def set_frame(self, frame):
        self.frame = frame
        self.seq += 1
        self.frame_time = time.monotonic()

    def snapshot(self):
        return self.frame, self.seq, (1.0, 0.0, 0.0, 0.0), True

    def health(self):
        h = self.frame.shape[:2] if self.frame is not None else (240, 320)
        return {"state": "online", "fw": "test", "fw_ver": (6, 2, 0),
                "colour_mode": None, "sensor_preset": None, "sensor_regs": "",
                "regs_seq": 0, "reg_ack": None, "raw_stream": False,
                "test_pattern": False, "calib_ok": True, "raw_seq": 0,
                "calibrating": False, "camera_failed": False,
                "generation": self.generation, "imu_age": 0.0,
                "frame_age": time.monotonic() - self.frame_time, "fps": 25.0,
                "camera_error": None, "camera_reconnects": 0,
                "capture_size": (h[1], h[0]), "bad": 0, "dropped": 0,
                "imu_reconnects": 0, "last_imu_error": None, "port": "TEST"}

    def send_bytes(self, _):
        return False

    def send_byte(self, _):
        return False

    def get_regs(self):
        return {}, None

    def take_raw(self):
        return None, 0


def make_args():
    return argparse.Namespace(
        windowed=True, kiosk=False, video_only=True, legacy_colour_tools=False,
        log=None, no_screen_imu=True, screen_imu_port=None, screen_imu_sign=None,
        sim=False, port=None, baud=115200, video=None, video_size="320x240",
        official=False, usb_composite=False,
        imu_ws="ws://192.168.4.1/api/v1/ws/imu_data")


def make_app(mod, frame, cfg=None, keep_presets=False):
    if cfg is not None:
        Path(mod.CONFIG).write_text(json.dumps(cfg), encoding="utf-8")
    elif os.path.exists(mod.CONFIG):
        os.remove(mod.CONFIG)
    presets = getattr(mod, "UV_PRESETS_FILE", None)
    if presets and not keep_presets and os.path.exists(presets):
        os.remove(presets)
    spy = ImageSpy()
    mod.Image = spy
    link = StaticLink(frame)
    app = mod.App(link, make_args())
    app.root.update()
    return app, link, spy


def close(app):
    app._exiting = True
    try:
        # Drop the App's pending update()/_relayout() timers first, or Tcl
        # complains about them firing into a destroyed interpreter.
        for after_id in app.root.tk.splitlist(app.root.tk.call("after", "info")):
            app.root.after_cancel(after_id)
        app.root.destroy()
    except Exception:
        pass


def resize_to(app, w, h):
    app.root.geometry("{}x{}".format(w, h))
    app.root.update()
    app._relayout()
    app.root.update()


def tick(app, link, frame=None):
    """One fresh RUN frame through App.update(); returns nothing (the spy
    holds the displayed array)."""
    link.set_frame(link.frame if frame is None else frame)
    app.update()


def wait_export(app, timeout=2.0):
    """R1: EXPORT finishes on a daemon thread and reports back through
    root.after(150) polling -- pump the real Tk loop until the busy flag
    clears instead of asserting on it synchronously. A timeout means the
    poll never ran, i.e. a real bug, not a flaky test."""
    t0 = time.monotonic()
    while app._uv_export_busy:
        if time.monotonic() - t0 > timeout:
            raise AssertionError("EXPORT never finished (R1 poll stuck?)")
        time.sleep(0.01)
        app.root.update()
    app.root.update()


# --------------------------------------------------------------- frames
def scene(h=240, w=320, seed=7):
    """A textured, non-symmetric BGR test frame (no fluorescent colours)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    base = np.stack([(xx * 255 // max(1, w - 1)), (yy * 255 // max(1, h - 1)),
                     ((xx + yy) % 97) * 2], axis=-1)
    noise = rng.integers(0, 30, size=(h, w, 3))
    return np.clip(base // 3 + noise, 0, 255).astype(np.uint8)


def blob_frame(h=240, w=320):
    """Dark grey frame with one bright yellow-green 'dye' patch that the
    factory detector picks up (hue 70, sat 200, val 230)."""
    hsv = np.zeros((h, w, 3), np.uint8)
    hsv[..., 2] = 40
    hsv[100:150, 140:200] = (70, 200, 230)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def text_item(app, text):
    for item in app.canvas.find_all():
        if app.canvas.type(item) == "text" and app.canvas.itemcget(item, "text") == text:
            return item
    return None


# --------------------------------------------------------------- tests
def test_uv_off_identity(new, old):
    """(a) UV OFF: the displayed array and its placement match 6.1.0 exactly."""
    if old is None:
        print("SKIPPED: UV-OFF IDENTITY (git or commit {} not available)".format(PRISTINE_610))
        return
    frames = [scene(), scene(480, 640, seed=3)]
    for geometry in ((1024, 600), (800, 480)):
        results = {}
        for label, mod in (("6.1.0", old), ("6.2.0", new)):
            app, link, spy = make_app(mod, frames[0])
            try:
                resize_to(app, *geometry)
                if label == "6.2.0":
                    # Having used UV mode and left it must not leave a trace.
                    app.toggle_uv_mode()
                    tick(app, link)
                    app.toggle_uv_mode()
                out = []
                for flip_h in (False, True):
                    app.video_flip_h = flip_h
                    for f in frames:
                        tick(app, link, f)
                        out.append((spy.arrays[-1],
                                    tuple(app.canvas.coords(app.video_item)),
                                    (app.W, app.H)))
                results[label] = out
            finally:
                close(app)
        for (a, pa, sa), (b, pb, sb) in zip(results["6.1.0"], results["6.2.0"]):
            assert sa == sb, (sa, sb)
            assert pa == pb, (pa, pb)
            assert a.shape == b.shape and np.array_equal(a, b), "UV-off pixels differ from 6.1.0"
    print("PASS: UV-OFF IDENTITY -- displayed arrays and placement == 6.1.0 at 1024x600 and "
          "800x480, plain and mirrored, 320x240 and 640x480, also after a UV on/off round trip")


def test_mode_toggle(mod):
    """(b) toggle_uv_mode on/off, button colour, status bit, dev key."""
    app, link, spy = make_app(mod, scene())
    try:
        assert app.uv_mode is False
        assert app.canvas.itemcget(app.uv_mode_btn[0], "fill") == "#455a64"
        assert app.uv_bar == [] and app.uv_bar_box is None
        app.toggle_uv_mode()
        assert app.uv_mode is True
        assert app.canvas.itemcget(app.uv_mode_btn[0], "fill") == "#7b1fa2"
        assert [b[0] for b in app.uv_bar] == ["boost", "boxes", "filter", "exit"]
        tick(app, link)
        assert "UV" in app.canvas.itemcget(app.statusbar, "text").split("    ")
        # Tapping UV MODE while on leaves UV mode (the button's own callback).
        app.canvas.event_generate("<Button-1>", x=int(app.canvas.coords(app.uv_mode_btn[0])[0]) + 5,
                                  y=int(app.canvas.coords(app.uv_mode_btn[0])[1]) + 5)
        app.root.update()
        assert app.uv_mode is False
        assert app.canvas.itemcget(app.uv_mode_btn[0], "fill") == "#455a64"
        assert app.uv_bar == []
        tick(app, link)
        assert "UV" not in app.canvas.itemcget(app.statusbar, "text").split("    ")
        # Dev key "u" in RUN.
        app.on_key(types.SimpleNamespace(keysym="u"))
        assert app.uv_mode is True
        app.on_key(types.SimpleNamespace(keysym="U"))
        assert app.uv_mode is False
    finally:
        close(app)
    print("PASS: MODE -- toggle on/off, grey/purple button, bar built/removed, "
          "status bit UV, button tap exits, dev key u")


def test_toggles_persist(mod):
    """(c) each uv_toggle flips, writes cfg['uv'] and maps onto the params;
    (d) a new App boots in normal mode with the saved toggles."""
    app, link, spy = make_app(mod, scene())
    try:
        assert app.uv_opts == {"boost": True, "boxes": True, "filter": False}
        p = app.uv_proc.params
        assert (p.boost_enabled, p.draw_boxes, p.filter_enabled, p.detect_enabled) == \
            (True, True, False, True)
        app.toggle_uv_mode()
        expect = dict(app.uv_opts)
        for name in ("boost", "boxes", "filter", "filter", "boost", "boost"):
            expect[name] = not expect[name]
            app.uv_toggle(name)
            assert app.uv_opts == expect
            assert app._video_rect()[2] == app.W
        app.uv_toggle("nonsense")                     # ignored, no crash
        assert app.uv_opts == expect
        final = dict(app.uv_opts)
    finally:
        close(app)

    # (d) restart on the same config file.
    spy_mod_cfg = json.loads(Path(mod.CONFIG).read_text(encoding="utf-8"))
    app, link, spy = make_app(mod, scene(), cfg=spy_mod_cfg)
    try:
        assert app.uv_mode is False
        assert app.uv_opts == final
        app.toggle_uv_mode()
        assert app.uv_proc.params.boost_enabled == final["boost"]
    finally:
        close(app)

    # Junk and old-revision configs fall back to sane defaults. want_panel_key
    # is R6: "uv_tuning_panel" round-trips through save_cfg only when the
    # loaded config already had it -- no setdefault ever adds it.
    rev = mod.CONFIG_REV
    for cfg, want, want_panel_key in (
            ({"config_rev": rev, "uv": {"boost": "maybe", "boxes": "off", "filter": None}},
             {"boost": True, "boxes": False, "filter": False}, False),
            ({"config_rev": rev, "uv": "garbage", "uv_tuning_panel": "no"},
             {"boost": True, "boxes": True, "filter": False}, True),
            ({"config_rev": rev - 1, "uv": {"boost": False, "boxes": False, "filter": True}},
             {"boost": True, "boxes": True, "filter": False}, False)):
        app, link, spy = make_app(mod, scene(), cfg=cfg)
        try:
            assert app.uv_mode is False and app.uv_opts == want, (cfg, app.uv_opts)
            assert app.uv_tuning_panel == (cfg.get("uv_tuning_panel") != "no")
            app.save_cfg()
            saved = json.loads(Path(mod.CONFIG).read_text(encoding="utf-8"))
            assert saved["uv"] == want
            assert ("uv_tuning_panel" in saved) == want_panel_key, saved
        finally:
            close(app)
    print("PASS: TOGGLES -- flip, persist to cfg['uv'], map to params (detect = boost or boxes), "
          "check marks; restart boots normal with saved toggles; junk/migrated config -> defaults; "
          "uv_tuning_panel never added by a setdefault (R6)")


def test_bar_clicks_and_geometry(mod):
    """(e) one canvas tap on each bar button = exactly one action; the bar
    stays clear of ZERO and of the status line at 1024x600 and 800x480."""
    app, link, spy = make_app(mod, scene())
    try:
        app.toggle_uv_mode()
        calls = []
        real = app.uv_toggle

        def counting(name):
            calls.append(name)
            real(name)
        app.uv_toggle = counting
        for name in ("boost", "boxes", "filter"):
            before = app.uv_opts[name]
            b = next(b for b in app.uv_bar if b[0] == name)
            app.canvas.event_generate("<Button-1>", x=(b[1] + b[3]) // 2, y=(b[2] + b[4]) // 2)
            app.root.update()
            assert calls.count(name) == 1, calls
            assert app.uv_opts[name] == (not before)
        # Taps outside the buttons do nothing.
        x0, y0, x1, y1 = app.uv_bar_box
        app.canvas.event_generate("<Button-1>", x=x0 + 1, y=y0 + 1)
        app.canvas.event_generate("<Button-1>", x=app.W // 2, y=app.H // 3)
        app.root.update()
        assert len(calls) == 3 and app.uv_mode
        # No picture (no signal): the labels are still there and still work.
        link.frame_time -= 10.0
        app.update()
        assert app.photo is None
        b = next(b for b in app.uv_bar if b[0] == "boost")
        assert app.canvas.itemcget(b[5], "state") in ("", "normal")
        app.canvas.event_generate("<Button-1>", x=(b[1] + b[3]) // 2, y=(b[2] + b[4]) // 2)
        app.root.update()
        assert calls.count("boost") == 2
        link.frame_time = time.monotonic()
        # EXIT UV.
        b = next(b for b in app.uv_bar if b[0] == "exit")
        app.canvas.event_generate("<Button-1>", x=(b[1] + b[3]) // 2, y=(b[2] + b[4]) // 2)
        app.root.update()
        assert app.uv_mode is False and app.uv_bar == []
        # Bar taps in normal mode do nothing.
        app.canvas.event_generate("<Button-1>", x=(b[1] + b[3]) // 2, y=(b[2] + b[4]) // 2)
        app.root.update()
        assert app.uv_mode is False and len(calls) == 4

        for geometry in ((1024, 600), (800, 480)):
            resize_to(app, *geometry)
            if not app.uv_mode:
                app.toggle_uv_mode()
            tick(app, link)
            app.root.update()
            zero = app.canvas.bbox(text_item(app, "ZERO"))
            zero_rect = next(r for r in app.canvas.find_overlapping(*zero)
                             if app.canvas.type(r) == "rectangle")
            zx0 = app.canvas.coords(zero_rect)[0]
            status_top = app.canvas.bbox(app.statusbar)[1]
            bx0, by0, bx1, by1 = app.uv_bar_box
            assert bx1 < zx0, ("bar overlaps ZERO", geometry, app.uv_bar_box, zx0)
            assert by1 <= status_top, ("bar overlaps status", geometry, app.uv_bar_box, status_top)
            assert bx0 >= 0 and by0 >= 0
            vx = app._video_rect()[0]
            assert abs((bx0 + bx1) / 2.0 - vx) <= 1.0
            for _, x0, y0, x1, y1, t in app.uv_bar:
                tb = app.canvas.bbox(t)
                assert x0 <= tb[0] and tb[2] <= x1, ("label overflows its button", geometry)
            # The bar's backgrounds really are in the picture, semi-transparent:
            # the displayed pixels under BOOST are neither the raw frame nor solid green.
            img = spy.arrays[-1]
            ih, iw = img.shape[:2]
            left, top = app.W // 2 - iw // 2, app.H // 2 - ih // 2
            b = next(b for b in app.uv_bar if b[0] == "boost")
            px = img[(b[2] + b[4]) // 2 - top, b[1] + 3 - left].astype(int)
            assert not np.array_equal(px, [0x2e, 0x7d, 0x32])
            assert px[1] > px[0] and px[1] > px[2], px          # tinted green (RGB)
    finally:
        close(app)
    print("PASS: BAR -- one tap = one toggle (3 toggles + EXIT UV), gaps/normal-mode taps "
          "ignored, works with no picture, clear of ZERO and status at 1024x600 and 800x480, "
          "labels fit, backgrounds blended into the picture")


def test_error_fallback(mod):
    """(f) a UV exception: update() survives, the unprocessed frame shows,
    one toast and one traceback per 10 s."""
    app, link, spy = make_app(mod, scene())
    try:
        tick(app, link)
        plain = spy.arrays[-1]
        app.toggle_uv_mode()
        toasts = []
        real_toast = app.toast
        app.toast = lambda text, *a, **k: (toasts.append(text), real_toast(text, *a, **k))

        def boom(*a, **k):
            raise RuntimeError("synthetic UV failure")
        app.uv_proc.process = boom
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for _ in range(3):
                tick(app, link)
        assert toasts == ["UV PROCESSING ERROR"], toasts
        assert err.getvalue().count("RuntimeError: synthetic UV failure") == 1, err.getvalue()
        shown = spy.arrays[-1]
        # Unprocessed frame (outside the bar it is pixel-identical to UV off).
        y0 = app.uv_bar_box[1] - (app.H // 2 - shown.shape[0] // 2)
        assert np.array_equal(shown[:y0 - 1], plain[:y0 - 1])

        # A failure while DRAWING falls back to the plain frame, bar and all.
        del app.uv_proc.process
        app._uv_err_t = -1e9
        app._uv_draw_regions = boom
        app._uv_paint_bar = boom
        with contextlib.redirect_stderr(io.StringIO()):
            tick(app, link)
        assert np.array_equal(spy.arrays[-1], plain)
        assert toasts == ["UV PROCESSING ERROR"] * 2
    finally:
        close(app)
    print("PASS: ERROR -- update() survives process/draw exceptions, unprocessed frame shown, "
          "toast + traceback throttled to one per 10 s")


def test_relayout_and_stages(mod):
    """(g) UV mode survives _relayout and a SETUP -> RUN round trip."""
    app, link, spy = make_app(mod, scene())
    try:
        app.toggle_uv_mode()
        resize_to(app, 800, 480)
        assert app.uv_mode and len(app.uv_bar) == 4 and app.uv_bar_box[3] < 480
        assert app.canvas.itemcget(app.uv_mode_btn[0], "fill") == "#7b1fa2"
        for t in (b[5] for b in app.uv_bar):
            assert t in app.hud
        app.set_stage(app.STAGE_SETUP)
        assert app.uv_bar == [] and app.uv_bar_box is None
        app.set_stage(app.STAGE_RUN)
        assert app.uv_mode and len(app.uv_bar) == 4
        tick(app, link)
        assert "UV" in app.canvas.itemcget(app.statusbar, "text").split("    ")
        resize_to(app, 1024, 600)
        assert app.uv_mode and len(app.uv_bar) == 4 and app.uv_bar_box[3] > 480
    finally:
        close(app)
    print("PASS: RELAYOUT/STAGES -- UV mode and its bar survive resize and SETUP -> RUN")


def test_tracker_resets(mod):
    """(h) tracker resets on frame-size change, link generation change,
    stale video, toggles and entering UV mode."""
    app, link, spy = make_app(mod, scene())
    try:
        resets = []
        real = app.uv_proc.reset
        app.uv_proc.reset = lambda: (resets.append(1), real())
        app.toggle_uv_mode()
        assert len(resets) == 1                     # entering UV
        tick(app, link)
        n = len(resets)
        tick(app, link)
        tick(app, link)
        assert len(resets) == n                     # steady state: no resets
        tick(app, link, scene(480, 640))
        assert len(resets) == n + 1                 # frame size changed
        tick(app, link)
        assert len(resets) == n + 1
        link.generation += 1
        tick(app, link)
        assert len(resets) == n + 2                 # probe restarted
        link.frame_time -= 1.0
        app.update()
        app.update()
        assert len(resets) == n + 3                 # stale (> 0.5 s), once
        tick(app, link)
        app.uv_toggle("filter")
        assert len(resets) == n + 4                 # any toggle
    finally:
        close(app)
    print("PASS: TRACKER -- reset on entering UV, size change, generation change, stale video "
          "(once), toggle; never in steady state")


def test_blob_pixels(mod):
    """(i) BOOST changes pixels inside the fluorescent region only; SMART BOX
    draws the box at display resolution."""
    frame = blob_frame()
    app, link, spy = make_app(mod, frame)
    try:
        tick(app, link)
        plain = spy.arrays[-1]
        ih, iw = plain.shape[:2]
        sx, sy = iw / 320.0, ih / 240.0
        inside = (slice(int(110 * sy), int(140 * sy)), slice(int(150 * sx), int(190 * sx)))
        far = (slice(0, int(40 * sy)), slice(0, int(60 * sx)))   # top-left, away from blob and bar

        app.toggle_uv_mode()
        app.uv_toggle("boxes")                     # boost on, boxes off, filter off
        for _ in range(3):
            tick(app, link)
        boosted = spy.arrays[-1]
        assert np.abs(boosted[inside].astype(int) - plain[inside]).mean() > 10
        # The factory tone curve (exposure 1.16 / gamma 1.27, applied even
        # with FILTER off, as in UVScope 1.1) is the only change out here.
        tone = cv2.cvtColor(cv2.resize(app.uv_proc.apply_filter(frame), (iw, ih),
                                       interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        assert np.array_equal(boosted[far], tone[far])
        assert not (boosted == [255, 0, 255]).all(axis=-1).any()   # no magenta box

        # With a neutral tone curve, out here is byte-identical to UV off.
        app._uv_base_params = lambda: mod.UVParams(exposure=1.0, gamma=1.0)
        app._uv_apply_opts()
        for _ in range(3):
            tick(app, link)
        neutral = spy.arrays[-1]
        assert np.array_equal(neutral[far], plain[far])
        assert np.abs(neutral[inside].astype(int) - plain[inside]).mean() > 10

        app.uv_toggle("boxes")                     # SMART BOX on: tracker needs 2 hits
        for _ in range(3):
            tick(app, link)
        boxed = spy.arrays[-1]
        magenta = (boxed == [255, 0, 255]).all(axis=-1)
        ys, xs = np.nonzero(magenta)
        assert len(xs) > 100, "no box drawn"
        # The box surrounds the blob (frame x 140..200, y 100..150) with at
        # most the merge dilation (merge_px 27 -> ~14 px a side) around it.
        m = 16
        assert (140 - m) * sx <= xs.min() <= 140 * sx and 200 * sx <= xs.max() <= (200 + m) * sx
        assert (100 - m) * sy <= ys.min() <= 100 * sy and 150 * sy <= ys.max() <= (150 + m) * sy
        # Thickness = round(1 * display short side / 360).
        th = max(1, int(round(1 * min(ih, iw) / 360.0)))
        row = magenta[int(125 * sy)]
        left_run = np.nonzero(row)[0]
        left_run = left_run[left_run < 170 * sx]
        assert th <= len(left_run) <= th + 1, (th, len(left_run))
    finally:
        close(app)
    print("PASS: BLOB -- boost changes the region only (outside == tone curve; == UV off with a "
          "neutral curve); SMART BOX draws a crisp display-resolution box of the scaled thickness")


def test_perf_guard(mod):
    """§3.8: 10 consecutive slow frames -> analysis_width 320, logged once,
    and it survives a later toggle."""
    app, link, spy = make_app(mod, scene(480, 640))
    try:
        app.toggle_uv_mode()
        real = app.uv_proc.process

        def slow(frame, draw=True):
            out, info = real(frame, draw=draw)
            info["ms"] = 50.0
            return out, info
        app.uv_proc.process = slow
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for i in range(9):
                tick(app, link)
            assert app.uv_proc.params.analysis_width == 640
            tick(app, link)
            assert app.uv_proc.params.analysis_width == 320
            for i in range(12):
                tick(app, link)
        assert err.getvalue().count("analysing at 320") == 1
        app.uv_toggle("filter")
        assert app.uv_proc.params.analysis_width == 320
    finally:
        close(app)
    print("PASS: PERF GUARD -- 10 frames over 35 ms -> analysis_width 320, logged once, "
          "kept across toggles")


# ------------------------------------------- tuning drawer (item 5, §3.6/§3.7)
def tab_tap(app):
    """Tap the arrow tab the way a finger does: a Button-1 on the main canvas."""
    x0, y0, x1, y1 = app.uv_tab
    app.canvas.event_generate("<Button-1>", x=int((x0 + x1) / 2), y=int((y0 + y1) / 2))
    app.root.update()


def press(widget, x, y):
    widget.event_generate("<ButtonPress-1>", x=x, y=y)


def drag(widget, x, y):
    widget.event_generate("<B1-Motion>", x=x, y=y)


def release(widget, x, y):
    widget.event_generate("<ButtonRelease-1>", x=x, y=y)


def tap(widget, x, y):
    press(widget, x, y)
    release(widget, x, y)


def body_point(app, kind, key, fx=0.5):
    """Widget coordinates of a body target, scrolling it into view first."""
    x0, y0, x1, y1 = next(h[:4] for h in app._uv_hits if h[4] == kind and h[5] == key)
    if y0 - app._uv_moved < 0 or y1 - app._uv_moved > app._uv_body_h:
        app._uv_scroll_to(y0 - 10)
    return int(x0 + (x1 - x0) * fx), int((y0 + y1) / 2 - app._uv_moved)


def head_tap(app, key):
    _, x0, y0, x1, y1 = next(h for h in app._uv_hhits if h[0] == key)
    tap(app.uv_dh, int((x0 + x1) / 2), int((y0 + y1) / 2))
    app.root.update()


def head_name(app):
    return app.uv_dh.itemcget(app._uv_name_item, "text")


def test_drawer_hidden_by_default(mod):
    app, link, spy = make_app(mod, scene())
    try:
        tick(app, link)
        assert app.uv_drawer is None and not app.canvas.find_withtag("uv_tab")   # UV off
        app.toggle_uv_mode()
        tick(app, link)
        assert app.uv_drawer_open is False and app.uv_drawer is None
        assert app.canvas.find_withtag("uv_tab"), "no arrow tab in UV mode"
        assert app._video_rect() == (app.W // 2, app.H // 2, app.W, app.H)
        # The tab sits on the right edge between the indicator panel and ZERO,
        # clear of the option bar.
        x0, y0, x1, y1 = app.uv_tab
        assert x1 == app.W and x0 < x1 and y1 < app.uv_bar_box[1]
        zero = app.canvas.bbox(text_item(app, "ZERO"))
        assert y1 < zero[1] and y0 > int(min(app.W, app.H) * 0.33)
        assert y1 - y0 >= 0.13 * app.H and x1 - x0 >= 0.066 * app.H     # finger sized
    finally:
        close(app)
    rev = mod.CONFIG_REV
    app, link, spy = make_app(mod, scene(), cfg={"config_rev": rev, "uv_tuning_panel": False})
    try:
        app.toggle_uv_mode()
        tick(app, link)
        assert not app.canvas.find_withtag("uv_tab")
        app.toggle_uv_drawer()
        assert app.uv_drawer_open is False and app.uv_drawer is None
        saved = json.loads(Path(mod.CONFIG).read_text(encoding="utf-8")) \
            if os.path.exists(mod.CONFIG) else {}
        assert saved.get("uv_tuning_panel", False) is False
    finally:
        close(app)
    print("PASS: DRAWER HIDDEN -- no tab in normal mode, closed tab between indicator and ZERO "
          "in UV mode, nothing at all when uv_tuning_panel is false")


def test_drawer_open_close(mod):
    app, link, spy = make_app(mod, scene())
    try:
        for geometry in ((1024, 600), (800, 480)):
            resize_to(app, *geometry)
            if not app.uv_mode:
                app.toggle_uv_mode()
            tick(app, link)
            opts = dict(app.uv_opts)
            W, H = app.W, app.H
            tab_tap(app)
            assert app.uv_drawer_open and app.uv_drawer is not None, geometry
            assert app.uv_opts == opts, "a tab tap also hit the option bar"
            tab_w, panel_w = app._uv_drawer_dims()
            assert panel_w >= 300 and W - panel_w - tab_w >= 0.45 * W - 1
            app.root.update()
            assert app.uv_drawer.winfo_x() == W - panel_w
            assert app.uv_drawer.winfo_width() == panel_w and app.uv_drawer.winfo_height() == H
            cx, cy, aw, ah = app._video_rect()
            assert (cx, cy, aw, ah) == ((W - panel_w - tab_w) // 2, H // 2, W - panel_w - tab_w, H)
            # The whole picture is displayed left of the drawer and its tab.
            tick(app, link)
            img = spy.arrays[-1]
            vx, vy = app.canvas.coords(app.video_item)
            left, right = vx - img.shape[1] // 2, vx - img.shape[1] // 2 + img.shape[1]
            assert (vx, vy) == (cx, cy) and left >= 0 and right <= aw, (left, right, aw)
            assert img.shape[1] == aw or img.shape[0] == H     # still fitted, not cropped
            # The tab moved to the drawer's left edge, pointing right.
            x0, y0, x1, y1 = app.uv_tab
            assert x1 == W - panel_w and x0 >= aw
            assert "\u25b6" in [app.canvas.itemcget(i, "text") for i in app.canvas.find_withtag("uv_tab")
                                if app.canvas.type(i) == "text"]
            # The bar is re-centred on the picture and stays left of the drawer.
            bx0, by0, bx1, by1 = app.uv_bar_box
            assert abs((bx0 + bx1) / 2.0 - cx) <= 1.0 and bx0 >= 0 and bx1 < x0
            # Close again: everything back where UV mode had it.
            tab_tap(app)
            assert not app.uv_drawer_open and app.uv_drawer is None
            assert app._video_rect() == (W // 2, H // 2, W, H)
            assert abs((app.uv_bar_box[0] + app.uv_bar_box[2]) / 2.0 - W // 2) <= 1.0
            assert app.uv_tab[2] == W and app.uv_opts == opts
    finally:
        close(app)
    print("PASS: DRAWER OPEN/CLOSE -- tab toggles it (without hitting the bar), picture refitted "
          "and wholly visible left of the drawer, bar re-centred, tab moves, at 1024x600 and 800x480")


def test_drawer_edits(mod):
    app, link, spy = make_app(mod, blob_frame())
    try:
        app.toggle_uv_mode()
        tick(app, link)
        tab_tap(app)
        resets = []
        real = app.uv_proc.reset
        app.uv_proc.reset = lambda: (resets.append(1), real())
        assert head_name(app) == "FACTORY"
        body = app.uv_db
        # + / - taps: one slider notch each, applied live, "*" marker.
        x, y = body_point(app, "plus", "exposure")
        tap(body, x, y)
        assert abs(app.uv_work.exposure - 1.17) < 1e-9, app.uv_work.exposure
        assert abs(app.uv_proc.params.exposure - 1.17) < 1e-9
        assert head_name(app) == "FACTORY *" and app.last_seq == -1
        assert body.itemcget(app._uv_rows["exposure"]["value"], "text") == "1.17"
        x, y = body_point(app, "minus", "exposure")
        tap(body, x, y)
        tap(body, x, y)
        assert abs(app.uv_work.exposure - 1.15) < 1e-9
        x, y = body_point(app, "plus", "exposure")
        tap(body, x, y)
        assert app.uv_work.exposure == 1.16 and head_name(app) == "FACTORY"   # back to clean
        x, y = body_point(app, "plus", "hue_min")
        tap(body, x, y)
        assert app.uv_work.hue_min == 43 and isinstance(app.uv_work.hue_min, int)
        x, y = body_point(app, "plus", "min_area_ratio")
        tap(body, x, y)
        assert abs(app.uv_work.min_area_ratio - 0.00200) < 1e-12
        assert body.itemcget(app._uv_rows["min_area_ratio"]["value"], "text") == "0.200"
        # A tap on the track jumps there; the ends are the UVScope range.
        tx0, tx1 = app._uv_rows["merge_px"]["tx"]
        _, y = body_point(app, "track", "merge_px")
        tap(body, tx1 + 5, y)
        assert app.uv_work.merge_px == 41
        # A sideways drag that starts on the track drags the value and keeps
        # dragging it whatever the finger does next.
        scroll0 = app._uv_scroll
        press(body, tx1 - 2, y)
        drag(body, tx1 - 40, y + 2)
        assert app.uv_work.merge_px < 41
        drag(body, tx0 - 5, y + 30)
        assert app.uv_work.merge_px == 0 and app._uv_scroll == scroll0
        drag(body, (tx0 + tx1) // 2, y)
        assert abs(app.uv_work.merge_px - 20) <= 1
        release(body, (tx0 + tx1) // 2, y)
        assert app.uv_proc.params.merge_px == app.uv_work.merge_px
        # One that starts on the track but goes up/down first is a scroll.
        mid = app.uv_work.merge_px
        _, y = body_point(app, "track", "merge_px")
        scroll0 = app._uv_scroll
        press(body, tx0 + 3, y)
        drag(body, tx0 + 6, y - 40)
        release(body, tx0 + 6, y - 40)
        assert app.uv_work.merge_px == mid and app._uv_scroll == scroll0 + 40
        # Toggle and cycle rows.
        x, y = body_point(app, "toggle", "show_labels")
        tap(body, x, y)
        assert app.uv_work.show_labels is True and app.uv_proc.params.show_labels is True
        x, y = body_point(app, "cycle", "box_bgr")
        tap(body, x, y)
        assert tuple(app.uv_work.box_bgr) == (0, 255, 0)
        x, y = body_point(app, "cycle", "dye")
        tap(body, x, y)
        for k, v in mod.UV_DYE_PRESETS["yellow_green"]["params"].items():
            assert getattr(app.uv_work, k) == v, k
        tap(body, *body_point(app, "cycle", "dye"))
        assert app.uv_work.hue_min == mod.UV_DYE_PRESETS["green"]["params"]["hue_min"]
        assert resets == [], "a drawer edit reset the tracker"
        # Bar toggles still own the four enable switches.
        assert app.uv_proc.params.boost_enabled == app.uv_opts["boost"]
        # Edits really reach the picture: green boxes now.
        for _ in range(3):
            tick(app, link)
        assert (spy.arrays[-1] == [0, 255, 0]).all(axis=-1).sum() > 50
        assert app.uv_dh.itemcget(app._uv_ms_item, "text").endswith("ms")
    finally:
        close(app)
    print("PASS: DRAWER EDITS -- -/+ step one notch (ints 1, 2-dec 0.01, min region 0.001%), "
          "track drag sets the value, toggles and cycles, live apply without tracker resets, "
          "'*' marker comes and goes")


def test_drawer_scroll(mod):
    app, link, spy = make_app(mod, scene())
    try:
        resize_to(app, 800, 480)
        app.toggle_uv_mode()
        tab_tap(app)
        body = app.uv_db
        assert app._uv_content_h > app._uv_body_h * 2
        before = app.uv_work.to_dict()
        # A finger drag that starts on a label (no target) scrolls.
        y = app._uv_body_h - 20
        press(body, 30, y)
        drag(body, 30, y - 4)                 # inside the slop: nothing yet
        assert app._uv_scroll == 0
        drag(body, 30, y - 150)
        assert app._uv_scroll == 150, app._uv_scroll
        release(body, 30, y - 150)
        # A drag that starts on a +/- button scrolls too and never presses it.
        x, yb = body_point(app, "plus", "sat_min")
        s0 = app._uv_scroll
        press(body, x, yb)
        drag(body, x, yb - 60)
        release(body, x, yb - 60)
        assert app._uv_scroll == s0 + 60
        assert app.uv_work.to_dict() == before, "scrolling changed a value"
        # Clamped at both ends.
        press(body, 30, 100)
        drag(body, 30, 100 + 10000)
        release(body, 30, 100 + 10000)
        assert app._uv_scroll == 0
        press(body, 30, 300)
        drag(body, 30, 300 - 10000)
        release(body, 30, 300 - 10000)
        assert app._uv_scroll == app._uv_content_h - app._uv_body_h
        # Mouse wheel (development only).
        body.event_generate("<MouseWheel>", delta=120, x=30, y=100)
        assert app._uv_scroll < app._uv_content_h - app._uv_body_h
        s1 = app._uv_scroll
        body.event_generate("<Button-5>", x=30, y=100)
        assert app._uv_scroll > s1
        assert app.uv_work.to_dict() == before
        # Relayout keeps the drawer open and the scroll position (clamped).
        app._uv_scroll_to(200)
        resize_to(app, 1024, 600)
        assert app.uv_drawer_open and app.uv_drawer is not None
        app.root.update()
        assert app.uv_drawer.winfo_width() == app._uv_drawer_dims()[1]
        assert app._uv_scroll == min(200, app._uv_content_h - app._uv_body_h)
        # A stage change destroys it; coming back to RUN rebuilds it.
        app.set_stage(app.STAGE_SETUP)
        assert app.uv_drawer is None and app._video_rect()[2] == app.W
        app.set_stage(app.STAGE_RUN)
        assert app.uv_drawer is not None
        # Leaving UV mode closes it for good.
        app.toggle_uv_mode()
        assert app.uv_drawer is None and not app.uv_drawer_open
        assert not app.canvas.find_withtag("uv_tab")
        app.toggle_uv_mode()
        assert app.uv_drawer is None and not app.uv_drawer_open
    finally:
        close(app)
    print("PASS: DRAWER SCROLL -- finger drag scrolls (8 px slop, clamped), never changes a "
          "value; wheel scrolls; relayout keeps it; stage change / UV exit close it")


def test_presets(mod):
    app, link, spy = make_app(mod, scene())
    path = Path(mod.UV_PRESETS_FILE)
    try:
        app.toggle_uv_mode()
        tab_tap(app)
        body = app.uv_db
        tap(body, *body_point(app, "plus", "exposure"))
        assert head_name(app) == "FACTORY *"
        head_tap(app, "save")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["presets"]) == 1 and data["active"] == data["presets"][0]["id"]
        first = data["presets"][0]
        assert re.fullmatch(r"\d\d:\d\d \d\d-\d\d", first["name"]), first["name"]
        assert abs(first["params"]["exposure"] - 1.17) < 1e-9
        assert app.uv_active_id == first["id"] and head_name(app) == first["name"]
        toast = [app.canvas.itemcget(i, "text") for i in app.toast_items
                 if app.canvas.type(i) == "text"]
        assert toast == ["SAVED " + first["name"]], toast
        # The toast is centred on the picture, not under the drawer.
        tx = app.canvas.coords(app.toast_items[1])[0]
        assert abs(tx - app._video_rect()[0]) <= 1
        # A second SAVE is always a NEW preset, listed first.
        tap(body, *body_point(app, "plus", "gamma"))
        head_tap(app, "save")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["presets"]) == 2
        second = data["presets"][0]
        assert second["id"] != first["id"] and data["active"] == second["id"]
        if second["name"][:11] == first["name"]:
            assert second["name"] == first["name"] + " (2)"
        # Preset list: FACTORY first, then newest first; tap one to load it.
        head_tap(app, "list")
        assert app._uv_list_open
        ids = [h[5] for h in app._uv_hits if h[4] == "preset"]
        assert ids == ["factory", second["id"], first["id"]], ids
        resets = []
        real = app.uv_proc.reset
        app.uv_proc.reset = lambda: (resets.append(1), real())
        tap(body, *body_point(app, "preset", "factory"))
        assert not app._uv_list_open and app.uv_active_id == "factory"
        assert app.uv_work == mod.UVParams() and head_name(app) == "FACTORY"
        assert resets, "a preset switch must restart the tracker"
        assert json.loads(path.read_text(encoding="utf-8"))["active"] == "factory"
        # Unsaved edits are dropped by a switch.
        tap(body, *body_point(app, "plus", "warmth"))
        assert head_name(app) == "FACTORY *"
        head_tap(app, "list")
        tap(body, *body_point(app, "preset", first["id"]))
        assert app.uv_active_id == first["id"]
        assert app.uv_work == mod.UVParams.from_dict(first["params"])
        assert abs(app.uv_work.warmth - 0.15) < 1e-9
        tap(body, *body_point(app, "plus", "warmth"))        # unsaved again ...
    finally:
        close(app)

    # ... and a restart forgets them, but keeps the active preset.
    app, link, spy = make_app(mod, scene(), keep_presets=True)
    try:
        assert app.uv_active_id == first["id"]
        assert app.uv_work == mod.UVParams.from_dict(first["params"])
        assert abs(app.uv_proc.params.exposure - 1.17) < 1e-9
        assert [p["id"] for p in app.uv_presets["presets"]] == [second["id"], first["id"]]
        app.toggle_uv_mode()
        tab_tap(app)
        # DELETE: one tap arms it, a second within 3 s deletes.
        rect, text = app._uv_hbtn["delete"]
        head_tap(app, "delete")
        assert app.uv_dh.itemcget(text, "text") == "CONFIRM?"
        assert app.uv_dh.itemcget(rect, "fill") == "#c62828"
        assert len(app.uv_presets["presets"]) == 2
        app._uv_del_until = time.monotonic() - 0.01           # 3 s went by
        head_tap(app, "delete")                                # re-arms only
        assert len(app.uv_presets["presets"]) == 2
        assert app.uv_dh.itemcget(text, "text") == "CONFIRM?"
        head_tap(app, "delete")
        assert [p["id"] for p in app.uv_presets["presets"]] == [second["id"]]
        assert app.uv_active_id == second["id"]                 # newest remaining
        assert app.uv_dh.itemcget(text, "text") == "DELETE"
        data = json.loads(path.read_text(encoding="utf-8"))
        assert [p["id"] for p in data["presets"]] == [second["id"]]
        assert data["active"] == second["id"]
        head_tap(app, "delete")
        head_tap(app, "delete")
        assert app.uv_presets["presets"] == [] and app.uv_active_id == "factory"
        # FACTORY cannot be deleted: greyed, and taps do nothing.
        assert app.uv_dh.itemcget(text, "fill") == mod.MUTED
        head_tap(app, "delete")
        head_tap(app, "delete")
        assert app.uv_active_id == "factory"
        assert json.loads(path.read_text(encoding="utf-8")) == {
            "version": 1, "active": "factory", "presets": []}
        # EXPORT: FACTORY + bundle into the (redirected) export folder.
        # R1: the write happens on a daemon thread now -- wait_export pumps
        # the Tk loop until the root.after(150) poll reports it is done.
        export = Path(mod.uv_find_export_dir()[0])
        head_tap(app, "save")
        head_tap(app, "export")
        wait_export(app)
        files = sorted(p.name for p in export.iterdir())
        assert len([f for f in files if f.startswith("uv_params_")]) >= 2, files
        assert any(f.endswith("_FACTORY.json") for f in files)
        assert any(f.startswith("uv_presets_all_") for f in files)
        toast = [app.canvas.itemcget(i, "text") for i in app.toast_items
                 if app.canvas.type(i) == "text"]
        assert toast and toast[0].startswith("EXPORTED 3 FILES"), toast
        # A failing export is a toast, never an exception into Tk: the
        # OSError happens on the worker thread, and the poll (Tk thread)
        # turns it into the toast.
        real_export = mod.uv_export
        mod.uv_export = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                head_tap(app, "export")
                wait_export(app)
        finally:
            mod.uv_export = real_export
        toast = [app.canvas.itemcget(i, "text") for i in app.toast_items
                 if app.canvas.type(i) == "text"]
        assert toast == ["EXPORT FAILED"], toast
    finally:
        close(app)

    # A corrupt presets file is FACTORY, not a crash.
    path.write_text("{ not json", encoding="utf-8")
    app, link, spy = make_app(mod, scene(), keep_presets=True)
    try:
        assert app.uv_active_id == "factory" and app.uv_work == mod.UVParams()
        app.toggle_uv_mode()
        tab_tap(app)
        assert head_name(app) == "FACTORY"
    finally:
        close(app)
    print("PASS: PRESETS -- SAVE makes a new active preset (atomic file, toast on the picture), "
          "list FACTORY + newest first, switch drops edits and resets tracker, restart keeps "
          "the active preset, DELETE needs two taps in 3 s, FACTORY undeletable, EXPORT writes "
          "files / fails as a toast, corrupt file -> FACTORY")


def test_diag_hides_bar(mod):
    """Legacy DIAG grid: bar labels (and their plain backgrounds) hidden and
    the bar not tappable while it is up; back as soon as it is gone."""
    app, link, spy = make_app(mod, scene())
    try:
        app.toggle_uv_mode()
        tick(app, link)
        app.diag_photo = scene(240, 320, seed=1)
        tick(app, link)
        for b in app.uv_bar:
            assert app.canvas.itemcget(b[5], "state") == "hidden"
        for r in app.uv_bar_bg:
            assert app.canvas.itemcget(r, "state") == "hidden"
        b = next(b for b in app.uv_bar if b[0] == "boost")
        before = dict(app.uv_opts)
        app.canvas.event_generate("<Button-1>", x=(b[1] + b[3]) // 2, y=(b[2] + b[4]) // 2)
        app.root.update()
        assert app.uv_opts == before
        app.diag_photo = None
        tick(app, link)
        for b in app.uv_bar:
            assert app.canvas.itemcget(b[5], "state") == "normal"
        b = next(b for b in app.uv_bar if b[0] == "boost")
        app.canvas.event_generate("<Button-1>", x=(b[1] + b[3]) // 2, y=(b[2] + b[4]) // 2)
        app.root.update()
        assert app.uv_opts["boost"] != before["boost"]
    finally:
        close(app)
    print("PASS: DIAG -- bar hidden and inert under the DIAG grid, restored after")


# ------------------------------------------- review fixes R1/R3/R5/R7/R8 (item 7b)
def test_save_cfg_atomic(mod):
    """R3: save_cfg writes through _uv_write_json (temp file + os.replace),
    so a failure mid-write (the rename itself) leaves the previous
    endoscope.json untouched -- never half-written -- and save_cfg's own
    try/except still swallows the failure instead of raising into Tk."""
    app, link, spy = make_app(mod, scene())
    try:
        app.save_cfg()
        before = Path(mod.CONFIG).read_bytes()
        real_replace = mod.os.replace
        mod.os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError("power cut"))
        try:
            app.video_flip_h = True      # a real change that would be written
            app.save_cfg()               # must not raise
        finally:
            mod.os.replace = real_replace
        assert Path(mod.CONFIG).read_bytes() == before, "failed rename must keep the old file"
        leftovers = [p for p in Path(mod.CONFIG).parent.iterdir()
                    if p.name.startswith(Path(mod.CONFIG).name + ".")]
        assert leftovers == [], leftovers
        # A normal save (no fault injected) really does persist the change.
        app.save_cfg()
        assert json.loads(Path(mod.CONFIG).read_text(
            encoding="utf-8"))["video_flip_h"] is True
    finally:
        close(app)
    print("PASS: SAVE CFG ATOMIC -- a failed rename leaves endoscope.json intact, no temp "
          "file left behind, and a normal save still persists")


def test_tuning_panel_default_not_sticky(mod):
    """R6: uv_tuning_panel is read with .get(..., UV_TUNING_PANEL_DEFAULT),
    never setdefault -- a config that never mentions the key must stay that
    way after boot and after a save, so a later release build can flip the
    module constant and have it take effect on every such config instead of
    a True frozen in by an earlier boot."""
    app, link, spy = make_app(mod, scene())
    try:
        assert "uv_tuning_panel" not in app.cfg
        assert app.uv_tuning_panel is mod.UV_TUNING_PANEL_DEFAULT is True
        app.save_cfg()
        saved = json.loads(Path(mod.CONFIG).read_text(encoding="utf-8"))
        assert "uv_tuning_panel" not in saved, (
            "save_cfg must not freeze the default into the config file")
    finally:
        close(app)
    # An explicit key in the file (the engineer hiding the drawer by hand,
    # or a release build's installer dropping one in) is still honoured.
    app, link, spy = make_app(mod, scene(),
                              cfg={"config_rev": mod.CONFIG_REV, "uv_tuning_panel": False})
    try:
        assert app.uv_tuning_panel is False
    finally:
        close(app)
    print("PASS: TUNING PANEL DEFAULT -- not written by setdefault (a release build's module "
          "constant alone controls it for untouched configs), an explicit key is honoured")


def test_export_async(mod):
    """R1: EXPORT's write happens off the Tk thread -- App.update() must
    return immediately even while the write is artificially slow, a second
    tap while busy is EXPORT BUSY and does not start a second write, and a
    worker exception becomes a toast instead of an unhandled thread crash."""
    app, link, spy = make_app(mod, scene())
    try:
        app.toggle_uv_mode()
        tab_tap(app)
        gate = threading.Event()
        calls = []
        real_export = mod.uv_export

        def slow_export(*a, **k):
            calls.append(1)
            gate.wait(2.0)              # stands in for a stalled USB write
            return real_export(*a, **k)
        mod.uv_export = slow_export
        try:
            head_tap(app, "export")
            assert app._uv_export_busy
            toast = [app.canvas.itemcget(i, "text") for i in app.toast_items
                     if app.canvas.type(i) == "text"]
            assert toast == ["EXPORTING…"], toast
            # The per-frame loop must not be the one blocked on the gate --
            # the worker thread is.
            t0 = time.monotonic()
            tick(app, link)
            assert time.monotonic() - t0 < 0.5, "update() blocked on the export"
            # A second tap while busy is ignored: a toast, no second write.
            head_tap(app, "export")
            toast = [app.canvas.itemcget(i, "text") for i in app.toast_items
                     if app.canvas.type(i) == "text"]
            assert toast == ["EXPORT BUSY"], toast
            assert calls == [1]
            gate.set()
            wait_export(app)
            toast = [app.canvas.itemcget(i, "text") for i in app.toast_items
                     if app.canvas.type(i) == "text"]
            assert toast and toast[0].startswith("EXPORTED"), toast
            assert not app._uv_export_busy
        finally:
            mod.uv_export = real_export
            gate.set()
    finally:
        close(app)
    print("PASS: EXPORT ASYNC -- update() never blocks on a slow write, a tap while busy is "
          "EXPORT BUSY (no second write started), the result toast lands once the worker is done")


def test_item_bind_leak_bounded(mod):
    """R5: button() and _uv_build_tab's tag_bind commands must not leak.
    Every UV toggle and every drawer open/close goes through set_stage,
    which rebuilds every button; 300 cycles must leave the Tcl interpreter's
    command table bounded, not growing with the iteration count."""
    app, link, spy = make_app(mod, scene())
    try:
        before = len(app.canvas._tclCommands)
        for _ in range(300):
            app.toggle_uv_mode()
            tab_tap(app)             # opens the drawer (arrow tab)
            tab_tap(app)             # closes it again
            app.toggle_uv_mode()
        after = len(app.canvas._tclCommands)
        assert after - before < 200, (
            "canvas._tclCommands grew by {} over 300 cycles -- tag_bind leak"
            .format(after - before))
    finally:
        close(app)
    print("PASS: ITEM BIND LEAK -- 300 UV toggle / drawer open-close cycles keep "
          "canvas._tclCommands bounded")


def test_uv_mode_needs_usb_camera(mod):
    """R7: toggle_uv_mode refuses to ENTER UV mode on the legacy serial
    link (no clean_video): a WARN toast, no state change, and the dev key
    goes through the same refusal. EXIT must still work regardless."""
    if os.path.exists(mod.CONFIG):
        os.remove(mod.CONFIG)
    presets = getattr(mod, "UV_PRESETS_FILE", None)
    if presets and os.path.exists(presets):
        os.remove(presets)
    link = StaticLink(scene())
    link.is_uvc = False                 # the legacy serial probe, not UVC
    spy = ImageSpy()
    mod.Image = spy
    app = mod.App(link, make_args())
    app.root.update()
    try:
        assert app.clean_video is False
        app.toggle_uv_mode()
        assert app.uv_mode is False
        toast = [app.canvas.itemcget(i, "text") for i in app.toast_items
                 if app.canvas.type(i) == "text"]
        assert toast == ["UV MODE NEEDS THE USB CAMERA"], toast
        # The dev key 'u' (RUN only) is refused the same way.
        app.on_key(types.SimpleNamespace(keysym="u"))
        assert app.uv_mode is False
        # EXIT must always work, even if UV mode were somehow already on
        # (e.g. a link swap after entry).
        app.uv_mode = True
        app.toggle_uv_mode()
        assert app.uv_mode is False
    finally:
        close(app)
    print("PASS: UV NEEDS USB CAMERA -- entering UV mode on the legacy serial link is refused "
          "(toast, no state change, dev key too); EXIT always works")


def test_small_portrait_geometry(mod):
    """R8: tiny/portrait screens must never raise, and must keep the bottom
    option bar and every open drawer slider track on-screen and usable."""
    app, link, spy = make_app(mod, scene())
    try:
        for w, h in ((640, 480), (480, 800)):
            resize_to(app, w, h)
            app.toggle_uv_mode()
            tick(app, link)
            tab_tap(app)                 # open the drawer
            tick(app, link)
            pad = max(10, app.H // 46)
            box = app.uv_bar_box
            assert box is not None
            assert box[0] >= pad and box[2] <= app.W - pad, (w, h, box)
            for field, row in app._uv_rows.items():
                tx = row.get("tx")
                if tx is not None:
                    assert tx[1] >= tx[0] + 20, (w, h, field, tx)
            app.toggle_uv_mode()          # back off before the next geometry
            tick(app, link)
    finally:
        close(app)
    print("PASS: SMALL/PORTRAIT GEOMETRY -- 640x480 (drawer open) and 480x800 run with no "
          "exceptions, the bottom bar stays inside [pad, W-pad], every slider track >= 20 px")


# ------------------------------------------- second-round fixes (item 7d)
def test_uv_init_guard(mod):
    """S1: a UV-init failure that nothing lower in the stack catches
    (simulated here by making uv_presets_load itself explode, then by
    making the UVProcessor constructor explode) must never crash
    App.__init__ -- the app still boots, UV off with FACTORY defaults
    exactly like a first run, the traceback lands on stderr, and UV still
    works normally afterwards."""
    if os.path.exists(mod.CONFIG):
        os.remove(mod.CONFIG)
    presets_path = getattr(mod, "UV_PRESETS_FILE", None)
    if presets_path and os.path.exists(presets_path):
        os.remove(presets_path)
    real_load = mod.uv_presets_load
    mod.uv_presets_load = lambda path: (_ for _ in ()).throw(
        RuntimeError("simulated UV-init failure"))
    spy = ImageSpy()
    mod.Image = spy
    link = StaticLink(scene())
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            app = mod.App(link, make_args())            # must not raise
        app.root.update()
    finally:
        mod.uv_presets_load = real_load
    try:
        assert app.uv_mode is False
        assert app.uv_opts == dict(mod.UV_OPT_DEFAULTS), app.uv_opts
        assert app.uv_tuning_panel is mod.UV_TUNING_PANEL_DEFAULT
        assert app.uv_active_id == mod.UV_FACTORY_ID
        assert app.uv_presets == {"version": 1, "active": mod.UV_FACTORY_ID, "presets": []}
        assert isinstance(app.uv_proc, mod.UVProcessor)
        assert "simulated UV-init failure" in err.getvalue()
        # UV still works normally after falling back.
        app.toggle_uv_mode()
        tick(app, link)
        assert app.uv_mode is True
    finally:
        close(app)
    # A second, independent failure point: constructing the processor
    # itself (not the presets loader) also falls back instead of raising.
    if os.path.exists(mod.CONFIG):
        os.remove(mod.CONFIG)
    if presets_path and os.path.exists(presets_path):
        os.remove(presets_path)
    real_proc = mod.UVProcessor
    calls = []

    def flaky_proc(params):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated processor-init failure")
        return real_proc(params)
    mod.UVProcessor = flaky_proc
    link2 = StaticLink(scene())
    err2 = io.StringIO()
    try:
        with contextlib.redirect_stderr(err2):
            app2 = mod.App(link2, make_args())           # must not raise
        app2.root.update()
    finally:
        mod.UVProcessor = real_proc
    try:
        assert isinstance(app2.uv_proc, real_proc)
        assert "simulated processor-init failure" in err2.getvalue()
    finally:
        close(app2)
    print("PASS: UV INIT GUARD -- a UV-init exception (presets loader or processor "
          "construction) never crashes App.__init__, boots to FACTORY/UV-off exactly like a "
          "first run, logs the traceback, and UV still works normally afterwards")


def test_bar_two_rows_clear_of_zero(mod):
    """S3: a screen too narrow for one row beside ZERO, even off-centre and
    at the smallest size/padding tried, drops to two rows of two instead of
    the old clamp (which bounded the bar to W, not to ZERO's own left
    edge) silently letting it overlap ZERO. 480x800 is such a screen --
    test_bar_clicks_and_geometry already pins the single-row case at
    1024x600/800x480, unaffected by any of this."""
    app, link, spy = make_app(mod, scene())
    try:
        resize_to(app, 480, 800)
        app.toggle_uv_mode()
        tick(app, link)
        zero = app.canvas.bbox(text_item(app, "ZERO"))
        zero_rect = next(r for r in app.canvas.find_overlapping(*zero)
                         if app.canvas.type(r) == "rectangle")
        zx0, zy0, zx1, zy1 = app.canvas.coords(zero_rect)
        bx0, by0, bx1, by1 = app.uv_bar_box
        # The fallback actually engaged: a single row of four could not
        # have been built this short at 480 px wide.
        row_spans = sorted(set((y0, y1) for _, _, y0, _, y1, _ in app.uv_bar))
        assert len(row_spans) == 2, ("expected two rows of two", app.uv_bar)
        assert bx1 <= zx0 or zx1 <= bx0 or by1 <= zy0 or zy1 <= by0, (
            "bar overlaps ZERO", app.uv_bar_box, app.canvas.coords(zero_rect))
        assert bx0 >= 0 and by0 >= 0
        for _, x0, y0, x1, y1, t in app.uv_bar:
            tb = app.canvas.bbox(t)
            assert x0 <= tb[0] and tb[2] <= x1, ("label overflows its button", app.uv_bar)
        # Every button still works, one tap each.
        calls = []
        real = app.uv_toggle
        app.uv_toggle = lambda name: (calls.append(name), real(name))[-1]
        for name in ("boost", "boxes", "filter"):
            b = next(b for b in app.uv_bar if b[0] == name)
            app.canvas.event_generate("<Button-1>", x=(b[1] + b[3]) // 2, y=(b[2] + b[4]) // 2)
            app.root.update()
        assert calls == ["boost", "boxes", "filter"], calls
    finally:
        close(app)
    print("PASS: BAR TWO ROWS -- 480x800 cannot fit one row beside ZERO at any size, drops to "
          "two rows of two instead, still clear of ZERO, labels fit, every button still works")


def test_track_hit_matches_drawn(mod):
    """S4: the drawn track's extent and its hit rect must come from the
    same variables -- on a narrow drawer (480x800) the -/+ buttons now
    shrink (R8's own fix forced the DRAWN rectangle to a 20 px floor
    without ever touching the "between the buttons" hit-test it forced its
    way past) so every point across the drawn track really does hit-test
    as "track", never drifting into "plus"."""
    app, link, spy = make_app(mod, scene())
    try:
        resize_to(app, 480, 800)
        app.toggle_uv_mode()
        tick(app, link)
        tab_tap(app)
        tick(app, link)
        checked = 0
        for field, row in app._uv_rows.items():
            tx = row.get("tx")
            if tx is None:
                continue
            tx0, tx1 = tx
            assert tx1 - tx0 >= 20, (field, tx)          # R8's own floor, unaffected
            x0, y0, x1, y1 = next(h[:4] for h in app._uv_hits
                                  if h[4] == "track" and h[5] == field)
            if y0 - app._uv_moved < 0 or y1 - app._uv_moved > app._uv_body_h:
                app._uv_scroll_to(y0 - 10)
            cy = int((y0 + y1) / 2 - app._uv_moved)
            for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
                x = int(round(tx0 + (tx1 - tx0) * frac))
                hit = app._uv_body_target(x, cy)
                assert hit == ("track", field), (field, x, cy, hit)
                checked += 1
        assert checked > 0
    finally:
        close(app)
    print("PASS: TRACK HIT MATCHES DRAWN (S4) -- every point across the drawn track hit-tests "
          "as 'track' at 480x800, never drifting into the +/- buttons' own hit rects")


def test_body_tap_needs_slop(mod):
    """S5: a body tap (track jump / preset / toggle / cycle / +-) only
    acts if release lands back over the same target within UV_DRAG_SLOP of
    the press point -- a fast swipe with no intervening motion event (so
    "mode" never left "tap") used to act anyway, wherever release landed."""
    app, link, spy = make_app(mod, scene())
    try:
        app.toggle_uv_mode()
        tab_tap(app)
        body = app.uv_db
        x, y = body_point(app, "toggle", "uv_reject")
        slop = mod.UV_DRAG_SLOP
        # Press, then "teleport" straight to a release far outside the
        # target with NO motion event in between -- exactly what a fast
        # swipe looks like to Tk when the finger skips several pixels.
        press(body, x, y)
        release(body, x + slop * 6, y)
        app.root.update()
        assert app.uv_work.uv_reject == mod.UV_FACTORY["uv_reject"], (
            "a swipe-like release far from the press point must not toggle")
        # The same press, released back within slop, still acts.
        press(body, x, y)
        release(body, x + slop - 1, y + slop - 1)
        app.root.update()
        assert app.uv_work.uv_reject != mod.UV_FACTORY["uv_reject"]
    finally:
        close(app)
    print("PASS: BODY TAP NEEDS SLOP -- a press/release pair with no motion event only acts "
          "when release is still within UV_DRAG_SLOP of the press point and over the same "
          "target; a fast swipe past that is ignored")


def test_quit_waits_for_export(mod):
    """S9: EXIT mid-export toasts FINISHING EXPORT... and joins the worker
    (up to 3 s) before destroying anything, instead of abandoning it to
    race the 4 s exit watchdog with no feedback to the operator."""
    app, link, spy = make_app(mod, scene())
    real_exit = mod.os._exit
    mod.os._exit = lambda code=0: None    # neutralise quit()'s 4 s watchdog
    try:
        app.toggle_uv_mode()
        tab_tap(app)
        gate = threading.Event()
        started = threading.Event()
        real_export = mod.uv_export

        def slow_export(*a, **k):
            started.set()
            gate.wait(2.0)
            return real_export(*a, **k)
        mod.uv_export = slow_export
        try:
            head_tap(app, "export")
            assert started.wait(2.0), "export worker never started"
            assert app._uv_export_busy
            thread = app._uv_export_thread
            assert thread is not None and thread.is_alive()
            real_destroy = app.root.destroy
            app.root.destroy = lambda: None   # inspect app state after quit()
            gate.set()                        # let the write finish quickly
            t0 = time.monotonic()
            app.quit()
            dt = time.monotonic() - t0
            app.root.destroy = real_destroy
            assert dt < 2.5, ("quit() took too long", dt)
            texts = [app.canvas.itemcget(i, "text") for i in app.toast_items
                     if app.canvas.type(i) == "text"]
            assert texts == ["FINISHING EXPORT…"], texts
            thread.join(2.0)
            assert not thread.is_alive()
            assert app._exiting is True
        finally:
            mod.uv_export = real_export
            gate.set()
    finally:
        mod.os._exit = real_exit
        close(app)
    print("PASS: QUIT WAITS FOR EXPORT -- EXIT mid-export toasts FINISHING EXPORT..., joins "
          "the worker thread before destroying anything, and still returns promptly")


def main():
    import tkinter as tk
    try:
        probe = tk.Tk()
        probe.destroy()
    except tk.TclError as exc:
        print("SKIPPED: test_uv_ui.py needs a display for Tk ({})".format(exc))
        return
    new = load_module(HERE / "endoscope.py", "endoscope_uv_ui_test")
    old = load_pristine_610()
    test_uv_off_identity(new, old)
    test_mode_toggle(new)
    test_toggles_persist(new)
    test_bar_clicks_and_geometry(new)
    test_error_fallback(new)
    test_relayout_and_stages(new)
    test_tracker_resets(new)
    test_blob_pixels(new)
    test_perf_guard(new)
    test_drawer_hidden_by_default(new)
    test_drawer_open_close(new)
    test_drawer_edits(new)
    test_drawer_scroll(new)
    test_presets(new)
    test_diag_hides_bar(new)
    test_save_cfg_atomic(new)
    test_tuning_panel_default_not_sticky(new)
    test_export_async(new)
    test_item_bind_leak_bounded(new)
    test_uv_mode_needs_usb_camera(new)
    test_small_portrait_geometry(new)
    test_uv_init_guard(new)
    test_bar_two_rows_clear_of_zero(new)
    test_track_hit_matches_drawn(new)
    test_body_tap_needs_slop(new)
    test_quit_waits_for_export(new)
    print("DONE: test_uv_ui.py")


if __name__ == "__main__":
    main()
