# AtomS3R-CAM 内窥镜原型 v5.0

本版本把视频和姿态分开处理：视频只走经过 M5Stack 验证的路径，陀螺仪、
四元数、零点和方向圆盘继续使用已有的成熟实现。默认界面不再显示 COLOR、
DIAG、TUNE，也不会自动重放旧版错误的摄像头寄存器或色彩矩阵。

## 先选一条路线

### 路线 A：官方出厂固件（建议先用它确认整机）

- 视频：官方 UVC，经 Linux `/dev/video*` 读取。
- 姿态：官方固件的 IMU WebSocket，经 AtomS3R-CAM 自己的 Wi-Fi 热点读取。
- 优点：完整保留原厂视频管线，最适合验证“原厂画面正常”的记忆。
- 代价：树莓派 Wi-Fi 要连设备热点；需要联网时可让树莓派走有线网络。
- 不需要刷 `atoms3r_cam_imu.ino`。

用 M5Burner / EasyLoader 恢复 **AtomS3R-CAM User Demo** 出厂固件。开机后在
树莓派上依次运行：

```bash
sudo apt update
sudo apt install -y python3-opencv python3-serial python3-pil.imagetk python3-numpy v4l-utils network-manager
sudo usermod -aG dialout,video "$USER"
```

重启树莓派，然后连接设备自身的开放热点：

```bash
nmcli device wifi connect "AtomS3R-CAM-WiFi" ifname wlan0
```

确认两条官方链路都存在：

```bash
v4l2-ctl --list-devices
curl http://192.168.4.1/
```

启动完整界面：

```bash
cd ~/Pidev
DISPLAY=:0 python3 endoscope.py --official --video auto
```

屏幕先显示校零页。保持探头静止约 3 秒；状态变成
`official UVC + IMU online` 后，按 ZERO，按屏幕提示向上倾斜并检查左右方向，
最后按 START。

### 路线 B：自定义单 USB 串口固件

- 视频：GC0308 RGB565 帧由官方同款 `frame2jpg()` 直接编码，再装入现有协议。
- 姿态：探头内 100 Hz Mahony 融合，50 Hz 四元数输出。
- 优点：视频和姿态都在一条 USB CDC 链路上，不占用树莓派 Wi-Fi。
- 这条路线需要刷本包的 `atoms3r_cam_imu.ino`。

Arduino IDE 设置：

| 设置 | 值 |
|---|---|
| Board | M5Stack -> M5AtomS3R（必须有 R） |
| USB CDC On Boot | Enabled |
| USB Mode | Hardware CDC and JTAG |

刷写时保持探头静止。树莓派端启动：

```bash
cd ~/Pidev
DISPLAY=:0 python3 endoscope.py
```

## 本次修复了什么

1. **恢复官方视频转换。** 旧固件注释说模式 0 是“原始 RGB565”，实际代码却
   手工按高字节优先拆成 RGB888；这不是官方路径。现在生产模式直接使用
   `frame2jpg(camera_fb_t*)`。
2. **恢复官方传感器初始化。** 只保留官方的翻转/镜像设置，不再额外强行改
   AWB、AEC、AGC 或颜色寄存器。
3. **真正实现 UVC 组合。** 旧 `--video` 参数没有在 `main()` 使用，而且
   `V4L2Source.snapshot()` 访问了不存在的 `camera/quat/still` 成员。两处均已修复。
4. **加入原厂固件模式。** `--official` 同时读取 UVC 和
   `ws://192.168.4.1/api/v1/ws/imu_data`，在树莓派端运行同一 Mahony 融合与
   静止偏置学习。
5. **阻断旧颜色配置。** v4.x 保存的寄存器、曲线、矩阵、红蓝交换和灰世界
   AWB 不再自动应用到新画面；只迁移方向轴和上下极性。
6. **修复就绪包截断。** 固件 `ready` JSON 缓冲区从 64 增至 128 字节。
7. **修复原厂 IMU 轴一致性。** 官方 WebSocket 发布数据时只交换了加速度计
   X/Y、没有交换陀螺仪 X/Y；接收端先撤销该加速度交换，再做融合。

## 画面仍异常时如何判断

先运行路线 A。它是原厂 UVC，不经过本项目的像素解释、串口 JPEG 或颜色校准：

- 路线 A 正常、路线 B 异常：问题仍在 Arduino 摄像头库版本或串口固件环境；
  先使用路线 A 完成原型。
- 两条路线都异常：问题已不在本项目的软件颜色转换；恢复官方固件后换 USB
  数据线/USB 口，并用另一台电脑的相机程序复测，随后检查模组或摄像头排线。
- 路线 A 没有 `/dev/video*`：刷入的不是官方 User Demo UVC 固件，或 USB 线
  只能供电不能传数据。
- UVC 正常但 IMU 离线：树莓派尚未连接 `AtomS3R-CAM-WiFi`，或
  `192.168.4.1` 不可达。

不要再通过 COLOR/TUNE 猜测正常画面的格式。只有明确调试旧协议时才加：

```bash
DISPLAY=:0 python3 endoscope.py --legacy-colour-tools
```

## 无硬件自检

```bash
python3 test_endoscope_core.py
python3 -m py_compile endoscope.py
```

第一条应输出：

```text
PASS: packet parser, Mahony fusion, WebSocket frames, official video path
```

## 依据的官方资料

- M5Stack AtomS3R-CAM 产品页（出厂 UVC、GC0308、BMI270、管脚与下载模式）：
  https://docs.m5stack.com/en/core/AtomS3R%20Cam
- M5Stack AtomS3R-CAM UserDemo：
  https://github.com/m5stack/AtomS3R-CAM-UserDemo/tree/AtomS3R-CAM
- 官方 UVC 视频服务（直接 `frame2jpg()`）：
  https://github.com/m5stack/AtomS3R-CAM-UserDemo/blob/AtomS3R-CAM/main/service/service_uvc.cpp
- 官方 IMU WebSocket：
  https://github.com/m5stack/AtomS3R-CAM-UserDemo/blob/AtomS3R-CAM/main/service/apis/api_imu.cpp
- M5Stack Arduino 摄像头示例：
  https://github.com/m5stack/M5AtomS3/blob/main/examples/Basics/camera/camera.ino
- Espressif UVC 组件说明：
  https://docs.espressif.com/projects/esp-iot-solution/en/latest/usb/usb_device/usb_device_uvc.html

## 当前验证边界

代码已通过协议解析、四元数融合、WebSocket 帧解析、官方视频路径静态检查和
Python 语法测试。此环境没有 AtomS3R-CAM、树莓派显示器和 Arduino/ESP-IDF
工具链，所以最终的 UVC 枚举、相机实拍颜色和烧录结果仍须在你的硬件上完成。

