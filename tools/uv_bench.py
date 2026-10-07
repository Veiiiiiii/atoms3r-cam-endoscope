#!/usr/bin/env python3
"""UV processing benchmark -- ms/frame at 320x240 and 640x480 (plan §3.8).

Each frame is center-cropped to 4:3 (a Pi camera typically delivers 4:3)
then resized to the two target sizes, run once each through
UVProcessor(UVParams()).process(frame, draw=False) -- the live endoscope
path -- and mean/p95 ms/frame is printed per size. §3.8's Pi-5 targets are
<= 8 ms at 320x240 and <= 20 ms at 640x480; this is a Windows dev-box
number, not the gate itself (the plan estimates Pi-5 at roughly Windows / 3).

    py -3 tools\\uv_bench.py                      # default sample videos
    py -3 tools\\uv_bench.py some_video.mp4 ...    # bench specific file(s)
    py -3 tools\\uv_bench.py --frames 120          # more frames per video

Run from anywhere; paths are resolved relative to this file's repo root.
"""
import argparse
import importlib.util
import statistics
import sys
import types
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

DEFAULT_VIDEOS = [
    Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\videoplayback.mp4"),
    Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\videoplayback (1).mp4"),
    Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\videoplayback (2).mp4"),
    Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp\Desktop\UVScope\UVScope1.1\sample-uv-video.mp4"),
]

SIZES = [(320, 240), (640, 480)]


def _load_endoscope():
    """Import endoscope.py, stubbing only what this Windows box cannot
    supply (fcntl is POSIX-only) plus serial (installed here, but stubbed
    anyway so this tool can never touch a real COM port -- same convention
    as test_uv_core.py / tools/uv_preview.py). cv2/numpy stay real: a
    benchmark of fake math would be meaningless."""
    if "fcntl" not in sys.modules:
        try:
            import fcntl  # noqa: F401
        except ImportError:
            fcntl_stub = types.ModuleType("fcntl")
            fcntl_stub.LOCK_EX = 2
            fcntl_stub.LOCK_NB = 4
            fcntl_stub.LOCK_UN = 8
            fcntl_stub.flock = lambda *a, **kw: None
            sys.modules["fcntl"] = fcntl_stub

    serial_stub = types.ModuleType("serial")
    serial_stub.Serial = object
    sys.modules.setdefault("serial", serial_stub)

    spec = importlib.util.spec_from_file_location("endoscope_uv_bench", REPO_ROOT / "endoscope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _center_crop_4x3(frame):
    h, w = frame.shape[:2]
    target = 4.0 / 3.0
    ratio = w / float(h)
    if ratio > target:
        new_w = max(1, int(round(h * target)))
        x0 = (w - new_w) // 2
        return frame[:, x0:x0 + new_w]
    new_h = max(1, int(round(w / target)))
    y0 = (h - new_h) // 2
    return frame[y0:y0 + new_h, :]


def _iter_frames(path, limit):
    cap = cv2.VideoCapture(str(path))
    n = 0
    try:
        while n < limit:
            ok, frame = cap.read()
            if not ok:
                break
            yield frame
            n += 1
    finally:
        cap.release()


def bench(video_paths, frames_per_video=60):
    endoscope = _load_endoscope()
    # One persistent processor per target size (like the real App would
    # run), so timings reflect steady-state cost, not constructor/LUT
    # overhead repeated every frame.
    procs = {size: endoscope.UVProcessor(endoscope.UVParams()) for size in SIZES}
    warmed = {size: False for size in SIZES}
    timings = {size: [] for size in SIZES}
    any_video = False

    for path in video_paths:
        path = Path(path)
        if not path.is_file():
            print(f"SKIP (not found): {path}")
            continue
        any_video = True
        n = 0
        for frame in _iter_frames(path, limit=frames_per_video):
            cropped = _center_crop_4x3(frame)
            for size in SIZES:
                w, h = size
                sized = cv2.resize(cropped, (w, h), interpolation=cv2.INTER_AREA)
                if not warmed[size]:
                    procs[size].process(sized, draw=False)   # first call builds the LUT
                    warmed[size] = True
                _, info = procs[size].process(sized, draw=False)
                timings[size].append(info.get("ms", 0.0))
            n += 1
        print(f"{path.name}: {n} frames")

    if not any_video:
        print("No input videos found -- nothing to benchmark.")
        return timings

    print()
    for size in SIZES:
        vals = timings[size]
        if not vals:
            print(f"{size[0]}x{size[1]}: no frames processed")
            continue
        vals_sorted = sorted(vals)
        p95 = vals_sorted[min(len(vals_sorted) - 1, int(round(0.95 * (len(vals_sorted) - 1))))]
        target = "<= 8ms Pi-5 target" if size == (320, 240) else "<= 20ms Pi-5 target"
        print(f"{size[0]}x{size[1]}: n={len(vals)}  mean={statistics.mean(vals):.2f} ms  "
              f"p95={p95:.2f} ms   ({target}; this is a Windows-box number)")
    return timings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="*", help="video file(s) to benchmark (default: sample set)")
    ap.add_argument("--frames", type=int, default=60, help="frames per video (default 60)")
    args = ap.parse_args(argv)

    video_paths = [Path(v) for v in args.videos] if args.videos else DEFAULT_VIDEOS
    bench(video_paths, frames_per_video=args.frames)
    return 0


if __name__ == "__main__":
    sys.exit(main())
