# v6.0.4 升级步骤

**先看 WORK_STATUS.md / TEST_REPORT.md：若为 checkpoint 或构建未通过，不要刷机。**
以下仅适用于包含真实 `release/atoms3r_cam_uvc_imu_v6_0_4.bin` 且标明构建通过的发布包。

## 需要改动什么

| 部分 | 本次操作 |
|---|---|
| AtomS3R-CAM 固件 | 必须更新；USB发送和IMU算法在固件中 |
| Pi 软件 | 必须更新；全屏、重连和ZERO检查在Pi中 |
| 两个桌面快捷方式 | install_pi.sh 自动调用 install_desktop.sh 替换 |
| 镜像/上下翻转配置 | 保留 ~/.config/endoscope.json |
| 视频数据线和像素格式 | 沿用正常工作的USB连接和官方视频路径 |
| Wi-Fi、Arduino IDE、系统重装 | 不需要 |

先保留当前可用 v6.0.3 原包，方便两端一起回退。新源码未由本助手上传到你的GitHub。

## 1. Windows 烧录（只在 Windows CMD 执行）

退出相机、串口监视器和旧程序。摄像头接Windows；按住复位键约2秒，内部绿灯亮后松开，进入下载模式。
在设备管理器看当前COM号。若解压目录如下，逐行执行：

```bat
cd /d "C:\Users\weic\Downloads\atoms3r_cam_endoscope_v6_0_4\atoms3r-cam-endoscope"
py -m pip install "esptool==4.8.1"
flash_prebuilt_windows.bat COM6
```

路径及COM号按实际修改；一次只复制一行，不要把两次命令粘在同一行。
脚本会擦除设备Flash再写入合并镜像。看到校验成功及
`Flash complete. Unplug and reconnect the USB-C cable.` 后拔插，再接回Pi。
不需要Arduino编译。未来重烧需再次进入下载模式。

## 2. 更新 GitHub（PC 浏览器）

打开你现有仓库，Add file → Upload files。将以下新版文件放仓库根目录并提交，
不要多套一层 atoms3r-cam-endoscope 文件夹：

- endoscope.py、run_usb.sh、install_pi.sh、install_desktop.sh
- update_endoscope.sh、diagnose_usb.sh、VERSION
- README.md、README_中文.md、INSTALL_V6_0_4_中文.md
- HANDOFF.md、PROTOCOL.md、CHANGELOG.md、TEST_REPORT.md、WORK_STATUS.md

网页打开 VERSION 确认6.0.4，再进行Pi的git pull。
Public仓库可以不上传firmware/、release/及烧录构建工具；Pi运行不需要这些。
**完整ZIP保存在本地或Private仓库**，后续开发者需要固件源码、测试和依赖锁。
不上传不等于删除已经公开的Git历史。

## 3. 树莓派安装（只在 Pi 终端执行）

先退出旧Endoscope。如果原目录是Git clone：

```bash
cd ~/atoms3r-cam-endoscope
git pull --ff-only
chmod +x ./*.sh
./install_pi.sh
sudo reboot
```

若pull提示本地改动，先 `git stash push -u -m before-v604` 再执行pull及后续步骤。
不要立即stash pop，把旧代码混回新版。若提示历史分叉，请保留现场检查，不要reset --hard。
如果原目录不是Git仓库，先改名备份，再从你自己仓库的Code→HTTPS地址clone。

安装脚本配置依赖、video/dialout组、设备专用udev规则，刷新桌面和应用菜单的
Endoscope、Update Endoscope。过时的其他Endoscope桌面入口会备份到
`~/.local/share/endoscope-icon-backup`。

## 4. 检查和启动

Pi重启后接摄像头，执行：

```bash
cd ~/atoms3r-cam-endoscope
./diagnose_usb.sh
```

应看到APP6.0.4、串口by-id含ATOMCAMV604、MJPG视频节点、ttyACM和USB power/control=on。
只看到APP6.0.4但设备仍ATOMCAMV603表示固件未更新。

双击Endoscope：默认全屏，若桌面管理器仅给最大化，约1秒后使用无边框屏幕尺寸。
EXIT或Esc退出。调试窗口使用 `./run_usb.sh --windowed`。

启动后让探头真正静止至少5秒等待标定和静止确认，镜头朝前按ZERO，再按原提示完成
向上抬起的镜头轴确认。运动中ZERO会被拒绝。任何串口重新打开或传感器重置后要重新ZERO。
镜像/上下翻转只改变画面，不改变传感器坐标。

## 5. 日常更新及验收

以后双击Update Endoscope，成功后再点Endoscope。更新器使用安装它的同一目录、
stash保留本地改动、ff-only更新。**不会自动刷摄像头固件**；本次需手动刷，未来看发布说明。

现场验证：连续开关5次；在另一角度静置10分钟再回固定物理方向记录AZ/EL误差；
视频IMU同时运行1小时；拔插自动恢复并提示重新ZERO。软件测试不替代这些实测。

异常时退出程序后收集：

```bash
cd ~/atoms3r-cam-endoscope
./diagnose_usb.sh 2>&1 | tee ~/endoscope-diagnose.txt
cp ~/.cache/endoscope-last-run.log ~/endoscope-last-run.txt
```

漂移记录可单独用 `./run_usb.sh --log ~/endoscope-drift.csv` 启动，记下ZERO和静置时间。
极慢纯水平转动可能被静止阈值抑制；六轴算法没有绝对航向参考，不能保证无限期零漂移。
