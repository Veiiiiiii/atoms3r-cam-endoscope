# 屏幕 IMU：XIAO nRF52840 Sense 烧录与安装

本目录的 `screen_imu.ino` 给安装在显示屏机身上的 **Seeed Studio XIAO
nRF52840 Sense** 使用。必须是带 LSM6DS3TR-C 的 **Sense** 版本。固件以
100 Hz 发送 48 字节 `SIMU` 二进制记录；USB 数据口不会输出调试文字。

## Arduino IDE 烧录步骤

1. 安装 Arduino IDE 2.x。打开 **File > Preferences > Additional Boards
   Manager URLs**，加入：
   `https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json`
2. 打开 **Tools > Board > Boards Manager**，搜索 `seeed nrf52`，安装
   **Seeed nRF52 Boards 1.1.13**。
3. 打开 **Tools > Manage Libraries**，搜索并安装
   **Seeed Arduino LSM6DS3 2.0.7**（本项目固定使用此版本）。
4. 打开 `screen_imu/screen_imu.ino`，在 **Tools > Board** 选择
   **Seeed XIAO nRF52840 Sense**，再在 **Tools > Port** 选择 XIAO 的串口。
5. 点击 **Upload**。如果普通上传找不到端口，快速双击板上的 RESET，等待
   UF2 启动盘/bootloader 串口出现，重新选择该端口后再点 **Upload**。
6. 拔插 XIAO，用数据 USB-C 线接到树莓派。应用会按 Seeed/XIAO/nRF52 的
   USB 身份自动识别；也可用 `--screen-imu-port /dev/serial/by-id/...` 指定。

不要用串口监视器读取该端口；它是应用的纯二进制数据口，115200 baud。

## 故障排查：状态栏一直不显示 `SCR ok`

固件会在 `setup()` 里显式给 LSM6DS3TR-C 的电源脚（P1.08，宏
`PIN_LSM6DS3TR_C_POWER`）上电后再 `begin()`，因此正常情况下开机即可工作。
若始终不到 `SCR ok`（应用状态栏也可能显示 `SCR retryN`）：
1. 确认板子确为 **Sense** 版（普通 XIAO nRF52840 无 IMU）。
2. 确认所选开发板是 **Seeed XIAO nRF52840 Sense**（不是非 Sense 版）——
   选错会导致 `PIN_LSM6DS3TR_C_POWER` 宏不存在、IMU 供电未打开。
3. 换一根**数据** USB-C 线（有些线只供电不传数据）。
4. 快速双击 RESET 重进 UF2 模式重新烧录。

## 安装方向、轴与符号检查

数据保持 LSM6DS3TR-C 的原生右手 X/Y/Z，不在固件里换轴。以 XIAO 元件面
朝向观察者、USB-C 插座位于板的一端为物理参照：X/Y 位于 PCB 平面内（分别
沿短边和长边），Z 垂直于 PCB；最终正方向以 Seeed XIAO nRF52840 Sense
pinout 上的 IMU 坐标标记为准。安装后把板牢固贴在**屏幕机身**上，不能贴在
摄像头或软线缆上。

首次使用：启动应用，保持摄像头和屏幕不动约 3 秒让屏幕陀螺仪校准，然后按
ZERO。保持摄像头不动，只把屏幕向右转：箭头应相对新屏幕方向向左。如果方向
相反，把配置 `screen_imu.sign` 从 `1` 改为 `-1`，或启动时加
`--screen-imu-sign -1`，然后重新 ZERO。屏幕与摄像头一起转动时，相对方位应
基本不变。

两个 IMU 都只有 6 轴，没有磁力计；两者的航向漂移差会逐渐出现。需要时重新
ZERO。这不提供绝对罗盘航向。

## 可选 UART1（未做硬件验证）

默认只启用 USB-CDC。若必须使用杜邦线，可取消草图顶部
`#define USE_UART1` 的注释；它会改为从 `Serial1` 以 115200 baud 发送同一
48 字节记录。使用 XIAO 的 D6/TX 接收端 RX，并共地。该路径默认关闭，且未在
实物上测试，不属于 USB-CDC 合同测试范围。
