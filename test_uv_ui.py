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
import subprocess
import sys
import tempfile
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


def make_app(mod, frame, cfg=None):
    if cfg is not None:
        Path(mod.CONFIG).write_text(json.dumps(cfg), encoding="utf-8")
    elif os.path.exists(mod.CONFIG):
        os.remove(mod.CONFIG)
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
            saved = json.loads(Path(mod.CONFIG).read_text(encoding="utf-8"))
            assert saved["uv"] == expect, saved.get("uv")
            assert saved["uv_tuning_panel"] is True
            p = app.uv_proc.params
            assert p.boost_enabled == expect["boost"]
            assert p.draw_boxes == expect["boxes"]
            assert p.filter_enabled == expect["filter"]
            assert p.detect_enabled == (expect["boost"] or expect["boxes"])
            labels = {b[0]: app.canvas.itemcget(b[5], "text") for b in app.uv_bar}
            for n in ("boost", "boxes", "filter"):
                assert labels[n].startswith("✓ ") == expect[n], labels
        # Here: boost False, boxes False, filter False -> nothing detects.
        assert app.uv_proc.params.detect_enabled is False
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

    # Junk and old-revision configs fall back to sane defaults.
    rev = mod.CONFIG_REV
    for cfg, want in (
            ({"config_rev": rev, "uv": {"boost": "maybe", "boxes": "off", "filter": None}},
             {"boost": True, "boxes": False, "filter": False}),
            ({"config_rev": rev, "uv": "garbage", "uv_tuning_panel": "no"},
             {"boost": True, "boxes": True, "filter": False}),
            ({"config_rev": rev - 1, "uv": {"boost": False, "boxes": False, "filter": True}},
             {"boost": True, "boxes": True, "filter": False})):
        app, link, spy = make_app(mod, scene(), cfg=cfg)
        try:
            assert app.uv_mode is False and app.uv_opts == want, (cfg, app.uv_opts)
            assert app.uv_tuning_panel == (cfg.get("uv_tuning_panel") != "no")
            app.save_cfg()
            saved = json.loads(Path(mod.CONFIG).read_text(encoding="utf-8"))
            assert saved["uv"] == want and "uv_tuning_panel" in saved
        finally:
            close(app)
    print("PASS: TOGGLES -- flip, persist to cfg['uv'], map to params (detect = boost or boxes), "
          "check marks; restart boots normal with saved toggles; junk/migrated config -> defaults")


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
    print("DONE: test_uv_ui.py")


if __name__ == "__main__":
    main()
