# AtomS3R-CAM Endoscope v6.0.4 完整交接

## 1. 交付结论

本目录是从用户树莓派现场工作副本恢复的**可运行、可安装、可烧录**版本：

- 主机软件：`endoscope.py`，版本 `6.0.4`；
- 固件：`release/atoms3r_cam_uvc_imu_v6_0_4.bin`；
- 固件身份：`AtomS3R-CAM UVC+IMU v6.0.4`、`ATOMCAMV604`；
- 传输：同一根 USB-C 上的 UVC/MJPEG 视频和 CDC ACM IMU；
- 网络：正常工作路径不使用摄像头 Wi-Fi；
- 树莓派入口：`run_usb.sh`；
- 桌面入口：`install_desktop.sh` 安装/刷新 `Endoscope` 和 `Update Endoscope`。

用户在现场完成最后两处补丁后报告“所有功能均正常工作，完美”。该声明证明的是当时设备上的实际运行结果，不等价于实验室一小时耐久或医疗认证。

## 2. 文件来源和不可混淆项

最终 `endoscope.py` 的 SHA-256 记录在 `SHA256SUMS.txt`。`handoff/history/` 保存最后两个现场补丁前的文件，便于审计：

1. `before-zero-unlock`：仍要求固件 stationary 标志才允许 ZERO；
2. `before-fullscreen-fix`：已放宽手动 ZERO，但还未采用最后成功的窗口管理器全屏修正；
3. 根目录 `endoscope.py`：上述两处现场修正后的最终工作文件。

从树莓派取回的压缩包把正确 v6.0.4 固件放在项目根目录，而烧录脚本要求 `release/`。本交付已将**同一份正确固件**放入烧录脚本要求的位置，并排除旧的 `release/atoms3r_cam_uvc_imu_v6.bin`，避免误刷。

旧 `atoms3r_cam_imu.ino` 属于早期 Arduino/串口视频路线，不是当前 ESP-IDF UVC+CDC v6.0.4 固件，因此没有放进运行目录。

## 3. 最后两个现场补丁

### 手动 ZERO

USB composite 模式仍要求：设备在线、不是校准中、IMU 包龄小于 1 秒，并在同一把锁中原子获取四元数和设备 generation。最后现场补丁不再把固件 `still` 标志作为硬性门槛；操作者按 ZERO 本身代表确认探头静止。普通静置、视频翻转和窗口状态不会重写 `q_ref`。

### 真正全屏

启动后检查窗口是否覆盖物理屏幕。窗口管理器拒绝首次全屏时，程序只执行一次受管理窗口重新映射，并在 250 ms 后重新请求 `-fullscreen=True`。它不再用 `overrideredirect(True)` 制造只有无边框/最大化外观的假全屏，也不在视频循环中反复置顶。

## 4. 运行架构

### 视频

`V4L2Source` 自动寻找广告 MJPEG 的 `/dev/videoN`，由读取线程独占 `VideoCapture` 的打开、读取与释放。读取失败后按分辨率阶梯及重新打开机制恢复。`MIRROR L/R` 和 `FLIP U/D` 只变换显示像素，不改变 IMU 坐标。

### IMU

`UsbCompositeProbeLink` 从 `/dev/ttyACM*` 读取固定 76 字节、CRC-32 保护的 v1 数据包。健康状态以最近的**有效协议包**而非任意串口字节判断。链路会发送忽略型心跳、切换 DTR，并在超时后重新打开。固件时间戳回退、传感器恢复或串口重新打开会使 generation 改变，旧 ZERO 随即失效。

### 固件恢复策略

预编译固件包含 v6.0.4 的非阻塞 CDC newest-wins 邮箱、USB owner task 发送泵、BMI270 100 Hz 采样、静态标定/零偏更新及 UVC 提交恢复。详细行为和标志见 `PROTOCOL.md` 与 `CHANGELOG.md`。

## 5. 安装和恢复

### 摄像头固件

正常工作的 ATOMCAMV604 不必重复刷。需要恢复时，在 Windows 先让设备进入下载模式，然后双击 `FLASH_FIRMWARE_WINDOWS_DOUBLE_CLICK.bat`。底层脚本擦除 Flash 并将合并镜像写入地址 `0x0`，不需要 Arduino IDE。

### 树莓派

`install_pi.sh` 安装 Python/OpenCV/Pillow/serial/v4l2 依赖，加入 `video,dialout` 组，写入防 ModemManager 和关闭 USB autosuspend 的设备规则，然后调用 `install_desktop.sh` 刷新两个快捷方式。重启后使用桌面 `Endoscope`。

`Update Endoscope` 只从 GitHub 更新 Pi 软件，不会自动烧录固件。它在更新前保存本地改动到 stash，使用 `git pull --ff-only`，不会执行破坏性的 reset。

## 6. 验证与故障采集

无硬件验证命令：

```bash
python3 -m py_compile endoscope.py test_endoscope_core.py test_v604.py
python3 test_endoscope_core.py
python3 test_v604.py
```

现场诊断：

```bash
./diagnose_usb.sh
```

日志位于 `~/.cache/endoscope-last-run.log`。验收应至少覆盖连续开关五次、拔插恢复、另一姿态静置十分钟后回到固定物理方向，以及视频和 IMU 同时运行一小时。

## 7. 不能隐瞒的限制

- 六轴 IMU 依靠重力约束俯仰/横滚，不能长期观测绝对 yaw；极慢纯 yaw 可能被静止零偏算法抑制。
- 本包不是医疗器械认证、临床验证或安全认证。
- 当前树莓派工作副本保存了确切固件二进制，却没有包含生成它的 ESP-IDF `firmware/` 源码树。故本包**运行与恢复完整，但固件源码不可从本包逐字重现**。

## 8. 固件源码继续工作

用户 Windows 上的 `atoms3r_checkpoint04_compiled` 原文件夹可能仍含准确的 `firmware/`。取得后应：

1. 核对其合并镜像 SHA-256 是否与本包一致；
2. 将准确 `firmware/` 源码加入新的 source-complete 归档；
3. 使用 ESP-IDF v5.1.4 和 `source-recovery/DEPENDENCY_LOCK.json` 重建；
4. 运行生产 `test_imu_math.cpp` 和固件静态契约测试；
5. 仅在重建镜像、USB 字符串及实际哈希核对一致后，称为可复现源码版本。

`source-recovery/` 保存上一构建环境的依赖锁和恢复记录，但其中没有完整 firmware 目录。任何后续 AI 或工程师都不得把官方 User Demo、旧 Arduino `.ino` 或旧 v6 镜像改名冒充本固件源码。
