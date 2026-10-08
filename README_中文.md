# AtomS3R-CAM Endoscope v6.0.4

本版本通过一根 USB-C 同时传输官方 UVC/MJPEG 视频和 CDC IMU 数据，不需要连接摄像头 Wi-Fi。

主要功能：

- 树莓派真正全屏界面；
- 摄像头视频及方向指示；
- 用户 ZERO 校零及设备重启后重新校零提示；
- `MIRROR L/R` 水平镜像；
- `FLIP U/D` 上下翻转；
- UVC、CDC 断线检测与重新连接；
- 桌面 `Endoscope` 和 `Update Endoscope` 快捷方式；
- Windows/Linux 预编译固件烧录脚本。

先阅读上一级 `START_HERE_先看这里.md`，安装细节见 `INSTALL_V6_0_4_中文.md`，工程交接见 `HANDOFF.md`。

当前现场软件来源：用户于 2026-09-10 从正常工作的树莓派上传的工作副本。应用版本为 6.0.4。

本仓库 `uv-mode` 分支（v6.2.0，测试版）在以上基础上新增了可选的 UV 荧光实时分析模式，包含底部开关条、工程师调参抽屉、方案保存与 U 盘导出；UV 模式关闭时与 v6.1.0 逐像素一致。操作员和工程师使用说明见 `UV_MODE_GUIDE_CN.md`。
