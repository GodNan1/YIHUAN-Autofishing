import argparse
import json
import random
import time
import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np
import pyautogui
from PIL import ImageGrab

try:
    import pygetwindow as gw
except ImportError:
    gw = None


MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

VK_CODE_MAP = {
    "a": 0x41,
    "d": 0x44,
    "f": 0x46,
    "q": 0x51,
    "e": 0x45,
    "esc": 0x1B,
    "space": 0x20,
    "enter": 0x0D,
}


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_uint),
        ("time", ctypes.c_uint),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyboardInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_uint), ("u", _InputUnion)]


def send_left_click_sendinput(screen_x: int, screen_y: int) -> None:
    user32 = ctypes.windll.user32
    screen_width = max(1, int(user32.GetSystemMetrics(0)) - 1)
    screen_height = max(1, int(user32.GetSystemMetrics(1)) - 1)
    absolute_x = int(screen_x * 65535 / screen_width)
    absolute_y = int(screen_y * 65535 / screen_height)

    inputs = (_Input * 3)()
    inputs[0].type = INPUT_MOUSE
    inputs[0].mi = _MouseInput(absolute_x, absolute_y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
    inputs[1].type = INPUT_MOUSE
    inputs[1].mi = _MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None)
    inputs[2].type = INPUT_MOUSE
    inputs[2].mi = _MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None)
    user32.SendInput(3, ctypes.byref(inputs), ctypes.sizeof(_Input))


def _vk_code_for_key(key: str) -> int:
    normalized = key.strip().lower()
    if len(normalized) == 1 and "a" <= normalized <= "z":
        return ord(normalized.upper())
    if normalized not in VK_CODE_MAP:
        raise ValueError(f"Unsupported key for SendInput: {key}")
    return VK_CODE_MAP[normalized]


def send_key_sendinput(key: str, key_up: bool = False) -> None:
    user32 = ctypes.windll.user32
    input_event = _Input()
    input_event.type = INPUT_KEYBOARD
    input_event.ki = _KeyboardInput(_vk_code_for_key(key), 0, KEYEVENTF_KEYUP if key_up else 0, 0, None)
    user32.SendInput(1, ctypes.byref(input_event), ctypes.sizeof(_Input))


def press_key_sendinput(key: str) -> None:
    # For ESC use scan code injection which some games accept when VK events are ignored
    if key.strip().lower() == "esc":
        user32 = ctypes.windll.user32
        vk = _vk_code_for_key(key)
        sc = user32.MapVirtualKeyW(vk, 0)
        # key down via scan code
        evt = _Input()
        evt.type = INPUT_KEYBOARD
        evt.ki = _KeyboardInput(0, sc, KEYEVENTF_SCANCODE, 0, None)
        user32.SendInput(1, ctypes.byref(evt), ctypes.sizeof(_Input))
        time.sleep(0.03)
        # key up via scan code
        evt_up = _Input()
        evt_up.type = INPUT_KEYBOARD
        evt_up.ki = _KeyboardInput(0, sc, KEYEVENTF_KEYUP | KEYEVENTF_SCANCODE, 0, None)
        user32.SendInput(1, ctypes.byref(evt_up), ctypes.sizeof(_Input))
        time.sleep(0.01)
        # Fallback for games that only respond to legacy keybd_event path
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    else:
        send_key_sendinput(key, key_up=False)
        send_key_sendinput(key, key_up=True)


# Macro-like switch: when enabled, auto speed calibration only runs A/D sampling
# and disables slider tracking control plus click/R tail workflow.
CALIBRATION_PURE_MODE = False

# Macro-like switch: when enabled, startup behaves as if
# --force-reestimate-speed is provided from command line.
FORCE_REESTIMATE_SPEED_ON_START = False


@dataclass
class MatchBox:
    left: int
    top: int
    width: int
    height: int
    score: float

    @property
    def center_x(self) -> int:
        return self.left + self.width // 2

    @property
    def center_y(self) -> int:
        return self.top + self.height // 2

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class SearchRegion:
    image: np.ndarray
    offset_x: int
    offset_y: int


@dataclass
class WindowRect:
    left: int
    top: int
    width: int
    height: int
    hwnd: Optional[int] = None


class ConstantSpeedPredictor:
    """Predict slider center by measured position + key-driven constant speed."""

    def __init__(self, speed_px_per_sec: float) -> None:
        self.speed_px_per_sec = max(1.0, speed_px_per_sec)
        self.estimated_x: Optional[float] = None
        self.last_update_time: Optional[float] = None

    def reset(self) -> None:
        self.estimated_x = None
        self.last_update_time = None

    def on_measurement(self, measured_x: float, now: float, blend: float) -> float:
        blend = max(0.0, min(1.0, blend))
        if self.estimated_x is None:
            self.estimated_x = float(measured_x)
        else:
            self.estimated_x = blend * float(measured_x) + (1.0 - blend) * self.estimated_x
        self.last_update_time = now
        return self.estimated_x

    def predict(self, now: float, key: Optional[str]) -> Optional[float]:
        if self.estimated_x is None:
            return None
        if self.last_update_time is None:
            self.last_update_time = now
            return self.estimated_x

        dt = max(0.0, now - self.last_update_time)
        self.estimated_x += self._velocity_for_key(key) * dt
        self.last_update_time = now
        return self.estimated_x

    def predict_at(self, future_time: float, key: Optional[str]) -> Optional[float]:
        if self.estimated_x is None:
            return None
        if self.last_update_time is None:
            return self.estimated_x
        dt = max(0.0, future_time - self.last_update_time)
        return self.estimated_x + self._velocity_for_key(key) * dt

    def _velocity_for_key(self, key: Optional[str]) -> float:
        if key == "d":
            return self.speed_px_per_sec
        if key == "a":
            return -self.speed_px_per_sec
        return 0.0


def parse_hsv_triplet(text: str) -> Tuple[int, int, int]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("HSV must be H,S,V")
    try:
        h, s, v = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("HSV values must be integers") from exc
    if not (0 <= h <= 179 and 0 <= s <= 255 and 0 <= v <= 255):
        raise argparse.ArgumentTypeError("HSV out of range: H[0,179], S/V[0,255]")
    return h, s, v


def load_template(path: Path) -> np.ndarray:
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Failed to load template: {path}")
    return image


def load_optional_template(path: Path) -> Optional[np.ndarray]:
    if not path.exists():
        return None
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    return image


def load_speed_cache(path: Path) -> Optional[float]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    speed_value = data.get("slider_speed_px_per_sec")
    if not isinstance(speed_value, (int, float)):
        return None
    speed = float(speed_value)
    if speed <= 1.0:
        return None
    return speed


def save_speed_cache(path: Path, speed_px_per_sec: float) -> None:
    payload = {
        "slider_speed_px_per_sec": float(speed_px_per_sec),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def find_target_window(title_part: str, width: int, height: int, tolerance: int) -> Optional[WindowRect]:
    if gw is None:
        return None

    target_title = title_part.lower().strip()
    candidates = gw.getAllWindows()
    for window in candidates:
        title = (window.title or "").lower()
        if target_title not in title:
            continue
        if window.width <= 0 or window.height <= 0:
            continue
        if abs(window.width - width) <= tolerance and abs(window.height - height) <= tolerance:
            hwnd = getattr(window, "_hWnd", None)
            return WindowRect(window.left, window.top, window.width, window.height, hwnd=hwnd)
    return None


def bring_window_foreground(hwnd: Optional[int]) -> None:
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    except Exception:
        pass


def capture_window_images(window_rect: WindowRect) -> Tuple[np.ndarray, np.ndarray]:
    left = max(0, int(window_rect.left))
    top = max(0, int(window_rect.top))
    right = max(left + 1, int(window_rect.left + window_rect.width))
    bottom = max(top + 1, int(window_rect.top + window_rect.height))

    screenshot = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    rgb = np.array(screenshot)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return bgr, gray


def locate_template(
    haystack_gray: np.ndarray,
    template_gray: np.ndarray,
    threshold: float,
    enable_blur: bool,
    blur_kernel: int,
) -> Optional[MatchBox]:
    if haystack_gray is None or template_gray is None:
        return None

    h, w = haystack_gray.shape[:2]
    th, tw = template_gray.shape[:2]
    if th > h or tw > w:
        return None

    source = haystack_gray
    tpl = template_gray
    if enable_blur:
        source = cv2.GaussianBlur(source, (blur_kernel, blur_kernel), 0)
        tpl = cv2.GaussianBlur(tpl, (blur_kernel, blur_kernel), 0)

    result = cv2.matchTemplate(source, tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None

    return MatchBox(
        left=int(max_loc[0]),
        top=int(max_loc[1]),
        width=int(tw),
        height=int(th),
        score=float(max_val),
    )


def to_absolute_box(box: Optional[MatchBox], offset_x: int, offset_y: int) -> Optional[MatchBox]:
    if box is None:
        return None
    return MatchBox(
        left=box.left + offset_x,
        top=box.top + offset_y,
        width=box.width,
        height=box.height,
        score=box.score,
    )


def clamp_region(gray: np.ndarray, left: int, top: int, right: int, bottom: int) -> Optional[SearchRegion]:
    h, w = gray.shape[:2]
    left = max(0, min(left, w - 1))
    top = max(0, min(top, h - 1))
    right = max(left + 1, min(right, w))
    bottom = max(top + 1, min(bottom, h))
    if right <= left or bottom <= top:
        return None
    return SearchRegion(gray[top:bottom, left:right], left, top)


def build_upper_middle_region(gray: np.ndarray, width_ratio: float) -> SearchRegion:
    h, w = gray.shape[:2]
    region_width = max(10, int(w * max(0.1, min(1.0, width_ratio))))
    left = (w - region_width) // 2
    top = 0
    right = left + region_width
    bottom = h // 2
    return clamp_region(gray, left, top, right, bottom)


def build_center_region(gray: np.ndarray, width_ratio: float, height_ratio: float) -> SearchRegion:
    h, w = gray.shape[:2]
    region_width = max(10, int(w * max(0.1, min(1.0, width_ratio))))
    region_height = max(10, int(h * max(0.1, min(1.0, height_ratio))))
    left = (w - region_width) // 2
    top = (h - region_height) // 2
    right = left + region_width
    bottom = top + region_height
    return clamp_region(gray, left, top, right, bottom)


def build_bottom_right_region(gray: np.ndarray, region_width: int, region_height: int) -> SearchRegion:
    h, w = gray.shape[:2]
    rw = max(10, min(region_width, w))
    rh = max(10, min(region_height, h))
    left = w - rw
    top = h - rh
    return clamp_region(gray, left, top, w, h)


def build_box_region(gray: np.ndarray, left: int, top: int, right: int, bottom: int) -> Optional[SearchRegion]:
    return clamp_region(gray, left, top, right, bottom)


def build_between_anchors_region(
    gray: np.ndarray,
    yu_box: MatchBox,
    yuxian_box: MatchBox,
    horizontal_padding: int,
    vertical_padding: int,
) -> Optional[SearchRegion]:
    left_anchor = min(yu_box.center_x, yuxian_box.center_x)
    right_anchor = max(yu_box.center_x, yuxian_box.center_x)

    left = left_anchor + horizontal_padding
    right = right_anchor - horizontal_padding

    top = min(yu_box.top, yuxian_box.top) - vertical_padding
    bottom = max(yu_box.bottom, yuxian_box.bottom) + vertical_padding

    if right - left < 8:
        return None
    return clamp_region(gray, left, top, right, bottom)


def find_green_box_in_region(
    screen_bgr: np.ndarray,
    region: SearchRegion,
    lower_hsv: Tuple[int, int, int],
    upper_hsv: Tuple[int, int, int],
    min_area: int,
    target_center_x: Optional[float] = None,
    x_tolerance: int = 120,
    target_height: Optional[float] = None,
    height_ratio_tolerance: float = 0.6,
) -> Optional[MatchBox]:
    x0 = region.offset_x
    y0 = region.offset_y
    h, w = region.image.shape[:2]

    roi_bgr = screen_bgr[y0 : y0 + h, x0 : x0 + w]
    hsv = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2HSV)

    mask = cv2.inRange(hsv, np.array(lower_hsv, dtype=np.uint8), np.array(upper_hsv, dtype=np.uint8))
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    best = None
    best_area = 0.0
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, ww, hh = cv2.boundingRect(contour)

        if target_center_x is not None:
            contour_center_x = float(x0 + x + ww / 2.0)
            if abs(contour_center_x - float(target_center_x)) > max(1, int(x_tolerance)):
                continue

        if target_height is not None and target_height > 0:
            min_h = float(target_height) * max(0.0, 1.0 - float(height_ratio_tolerance))
            max_h = float(target_height) * (1.0 + float(height_ratio_tolerance))
            if not (min_h <= float(hh) <= max_h):
                continue

        if area > best_area:
            best_area = area
            best = (x, y, ww, hh)

    if best is None:
        return None

    x, y, ww, hh = best
    return MatchBox(left=x0 + x, top=y0 + y, width=ww, height=hh, score=1.0)


def update_direction_key(current_key: Optional[str], new_key: Optional[str]) -> Optional[str]:
    if current_key == new_key:
        return current_key

    if current_key is not None:
        send_key_sendinput(current_key, key_up=True)

    if new_key is not None:
        send_key_sendinput(new_key, key_up=False)

    return new_key


def run_controller(args: argparse.Namespace) -> None:
    slider_template = load_template(Path(args.slider_template))
    yu_template = load_template(Path(args.yu_template))
    yuxian_template = load_template(Path(args.yuxian_template))
    f_template = load_template(Path(args.f_template))
    q_e_template = load_template(Path(args.qe_template))
    click_template = load_optional_template(Path(args.click_template))
    r_template = load_optional_template(Path(args.r_template))
    watchdog_timeout = max(0.0, float(args.watchdog_timeout))
    watchdog_max_consecutive_timeouts = max(1, int(args.watchdog_max_consecutive_timeouts))
    last_any_detection_time = time.time()
    watchdog_timeout_streak = 0

    if gw is None:
        raise RuntimeError("pygetwindow 未安装，请先执行: pip install pygetwindow")

    blur_kernel = max(1, int(args.blur_kernel))
    if blur_kernel % 2 == 0:
        blur_kernel += 1

    pyautogui.PAUSE = 0
    pyautogui.FAILSAFE = True

    active_fps = max(1.0, float(args.fps))
    idle_fps = max(0.5, float(args.idle_fps))

    current_direction_key: Optional[str] = None
    workflow_state = "qe_phase"
    last_f_press_time = 0.0
    last_click_time = 0.0
    click_gone_frames = 0
    click_miss_start_time: Optional[float] = None
    last_window_warn_time = 0.0
    next_frame_time = time.perf_counter()
    previous_anchors_visible = False
    last_anchors_seen_time = 0.0
    last_green_seen_time = 0.0
    last_control_target_seen_time = 0.0
    last_valid_green_box: Optional[MatchBox] = None

    last_slider_box: Optional[MatchBox] = None
    slider_lost_count = 0
    assist_start_time = time.time()

    click_enabled = click_template is not None
    r_enabled = r_template is not None

    if not click_enabled:
        print(f"未找到 click 模板: {args.click_template}，click 功能禁用。")
    if not r_enabled:
        print(f"未找到 R 模板: {args.r_template}，R 功能禁用。")

    f_reaction_min = max(0.0, float(args.f_reaction_min))
    f_reaction_max = max(0.0, float(args.f_reaction_max))
    if f_reaction_min > f_reaction_max:
        f_reaction_min, f_reaction_max = f_reaction_max, f_reaction_min

    click_reaction_min = max(0.0, float(args.click_reaction_min))
    click_reaction_max = max(0.0, float(args.click_reaction_max))
    if click_reaction_min > click_reaction_max:
        click_reaction_min, click_reaction_max = click_reaction_max, click_reaction_min
    click_post_esc_delay_min = max(0.0, float(args.click_post_esc_delay_min))
    click_post_esc_delay_max = max(0.0, float(args.click_post_esc_delay_max))
    if click_post_esc_delay_min > click_post_esc_delay_max:
        click_post_esc_delay_min, click_post_esc_delay_max = click_post_esc_delay_max, click_post_esc_delay_min
    wait_click_restart_timeout = max(0.0, float(args.wait_click_restart_timeout))
    click_detect_duration = max(0.0, float(args.click_detect_duration))

    speed_cache_path = Path(args.speed_cache_file)
    cached_speed = None
    if args.use_speed_cache and not args.force_reestimate_speed:
        cached_speed = load_speed_cache(speed_cache_path)

    effective_speed = float(args.slider_speed_px_per_sec)
    if cached_speed is not None:
        effective_speed = cached_speed
        print(f"已加载本地速度缓存: {effective_speed:.2f} px/s ({speed_cache_path})")
    else:
        print(f"使用初始速度参数: {effective_speed:.2f} px/s")

    predictor = ConstantSpeedPredictor(effective_speed)
    should_auto_estimate_speed = args.auto_estimate_speed and (cached_speed is None or args.force_reestimate_speed)
    speed_cache_saved = False
    calibration_only_mode = should_auto_estimate_speed
    calibration_finished = False
    calibration_finished_message = ""

    calibration_stage: Optional[str] = "left" if should_auto_estimate_speed else None
    calibration_wait_until = 0.0
    calibration_stage_start_time = time.time()
    calibration_prev_measure: Optional[Tuple[float, float]] = None
    calibration_seen_motion = False
    calibration_still_frames = 0
    left_speed_samples: list[float] = []
    right_speed_samples: list[float] = []

    outside_direction_pending: Optional[str] = None
    outside_pending_frames = 0
    wait_click_state_enter_time: Optional[float] = None

    just_entered_qe = False   # 新增：标记是否刚进入 qe_phase

    if should_auto_estimate_speed:
        print("开始自动估速: 长按 A 到左边停住 -> 长按 D 到右边停住")

    def wait_next_frame(target_fps: float) -> None:
        nonlocal next_frame_time
        frame_interval = 1.0 / max(0.5, target_fps)
        next_frame_time += frame_interval
        sleep_time = next_frame_time - time.perf_counter()
        if sleep_time > 0:
            time.sleep(sleep_time)
        else:
            next_frame_time = time.perf_counter()

    def detect_recovery_stage(screen_bgr: np.ndarray, screen_gray: np.ndarray) -> str:
        qr_bottom = build_bottom_right_region(screen_gray, args.trigger_region_width, args.trigger_region_height)
        qr_upper = build_upper_middle_region(screen_gray, args.f_search_width_ratio)
        anchors_upper = build_upper_middle_region(screen_gray, args.search_width_ratio)

        detected_stage: Optional[str] = None

        if qr_bottom is not None:
            q_e_hit = locate_template(qr_bottom.image, q_e_template, args.qe_threshold, args.enable_blur, blur_kernel)
            if q_e_hit is not None:
                detected_stage = "qe_phase"

        if detected_stage is None and qr_upper is not None:
            f_hit = locate_template(qr_upper.image, f_template, args.f_threshold, args.enable_blur, blur_kernel)
            if f_hit is not None:
                detected_stage = "f_phase"

        if detected_stage is None and anchors_upper is not None:
            yu_h = locate_template(anchors_upper.image, yu_template, args.yu_threshold, args.enable_blur, blur_kernel)
            yux_h = locate_template(anchors_upper.image, yuxian_template, args.yuxian_threshold, args.enable_blur, blur_kernel)
            yu_h = to_absolute_box(yu_h, anchors_upper.offset_x, anchors_upper.offset_y) if yu_h is not None else None
            yux_h = to_absolute_box(yux_h, anchors_upper.offset_x, anchors_upper.offset_y) if yux_h is not None else None
            if yu_h is not None and yux_h is not None:
                anchors_reg = build_between_anchors_region(screen_gray, yu_h, yux_h, args.anchor_padding, args.anchor_vertical_padding)
                if anchors_reg is not None:
                    green_h = find_green_box_in_region(screen_bgr, anchors_reg, args.green_lower_hsv, args.green_upper_hsv, args.green_min_area)
                    if green_h is not None:
                        detected_stage = "slider"
                    else:
                        detected_stage = "slider"

        if detected_stage is None and click_template is not None:
            click_h = locate_template(screen_gray, click_template, args.click_threshold, args.enable_blur, blur_kernel)
            if click_h is not None:
                detected_stage = "wait_click_disappear"

        if detected_stage is None and qr_bottom is not None and r_template is not None:
            r_h = locate_template(qr_bottom.image, r_template, args.r_threshold, args.enable_blur, blur_kernel)
            if r_h is not None:
                detected_stage = "qe_phase"

        if detected_stage is None:
            detected_stage = "qe_phase"

        return detected_stage

    print("开始识别，按 Ctrl+C 停止。")

    try:
        while True:
            target_window = find_target_window(
                args.window_title,
                args.window_width,
                args.window_height,
                args.window_size_tolerance,
            )
            if target_window is None:
                current_direction_key = update_direction_key(current_direction_key, None)
                predictor.reset()
                now = time.time()
                if now - last_window_warn_time >= 2.0:
                    print(
                        "未找到目标窗口: "
                        f"标题包含 '{args.window_title}' 且尺寸约 {args.window_width}x{args.window_height}"
                    )
                    last_window_warn_time = now
                wait_next_frame(idle_fps)
                continue

            screen_bgr, screen_gray = capture_window_images(target_window)
            now = time.time()

            upper_middle_region: Optional[SearchRegion] = None
            bottom_right_region: Optional[SearchRegion] = None
            yu_box: Optional[MatchBox] = None
            yuxian_box: Optional[MatchBox] = None
            anchors_region: Optional[SearchRegion] = None
            green_box: Optional[MatchBox] = None
            slider_box: Optional[MatchBox] = None
            f_box: Optional[MatchBox] = None
            q_e_box: Optional[MatchBox] = None
            click_hit: Optional[MatchBox] = None
            r_hit: Optional[MatchBox] = None
            anchors_visible = False
            anchors_effective_visible = False

            if workflow_state in ("qe_phase", "f_phase") or (workflow_state == "slider" and r_enabled):
                bottom_right_region = build_bottom_right_region(
                    screen_gray,
                    args.trigger_region_width,
                    args.trigger_region_height,
                )

            f_region: Optional[SearchRegion] = None
            if workflow_state == "f_phase":
                f_region = build_upper_middle_region(
                    screen_gray,
                    args.f_search_width_ratio,
                )

            if workflow_state in ("slider", "wait_click_disappear"):
                upper_middle_region = build_upper_middle_region(screen_gray, args.search_width_ratio)
                yu_box = locate_template(
                    upper_middle_region.image,
                    yu_template,
                    args.yu_threshold,
                    args.enable_blur,
                    blur_kernel,
                )
                yuxian_box = locate_template(
                    upper_middle_region.image,
                    yuxian_template,
                    args.yuxian_threshold,
                    args.enable_blur,
                    blur_kernel,
                )
                yu_box = to_absolute_box(yu_box, upper_middle_region.offset_x, upper_middle_region.offset_y)
                yuxian_box = to_absolute_box(yuxian_box, upper_middle_region.offset_x, upper_middle_region.offset_y)

                anchors_visible = yu_box is not None and yuxian_box is not None
                if workflow_state == "slider" and previous_anchors_visible and not anchors_visible:
                    print("未检测到 yu/yuxian，进入 click/R 收尾阶段。")
                previous_anchors_visible = anchors_visible

                if anchors_visible:
                    last_anchors_seen_time = now
                    anchors_effective_visible = True
                elif now - last_anchors_seen_time <= args.anchor_lost_grace_seconds:
                    anchors_effective_visible = True

                if anchors_visible:
                    anchors_region = build_between_anchors_region(
                        screen_gray,
                        yu_box,
                        yuxian_box,
                        args.anchor_padding,
                        args.anchor_vertical_padding,
                    )
                    if anchors_region is not None and workflow_state != "slider":
                        green_box = find_green_box_in_region(
                            screen_bgr,
                            anchors_region,
                            args.green_lower_hsv,
                            args.green_upper_hsv,
                            args.green_min_area,
                        )

                if workflow_state != "slider" and green_box is not None:
                    last_green_seen_time = now
                    last_valid_green_box = green_box

                if click_enabled and (
                    workflow_state == "wait_click_disappear"
                    or (workflow_state == "slider" and not anchors_effective_visible)
                ):
                    click_hit = locate_template(
                        screen_gray,
                        click_template,
                        args.click_threshold,
                        args.enable_blur,
                        blur_kernel,
                    )

            if workflow_state == "qe_phase":
                q_e_box = locate_template(
                    bottom_right_region.image,
                    q_e_template,
                    args.qe_threshold,
                    args.enable_blur,
                    blur_kernel,
                )
                q_e_box = to_absolute_box(q_e_box, bottom_right_region.offset_x, bottom_right_region.offset_y)

            elif workflow_state == "f_phase":
                f_box = locate_template(
                    f_region.image,
                    f_template,
                    args.f_threshold,
                    args.enable_blur,
                    blur_kernel,
                )
                f_box = to_absolute_box(f_box, f_region.offset_x, f_region.offset_y)

            elif workflow_state == "slider":
                slider_regions = []
                if anchors_region is not None:
                    slider_regions.append(anchors_region)
                if green_box is not None:
                    slider_regions.append(
                        build_box_region(
                            screen_gray,
                            green_box.left - args.slider_search_padding_x,
                            green_box.top - args.slider_search_padding_y,
                            green_box.right + args.slider_search_padding_x,
                            green_box.bottom + args.slider_search_padding_y,
                        )
                    )
                if last_slider_box is not None and anchors_region is not None:
                    slider_regions.append(
                        build_box_region(
                            screen_gray,
                            last_slider_box.center_x - args.slider_track_window,
                            last_slider_box.top - args.slider_search_padding_y,
                            last_slider_box.center_x + args.slider_track_window,
                            last_slider_box.bottom + args.slider_search_padding_y,
                        )
                    )
                if anchors_region is None and upper_middle_region is not None:
                    slider_regions.append(upper_middle_region)

                for region in slider_regions:
                    if region is None:
                        continue
                    candidate = locate_template(
                        region.image,
                        slider_template,
                        args.slider_threshold,
                        args.enable_blur,
                        blur_kernel,
                    )
                    candidate = to_absolute_box(candidate, region.offset_x, region.offset_y)
                    if candidate is None:
                        continue

                    if (
                        anchors_region is not None
                        and (
                            candidate.center_x < anchors_region.offset_x
                            or candidate.center_x > anchors_region.offset_x + anchors_region.image.shape[1]
                        )
                    ):
                        continue

                    if (
                        green_box is not None
                        and (
                            candidate.center_x < green_box.left - args.slider_outside_allowance
                            or candidate.center_x > green_box.right + args.slider_outside_allowance
                        )
                    ):
                        continue

                    if (
                        last_slider_box is not None
                        and abs(candidate.center_x - last_slider_box.center_x) > args.slider_max_jump
                        and candidate.score < args.slider_strong_threshold
                    ):
                        continue

                    slider_box = candidate
                    break

                if slider_box is None and slider_lost_count > 0 and anchors_region is not None:
                    recover_candidate = locate_template(
                        anchors_region.image,
                        slider_template,
                        args.slider_recover_threshold,
                        args.enable_blur,
                        blur_kernel,
                    )
                    recover_candidate = to_absolute_box(
                        recover_candidate,
                        anchors_region.offset_x,
                        anchors_region.offset_y,
                    )
                    if recover_candidate is not None:
                        slider_box = recover_candidate

                if slider_box is not None:
                    if last_slider_box is not None:
                        alpha = max(0.0, min(args.slider_ema_alpha, 1.0))
                        smoothed_center_x = int(alpha * slider_box.center_x + (1.0 - alpha) * last_slider_box.center_x)
                        slider_box = MatchBox(
                            left=smoothed_center_x - slider_box.width // 2,
                            top=slider_box.top,
                            width=slider_box.width,
                            height=slider_box.height,
                            score=slider_box.score,
                        )
                    last_slider_box = slider_box
                    slider_lost_count = 0
                else:
                    slider_lost_count += 1
                    if last_slider_box is not None and slider_lost_count <= args.slider_lost_frames:
                        slider_box = last_slider_box
                    else:
                        last_slider_box = None
                        slider_lost_count = 0

                # 先识别滑块，再按滑块X轴位置与高度约束识别绿色区域，减少背景误检。
                if anchors_region is not None:
                    x_hint = None
                    h_hint = None
                    if slider_box is not None:
                        x_hint = float(slider_box.center_x)
                        h_hint = float(slider_box.height)
                    elif last_slider_box is not None:
                        x_hint = float(last_slider_box.center_x)
                        h_hint = float(last_slider_box.height)

                    green_box = find_green_box_in_region(
                        screen_bgr,
                        anchors_region,
                        args.green_lower_hsv,
                        args.green_upper_hsv,
                        args.green_min_area,
                        target_center_x=x_hint,
                        x_tolerance=args.green_x_tolerance,
                        target_height=h_hint,
                        height_ratio_tolerance=args.green_height_ratio_tolerance,
                    )

                if green_box is not None:
                    last_green_seen_time = now
                    last_valid_green_box = green_box
                elif (
                    last_valid_green_box is not None
                    and now - last_green_seen_time <= args.green_lost_grace_seconds
                ):
                    green_box = last_valid_green_box

                if r_enabled and click_hit is None and not anchors_effective_visible and bottom_right_region is not None:
                    if click_miss_start_time is not None and (
                        now - click_miss_start_time
                        >= max(click_detect_duration, args.r_detect_delay_after_click_miss)
                    ):
                        r_hit = locate_template(
                            bottom_right_region.image,
                            r_template,
                            args.r_threshold,
                            args.enable_blur,
                            blur_kernel,
                        )
                        r_hit = to_absolute_box(r_hit, bottom_right_region.offset_x, bottom_right_region.offset_y)

            if workflow_state == "slider":
                calibration_active = (
                    CALIBRATION_PURE_MODE
                    and should_auto_estimate_speed
                    and calibration_stage is not None
                )

                if (not calibration_active) and not anchors_effective_visible and click_hit is not None and now - last_click_time >= args.click_cooldown:
                    click_miss_start_time = None
                    click_delay = random.uniform(click_reaction_min, click_reaction_max)
                    if click_delay > 0:
                        time.sleep(click_delay)

                    bring_window_foreground(target_window.hwnd)
                    press_key_sendinput("esc")
                    click_post_esc_delay = random.uniform(click_post_esc_delay_min, click_post_esc_delay_max)
                    if click_post_esc_delay > 0:
                        time.sleep(click_post_esc_delay)
                    last_click_time = time.time()
                    # 喂看门狗：在识别到 click 并发送 ESC 后，重置最近一次完整检测时间，避免被误判为卡住
                    last_any_detection_time = time.time()
                    watchdog_timeout_streak = 0
                    click_gone_frames = 0
                    workflow_state = "wait_click_disappear"
                    wait_click_state_enter_time = now
                    current_direction_key = update_direction_key(current_direction_key, None)
                    predictor.reset()
                    outside_direction_pending = None
                    outside_pending_frames = 0
                    print(
                        f"识别到模板 {args.click_template}，已按下 ESC，"
                        f"匹配分数: {click_hit.score:.3f}，延时: {click_delay * 1000:.0f}ms"
                    )
                elif (not calibration_active) and not anchors_effective_visible and click_hit is None and r_enabled:
                    if click_miss_start_time is None:
                        click_miss_start_time = now
                    if r_hit is not None:
                        workflow_state = "qe_phase"
                        last_any_detection_time = now
                        watchdog_timeout_streak = 0
                        just_entered_qe = True 
                        assist_start_time = time.time()
                        last_slider_box = None
                        slider_lost_count = 0
                        click_miss_start_time = None
                        current_direction_key = update_direction_key(current_direction_key, None)
                        predictor.reset()
                        outside_direction_pending = None
                        outside_pending_frames = 0
                        print(
                            f"识别到模板 {args.r_template}，跳过 click 收尾并重开新一轮，"
                            f"匹配分数: {r_hit.score:.3f}"
                        )
                else:
                    if anchors_effective_visible:
                        click_miss_start_time = None

                    measured_slider_x: Optional[float] = None
                    if slider_box is not None:
                        measured_slider_x = float(slider_box.center_x)
                    elif (
                        green_box is not None
                        and yu_box is not None
                        and yuxian_box is not None
                        and now - assist_start_time <= args.init_center_assist_seconds
                    ):
                        measured_slider_x = float((yu_box.center_x + yuxian_box.center_x) // 2)

                    if measured_slider_x is not None:
                        predictor.on_measurement(measured_slider_x, now, args.predictor_measure_blend)
                    else:
                        predictor.predict(now, current_direction_key)

                    if should_auto_estimate_speed:
                        if calibration_stage == "left":
                            current_direction_key = update_direction_key(current_direction_key, "a")
                        elif calibration_stage == "right":
                            current_direction_key = update_direction_key(current_direction_key, "d")
                        elif calibration_stage == "wait_right":
                            current_direction_key = update_direction_key(current_direction_key, None)

                        if calibration_stage == "wait_right" and now >= calibration_wait_until:
                            calibration_stage = "right"
                            calibration_stage_start_time = now
                            calibration_prev_measure = None
                            calibration_seen_motion = False
                            calibration_still_frames = 0
                            print("自动估速: 开始长按 D 测试向右速度")

                        if calibration_stage in ("left", "right"):
                            if measured_slider_x is not None:
                                if calibration_prev_measure is not None:
                                    prev_t, prev_x = calibration_prev_measure
                                    dt = now - prev_t
                                    dx = measured_slider_x - prev_x
                                    if dt >= args.speed_calibration_min_dt:
                                        if abs(dx) >= args.speed_calibration_min_delta:
                                            calibration_seen_motion = True
                                            calibration_still_frames = 0
                                            instant_speed = abs(dx) / dt
                                            if calibration_stage == "left":
                                                left_speed_samples.append(instant_speed)
                                            else:
                                                right_speed_samples.append(instant_speed)
                                        elif calibration_seen_motion:
                                            calibration_still_frames += 1
                                calibration_prev_measure = (now, measured_slider_x)

                            stage_timeout = (now - calibration_stage_start_time) >= args.speed_calibration_stage_timeout
                            stage_done = calibration_seen_motion and calibration_still_frames >= args.speed_calibration_stop_frames

                            if stage_done or stage_timeout:
                                current_direction_key = update_direction_key(current_direction_key, None)
                                if calibration_stage == "left":
                                    calibration_stage = "wait_right"
                                    calibration_wait_until = now + args.speed_calibration_switch_pause
                                    calibration_prev_measure = None
                                    calibration_seen_motion = False
                                    calibration_still_frames = 0
                                    print("自动估速: 左向完成，准备右向测试")
                                else:
                                    left_speed = float(np.median(left_speed_samples)) if left_speed_samples else None
                                    right_speed = float(np.median(right_speed_samples)) if right_speed_samples else None
                                    chosen_speed: Optional[float] = None
                                    if left_speed is not None and right_speed is not None:
                                        chosen_speed = (left_speed + right_speed) / 2.0
                                    elif left_speed is not None:
                                        chosen_speed = left_speed
                                    elif right_speed is not None:
                                        chosen_speed = right_speed

                                    if chosen_speed is not None:
                                        predictor.speed_px_per_sec = max(1.0, chosen_speed)
                                        if args.use_speed_cache and not speed_cache_saved:
                                            try:
                                                save_speed_cache(speed_cache_path, predictor.speed_px_per_sec)
                                                speed_cache_saved = True
                                            except OSError as exc:
                                                print(f"写入速度缓存失败: {exc}")
                                        print(
                                            f"自动估速完成: 左={left_speed if left_speed is not None else 0.0:.2f}, "
                                            f"右={right_speed if right_speed is not None else 0.0:.2f}, "
                                            f"采用={predictor.speed_px_per_sec:.2f} px/s"
                                        )
                                        calibration_finished_message = "校准完成，已保存速度。请手动重启任务进入正常流程。"
                                    else:
                                        print("自动估速失败：未采集到有效速度样本，继续使用当前速度参数")
                                        calibration_finished_message = "校准结束，但未得到有效速度。请检查识别后手动重启任务。"

                                    should_auto_estimate_speed = False
                                    calibration_stage = None
                                    calibration_finished = True

                    if (not calibration_active) and green_box is not None:
                        control_slider_x = measured_slider_x
                        if control_slider_x is None:
                            control_slider_x = predictor.predict_at(now + args.control_lead_seconds, current_direction_key)

                        if control_slider_x is None:
                            if now - last_control_target_seen_time > args.control_target_lost_hold_seconds:
                                current_direction_key = update_direction_key(current_direction_key, None)
                                outside_direction_pending = None
                                outside_pending_frames = 0
                        else:
                            last_control_target_seen_time = now
                            left_limit = float(green_box.left + args.bound_padding)
                            right_limit = float(green_box.right - args.bound_padding)

                            desired_direction: Optional[str] = None
                            if control_slider_x < left_limit - args.dead_zone:
                                desired_direction = "d"
                            elif control_slider_x > right_limit + args.dead_zone:
                                desired_direction = "a"

                            if desired_direction is None:
                                current_direction_key = update_direction_key(current_direction_key, None)
                                outside_direction_pending = None
                                outside_pending_frames = 0
                            else:
                                if outside_direction_pending != desired_direction:
                                    outside_direction_pending = desired_direction
                                    outside_pending_frames = 1
                                else:
                                    outside_pending_frames += 1

                                if outside_pending_frames >= max(1, args.outside_confirm_frames):
                                    current_direction_key = update_direction_key(current_direction_key, desired_direction)
                    elif not calibration_active:
                        if now - last_green_seen_time > args.green_lost_grace_seconds:
                            current_direction_key = update_direction_key(current_direction_key, None)
                            outside_direction_pending = None
                            outside_pending_frames = 0

            elif workflow_state == "wait_click_disappear":
                click_miss_start_time = None
                current_direction_key = update_direction_key(current_direction_key, None)
                predictor.reset()
                outside_direction_pending = None
                outside_pending_frames = 0

                if click_hit is None:
                    click_gone_frames += 1
                else:
                    click_gone_frames = 0

                if click_gone_frames >= args.click_disappear_frames:
                    workflow_state = "qe_phase"
                    wait_click_state_enter_time = None
                    last_any_detection_time = now 
                    watchdog_timeout_streak = 0
                    just_entered_qe = True  
                    print("click 已消失，进入 Q_E 任务阶段。")
                elif (
                    wait_click_restart_timeout > 0
                    and wait_click_state_enter_time is not None
                    and (now - wait_click_state_enter_time) >= wait_click_restart_timeout
                ):
                    print(f"按下 ESC 后 {wait_click_restart_timeout:.1f}s 未重启，直接再次执行 ESC 触发。")
                    click_delay = random.uniform(click_reaction_min, click_reaction_max)
                    if click_delay > 0:
                        time.sleep(click_delay)

                    bring_window_foreground(target_window.hwnd)
                    press_key_sendinput("esc")
                    click_post_esc_delay = random.uniform(click_post_esc_delay_min, click_post_esc_delay_max)
                    if click_post_esc_delay > 0:
                        time.sleep(click_post_esc_delay)

                    # 重新计时并停留在当前阶段等待重开
                    last_click_time = time.time()
                    wait_click_state_enter_time = time.time()
                    click_gone_frames = 0
                    last_any_detection_time = time.time()
                    watchdog_timeout_streak = 0

            elif workflow_state == "qe_phase":
                if just_entered_qe:
                    # 点击窗口内 (800±50, 750±50) 区域
                    offset_x = target_window.left + 800 + random.randint(-50, 50)
                    offset_y = target_window.top + 750 + random.randint(-50, 50)
                    original_mouse_x, original_mouse_y = pyautogui.position()
                    bring_window_foreground(target_window.hwnd)
                    send_left_click_sendinput(offset_x, offset_y)
                    pyautogui.moveTo(original_mouse_x, original_mouse_y, duration=0)
                    just_entered_qe = False
                click_miss_start_time = None
                current_direction_key = update_direction_key(current_direction_key, None)
                predictor.reset()
                outside_direction_pending = None
                outside_pending_frames = 0

                if q_e_box is not None and now - last_f_press_time >= args.f_cooldown:
                    delay = random.uniform(f_reaction_min, f_reaction_max)
                    if delay > 0:
                        time.sleep(delay)
                    press_key_sendinput("f")
                    last_f_press_time = time.time()
                    workflow_state = "f_phase"
                    print(
                        f"识别到 Q_E 图标，已按下 F，"
                        f"匹配分数: {q_e_box.score:.3f}，延时: {delay * 1000:.0f}ms"
                    )

            elif workflow_state == "f_phase":
                click_miss_start_time = None
                current_direction_key = update_direction_key(current_direction_key, None)
                predictor.reset()
                outside_direction_pending = None
                outside_pending_frames = 0

                if f_box is not None and now - last_f_press_time >= args.f_cooldown:
                    delay = random.uniform(f_reaction_min, f_reaction_max)
                    if delay > 0:
                        time.sleep(delay)
                    press_key_sendinput("f")
                    last_f_press_time = time.time()
                    workflow_state = "slider"
                    assist_start_time = time.time()
                    last_slider_box = None
                    slider_lost_count = 0
                    last_valid_green_box = None
                    last_green_seen_time = 0.0
                    last_control_target_seen_time = 0.0
                    predictor.reset()
                    outside_direction_pending = None
                    outside_pending_frames = 0
                    if should_auto_estimate_speed:
                        calibration_stage = "left"
                        calibration_wait_until = 0.0
                        calibration_stage_start_time = time.time()
                        calibration_prev_measure = None
                        calibration_seen_motion = False
                        calibration_still_frames = 0
                        left_speed_samples.clear()
                        right_speed_samples.clear()
                        print("进入滑块阶段，开始自动估速(先 A 后 D)")
                    print(
                        f"识别到 F 图标，已按下 F，"
                        f"匹配分数: {f_box.score:.3f}，延时: {delay * 1000:.0f}ms"
                    )

            if calibration_only_mode and calibration_finished:
                current_direction_key = update_direction_key(current_direction_key, None)
                print(calibration_finished_message)
                break
            

            # ===== 看门狗超时检测 =====
            if watchdog_timeout > 0 and (now - last_any_detection_time) > watchdog_timeout:
                watchdog_timeout_streak += 1
                print(f"看门狗触发：{watchdog_timeout:.0f}秒无完整循环，尝试检测并恢复卡住阶段。")
                print(f"看门狗连续超时次数：{watchdog_timeout_streak}/{watchdog_max_consecutive_timeouts}")

                # 快速检测当前屏幕，判断卡在什么阶段，尽量恢复到该阶段继续尝试
                detected_stage = detect_recovery_stage(screen_bgr, screen_gray)

                workflow_state = detected_stage
                wait_click_state_enter_time = now if workflow_state == "wait_click_disappear" else None
                just_entered_qe = True if workflow_state == "qe_phase" else False

                current_direction_key = update_direction_key(current_direction_key, None)
                predictor.reset()
                last_f_press_time = 0.0
                last_click_time = 0.0
                click_gone_frames = 0
                click_miss_start_time = None
                previous_anchors_visible = False
                last_anchors_seen_time = 0.0
                last_green_seen_time = 0.0
                last_control_target_seen_time = 0.0
                last_valid_green_box = None
                last_slider_box = None
                slider_lost_count = 0
                assist_start_time = time.time()
                outside_direction_pending = None
                outside_pending_frames = 0
                if should_auto_estimate_speed:
                    should_auto_estimate_speed = False
                    calibration_stage = None
                    calibration_prev_measure = None
                    calibration_seen_motion = False
                    calibration_still_frames = 0
                    left_speed_samples.clear()
                    right_speed_samples.clear()
                    calibration_wait_until = 0.0
                    print("自动估速已被看门狗终止。")
                last_any_detection_time = now   # 重置后立即喂狗

                if watchdog_timeout_streak >= watchdog_max_consecutive_timeouts:
                    print(
                        f"看门狗连续超时达到 {watchdog_timeout_streak} 次，自动停止项目。"
                    )
                    break
                
            wait_next_frame(active_fps)

    except KeyboardInterrupt:
        print("已停止控制。")
    finally:
        update_direction_key(current_direction_key, None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="模板匹配自动控制：A/D 纠偏 + F 触发")
    parser.add_argument("--slider-template", default="huakuai.png", help="滑块模板图")
    parser.add_argument("--yu-template", default="yu.png", help="左锚点模板图")
    parser.add_argument("--yuxian-template", default="yuxian.png", help="右锚点模板图")
    parser.add_argument("--f-template", default="FFF.png", help="F 提示模板图")
    parser.add_argument("--qe-template", default="Q_E.png", help="Q_E 提示模板图")

    parser.add_argument("--slider-threshold", type=float, default=0.75, help="huakuai 匹配阈值")
    parser.add_argument("--slider-search-padding-x", type=int, default=120, help="滑块局部搜索左右扩展像素")
    parser.add_argument("--slider-search-padding-y", type=int, default=30, help="滑块局部搜索上下扩展像素")
    parser.add_argument("--slider-track-window", type=int, default=180, help="基于上一帧的滑块追踪窗口半宽")
    parser.add_argument("--slider-max-jump", type=int, default=220, help="单帧允许滑块最大跳变像素")
    parser.add_argument("--slider-strong-threshold", type=float, default=0.90, help="滑块强匹配分数阈值")
    parser.add_argument("--slider-recover-threshold", type=float, default=0.58, help="滑块丢失后重捕获阈值")
    parser.add_argument("--slider-outside-allowance", type=int, default=30, help="滑块可超出绿色边界容差像素")
    parser.add_argument("--slider-lost-frames", type=int, default=10, help="滑块丢失后保留历史帧数")
    parser.add_argument("--slider-ema-alpha", type=float, default=0.65, help="滑块中心平滑系数(0-1)")

    parser.add_argument("--slider-speed-px-per-sec", type=float, default=550.0, help="滑块恒定速度估计(像素/秒)")
    parser.add_argument("--control-lead-seconds", type=float, default=0.06, help="控制前瞻时间(秒)")
    parser.add_argument("--predictor-measure-blend", type=float, default=0.70, help="测量融合系数(0-1)")
    parser.add_argument("--speed-cache-file", default="slider_speed_cache.json", help="本地速度缓存文件路径")
    parser.add_argument("--use-speed-cache", action=argparse.BooleanOptionalAction, default=True, help="是否启用本地速度缓存")
    parser.add_argument("--auto-estimate-speed", action=argparse.BooleanOptionalAction, default=True, help="是否自动估算滑块速度")
    parser.add_argument("--force-reestimate-speed", action="store_true", help="忽略缓存并重新自动估速")
    parser.add_argument("--speed-calibration-min-dt", type=float, default=0.01, help="自动估速采样最小间隔(秒)")
    parser.add_argument("--speed-calibration-min-delta", type=float, default=2.0, help="自动估速有效位移阈值(像素)")
    parser.add_argument("--speed-calibration-stop-frames", type=int, default=6, help="连续静止帧数达到后判定触边停止")
    parser.add_argument("--speed-calibration-stage-timeout", type=float, default=4.0, help="单方向估速超时(秒)")
    parser.add_argument("--speed-calibration-switch-pause", type=float, default=0.20, help="左右切换估速前暂停(秒)")

    parser.add_argument("--yu-threshold", type=float, default=0.75, help="yu 匹配阈值")
    parser.add_argument("--yuxian-threshold", type=float, default=0.75, help="yuxian 匹配阈值")
    parser.add_argument("--f-threshold", type=float, default=0.60, help="F 匹配阈值")
    parser.add_argument("--qe-threshold", type=float, default=0.80, help="Q_E 匹配阈值")

    parser.add_argument(
        "--green-lower-hsv",
        type=parse_hsv_triplet,
        default=(35, 40, 40),
        help="绿色识别下限 HSV，格式 H,S,V，默认 35,40,40",
    )
    parser.add_argument(
        "--green-upper-hsv",
        type=parse_hsv_triplet,
        default=(90, 255, 255),
        help="绿色识别上限 HSV，格式 H,S,V，默认 90,255,255",
    )
    parser.add_argument("--green-min-area", type=int, default=200, help="绿色区域最小面积阈值")
    parser.add_argument("--green-x-tolerance", type=int, default=120, help="绿色区域与滑块中心X轴容差像素")
    parser.add_argument("--green-height-ratio-tolerance", type=float, default=0.6, help="绿色区域高度相对滑块高度的容差比例")
    parser.add_argument("--anchor-padding", type=int, default=5, help="左右锚点向内收缩像素")
    parser.add_argument("--anchor-vertical-padding", type=int, default=25, help="锚点上下扩展像素")

    parser.add_argument("--window-title", default="异环", help="目标窗口标题(包含匹配)")
    parser.add_argument("--window-width", type=int, default=1600, help="目标窗口宽度")
    parser.add_argument("--window-height", type=int, default=900, help="目标窗口高度")
    parser.add_argument("--window-size-tolerance", type=int, default=120, help="窗口尺寸容差像素")

    parser.add_argument("--enable-blur", action="store_true", help="启用高斯模糊后再匹配")
    parser.add_argument("--blur-kernel", type=int, default=3, help="高斯模糊核大小(奇数)")

    parser.add_argument("--fps", type=float, default=30.0, help="窗口存在时识别帧率")
    parser.add_argument("--idle-fps", type=float, default=5.0, help="窗口丢失时识别帧率")
    parser.add_argument("--search-width-ratio", type=float, default=0.6, help="上半中间搜索区域宽度占比")
    parser.add_argument("--trigger-region-width", type=int, default=500, help="右下触发区宽度")
    parser.add_argument("--trigger-region-height", type=int, default=200, help="右下触发区高度")

    parser.add_argument("--bound-padding", type=int, default=5, help="绿色区域边界内缩像素")
    parser.add_argument("--dead-zone", type=int, default=3, help="停止按键死区像素")
    parser.add_argument("--outside-confirm-frames", type=int, default=2, help="连续越界帧数达到后才切换方向")
    parser.add_argument("--anchor-lost-grace-seconds", type=float, default=0.25, help="锚点短时丢失容忍时长(秒)")
    parser.add_argument("--green-lost-grace-seconds", type=float, default=0.25, help="绿色区域短时丢失容忍时长(秒)")
    parser.add_argument("--control-target-lost-hold-seconds", type=float, default=0.12, help="控制目标短时丢失时保持当前按键时长(秒)")
    parser.add_argument("--f-cooldown", type=float, default=0.5, help="按 F 冷却时间(秒)")
    parser.add_argument("--f-reaction-min", type=float, default=0.10, help="按 F 最小随机延时(秒)")
    parser.add_argument("--f-reaction-max", type=float, default=0.20, help="按 F 最大随机延时(秒)")
    parser.add_argument("--f-search-width-ratio", type=float, default=0.60, help="F 识别区域宽度占比，默认上半部分居中")

    parser.add_argument("--click-template", default="click.png", help="关闭提示模板图")
    parser.add_argument("--click-threshold", type=float, default=0.78, help="click 模板匹配阈值")
    parser.add_argument("--r-template", default="R.PNG", help="重开提示模板图")
    parser.add_argument("--r-threshold", type=float, default=0.80, help="R 模板匹配阈值")
    parser.add_argument("--r-detect-delay-after-click-miss", type=float, default=1.0, help="click miss 后延迟检测 R 秒数")
    parser.add_argument("--click-cooldown", type=float, default=1.0, help="click 点击冷却(秒)")
    parser.add_argument("--click-detect-duration", type=float, default=10.0, help="进入收尾阶段后 click 识别持续时长(秒)")
    parser.add_argument("--click-disappear-frames", type=int, default=3, help="click 消失判定连续帧数")
    parser.add_argument("--click-reaction-min", type=float, default=0.20, help="click 最小随机延时(秒)")
    parser.add_argument("--click-reaction-max", type=float, default=0.45, help="click 最大随机延时(秒)")
    parser.add_argument("--click-post-esc-delay-min", type=float, default=0.23, help="按下 ESC 后最小额外等待时间(秒)")
    parser.add_argument("--click-post-esc-delay-max", type=float, default=0.75, help="按下 ESC 后最大额外等待时间(秒)")
    parser.add_argument("--wait-click-restart-timeout", type=float, default=5.0, help="按下 ESC 后等待重启超时(秒)，超时后执行阶段重检")
    parser.add_argument("--init-center-assist-seconds", type=float, default=2.0, help="初始中心辅助时长(秒)")
    parser.add_argument("--watchdog-timeout", type=float, default=40.0, help="无动作重置的超时秒数,0 表示禁用")
    parser.add_argument("--watchdog-max-consecutive-timeouts", type=int, default=3, help="看门狗连续超时多少次后自动停止项目")
    
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if FORCE_REESTIMATE_SPEED_ON_START:
        args.force_reestimate_speed = True
        args.auto_estimate_speed = True
    run_controller(args)

if __name__ == "__main__":
    main()
