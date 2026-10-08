# UV 荧光模式使用说明（v6.0.5，测试版，基于现场验证的 6.0.4；不含屏幕陀螺仪 —— 6.1.0+UV 的版本是 v6.2.0，在 `uv-mode-screengyro` 分支）

## 操作员用法

- 左侧按钮列（`FLIP U/D` 下方）新增 `UV MODE`：灰色 `#455a64` 表示关闭，紫色 `#7b1fa2` 表示已开启。开启后画面底部会出现一条半透明的按钮条，不会挡住图像。
- 底部按钮条从左到右三个独立开关 + 一个退出：
  - `BOOST` — 把检测到的荧光区域提亮、加饱和度，便于看清；
  - `SMART BOX` — 在检测到的区域画框和标签；
  - `FILTER` — 整体滤镜（降蓝光、调色温/曝光/伽马），让画面看起来更像专用 UV 设备拍的；
  - `EXIT UV` — 退出 UV 模式，回到普通画面（和再点一次 `UV MODE` 效果一样）。
  - 绿色 = 开，灰色 = 关；出厂默认 `BOOST` 开、`SMART BOX` 开、`FILTER` 关。
- 三个开关的状态会**永久记住**（存在 `~/.config/endoscope.json` 里），但**每次开机 App 总是以普通模式启动**——即使上次退出前是 UV 模式，也只是开关记忆被保留，UV 模式本身需要重新点 `UV MODE` 进入。进入 UV 模式后会自动恢复上次的三个开关状态。
- 没有信号（无画面）时按钮条依然显示文字并且可以点击。
- 状态栏在 UV 模式下会多出一个 `UV` 字样。

## 工程师调参抽屉

- 只有在 UV 模式下，屏幕右边缘会出现一个很小的箭头标签 `◀`；点一下拉出调参抽屉（宽度约为屏幕宽度的 38%，画面会相应收窄但始终完整可见，不会被遮住）；箭头变成 `▶`，再点一次收起抽屉、画面恢复原宽度。
- 抽屉内容偏多时可以用手指上下拖动滚动，也支持鼠标滚轮（仅 Windows 预览时）；从数值滑条上开始的拖动是在调值，从空白处开始的拖动才是滚动。
- 参数按三个功能分组（名字、含义均取自 UVScope 1.1 的调参面板，只是做了精简）：
  - **FILTER**：strength（滤镜强度）、blue cut（降蓝光程度）、warmth（暖色调）、exposure（曝光）、gamma（伽马）；
  - **DETECTION**：dye type（染料/样本类型，点一下在几种预设间循环）、sensitivity（灵敏度）、hue min / hue max（色相范围）、saturation min（最小饱和度）、brightness min（最小亮度）、reject UV wash（开关，剔除整屏泛蓝/泛紫的误判）、rejection strength（剔除强度）、min region %（最小区域占比）、merge nearby px（相邻区域合并距离）、noise filter px（去噪开运算像素）；
  - **BOOST**：saturation gain（饱和度增益）、brightness gain（亮度增益）、blue cut inside（区域内部降蓝光）、edge feather px（边缘羽化像素）；
  - **BOX**：line width（框线宽度）、box colour（框颜色，在洋红/绿/青/黄/红/白之间循环）、show ID & area %（是否显示编号和面积占比）、outline（是否描边）、steadiness（框体平滑程度，对应追踪器的 track_alpha）。
- 每一行的数值旁边有大号 `−`/`+` 按钮（点一下走一格）和一条可拖动的滑条（拖动直接改值）。
- 抽屉顶部：实时处理耗时 `UV n ms`；方案（preset）选择器——点一下弹出列表，`FACTORY`（出厂值）加所有已保存方案，**最新保存的排最前面**，点选即可切换；`SAVE`、`DELETE`、`EXPORT` 三个按钮。
- `SAVE`：总是**新建**一个方案，自动命名为 `HH:MM DD-MM`（比如 `14:32 07-10`）；同一分钟内重复保存会自动加 `(2)`、`(3)` 这样的后缀。保存后这个新方案立刻变成当前激活方案。
- 当前抽屉里的数值与激活方案不一致时，方案名旁边会出现 `*` 标记；**没有点 SAVE 的修改在切换方案或重启后会丢失**——重启或切换方案都会把抽屉数值还原成激活方案保存的值。
- `DELETE`：需要连续点两次（3 秒内）才会真正删除，第一次点会提示确认；`FACTORY` 是代码内置的出厂方案，**不能修改也不能删除**，不会出现在可删除列表里受影响。

## 导出

- `EXPORT` 按钮：优先把文件写到已挂载、可写的 U 盘下的 `Endoscope_UV_presets/` 文件夹（树莓派自动挂载路径形如 `/media/<用户名>/<U盘名>/`），找不到可用 U 盘时写到 `~/Endoscope_UV_presets/`。写完会在画面上弹出实际写入路径。
- 每个方案单独导出一个 UVScope 1.1 兼容的 `uv_params_<日期时间>_<方案名>.json`，同时再生成一个包含全部方案的汇总文件 `uv_presets_all_<日期时间>.json`。
- 导出的 `.json` 文件可以直接拿到 Windows 上的 UVScope 1.1 里用 **"Load config"（载入配置）** 打开，字段完全一致。
- **如何把一个导出的方案变成下一个版本的出厂值**：两种办法——
  1. 打开导出的 `.json`，把里面的参数值手动抄到 `endoscope.py` 里的 `UV_FACTORY` 字典（第 2822 行附近）对应的键上，然后升级版本号重新打包；
  2. 或者把导出的 `.json` 文件直接交给开发者，由开发者改代码、测试、出新版本。
  - 普通使用者自己不需要、也不应该去改 `endoscope.py`。

## 正式版隐藏调参抽屉

- 调参抽屉是测试版特性，正式发布前可以整体隐藏：把 `~/.config/endoscope.json` 里的 `"uv_tuning_panel"` 改成 `false`（代码里的默认值是 `true`；这个键不会自动写入配置文件，必须手动加进去改成 `false` 才会隐藏抽屉）。
- 改为 `false` 后，UV 模式下右边缘不会出现任何箭头标签，操作员只能用底部按钮条的三个开关，看不到、也碰不到调参功能。
- 三个开关本身（BOOST/SMART BOX/FILTER）不受这个开关影响，正式版和测试版操作员体验一致。

## 文件位置

- `~/.config/endoscope.json`：
  - `"uv"` 键 —— 三个开关的记忆状态，形如 `{"boost": true, "boxes": true, "filter": false}`；
  - `"uv_tuning_panel"` 键 —— 是否显示调参抽屉（见上一节）。
- `~/.config/endoscope_uv_presets.json`：所有保存的调参方案和当前激活的方案 id（`FACTORY` 本身不存在这个文件里，它写死在代码中）。
- 日志：`~/.cache/endoscope-last-run.log`（和普通模式共用同一份日志，UV 处理出错的 traceback 也打在这里）。

## 注意事项

- 当前的检测阈值（色相/饱和度/亮度范围等）是对着 YouTube 上的 UV 荧光参考视频调出来的，**还没有在真实摄像头 + 真实 UV 灯的现场环境下验证过**。正式用于现场前，务必用真实设备和真实样本试一遍；如果检测偏松/偏紧，可以在调参抽屉里现场调整参数，调好以后用 `EXPORT` 导出保存，再按上一节的方法考虑是否做成下一版的出厂值。
- 即使 `FILTER` 开关处于关闭状态，出厂的 `exposure 1.16` / `gamma 1.27` 仍会让 UV 模式下的画面整体比普通模式略亮一些——这不是 bug，UVScope 1.1 本身就是这个行为，为了保证导出文件和 UVScope 1.1 互通，这里保持一致。
- 如果画面上弹出 `UV PROCESSING ERROR`（最多每 10 秒弹一次），说明某一帧的 UV 处理或绘制抛出了异常，画面会自动回退显示原始视频，不会卡死或崩溃；具体原因看 `~/.cache/endoscope-last-run.log` 里的 traceback。

## 开发者：在 Windows 上预览与跑测试

- 预览窗口（用录好的视频文件在 Windows 上模拟摄像头输入，可以直接点按钮、拉抽屉）：

  ```bash
  py -3 tools/uv_preview.py --shots DIR --seconds 14 [--geometry 800x480] [--video PATH]
  ```

  `--shots DIR` 指定截图输出目录，`--seconds` 运行秒数，`--geometry` 可选窗口尺寸（默认贴近树莓派触屏比例），`--video` 指定循环播放的视频文件（不指定则用内置样例）。

- 跑所有测试（Windows 下除 `test_uv_core.py` 外都需要 `PYTHONPATH=../winshim`，因为用到了 Unix 专属的 `fcntl`，`winshim` 是仅供本机测试用的占位实现）：

  ```bash
  py -3 test_uv_core.py
  PYTHONPATH=../winshim py -3 test_uv_ui.py
  PYTHONPATH=../winshim py -3 test_uv_presets.py
  PYTHONPATH=../winshim py -3 test_endoscope_core.py
  PYTHONPATH=../winshim py -3 test_v604.py
  ```

  （本分支没有 `test_screen_gyro.py`——不含屏幕陀螺仪功能。）

- `tools/uv_bench.py` 单独测 UV 处理每帧耗时（320x240 和 640x480），Windows 开发机的数字只是估算，不代表树莓派 5 的真实表现。
