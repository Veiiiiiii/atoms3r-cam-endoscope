#!/usr/bin/env python3
"""Pixel-parity and edge-case tests for the ported UV fluorescence core
(endoscope.UVParams / UVProcessor, see UV-PORT-PLAN.md §3.2/§3.9) against
UVScope 1.1's own uvscope/core.py -- the Windows tool the algorithm was
tuned and developed in.

Run: py -3 test_uv_core.py   (hardware-free; no display/camera needed.
Unlike test_endoscope_core.py, this file leaves cv2/numpy REAL -- pixel
parity against UVScope needs the genuine OpenCV, not a test double.)

PARITY (and the HUD-parity check folded into it) print a clear SKIPPED
message and return early if UVScope1.1 or the sample videos are not on
this machine -- e.g. on the Pi, which never carries the Windows UVScope
checkout. Every other test group (SCALING, draw=False, EDGE, TIMING) does
not depend on UVScope1.1 and always runs.
"""
import importlib.util
import json
import sys
import types
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent

# ---- read-only reference material outside the repo (plan §1) -------------
UVSCOPE_DIR = Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\UVScope1.1")
UVSCOPE_CORE = UVSCOPE_DIR / "uvscope" / "core.py"
SAMPLE_VIDEOS = [
    Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\videoplayback.mp4"),
    Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\videoplayback (1).mp4"),
    Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\videoplayback (2).mp4"),
]
SAMPLE_720P_VIDEO = UVSCOPE_DIR / "sample-uv-video.mp4"


# --------------------------------------------------------------- loaders
def load_app():
    """Load endoscope.py the way test_endoscope_core.py does (no display /
    camera / serial device needed), but WITHOUT faking cv2 or tkinter --
    this file needs the real OpenCV for pixel-exact parity, and real Tk is
    already installed on this dev box."""
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
    # pyserial IS installed on this dev box, but stub it anyway (matching
    # test_endoscope_core.py / tools/uv_preview.py's own convention) so this
    # file can never touch a real COM port.
    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = object
    sys.modules.setdefault("serial", fake_serial)

    spec = importlib.util.spec_from_file_location("endoscope_uv_core_test", HERE / "endoscope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_uvscope_core():
    """Import UVScope1.1's own core.py by file path -- the reference
    algorithm. Read-only; never modified by this task.

    core.py uses `from __future__ import annotations`, so its dataclass
    decorator resolves field types by looking the module up in
    sys.modules by name (CPython 3.12's dataclasses._is_type) -- it must
    be registered there BEFORE exec_module runs, or that lookup returns
    None and dataclass() crashes with an unhelpful AttributeError."""
    spec = importlib.util.spec_from_file_location("uvscope_core_reference", UVSCOPE_CORE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _uvscope_available() -> bool:
    return (UVSCOPE_CORE.is_file()
            and SAMPLE_720P_VIDEO.is_file()
            and all(v.is_file() for v in SAMPLE_VIDEOS))


# --------------------------------------------------------------- fixtures
def _sample_frames(path, count=40, resize_to=None):
    """``count`` evenly spaced BGR uint8 frames from a video file."""
    cap = cv2.VideoCapture(str(path))
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frames = []
        if total > 0:
            idx = np.linspace(0, total - 1, count).round().astype(int)
            for i in idx:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
                ok, frame = cap.read()
                if ok:
                    frames.append(frame)
        if len(frames) < count:
            # Some containers misreport frame count (or seeking landed on a
            # bad frame) -- fall back to reading everything and subsampling.
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            all_frames = []
            while True:
                ok, frame = cap.read()
                if not ok:
                    break
                all_frames.append(frame)
            if all_frames:
                idx = np.linspace(0, len(all_frames) - 1, min(count, len(all_frames)))
                frames = [all_frames[int(round(i))] for i in idx]
    finally:
        cap.release()
    if resize_to is not None:
        w, h = resize_to
        frames = [cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA) for f in frames]
    return frames


def _synthetic_patch_frame(h=240, w=320, hue=70, sat=200, val=220,
                            y0=60, y1=180, x0=80, x1=220):
    """A black frame with one rectangular patch whose HSV sits inside the
    FACTORY detection range (hue 42..110, sat>=84, val>=188) -- a
    deterministic, reproducible "something fluorescent is here" frame."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    hsv = np.zeros((1, 1, 3), dtype=np.uint8)
    hsv[0, 0] = (hue, sat, val)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    frame[y0:y1, x0:x1] = bgr
    return frame


def _regions_equal(a, b, eps=1e-6) -> bool:
    if len(a) != len(b):
        return False
    for ra, rb in zip(a, b):
        if ra["id"] != rb["id"] or tuple(ra["box"]) != tuple(rb["box"]):
            return False
        if abs(ra["percent"] - rb["percent"]) > eps:
            return False
    return True


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


# -------------------------------------------------------------- PARAM SETS
def _param_sets(ref, ours):
    """[(label, ref_params, our_params), ...]. Every pair is derived from
    the SAME dict (never hand-duplicated literals that could drift apart),
    so a mismatch always comes from the two processors disagreeing, never
    from the test itself feeding them different numbers."""
    sets = []

    def add(label, d):
        sets.append((label, ref.UVParams.from_dict(d), ours.UVParams.from_dict(d)))

    add("factory", dict(ours.UV_FACTORY))
    sets.append(("uvscope_defaults", ref.UVParams(),
                 ours.UVParams.from_dict(ref.UVParams().to_dict())))
    add("filter_enabled", dict(ours.UV_FACTORY, filter_enabled=True))
    add("uv_reject", dict(ours.UV_FACTORY, uv_reject=True))
    add("labels_contours", dict(ours.UV_FACTORY, show_labels=True, draw_contours=True))
    add("open_px_5", dict(ours.UV_FACTORY, open_px=5))
    red_pink = dict(ours.UV_FACTORY)
    red_pink.update(ours.UV_DYE_PRESETS["red_pink"]["params"])
    add("red_pink_hue_wrap", red_pink)
    add("boost_off", dict(ours.UV_FACTORY, boost_enabled=False))
    add("track_off", dict(ours.UV_FACTORY, track_enabled=False))

    return sets


# ------------------------------------------------------------------ PARITY
def test_parity():
    if not _uvscope_available():
        print("SKIPPED: PARITY (UVScope1.1 folder or sample videos not found on this machine -- "
              f"expected under {UVSCOPE_DIR})")
        return 0

    ref = load_uvscope_core()
    ours = load_app()
    sets = _param_sets(ref, ours)

    videos = [(p, None) for p in SAMPLE_VIDEOS] + [(SAMPLE_720P_VIDEO, (640, 360))]
    compared = 0

    # show_hud (True in UVScope's own defaults, among our parameter sets)
    # bakes a timing number into the pixels. Freeze the (shared, stdlib)
    # clock for the whole comparison so both processors compute the exact
    # same "N ms" string regardless of real wall-clock jitter between two
    # separately-implemented process() calls -- without this, HUD-on frames
    # would mismatch by a few pixels at random, not because anything is
    # actually wrong.
    import time as time_mod
    orig_counter = time_mod.perf_counter
    calls = {"n": 0}

    def frozen_counter():
        calls["n"] += 1
        return 0.0 if calls["n"] % 2 else 0.01   # every call pair reads as "10 ms"

    time_mod.perf_counter = frozen_counter
    try:
        for path, resize_to in videos:
            frames = _sample_frames(path, count=40, resize_to=resize_to)
            assert len(frames) >= 40, f"{path.name}: only sampled {len(frames)} frames (need >= 40)"

            for label, ref_p, our_p in sets:
                ref_proc = ref.UVProcessor(ref_p.copy())
                our_proc = ours.UVProcessor(our_p.copy())
                for i, frame in enumerate(frames):
                    ref_out, ref_info = ref_proc.process(frame.copy())
                    our_out, our_info = our_proc.process(frame.copy(), draw=True)
                    assert np.array_equal(ref_out, our_out), (
                        f"{path.name} [{label}] frame {i}: pixel mismatch")
                    assert _regions_equal(ref_info.get("regions", []), our_info.get("regions", [])), (
                        f"{path.name} [{label}] frame {i}: region mismatch\n"
                        f"  ref={ref_info.get('regions')}\n  our={our_info.get('regions')}")
                    compared += 1

        # And a dedicated show_hud=True check, with a bigger, guaranteed
        # detection (the sample videos may or may not show fluorescence in
        # every frame) so the REGIONS/COVER part of the HUD text is non-zero
        # and genuinely exercised too. Short side 360 -> scale 1, so D8's
        # pixel rescale is a no-op and this must match UVScope exactly.
        frame = _synthetic_patch_frame(h=640, w=360, y0=200, y1=400, x0=80, x1=260)
        hud_params = dict(ours.UV_FACTORY, show_hud=True, show_labels=True, track_enabled=False)
        ref_proc = ref.UVProcessor(ref.UVParams.from_dict(hud_params))
        our_proc = ours.UVProcessor(ours.UVParams.from_dict(hud_params))
        ref_out, _ = ref_proc.process(frame.copy())
        our_out, _ = our_proc.process(frame.copy(), draw=True)
        assert np.array_equal(ref_out, our_out), "HUD-on output mismatch with a frozen clock"
    finally:
        time_mod.perf_counter = orig_counter

    print(f"PASS: PARITY -- {compared} frame x parameter-set comparisons bit-identical "
          f"across {len(videos)} videos x {len(sets)} parameter sets, plus frozen-clock HUD parity")
    return compared


# ------------------------------------------------------------ SCALING (D8)
def test_scaling():
    ours = load_app()

    # "at 320x240 assert effective merge/feather match round(27*240/360)=18
    # and round(13*240/360)=9" -- factory merge_px=27, feather_px=13.
    s = 240.0 / 360.0
    assert int(round(27 * s)) == 18
    assert int(round(13 * s)) == 9

    proc = ours.UVProcessor(ours.UVParams())
    frame = np.zeros((240, 320, 3), dtype=np.uint8)   # H=240, W=320 -> short side 240
    _, info = proc.process(frame, draw=False)
    assert abs(info["scale"] - s) < 1e-9, info["scale"]

    # A landscape frame wide enough to trigger the analysis_width=640
    # downscale shows s_analysis and s_full are genuinely independent: the
    # analysis frame ends up exactly 360 short-side (s_analysis==1) while
    # the FULL frame's short side is 720 (s_full==2).
    wide = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, info_wide = proc.process(wide, draw=False)
    assert abs(info_wide["scale"] - 1.0) < 1e-9, info_wide["scale"]

    # Downscaling a frame by 0.5 should map its main detected region back
    # onto the full-size detection with IoU > 0.6 (small morphology/edge
    # differences are expected; a gross mismatch would mean the D8 pixel
    # rescale is broken).
    full = _synthetic_patch_frame(h=640, w=360, y0=200, y1=320, x0=120, x1=220)
    params = ours.UVParams.from_dict(dict(ours.UV_FACTORY, track_enabled=False))
    _, info_full = ours.UVProcessor(params.copy()).process(full, draw=False)
    assert info_full["regions"], "synthetic patch must be detected at full size"

    half = cv2.resize(full, (360 // 2, 640 // 2), interpolation=cv2.INTER_AREA)
    _, info_half = ours.UVProcessor(params.copy()).process(half, draw=False)
    assert info_half["regions"], "synthetic patch must be detected at half size"

    def biggest(regions):
        return max(regions, key=lambda r: r["box"][2] * r["box"][3])["box"]

    fb = biggest(info_full["regions"])
    hb = biggest(info_half["regions"])
    mapped = (hb[0] * 2, hb[1] * 2, hb[2] * 2, hb[3] * 2)
    score = _iou(fb, mapped)
    assert score > 0.6, (score, fb, mapped)

    print(f"PASS: SCALING -- merge/feather formula, independent analysis/full scale, "
          f"downscale-by-0.5 IoU={score:.2f}")


# ------------------------------------------------------------ draw=False
def test_draw_false():
    ours = load_app()
    frame = _synthetic_patch_frame()
    params = ours.UVParams.from_dict(ours.UV_FACTORY)

    # Same regions whether drawn or not -- feed each FRESH processor the
    # same frame twice so the tracker (min_hits=2) actually confirms a box.
    proc_a = ours.UVProcessor(params.copy())
    proc_a.process(frame, draw=True)
    _, info_a = proc_a.process(frame, draw=True)

    proc_b = ours.UVProcessor(params.copy())
    proc_b.process(frame, draw=False)
    _, info_b = proc_b.process(frame, draw=False)

    assert info_a["regions"], "expected a confirmed region by the 2nd frame"
    assert _regions_equal(info_a["regions"], info_b["regions"]), (
        info_a["regions"], info_b["regions"])

    # With nothing left to draw (boxes/contours/HUD all off), draw=True and
    # draw=False must produce IDENTICAL pixels, not just identical regions.
    off = ours.UVParams.from_dict(dict(ours.UV_FACTORY, draw_boxes=False, draw_contours=False))
    out_c, _ = ours.UVProcessor(off.copy()).process(frame, draw=True)
    out_d, _ = ours.UVProcessor(off.copy()).process(frame, draw=False)
    assert np.array_equal(out_c, out_d)

    print("PASS: draw=False -- regions match draw=True; pixels match when nothing is drawn")


# ------------------------------------------------------------------- EDGE
def test_edges():
    ours = load_app()
    proc = ours.UVProcessor(ours.UVParams())

    out, info = proc.process(None)
    assert out is None and info == {}

    empty = np.zeros((0, 0, 3), dtype=np.uint8)
    out, info = proc.process(empty)
    assert out is empty and info == {}

    one_px = np.full((1, 1, 3), 128, dtype=np.uint8)
    out, info = proc.process(one_px, draw=False)
    assert out.shape == (1, 1, 3)

    rng = np.random.default_rng(0)
    small16 = rng.integers(0, 255, (16, 16, 3), dtype=np.uint8)
    out, info = proc.process(small16, draw=False)
    assert out.shape == (16, 16, 3)

    gray = np.zeros((64, 64), dtype=np.uint8)          # 2-D, no channel axis
    out, info = proc.process(gray)
    assert out is gray and info == {}

    black = np.zeros((240, 320, 3), dtype=np.uint8)
    out, info = proc.process(black, draw=False)
    assert info["count"] == 0 and info["regions"] == []

    yellow_hsv = np.zeros((1, 1, 3), dtype=np.uint8)
    yellow_hsv[0, 0] = (60, 255, 255)                   # fully saturated, inside factory hue range
    yellow_bgr = cv2.cvtColor(yellow_hsv, cv2.COLOR_HSV2BGR)[0, 0]
    sat_frame = np.tile(yellow_bgr, (240, 320, 1)).astype(np.uint8)
    out, info = proc.process(sat_frame, draw=False)
    assert out.shape == sat_frame.shape
    assert info["coverage"] > 95.0, info["coverage"]    # the whole frame should read as "lit"

    # from_dict robustness: junk / out-of-range / unknown / missing keys.
    junk = ours.UVParams.from_dict({
        "hue_min": "oops", "sat_min": -500, "val_min": 99999,
        "box_bgr": "not-a-list", "filter_enabled": 1, "unknown_field_xyz": 1,
        "min_area_ratio": -5,
    })
    assert junk.hue_min == ours.UV_FACTORY["hue_min"]          # unparsable -> factory fallback
    assert junk.sat_min == 0 and junk.val_min == 255           # clamped into UVScope's UI range
    assert junk.box_bgr == ours.UV_FACTORY["box_bgr"]          # unusable junk -> factory
    assert junk.filter_enabled is True                         # 1 -> True
    assert not hasattr(junk, "unknown_field_xyz")              # unknown keys are dropped
    assert junk.min_area_ratio == ours.UV_UI_RANGES["min_area_ratio"][0]

    missing = ours.UVParams.from_dict({"hue_min": 50})
    assert missing.hue_min == 50
    assert missing.box_bgr == ours.UV_FACTORY["box_bgr"]       # missing keys -> factory

    none_params = ours.UVParams.from_dict(None)
    assert none_params.to_dict() == ours.UV_FACTORY

    # Non-finite / overflowing numbers (json.load accepts Infinity, NaN and
    # 1e999) and string booleans must fall back to factory, never raise.
    hostile = ours.UVParams.from_dict(json.loads(
        '{"hue_min": Infinity, "exposure": NaN, "sensitivity": NaN,'
        ' "merge_px": 1e999, "box_bgr": [Infinity, 0, -Infinity],'
        ' "uv_reject": "false", "draw_boxes": "yes", "show_labels": null,'
        ' "analysis_width": -50, "track_min_hits": 0}'))
    assert hostile.hue_min == ours.UV_FACTORY["hue_min"]
    assert hostile.exposure == ours.UV_FACTORY["exposure"]
    assert hostile.sensitivity == ours.UV_FACTORY["sensitivity"]
    assert hostile.merge_px == ours.UV_FACTORY["merge_px"]
    assert hostile.box_bgr == ours.UV_FACTORY["box_bgr"]
    assert hostile.uv_reject is False and hostile.draw_boxes is True
    assert hostile.show_labels == ours.UV_FACTORY["show_labels"]
    assert hostile.analysis_width == 0 and hostile.track_min_hits == 1
    assert ours.UVParams.from_dict({"exposure": 10 ** 400}).exposure == \
        ours.UV_FACTORY["exposure"]

    # UV_FACTORY must round-trip through from_dict/to_dict unchanged.
    rt = ours.UVParams.from_dict(ours.UV_FACTORY).to_dict()
    assert rt == ours.UV_FACTORY, (rt, ours.UV_FACTORY)

    print("PASS: EDGE -- None/empty/1x1/16x16/grayscale/all-black/all-saturated/"
          "from_dict junk+missing/UV_FACTORY round-trip")


# ----------------------------------------------------------------- TIMING
def test_timing():
    ours = load_app()
    proc = ours.UVProcessor(ours.UVParams())
    rng = np.random.default_rng(1)
    for w, h in ((320, 240), (640, 480)):
        frame = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        proc.process(frame, draw=False)                 # warm up the LUT
        times = [proc.process(frame, draw=False)[1].get("ms", 0.0) for _ in range(15)]
        avg = sum(times) / len(times)
        print(f"TIMING {w}x{h}: {avg:.2f} ms/frame avg over {len(times)} frames "
              f"(Pi-5 target: {'<=8ms' if (w, h) == (320, 240) else '<=20ms'} -- "
              "this is a Windows dev-box number, not the gate)")
        assert avg < 100.0, f"{w}x{h} unexpectedly slow on this dev box: {avg:.2f} ms"


def main():
    test_edges()
    test_scaling()
    test_draw_false()
    test_timing()
    test_parity()
    print("DONE: test_uv_core.py")


if __name__ == "__main__":
    main()
