# 烧录说明（唯一受支持的方式）

**结论先写在前面：只用 Windows CMD + esptool 烧 `release/` 里的预编译镜像，写到 `0x0`。
不要用 Arduino IDE 编译烧录。** 你手上能正常工作的机器全部来自这条路，这不是巧合。

---

## 1. 为什么 Arduino 烧完一定花屏

不是运气问题，是四项底层配置对不上。AtomS3R-CAM 用的是 **ESP32-S3-PICO-1-N8R8**：
8MB Flash + **8MB 八线（Octal / OPI）PSRAM**。本仓库 `firmware/sdkconfig` 里的实际取值：

| 项目 | 本固件要求 | Arduino「ESP32S3 Dev Module」默认 | 后果 |
|---|---|---|---|
| PSRAM 模式 | `CONFIG_SPIRAM_MODE_OCT=y`（八线，80MHz） | QSPI 四线，或直接关闭 | **摄像头帧缓冲分配在 PSRAM 上。PSRAM 模式错 → 帧缓冲读回来是垃圾 → 花屏。这一条就是花屏的直接原因。** |
| Flash 模式 | `CONFIG_ESPTOOLPY_FLASHMODE="dio"` | QIO | 八线 PSRAM 占用了 QIO Flash 要用的同一组引脚，总线冲突 |
| Flash 容量 | `CONFIG_ESPTOOLPY_FLASHSIZE="8MB"` | 4MB | bootloader 头里的容量写错，0x210000 之后的分区落在映射之外 |
| 分区表 | 自定义，`0x8000`；`factory` 在 `0x10000` 占 2M；`assetpool`(type 233/0x23) 在 `0x210000` 占 2M | Arduino 自带方案，无 `assetpool` | 分区查找全部失效 |

`sdkconfig` 里有一处特别能说明问题：

```
CONFIG_ESPTOOLPY_FLASHMODE_QIO=y
CONFIG_ESPTOOLPY_FLASHMODE="dio"      <-- 解析结果是 dio，不是 qio
```

即使 menuconfig 里勾的是 QIO，ESP-IDF 因为检测到 `SPIRAM_MODE_OCT` 而**自动把 Flash 降级成
DIO**。`firmware/merge_firmware.sh` 里也是硬写死的
`--flash_mode dio --flash_freq 80m --flash_size 8MB`。Arduino IDE 不做这个降级，
你在板卡菜单里选什么就烧什么，于是烧出来的就是花屏机。

**所以不存在「Arduino 也能烧好」的写法。** 下面只给 CMD 这一条路。

---

## 2. 镜像本身（已核对）

`release/atoms3r_cam_uvc_imu_v6_0_1.bin` 是一个**合并镜像**，逐字节确认过：

| 偏移 | 内容 | 校验 |
|---|---|---|
| `0x0` | bootloader | 魔数 `E9`；`spi_mode=0x02`(DIO)；`size/speed=0x3F`(8MB / 80MHz)；`chip_id=9`(ESP32-S3) |
| `0x8000` | 分区表 | 魔数 `AA50` |
| `0x10000` | 应用程序 | 魔数 `E9` |

总长 `832,656` 字节（`0xCB490`）。它**不含** AssetPool（v6 是纯 UVC 设备，没有屏幕 UI 资源，
用不到），所以文件比 `merge_firmware.sh` 的四段合并要短，这是正常的。

SHA-256：`ff7cdfaf4f2e012d19e7295fedffbdb62a4aafed085579e564c400d4262b2361`

---

## 3. 烧录步骤（Windows CMD）

**一次性准备**

```
py -m pip install "esptool==4.8.1"
```

**每次烧录**

1. **进下载模式。** 长按机身侧面 reset 键约 2 秒，直到内部绿灯亮起再松手。
   这一步不能跳过，原因见第 4 节。
2. 在设备管理器里看「端口 (COM 和 LPT)」，记下 COM 号。
3. 校验镜像没在传输中损坏：
   ```
   certutil -hashfile release\atoms3r_cam_uvc_imu_v6_0_1.bin SHA256
   ```
   输出应与上面的 SHA-256 一致。
4. 烧录（把 COM6 换成你的端口）：
   ```
   flash_prebuilt_windows.bat COM6
   ```
   或者手打这两条，效果完全相同：
   ```
   py -m esptool --chip esp32s3 --port COM6 erase_flash
   py -m esptool --chip esp32s3 --port COM6 --baud 921600 write_flash -z ^
       --flash_mode dio --flash_freq 80m --flash_size 8MB ^
       0x0 release\atoms3r_cam_uvc_imu_v6_0_1.bin
   ```
5. 拔掉 USB-C，重新插上。

`erase_flash` 不能省。旧的 NVS / 出厂分区留在 Flash 里会被新固件当成自己的配置读进去。

**关于那三个显式的 `--flash_mode dio --flash_freq 80m --flash_size 8MB`：**
合并镜像的头里本来就写好了这些值，esptool 4.x 默认 `keep` 会原样保留。写成显式的是为了
不依赖 esptool 版本的默认行为——esptool 各大版本改过这些默认值，显式写死就不会因为
升级 pip 包而突然烧出一台花屏机。

---

## 4. 烧完 v6 之后，端口会消失（这是正常的）

v6 固件用 TinyUSB 把 USB 口接管成了 **UVC 摄像头 + CDC 串口**的复合设备。
ROM 里那个 USB-Serial-JTAG 下载口不再出现。

**后果：以后每次重烧都必须先手动长按 reset 进下载模式**，esptool 没法像空白板那样
自动把它复位进下载模式。看到 `Failed to connect / No serial data received`，
99% 是忘了这一步，不是板子坏了。

---

## 5. 烧录成功的判据

Windows 上：打开「相机」应用，应该能选到 AtomS3R 的画面。

树莓派上：

```
ls -l /dev/video* /dev/ttyACM*
```

两类节点都要在。`/dev/video*` 是画面，`/dev/ttyACM*` 是 IMU，它们是**同一个物理设备的
两个接口**，不是两根线。只出现一个说明固件没烧全，重烧。

再确认协商到的分辨率：

```
v4l2-ctl --device /dev/video0 --list-formats-ext
```

列表里第一项应该是 **MJPG 320x240 @30fps**。这就是主机端 `endoscope.py` 现在默认请求的
尺寸——固件公布的 640x480 只有 15fps，而 ESP32-S3 只有 USB 全速，等时带宽还要分给 IMU
的 CDC 接口，VGA 能协商成功但根本喂不满（实测 2fps，然后流饿死）。

---

## 6. 要恢复出厂固件

用 M5Burner / EasyLoader 刷回 M5Stack 官方 demo 即可，本仓库的 `erase_flash` 不会造成
不可逆的改动。
