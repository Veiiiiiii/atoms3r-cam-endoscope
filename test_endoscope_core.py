#!/usr/bin/env python3
"""Hardware-free regression tests for the endoscope transport and fusion."""

import importlib.util
import json
import math
import socket
import sys
import types
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load_app():
    # The Raspberry Pi installs these packages. CI for the protocol/fusion does
    # not need a display or image decoder, so provide inert import stubs.
    serial = types.ModuleType("serial")
    serial.Serial = object
    sys.modules.setdefault("serial", serial)

    cv2 = types.ModuleType("cv2")
    cv2.CAP_V4L2 = 200
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


def test_packet_parser(app):
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
    fusion = app.MahonyFusion()
    t = 100.0
    # A stationary non-zero gyro offset must be calibrated away.
    for _ in range(40):
        fusion.update((0.0, 0.0, 1.0), (0.4, -0.3, 0.2), t)
        t += 0.1
    assert fusion.calibrated
    assert fusion.still
    assert max(abs(a - b) for a, b in zip(fusion.bias, (0.4, -0.3, 0.2))) < 0.03

    # One second at +90 dps around body Z should produce about +90 degrees yaw.
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


def test_firmware_uses_official_path():
    source = (HERE / "atoms3r_cam_imu.ino").read_text(encoding="utf-8")
    production = source.split("if (mode == 0)", 1)[1].split("else if", 1)[0]
    startup = source.split("bool startCamera()", 1)[1].split("static uint8_t *rgbBuf", 1)[0]
    assert "frame2jpg(fb, JPEG_QUALITY" in production
    assert "set_whitebal" not in startup
    assert "char rdy[128]" in source
    assert r'\"video\":\"official_frame2jpg\"' in source


def main():
    app = load_app()
    test_packet_parser(app)
    test_fusion(app)
    test_websocket_frame(app)
    test_firmware_uses_official_path()
    print("PASS: packet parser, Mahony fusion, WebSocket frames, official video path")


if __name__ == "__main__":
    main()
