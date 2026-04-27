# YIHUAN-Autofishing

基于 OpenCV 模板匹配的自动控制脚本（自动按键＋视觉识别）。

快速上手

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 在项目根目录启动（推荐）：

```bash
python src/main.py
```

3. 如果需要强制重校准（只做一次速度校准并保存）：

```bash
python src/main.py --force-reestimate-speed
```

说明与建议

- 程序默认流程：`Q_E` -> `F` -> `滑块（huakuai）`。
- 首次运行或使用 `--force-reestimate-speed` 时会自动进行滑块速度校准（先 A 向左，后 D 向右），结果写入 `slider_speed_cache.json`。
- 为避免校准被控制逻辑干扰，代码提供了两个代码级开关（位于 `src/main.py` 顶部）：
	- `CALIBRATION_PURE_MODE`：若为 `True`，校准阶段仅执行 A/D 速度采样，不会执行绿色工作区追踪与 click/R 收尾。
	- `FORCE_REESTIMATE_SPEED_ON_START`：若为 `True`，程序启动时等价于传入 `--force-reestimate-speed`，用于在没有命令行参数时仍强制校准一次。

- 推荐做法：首次设置并确认模板、窗口后运行一次强制校准；校准完成并验证后将 `FORCE_REESTIMATE_SPEED_ON_START` 设回 `False`。

常见问题

- 如果找不到模板（FileNotFoundError），请从项目根目录运行，或通过参数显式指定模板路径，例如：

```bash
python src/main.py --slider-template huakuai.png --yu-template yu.png ...
```

- 如果程序在某个阶段卡住，看门狗（`--watchdog-timeout`）会触发并尝试恢复到检测到的阶段；看门狗重试上限已限制为 3 次，连续超时达上限会退出程序。

重要参数（摘要）

- 模板与提示图：`--slider-template`、`--yu-template`、`--yuxian-template`、`--f-template`、`--qe-template`
- 速度与校准：`--slider-speed-px-per-sec`、`--use-speed-cache`、`--force-reestimate-speed`、`--speed-calibration-*`
- 滑块匹配/追踪：`--slider-threshold`、`--slider-search-padding-x`、`--slider-track-window`、`--slider-ema-alpha` 等
- 视觉/颜色：`--green-lower-hsv`、`--green-upper-hsv`、`--green-min-area`
- 看门狗与性能：`--watchdog-timeout`（默认 40s）、`--fps`、`--idle-fps`

更多细节与完整参数列表请查看 `src/main.py` 内 `build_parser()` 的参数注释。

停止程序：在终端按 `Ctrl + C`。

注：请始终在项目根目录运行脚本以保证相对模板路径正确。
- `--enable-blur`：启用高斯模糊再匹配
- `--blur-kernel`：高斯模糊核大小（奇数），默认 `3`
- `--fps`：窗口存在时识别帧率，默认 `30.0`
- `--idle-fps`：窗口丢失时识别帧率，默认 `5.0`
- `--search-width-ratio`：上半中间搜索区域宽度占比，默认 `0.6`
- `--trigger-region-width`：右下触发区域宽度，默认 `500`
- `--trigger-region-height`：右下触发区域高度，默认 `200`

### F 触发与反应时间

- `--f-cooldown`：按 F 冷却时间（秒），默认 `0.5`
- `--f-reaction-min`：按 F 最小随机延时（秒），默认 `0.10`
- `--f-reaction-max`：按 F 最大随机延时（秒），默认 `0.20`
- `--f-search-width-ratio`：F 识别区域宽度占比，默认 `0.60`

### click / R 收尾

- `--click-template`：click 模板图，默认 `click.png`
- `--click-threshold`：click 匹配阈值，默认 `0.78`
- `--r-template`：R 模板图，默认 `R.PNG`
- `--r-threshold`：R 匹配阈值，默认 `0.80`
- `--r-detect-delay-after-click-miss`：click miss 后延迟检测 R（秒），默认 `1.0`
- `--click-cooldown`：click 点击冷却（秒），默认 `1.0`
- `--click-detect-duration`：收尾阶段 click 检测持续时长（秒），默认 `10.0`
- `--click-disappear-frames`：click 消失判定连续帧数，默认 `3`
- `--click-reaction-min`：click 最小随机延时（秒），默认 `0.10`
- `--click-reaction-max`：click 最大随机延时（秒），默认 `0.20`

### 看门狗

- `--watchdog-timeout`：无完整循环重置超时（秒），默认 `40.0`，`0` 表示禁用
- `--watchdog-max-consecutive-timeouts`：看门狗连续超时后自动停止阈值，默认 `10`

## 运行建议

- 保持目标窗口可见，分辨率尽量接近配置值。
- 首次建议先做一次 `--force-reestimate-speed` 校准。
- 若速度校准波动较大，优先检查模板图质量和锚点识别稳定性。

## 停止方式

运行中按 `Ctrl + C` 停止。
