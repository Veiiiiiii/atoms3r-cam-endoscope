#!/usr/bin/env python3
"""Hardware-free regression tests for v6 transport, fusion and video flips."""

import importlib.util
import inspect
import json
import math
import socket
import sys
import threading
import time
import types
import zlib
from pathlib import Path

import numpy as np


HERE = Path(__file__).resolve().parent


def load_app():
    """Load the application without requiring a display or camera device."""
    serial = types.ModuleType("serial")
    serial.Serial = object
    sys.modules.setdefault("serial", serial)

    cv2 = types.ModuleType("cv2")
    cv2.CAP_V4L2 = 200
    cv2.ROTATE_180 = 1
    cv2.rotate = lambda image, _: np.rot90(image, 2).copy()
    cv2.flip = lambda image, code: (
        image[::-1, ::-1].copy() if code == -1 else
        image[:, ::-1].copy() if code == 1 else
        image[::-1].copy())
    sys.modules.setdefault("cv2", cv2)

    tk = types.ModuleType("tkinter")
    tkfont = types.ModuleType("tkinter.font")
    tkfont.Font = object
    tk.font = tkfont
    sys.modules.setdefault("tkinter", tk)
    sys.modules.setdefault("tkinter.font", tkfont)

    try:
        import PIL
        image_tk = types.ModuleType("PIL.ImageTk")
        image_tk.PhotoImage = object
        PIL.ImageTk = image_tk
        sys.modules["PIL.ImageTk"] = image_tk
    except ImportError:
        pil = types.ModuleType("PIL")
        pil.Image = types.SimpleNamespace()
        pil.ImageTk = types.SimpleNamespace(PhotoImage=object)
        sys.modules["PIL"] = pil

    spec = importlib.util.spec_from_file_location("endoscope_app", HERE / "endoscope.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_usb_packet(app, sequence=7, flags=0x0d, quaternion=(1, 0, 0, 0)):
    """Build exactly the record emitted by firmware/service_usb_imu.cpp."""
    without_crc = app.USB_IMU_PACKET.pack(
        app.USB_IMU_MAGIC, app.USB_IMU_VERSION, flags,
        app.USB_IMU_PACKET.size, sequence, 123456789,
        0.1, -0.2, 0.98, 1.2, -2.3, 3.4, 10.0, 20.0, 30.0,
        *quaternion, 0)
    crc = zlib.crc32(without_crc[:-4]) & 0xffffffff
    return without_crc[:-4] + crc.to_bytes(4, "little")


def test_usb_packet_parser(app):
    assert app.USB_IMU_PACKET.size == 76
    parser = app.UsbImuPacketParser()
    valid = make_usb_packet(app)

    # Noise, a corrupted record, and split reads must all recover at the next
    # magic word. This models reconnect boot text and arbitrary CDC chunks.
    corrupt = bytearray(valid)
    corrupt[25] ^= 0x80
    assert parser.feed(b"boot noise" + bytes(corrupt[:31])) == []
    records = parser.feed(bytes(corrupt[31:]) + valid[:9])
    assert records == []
    records = parser.feed(valid[9:])
    assert len(records) == 1
    record = records[0]
    assert record["sequence"] == 7
    assert record["flags"] == 0x0d
    assert abs(sum(v * v for v in record["quaternion"]) - 1.0) < 1e-6
    assert parser.bad_packets == 1


def test_legacy_packet_parser(app):
    """The older serial mode remains available as a diagnostic fallback."""
    link = app.ProbeLink()
    payload = json.dumps({"q": [1, 0, 0, 0]}).encode()
    checksum = 0
    for value in payload:
        checksum ^= value
    packet = (app.SYNC + bytes([app.TYPE_IMU]) +
              len(payload).to_bytes(4, "little") + payload + bytes([checksum]))
    link.buf.extend(b"boot text\x00\xff" + packet)
    kind, got = link._next_packet()
    assert kind == app.TYPE_IMU and got == payload


def test_fusion(app):
    """Keep the stock-WiFi fallback's host-side fusion covered."""
    fusion = app.MahonyFusion()
    t = 100.0
    for _ in range(40):
        fusion.update((0.0, 0.0, 1.0), (0.4, -0.3, 0.2), t)
        t += 0.1
    assert fusion.calibrated
    assert fusion.still
    assert max(abs(a - b) for a, b in zip(fusion.bias, (0.4, -0.3, 0.2))) < 0.03

    for _ in range(10):
        fusion.update((0.0, 0.0, 1.0), (0.4, -0.3, 90.2), t)
        t += 0.1
    w, x, y, z = fusion.q
    yaw = math.degrees(math.atan2(2 * (w * z + x * y),
                                  1 - 2 * (y * y + z * z)))
    assert 84.0 < yaw < 96.0, yaw
    assert abs(sum(v * v for v in fusion.q) - 1.0) < 1e-6


def test_websocket_frame(app):
    client, server = socket.socketpair()
    try:
        ws = app.WsJsonStream("ws://example.invalid/test")
        ws.sock = client
        payload = b'{"ax":0,"ay":0,"az":1}'
        server.sendall(bytes((0x81, len(payload))) + payload)
        assert ws.recv_json()["az"] == 1
    finally:
        client.close()
        server.close()


def test_video_flips_are_independent(app):
    image = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)
    viewer = object.__new__(app.App)
    viewer.rot180 = False
    viewer.video_flip_h = True
    viewer.video_flip_v = False
    assert np.array_equal(viewer._apply_video_orientation(image), image[:, ::-1])
    viewer.video_flip_h = False
    viewer.video_flip_v = True
    assert np.array_equal(viewer._apply_video_orientation(image), image[::-1])
    viewer.video_flip_h = True
    assert np.array_equal(viewer._apply_video_orientation(image), image[::-1, ::-1])


def test_firmware_composite_contract():
    """Static checks catch accidental removal of the one-cable architecture."""
    descriptor = (HERE / "firmware/components/usb_device_uvc/tusb/usb_descriptors.c").read_text()
    config = (HERE / "firmware/components/usb_device_uvc/tusb/tusb_config.h").read_text()
    service = (HERE / "firmware/main/service/service_usb_imu.cpp").read_text()
    uvc_service = (HERE / "firmware/main/service/service_uvc.cpp").read_text()
    cdc = (HERE / "firmware/components/usb_device_uvc/usb_device_cdc.c").read_text()
    main = (HERE / "firmware/main/usb_webcam_main.cpp").read_text()
    assert "TUD_CDC_DESCRIPTOR" in descriptor
    assert "#define CFG_TUD_CDC" in config and " 1" in config
    assert "static_assert(sizeof(ImuPacketV1) == 76" in service
    assert "start_service_usb_imu();" in main
    assert "start_service_web_server();" not in main
    # Regression for v6.0's permanent UVC stall: every callback return must
    # release SharedData, including capture/JPEG/oversize failures.
    assert "class SharedDataGuard" in uvc_service
    assert "#define UVC_MAX_FRAMESIZE_SIZE (128 * 1024)" in uvc_service
    # v6.0.3 IMU-drop fixes. All TinyUSB CDC transmit calls must stay inside
    # the TinyUSB task (usbd_defer_func); the IMU task only fills a mailbox.
    assert "usb_device_cdc_submit(&packet, sizeof(packet));" in service
    assert "usb_device_cdc_write" not in service
    assert "usbd_defer_func(cdc_tx_pump" in cdc
    assert "usbd_edpt_clear_stall" in cdc          # last-resort endpoint re-arm
    assert "tud_cdc_tx_complete_cb" in cdc         # progress ground truth
    # The probe must repair its own sensor and say so on the wire.
    assert "kFlagSensorFault = 1u << 4" in service
    assert "kFlagSensorRecovered = 1u << 5" in service
    assert "recover_imu_sensor()" in service
    assert "#define CFG_TUD_CDC_TX_BUFSIZE   512" in config


def test_usb_fault_states(app):
    """Sensor trouble must be named, not shown as an eternal "connecting"."""
    link = app.UsbCompositeProbeLink()
    valid = (app.USB_IMU_FLAG_VALID | app.USB_IMU_FLAG_CALIBRATED)

    link._publish({"flags": valid, "sequence": 1, "quaternion": (1, 0, 0, 0)})
    assert link.state == "online"

    fault = app.USB_IMU_FLAG_SENSOR_FAULT | app.USB_IMU_FLAG_CALIBRATED
    link._publish({"flags": fault, "sequence": 2, "quaternion": (1, 0, 0, 0)})
    assert link.state == "imu_fault"

    # Old firmware never sets the fault bit; that stays "connecting".
    link._publish({"flags": app.USB_IMU_FLAG_CALIBRATED, "sequence": 3,
                   "quaternion": (1, 0, 0, 0)})
    assert link.state == "connecting"

    # A recovery re-seeds the firmware fusion, so the app must invalidate any
    # earlier zero: one generation bump per recovery edge, not per packet.
    gen = link.generation
    recovered = valid | app.USB_IMU_FLAG_SENSOR_RECOVERED
    link._publish({"flags": recovered, "sequence": 4, "quaternion": (1, 0, 0, 0)})
    link._publish({"flags": recovered, "sequence": 5, "quaternion": (1, 0, 0, 0)})
    assert link.generation == gen + 1
    link._publish({"flags": valid, "sequence": 6, "quaternion": (1, 0, 0, 0)})
    link._publish({"flags": recovered, "sequence": 7, "quaternion": (1, 0, 0, 0)})
    assert link.generation == gen + 2
    assert link.state == "online"


def test_fw_version_from_serial_string(app):
    """v6.0.3+ encodes its version in iSerialNumber for on-screen display."""
    by_id = ("/dev/serial/by-id/usb-Espressif_AtomS3R-CAM_UVC+IMU_v6.0.3_"
             "ATOMCAMV603-if04-port0")
    assert app._fw_version_from_port(by_id) == (6, 0, 3)
    assert app._fw_version_from_port("/dev/ttyACM99") is None


def test_v4l2_handle_stays_on_reader_thread(app):
    """The UI thread must never free a VideoCapture blocked in V4L2 select."""
    stop_source = inspect.getsource(app.V4L2Source.stop)
    loop_source = inspect.getsource(app.V4L2Source._loop)
    assert ".release(" not in stop_source
    assert ".release(" in loop_source
    assert "self.reconnects += 1" in loop_source

    captures = []

    class FakeCapture:
        """First handle fails once; replacement provides small live frames."""

        def __init__(self, _source, _backend):
            self.owner = threading.get_ident()
            self.release_thread = None
            self.first = len(captures) == 0
            captures.append(self)

        def isOpened(self):
            return True

        def set(self, *_args):
            return True

        def read(self):
            time.sleep(0.01)
            if self.first:
                self.first = False
                return False, None
            return True, np.zeros((2, 2, 3), dtype=np.uint8)

        def release(self):
            self.release_thread = threading.get_ident()

    app.cv2.VideoCapture = FakeCapture
    app.cv2.VideoWriter_fourcc = lambda *_args: 0
    app.cv2.CAP_PROP_FOURCC = 1
    app.cv2.CAP_PROP_FRAME_WIDTH = 2
    app.cv2.CAP_PROP_FRAME_HEIGHT = 3
    app.cv2.CAP_PROP_BUFFERSIZE = 4

    main_thread = threading.get_ident()
    source = app.V4L2Source(index=0).start()
    deadline = time.monotonic() + 2.0
    while (len(captures) < 2 or source.frame_seq == 0) and time.monotonic() < deadline:
        time.sleep(0.02)
    source.stop()

    assert len(captures) >= 2 and source.reconnects >= 1
    assert captures[0].release_thread == captures[0].owner != main_thread
    assert captures[1].release_thread == captures[1].owner != main_thread


def main():
    app = load_app()
    test_usb_packet_parser(app)
    test_legacy_packet_parser(app)
    test_fusion(app)
    test_websocket_frame(app)
    test_video_flips_are_independent(app)
    test_firmware_composite_contract()
    test_usb_fault_states(app)
    test_fw_version_from_serial_string(app)
    test_v4l2_handle_stays_on_reader_thread(app)
    print("PASS: USB packets, fault states, fw-version, fusion, WebSocket, "
          "flips, UVC recovery, firmware contract")


if __name__ == "__main__":
    main()
