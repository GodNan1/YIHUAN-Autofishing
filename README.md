# YIHUAN-Autofishing

基于 OpenCV 模板匹配的自动钓鱼控制脚本（Python）。

项目仓库：
https://github.com/GodNan1/YIHUAN-Autofishing

## 功能概览

- 任务流程：Q_E -> F -> 滑块控制
- 滑块阶段识别：yu/yuxian 锚点、绿色工作区、huakuai 滑块
- 滑块控制策略：越界确认后再 A/D 纠偏，减少抖动
- 支持自动估速：左向 + 右向采样，取中位数后平均
- 支持本地速度缓存：默认写入 slider_speed_cache.json
- 收尾逻辑：click 与 R 互斥检测
- 仅在目标窗口（标题包含关键字，尺寸容差匹配）内工作
- 看门狗超时重置，连续超时可自动停止

## 依赖安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
python src/main.py
```

强制重新校准速度：

```bash
python src/main.py --force-reestimate-speed
```

## 自动估速说明

首次无缓存或使用 `--force-reestimate-speed` 时，程序会进入估速流程：

1. 长按 A 向左移动，采样速度。
2. 短暂停顿后长按 D 向右移动，采样速度。
3. 左右速度各取中位数，再取平均作为最终速度。
4. 默认保存到 `slider_speed_cache.json`。
5. 校准完成后程序结束，需手动重启进入正常流程。

### 宏定义开关（代码级）

在 `src/main.py` 顶部提供了宏定义开关：

```python
CALIBRATION_PURE_MODE = True
```

- `True`：校准阶段进入纯测速模式，只执行 A/D 采样，不执行绿色工作区追踪控制，也不执行 click/R 收尾分支（推荐）。
- `False`：校准阶段允许和常规滑块流程并行，可能被追踪逻辑影响采样一致性。

## 参数说明（与当前代码一致）

### 模板与基础识别

- `--slider-template`：滑块模板图，默认 `huakuai.png`
- `--yu-template`：左锚点模板图，默认 `yu.png`
- `--yuxian-template`：右锚点模板图，默认 `yuxian.png`
- `--f-template`：F 提示模板图，默认 `FFF.png`
- `--qe-template`：Q_E 提示模板图，默认 `Q_E.png`

### 滑块匹配与追踪

- `--slider-threshold`：滑块匹配阈值，默认 `0.75`
- `--slider-search-padding-x`：滑块局部搜索左右扩展像素，默认 `120`
- `--slider-search-padding-y`：滑块局部搜索上下扩展像素，默认 `30`
- `--slider-track-window`：基于上一帧追踪窗口半宽，默认 `180`
- `--slider-max-jump`：单帧允许滑块最大跳变像素，默认 `220`
- `--slider-strong-threshold`：滑块强匹配阈值，默认 `0.90`
- `--slider-recover-threshold`：滑块丢失后重捕获阈值，默认 `0.58`
- `--slider-outside-allowance`：滑块可超出绿色边界容差像素，默认 `30`
- `--slider-lost-frames`：滑块丢失后保留历史帧数，默认 `10`
- `--slider-ema-alpha`：滑块中心平滑系数，默认 `0.65`

### 控制与速度估计

- `--slider-speed-px-per-sec`：初始速度估计（px/s），默认 `550.0`
- `--control-lead-seconds`：控制前瞻时间（秒），默认 `0.06`
- `--predictor-measure-blend`：测量融合系数，默认 `0.70`
- `--speed-cache-file`：速度缓存文件，默认 `slider_speed_cache.json`
- `--use-speed-cache / --no-use-speed-cache`：是否使用速度缓存，默认启用
- `--auto-estimate-speed / --no-auto-estimate-speed`：是否自动估速，默认启用
- `--force-reestimate-speed`：忽略缓存并强制重新估速
- `--speed-calibration-min-dt`：估速采样最小时间间隔（秒），默认 `0.01`
- `--speed-calibration-min-delta`：估速有效位移阈值（像素），默认 `2.0`
- `--speed-calibration-stop-frames`：连续静止帧判定触边停止，默认 `6`
- `--speed-calibration-stage-timeout`：单方向估速超时（秒），默认 `4.0`
- `--speed-calibration-switch-pause`：左右切换前暂停（秒），默认 `0.20`

### 锚点、绿色区域与控制边界

- `--yu-threshold`：yu 匹配阈值，默认 `0.75`
- `--yuxian-threshold`：yuxian 匹配阈值，默认 `0.75`
- `--f-threshold`：F 匹配阈值，默认 `0.60`
- `--qe-threshold`：Q_E 匹配阈值，默认 `0.80`
- `--green-lower-hsv`：绿色下限 HSV，默认 `35,40,40`
- `--green-upper-hsv`：绿色上限 HSV，默认 `90,255,255`
- `--green-min-area`：绿色区域最小面积，默认 `200`
- `--anchor-padding`：左右锚点向内收缩像素，默认 `5`
- `--anchor-vertical-padding`：锚点上下扩展像素，默认 `25`
- `--bound-padding`：绿色边界内缩像素，默认 `5`
- `--dead-zone`：停止按键死区像素，默认 `3`
- `--outside-confirm-frames`：连续越界确认帧数，默认 `2`
- `--anchor-lost-grace-seconds`：锚点短时丢失容忍时长（秒），默认 `0.25`
- `--green-lost-grace-seconds`：绿色区域短时丢失容忍时长（秒），默认 `0.25`
- `--control-target-lost-hold-seconds`：控制目标短时丢失保持按键时长（秒），默认 `0.12`
- `--init-center-assist-seconds`：进入滑块阶段初始中心辅助时长（秒），默认 `2.0`

### 窗口与性能

- `--window-title`：目标窗口标题（包含匹配），默认 `异环`
- `--window-width`：目标窗口宽度，默认 `1600`
- `--window-height`：目标窗口高度，默认 `900`
- `--window-size-tolerance`：窗口尺寸容差像素，默认 `120`
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
