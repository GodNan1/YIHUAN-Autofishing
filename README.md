# OpenCV 模板识别自动控制

本项目通过 OpenCV 模板匹配实现以下控制逻辑：
- 先识别 `Q_E.png`，再识别 `F.png`，之后进入 `huakuai.png` 的 A / D 控制任务
- 识别 `yu.png`（左锚点）和 `yuxian.png`（右锚点）
- 在左右锚点之间识别绿色区域，并使用 A / D 键把 `huakuai.png` 维持在绿色区域内
- `huakuai.png` 使用“绿色区域附近局部匹配 + 历史追踪 + 平滑”混合策略，提高稳定性
- 识别到 `F.png` 时自动按下 F 键
- 识别到 `Q_E.png` 时同样自动按下 F 键
- 识别到 `click.png` 时按随机反应延时点击一次（不依赖外部 OCR 程序）
- `click.png` 与 `R.png` 在收尾阶段互斥：出现 click 时不检测 R；未出现 click 且出现 R 时直接重开下一轮
- 仅在标题包含 `异环` 且大小为 `1600x900` 的窗口中执行识别
- 窗口大小支持“约 1600x900”的容差匹配
- `yu.png`、`yuxian.png` 与 `huakuai.png` 仅在窗口上半部分中间区域匹配
- `Q_E.png` 仅在窗口右下角区域匹配（默认 500x200，从右下角反推）
- `FFF.png` 仅在窗口上半部分中间区域匹配，识别到后按人类反应延时按下 F

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 运行示例

```bash
python src/main.py
```

参数说明：
- `--slider-template`：滑块模板图路径，默认 `huakuai.png`
- `--yu-template`：左锚点模板图路径，默认 `yu.png`
- `--yuxian-template`：右锚点模板图路径，默认 `yuxian.png`
- `--f-template`：F 提示模板图路径，默认 `FFF.png`
- `--qe-template`：Q_E 提示模板图路径，默认 `Q_E.png`
- `--slider-threshold`：滑块匹配阈值，默认 `0.75`
- `--slider-search-padding-x`：滑块局部搜索左右扩展像素，默认 `120`
- `--slider-search-padding-y`：滑块局部搜索上下扩展像素，默认 `30`
- `--slider-track-window`：基于上一帧的追踪窗口半宽，默认 `180`
- `--slider-max-jump`：单帧允许滑块最大跳变像素，默认 `160`
- `--slider-strong-threshold`：滑块强匹配分数阈值，默认 `0.90`
- `--slider-recover-threshold`：滑块丢失后重捕获阈值，默认 `0.62`
- `--slider-outside-allowance`：滑块可超出绿色边界容差像素，默认 `30`
- `--slider-lost-frames`：滑块丢失后保留历史帧数，默认 `6`
- `--slider-ema-alpha`：滑块中心平滑系数(0-1)，默认 `0.65`
- `--slider-speed-px-per-sec`：滑块恒定速度估计（像素/秒），默认 `550`
- `--control-lead-seconds`：控制前瞻时间（秒），默认 `0.06`
- `--predictor-measure-blend`：预测器测量融合系数（0-1），默认 `0.70`
- `--speed-cache-file`：本地速度缓存文件路径，默认 `slider_speed_cache.json`
- `--use-speed-cache/--no-use-speed-cache`：是否启用本地速度缓存，默认启用
- `--auto-estimate-speed/--no-auto-estimate-speed`：是否自动估速，默认启用
- `--force-reestimate-speed`：忽略缓存并重新自动估速
- `--speed-calibration-min-dt`：自动估速采样最小间隔（秒），默认 `0.01`
- `--speed-calibration-min-delta`：自动估速有效位移阈值（像素），默认 `2.0`
- `--speed-calibration-stop-frames`：连续静止帧数达到后判定触边停止，默认 `6`
- `--speed-calibration-stage-timeout`：单方向估速超时（秒），默认 `4.0`
- `--speed-calibration-switch-pause`：左右切换估速前暂停（秒），默认 `0.20`
- `--yu-threshold`：yu 匹配阈值，默认 `0.75`
- `--yuxian-threshold`：yuxian 匹配阈值，默认 `0.75`
- `--f-threshold`：F 图标匹配阈值，默认 `0.60`
- `--qe-threshold`：Q_E 图标匹配阈值，默认 `0.80`
- `--green-lower-hsv`：绿色识别 HSV 下限，默认 `35,40,40`
- `--green-upper-hsv`：绿色识别 HSV 上限，默认 `90,255,255`
- `--green-min-area`：绿色区域最小面积阈值，默认 `200`
- `--anchor-padding`：左右锚点向内收缩像素，默认 `5`
- `--anchor-vertical-padding`：锚点上下扩展像素，默认 `25`
- `--window-title`：目标窗口标题，默认 `异环`
- `--window-width`：目标窗口宽度，默认 `1600`
- `--window-height`：目标窗口高度，默认 `900`
- `--window-size-tolerance`：窗口尺寸容差像素，默认 `120`
- `--enable-blur`：启用模糊匹配（高斯模糊预处理）
- `--blur-kernel`：模糊核大小（奇数），默认 `3`
- `--fps`：窗口存在时识别帧率，默认 `30`
- `--idle-fps`：窗口丢失时识别帧率，默认 `5`（降低占用）
- `--search-width-ratio`：上半部分中间识别区域宽度占比，默认 `0.6`
- `--trigger-region-width`：右下角触发识别区域宽度，默认 `500`
- `--trigger-region-height`：右下角触发识别区域高度，默认 `200`
- `--bound-padding`：绿色区域边界内缩像素，默认 `5`
- `--dead-zone`：停止按键死区像素，默认 `2`
- `--outside-confirm-frames`：连续越界帧数达到后才切换方向，默认 `2`
- `--f-cooldown`：按 F 冷却时间(秒)，默认 `0.5`
- `--f-reaction-min`：按 F 最小随机延时(秒)，默认 `0.10`
- `--f-reaction-max`：按 F 最大随机延时(秒)，默认 `0.20`
- `--f-search-width-ratio`：F 上半部分识别区域宽度占比，默认 `0.60`
- `--click-template`：关闭提示模板图，默认 `click.png`
- `--click-threshold`：click 模板匹配阈值，默认 `0.78`
- `--r-template`：重开提示模板图，默认 `R.PNG`
- `--r-threshold`：R 模板匹配阈值，默认 `0.80`
- `--r-detect-delay-after-click-miss`：click 未命中后延迟检测 R 的秒数，默认 `1.0`
- `--click-cooldown`：click 模板点击冷却(秒)，默认 `1.0`
- `--click-reaction-min`：click 模板点击最小随机延时(秒)，默认 `0.10`
- `--click-reaction-max`：click 模板点击最大随机延时(秒)，默认 `0.20`

## 3. 校准说明（自动估速 + 本地缓存）

首次使用（建议）：
1. 直接运行 `python src/main.py`，保持场景正常进行到滑块阶段。
2. 程序会自动执行双向校准：先长按 `A` 到左边停住，再长按 `D` 到右边停住。
3. 自动估出左右速度后取平均值，并写入 `slider_speed_cache.json`（默认路径）。
4. 校准完成后程序会自动结束，本次不再继续执行正常流程。

后续日常运行：
1. 手动重启任务后，默认会优先读取本地缓存速度，不再重复估速。
2. 若要临时禁用缓存，可加 `--no-use-speed-cache`。

需要重新校准时：
1. 使用 `python src/main.py --force-reestimate-speed`。
2. 本次运行会忽略旧缓存并重新估速，完成后覆盖缓存文件并自动结束任务。

估速稳定性建议：
1. 触边停住判定太慢：减小 `--speed-calibration-stop-frames`。
2. 校准阶段容易超时：增大 `--speed-calibration-stage-timeout`。
3. 检测噪声大：增大 `--speed-calibration-min-delta`。

控制策略说明：
1. 滑块只要位于绿色工作区内就不会主动移动，不再追逐工作区中心。
2. 仅当滑块连续多帧越出绿色边界后，才会触发 A/D 纠偏。

## 4. 结果

程序运行后会持续截图识别并自动按键，按 `Ctrl + C` 停止。

建议：
- 目标窗口保持可见并尽量固定缩放比例
- 若识别不稳定，调高或调低对应阈值
