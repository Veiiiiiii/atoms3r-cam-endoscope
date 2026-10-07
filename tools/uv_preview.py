#!/usr/bin/env python3
"""
Windows preview harness for endoscope.py (work item 6, first half).

Runs the real Tk App exactly as it runs on the Pi, except the probe is
replaced by FakeVideoLink: a background thread that loops an MP4 file,
imitating the AtomS3R UVC camera's frames and reporting a healthy,
always-online USB-composite link (identity quaternion, generation that
never changes, so a captured ZERO never goes stale). --video-only is set
in the fake args Namespace, so the App skips straight to the live RUN
view the same way it would on the Pi with no IMU zeroing step.

This file imports endoscope.py directly (never through `main()`, so no
real camera/serial code runs) and never touches the operator's real
~/.config: CONFIG is redirected to a throwaway temp file before the App
is constructed.

    py -3 tools\\uv_preview.py                       # just look at it
    py -3 tools\\uv_preview.py --seconds 8            # auto-quit after 8s
    py -3 tools\\uv_preview.py --shots DIR            # scripted screenshots

Run from the repo root (it locates endoscope.py next to this file's
parent directory).
"""
import argparse
import importlib.util
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

if sys.platform == "win32":
    # Without this, Tk reports window geometry in DPI-scaled "logical"
    # pixels while ImageGrab (and the real screen) work in physical
    # pixels. On a scaled display that mismatch makes a bbox built from
    # winfo_root{x,y}/width/height grab the wrong rectangle -- too small,
    # and offset -- so the PNG shows a sliver of whatever is behind the
    # window instead of the window itself. Declaring this process
    # per-monitor-DPI-aware (Windows 8.1+) makes Tk's numbers and the
    # screen agree. Best-effort: an older Windows without this API still
    # runs, just with the stock DPI-mismatch risk on scaled displays.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def load_endoscope():
    """Import endoscope.py, stubbing only what Windows itself cannot supply.

    cv2/numpy/Pillow/Tk are all real and installed on this box (see the
    plan's §1 Python note), so only two stand-ins are needed:

    - `fcntl`: POSIX-only (used for a single-instance file lock that main()
      sets up; App itself never touches it, but the unconditional top-level
      `import fcntl` would blow up before we get that far). A no-op stub is
      enough since we never call claim_single_instance().
    - `serial`: pyserial IS installed here, but stubbing it the same way
      test_endoscope_core.load_app() does keeps this harness from ever
      touching a real COM port, and matches the project's own hardware-free
      pattern for loading the module off-Pi.
    """
    if "fcntl" not in sys.modules:
        fcntl_stub = types.ModuleType("fcntl")
        fcntl_stub.LOCK_EX = 2
        fcntl_stub.LOCK_NB = 4
        fcntl_stub.flock = lambda *a, **kw: None
        sys.modules["fcntl"] = fcntl_stub

    serial_stub = types.ModuleType("serial")
    serial_stub.Serial = object
    sys.modules.setdefault("serial", serial_stub)

    spec = importlib.util.spec_from_file_location(
        "endoscope_app", REPO_ROOT / "endoscope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeCamera:
    """Stands in for V4L2Source: the subset of its surface App.health() reads
    (`online`, `frame_time`, `fps()`, `error`, `reconnects`, `actual`)."""

    def __init__(self, size):
        self.online = True
        self.frame_time = 0.0
        self.error = None
        self.reconnects = 0
        self.actual = size
        self._stamps = []

    def fps(self):
        now = time.monotonic()
        self._stamps = [t for t in self._stamps if now - t < 2.0]
        return float(len(self._stamps)) / 2.0 if self._stamps else 0.0

    def _mark_frame(self):
        self._stamps.append(time.monotonic())


class FakeVideoLink:
    """
    Drop-in for UsbCompositeProbeLink, as App actually uses one (see
    App.__init__, App.update, enter_run, probe_status_text, capture_zero
    and link_ready): same lock-guarded attributes, same health() keys, same
    is_usb_composite / is_uvc flags so App takes the identical UVC code
    path it would with the real probe.

    Instead of a camera device it loops `video_path` in a background
    thread at the file's own fps, centre-cropping to 4:3 and resizing to
    `size` to imitate the AtomS3R UVC camera's frame, and always reports a
    healthy online state with an identity quaternion (no IMU to fuse here,
    so heading is simply never anything but zero).
    """

    is_uvc = True
    is_usb_composite = True

    def __init__(self, video_path, size=(320, 240)):
        import cv2  # local import: load_endoscope() has already set up cv2

        self._cv2 = cv2
        self.video_path = str(video_path)
        self.size = size
        self.camera = _FakeCamera(size)

        self.lock = threading.Lock()
        self.quat = (1.0, 0.0, 0.0, 0.0)   # identity: a fake probe has no tilt
        self.still = True
        self.state = "online"
        self.fw = "uv_preview_sim"
        self.fw_ver = (6, 2, 0)
        self.calib_ok = True
        self.calibrating = False
        self.camera_failed = False
        self.generation = 1                # constant: a captured ZERO never stales
        self.imu_time = time.monotonic()
        self.bad_packets = 0
        self.dropped_packets = 0
        self.last_imu_error = None
        self.port = "FAKE"
        self.colour_mode = None
        self.sensor_preset = None
        self.sensor_regs = ""
        self.regs_seq = 0
        self.reg_ack = None
        self.raw_swap = False
        self.raw_stream = False
        self.test_pattern = False

        self._frame = None
        self._frame_seq = 0
        self._running = False
        self._thread = None

    # -- lifecycle -----------------------------------------------------
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="uv-preview-video")
        self._thread.start()
        return self

    def stop(self):
        self._running = False

    # -- the App's link interface --------------------------------------
    def snapshot(self):
        with self.lock:
            return self._frame, self._frame_seq, self.quat, self.still

    def health(self):
        now = time.monotonic()
        with self.lock:
            return {
                "state": self.state,
                "fw": self.fw,
                "fw_ver": self.fw_ver,
                "colour_mode": self.colour_mode,
                "sensor_preset": self.sensor_preset,
                "sensor_regs": self.sensor_regs,
                "regs_seq": self.regs_seq,
                "reg_ack": self.reg_ack,
                "raw_stream": False,
                "test_pattern": False,
                "calib_ok": self.calib_ok,
                "raw_seq": 0,
                "calibrating": self.calibrating,
                "camera_failed": not self.camera.online,
                "generation": self.generation,
                "imu_age": now - self.imu_time if self.imu_time else 1e9,
                "frame_age": (now - self.camera.frame_time
                              if self.camera.frame_time else 1e9),
                "fps": self.camera.fps(),
                "camera_error": self.camera.error,
                "camera_reconnects": self.camera.reconnects,
                "capture_size": self.camera.actual,
                "bad": self.bad_packets,
                "dropped": self.dropped_packets,
                "imu_reconnects": 0,
                "last_imu_error": self.last_imu_error,
                "port": self.port,
            }

    # UV mode needs none of these (legacy sensor-register tools are not in
    # scope here); UsbCompositeProbeLink disables them identically.
    def send_bytes(self, _):
        return False

    def send_byte(self, _):
        return False

    def get_regs(self):
        return {}, None

    def take_raw(self):
        return None, 0

    # -- the video loop --------------------------------------------------
    def _loop(self):
        cv2 = self._cv2
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            print("uv_preview: cannot open video {!r}".format(self.video_path),
                  file=sys.stderr)
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        if fps <= 1.0 or fps > 120.0:
            fps = 25.0
        period = 1.0 / fps
        tw, th = self.size
        next_due = time.monotonic()
        while self._running:
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            frame = _center_crop_4x3(frame)
            frame = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_LINEAR)
            now = time.monotonic()
            with self.lock:
                self._frame = frame
                self._frame_seq += 1
                self.imu_time = now
            self.camera.frame_time = now
            self.camera._mark_frame()
            next_due += period
            delay = next_due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_due = time.monotonic()      # fell behind; resync
        cap.release()


def _center_crop_4x3(frame):
    """Crop to a 4:3 box around the centre, the AtomS3R-CAM's own aspect."""
    h, w = frame.shape[:2]
    target = 4.0 / 3.0
    cur = w / float(h)
    if cur > target:
        new_w = int(round(h * target))
        x0 = max(0, (w - new_w) // 2)
        return frame[:, x0:x0 + new_w]
    if cur < target:
        new_h = int(round(w / target))
        y0 = max(0, (h - new_h) // 2)
        return frame[y0:y0 + new_h, :]
    return frame


def make_args(windowed=True):
    """Every attribute App (and the helpers it calls, e.g.
    _screen_imu_settings) reads off args -- see endoscope.py App.__init__,
    App.update and _screen_imu_settings. A plain Namespace stands in for
    argparse's result; App never mutates it."""
    return argparse.Namespace(
        windowed=windowed,
        kiosk=False,
        video_only=True,
        legacy_colour_tools=False,
        log=None,
        no_screen_imu=True,
        screen_imu_port=None,
        screen_imu_sign=None,
        sim=False,
        port=None,
        baud=115200,
        video=None,
        video_size="320x240",
        official=False,
        usb_composite=False,
        imu_ws="ws://192.168.4.1/api/v1/ws/imu_data",
    )


def take_screenshot(root, out_path):
    """Grab exactly the Tk window's own screen rectangle.

    PIL.ImageGrab needs root.update() run first so winfo_root{x,y}/width/
    height report the window's current, mapped position and size rather
    than stale pre-map values.
    """
    from PIL import ImageGrab

    root.update()
    x0 = root.winfo_rootx()
    y0 = root.winfo_rooty()
    x1 = x0 + root.winfo_width()
    y1 = y0 + root.winfo_height()
    img = ImageGrab.grab(bbox=(x0, y0, x1, y1))
    if sys.platform == "win32" and all(hi == 0 for _, hi in img.getextrema()):
        # A locked workstation (or a disconnected RDP session) gives
        # ImageGrab nothing but black. Ask the window to paint itself
        # into a bitmap instead; that works without a visible desktop.
        img = _print_window(root) or img
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path))
    print("uv_preview: wrote", out_path)


def _print_window(root):
    """Win32 PrintWindow capture of the Tk client area, or None."""
    try:
        import ctypes
        import ctypes.wintypes as wt
        from PIL import Image

        class _BMI(ctypes.Structure):
            _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG),
                        ("biHeight", wt.LONG), ("biPlanes", wt.WORD),
                        ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                        ("biClrImportant", wt.DWORD)]

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        # 64-bit handles: without these ctypes would truncate them to int.
        vp = ctypes.c_void_p
        user32.GetDC.restype = vp
        user32.GetDC.argtypes = [vp]
        user32.ReleaseDC.argtypes = [vp, vp]
        user32.PrintWindow.argtypes = [vp, vp, ctypes.c_uint]
        gdi32.CreateCompatibleDC.restype = vp
        gdi32.CreateCompatibleDC.argtypes = [vp]
        gdi32.CreateCompatibleBitmap.restype = vp
        gdi32.CreateCompatibleBitmap.argtypes = [vp, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = vp
        gdi32.SelectObject.argtypes = [vp, vp]
        gdi32.GetDIBits.argtypes = [vp, vp, ctypes.c_uint, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        gdi32.DeleteObject.argtypes = [vp]
        gdi32.DeleteDC.argtypes = [vp]
        hwnd = root.winfo_id()
        w, h = root.winfo_width(), root.winfo_height()
        hdc = user32.GetDC(hwnd)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mdc, bmp)
        ok = user32.PrintWindow(hwnd, mdc, 2)   # PW_RENDERFULLCONTENT
        bmi = _BMI(ctypes.sizeof(_BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
        if not ok:
            return None
        return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1
                                ).convert("RGB")
    except Exception as exc:
        print("uv_preview: PrintWindow capture failed:", exc, file=sys.stderr)
        return None


def run_shots_script(app, out_dir):
    """Scripted screenshot sequence, driven on the Tk main thread via
    root.after so every call into `app` happens where Tk expects it.

    toggle_uv_mode / uv_toggle / toggle_uv_drawer are added to App by a
    parallel work item (§3.9 of UV-PORT-PLAN.md); until they land this
    harness still produces 01_normal.png and prints which later shots it
    skipped, so it works today and keeps working once they appear.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    root = app.root

    def shot(name):
        # Wait for a few App.update() ticks first: a toggle only takes
        # effect on the next drawn frame, so capturing in the same tick
        # would photograph the previous state's picture and an empty
        # status line.
        root.after(300, lambda: take_screenshot(root, out_dir / name))

    def maybe(name_attr, *call_args):
        fn = getattr(app, name_attr, None)
        if fn is None:
            print("uv_preview: app.{} not present yet, skipping".format(name_attr))
            return False
        fn(*call_args)
        return True

    # Each closure runs, then the next is scheduled with a short delay so at
    # least one App.update() (40 ms cadence) lands in between and the
    # frame/HUD actually changes before the next capture.
    steps_state = {"uv_present": False, "drawer_present": False}

    def seq_01():
        shot("01_normal.png")

    def seq_02():
        if maybe("toggle_uv_mode"):
            steps_state["uv_present"] = True
            shot("02_uv_default.png")
        else:
            print("uv_preview: UV mode not implemented yet "
                  "(skipping 02/03/04)")

    def seq_03_on():
        if steps_state["uv_present"] and maybe("uv_toggle", "filter"):
            shot("03_uv_filter.png")

    def seq_03_off():
        if steps_state["uv_present"]:
            maybe("uv_toggle", "filter")   # back off

    def seq_04_on():
        if steps_state["uv_present"]:
            if maybe("toggle_uv_drawer"):
                steps_state["drawer_present"] = True
                shot("04_drawer_open.png")
            else:
                print("uv_preview: tuning drawer not implemented yet "
                      "(skipping 04)")

    def seq_05_scrolled():
        # Drag the drawer body half way down, as a finger would.
        if steps_state["drawer_present"] and app.uv_db is not None:
            app._uv_scroll_to(app._uv_content_h // 2)
            shot("05_drawer_scrolled.png")

    def seq_05b_bottom():
        if steps_state["drawer_present"] and app.uv_db is not None:
            app._uv_scroll_to(app._uv_content_h)       # clamps to the end
            shot("05b_drawer_bottom.png")

    def seq_06_edited():
        # A few -/+ taps and a toggle: the preset name gets its "*".
        if steps_state["drawer_present"] and app.uv_db is not None:
            app._uv_scroll_to(0)
            app._uv_set_value("exposure", app.uv_work.exposure + 0.2)
            app._uv_set_value("box_thickness", 3)
            app.uv_work.show_labels = True
            app._uv_edited("show_labels")
            shot("06_drawer_edited.png")

    def seq_07_list():
        # SAVE (to the throwaway presets file) then open the preset list.
        if steps_state["drawer_present"] and app.uv_db is not None:
            app.uv_preset_save()
            app._uv_toggle_list()
            shot("07_preset_list.png")

    def seq_08_confirm():
        if steps_state["drawer_present"] and app.uv_db is not None:
            app._uv_toggle_list()
            app.uv_preset_delete()          # first tap: arms CONFIRM?
            shot("08_delete_confirm.png")

    def seq_04_off():
        if steps_state["drawer_present"]:
            maybe("toggle_uv_drawer")      # close it again

    def seq_exit_uv():
        if steps_state["uv_present"]:
            maybe("toggle_uv_mode")        # leave UV mode as we found it

    def seq_quit():
        app.quit()

    chain = [seq_01, seq_02, seq_03_on, seq_03_off, seq_04_on,
             seq_05_scrolled, seq_05b_bottom, seq_06_edited, seq_07_list, seq_08_confirm,
             seq_04_off, seq_exit_uv, seq_quit]
    STEP_MS = 1000     # leaves room for the 300 ms post-action shot delay

    def run_next(i=0):
        if i >= len(chain):
            return
        chain[i]()
        root.after(STEP_MS, lambda: run_next(i + 1))

    # Give the video thread a little head start so 01_normal.png shows a
    # live frame rather than the very first black one.
    root.after(600, run_next)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    # Fixed Windows dev-box path (the reference sample video lives outside
    # this repo, in the UVScope1.1 source tree named in UV-PORT-PLAN.md §1).
    default_video = Path(r"C:\Users\weic\OneDrive - Innova Electronics Corp"
                         r"\Desktop\UVScope\UVScope1.1\sample-uv-video.mp4")
    ap.add_argument("--video", default=str(default_video),
                    help="video file to loop as the fake camera feed "
                         "(default: UVScope1.1's sample-uv-video.mp4)")
    ap.add_argument("--size", default="320x240",
                    help="fake camera frame size WxH (default 320x240, "
                         "the AtomS3R UVC default)")
    ap.add_argument("--geometry", default="1024x600",
                    help="Tk window size WxH (default 1024x600, the likely "
                         "Pi touchscreen)")
    ap.add_argument("--seconds", type=float, default=None,
                    help="auto-quit after N seconds")
    ap.add_argument("--shots", metavar="DIR",
                    help="run the scripted screenshot sequence into DIR, "
                         "then quit")
    args = ap.parse_args()

    try:
        sw, sh = (int(x) for x in args.size.lower().split("x", 1))
    except ValueError:
        ap.error("--size must look like 320x240")
    try:
        gw, gh = (int(x) for x in args.geometry.lower().split("x", 1))
    except ValueError:
        ap.error("--geometry must look like 1024x600")

    if not Path(args.video).exists():
        ap.error("video file not found: {}".format(args.video))

    endoscope = load_endoscope()

    # Never touch the operator's real ~/.config/endoscope.json.
    tmp_cfg = Path(tempfile.gettempdir()) / "uv_preview_endoscope_cfg.json"
    endoscope.CONFIG = str(tmp_cfg)
    # Same for the tuning drawer's presets file and EXPORT: a fresh scratch
    # folder per run, so screenshots never depend on (or add to) earlier runs.
    scratch = Path(tempfile.mkdtemp(prefix="uv_preview_"))
    endoscope.UV_PRESETS_FILE = str(scratch / "endoscope_uv_presets.json")
    endoscope.UV_MEDIA_ROOT = str(scratch / "media")
    endoscope.uv_find_export_dir = lambda **kw: (
        str(scratch / endoscope.UV_EXPORT_DIRNAME), False)

    link = FakeVideoLink(args.video, size=(sw, sh)).start()
    app_args = make_args(windowed=True)
    app = endoscope.App(link, app_args)

    # App.__init__ already set self.W/self.H from a hard-coded 1024x600
    # --windowed geometry; honour --geometry by resizing now, same as a
    # user dragging the window, which App already handles via <Configure>.
    app.root.geometry("{}x{}".format(gw, gh))
    app.root.update()

    if args.seconds is not None:
        app.root.after(int(args.seconds * 1000), app.quit)

    if args.shots:
        run_shots_script(app, Path(args.shots))

    try:
        app.run()
    finally:
        link.stop()


if __name__ == "__main__":
    main()
