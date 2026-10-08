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

## 9. v6.0.5 UV 模式

UV 荧光分析模式全部在单文件 `endoscope.py` 内，不新增依赖（numpy/cv2 已在 install_pi.sh 里）。本分支（`uv-mode`）的基础是现场验证过的 6.0.4（commit `38500b5`），**不包含屏幕陀螺仪**功能（那是 6.1.0/6.2.0 的内容，在 `uv-mode-screengyro` 分支）；UV 代码本身与那个分支上的 UV 代码语义完全一致，只是挂在不同的基础版本上。

### 架构

- **核心算法**（`endoscope.py` 约第 2450–3100 行）：`UVParams`（dataclass，默认值 = `UV_FACTORY` = 出厂 `uv_params.json`）、`UV_REF_SHORT_SIDE = 360`、`UV_DYE_PRESETS`（染料预设，来自 UVScope1.1 的 `PRESETS`）、`UVBoxTracker`（跨帧框体追踪/平滑）、`UVProcessor`（`_build_lut` / `apply_filter` / `_detect` / `_boost` / `_annotate` / `process`）。这些是从 `UVScope1.1\uvscope\core.py` 原样移植，在 scale=1 时逐像素一致（`test_uv_core.py` 的 PARITY 用例保证）。
- **像素参数缩放**（计划 §3.2/D8）：`merge_px`/`open_px` 按分析帧的 `min(h,w)/360` 缩放，`feather_px` 和框线宽按显示分辨率的 `min(H,W)/360` 缩放，取整规则沿用 UVScope（`|1`、`>=3`）。颜色阈值和比例不缩放。
- **App 侧固定 API 名**（计划 §3.9，供以后改动时对照）：`self.uv_mode`（bool）、`self.uv_opts`（`{"boost","boxes","filter"}` 三个布尔）、`self.uv_proc`（`UVProcessor` 实例）、`self.uv_drawer_open`（bool）；方法 `toggle_uv_mode()`、`uv_toggle(name)`、`toggle_uv_drawer()`（均在 endoscope.py 约第 4420–4550 行一带）。`UVProcessor.process(frame, draw=False)` 在采集分辨率跑一次，App 自己在显示分辨率把 `info["regions"]`/`info["contours"]` 画成框/标签，这样线条在任何屏幕分辨率下都清晰。
- **UI**：`UV MODE` 按钮（左列，FLIP U/D 下方）；底部半透明条（`BOOST`/`SMART BOX`/`FILTER`/`EXIT UV`，背景色按 α 混合进画面像素，靠几何命中而不是 Tk 透明度）；右边缘小箭头拉出的调参抽屉（Canvas 覆盖层，触摸拖动滚动，`-`/`+`/拖动条改值）。
- **预设与导出**：`uv_presets.py` 相关逻辑都在 endoscope.py 内——`UV_PRESETS_FILE`（`~/.config/endoscope_uv_presets.json`，FACTORY 不落盘）、`UV_EXPORTED_BY = "Endoscope " + APP_VER`（随版本号自动变化，不要再写死字符串）、导出辅助函数在约第 3120–3320 行（`_uv_write_json`、导出目录选择——U 盘优先、单预设文件 + 全量 bundle、`os.sync()`）。

### 不变量

- UV 模式关闭时，RUN 阶段画面像素和控件位置必须与 6.0.4（commit `38500b5`，现场验证、树莓派上实际运行的版本）逐像素一致，唯一允许的差异是新增的 `UV MODE` 按钮本身；`test_uv_ui.py` 的 UV-OFF IDENTITY 用例用 `git show 38500b5:endoscope.py` 直接对比校验。
- UV 处理或绘制中的任何异常只能回退显示原始帧、把 traceback 打到 stderr（落在 `~/.cache/endoscope-last-run.log`）、最多每 10 秒弹一次 `UV PROCESSING ERROR` 提示，绝不能让 Tk 主循环崩溃。
- 除 SAVE/EXPORT 的小 JSON 写入外，UV 相关代码不得在 Tk 线程上做阻塞 I/O。
- 新版本号只应该通过 `APP_VER`（endoscope.py 第 129 行附近）和 `VERSION` 文件改动；`UV_EXPORTED_BY` 已经是 `"Endoscope " + APP_VER` 派生值，升级版本号时它会自动跟着变，不需要也不应该单独修改。

### 测试命令

```bash
py -3 test_uv_core.py
PYTHONPATH=../winshim py -3 test_uv_ui.py
PYTHONPATH=../winshim py -3 test_uv_presets.py
PYTHONPATH=../winshim py -3 test_endoscope_core.py
PYTHONPATH=../winshim py -3 test_v604.py
```

`test_uv_ui.py`/`test_uv_presets.py`/`test_endoscope_core.py`/`test_v604.py` 在 Windows 上需要 `PYTHONPATH=../winshim`，因为它们间接用到 Unix 专属的 `fcntl`（锁文件/原子写），`winshim` 提供一个仅供本机测试用的占位实现；树莓派本身是 Linux，不需要这个垫片。本分支没有 `test_screen_gyro.py`（没有屏幕陀螺仪功能）。Windows 下跑应用本体做视觉确认用 `py -3 tools/uv_preview.py --shots DIR --seconds 14 [--geometry 800x480] [--video PATH]`；`tools/uv_bench.py` 测 UV 处理每帧耗时（320x240、640x480）。

### 尚待现场验证

- 真实 Raspberry Pi 5 上的处理耗时（当前 `test_uv_core.py`/`tools/uv_bench.py` 的数字是 Windows 开发机估算，不是 Pi 实测）；
- 真实触屏下底部条和调参抽屉的点击/拖动手感；
- 真实 U 盘插入后的 EXPORT 路径和文件；
- 真实 UV 灯 + 这颗摄像头下的检测阈值（现在是对着 YouTube UV 视频调的，不是真实荧光光源）。
