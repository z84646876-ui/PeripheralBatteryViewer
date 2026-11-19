# -*- coding: utf-8 -*-
"""neu_ui.py —— 纯 Python 新拟物派 (Neumorphism) UI 引擎与组件库（零第三方依赖）

严格遵循固定左上光源、同色基底 `#e0e5ec`、双方向柔化阴影 (`#b8bcc2` / `#ffffff`)、
无纯黑阴影与无实色边框的设计原则：
  - SoftCard / RecessedWell: 3-pass 分离卷积生成柔和二维圆角阴影，9-slice 瞬时缩放
  - NeuButton: 悬停时减半阴影模糊、按下时原位翻转为 inset 凹陷、焦点展示紫罗兰外轮廓
  - NeuToggle: 凹陷圆角轨道 + 凸起圆形旋钮，开启态为 `#6d5dfc` 并带平滑滑动过渡
  - NeuEntry / NeuSelect / NeuProgressBar / NeuTabs / NeuScrollbar / NeuRoleIcon
"""

from __future__ import annotations

import base64
import ctypes
import math
import struct
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import zlib
from ctypes import wintypes
from typing import Any, Callable, Optional

# ---- 设计令牌 (Neumorphism Color & Typography Tokens, 支持浅色 / 深色双主题) ----
try:
    from devices import BAR_AMBER, BAR_GREEN, BAR_RED, BAR_YELLOW, GRAY_DISABLED
except Exception:
    GRAY_DISABLED = "#8a93a2"
    BAR_GREEN = "#7fafc7"
    BAR_YELLOW = "#d8c68a"
    BAR_RED = "#c07a80"
    BAR_AMBER = "#d4b28c"

LIGHT_THEME: dict[str, Any] = {
    "BASE": "#e0e5ec",
    "SHADOW_GREY": "#b8bcc2",
    "HIGHLIGHT": "#ffffff",
    "TEXT": "#3d4451",
    "TEXT_MUTED": "#4f5868",
    "TEXT_DIM": "#5e6778",       # WCAG AA (>= 4.50:1) on #e0e5ec
    "TEXT_DISABLED": "#6e7889",  # 离线 / 禁用态弱化文字
    "BLURPLE": "#6d5dfc",
    "BLURPLE_BRAND": "#6d5dfc",      # 主按钮与强激活背景
    "BLURPLE_FOCUS": "#6252f5",      # 键盘焦点环
    "BLURPLE_SELECTION": "#e4e1fa",  # 选中底色（浅淡高亮，不抢主按钮）
    "BLURPLE_LINK": "#5646ea",       # 可点击链接与强调文本（>=4.5:1）
    "TEXT_ON_ACCENT": "#ffffff",     # 实心强调按钮前景恒为高对比白字
    "TEXT_ON_DANGER": "#ffffff",
    "CORAL": "#ff6b6b",
    "MINT": "#4ecdc4",
    "RED_DANGER": "#a84848",     # WCAG AA (>= 4.50:1) 告警/危险文字
    "DANGER_FILL": "#c2464b",    # 实心高危确认按钮填充色
    "AMBER_CHARGE": "#875424",   # WCAG AA (>= 4.99:1) 充电状态文字
    "SUCCESS": "#296d50",        # WCAG AA (>= 4.50:1) 成功提示绿色
    "INFO": "#3e6678",           # WCAG AA (>= 4.91:1) 信息与预估高亮
    "PILL_NEUTRAL": "#dde3ec",   # 通用中性标签底色
    "SEP_DARK": "#d0d6e1",
    "SEP_LIGHT": "#eef2f8",
    "POPUP_BORDER": "#cfd6e2",
    "POPUP_BG": "#e3e8ef",
    "TAB_ACTIVE_BG": "#d5dce7",
    "TOOLTIP_BG": "#2f3745",
    "TOOLTIP_FG": "#f4f7fb",
    "MUTED_CARD_BG": "#d7dde6",
    "SKELETON_BG": "#cfd6e2",
    "SKELETON_PULSE_BG": "#d9dfe9",
    "SCROLL_TRACK_IDLE": "#c5cbd6",
    "TOGGLE_KNOB_HI_ON": "#8f83ff",
    "TOGGLE_KNOB_SH_ON": "#9ea5b3",
    "WIN_BTN_HOVER_BG": "#e6ebf2",
    "WIN_BTN_PRESS_BG": "#d6dce6",
    "WIN_BTN_CLOSE_HOVER_BG": "#f4e2e3",
    "WIN_BTN_CLOSE_PRESS_BG": "#f0d8da",
    "WIN_BTN_CLOSE_FG": "#c85d63",
    "OVERLAY_BACKDROP_BG": "#d0d7e2",
    "HEADING_ACTIVE_BG": "#d4dae3",
    "TRANSP_KEY": "#e5dde6",     # 与新拟物灰阶像素 (r<=g<=b) 恒互斥的 1-bit 透明键
    "HOVER_PILL_BG": "#e7ecf3",
    "CARD_GLOW_RGB": (255, 255, 255),
}

DARK_THEME: dict[str, Any] = {
    "BASE": "#22262f",
    "SHADOW_GREY": "#15181e",
    "HIGHLIGHT": "#313744",
    "TEXT": "#eef2f8",
    "TEXT_MUTED": "#c5cddb",
    "TEXT_DIM": "#9ea8b8",       # WCAG AA (>= 4.50:1) on #22262f
    "TEXT_DISABLED": "#848e9f",
    "BLURPLE": "#7c6fff",
    "BLURPLE_BRAND": "#7c6fff",
    "BLURPLE_FOCUS": "#9489ff",
    "BLURPLE_SELECTION": "#323052",
    "BLURPLE_LINK": "#a59cff",
    "TEXT_ON_ACCENT": "#ffffff",
    "TEXT_ON_DANGER": "#ffffff",
    "CORAL": "#ff7e7e",
    "MINT": "#58d8cf",
    "RED_DANGER": "#f28b92",
    "DANGER_FILL": "#c94a53",
    "AMBER_CHARGE": "#e8ad6d",
    "SUCCESS": "#5ec492",
    "INFO": "#7bbbe0",
    "PILL_NEUTRAL": "#2d3340",
    "SEP_DARK": "#171a22",
    "SEP_LIGHT": "#333947",
    "POPUP_BORDER": "#3a4152",
    "POPUP_BG": "#272c37",
    "TAB_ACTIVE_BG": "#2c3240",
    "TOOLTIP_BG": "#14171f",
    "TOOLTIP_FG": "#f1f5fb",
    "MUTED_CARD_BG": "#1d212a",
    "SKELETON_BG": "#2b303c",
    "SKELETON_PULSE_BG": "#363d4d",
    "SCROLL_TRACK_IDLE": "#343b48",
    "TOGGLE_KNOB_HI_ON": "#a59bff",
    "TOGGLE_KNOB_SH_ON": "#161922",
    "WIN_BTN_HOVER_BG": "#2e3543",
    "WIN_BTN_PRESS_BG": "#1b1f27",
    "WIN_BTN_CLOSE_HOVER_BG": "#4f282d",
    "WIN_BTN_CLOSE_PRESS_BG": "#5f2c32",
    "WIN_BTN_CLOSE_FG": "#f2838b",
    "OVERLAY_BACKDROP_BG": "#161920",
    "HEADING_ACTIVE_BG": "#2e3543",
    "TRANSP_KEY": "#2a222b",     # g < r,b 与深色灰阶互斥
    "HOVER_PILL_BG": "#2c3340",
    "CARD_GLOW_RGB": (136, 152, 196),
}

STATUS_PILL_STYLES_BY_THEME: dict[str, dict[str, tuple[str, str]]] = {
    "light": {
        "charging": ("#efe2d4", "#875424"),
        "full": ("#dae8ef", "#3e6678"),
        "wired_power": ("#dde3ec", "#4f5868"),
        "wireless": ("#dde8ec", "#3e6678"),
        "stale": ("#e3e5ec", "#3e6678"),
        "offline": ("#cfd5e0", "#555f70"),
        "hidden": ("#e5e2ee", "#5a5572"),  # 与危险红色区隔，采用柔和灰紫表示“已隐藏”
    },
    "dark": {
        "charging": ("#3a2f24", "#e8ad6d"),
        "full": ("#243540", "#7bbbe0"),
        "wired_power": ("#2d3340", "#c5cddb"),
        "wireless": ("#25353e", "#7bbbe0"),
        "stale": ("#2b303b", "#7bbbe0"),
        "offline": ("#2a303b", "#9aa4b5"),
        "hidden": ("#312e3f", "#b9b3cf"),
    },
}

STATUS_PILL_BORDERS_BY_THEME: dict[str, dict[str, Optional[str]]] = {
    "light": {
        "offline": "#b8c1d0",
        "hidden": "#cdc8db",
        "stale": "#c2cad8",
    },
    "dark": {
        "offline": "#3f4758",
        "hidden": "#47425b",
        "stale": "#3c4454",
    },
}

TEXT_SEP = " · "

BASE = LIGHT_THEME["BASE"]
SHADOW_GREY = LIGHT_THEME["SHADOW_GREY"]
HIGHLIGHT = LIGHT_THEME["HIGHLIGHT"]
TEXT = LIGHT_THEME["TEXT"]
TEXT_MUTED = LIGHT_THEME["TEXT_MUTED"]
TEXT_DIM = LIGHT_THEME["TEXT_DIM"]
TEXT_DISABLED = LIGHT_THEME["TEXT_DISABLED"]
BLURPLE = LIGHT_THEME["BLURPLE"]
BLURPLE_BRAND = LIGHT_THEME["BLURPLE_BRAND"]
BLURPLE_FOCUS = LIGHT_THEME["BLURPLE_FOCUS"]
BLURPLE_SELECTION = LIGHT_THEME["BLURPLE_SELECTION"]
BLURPLE_LINK = LIGHT_THEME["BLURPLE_LINK"]
TEXT_ON_ACCENT = LIGHT_THEME["TEXT_ON_ACCENT"]
TEXT_ON_DANGER = LIGHT_THEME["TEXT_ON_DANGER"]
CORAL = LIGHT_THEME["CORAL"]
MINT = LIGHT_THEME["MINT"]
RED_DANGER = LIGHT_THEME["RED_DANGER"]
DANGER_FILL = LIGHT_THEME["DANGER_FILL"]
AMBER_CHARGE = LIGHT_THEME["AMBER_CHARGE"]
SUCCESS = LIGHT_THEME["SUCCESS"]
INFO = LIGHT_THEME["INFO"]
PILL_NEUTRAL = LIGHT_THEME["PILL_NEUTRAL"]
SEP_DARK = LIGHT_THEME["SEP_DARK"]
SEP_LIGHT = LIGHT_THEME["SEP_LIGHT"]
POPUP_BORDER = LIGHT_THEME["POPUP_BORDER"]
POPUP_BG = LIGHT_THEME["POPUP_BG"]
TAB_ACTIVE_BG = LIGHT_THEME["TAB_ACTIVE_BG"]
TOOLTIP_BG = LIGHT_THEME["TOOLTIP_BG"]
TOOLTIP_FG = LIGHT_THEME["TOOLTIP_FG"]
MUTED_CARD_BG = LIGHT_THEME["MUTED_CARD_BG"]
SKELETON_BG = LIGHT_THEME["SKELETON_BG"]
SKELETON_PULSE_BG = LIGHT_THEME["SKELETON_PULSE_BG"]
SCROLL_TRACK_IDLE = LIGHT_THEME["SCROLL_TRACK_IDLE"]
TOGGLE_KNOB_HI_ON = LIGHT_THEME["TOGGLE_KNOB_HI_ON"]
TOGGLE_KNOB_SH_ON = LIGHT_THEME["TOGGLE_KNOB_SH_ON"]
WIN_BTN_HOVER_BG = LIGHT_THEME["WIN_BTN_HOVER_BG"]
WIN_BTN_PRESS_BG = LIGHT_THEME["WIN_BTN_PRESS_BG"]
WIN_BTN_CLOSE_HOVER_BG = LIGHT_THEME["WIN_BTN_CLOSE_HOVER_BG"]
WIN_BTN_CLOSE_PRESS_BG = LIGHT_THEME["WIN_BTN_CLOSE_PRESS_BG"]
WIN_BTN_CLOSE_FG = LIGHT_THEME["WIN_BTN_CLOSE_FG"]
OVERLAY_BACKDROP_BG = LIGHT_THEME["OVERLAY_BACKDROP_BG"]
HEADING_ACTIVE_BG = LIGHT_THEME["HEADING_ACTIVE_BG"]
TRANSP_KEY = LIGHT_THEME["TRANSP_KEY"]
HOVER_PILL_BG = LIGHT_THEME["HOVER_PILL_BG"]
CARD_GLOW_RGB: tuple[int, int, int] = LIGHT_THEME["CARD_GLOW_RGB"]

STATUS_PILL_STYLES: dict[str, tuple[str, str]] = dict(STATUS_PILL_STYLES_BY_THEME["light"])

# 统一字阶常量（集中四档+标题：caption / body / subtitle / title / h1 / numeric）
FONT_CAPTION = 9
FONT_BODY = 10
FONT_SUBTITLE = 12
FONT_TITLE = 15
FONT_H1 = 18
FONT_NUMERIC = 25

FONT_SCALE_MODES = ("standard", "large", "xlarge")
_THEME_PREF = "light"       # "light" | "dark" | "system"
_ACTIVE_THEME = "light"     # "light" | "dark"
_FONT_SCALE_MODE = "standard"

BASE_RGB = (224, 229, 236)
SHADOW_RGB = (184, 188, 194)
HIGHLIGHT_RGB = (255, 255, 255)
BLURPLE_RGB = (109, 93, 252)
FOCUS_RGB = (98, 82, 245)
CORAL_RGB = (255, 107, 107)
MINT_RGB = (78, 205, 196)
FOCUS_SHADOW_RGB = (
    int(SHADOW_RGB[0] * 0.65 + BLURPLE_RGB[0] * 0.35),
    int(SHADOW_RGB[1] * 0.65 + BLURPLE_RGB[1] * 0.35),
    int(SHADOW_RGB[2] * 0.65 + BLURPLE_RGB[2] * 0.35),
)

_FONT_CACHE: Optional[str] = None
_DISPLAY_FONT_CACHE: Optional[str] = None
_MONO_CACHE: Optional[str] = None
_ERROR_HANDLER: Optional[Callable[[str, Exception], None]] = None


def _resolve_system_dark() -> bool:
    try:
        from tray_common import is_windows_dark_mode
        return bool(is_windows_dark_mode())
    except Exception:
        return False


def clear_render_caches() -> None:
    """主题或 DPI 切换后清理位图纹理缓存，避免残余旧主题表面图像。"""
    try:
        _CACHE._seeds.clear()
    except Exception:
        pass
    _PILL_PHOTO_CACHE.clear()
    _TOGGLE_PHOTO_CACHE.clear()
    _SCRIM_PNG_CACHE.clear()
    _MODAL_SEED_CACHE.clear()


def apply_theme(mode: str = "light") -> str:
    """切换浅色 / 深色 / 跟随系统主题并同步全部全局设计令牌，返回生效主题名（light/dark）。"""
    global _THEME_PREF, _ACTIVE_THEME
    global BASE_RGB, SHADOW_RGB, HIGHLIGHT_RGB, BLURPLE_RGB, FOCUS_RGB, CORAL_RGB, MINT_RGB, FOCUS_SHADOW_RGB
    global STATUS_PILL_STYLES, CARD_GLOW_RGB

    pref = str(mode or "light").strip().lower()
    if pref not in ("light", "dark", "system"):
        pref = "light"
    _THEME_PREF = pref
    eff = ("dark" if _resolve_system_dark() else "light") if pref == "system" else pref
    changed = (eff != _ACTIVE_THEME)
    _ACTIVE_THEME = eff

    palette = DARK_THEME if eff == "dark" else LIGHT_THEME
    g = globals()
    for k, v in palette.items():
        g[k] = v

    BASE_RGB = hex_to_rgb(BASE)
    SHADOW_RGB = hex_to_rgb(SHADOW_GREY)
    HIGHLIGHT_RGB = hex_to_rgb(HIGHLIGHT)
    BLURPLE_RGB = hex_to_rgb(BLURPLE_BRAND)
    FOCUS_RGB = hex_to_rgb(BLURPLE_FOCUS)
    CORAL_RGB = hex_to_rgb(CORAL)
    MINT_RGB = hex_to_rgb(MINT)
    FOCUS_SHADOW_RGB = (
        int(SHADOW_RGB[0] * 0.65 + FOCUS_RGB[0] * 0.35),
        int(SHADOW_RGB[1] * 0.65 + FOCUS_RGB[1] * 0.35),
        int(SHADOW_RGB[2] * 0.65 + FOCUS_RGB[2] * 0.35),
    )
    CARD_GLOW_RGB = tuple(palette["CARD_GLOW_RGB"])
    STATUS_PILL_STYLES = dict(STATUS_PILL_STYLES_BY_THEME[eff])
    if changed:
        clear_render_caches()
    return eff


def get_theme_mode() -> str:
    return _THEME_PREF


def get_active_theme() -> str:
    return _ACTIVE_THEME


def is_dark_theme() -> bool:
    return _ACTIVE_THEME == "dark"


def get_status_pill_style(kind: str) -> tuple[str, str, Optional[str]]:
    """返回状态胶囊当前主题下的 (背景色, 前景色, 描边色或None)。"""
    styles = STATUS_PILL_STYLES_BY_THEME.get(_ACTIVE_THEME, STATUS_PILL_STYLES_BY_THEME["light"])
    borders = STATUS_PILL_BORDERS_BY_THEME.get(_ACTIVE_THEME, STATUS_PILL_BORDERS_BY_THEME["light"])
    bg_c, fg_c = styles.get(kind, (PILL_NEUTRAL, TEXT_MUTED))
    return bg_c, fg_c, borders.get(kind)


def set_font_scale_mode(mode: str) -> str:
    """设置全局界面字号档位：standard / large / xlarge。"""
    global _FONT_SCALE_MODE
    m = str(mode or "standard").strip().lower()
    if m not in FONT_SCALE_MODES:
        m = "standard"
    _FONT_SCALE_MODE = m
    return m


def get_font_scale_mode() -> str:
    return _FONT_SCALE_MODE


def font_scale_ratio() -> float:
    return {"standard": 1.0, "large": 1.16, "xlarge": 1.30}.get(_FONT_SCALE_MODE, 1.0)


def scaled_font_size(size: int) -> int:
    """按界面字号档位线性放大各级文字（确保标准态保持原字号不变）。"""
    s = int(size)
    if _FONT_SCALE_MODE == "large":
        return s + (3 if s >= 20 else 2)
    if _FONT_SCALE_MODE == "xlarge":
        return s + (5 if s >= 20 else (4 if s >= 15 else 3))
    return s


def battery_accent_color(
    level: Optional[int],
    *,
    threshold: int = 20,
    charging: bool = False,
    offline: bool = False,
    full: bool = False,
    asleep: bool = False,
    stale: bool = False,
) -> str:
    """统一主面板与调试面板的电量文本语义色。"""
    if offline or asleep or level is None:
        return TEXT_DISABLED
    if charging and not full:
        return AMBER_CHARGE
    if not full and level < threshold:
        return RED_DANGER
    if stale:
        return INFO
    return TEXT


def fit_ellipsis(text: str, max_px: int, font_obj: tkfont.Font) -> str:
    """按像素宽度二分截断长文本并追加省略号，统一适配中英文混排。"""
    s = " ".join((text or "").split())
    if not s or max_px <= 0:
        return s
    try:
        if font_obj.measure(s) <= max_px:
            return s
        lo, hi = 1, len(s)
        best = 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if font_obj.measure(s[:mid] + "…") <= max_px:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return s[:best].rstrip() + "…"
    except Exception:
        return s


_KB_FOCUS_ACTIVE = False
_FOCUS_HOOKED_ROOTS: set[int] = set()


def _mark_keyboard_focus() -> None:
    global _KB_FOCUS_ACTIVE
    _KB_FOCUS_ACTIVE = True


def _mark_pointer_focus() -> None:
    global _KB_FOCUS_ACTIVE
    _KB_FOCUS_ACTIVE = False


def _ensure_focus_hooks(widget: tk.Misc) -> None:
    try:
        top = widget.winfo_toplevel()
        tid = int(top.winfo_id())
        if tid in _FOCUS_HOOKED_ROOTS:
            return
        _FOCUS_HOOKED_ROOTS.add(tid)
        top.bind("<KeyPress-Tab>", lambda _e: _mark_keyboard_focus(), add="+")
        top.bind("<Shift-KeyPress-Tab>", lambda _e: _mark_keyboard_focus(), add="+")
        top.bind("<ISO_Left_Tab>", lambda _e: _mark_keyboard_focus(), add="+")

        def _on_top_pointer_press(event) -> None:
            _mark_pointer_focus()
            try:
                focused = top.focus_get()
                if isinstance(focused, (tk.Entry, tk.Text)):
                    w = getattr(event, "widget", None)
                    in_same_entry = False
                    while w is not None:
                        if w is focused or (isinstance(w, NeuEntry) and w.entry is focused):
                            in_same_entry = True
                            break
                        try:
                            parent_name = w.winfo_parent()
                        except Exception:
                            break
                        if not parent_name:
                            break
                        try:
                            w = w.nametowidget(parent_name)
                        except Exception:
                            break
                    if not in_same_entry:
                        top.focus_set()
            except Exception:
                pass

        top.bind("<ButtonPress-1>", _on_top_pointer_press, add="+")
    except Exception:
        pass


_OUTSIDE_POINT_STRUCT_READY = False


class _POINT_CT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _setup_win32_popup_funcs() -> Any:
    global _OUTSIDE_POINT_STRUCT_READY
    if sys.platform != "win32":
        return None
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    if not _OUTSIDE_POINT_STRUCT_READY:
        try:
            u32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT_CT)]
            u32.GetCursorPos.restype = wintypes.BOOL
            u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
            u32.GetAsyncKeyState.restype = ctypes.c_short
            u32.SetWindowPos.argtypes = [
                wintypes.HWND, wintypes.HWND,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
            ]
            u32.SetWindowPos.restype = wintypes.BOOL
            u32.SetForegroundWindow.argtypes = [wintypes.HWND]
            u32.SetForegroundWindow.restype = wintypes.BOOL
            u32.GetForegroundWindow.argtypes = []
            u32.GetForegroundWindow.restype = wintypes.HWND
            u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            u32.GetWindowThreadProcessId.restype = wintypes.DWORD
        except Exception:
            pass
        _OUTSIDE_POINT_STRUCT_READY = True
    return u32


def _set_window_pos_topmost(
    hwnd: int,
    x: int = 0,
    y: int = 0,
    w: int = 0,
    h: int = 0,
    *,
    nomove: bool = False,
    nosize: bool = False,
    no_activate: bool = True,
    show: bool = True,
) -> bool:
    """用 64 位安全声明将 HWND 置于 HWND_TOPMOST 最顶层（不被任务栏/其他顶层窗遮挡）。"""
    if not hwnd or sys.platform != "win32":
        return False
    u32 = _setup_win32_popup_funcs()
    if u32 is None:
        return False
    flags = 0
    if nosize:
        flags |= 0x0001
    if nomove:
        flags |= 0x0002
    if no_activate:
        flags |= 0x0010
    if show:
        flags |= 0x0040
    try:
        return bool(u32.SetWindowPos(wintypes.HWND(int(hwnd)), wintypes.HWND(-1), int(x), int(y), int(w), int(h), flags))
    except Exception:
        return False


def _get_pointer_screen_pos(fallback: Optional[tk.Misc] = None) -> tuple[int, int]:
    if sys.platform == "win32":
        u32 = _setup_win32_popup_funcs()
        if u32 is not None:
            try:
                pt = _POINT_CT()
                if u32.GetCursorPos(ctypes.byref(pt)):
                    return int(pt.x), int(pt.y)
            except Exception:
                pass
    if fallback is not None:
        try:
            px, py = fallback.winfo_pointerxy()
            return int(px), int(py)
        except Exception:
            pass
    return -99999, -99999


def _sample_mouse_buttons_down() -> tuple[bool, bool, bool]:
    if sys.platform != "win32":
        return (False, False, False)
    u32 = _setup_win32_popup_funcs()
    if u32 is None:
        return (False, False, False)
    try:
        return (
            bool(int(u32.GetAsyncKeyState(0x01)) & 0x8000),
            bool(int(u32.GetAsyncKeyState(0x02)) & 0x8000),
            bool(int(u32.GetAsyncKeyState(0x04)) & 0x8000),
        )
    except Exception:
        return (False, False, False)


def _foreground_pid() -> int:
    if sys.platform != "win32":
        return 0
    u32 = _setup_win32_popup_funcs()
    if u32 is None:
        return 0
    try:
        fg = u32.GetForegroundWindow()
        if not fg:
            return 0
        pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


class _OutsideClickWatcher:
    """监听任意窗口/桌面/任务栏上的全局鼠标按下边沿及前台切换；点击浮层外部立即收起菜单或下拉框。"""

    def __init__(
        self,
        owner: tk.Misc,
        rects_fn: Callable[[], list[tuple[int, int, int, int]]],
        on_outside_click: Callable[[], Any],
        interval_ms: int = 16,
    ) -> None:
        self._owner = owner
        self._rects_fn = rects_fn
        self._on_outside = on_outside_click
        self._interval_ms = max(12, int(interval_ms))
        self._prev = _sample_mouse_buttons_down()
        self._self_pid = int(ctypes.windll.kernel32.GetCurrentProcessId()) if sys.platform == "win32" else 0
        self._had_our_fg = (_foreground_pid() == self._self_pid) if self._self_pid else False
        self._aid: Optional[str] = None
        self._stopped = False
        self._schedule()

    def _schedule(self) -> None:
        if self._stopped:
            return
        try:
            if not self._owner.winfo_exists():
                return
            self._aid = self._owner.after(self._interval_ms, self._tick)
        except Exception:
            self._aid = None

    def _tick(self) -> None:
        self._aid = None
        if self._stopped:
            return
        try:
            if not self._owner.winfo_exists():
                return
        except Exception:
            return
        cur = _sample_mouse_buttons_down()
        # 仅检测松开->按下的新建点击边沿，避免刚点击弹出时尚未松手即误触发关闭
        rising = any(c and (not p) for c, p in zip(cur, self._prev))
        self._prev = cur
        if rising:
            cx, cy = _get_pointer_screen_pos(self._owner)
            rects = self._rects_fn()
            inside = any(x0 <= cx <= x1 and y0 <= cy <= y1 for x0, y0, x1, y1 in rects)
            if not inside:
                self.stop()
                self._on_outside()
                return
        if self._self_pid:
            fg_pid = _foreground_pid()
            if fg_pid == self._self_pid:
                self._had_our_fg = True
            elif self._had_our_fg and fg_pid != 0:
                self.stop()
                self._on_outside()
                return
        self._schedule()

    def stop(self) -> None:
        self._stopped = True
        if self._aid is not None:
            try:
                if self._owner.winfo_exists():
                    self._owner.after_cancel(self._aid)
            except Exception:
                pass
            self._aid = None


_ACTIVE_POPUP_MENUS: list[Any] = []


def has_active_popup_menu() -> bool:
    return any(getattr(m, "is_open", lambda: False)() for m in list(_ACTIVE_POPUP_MENUS))


def dismiss_active_popup_menus(top: Optional[tk.Misc] = None) -> None:
    """关闭当前打开的全部弹出菜单与下拉选择器。"""
    for m in list(_ACTIVE_POPUP_MENUS):
        try:
            m.close(restore_focus=False)
        except Exception:
            pass
    if top is not None:
        try:
            root_w = top.winfo_toplevel()
            for sel in list(getattr(root_w, "_neu_open_selects", ())):
                if hasattr(sel, "_close_popup"):
                    sel._close_popup(restore_focus=False)
        except Exception:
            pass


def set_command_error_handler(handler: Optional[Callable[[str, Exception], None]]) -> None:
    """注册全局按钮回调异常处理器（向用户展示错误提示并记录日志）。"""
    global _ERROR_HANDLER
    _ERROR_HANDLER = handler


def _safe_invoke(command: Optional[Callable[[], Any]], source: str = "") -> None:
    if not command:
        return
    try:
        command()
    except Exception as exc:
        if _ERROR_HANDLER is not None:
            try:
                _ERROR_HANDLER(source, exc)
                return
            except Exception:
                pass
        import traceback
        traceback.print_exc()


class NeuTooltip:
    """轻量悬停无障碍提示气泡：延迟出现、不抢焦点、移开或点击即自动销毁。"""

    def __init__(self, widget: tk.Misc, text: str, delay_ms: int = 300) -> None:
        self._widget = widget
        self._text = text
        self._delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._cancel, add="+")
        widget.bind("<ButtonPress-1>", self._cancel, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def set_text(self, text: str) -> None:
        self._text = text

    def _schedule(self, _e=None) -> None:
        self._cancel()
        if not self._text:
            return
        try:
            self._after_id = self._widget.after(self._delay_ms, self._show)
        except Exception:
            self._after_id = None

    def _cancel(self, _e=None) -> None:
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._tip is not None:
            try:
                if self._tip.winfo_exists():
                    self._tip.destroy()
            except Exception:
                pass
            self._tip = None

    def _on_destroy(self, event=None) -> None:
        if event is None or event.widget is self._widget:
            self._cancel()

    def _show(self) -> None:
        self._after_id = None
        if not self._text or not self._widget.winfo_exists():
            return
        try:
            px, py = self._widget.winfo_pointerxy()
            over = self._widget.winfo_containing(px, py)
            w = over
            inside = False
            while w is not None:
                if w is self._widget:
                    inside = True
                    break
                w = getattr(w, "master", None)
            if not inside:
                return
        except Exception:
            pass

        tip = tk.Toplevel(self._widget)
        tip.withdraw()
        tip.configure(bg=TOOLTIP_BG)
        enable_frameless_taskbar(tip, no_activate=True)
        lbl = tk.Label(
            tip,
            text=self._text,
            bg=TOOLTIP_BG,
            fg=TOOLTIP_FG,
            font=ui_font(FONT_CAPTION, root=tip),
            padx=sx(9, tip),
            pady=sx(4, tip),
            justify="left",
        )
        lbl.pack()
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        wl, wt, wr, wb = get_work_area()
        wx = self._widget.winfo_rootx() + (self._widget.winfo_width() - tw) // 2
        wy = self._widget.winfo_rooty() + self._widget.winfo_height() + sx(4, tip)
        if wy + th > wb - 6:
            wy = self._widget.winfo_rooty() - th - sx(4, tip)
        wx = max(wl + 6, min(wr - tw - 6, wx))
        wy = max(wt + 6, min(wb - th - 6, wy))
        tip.geometry(f"{tw}x{th}+{wx}+{wy}")
        tip.deiconify()
        enable_frameless_taskbar(tip, no_activate=True)
        try:
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            hwnd = _root_hwnd(tip)
            if hwnd:
                u32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1), wx, wy, tw, th, 0x0010 | 0x0040)
        except Exception:
            pass
        self._tip = tip


def attach_tooltip(widget: tk.Misc, text: str, delay_ms: int = 300) -> NeuTooltip:
    return NeuTooltip(widget, text, delay_ms=delay_ms)


def hex_to_rgb(hx: str) -> tuple[int, int, int]:
    s = hx.lstrip("#")
    if len(s) != 6:
        return BASE_RGB
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def blend_rgb(
    bg: tuple[int, int, int], fg: tuple[int, int, int], alpha: float
) -> tuple[int, int, int]:
    a = 0.0 if alpha <= 0.0 else (1.0 if alpha >= 1.0 else alpha)
    inv = 1.0 - a
    return (
        int(bg[0] * inv + fg[0] * a + 0.5),
        int(bg[1] * inv + fg[1] * a + 0.5),
        int(bg[2] * inv + fg[2] * a + 0.5),
    )


def pick_font_family(root: Optional[tk.Misc] = None) -> str:
    """优先使用 Microsoft YaHei UI，确保纯中文及中英混排走统一字族不逐字跳跃。"""
    global _FONT_CACHE
    if _FONT_CACHE:
        return _FONT_CACHE
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    for candidate in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Variable Text", "Segoe UI", "Inter"):
        if candidate in fams:
            _FONT_CACHE = candidate
            return candidate
    _FONT_CACHE = "Microsoft YaHei UI"
    return _FONT_CACHE


def pick_display_font_family(root: Optional[tk.Misc] = None) -> str:
    """纯数字 / 英文展示文本优先使用 Win11 Segoe UI Variable Display。"""
    global _DISPLAY_FONT_CACHE
    if _DISPLAY_FONT_CACHE:
        return _DISPLAY_FONT_CACHE
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    for candidate in ("Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"):
        if candidate in fams:
            _DISPLAY_FONT_CACHE = candidate
            return candidate
    _DISPLAY_FONT_CACHE = pick_font_family(root)
    return _DISPLAY_FONT_CACHE


def pick_mono_family(root: Optional[tk.Misc] = None) -> str:
    global _MONO_CACHE
    if _MONO_CACHE:
        return _MONO_CACHE
    try:
        fams = set(tkfont.families(root))
    except Exception:
        fams = set()
    for candidate in ("Cascadia Mono", "Consolas", "Courier New"):
        if candidate in fams:
            _MONO_CACHE = candidate
            return candidate
    _MONO_CACHE = "Consolas"
    return _MONO_CACHE


def ui_font(size: int, weight: str = "normal", root: Optional[tk.Misc] = None) -> tuple[str, int, str]:
    fam = pick_font_family(root)
    sz = scaled_font_size(size)
    return (fam, sz, weight) if weight != "normal" else (fam, sz, "normal")


def display_font(size: int = FONT_NUMERIC, weight: str = "bold", root: Optional[tk.Misc] = None) -> tuple[str, int, str]:
    fam = pick_display_font_family(root)
    sz = scaled_font_size(size)
    return (fam, sz, weight) if weight != "normal" else (fam, sz, "normal")


def mono_font(size: int = FONT_CAPTION, root: Optional[tk.Misc] = None) -> tuple[str, int]:
    return (pick_mono_family(root), scaled_font_size(size))


_DPI_CACHE: Optional[float] = None


def _query_window_dpi_scale(root: Optional[tk.Misc]) -> float:
    if root is not None:
        try:
            hwnd = int(root.winfo_toplevel().winfo_id())
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            get_dpi = getattr(u32, "GetDpiForWindow", None)
            if get_dpi is not None:
                get_dpi.restype = wintypes.UINT
                get_dpi.argtypes = [wintypes.HWND]
                dpi = int(get_dpi(wintypes.HWND(hwnd)))
                if dpi >= 96:
                    return max(1.0, dpi / 96.0)
        except Exception:
            pass
        try:
            return max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            pass
    return 1.0


def refresh_dpi_scale(root: Optional[tk.Misc] = None) -> bool:
    """检测多显示器 DPI 切换并按需刷新缓存，返回是否发生 DPI 变化。"""
    global _DPI_CACHE
    new_sc = _query_window_dpi_scale(root)
    if _DPI_CACHE is None or abs(new_sc - _DPI_CACHE) >= 0.05:
        changed = _DPI_CACHE is not None
        _DPI_CACHE = new_sc
        return changed
    return False


def dpi_scale(root: Optional[tk.Misc] = None) -> float:
    """返回相对 96 DPI 的显示器物理缩放系数（如 1.0, 1.25, 1.5, 1.75, 2.0）。"""
    global _DPI_CACHE
    if _DPI_CACHE is not None:
        return _DPI_CACHE
    if root is not None:
        _DPI_CACHE = _query_window_dpi_scale(root)
        return _DPI_CACHE
    return 1.0


def sx(px: float, root: Optional[tk.Misc] = None, max_scale: float = 2.5) -> int:
    """将 96-DPI 逻辑像素转换为当前 DPI 物理像素。"""
    sc = min(max_scale, dpi_scale(root))
    return max(1, int(round(px * sc)))


def fit_geometry(
    root: tk.Tk, w_dp: int, h_dp: int, min_w_dp: int, min_h_dp: int
) -> None:
    """按当前 DPI 与桌面工作区计算舒适且不遮挡任务栏的窗口尺寸与最小尺寸。"""
    sc = dpi_scale(root)
    wl, wt, wr, wb = get_work_area()
    wa_w = max(800, wr - wl)
    wa_h = max(600, wb - wt)
    w = min(wa_w - 32, int(round(w_dp * sc)))
    h = min(wa_h - 32, int(round(h_dp * sc)))
    mw = min(w, int(round(min_w_dp * sc)))
    mh = min(h, int(round(min_h_dp * sc)))
    x = wl + max(8, (wa_w - w) // 2)
    y = wt + max(8, (wa_h - h) // 2)
    root.geometry(f"{w}x{h}+{x}+{y}")
    root.minsize(mw, mh)


# ---- SDF 与 3-pass 分离均值模糊 (模拟真实 CSS box-shadow) ----

def _sdf_rounded_box(
    px: float, py: float, cx: float, cy: float, hw: float, hh: float, rad: float
) -> float:
    dx = abs(px - cx) - hw + rad
    dy = abs(py - cy) - hh + rad
    return math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - rad


def _box_blur_1d_rows(grid: list[list[float]], r: int) -> list[list[float]]:
    if r <= 0:
        return [row[:] for row in grid]
    h = len(grid)
    w = len(grid[0])
    inv = 1.0 / (2 * r + 1)
    out: list[list[float]] = []
    for y in range(h):
        src = grid[y]
        dst = [0.0] * w
        # prefix sum (out-of-bounds = 0.0)
        pref = [0.0] * (w + 1)
        s = 0.0
        for x in range(w):
            s += src[x]
            pref[x + 1] = s
        for x in range(w):
            x0 = x - r
            x1 = x + r + 1
            if x0 < 0:
                x0 = 0
            if x1 > w:
                x1 = w
            dst[x] = (pref[x1] - pref[x0]) * inv
        out.append(dst)
    return out


def _transpose(grid: list[list[float]]) -> list[list[float]]:
    h = len(grid)
    w = len(grid[0])
    return [[grid[y][x] for y in range(h)] for x in range(w)]


def _blur_2d(grid: list[list[float]], box_r: int, passes: int = 3) -> list[list[float]]:
    if box_r <= 0:
        return [row[:] for row in grid]
    cur = grid
    for _ in range(passes):
        cur = _box_blur_1d_rows(cur, box_r)
        cur = _transpose(cur)
        cur = _box_blur_1d_rows(cur, box_r)
        cur = _transpose(cur)
    return cur


class _ShadowSeedCache:
    """缓存 9-slice 新拟物阴影种子图，任何目标尺寸仅需极快字节切片拼接。"""

    def __init__(self) -> None:
        self._seeds: dict[tuple, tuple[int, int, list[bytes]]] = {}

    def _get_seed(
        self,
        radius: int,
        off: int,
        box_r: int,
        pad: int,
        inset: bool,
        shadow_rgb: tuple[int, int, int],
        light_rgb: tuple[int, int, int],
        fill_rgb: tuple[int, int, int],
        outer_rgb: tuple[int, int, int],
        shadow_strength: float,
        light_strength: float,
        glow_rgb: Optional[tuple[int, int, int]] = None,
        glow_strength: float = 0.0,
    ) -> tuple[int, int, list[bytes]]:
        eff_glow_rgb = HIGHLIGHT_RGB if glow_rgb is None else glow_rgb
        key = (
            radius,
            off,
            box_r,
            pad,
            inset,
            shadow_rgb,
            light_rgb,
            fill_rgb,
            outer_rgb,
            round(shadow_strength, 3),
            round(light_strength, 3),
            eff_glow_rgb if glow_strength > 0.0 else HIGHLIGHT_RGB,
            round(glow_strength, 3),
        )
        cached = self._seeds.get(key)
        if cached is not None:
            return cached

        reach = off + 3 * box_r
        eff_pad = max(pad, 0)
        # cut: 从图像边缘到直边恒定区的距离
        cut = eff_pad + radius + reach
        box_span = max(2 * (radius + reach) + 8, 2 * radius + 16)
        size = eff_pad * 2 + box_span
        if cut * 2 + 2 >= size:
            size = cut * 2 + 4
            box_span = size - eff_pad * 2

        cx = size / 2.0
        cy = size / 2.0
        hw = box_span / 2.0
        hh = box_span / 2.0
        rad = min(float(radius), hw - 1.0, hh - 1.0)

        need_sdf = (not inset) and glow_strength > 0.0
        face_cov: list[list[float]] = []
        sdf_rows: list[list[float]] = []
        for y in range(size):
            py = y + 0.5
            row: list[float] = []
            srow: list[float] = []
            for x in range(size):
                d = _sdf_rounded_box(x + 0.5, py, cx, cy, hw, hh, rad)
                row.append(0.0 if d >= 0.5 else (1.0 if d <= -0.5 else 0.5 - d))
                if need_sdf:
                    srow.append(d)
            face_cov.append(row)
            if need_sdf:
                sdf_rows.append(srow)

        blurred = _blur_2d(face_cov, box_r, passes=3)
        br0, bg0, bb0 = outer_rgb
        fr0, fg0, fb0 = fill_rgb
        sr, sg, sb = shadow_rgb
        lr, lg, lb = light_rgb
        gr, gg, gb = glow_rgb

        seed_rows: list[bytes] = []
        for y in range(size):
            row_bytes = bytearray(size * 3)
            yn = y - off
            yp = y + off
            fc_y = face_cov[y]
            bl_y = blurred[y]
            sd_y = sdf_rows[y] if need_sdf else None
            for x in range(size):
                cov = fc_y[x]
                xn = x - off
                xp = x + off

                if not inset:
                    # 外凸起：先在底色上铺设高光与暗影（悬停时叠加四周均匀发光光晕），再覆盖锐利柔边的前景面板
                    bd = blurred[yn][xn] if (0 <= yn < size and 0 <= xn < size) else 0.0
                    bl = blurred[yp][xp] if (0 <= yp < size and 0 <= xp < size) else 0.0
                    al_l = min(1.0, bl * light_strength)
                    al_d = min(1.0, bd * shadow_strength)
                    if glow_strength > 0.0:
                        al_d *= max(0.06, 1.0 - glow_strength * 0.92)

                    r = br0 * (1.0 - al_l) + lr * al_l
                    g = bg0 * (1.0 - al_l) + lg * al_l
                    b = bb0 * (1.0 - al_l) + lb * al_l

                    r = r * (1.0 - al_d) + sr * al_d
                    g = g * (1.0 - al_d) + sg * al_d
                    b = b * (1.0 - al_d) + sb * al_d

                    if glow_strength > 0.0:
                        al_g = min(1.0, (bl_y[x] * 2.15) ** 0.65 * glow_strength)
                        r = r * (1.0 - al_g) + gr * al_g
                        g = g * (1.0 - al_g) + gg * al_g
                        b = b * (1.0 - al_g) + gb * al_g

                    if cov > 0.0:
                        face_r, face_g, face_b = float(fr0), float(fg0), float(fb0)
                        if sd_y is not None:
                            d_val = sd_y[x]
                            if d_val > -2.6:
                                rim = max(0.0, min(1.0, (d_val + 2.6) / 2.6))
                                rim_a = (rim ** 1.15) * 0.72 * glow_strength
                                face_r = face_r * (1.0 - rim_a) + gr * rim_a
                                face_g = face_g * (1.0 - rim_a) + gg * rim_a
                                face_b = face_b * (1.0 - rim_a) + gb * rim_a
                        r = r * (1.0 - cov) + face_r * cov
                        g = g * (1.0 - cov) + face_g * cov
                        b = b * (1.0 - cov) + face_b * cov
                else:
                    # 内凹陷：仅在轮廓内部绘制顶部/左侧暗影与底部/右侧高光
                    if cov <= 0.0:
                        r, g, b = float(br0), float(bg0), float(bb0)
                    else:
                        bd_in = 1.0 - (blurred[yn][xn] if (0 <= yn < size and 0 <= xn < size) else 0.0)
                        bl_in = 1.0 - (blurred[yp][xp] if (0 <= yp < size and 0 <= xp < size) else 0.0)
                        al_d = min(1.0, max(0.0, bd_in) * shadow_strength)
                        al_l = min(1.0, max(0.0, bl_in) * light_strength)

                        ir = fr0 * (1.0 - al_l) + lr * al_l
                        ig = fg0 * (1.0 - al_l) + lg * al_l
                        ib = fb0 * (1.0 - al_l) + lb * al_l

                        ir = ir * (1.0 - al_d) + sr * al_d
                        ig = ig * (1.0 - al_d) + sg * al_d
                        ib = ib * (1.0 - al_d) + sb * al_d

                        r = br0 * (1.0 - cov) + ir * cov
                        g = bg0 * (1.0 - cov) + ig * cov
                        b = bb0 * (1.0 - cov) + ib * cov

                idx = x * 3
                row_bytes[idx] = max(0, min(255, int(r + 0.5)))
                row_bytes[idx + 1] = max(0, min(255, int(g + 0.5)))
                row_bytes[idx + 2] = max(0, min(255, int(b + 0.5)))
            seed_rows.append(bytes(row_bytes))

        res = (size, cut, seed_rows)
        self._seeds[key] = res
        return res

    def render_rgb_rows(
        self,
        box_w: int,
        box_h: int,
        *,
        radius: int = 24,
        off: int = 6,
        box_r: int = 4,
        pad: int = 18,
        inset: bool = False,
        shadow_rgb: Optional[tuple[int, int, int]] = None,
        light_rgb: Optional[tuple[int, int, int]] = None,
        fill_rgb: Optional[tuple[int, int, int]] = None,
        outer_rgb: Optional[tuple[int, int, int]] = None,
        shadow_strength: float = 0.90,
        light_strength: float = 0.92,
        glow_rgb: Optional[tuple[int, int, int]] = None,
        glow_strength: float = 0.0,
    ) -> tuple[int, int, list[bytes]]:
        s_rgb = SHADOW_RGB if shadow_rgb is None else shadow_rgb
        l_rgb = HIGHLIGHT_RGB if light_rgb is None else light_rgb
        f_rgb = BASE_RGB if fill_rgb is None else fill_rgb
        o_rgb = BASE_RGB if outer_rgb is None else outer_rgb
        g_rgb = CARD_GLOW_RGB if glow_rgb is None else glow_rgb
        box_w = max(8, int(box_w))
        box_h = max(8, int(box_h))
        tot_w = box_w + pad * 2
        tot_h = box_h + pad * 2
        eff_r = max(2, min(radius, box_w // 2, box_h // 2))

        size, cut, seed_rows = self._get_seed(
            eff_r,
            off,
            box_r,
            pad,
            inset,
            s_rgb,
            l_rgb,
            f_rgb,
            o_rgb,
            shadow_strength,
            light_strength,
            g_rgb,
            glow_strength,
        )

        # 横向 9-slice
        if tot_w >= cut * 2:
            lc = cut
            rc = cut
            mid_w = tot_w - cut * 2
        else:
            lc = tot_w // 2
            rc = tot_w - lc
            mid_w = 0

        # 纵向 9-slice
        if tot_h >= cut * 2:
            tc = cut
            bc = cut
            mid_h = tot_h - cut * 2
        else:
            tc = tot_h // 2
            bc = tot_h - tc
            mid_h = 0

        def _slice_row(sr: bytes) -> bytes:
            if mid_w > 0:
                mid_px = sr[cut * 3 : (cut + 1) * 3]
                return sr[: lc * 3] + (mid_px * mid_w) + sr[(size - rc) * 3 :]
            return sr[: lc * 3] + sr[(size - rc) * 3 :]

        rows: list[bytes] = []
        for y in range(tc):
            rows.append(_slice_row(seed_rows[y]))
        if mid_h > 0:
            mid_row = _slice_row(seed_rows[cut])
            for _ in range(mid_h):
                rows.append(mid_row)
        for y in range(size - bc, size):
            rows.append(_slice_row(seed_rows[y]))
        return tot_w, tot_h, rows

    def render_photo(
        self,
        box_w: int,
        box_h: int,
        *,
        radius: int = 24,
        off: int = 6,
        box_r: int = 4,
        pad: int = 18,
        inset: bool = False,
        shadow_rgb: Optional[tuple[int, int, int]] = None,
        light_rgb: Optional[tuple[int, int, int]] = None,
        fill_rgb: Optional[tuple[int, int, int]] = None,
        outer_rgb: Optional[tuple[int, int, int]] = None,
        shadow_strength: float = 0.90,
        light_strength: float = 0.92,
        glow_rgb: Optional[tuple[int, int, int]] = None,
        glow_strength: float = 0.0,
    ) -> tk.PhotoImage:
        tot_w, tot_h, rows = self.render_rgb_rows(
            box_w,
            box_h,
            radius=radius,
            off=off,
            box_r=box_r,
            pad=pad,
            inset=inset,
            shadow_rgb=shadow_rgb,
            light_rgb=light_rgb,
            fill_rgb=fill_rgb,
            outer_rgb=outer_rgb,
            shadow_strength=shadow_strength,
            light_strength=light_strength,
            glow_rgb=glow_rgb,
            glow_strength=glow_strength,
        )
        header = f"P6\n{tot_w} {tot_h}\n255\n".encode("ascii")
        return tk.PhotoImage(data=header + b"".join(rows), format="PPM")


_CACHE = _ShadowSeedCache()
_PILL_PHOTO_CACHE: dict[tuple, bytes] = {}
_TOGGLE_PHOTO_CACHE: dict[tuple, bytes] = {}
_OUTLINE_PNG_CACHE: dict[tuple, str] = {}
_GLYPH_PNG_CACHE: dict[tuple, str] = {}
_HOVER_PILL_CACHE: dict[tuple, str] = {}
_HOVER_DOT_CACHE: dict[tuple, str] = {}
_SS_OFFSETS = (0.125, 0.375, 0.625, 0.875)


def _encode_rgba_png_b64(w: int, h: int, raw_rows: list[bytes]) -> str:
    def _chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"".join(raw_rows), level=1)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    return base64.b64encode(png).decode("ascii")


def _make_aa_rounded_outline_photo(
    w: int,
    h: int,
    *,
    radius: float,
    color_hex: str,
    stroke_width: float = 1.5,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    """生成 SDF 抗锯齿圆角描边透明 PNG PhotoImage，消除 Tk Canvas 锯齿与接缝。"""
    iw = max(4, int(round(w)))
    ih = max(4, int(round(h)))
    sw = max(0.8, float(stroke_width))
    rad_val = max(1.5, float(radius))
    key = (iw, ih, round(rad_val, 2), color_hex, round(sw, 2))
    b64 = _OUTLINE_PNG_CACHE.get(key)
    if b64 is None:
        r_c, g_c, b_c = hex_to_rgb(color_hex)
        cx = iw / 2.0
        cy = ih / 2.0
        half_w = sw / 2.0
        hw = max(1.5, (iw - sw - 1.0) / 2.0)
        hh = max(1.5, (ih - sw - 1.0) / 2.0)
        rad = min(rad_val, hw, hh)
        raw_rows: list[bytes] = []
        for y in range(ih):
            py = y + 0.5
            rb = bytearray(1 + iw * 4)
            pos = 1
            for x in range(iw):
                sd = _sdf_rounded_box(x + 0.5, py, cx, cy, hw, hh, rad)
                cov = max(0.0, min(1.0, 0.5 + half_w - abs(sd)))
                if cov > 0.0:
                    rb[pos] = r_c
                    rb[pos + 1] = g_c
                    rb[pos + 2] = b_c
                    rb[pos + 3] = int(cov * 255.0 + 0.5)
                pos += 4
            raw_rows.append(bytes(rb))
        b64 = _encode_rgba_png_b64(iw, ih, raw_rows)
        _OUTLINE_PNG_CACHE[key] = b64
    return tk.PhotoImage(data=b64, master=master)


def _dist_segment(px: float, py: float, x0: float, y0: float, x1: float, y1: float) -> float:
    vx = x1 - x0
    vy = y1 - y0
    wx = px - x0
    wy = py - y0
    seg_len2 = vx * vx + vy * vy
    if seg_len2 <= 1e-9:
        return math.hypot(wx, wy)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / seg_len2))
    return math.hypot(wx - t * vx, wy - t * vy)


def _make_win_btn_glyph_photo(
    w: int,
    h: int,
    *,
    kind: str,
    color_hex: str,
    sc: float,
    dy: float = 0.0,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    """生成窗口控制按钮的 SDF 抗锯齿透明 PNG 图标。"""
    iw = max(8, int(round(w)))
    ih = max(8, int(round(h)))
    key = (iw, ih, kind, color_hex, round(sc, 2), round(dy, 2))
    b64 = _GLYPH_PNG_CACHE.get(key)
    if b64 is None:
        r_c, g_c, b_c = hex_to_rgb(color_hex)
        cx = iw / 2.0
        cy = ih / 2.0 + dy
        d = 4.6 * sc
        lw = max(1.4, 1.45 * sc)
        half_w = lw / 2.0
        raw_rows: list[bytes] = []
        for y in range(ih):
            py = y + 0.5
            rb = bytearray(1 + iw * 4)
            pos = 1
            for x in range(iw):
                px = x + 0.5
                if kind == "min":
                    dist = _dist_segment(px, py, cx - d, cy + 0.5, cx + d, cy + 0.5)
                elif kind == "max":
                    dist = abs(_sdf_rounded_box(px, py, cx, cy, d - 0.5, d - 0.5, 1.2 * sc))
                elif kind == "restore":
                    off = 1.8 * sc
                    front_d = abs(
                        _sdf_rounded_box(
                            px,
                            py,
                            cx - off * 0.5,
                            cy + off * 0.5,
                            d - off * 0.5,
                            d - off * 0.5,
                            1.0 * sc,
                        )
                    )
                    back_top = _dist_segment(px, py, cx - d + off, cy - d, cx + d, cy - d)
                    back_right = _dist_segment(px, py, cx + d, cy - d, cx + d, cy + d - off)
                    dist = min(front_d, back_top, back_right)
                else:
                    s1 = _dist_segment(px, py, cx - d + 0.6, cy - d + 0.6, cx + d - 0.6, cy + d - 0.6)
                    s2 = _dist_segment(px, py, cx + d - 0.6, cy - d + 0.6, cx - d + 0.6, cy + d - 0.6)
                    dist = min(s1, s2)
                cov = max(0.0, min(1.0, 0.5 + half_w - dist))
                if cov > 0.0:
                    rb[pos] = r_c
                    rb[pos + 1] = g_c
                    rb[pos + 2] = b_c
                    rb[pos + 3] = int(cov * 255.0 + 0.5)
                pos += 4
            raw_rows.append(bytes(rb))
        b64 = _encode_rgba_png_b64(iw, ih, raw_rows)
        _GLYPH_PNG_CACHE[key] = b64
    return tk.PhotoImage(data=b64, master=master)


def _make_aa_pill_badge_photo(
    w: int,
    h: int,
    *,
    fill_hex: str,
    border_hex: str,
    radius: float,
    dark_theme: bool,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    """生成带柔和新拟物微阴影与圆角抗锯齿边框的透明 PNG 胶囊背景。"""
    iw = max(12, int(round(w)))
    ih = max(12, int(round(h)))
    rad_val = max(3.0, float(radius))
    key = (iw, ih, fill_hex, border_hex, round(rad_val, 2), bool(dark_theme))
    b64 = _HOVER_PILL_CACHE.get(key)
    if b64 is None:
        fr, fg, fb = hex_to_rgb(fill_hex)
        br, bg_c, bb = hex_to_rgb(border_hex)
        sr, sg, sb = (6, 8, 14) if dark_theme else (82, 95, 118)
        pad_l, pad_t, pad_r, pad_b = 3.0, 2.0, 3.0, 4.0
        bw = max(4.0, iw - pad_l - pad_r)
        bh = max(4.0, ih - pad_t - pad_b)
        cx = pad_l + bw / 2.0
        cy = pad_t + bh / 2.0
        hw = bw / 2.0
        hh = bh / 2.0
        rad = min(rad_val, hw, hh)
        raw_rows: list[bytes] = []
        for y in range(ih):
            py = y + 0.5
            rb = bytearray(1 + iw * 4)
            pos = 1
            for x in range(iw):
                px = x + 0.5
                sd = _sdf_rounded_box(px, py, cx, cy, hw, hh, rad)
                fill_cov = max(0.0, min(1.0, 0.5 - sd))
                # 细微外边框（1px SDF 抗锯齿）
                rim_cov = max(0.0, min(1.0, 0.5 + 0.55 - abs(sd + 0.35)))
                # 底部轻柔投影
                sd_sh = _sdf_rounded_box(px, py - 1.4, cx, cy, hw, hh, rad)
                sh_a = max(0.0, min(1.0, (2.6 - sd_sh) / 3.0)) * (0.34 if dark_theme else 0.16)
                if fill_cov > 0.0:
                    # 内部由填充色与内沿边框混色
                    b_mix = rim_cov * (0.85 if dark_theme else 0.65)
                    cr = int(fr * (1.0 - b_mix) + br * b_mix + 0.5)
                    cg = int(fg * (1.0 - b_mix) + bg_c * b_mix + 0.5)
                    cb = int(fb * (1.0 - b_mix) + bb * b_mix + 0.5)
                    out_a = fill_cov + sh_a * (1.0 - fill_cov)
                    if out_a > 1e-6:
                        rr = int((cr * fill_cov + sr * sh_a * (1.0 - fill_cov)) / out_a + 0.5)
                        rg = int((cg * fill_cov + sg * sh_a * (1.0 - fill_cov)) / out_a + 0.5)
                        rbl = int((cb * fill_cov + sb * sh_a * (1.0 - fill_cov)) / out_a + 0.5)
                        rb[pos] = rr
                        rb[pos + 1] = rg
                        rb[pos + 2] = rbl
                        rb[pos + 3] = int(out_a * 255.0 + 0.5)
                elif sh_a > 0.0:
                    ai = int(sh_a * 255.0 + 0.5)
                    if ai > 0:
                        rb[pos] = sr
                        rb[pos + 1] = sg
                        rb[pos + 2] = sb
                        rb[pos + 3] = ai
                pos += 4
            raw_rows.append(bytes(rb))
        b64 = _encode_rgba_png_b64(iw, ih, raw_rows)
        _HOVER_PILL_CACHE[key] = b64
    return tk.PhotoImage(data=b64, master=master)


def _make_aa_dot_photo(
    r_px: float,
    *,
    fill_hex: str,
    ring_hex: str,
    ring_w: float = 1.6,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    """生成 SDF 抗锯齿圆点透明 PNG（带外描边环）。"""
    rad = max(2.5, float(r_px))
    rw = max(1.0, float(ring_w))
    sz = int(math.ceil((rad + rw + 1.5) * 2.0))
    if sz % 2 == 1:
        sz += 1
    key = (sz, round(rad, 2), fill_hex, ring_hex, round(rw, 2))
    b64 = _HOVER_DOT_CACHE.get(key)
    if b64 is None:
        fr, fg, fb = hex_to_rgb(fill_hex)
        rr, rg, rb_c = hex_to_rgb(ring_hex)
        c = sz / 2.0
        outer_r = rad + rw * 0.5
        inner_r = rad - rw * 0.5
        raw_rows: list[bytes] = []
        for y in range(sz):
            py = y + 0.5
            rb = bytearray(1 + sz * 4)
            pos = 1
            for x in range(sz):
                dist = math.hypot(x + 0.5 - c, py - c)
                outer_cov = max(0.0, min(1.0, 0.5 + outer_r - dist))
                if outer_cov > 0.0:
                    inner_cov = max(0.0, min(1.0, 0.5 + inner_r - dist))
                    cr = int(rr * (1.0 - inner_cov) + fr * inner_cov + 0.5)
                    cg = int(rg * (1.0 - inner_cov) + fg * inner_cov + 0.5)
                    cb = int(rb_c * (1.0 - inner_cov) + fb * inner_cov + 0.5)
                    rb[pos] = cr
                    rb[pos + 1] = cg
                    rb[pos + 2] = cb
                    rb[pos + 3] = int(outer_cov * 255.0 + 0.5)
                pos += 4
            raw_rows.append(bytes(rb))
        b64 = _encode_rgba_png_b64(sz, sz, raw_rows)
        _HOVER_DOT_CACHE[key] = b64
    return tk.PhotoImage(data=b64, master=master)


def _simplify_sparkline_anchors(
    poly_pts: list[tuple[float, float]],
    *,
    min_dx: float = 4.0,
) -> list[tuple[float, float]]:
    """把长平台期整数电量折线简化为平滑过渡锚点（保留首尾，消除阶梯突变与过密尖刺）。"""
    n = len(poly_pts)
    if n <= 2:
        return list(poly_pts)
    raw_anchors: list[tuple[float, float]] = [poly_pts[0]]
    i = 0
    while i < n:
        y_val = poly_pts[i][1]
        j = i
        while j + 1 < n and abs(poly_pts[j + 1][1] - y_val) < 0.05:
            j += 1
        if i > 0 and j < n - 1:
            mid_x = 0.5 * (poly_pts[i][0] + poly_pts[j][0])
            raw_anchors.append((mid_x, y_val))
        i = j + 1
    raw_anchors.append(poly_pts[-1])

    # 按 min_dx 合并过密锚点（首尾严格保留），防止 7天/30天 窗口内 1px 陡跳引发奇异斜率
    if len(raw_anchors) <= 2:
        return raw_anchors
    merged: list[tuple[float, float]] = [raw_anchors[0]]
    for idx in range(1, len(raw_anchors) - 1):
        ax, ay = raw_anchors[idx]
        if ax - merged[-1][0] >= min_dx and raw_anchors[-1][0] - ax >= min_dx * 0.5:
            merged.append((ax, ay))
        elif len(merged) > 1:
            # 与前一个中间锚点融合成平滑均值
            px, py = merged[-1]
            merged[-1] = (0.5 * (px + ax), 0.5 * (py + ay))
    if raw_anchors[-1][0] - merged[-1][0] < 0.5 and len(merged) >= 2:
        merged[-1] = raw_anchors[-1]
    else:
        merged.append(raw_anchors[-1])
    return merged


def _monotone_cubic_eval(
    anchors: list[tuple[float, float]],
    w: int,
) -> tuple[list[Optional[float]], float, float]:
    """对锚点做 Fritsch-Carlson 单调三次样条插值（绝不过冲），返回每像素列 y 值及有效 x 范围。"""
    y_col: list[Optional[float]] = [None] * w
    m_pts = len(anchors)
    if m_pts < 2:
        return y_col, 0.0, 0.0

    dedup: list[tuple[float, float]] = [anchors[0]]
    for ax, ay in anchors[1:]:
        if ax - dedup[-1][0] >= 0.5:
            dedup.append((ax, ay))
        else:
            dedup[-1] = (dedup[-1][0], ay)
    if len(dedup) < 2:
        dedup = [anchors[0], (anchors[0][0] + 1.0, anchors[-1][1])]

    xs = [p[0] for p in dedup]
    ys = [p[1] for p in dedup]
    k = len(xs)
    dxs = [xs[i + 1] - xs[i] for i in range(k - 1)]
    dys = [ys[i + 1] - ys[i] for i in range(k - 1)]
    secs = [dys[i] / dxs[i] if dxs[i] > 1e-6 else 0.0 for i in range(k - 1)]

    tangents = [0.0] * k
    tangents[0] = secs[0]
    tangents[-1] = secs[-1]
    for i in range(1, k - 1):
        if secs[i - 1] * secs[i] <= 0.0:
            tangents[i] = 0.0
        else:
            tangents[i] = 0.5 * (secs[i - 1] + secs[i])
    for i in range(k - 1):
        if abs(secs[i]) < 1e-9:
            tangents[i] = 0.0
            tangents[i + 1] = 0.0
        else:
            a = tangents[i] / secs[i]
            b = tangents[i + 1] / secs[i]
            h2 = a * a + b * b
            if h2 > 9.0:
                tau = 3.0 / math.sqrt(h2)
                tangents[i] = tau * a * secs[i]
                tangents[i + 1] = tau * b * secs[i]

    seg_idx = 0
    x_start = max(0, int(math.floor(xs[0])))
    x_end = min(w - 1, int(math.ceil(xs[-1])))
    for x in range(x_start, x_end + 1):
        px = x + 0.5
        if px <= xs[0]:
            y_col[x] = ys[0]
            continue
        if px >= xs[-1]:
            y_col[x] = ys[-1]
            continue
        while seg_idx < k - 2 and px > xs[seg_idx + 1]:
            seg_idx += 1
        h = dxs[seg_idx]
        if h <= 1e-6:
            y_col[x] = ys[seg_idx]
            continue
        t = (px - xs[seg_idx]) / h
        t2 = t * t
        t3 = t2 * t
        h00 = 2.0 * t3 - 3.0 * t2 + 1.0
        h10 = t3 - 2.0 * t2 + t
        h01 = -2.0 * t3 + 3.0 * t2
        h11 = t3 - t2
        y_col[x] = (
            h00 * ys[seg_idx]
            + h10 * h * tangents[seg_idx]
            + h01 * ys[seg_idx + 1]
            + h11 * h * tangents[seg_idx + 1]
        )
    return y_col, xs[0], xs[-1]


def _make_aa_sparkline_photo(
    w: int,
    h: int,
    *,
    y_col: list[Optional[float]],
    x_min: float,
    x_max: float,
    accent_hex: str,
    bottom_y: float,
    lw: float = 2.0,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    """用真实线段 SDF 亚像素抗锯齿 + 纵向渐变透明面积填充渲染平滑电量趋势曲线 PNG。"""
    iw = max(8, int(w))
    ih = max(8, int(h))
    ar, ag, ab = hex_to_rgb(accent_hex)
    half_w = max(0.6, lw * 0.5)
    reach = half_w + 1.2
    x0_i = max(0, int(math.floor(x_min)) - 2)
    x1_i = min(iw - 1, int(math.ceil(x_max)) + 2)

    # 预计算每列局部纵向包络 [lo_y, hi_y]，保证零尖刺且跳过远距离像素
    col_segs: list[list[tuple[float, float, float, float]]] = [[] for _ in range(iw)]
    col_ymin = [0.0] * iw
    col_ymax = [-1.0] * iw
    for x in range(x0_i, x1_i + 1):
        segs: list[tuple[float, float, float, float]] = []
        lo_y = 1e9
        hi_y = -1e9
        for kx in range(max(0, x - 2), min(iw - 1, x + 2)):
            ya = y_col[kx]
            yb = y_col[kx + 1]
            if ya is not None and yb is not None:
                segs.append((kx + 0.5, ya, kx + 1.5, yb))
                lo_y = min(lo_y, ya, yb)
                hi_y = max(hi_y, ya, yb)
        col_segs[x] = segs
        if segs:
            col_ymin[x] = lo_y - reach
            col_ymax[x] = hi_y + reach

    raw_rows: list[bytes] = []
    for y in range(ih):
        py = y + 0.5
        rb = bytearray(1 + iw * 4)
        pos = 1
        for x in range(x0_i):
            pos += 4
        for x in range(x0_i, x1_i + 1):
            px = x + 0.5
            yc = y_col[x]
            stroke_a = 0.0
            if col_ymin[x] <= py <= col_ymax[x]:
                best_d = 1e9
                for sx0, sy0, sx1, sy1 in col_segs[x]:
                    d = _dist_segment(px, py, sx0, sy0, sx1, sy1)
                    if d < best_d:
                        best_d = d
                stroke_a = max(0.0, min(1.0, 0.5 + half_w - best_d))

            fill_a = 0.0
            if yc is not None and x_min - 0.2 <= px <= x_max + 0.2 and py >= yc - 0.5 and py <= bottom_y + 0.5:
                top_cov = max(0.0, min(1.0, py - (yc - 0.5)))
                bot_cov = max(0.0, min(1.0, (bottom_y + 0.5) - py))
                span_y = max(4.0, bottom_y - yc)
                grad = max(0.0, min(1.0, 1.0 - (py - yc) / span_y))
                fill_a = top_cov * bot_cov * (0.03 + 0.24 * grad)

            out_a = max(stroke_a, fill_a)
            if out_a > 0.004:
                rb[pos] = ar
                rb[pos + 1] = ag
                rb[pos + 2] = ab
                rb[pos + 3] = int(min(1.0, out_a) * 255.0 + 0.5)
            pos += 4
        raw_rows.append(bytes(rb))
    b64 = _encode_rgba_png_b64(iw, ih, raw_rows)
    return tk.PhotoImage(data=b64, master=master)


def _blend_aa_capsule_rows(
    rows: list[bytearray],
    tot_w: int,
    tot_h: int,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    radius: float,
    fill_rgb: tuple[int, int, int],
    *,
    stroke_only: bool = False,
    half_stroke: float = 0.75,
) -> None:
    """用 4x4 超采样 SDF 在 RGB 行缓冲中混合平滑抗锯齿圆角胶囊/圆环。"""
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    hw = max(0.5, (x1 - x0) * 0.5)
    hh = max(0.5, (y1 - y0) * 0.5)
    rad = max(0.5, min(radius, hw, hh))
    fr, fg, fb = fill_rgb

    bx0 = max(0, int(math.floor(x0 - 2.0)))
    bx1 = min(tot_w, int(math.ceil(x1 + 2.0)))
    by0 = max(0, int(math.floor(y0 - 2.0)))
    by1 = min(tot_h, int(math.ceil(y1 + 2.0)))

    for y in range(by0, by1):
        rb = rows[y]
        py = y + 0.5
        for x in range(bx0, bx1):
            px = x + 0.5
            d = _sdf_rounded_box(px, py, cx, cy, hw, hh, rad)
            if stroke_only:
                d_s = abs(d) - half_stroke
                if d_s >= 1.1:
                    continue
                if d_s <= -1.1:
                    cov = 1.0
                else:
                    hits = 0
                    for oy in _SS_OFFSETS:
                        for ox in _SS_OFFSETS:
                            ds = abs(_sdf_rounded_box(x + ox, y + oy, cx, cy, hw, hh, rad)) - half_stroke
                            if ds <= 0.0:
                                hits += 1
                    cov = hits / 16.0
            else:
                if d >= 1.1:
                    continue
                if d <= -1.1:
                    cov = 1.0
                else:
                    hits = 0
                    for oy in _SS_OFFSETS:
                        for ox in _SS_OFFSETS:
                            if _sdf_rounded_box(x + ox, y + oy, cx, cy, hw, hh, rad) <= 0.0:
                                hits += 1
                    cov = hits / 16.0
            if cov <= 0.0:
                continue
            idx = x * 3
            if cov >= 0.999:
                rb[idx] = fr
                rb[idx + 1] = fg
                rb[idx + 2] = fb
            else:
                inv = 1.0 - cov
                rb[idx] = max(0, min(255, int(rb[idx] * inv + fr * cov + 0.5)))
                rb[idx + 1] = max(0, min(255, int(rb[idx + 1] * inv + fg * cov + 0.5)))
                rb[idx + 2] = max(0, min(255, int(rb[idx + 2] * inv + fb * cov + 0.5)))


def _make_pill_photo(
    w: int,
    h: int,
    fill_rgb: tuple[int, int, int],
    outer_rgb: tuple[int, int, int],
    *,
    pad_px: float = 1.0,
    border_rgb: Optional[tuple[int, int, int]] = None,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    w = max(8, int(w))
    h = max(8, int(h))
    key = (w, h, fill_rgb, outer_rgb, border_rgb, round(pad_px, 2))
    data = _PILL_PHOTO_CACHE.get(key)
    if data is None:
        if len(_PILL_PHOTO_CACHE) > 256:
            _PILL_PHOTO_CACHE.clear()
        base_row = bytes(outer_rgb) * w
        rows = [bytearray(base_row) for _ in range(h)]
        r = max(1.5, (min(w, h) - pad_px * 2.0) * 0.5)
        _blend_aa_capsule_rows(
            rows, w, h, pad_px, pad_px, w - pad_px, h - pad_px, r, fill_rgb
        )
        if border_rgb is not None:
            _blend_aa_capsule_rows(
                rows,
                w,
                h,
                pad_px + 0.35,
                pad_px + 0.35,
                w - pad_px - 0.35,
                h - pad_px - 0.35,
                max(1.2, r - 0.35),
                border_rgb,
                stroke_only=True,
                half_stroke=0.48,
            )
        data = f"P6\n{w} {h}\n255\n".encode("ascii") + b"".join(bytes(rw) for rw in rows)
        _PILL_PHOTO_CACHE[key] = data
    return tk.PhotoImage(data=data, format="PPM", master=master)


def _render_toggle_photo(
    tw: int,
    th: int,
    pad: int,
    knob_d: int,
    pos: float,
    focused: bool,
    track_rows: list[bytes],
    *,
    cache_key: Optional[tuple] = None,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    if cache_key is not None and cache_key in _TOGGLE_PHOTO_CACHE:
        return tk.PhotoImage(data=_TOGGLE_PHOTO_CACHE[cache_key], format="PPM", master=master)
    tot_w = tw + pad * 2
    tot_h = th + pad * 2
    rows = [bytearray(r) for r in track_rows]

    t_pos = max(0.0, min(1.0, float(pos)))
    t_col = t_pos * t_pos * (3.0 - 2.0 * t_pos)
    r = knob_d * 0.5
    cx = pad + 4.0 + (tw - 8.0 - knob_d) * t_pos + r
    cy = tot_h * 0.5

    # 平滑混合旋钮底色、高光色与轨道柔影色
    knob_rgb = (
        BASE_RGB[0] + (BLURPLE_RGB[0] - BASE_RGB[0]) * t_col,
        BASE_RGB[1] + (BLURPLE_RGB[1] - BASE_RGB[1]) * t_col,
        BASE_RGB[2] + (BLURPLE_RGB[2] - BASE_RGB[2]) * t_col,
    )
    hi_on = hex_to_rgb(TOGGLE_KNOB_HI_ON)
    sh_on = hex_to_rgb(TOGGLE_KNOB_SH_ON)
    hi_rgb = (
        HIGHLIGHT_RGB[0] + (hi_on[0] - HIGHLIGHT_RGB[0]) * t_col,
        HIGHLIGHT_RGB[1] + (hi_on[1] - HIGHLIGHT_RGB[1]) * t_col,
        HIGHLIGHT_RGB[2] + (hi_on[2] - HIGHLIGHT_RGB[2]) * t_col,
    )
    sh_rgb = (
        SHADOW_RGB[0] + (sh_on[0] - SHADOW_RGB[0]) * t_col,
        SHADOW_RGB[1] + (sh_on[1] - SHADOW_RGB[1]) * t_col,
        SHADOW_RGB[2] + (sh_on[2] - SHADOW_RGB[2]) * t_col,
    )

    sh_dx, sh_dy = 1.3, 1.7
    sh_spread = 3.0
    bx0 = max(0, int(math.floor(cx - r - sh_spread - 2)))
    bx1 = min(tot_w, int(math.ceil(cx + r + sh_spread + 3)))
    by0 = max(0, int(math.floor(cy - r - sh_spread - 2)))
    by1 = min(tot_h, int(math.ceil(cy + r + sh_spread + 3)))

    kr, kg, kb = knob_rgb
    hr, hg, hb = hi_rgb
    sr, sg, sb = sh_rgb
    r_sh = r + sh_spread

    for y in range(by0, by1):
        rb = rows[y]
        py = y + 0.5
        for x in range(bx0, bx1):
            px = x + 0.5
            idx = x * 3
            br0, bg0, bb0 = float(rb[idx]), float(rb[idx + 1]), float(rb[idx + 2])

            # 1) 旋钮软阴影（平滑羽化，消除月牙色带锯齿）
            d_sh = math.hypot(px - (cx + sh_dx), py - (cy + sh_dy))
            if d_sh < r_sh:
                t_s = max(0.0, min(1.0, (r_sh - d_sh) / (sh_spread + 1.4)))
                al_s = (t_s * t_s * (3.0 - 2.0 * t_s)) * 0.56
                br0 = br0 * (1.0 - al_s) + sr * al_s
                bg0 = bg0 * (1.0 - al_s) + sg * al_s
                bb0 = bb0 * (1.0 - al_s) + sb * al_s

            # 2) 旋钮圆形本体 4x4 超采样抗锯齿 + 柔和高光球面感
            dx = px - cx
            dy = py - cy
            dist = math.hypot(dx, dy)
            if dist <= r - 1.1:
                cov = 1.0
            elif dist >= r + 1.1:
                cov = 0.0
            else:
                hits = 0
                for oy in _SS_OFFSETS:
                    sy = y + oy - cy
                    for ox in _SS_OFFSETS:
                        sx_v = x + ox - cx
                        if sx_v * sx_v + sy * sy <= r * r:
                            hits += 1
                cov = hits / 16.0

            if cov > 0.0:
                nx = dx / max(1.0, r)
                ny = dy / max(1.0, r)
                lit = max(0.0, min(1.0, (-nx - ny) * 0.28 + 0.16)) * 0.22
                shade = max(0.0, min(1.0, (nx + ny) * 0.24)) * 0.10
                fr = kr * (1.0 - lit - shade) + hr * lit + sr * shade
                fg = kg * (1.0 - lit - shade) + hg * lit + sg * shade
                fb = kb * (1.0 - lit - shade) + hb * lit + sb * shade
                br0 = br0 * (1.0 - cov) + fr * cov
                bg0 = bg0 * (1.0 - cov) + fg * cov
                bb0 = bb0 * (1.0 - cov) + fb * cov

            rb[idx] = max(0, min(255, int(br0 + 0.5)))
            rb[idx + 1] = max(0, min(255, int(bg0 + 0.5)))
            rb[idx + 2] = max(0, min(255, int(bb0 + 0.5)))

    if focused:
        f_r = (tot_h - 3.0) * 0.5
        _blend_aa_capsule_rows(
            rows, tot_w, tot_h, 1.5, 1.5, tot_w - 1.5, tot_h - 1.5, f_r, BLURPLE_RGB,
            stroke_only=True, half_stroke=0.75,
        )

    data = f"P6\n{tot_w} {tot_h}\n255\n".encode("ascii") + b"".join(bytes(rw) for rw in rows)
    if cache_key is not None:
        if len(_TOGGLE_PHOTO_CACHE) > 64:
            _TOGGLE_PHOTO_CACHE.clear()
        _TOGGLE_PHOTO_CACHE[cache_key] = data
    return tk.PhotoImage(data=data, format="PPM", master=master)


def make_surface_photo(
    box_w: int,
    box_h: int,
    *,
    radius: int = 24,
    off: int = 7,
    box_r: int = 4,
    pad: int = 19,
    inset: bool = False,
    shadow_rgb: Optional[tuple[int, int, int]] = None,
    light_rgb: Optional[tuple[int, int, int]] = None,
    fill_rgb: Optional[tuple[int, int, int]] = None,
    outer_rgb: Optional[tuple[int, int, int]] = None,
    shadow_strength: float = 0.96,
    light_strength: float = 1.0,
    glow_rgb: Optional[tuple[int, int, int]] = None,
    glow_strength: float = 0.0,
) -> tk.PhotoImage:
    return _CACHE.render_photo(
        box_w,
        box_h,
        radius=radius,
        off=off,
        box_r=box_r,
        pad=pad,
        inset=inset,
        shadow_rgb=shadow_rgb,
        light_rgb=light_rgb,
        fill_rgb=fill_rgb,
        outer_rgb=outer_rgb,
        shadow_strength=shadow_strength,
        light_strength=light_strength,
        glow_rgb=glow_rgb,
        glow_strength=glow_strength,
    )


class NeuRoleBadge(tk.Canvas):
    """柔和同色系新拟物设备徽章：凹陷圆槽 + 细线设备几何图标。"""

    def __init__(
        self,
        parent: tk.Widget,
        role: str,
        *,
        size: Optional[int] = None,
        color: Optional[str] = None,
        inset: bool = True,
        surface_bg: Optional[str] = None,
    ) -> None:
        eff_color = TEXT_MUTED if color is None else color
        eff_surface = BASE if surface_bg is None else surface_bg
        sz = size if size is not None else sx(36, parent, max_scale=1.5)
        super().__init__(parent, width=sz, height=sz, bg=eff_surface, bd=0, highlightthickness=0, takefocus=0)
        self._pad = 0 if inset else 4
        self._inner = sz - self._pad * 2
        self._sz = sz
        self._role = role
        self._color = eff_color
        self._inset = inset
        self._surface_bg = eff_surface
        self._bg_photo = self._build_bg_photo(eff_surface)
        self.create_image(0, 0, anchor="nw", image=self._bg_photo)
        draw_vector_role_glyph(self, sz / 2.0, sz / 2.0, role, color=eff_color, scale=sz / 38.0)

    def _build_bg_photo(self, surface_bg: str) -> tk.PhotoImage:
        rgb = hex_to_rgb(surface_bg)
        return make_surface_photo(
            self._inner,
            self._inner,
            radius=self._inner // 2,
            off=3,
            box_r=2,
            pad=self._pad,
            inset=self._inset,
            fill_rgb=rgb,
            outer_rgb=rgb,
            shadow_strength=0.78,
            light_strength=0.88,
        )

    def set_style(
        self,
        role: Optional[str] = None,
        color: Optional[str] = None,
        surface_bg: Optional[str] = None,
    ) -> None:
        nr = role if role is not None else self._role
        nc = color if color is not None else self._color
        nb = surface_bg if surface_bg is not None else self._surface_bg
        if nr == self._role and nc == self._color and nb == self._surface_bg:
            return
        if nb != self._surface_bg:
            self._surface_bg = nb
            super().configure(bg=nb)
            self._bg_photo = self._build_bg_photo(nb)
        self._role = nr
        self._color = nc
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._bg_photo)
        draw_vector_role_glyph(self, self._sz / 2.0, self._sz / 2.0, nr, color=nc, scale=self._sz / 38.0)


def _draw_pill_icon(
    canvas: tk.Canvas, cx: float, cy: float, kind: str, color: str, scale: float = 1.0
) -> None:
    """绘制胶囊内自绘单色矢量小图标（充电闪电 / 充满对勾），彻底避免 emoji 彩色回退与基线抖动。"""
    s = max(0.75, scale)
    if kind == "charge":
        pts = (
            cx + 1.2 * s, cy - 4.4 * s,
            cx - 2.6 * s, cy + 0.5 * s,
            cx + 0.2 * s, cy + 0.5 * s,
            cx - 1.2 * s, cy + 4.4 * s,
            cx + 2.8 * s, cy - 0.6 * s,
            cx - 0.1 * s, cy - 0.6 * s,
        )
        canvas.create_polygon(*pts, fill=color, outline="")
    elif kind == "check":
        lw = max(1.5, 1.65 * s)
        canvas.create_line(
            cx - 3.6 * s, cy + 0.2 * s,
            cx - 0.9 * s, cy + 3.0 * s,
            cx + 3.8 * s, cy - 2.8 * s,
            fill=color, width=lw, capstyle="round", joinstyle="round",
        )


class NeuStatusPill(tk.Canvas):
    """抗锯齿圆角状态胶囊标签（支持矢量前置图标、最小占位宽防抖与可选细描边）。"""

    def __init__(
        self,
        parent: tk.Widget,
        text: str = "",
        *,
        bg_color: Optional[str] = None,
        fg_color: Optional[str] = None,
        surface_bg: Optional[str] = None,
        border_color: Optional[str] = None,
        icon_kind: str = "",
        min_width_dp: int = 0,
        font_size: int = FONT_CAPTION,
    ) -> None:
        self._text = text
        self._pill_bg = BASE if bg_color is None else bg_color
        self._pill_fg = TEXT_MUTED if fg_color is None else fg_color
        self._surface_bg = BASE if surface_bg is None else surface_bg
        self._border_color = border_color
        self._icon_kind = icon_kind
        self._min_width_dp = max(0, int(min_width_dp))
        self._font = ui_font(font_size, "bold", root=parent)
        self._font_obj = tkfont.Font(root=parent, font=self._font)
        self._pad_x = sx(9, parent)
        self._pad_y = sx(3, parent)
        self._icon_slot = sx(11, parent) if icon_kind else 0
        self._icon_gap = sx(3, parent) if icon_kind else 0
        super().__init__(parent, width=8, height=8, bg=self._surface_bg, bd=0, highlightthickness=0, takefocus=0)
        w, h = self._measure()
        super().configure(width=w, height=h)
        self._pill_w = w
        self._pill_h = h
        self._redraw()

    def _measure(self) -> tuple[int, int]:
        txt = self._text or ""
        tw = self._font_obj.measure(txt) if txt else 0
        icon_extra = (self._icon_slot + (self._icon_gap if txt else 0)) if self._icon_kind else 0
        th = self._font_obj.metrics("linespace")
        min_px = sx(max(24, self._min_width_dp), self) if (txt or self._icon_kind) else sx(24, self)
        return max(min_px, tw + icon_extra + self._pad_x * 2), th + self._pad_y * 2

    def set_pill(
        self,
        text: str,
        *,
        bg_color: str,
        fg_color: str,
        surface_bg: Optional[str] = None,
        border_color: Optional[str] = None,
        icon_kind: Optional[str] = None,
    ) -> None:
        sb = surface_bg if surface_bg is not None else self._surface_bg
        ik = self._icon_kind if icon_kind is None else icon_kind
        if (
            text == self._text
            and bg_color == self._pill_bg
            and fg_color == self._pill_fg
            and sb == self._surface_bg
            and border_color == self._border_color
            and ik == self._icon_kind
        ):
            return
        self._text = text
        self._pill_bg = bg_color
        self._pill_fg = fg_color
        self._surface_bg = sb
        self._border_color = border_color
        self._icon_kind = ik
        self._icon_slot = sx(11, self) if ik else 0
        self._icon_gap = sx(3, self) if ik else 0
        super().configure(bg=sb)
        w, h = self._measure()
        if w != self._pill_w or h != self._pill_h:
            self._pill_w, self._pill_h = w, h
            super().configure(width=w, height=h)
        self._redraw()

    def cget(self, key: str):  # type: ignore[override]
        if key == "text":
            return self._text
        return super().cget(key)

    def _redraw(self) -> None:
        self.delete("all")
        if not self._text and not self._icon_kind:
            return
        w, h = self._pill_w, self._pill_h
        b_rgb = hex_to_rgb(self._border_color) if self._border_color else None
        self._pill_photo = _make_pill_photo(
            w, h, hex_to_rgb(self._pill_bg), hex_to_rgb(self._surface_bg),
            pad_px=1.0, border_rgb=b_rgb, master=self,
        )
        self.create_image(0, 0, anchor="nw", image=self._pill_photo)
        if self._icon_kind:
            tw = self._font_obj.measure(self._text) if self._text else 0
            total_w = self._icon_slot + (self._icon_gap + tw if self._text else 0)
            start_x = (w - total_w) / 2.0
            _draw_pill_icon(
                self, start_x + self._icon_slot / 2.0, h / 2.0, self._icon_kind,
                self._pill_fg, scale=max(0.85, dpi_scale(self) * 0.9),
            )
            if self._text:
                self.create_text(
                    start_x + self._icon_slot + self._icon_gap + tw / 2.0,
                    h / 2.0, text=self._text, fill=self._pill_fg, font=self._font,
                )
        else:
            self.create_text(w / 2.0, h / 2.0, text=self._text, fill=self._pill_fg, font=self._font)


def draw_vector_role_glyph(
    canvas: tk.Canvas,
    cx: float,
    cy: float,
    role: str,
    color: Optional[str] = None,
    scale: float = 1.0,
) -> None:
    """在柔和圆槽内用平滑线框绘制设备几何轮廓，保持同光源柔雅统一。"""
    if color is None:
        color = TEXT_MUTED
    s = max(0.8, scale)
    w = max(1.6, 1.8 * s)
    if role == "mouse":
        canvas.create_oval(cx - 5.5 * s, cy - 8 * s, cx + 5.5 * s, cy + 8 * s, outline=color, width=w)
        canvas.create_line(cx, cy - 5.5 * s, cx, cy - 1.5 * s, fill=color, width=w, capstyle="round")
    elif role == "keyboard":
        canvas.create_rectangle(cx - 8 * s, cy - 5 * s, cx + 8 * s, cy + 5 * s, outline=color, width=w)
        for dx in (-4.5, 0.0, 4.5):
            canvas.create_line(
                cx + (dx - 1) * s, cy - 1.5 * s, cx + (dx + 1) * s, cy - 1.5 * s,
                fill=color, width=1.6 * s, capstyle="round"
            )
        canvas.create_line(cx - 4 * s, cy + 2 * s, cx + 4 * s, cy + 2 * s, fill=color, width=1.6 * s, capstyle="round")
    elif role == "headset":
        canvas.create_arc(cx - 7 * s, cy - 7 * s, cx + 7 * s, cy + 7 * s, start=0, extent=180, style="arc", outline=color, width=w)
        canvas.create_rectangle(cx - 8 * s, cy - 1 * s, cx - 5 * s, cy + 5.5 * s, outline=color, width=1.6 * s)
        canvas.create_rectangle(cx + 5 * s, cy - 1 * s, cx + 8 * s, cy + 5.5 * s, outline=color, width=1.6 * s)
    elif role == "controller":
        canvas.create_oval(cx - 8 * s, cy - 4.5 * s, cx + 8 * s, cy + 4.5 * s, outline=color, width=w)
        canvas.create_line(cx - 5 * s, cy, cx - 2 * s, cy, fill=color, width=1.6 * s, capstyle="round")
        canvas.create_line(cx - 3.5 * s, cy - 1.5 * s, cx - 3.5 * s, cy + 1.5 * s, fill=color, width=1.6 * s, capstyle="round")
        canvas.create_oval(cx + 2.5 * s, cy - 2 * s, cx + 4.5 * s, cy, fill=color, outline="")
        canvas.create_oval(cx + 4.5 * s, cy, cx + 6.5 * s, cy + 2 * s, fill=color, outline="")
    else:
        canvas.create_rectangle(cx - 6.5 * s, cy - 4.5 * s, cx + 5.5 * s, cy + 4.5 * s, outline=color, width=w)
        canvas.create_rectangle(cx + 5.5 * s, cy - 2 * s, cx + 7.5 * s, cy + 2 * s, fill=color, outline="")


# ---- 核心新拟物组件 ----

class NeuCard(tk.Frame):
    """新拟物派圆角容器（支持 Soft Card 凸起与 Recessed Well 凹陷，9-slice 自适应拉伸）。"""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        radius: int = 24,
        pad: int = 18,
        inner_pad: tuple[int, int] = (20, 18),
        off: int = 7,
        box_r: int = 4,
        inset: bool = False,
        hoverable: bool = False,
        shadow_rgb: Optional[tuple[int, int, int]] = None,
        outer_bg: Optional[str] = None,
    ) -> None:
        eff_outer = BASE if outer_bg is None else outer_bg
        eff_shadow = SHADOW_RGB if shadow_rgb is None else shadow_rgb
        super().__init__(parent, bg=eff_outer, bd=0, highlightthickness=0, takefocus=0)
        self._radius = radius
        self._pad = 0 if inset else pad
        self._off = off
        self._box_r = box_r
        self._inset = inset
        self._hoverable = hoverable and (not inset)
        self._shadow_rgb = eff_shadow
        self._fill_hex = BASE
        self._fill_rgb = BASE_RGB
        self._outer_bg = eff_outer
        self._outer_rgb = hex_to_rgb(eff_outer)
        self._muted = False
        self._hovered = False
        self._focused = False
        self._hover_leave_aid: Optional[str] = None
        self._size_cache: tuple[int, int, bool, bool, tuple[int, int, int], tuple[int, int, int], bool] = (
            -1, -1, False, False, BASE_RGB, BASE_RGB, False
        )
        self._photo: Optional[tk.PhotoImage] = None

        self._bg_label = tk.Label(self, bg=eff_outer, bd=0, highlightthickness=0, takefocus=0)
        self._bg_label.place(x=0, y=0, relwidth=1.0, relheight=1.0)

        px = self._pad + inner_pad[0]
        py = self._pad + inner_pad[1]
        self.content = tk.Frame(self, bg=BASE, bd=0, highlightthickness=0)
        self.content.pack(fill="both", expand=True, padx=px, pady=py)
        try:
            self._bg_label.lower()
            self.content.tkraise()
        except Exception:
            pass

        self.bind("<Configure>", self._on_configure)
        self.bind("<Destroy>", self._on_card_destroy, add="+")
        if self._hoverable:
            self.sync_hover_tree()

    def _on_card_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._hover_leave_aid is not None:
            try:
                self.after_cancel(self._hover_leave_aid)
            except Exception:
                pass
            self._hover_leave_aid = None

    def _bind_hover_tree(self, widget: tk.Widget) -> None:
        if getattr(widget, "_neu_card_hover_owner", None) is self:
            return
        try:
            setattr(widget, "_neu_card_hover_owner", self)
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
        except Exception:
            pass

    def sync_hover_tree(self) -> None:
        """递归为卡片及全部子控件绑定悬停感知，确保指在任何内容区都稳定触发外圈发亮。"""
        if not self._hoverable:
            return
        stack: list[tk.Widget] = [self]
        while stack:
            cur = stack.pop()
            self._bind_hover_tree(cur)
            try:
                stack.extend(cur.winfo_children())
            except Exception:
                pass

    def register_child_hover(self, widget: tk.Widget) -> None:
        if self._hoverable:
            self._bind_hover_tree(widget)

    def _is_pointer_inside(self) -> bool:
        try:
            px, py = self.winfo_pointerxy()
            w = self.winfo_containing(px, py)
            while w is not None:
                if w is self:
                    return True
                w = w.master
        except Exception:
            pass
        return False

    def _verify_hover_now(self) -> None:
        self._hover_leave_aid = None
        if not self.winfo_exists():
            return
        if self._hovered and not self._is_pointer_inside():
            self._hovered = False
            self._redraw()

    def set_focus_glow(self, focused: bool) -> None:
        if self._focused != focused:
            self._focused = focused
            self._redraw()

    def set_outer_bg(self, outer_hex: str) -> None:
        rgb = hex_to_rgb(outer_hex)
        if self._outer_bg != outer_hex:
            self._outer_bg = outer_hex
            self._outer_rgb = rgb
            super().configure(bg=outer_hex)
            self._bg_label.configure(bg=outer_hex)
            self._redraw()

    def set_muted(self, muted: bool, fill_hex: Optional[str] = None) -> None:
        """置灰/恢复卡片材质底色与阴影强度（离线设备卡片自动置灰）。"""
        target_hex = fill_hex or (MUTED_CARD_BG if muted else BASE)
        target_rgb = hex_to_rgb(target_hex)
        if self._muted != muted or self._fill_rgb != target_rgb:
            self._muted = muted
            self._fill_hex = target_hex
            self._fill_rgb = target_rgb
            self.content.configure(bg=target_hex)
            self._redraw()

    def _on_enter(self, _e=None) -> None:
        if self._hover_leave_aid is not None:
            try:
                self.after_cancel(self._hover_leave_aid)
            except Exception:
                pass
            self._hover_leave_aid = None
        # 立即清理同容器内其他仍处于悬停态的兄弟卡片，确保仅当前指向模块发亮
        try:
            for sib in self.master.winfo_children():
                if sib is not self and isinstance(sib, NeuCard) and sib._hovered:
                    sib._hovered = False
                    sib._redraw()
        except Exception:
            pass
        if not self._hovered:
            self._hovered = True
            self._redraw()

    def _on_leave(self, _e=None) -> None:
        if not self._is_pointer_inside():
            if self._hovered:
                self._hovered = False
                self._redraw()
            return
        if self._hovered and self._hover_leave_aid is None:
            try:
                self._hover_leave_aid = self.after(40, self._verify_hover_now)
            except Exception:
                pass

    def _on_configure(self, event) -> None:
        if event.widget is self:
            self._redraw(event.width, event.height)

    def _redraw(self, w: Optional[int] = None, h: Optional[int] = None) -> None:
        cw = w if w is not None else self.winfo_width()
        ch = h if h is not None else self.winfo_height()
        if cw < 24 or ch < 24:
            return
        state = (cw, ch, self._hovered, self._focused, self._fill_rgb, self._outer_rgb, self._muted)
        if state == self._size_cache:
            return
        self._size_cache = state

        box_w = max(8, cw - self._pad * 2)
        box_h = max(8, ch - self._pad * 2)
        s_mult = 0.55 if self._muted else 1.0
        if self._inset:
            s_rgb = FOCUS_SHADOW_RGB if self._focused else self._shadow_rgb
            s_str = (0.85 if self._focused else 0.82) * s_mult
            img = make_surface_photo(
                box_w,
                box_h,
                radius=self._radius,
                off=self._off,
                box_r=self._box_r,
                pad=self._pad,
                inset=True,
                fill_rgb=self._fill_rgb,
                outer_rgb=self._outer_rgb,
                shadow_rgb=s_rgb,
                shadow_strength=s_str,
                light_strength=0.88 * s_mult,
            )
        elif self._hovered:
            img = make_surface_photo(
                box_w,
                box_h,
                radius=self._radius,
                off=max(2, self._off // 2),
                box_r=self._box_r,
                pad=self._pad,
                inset=False,
                fill_rgb=self._fill_rgb,
                outer_rgb=self._outer_rgb,
                shadow_rgb=self._shadow_rgb,
                shadow_strength=0.14,
                light_strength=1.0,
                glow_rgb=CARD_GLOW_RGB,
                glow_strength=0.86 if self._muted else 1.0,
            )
        else:
            # 可悬停模块未指向时保持安静柔和立体感，四周不发光；指向时才周围发亮
            idle_light = (0.16 * s_mult) if self._hoverable else (0.85 * s_mult)
            idle_shadow = (0.72 * s_mult) if self._hoverable else (0.95 * s_mult)
            img = make_surface_photo(
                box_w,
                box_h,
                radius=self._radius,
                off=max(3, self._off - 2) if self._muted else self._off,
                box_r=self._box_r,
                pad=self._pad,
                inset=False,
                fill_rgb=self._fill_rgb,
                outer_rgb=self._outer_rgb,
                shadow_rgb=self._shadow_rgb,
                shadow_strength=idle_shadow,
                light_strength=idle_light,
            )
        self._photo = img
        self._bg_label.configure(image=self._photo)
        try:
            self._bg_label.lower()
        except Exception:
            pass


class NeuButton(tk.Canvas):
    """新拟物 Raised Button：悬停渐亮、显著按压位移与回弹、焦点保留、禁用去饱和及矢量旋弧。"""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        text: str = "",
        command: Optional[Callable[[], Any]] = None,
        accent: bool = False,
        danger_fill: bool = False,
        dot_color: Optional[str] = None,
        radius: int = 16,
        pad: int = 11,
        font_size: int = FONT_BODY,
        weight: str = "normal",
        padx: int = 16,
        pady: int = 8,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fg: Optional[str] = None,
        tooltip: str = "",
        surface_bg: Optional[str] = None,
        inset_active: bool = False,
    ) -> None:
        eff_surface = BASE if surface_bg is None else surface_bg
        self._text = text
        self._command = command
        self._accent = accent
        self._danger_fill = bool(danger_fill)
        self._dot_color = dot_color or (BLURPLE_BRAND if (accent and not danger_fill) else None)
        self._radius = max(4, sx(radius, parent))
        self._pad = max(3, sx(pad, parent))
        self._font_size = font_size
        self._weight = weight
        self._font = ui_font(font_size, weight=weight, root=parent)
        self._padx = sx(padx, parent)
        self._pady = sx(pady, parent)
        self._custom_fg = fg
        self._surface_bg = eff_surface
        self._inset_active = inset_active
        self._state = "normal"
        self._hovered = False
        self._hover_prog = 0.0
        self._hover_aid: Optional[str] = None
        self._pressed = False
        self._press_prog = 0.0
        self._rebound_aid: Optional[str] = None
        self._key_aid: Optional[str] = None
        self._focused = False
        self._loading = False
        self._loading_lock_w: Optional[int] = None
        self._spin_step = 0
        self._spin_id: Optional[str] = None
        self._photo: Optional[tk.PhotoImage] = None
        self._last_key: tuple = ()

        self._min_w = sx(width, parent) if width else None
        self._min_h = sx(height, parent) if height else None
        super().__init__(
            parent,
            width=120,
            height=48,
            bg=eff_surface,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=1,
        )
        bw, bh = self._measure_box(self._min_w, self._min_h)
        self._box_w = bw
        self._box_h = bh
        super().configure(width=bw + self._pad * 2, height=bh + self._pad * 2)

        self.bind("<Configure>", self._on_configure)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<KeyPress-Return>", self._on_key_activate)
        self.bind("<KeyPress-space>", self._on_key_activate)
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._tooltip_obj: Optional[NeuTooltip] = attach_tooltip(self, tooltip) if tooltip else None
        _ensure_focus_hooks(self)

        self._render()

    def _cancel_anims(self) -> None:
        for attr in ("_hover_aid", "_rebound_aid", "_key_aid", "_spin_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        self._cancel_anims()

    def set_disabled(self, disabled: bool) -> None:
        """统一切换按钮启用/禁用态（自动收敛 hover 与焦点高亮）。"""
        self.configure(state="disabled" if bool(disabled) else "normal")

    def _measure_text_px(self) -> tuple[int, int]:
        txt = self._text or "按钮"
        try:
            tid = self.create_text(0, 0, text=txt, font=self._font)
            bx = self.bbox(tid)
            self.delete(tid)
            if bx is not None:
                return int(bx[2] - bx[0]), int(bx[3] - bx[1])
        except Exception:
            pass
        return len(txt) * 14, 18

    def _measure_box(self, min_w: Optional[int] = None, min_h: Optional[int] = None) -> tuple[int, int]:
        tw, th = self._measure_text_px()
        dot_extra = sx(15, self) if (self._dot_color or self._loading) else 0
        eff_min_w = max(min_w or 0, self._loading_lock_w or 0)
        w = max(eff_min_w, tw + dot_extra + self._padx * 2 + sx(4, self))
        h = max(min_h or 0, th + self._pady * 2 + sx(2, self))
        return int(w), int(h)

    def set_surface_bg(self, surface_bg: str) -> None:
        if surface_bg != self._surface_bg:
            self._surface_bg = surface_bg
            super().configure(bg=surface_bg)
            self._render(force=True)

    def set_loading(self, loading: bool, text: Optional[str] = None) -> None:
        """开启/关闭内置 8 帧旋转弧线加载态，保持原有宽度防止工具栏位移。"""
        loading = bool(loading)
        if loading and not self._loading:
            self._loading_lock_w = self._box_w
        elif not loading:
            self._loading_lock_w = None
        if text is not None:
            self._text = text
        if loading == self._loading:
            if text is not None:
                self.configure(text=text)
            return
        self._loading = loading
        self._state = "disabled" if loading else "normal"
        super().configure(cursor="arrow" if loading else "hand2")
        if self._spin_id is not None:
            try:
                self.after_cancel(self._spin_id)
            except Exception:
                pass
            self._spin_id = None
        if loading:
            self._spin_step = 0
            self._tick_spinner()
        else:
            self._resize_and_render()

    def _tick_spinner(self) -> None:
        if not self._loading or not self.winfo_exists():
            self._spin_id = None
            return
        self._spin_step = (self._spin_step + 1) % 8
        self._resize_and_render()
        self._spin_id = self.after(90, self._tick_spinner)

    def _resize_and_render(self) -> None:
        bw, bh = self._measure_box(self._min_w, self._min_h)
        if bw != self._box_w or bh != self._box_h:
            self._box_w = bw
            self._box_h = bh
            super().configure(width=self._box_w + self._pad * 2, height=self._box_h + self._pad * 2)
        self._render(force=True)

    def configure(self, cnf=None, **kw):  # type: ignore[override]
        need_render = False
        if "text" in kw:
            self._text = kw.pop("text")
            need_render = True
        if "state" in kw:
            self._state = str(kw.pop("state"))
            super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            need_render = True
        if "dot_color" in kw:
            self._dot_color = kw.pop("dot_color")
            need_render = True
        if "fg" in kw:
            self._custom_fg = kw.pop("fg")
            need_render = True
        if "inset_active" in kw:
            self._inset_active = bool(kw.pop("inset_active"))
            need_render = True
        if "command" in kw:
            self._command = kw.pop("command")
        if kw:
            super().configure(cnf, **kw) if cnf else super().configure(**kw)
        if need_render:
            self._resize_and_render()

    config = configure

    def _on_configure(self, event) -> None:
        self._box_w = max(24, event.width - self._pad * 2)
        self._box_h = max(20, event.height - self._pad * 2)
        self._render()

    def _animate_hover(self, target: float) -> None:
        if self._hover_aid is not None:
            try:
                self.after_cancel(self._hover_aid)
            except Exception:
                pass
            self._hover_aid = None
        if not self.winfo_exists():
            return
        if abs(self._hover_prog - target) < 0.05:
            self._hover_prog = target
            self._render()
            return
        step = 0.34 if target > self._hover_prog else -0.34
        self._hover_prog = max(0.0, min(1.0, self._hover_prog + step))
        self._render()
        if abs(self._hover_prog - target) >= 0.05 and self.winfo_exists():
            try:
                self._hover_aid = self.after(30, lambda: self._animate_hover(target))
            except Exception:
                self._hover_aid = None

    def _animate_rebound(self) -> None:
        if self._rebound_aid is not None:
            try:
                self.after_cancel(self._rebound_aid)
            except Exception:
                pass
            self._rebound_aid = None
        if not self.winfo_exists():
            return
        if self._pressed or self._press_prog <= 0.05:
            if not self._pressed:
                self._press_prog = 0.0
                self._render()
            return
        self._press_prog = max(0.0, self._press_prog - 0.5)
        self._render()
        if self._press_prog > 0.05 and self.winfo_exists():
            try:
                self._rebound_aid = self.after(38, self._animate_rebound)
            except Exception:
                self._rebound_aid = None

    def _on_enter(self, _e=None) -> None:
        if self._state == "disabled":
            return
        self._hovered = True
        self._animate_hover(1.0)

    def _on_leave(self, _e=None) -> None:
        self._hovered = False
        self._pressed = False
        self._press_prog = 0.0
        self._animate_hover(0.0)

    def _on_press(self, _e=None) -> None:
        if self._state == "disabled":
            return
        _mark_pointer_focus()
        self._focused = False
        self._pressed = True
        self._press_prog = 1.0
        self._render()

    def _on_release(self, event) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._focused = False
        self._animate_rebound()
        if was_pressed and self._state != "disabled":
            w = self.winfo_width()
            h = self.winfo_height()
            if 0 <= event.x <= w and 0 <= event.y <= h and self._command:
                _safe_invoke(self._command, self._text or "按钮")

    def _on_focus_in(self, _e=None) -> None:
        self._focused = bool(_KB_FOCUS_ACTIVE)
        self._render()

    def _on_focus_out(self, _e=None) -> None:
        self._focused = False
        self._render()

    def _on_key_activate(self, _e=None) -> None:
        if self._state == "disabled" or not self._command:
            return
        self._pressed = True
        self._press_prog = 1.0
        self._render()
        if self._key_aid is not None:
            try:
                self.after_cancel(self._key_aid)
            except Exception:
                pass
        self._key_aid = self.after(90, self._finish_key_activate)

    def _finish_key_activate(self) -> None:
        self._key_aid = None
        if not self.winfo_exists():
            return
        self._pressed = False
        self._animate_rebound()
        if self._command:
            _safe_invoke(self._command, self._text or "按钮")

    def _render(self, force: bool = False) -> None:
        if self._state == "disabled":
            self._hovered = False
            self._hover_prog = 0.0
            mode = "disabled"
        elif self._pressed or self._inset_active:
            mode = "pressed"
        elif self._hovered or self._hover_prog > 0.05:
            mode = "hover"
        else:
            mode = "normal"
        h_q = round(self._hover_prog, 2)
        p_q = round(self._press_prog, 2)
        key = (
            self._box_w, self._box_h, mode, h_q, p_q, self._focused, self._text,
            self._dot_color, self._custom_fg, self._danger_fill, self._loading,
            self._spin_step, self._surface_bg, _ACTIVE_THEME,
        )
        if not force and key == self._last_key:
            return
        self._last_key = key

        bw, bh, pad = self._box_w, self._box_h, self._pad
        tight = pad < sx(9, self)
        ultra_tight = pad <= sx(6, self)
        surf_rgb = hex_to_rgb(self._surface_bg)
        danger_rgb = hex_to_rgb(DANGER_FILL) if self._danger_fill else None

        if mode == "pressed":
            fill_rgb = blend_rgb(surf_rgb, danger_rgb, 0.92) if danger_rgb else surf_rgb
            self._photo = make_surface_photo(
                bw, bh, radius=self._radius, off=2 if ultra_tight else (3 if tight else 4),
                box_r=1 if ultra_tight else (2 if tight else 3), pad=pad, inset=not self._danger_fill,
                fill_rgb=fill_rgb, outer_rgb=surf_rgb, shadow_strength=0.84,
            )
        elif mode == "disabled":
            fill_rgb = blend_rgb(surf_rgb, hex_to_rgb(GRAY_DISABLED), 0.14)
            self._photo = make_surface_photo(
                bw, bh, radius=self._radius, off=2, box_r=1 if tight else 2, pad=pad,
                inset=False, fill_rgb=fill_rgb, outer_rgb=surf_rgb, shadow_strength=0.30, light_strength=0.32,
            )
        elif mode == "hover":
            base_off = 2 if ultra_tight else (3 if tight else 5)
            h_off = max(2, int(round(base_off - (base_off - 2) * self._hover_prog)))
            sh_str = 0.92 - 0.16 * self._hover_prog
            fill_rgb = blend_rgb(surf_rgb, danger_rgb, 0.94 + 0.06 * self._hover_prog) if danger_rgb else surf_rgb
            self._photo = make_surface_photo(
                bw, bh, radius=self._radius, off=h_off, box_r=1 if tight else 2, pad=pad,
                inset=False, fill_rgb=fill_rgb, outer_rgb=surf_rgb, shadow_strength=sh_str,
                glow_rgb=CARD_GLOW_RGB, glow_strength=0.42 * self._hover_prog if not self._danger_fill else 0.0,
            )
        else:
            fill_rgb = danger_rgb if danger_rgb else surf_rgb
            self._photo = make_surface_photo(
                bw, bh, radius=self._radius, off=2 if ultra_tight else (3 if tight else 5),
                box_r=1 if ultra_tight else (2 if tight else 3), pad=pad, inset=False,
                fill_rgb=fill_rgb, outer_rgb=surf_rgb,
            )

        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)

        if self._focused and mode != "disabled":
            o = max(2, pad - sx(3, self))
            r = self._radius + sx(2, self)
            x0, y0, x1, y1 = o, o, pad * 2 + bw - o, pad * 2 + bh - o
            self._draw_rounded_outline(x0, y0, x1, y1, r, BLURPLE_FOCUS, width=1.7)

        cx = pad + bw / 2.0
        press_dy = max(1.2, sx(1.35, self)) * (1.0 if self._pressed else (0.55 if self._inset_active else self._press_prog))
        cy = pad + bh / 2.0 + press_dy
        if mode == "disabled":
            fg = TEXT_DISABLED
        elif self._danger_fill:
            fg = TEXT_ON_DANGER
        elif self._custom_fg:
            fg = self._custom_fg
        elif self._accent or self._inset_active:
            fg = BLURPLE_LINK
        else:
            fg = TEXT

        if self._loading:
            tw, _ = self._measure_text_px()
            sr = max(4.5, sx(5, self))
            gap = sx(7, self)
            total = sr * 2 + gap + tw
            start_x = cx - total / 2.0
            scx = start_x + sr
            self.create_arc(
                scx - sr, cy - sr, scx + sr, cy + sr,
                start=self._spin_step * 45, extent=230, style="arc", outline=BLURPLE_BRAND, width=1.8,
            )
            self.create_text(
                start_x + sr * 2 + gap + tw / 2.0,
                cy,
                text=self._text,
                fill=fg,
                font=self._font,
            )
        elif self._dot_color:
            dot_col = TEXT_DISABLED if mode == "disabled" else self._dot_color
            tw, _ = self._measure_text_px()
            dot_r = max(3.0, sx(3.5, self))
            gap = sx(7, self)
            total = dot_r * 2 + gap + tw
            start_x = cx - total / 2.0
            self.create_oval(
                start_x, cy - dot_r, start_x + dot_r * 2, cy + dot_r, fill=dot_col, outline=""
            )
            self.create_text(
                start_x + dot_r * 2 + gap + tw / 2.0,
                cy,
                text=self._text,
                fill=fg,
                font=self._font,
            )
        else:
            self.create_text(cx, cy, text=self._text, fill=fg, font=self._font)

    def _draw_rounded_outline(
        self, x0: float, y0: float, x1: float, y1: float, r: float, color: str, width: float = 1.5
    ) -> None:
        pad_px = width + 2.0
        w = max(4, int(round(x1 - x0 + pad_px)))
        h = max(4, int(round(y1 - y0 + pad_px)))
        photo = _make_aa_rounded_outline_photo(
            w, h, radius=r, color_hex=color, stroke_width=width, master=self
        )
        self._outline_photo = photo
        self.create_image(x0 - pad_px / 2.0, y0 - pad_px / 2.0, anchor="nw", image=photo)


class NeuToggle(tk.Frame):
    """新拟物 Soft Toggle：SDF 抗锯齿凹陷胶囊轨道 + 4x4 超采样圆形旋钮与柔和球面阴影。"""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        text: str = "",
        variable: tk.BooleanVar,
        command: Optional[Callable[[], Any]] = None,
        font_size: int = 10,
    ) -> None:
        super().__init__(parent, bg=BASE, bd=0, highlightthickness=0, cursor="hand2")
        self.variable = variable
        self._command = command
        self._pad = 4
        self._tw = sx(52, parent, max_scale=1.45)
        self._th = sx(28, parent, max_scale=1.45)
        self._knob_d = max(18, self._th - 8)
        self._focused = False
        self._pos = 1.0 if variable.get() else 0.0
        self._anim_id: Optional[str] = None
        self._frame_photo: Optional[tk.PhotoImage] = None
        self._trace_id: Optional[str] = None

        self._cv = tk.Canvas(
            self,
            width=self._tw + self._pad * 2,
            height=self._th + self._pad * 2,
            bg=BASE,
            bd=0,
            highlightthickness=0,
            takefocus=1,
            cursor="hand2",
        )
        self._cv.pack(side="left")
        _, _, self._track_rows = _CACHE.render_rgb_rows(
            self._tw,
            self._th,
            radius=self._th // 2,
            off=4,
            box_r=3,
            pad=self._pad,
            inset=True,
            shadow_strength=0.85,
        )

        self._lbl = tk.Label(
            self,
            text=text,
            bg=BASE,
            fg=TEXT if variable.get() else TEXT_MUTED,
            font=ui_font(font_size, root=parent),
            cursor="hand2",
        )
        if text:
            self._lbl.pack(side="left", padx=(6, 0))

        self._cv.bind("<Button-1>", self._toggle)
        self._lbl.bind("<Button-1>", self._toggle)
        self._cv.bind("<KeyPress-space>", self._toggle)
        self._cv.bind("<KeyPress-Return>", self._toggle)
        self._cv.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self._cv.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self._trace_id = self.variable.trace_add("write", lambda *_: self._sync_from_var())
        self.bind("<Destroy>", self._on_destroy, add="+")
        _ensure_focus_hooks(self)

        self._draw()

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None
        if self._trace_id is not None:
            try:
                self.variable.trace_remove("write", self._trace_id)
            except Exception:
                pass
            self._trace_id = None

    def _set_focus(self, focused: bool) -> None:
        self._focused = bool(focused) and bool(_KB_FOCUS_ACTIVE)
        self._draw()

    def _toggle(self, event=None) -> None:
        if event is not None and event.type == tk.EventType.ButtonPress:
            _mark_pointer_focus()
            self._focused = False
            self._draw()
        self.variable.set(not bool(self.variable.get()))
        if self._command:
            _safe_invoke(self._command, "开关")

    def _sync_from_var(self) -> None:
        if not self.winfo_exists():
            return
        on = bool(self.variable.get())
        self._lbl.configure(fg=TEXT if on else TEXT_MUTED)
        target = 1.0 if on else 0.0
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None
        if abs(self._pos - target) < 0.01:
            self._pos = target
            self._draw()
        else:
            self._step_anim(target)

    def _step_anim(self, target: float) -> None:
        if not self.winfo_exists():
            return
        diff = target - self._pos
        if abs(diff) <= 0.08:
            self._pos = target
            self._anim_id = None
            self._draw()
            return
        self._pos += diff * 0.36
        self._draw()
        self._anim_id = self.after(14, lambda: self._step_anim(target))

    def _draw(self) -> None:
        ckey: Optional[tuple] = None
        if self._pos <= 0.0 or self._pos >= 1.0:
            ckey = (
                self._tw, self._th, self._pad, self._knob_d,
                1 if self._pos >= 0.5 else 0, self._focused, _ACTIVE_THEME,
            )
        self._frame_photo = _render_toggle_photo(
            self._tw,
            self._th,
            self._pad,
            self._knob_d,
            self._pos,
            self._focused,
            self._track_rows,
            cache_key=ckey,
            master=self._cv,
        )
        self._cv.delete("all")
        self._cv.create_image(0, 0, anchor="nw", image=self._frame_photo)


class NeuProgressBar(tk.Canvas):
    """新拟物凹槽能量条：inset 凹槽轨道 + 4x4 超采样抗锯齿填充条。"""

    def __init__(self, parent: tk.Widget, height: Optional[int] = None, surface_bg: Optional[str] = None) -> None:
        eff_bg = BASE if surface_bg is None else surface_bg
        self._bar_h = height if height is not None else sx(11, parent, max_scale=1.45)
        self._surface_bg = eff_bg
        super().__init__(parent, width=sx(80, parent), height=self._bar_h, bg=eff_bg, bd=0, highlightthickness=0, takefocus=0)
        self._pct = 0
        self._color = MINT
        self._track_rows: Optional[list[bytes]] = None
        self._photo: Optional[tk.PhotoImage] = None
        self._last_key: tuple = ()
        self.bind("<Configure>", lambda e: self._redraw())

    def set_surface_bg(self, surface_bg: str) -> None:
        if surface_bg != self._surface_bg:
            self._surface_bg = surface_bg
            super().configure(bg=surface_bg)
            self._redraw(force=True)

    def set_value(self, pct: int, color: str) -> None:
        self._pct = max(0, min(100, int(pct)))
        self._color = color
        self._redraw(force=True)

    def _redraw(self, force: bool = False) -> None:
        w = max(24, self.winfo_width())
        h = self._bar_h
        tkey = (w, self._surface_bg)
        if tkey != self._last_key or self._track_rows is None:
            self._last_key = tkey
            rgb = hex_to_rgb(self._surface_bg)
            _, _, self._track_rows = _CACHE.render_rgb_rows(
                w, h, radius=h // 2, off=3, box_r=2, pad=0, inset=True,
                fill_rgb=rgb, outer_rgb=rgb, shadow_strength=0.72,
            )
            force = True
        if not force and self._photo is not None:
            return
        rows = [bytearray(rw) for rw in self._track_rows]
        if self._pct > 0:
            inset_pad = 2.0
            max_fw = max(0.0, w - inset_pad * 2.0)
            fw = max(float(h) - inset_pad * 2.0, max_fw * self._pct / 100.0)
            fw = min(fw, max_fw)
            x0, y0 = inset_pad, inset_pad
            x1, y1 = inset_pad + fw, float(h) - inset_pad
            r = max(1.0, (y1 - y0) * 0.5)
            _blend_aa_capsule_rows(rows, w, h, x0, y0, x1, y1, r, hex_to_rgb(self._color))
        data = f"P6\n{w} {h}\n255\n".encode("ascii") + b"".join(bytes(rw) for rw in rows)
        self._photo = tk.PhotoImage(data=data, format="PPM", master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._photo)


class NeuEntry(NeuCard):
    """新拟物 Recessed Input：16px 凹陷输入框，聚焦时保持凹陷并转向紫罗兰阴影色调。"""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        textvariable: Optional[tk.StringVar] = None,
        width: int = 24,
        font_tuple: Optional[tuple] = None,
    ) -> None:
        super().__init__(parent, radius=16, pad=0, inner_pad=(14, 7), off=5, box_r=3, inset=True)
        self.entry = tk.Entry(
            self.content,
            textvariable=textvariable,
            width=width,
            bg=BASE,
            fg=TEXT,
            disabledforeground=TEXT_DIM,
            insertbackground=BLURPLE_BRAND,
            selectbackground=BLURPLE_BRAND,
            selectforeground=TEXT_ON_ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            cursor="xterm",
            font=font_tuple or ui_font(FONT_BODY, root=parent),
        )
        self.entry.pack(fill="both", expand=True)
        for w in (self, self._bg_label, self.content):
            try:
                w.configure(cursor="xterm")
                w.bind("<ButtonPress-1>", self._focus_entry_on_click, add="+")
            except Exception:
                pass
        self.entry.bind("<ButtonPress-1>", self._focus_entry_on_click, add="+")
        self.entry.bind("<FocusIn>", lambda _e: self.set_focus_glow(True), add="+")
        self.entry.bind("<FocusOut>", lambda _e: self.set_focus_glow(False), add="+")
        _ensure_focus_hooks(self)

    def _focus_entry_on_click(self, event=None) -> None:
        if not self.entry.winfo_exists():
            return
        try:
            self.entry.focus_force()
            if event is not None and event.widget is not self.entry:
                self.entry.icursor("end")
        except Exception:
            try:
                self.entry.focus_set()
            except Exception:
                pass


class NeuSelect(tk.Frame):
    """新拟物凹槽选择器 + 圆角透明去角悬浮下拉菜单（支持键盘聚焦与等宽精准对齐）。"""

    def __init__(
        self,
        parent: tk.Widget,
        *,
        variable: tk.StringVar,
        values: list[str],
        on_change: Optional[Callable[[], Any]] = None,
        width_px: int = 112,
    ) -> None:
        self._target_w = max(sx(72, parent), int(width_px))
        self._font = ui_font(FONT_CAPTION, root=parent)
        self._font_obj = tkfont.Font(root=parent, font=self._font)
        self._target_h = max(sx(30, parent), self._font_obj.metrics("linespace") + sx(14, parent))
        super().__init__(
            parent, width=self._target_w, height=self._target_h, bg=BASE, bd=0, highlightthickness=0, takefocus=1
        )
        self.pack_propagate(False)
        self.variable = variable
        self.values = list(values)
        self._on_change = on_change
        self._popup: Optional[tk.Toplevel] = None
        self._well = NeuCard(
            self, radius=14, pad=0, inner_pad=(11, 5), off=4, box_r=2, inset=True
        )
        self._well.pack(fill="both", expand=True)
        self._row = tk.Frame(self._well.content, bg=BASE, cursor="hand2")
        self._row.pack(fill="both", expand=True)
        self._lbl = tk.Label(
            self._row,
            textvariable=self.variable,
            bg=BASE,
            fg=TEXT,
            font=self._font,
            cursor="hand2",
            anchor="w",
        )
        self._lbl.pack(side="left", fill="x", expand=True)
        self._arrow = tk.Canvas(
            self._row,
            width=sx(12, parent),
            height=sx(10, parent),
            bg=BASE,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=0,
        )
        self._arrow.pack(side="right", padx=(sx(4, parent), 0))
        aw, ah = sx(12, parent), sx(10, parent)
        self._arrow.create_polygon(
            aw * 0.22, ah * 0.34,
            aw * 0.78, ah * 0.34,
            aw * 0.50, ah * 0.72,
            fill=BLURPLE_LINK,
            outline="",
        )

        for w in (self._well, self._well.content, self._row, self._lbl, self._arrow):
            w.bind("<Button-1>", self._toggle_popup)
        self.bind("<FocusIn>", lambda _e: self._well.set_focus_glow(bool(_KB_FOCUS_ACTIVE)))
        self.bind("<FocusOut>", lambda _e: self._well.set_focus_glow(False) if not self._popup else None)
        self.bind("<Return>", self._toggle_popup)
        self.bind("<space>", self._toggle_popup)
        self.bind("<Up>", lambda _e: self._step_value(-1))
        self.bind("<Down>", lambda _e: self._step_value(1))
        self.bind("<Destroy>", lambda e: self._close_popup(restore_focus=False) if e.widget is self else None, add="+")
        self._outside_binds: list[tuple[str, str]] = []
        self._outside_watcher: Optional[_OutsideClickWatcher] = None
        self._card_screen_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._pop_photos: list[tk.PhotoImage] = []
        _ensure_focus_hooks(self)

    def _step_value(self, delta: int) -> None:
        if not self.values:
            return
        cur = self.variable.get()
        idx = self.values.index(cur) if cur in self.values else 0
        nxt = (idx + delta) % len(self.values)
        self._choose(self.values[nxt], via_pointer=False)

    def _register_popup_owner(self, register: bool) -> None:
        try:
            top = self.winfo_toplevel()
            reg = getattr(top, "_neu_open_selects", None)
            if reg is None:
                reg = set()
                setattr(top, "_neu_open_selects", reg)
            if register:
                reg.add(self)
            else:
                reg.discard(self)
        except Exception:
            pass

    def _toggle_popup(self, event=None) -> str:
        is_ptr = event is not None and event.type == tk.EventType.ButtonPress
        if is_ptr:
            _mark_pointer_focus()
        if self._popup is not None and self._popup.winfo_exists():
            self._close_popup(restore_focus=not is_ptr)
            return "break"
        # 先关闭同一窗口/全局已展开的其他选择器或右键/更多菜单
        try:
            dismiss_active_popup_menus(self)
        except Exception:
            pass

        self._well.set_focus_glow(bool(_KB_FOCUS_ACTIVE))
        pop = tk.Toplevel(self)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.configure(bg=TRANSP_KEY)
        try:
            pop.attributes("-topmost", True)
            pop.attributes("-transparentcolor", TRANSP_KEY)
        except Exception:
            pass
        self._popup = pop
        self._register_popup_owner(True)

        # 使用单层 Canvas 自绘阴影圆角菜单项，彻底规避 Windows 分层窗口子 Frame 遮挡/空白撕裂
        pad = sx(10, self)
        in_x = sx(6, self)
        in_y = sx(6, self)
        dot_col_w = sx(18, self)
        font_norm = ui_font(FONT_CAPTION, "normal", root=self)
        font_bold = ui_font(FONT_CAPTION, "bold", root=self)
        f_bold_obj = tkfont.Font(root=self, font=font_bold)
        row_h = max(sx(30, self), f_bold_obj.metrics("linespace") + sx(12, self))
        max_txt_w = max([f_bold_obj.measure(str(v)) for v in self.values] + [sx(60, self)])
        box_w = max(self.winfo_width() or self._target_w, self._target_w, max_txt_w + dot_col_w + in_x * 2 + sx(18, self))
        box_h = max(row_h, len(self.values) * row_h) + in_y * 2
        pw = box_w + pad * 2
        ph = box_h + pad * 2

        wl, wt, wr, wb = get_work_area()
        sx_anchor = self.winfo_rootx() + self.winfo_width() - box_w
        sy_below = self.winfo_rooty() + self.winfo_height() + 4
        card_x = max(wl + 6, min(wr - box_w - 6, sx_anchor))
        if sy_below + box_h <= wb - 6:
            card_y = sy_below
        else:
            card_y = max(wt + 6, self.winfo_rooty() - 4 - box_h)
        x = card_x - pad
        y = card_y - pad
        self._card_screen_rect = (card_x, card_y, card_x + box_w, card_y + box_h)

        pop_cv = tk.Canvas(
            pop, width=pw, height=ph, bg=TRANSP_KEY, bd=0, highlightthickness=0, cursor="hand2", takefocus=0
        )
        pop_cv.pack(fill="both", expand=True)

        card_photo = make_surface_photo(
            box_w, box_h, radius=16, off=5, box_r=3, pad=pad, inset=False,
            fill_rgb=hex_to_rgb(POPUP_BG), outer_rgb=hex_to_rgb(TRANSP_KEY),
        )
        pill_w = max(24, box_w - in_x * 2)
        pill_h = max(18, row_h - sx(4, self))
        hover_pill = _make_pill_photo(
            pill_w, pill_h, hex_to_rgb(BLURPLE_SELECTION), hex_to_rgb(POPUP_BG), pad_px=1.0, master=pop_cv
        )
        dot_sz = max(8, sx(8, self))
        dot_norm = _make_pill_photo(
            dot_sz, dot_sz, hex_to_rgb(BLURPLE_BRAND), hex_to_rgb(POPUP_BG), pad_px=1.0, master=pop_cv
        )
        dot_hover = _make_pill_photo(
            dot_sz, dot_sz, hex_to_rgb(BLURPLE_BRAND), hex_to_rgb(BLURPLE_SELECTION), pad_px=1.0, master=pop_cv
        )
        self._pop_photos = [card_photo, hover_pill, dot_norm, dot_hover]
        pop_cv.create_image(0, 0, anchor="nw", image=card_photo)

        row_rects: list[tuple[float, float, str]] = []
        for idx, val in enumerate(self.values):
            ry0 = pad + in_y + idx * row_h
            row_rects.append((ry0, ry0 + row_h, val))

        hover_state: dict[str, Optional[int]] = {
            "idx": self.values.index(self.variable.get()) if (not is_ptr and self.variable.get() in self.values) else None
        }

        def _row_at(px: float, py: float) -> Optional[int]:
            if not (pad <= px <= pw - pad and pad + in_y <= py <= ph - pad - in_y):
                return None
            for idx, (ry0, ry1, _v) in enumerate(row_rects):
                if ry0 <= py < ry1:
                    return idx
            return None

        def _draw_items(hi: Optional[int]) -> None:
            pop_cv.delete("menu_item")
            cur = self.variable.get()
            for idx, (ry0, ry1, val) in enumerate(row_rects):
                is_sel = val == cur
                is_hi = idx == hi
                cy = (ry0 + ry1) / 2.0
                if is_hi:
                    pop_cv.create_image(pad + in_x, cy, anchor="w", image=hover_pill, tags="menu_item")
                if is_sel:
                    pop_cv.create_image(
                        pad + in_x + dot_col_w / 2.0 + sx(2, self), cy, anchor="center",
                        image=(dot_hover if is_hi else dot_norm), tags="menu_item",
                    )
                pop_cv.create_text(
                    pad + in_x + dot_col_w + sx(4, self),
                    cy,
                    text=val,
                    anchor="w",
                    fill=BLURPLE_LINK if (is_sel or is_hi) else TEXT,
                    font=font_bold if is_sel else font_norm,
                    tags="menu_item",
                )

        _draw_items(hover_state["idx"])

        def _card_rect_live() -> tuple[int, int, int, int]:
            try:
                if pop.winfo_exists():
                    rx0 = int(pop.winfo_rootx()) + pad
                    ry0 = int(pop.winfo_rooty()) + pad
                    return (rx0, ry0, rx0 + box_w, ry0 + box_h)
            except Exception:
                pass
            return self._card_screen_rect

        def _on_motion(e) -> None:
            ni = _row_at(e.x, e.y)
            if ni != hover_state["idx"]:
                hover_state["idx"] = ni
                _draw_items(ni)

        def _on_leave(_e=None) -> None:
            if hover_state["idx"] is not None:
                hover_state["idx"] = None
                _draw_items(None)

        def _on_press(e) -> str:
            try:
                rx0, ry0, rx1, ry1 = _card_rect_live()
                if not (rx0 <= e.x_root <= rx1 and ry0 <= e.y_root <= ry1):
                    self._close_popup(restore_focus=False)
                    return "break"
            except Exception:
                pass
            idx = _row_at(e.x, e.y)
            if idx is not None:
                self._choose(row_rects[idx][2], via_pointer=True)
            else:
                self._close_popup(restore_focus=False)
            return "break"

        def _on_pop_outer_press(e) -> str:
            # 关键修复：pop.grab_set() 时窗口内其它区域点击事件直接发往 pop（而非 pop_cv）
            try:
                rx0, ry0, rx1, ry1 = _card_rect_live()
                if e.widget is pop or not (rx0 <= e.x_root <= rx1 and ry0 <= e.y_root <= ry1):
                    self._close_popup(restore_focus=False)
                    return "break"
            except Exception:
                self._close_popup(restore_focus=False)
                return "break"
            return "break"

        def _on_key_move(delta: int) -> str:
            _mark_keyboard_focus()
            if not row_rects:
                return "break"
            cur_i = hover_state["idx"]
            if cur_i is None:
                cur_val = self.variable.get()
                cur_i = self.values.index(cur_val) if cur_val in self.values else 0
            nxt = (cur_i + delta) % len(row_rects)
            hover_state["idx"] = nxt
            _draw_items(nxt)
            return "break"

        def _on_key_confirm(_e=None) -> str:
            idx = hover_state["idx"]
            if idx is not None and 0 <= idx < len(row_rects):
                self._choose(row_rects[idx][2], via_pointer=False)
            else:
                self._close_popup(restore_focus=True)
            return "break"

        def _on_wheel(e) -> str:
            # 滚轮触发时关闭浮层，并将滚轮事件透传给底层弹窗滚动区
            self._close_popup(restore_focus=False)
            try:
                modal = getattr(self.winfo_toplevel(), "_neu_active_modal", None)
                if modal is not None and hasattr(modal, "_on_body_wheel"):
                    modal._on_body_wheel(e)
            except Exception:
                pass
            return "break"

        pop_cv.bind("<Motion>", _on_motion)
        pop_cv.bind("<Leave>", _on_leave)
        pop_cv.bind("<ButtonPress-1>", _on_press)
        pop.bind("<ButtonPress-1>", _on_pop_outer_press)
        pop.bind("<ButtonPress-2>", lambda _e: (self._close_popup(restore_focus=False), "break")[1])
        pop.bind("<ButtonPress-3>", lambda _e: (self._close_popup(restore_focus=False), "break")[1])
        pop.bind("<MouseWheel>", _on_wheel)
        pop_cv.bind("<MouseWheel>", _on_wheel)
        pop.bind("<Escape>", lambda _e: (self._close_popup(restore_focus=bool(_KB_FOCUS_ACTIVE)), "break")[1])
        pop.bind("<Up>", lambda _e: _on_key_move(-1))
        pop.bind("<Down>", lambda _e: _on_key_move(1))
        pop.bind("<Return>", _on_key_confirm)
        pop.bind("<space>", _on_key_confirm)
        pop.bind("<FocusOut>", lambda _e: self.after(30, self._check_close))

        try:
            top = self.winfo_toplevel()
            base_geom = (
                int(top.winfo_x()),
                int(top.winfo_y()),
                int(top.winfo_width()),
                int(top.winfo_height()),
            )

            def _on_top_configure(ev=None) -> None:
                if ev is not None and ev.widget is not top:
                    return
                try:
                    cur_g = (int(top.winfo_x()), int(top.winfo_y()), int(top.winfo_width()), int(top.winfo_height()))
                    if cur_g != base_geom:
                        self._close_popup(restore_focus=False)
                except Exception:
                    pass

            for seq in ("<ButtonPress-1>", "<ButtonPress-3>", "<Deactivate>", "<Unmap>"):
                bid = top.bind(seq, lambda _e: self._close_popup(restore_focus=False), add="+")
                self._outside_binds.append((seq, bid))
            cid = top.bind("<Configure>", _on_top_configure, add="+")
            self._outside_binds.append(("<Configure>", cid))
        except Exception:
            pass

        pop.geometry(f"{pw}x{ph}+{x}+{y}")
        pop.update_idletasks()
        pop.deiconify()
        try:
            pop.attributes("-transparentcolor", TRANSP_KEY)
            pop.attributes("-topmost", True)
            pop.lift()
        except Exception:
            pass
        _set_window_pos_topmost(_root_hwnd(pop), x, y, pw, ph, no_activate=False)
        self._card_screen_rect = _card_rect_live()
        try:
            pop.grab_set()
        except Exception:
            pass
        try:
            pop.focus_force()
        except Exception:
            pass
        self._outside_watcher = _OutsideClickWatcher(
            self,
            lambda: [_card_rect_live()],
            lambda: self._close_popup(restore_focus=False),
        )
        return "break"

    def _check_close(self) -> None:
        if self._popup is not None and self._popup.winfo_exists():
            try:
                f = self._popup.focus_get()
                if f is None or f.winfo_toplevel() is not self._popup:
                    self._close_popup(restore_focus=False)
            except Exception:
                self._close_popup(restore_focus=False)

    def _close_popup(self, restore_focus: bool = True) -> None:
        self._register_popup_owner(False)
        watcher = getattr(self, "_outside_watcher", None)
        self._outside_watcher = None
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass
        try:
            if hasattr(self, "_outside_binds") and self._outside_binds:
                top = self.winfo_toplevel()
                for seq, bid in self._outside_binds:
                    try:
                        if top.winfo_exists():
                            top.unbind(seq, bid)
                    except Exception:
                        pass
                self._outside_binds.clear()
        except Exception:
            pass
        try:
            if hasattr(self, "_well") and self._well.winfo_exists():
                self._well.set_focus_glow(False)
        except Exception:
            pass
        pop = self._popup
        self._popup = None
        if pop is not None:
            try:
                if pop.winfo_exists():
                    try:
                        pop.grab_release()
                    except Exception:
                        pass
                    pop.destroy()
            except Exception:
                pass
        self._pop_photos = []
        try:
            if self.winfo_exists():
                modal = getattr(self.winfo_toplevel(), "_neu_active_modal", None)
                if modal is not None and hasattr(modal, "restore_grab"):
                    modal.restore_grab()
        except Exception:
            pass
        if restore_focus and _KB_FOCUS_ACTIVE and self.winfo_exists():
            try:
                self.focus_set()
            except Exception:
                pass

    def _choose(self, val: str, *, via_pointer: bool = False) -> None:
        if via_pointer:
            _mark_pointer_focus()
        prev = self.variable.get()
        self.variable.set(val)
        self._close_popup(restore_focus=not via_pointer)
        if val != prev and self._on_change:
            _safe_invoke(self._on_change, "选择器")


class NeuPopupMenu:
    """与全局风格一致的圆角新拟物弹出菜单（支持子菜单、分隔线、勾选圆点、危险项与点击任意外部关闭）。"""

    def __init__(
        self,
        master: tk.Misc,
        items: list[dict[str, Any]],
        *,
        min_width_dp: int = 160,
        topmost: bool = True,
        anchor_bottom: bool = False,
        right_align: bool = False,
        on_close: Optional[Callable[[], Any]] = None,
        _parent_menu: Optional["NeuPopupMenu"] = None,
    ) -> None:
        self._master = master
        self._items = [dict(it) for it in items]
        self._min_width_dp = int(min_width_dp)
        self._topmost = bool(topmost)
        self._anchor_bottom = bool(anchor_bottom)
        self._right_align = bool(right_align)
        self._on_close = on_close
        self._parent_menu = _parent_menu
        self._is_root = _parent_menu is None
        self._popup: Optional[tk.Toplevel] = None
        self._canvas: Optional[tk.Canvas] = None
        self._submenu: Optional[NeuPopupMenu] = None
        self._sub_open_aid: Optional[str] = None
        self._sub_close_aid: Optional[str] = None
        self._sub_owner_idx: Optional[int] = None
        self._outside_watcher: Optional[_OutsideClickWatcher] = None
        self._outside_binds: list[tuple[str, str]] = []
        self._photos: list[tk.PhotoImage] = []
        self._row_meta: list[dict[str, Any]] = []
        self._hover_idx: Optional[int] = None
        self._pad = sx(10, master)
        self._box_w = 180
        self._box_h = 80
        self._pw = 200
        self._ph = 100
        self._card_rect: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._closed = False
        self._draw_fn: Optional[Callable[[Optional[int]], None]] = None

    def is_open(self) -> bool:
        return (not self._closed) and (self._popup is not None) and bool(self._popup.winfo_exists())

    def _root_menu(self) -> "NeuPopupMenu":
        cur: NeuPopupMenu = self
        while cur._parent_menu is not None:
            cur = cur._parent_menu
        return cur

    def _all_card_rects(self) -> list[tuple[int, int, int, int]]:
        rects: list[tuple[int, int, int, int]] = []
        cur: Optional[NeuPopupMenu] = self._root_menu()
        while cur is not None and cur.is_open():
            rects.append(cur._live_card_rect())
            cur = cur._submenu
        return rects

    def _live_card_rect(self) -> tuple[int, int, int, int]:
        pop = self._popup
        if pop is not None and pop.winfo_exists():
            try:
                rx0 = int(pop.winfo_rootx()) + self._pad
                ry0 = int(pop.winfo_rooty()) + self._pad
                return (rx0, ry0, rx0 + self._box_w, ry0 + self._box_h)
            except Exception:
                pass
        return self._card_rect

    def open_at(self, x_root: int, y_root: int, *, parent_rect: Optional[tuple[int, int, int, int]] = None) -> "NeuPopupMenu":
        if self._is_root:
            dismiss_active_popup_menus(self._master)
            _ACTIVE_POPUP_MENUS.append(self)

        pop_parent = (
            self._parent_menu._popup
            if (self._parent_menu is not None and self._parent_menu._popup is not None and self._parent_menu._popup.winfo_exists())
            else self._master
        )
        pop = tk.Toplevel(pop_parent)
        pop.withdraw()
        pop.overrideredirect(True)
        pop.configure(bg=TRANSP_KEY)
        try:
            pop.attributes("-transparentcolor", TRANSP_KEY)
            if self._topmost:
                pop.attributes("-topmost", True)
        except Exception:
            pass
        self._popup = pop
        self._closed = False

        pad = sx(10, self._master)
        in_x = sx(6, self._master)
        in_y = sx(6, self._master)
        dot_col_w = sx(16, self._master)
        has_sub = any(it.get("type") == "submenu" for it in self._items)
        arrow_col_w = sx(18, self._master) if has_sub else sx(6, self._master)
        font_norm = ui_font(FONT_CAPTION, "normal", root=self._master)
        font_bold = ui_font(FONT_CAPTION, "bold", root=self._master)
        f_bold_obj = tkfont.Font(root=self._master, font=font_bold)
        row_h = max(sx(30, self._master), f_bold_obj.metrics("linespace") + sx(12, self._master))
        sep_h = max(sx(9, self._master), 9)

        labels = [str(it.get("label", "")) for it in self._items if it.get("type", "item") != "separator"]
        max_txt_w = max([f_bold_obj.measure(lbl) for lbl in labels] + [sx(64, self._master)])
        box_w = max(sx(self._min_width_dp, self._master), max_txt_w + dot_col_w + arrow_col_w + in_x * 2 + sx(16, self._master))

        y_cur = float(pad + in_y)
        self._row_meta = []
        for idx, it in enumerate(self._items):
            kind = str(it.get("type") or "item")
            if kind == "separator":
                self._row_meta.append({"kind": "separator", "y0": y_cur, "y1": y_cur + sep_h, "item": it})
                y_cur += sep_h
            else:
                self._row_meta.append({"kind": kind, "y0": y_cur, "y1": y_cur + row_h, "item": it})
                y_cur += row_h
        box_h = max(row_h + in_y * 2, int(round(y_cur - pad + in_y)))
        pw = box_w + pad * 2
        ph = box_h + pad * 2
        self._pad = pad
        self._box_w = box_w
        self._box_h = box_h
        self._pw = pw
        self._ph = ph

        wl, wt, wr, wb = get_work_area()
        if parent_rect is not None:
            px0, _py0, px1, _py1 = parent_rect
            if px1 + 2 + box_w <= wr - 6:
                card_x = px1 + 2
            else:
                card_x = max(wl + 6, px0 - box_w - 2)
            card_y = max(wt + 6, min(wb - box_h - 6, int(y_root) - in_y))
        else:
            raw_x = int(x_root) - box_w if self._right_align else int(x_root)
            card_x = max(wl + 6, min(wr - box_w - 6, raw_x))
            if self._anchor_bottom:
                if int(y_root) - box_h - 6 >= wt + 6:
                    card_y = min(wb - box_h - 6, int(y_root) - box_h - 6)
                else:
                    card_y = min(wb - box_h - 6, int(y_root) + 6)
            else:
                if int(y_root) + box_h <= wb - 6:
                    card_y = max(wt + 6, int(y_root))
                else:
                    card_y = max(wt + 6, int(y_root) - box_h - 4)

        wx = card_x - pad
        wy = card_y - pad
        self._card_rect = (card_x, card_y, card_x + box_w, card_y + box_h)

        pop_cv = tk.Canvas(
            pop, width=pw, height=ph, bg=TRANSP_KEY, bd=0, highlightthickness=0, cursor="hand2", takefocus=0
        )
        pop_cv.pack(fill="both", expand=True)
        self._canvas = pop_cv

        card_photo = make_surface_photo(
            box_w, box_h, radius=16, off=5, box_r=3, pad=pad, inset=False,
            fill_rgb=hex_to_rgb(POPUP_BG), outer_rgb=hex_to_rgb(TRANSP_KEY),
        )
        pill_w = max(24, box_w - in_x * 2)
        pill_h = max(18, row_h - sx(4, self._master))
        hover_pill = _make_pill_photo(
            pill_w, pill_h, hex_to_rgb(BLURPLE_SELECTION), hex_to_rgb(POPUP_BG), pad_px=1.0, master=pop_cv
        )
        danger_bg_rgb = blend_rgb(hex_to_rgb(POPUP_BG), hex_to_rgb(RED_DANGER), 0.16)
        danger_pill = _make_pill_photo(
            pill_w, pill_h, danger_bg_rgb, hex_to_rgb(POPUP_BG), pad_px=1.0, master=pop_cv
        )
        dot_sz = max(8, sx(8, self._master))
        dot_norm = _make_pill_photo(
            dot_sz, dot_sz, hex_to_rgb(BLURPLE_BRAND), hex_to_rgb(POPUP_BG), pad_px=1.0, master=pop_cv
        )
        dot_hover = _make_pill_photo(
            dot_sz, dot_sz, hex_to_rgb(BLURPLE_BRAND), hex_to_rgb(BLURPLE_SELECTION), pad_px=1.0, master=pop_cv
        )
        custom_dots: dict[str, tk.PhotoImage] = {}
        self._photos = [card_photo, hover_pill, danger_pill, dot_norm, dot_hover]
        pop_cv.create_image(0, 0, anchor="nw", image=card_photo)

        sep_col = SEP_DARK if _ACTIVE_THEME == "light" else SEP_LIGHT
        arr_cx = pad + box_w - in_x - sx(9, self._master)
        arr_r = max(3.0, float(sx(3, self._master)))

        def _get_dot_photo(hex_col: str, on_hover: bool) -> tk.PhotoImage:
            ck = f"{hex_col}:{int(on_hover)}"
            ph_img = custom_dots.get(ck)
            if ph_img is None:
                outer = hex_to_rgb(BLURPLE_SELECTION if on_hover else POPUP_BG)
                ph_img = _make_pill_photo(dot_sz, dot_sz, hex_to_rgb(hex_col), outer, pad_px=1.0, master=pop_cv)
                custom_dots[ck] = ph_img
                self._photos.append(ph_img)
            return ph_img

        def _draw_items(hi: Optional[int]) -> None:
            if not pop_cv.winfo_exists():
                return
            pop_cv.delete("menu_item")
            for idx, rm in enumerate(self._row_meta):
                ry0, ry1, it = rm["y0"], rm["y1"], rm["item"]
                cy = (ry0 + ry1) / 2.0
                if rm["kind"] == "separator":
                    pop_cv.create_line(
                        pad + in_x + sx(8, self._master), cy,
                        pw - pad - in_x - sx(8, self._master), cy,
                        fill=sep_col, width=1, tags="menu_item",
                    )
                    continue
                is_disabled = bool(it.get("disabled", False))
                is_danger = bool(it.get("danger", False))
                is_chk = bool(it.get("checked", False))
                is_sub = rm["kind"] == "submenu"
                is_hi = (idx == hi) and (not is_disabled)
                if is_hi:
                    pop_cv.create_image(
                        pad + in_x, cy, anchor="w",
                        image=(danger_pill if is_danger else hover_pill),
                        tags="menu_item",
                    )
                dot_hex = it.get("dot_color")
                if dot_hex:
                    pop_cv.create_image(
                        pad + in_x + dot_col_w / 2.0 + sx(2, self._master), cy, anchor="center",
                        image=_get_dot_photo(str(dot_hex), is_hi), tags="menu_item",
                    )
                elif is_chk:
                    pop_cv.create_image(
                        pad + in_x + dot_col_w / 2.0 + sx(2, self._master), cy, anchor="center",
                        image=(dot_hover if is_hi else dot_norm), tags="menu_item",
                    )
                if is_disabled:
                    txt_col = TEXT_DISABLED
                elif is_danger:
                    txt_col = RED_DANGER
                elif is_chk or is_hi:
                    txt_col = BLURPLE_LINK
                else:
                    txt_col = TEXT
                pop_cv.create_text(
                    pad + in_x + dot_col_w + sx(4, self._master),
                    cy,
                    text=str(it.get("label", "")),
                    anchor="w",
                    fill=txt_col,
                    font=font_bold if (is_chk or (is_danger and is_hi)) else font_norm,
                    tags="menu_item",
                )
                if is_sub:
                    ac = BLURPLE_LINK if is_hi else TEXT_DIM
                    pop_cv.create_line(
                        arr_cx - arr_r * 0.45, cy - arr_r,
                        arr_cx + arr_r * 0.55, cy,
                        arr_cx - arr_r * 0.45, cy + arr_r,
                        fill=ac, width=1.6, capstyle="round", joinstyle="round", tags="menu_item",
                    )

        self._draw_fn = _draw_items
        _draw_items(None)

        pop.geometry(f"{pw}x{ph}+{wx}+{wy}")
        pop.update_idletasks()
        pop.deiconify()
        try:
            pop.attributes("-transparentcolor", TRANSP_KEY)
            if self._topmost:
                pop.attributes("-topmost", True)
            pop.lift()
        except Exception:
            pass
        self._card_rect = self._live_card_rect()
        if self._topmost:
            _set_window_pos_topmost(_root_hwnd(pop), wx, wy, pw, ph, no_activate=False)

        if self._is_root:
            pop.bind("<Motion>", self._on_root_motion)
            pop.bind("<Leave>", lambda _e: self._schedule_hover_clear())
            pop.bind("<ButtonPress-1>", self._on_root_press)
            pop.bind("<ButtonPress-2>", lambda _e: (self.close(restore_focus=False), "break")[1])
            pop.bind("<ButtonPress-3>", self._on_root_right_press)
            pop.bind("<MouseWheel>", lambda _e: (self.close(restore_focus=False), "break")[1])
            pop.bind("<Escape>", self._on_key_escape)
            pop.bind("<Up>", lambda _e: self._on_key_move(-1))
            pop.bind("<Down>", lambda _e: self._on_key_move(1))
            pop.bind("<Right>", lambda _e: self._on_key_right())
            pop.bind("<Left>", lambda _e: self._on_key_left())
            pop.bind("<Return>", lambda _e: self._on_key_confirm())
            pop.bind("<space>", lambda _e: self._on_key_confirm())
            pop.bind("<FocusOut>", lambda _e: pop.after(40, self._check_focus_close))
            pop.bind("<Destroy>", self._on_pop_destroy, add="+")

            try:
                top = self._master.winfo_toplevel()
                for seq in ("<Deactivate>", "<Unmap>"):
                    bid = top.bind(seq, lambda _e: self.close(restore_focus=False), add="+")
                    self._outside_binds.append((seq, bid))
            except Exception:
                pass

            try:
                pop.grab_set()
            except Exception:
                pass
            try:
                u32 = _setup_win32_popup_funcs()
                hwnd = _root_hwnd(pop)
                if u32 is not None and hwnd:
                    u32.SetForegroundWindow(wintypes.HWND(hwnd))
                pop.focus_force()
            except Exception:
                pass
            self._outside_watcher = _OutsideClickWatcher(
                pop,
                self._all_card_rects,
                lambda: self.close(restore_focus=False),
            )
        else:
            root_m = self._root_menu()
            pop.bind("<Motion>", root_m._on_root_motion)
            pop.bind("<Leave>", lambda _e: root_m._schedule_hover_clear())
            pop.bind("<ButtonPress-1>", root_m._on_root_press)
            pop.bind("<ButtonPress-2>", lambda _e: (root_m.close(restore_focus=False), "break")[1])
            pop.bind("<ButtonPress-3>", root_m._on_root_right_press)
            pop.bind("<MouseWheel>", lambda _e: (root_m.close(restore_focus=False), "break")[1])
            pop.bind("<Escape>", root_m._on_key_escape)
            pop.bind("<Destroy>", self._on_pop_destroy, add="+")
        return self

    def _row_at_local(self, lx: float, ly: float) -> Optional[int]:
        if not (self._pad <= lx <= self._pw - self._pad and self._pad <= ly <= self._ph - self._pad):
            return None
        for idx, rm in enumerate(self._row_meta):
            if rm["kind"] == "separator":
                continue
            if rm["y0"] <= ly < rm["y1"]:
                return idx
        return None

    def _cancel_sub_timers(self) -> None:
        for attr in ("_sub_open_aid", "_sub_close_aid"):
            aid = getattr(self, attr, None)
            if aid is not None and self._popup is not None:
                try:
                    self._popup.after_cancel(aid)
                except Exception:
                    pass
            setattr(self, attr, None)

    def _set_hover_idx(self, ni: Optional[int]) -> None:
        if ni != self._hover_idx:
            self._hover_idx = ni
            if self._draw_fn is not None:
                self._draw_fn(ni)

    def _close_submenu(self) -> None:
        self._cancel_sub_timers()
        sub = self._submenu
        self._submenu = None
        self._sub_owner_idx = None
        if sub is not None:
            sub.close(restore_focus=False)

    def _open_submenu_for_row(self, idx: int) -> None:
        self._cancel_sub_timers()
        if idx < 0 or idx >= len(self._row_meta):
            return
        rm = self._row_meta[idx]
        if rm["kind"] != "submenu":
            return
        if self._submenu is not None and self._submenu.is_open() and self._sub_owner_idx == idx:
            return
        self._close_submenu()
        sub_items = list(rm["item"].get("items") or [])
        if not sub_items or self._popup is None or not self._popup.winfo_exists():
            return
        rx0, ry0, rx1, _ry1 = self._live_card_rect()
        row_screen_y = ry0 + int(rm["y0"] - self._pad)
        sub = NeuPopupMenu(
            self._master,
            sub_items,
            min_width_dp=124,
            topmost=self._topmost,
            _parent_menu=self,
        )
        self._submenu = sub
        self._sub_owner_idx = idx
        sub.open_at(rx1 + 2, row_screen_y, parent_rect=(rx0, ry0, rx1, ry0 + self._box_h))

    def _handle_motion_screen(self, x_root: int, y_root: int) -> None:
        if self._closed:
            return
        if self._submenu is not None and self._submenu.is_open():
            sx0, sy0, sx1, sy1 = self._submenu._live_card_rect()
            if sx0 - 4 <= x_root <= sx1 + 4 and sy0 - 4 <= y_root <= sy1 + 4:
                self._cancel_sub_timers()
                if self._sub_owner_idx is not None:
                    self._set_hover_idx(self._sub_owner_idx)
                lx = x_root - sx0 + self._submenu._pad
                ly = y_root - sy0 + self._submenu._pad
                ni_sub = self._submenu._row_at_local(lx, ly)
                self._submenu._set_hover_idx(ni_sub)
                return
            self._submenu._set_hover_idx(None)

        rx0, ry0, rx1, ry1 = self._live_card_rect()
        if rx0 <= x_root <= rx1 and ry0 <= y_root <= ry1:
            lx = x_root - rx0 + self._pad
            ly = y_root - ry0 + self._pad
            ni = self._row_at_local(lx, ly)
            self._set_hover_idx(ni)
            if ni is not None and self._row_meta[ni]["kind"] == "submenu":
                if self._sub_owner_idx != ni:
                    self._cancel_sub_timers()
                    if self._popup is not None and self._popup.winfo_exists():
                        self._sub_open_aid = self._popup.after(90, lambda i=ni: self._open_submenu_for_row(i))
                else:
                    self._cancel_sub_timers()
            elif self._submenu is not None and self._submenu.is_open():
                if self._sub_close_aid is None and self._popup is not None and self._popup.winfo_exists():
                    self._sub_close_aid = self._popup.after(110, self._close_submenu)
        else:
            self._set_hover_idx(None)

    def _handle_press_screen(self, x_root: int, y_root: int) -> str:
        if self._closed:
            return "break"
        if self._submenu is not None and self._submenu.is_open():
            sx0, sy0, sx1, sy1 = self._submenu._live_card_rect()
            if sx0 <= x_root <= sx1 and sy0 <= y_root <= sy1:
                lx = x_root - sx0 + self._submenu._pad
                ly = y_root - sy0 + self._submenu._pad
                s_idx = self._submenu._row_at_local(lx, ly)
                if s_idx is not None:
                    self._submenu._activate_row(s_idx)
                else:
                    self.close(restore_focus=False)
                return "break"

        rx0, ry0, rx1, ry1 = self._live_card_rect()
        if rx0 <= x_root <= rx1 and ry0 <= y_root <= ry1:
            lx = x_root - rx0 + self._pad
            ly = y_root - ry0 + self._pad
            idx = self._row_at_local(lx, ly)
            if idx is not None:
                self._activate_row(idx)
            else:
                self.close(restore_focus=False)
            return "break"

        self.close(restore_focus=False)
        return "break"

    def _activate_row(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._row_meta):
            return
        rm = self._row_meta[idx]
        it = rm["item"]
        if it.get("disabled", False) or rm["kind"] == "separator":
            return
        if rm["kind"] == "submenu":
            self._open_submenu_for_row(idx)
            return
        cmd = it.get("command")
        self._root_menu().close(restore_focus=False)
        try:
            if self._master.winfo_exists():
                self._master.update_idletasks()
        except Exception:
            pass
        if cmd:
            _safe_invoke(cmd, "快捷菜单")

    def _on_root_motion(self, e) -> None:
        self._handle_motion_screen(int(e.x_root), int(e.y_root))

    def _schedule_hover_clear(self) -> None:
        px, py = _get_pointer_screen_pos(self._popup)
        for x0, y0, x1, y1 in self._all_card_rects():
            if x0 <= px <= x1 and y0 <= py <= y1:
                return
        self._set_hover_idx(None)

    def _on_root_press(self, e) -> str:
        return self._handle_press_screen(int(e.x_root), int(e.y_root))

    def _on_root_right_press(self, e) -> str:
        if any(x0 <= e.x_root <= x1 and y0 <= e.y_root <= y1 for x0, y0, x1, y1 in self._all_card_rects()):
            return self._handle_press_screen(int(e.x_root), int(e.y_root))
        self.close(restore_focus=False)
        return "break"

    def _enabled_indices(self) -> list[int]:
        return [
            i for i, rm in enumerate(self._row_meta)
            if rm["kind"] != "separator" and not rm["item"].get("disabled", False)
        ]

    def _on_key_escape(self, _e=None) -> str:
        if self._submenu is not None and self._submenu.is_open():
            self._close_submenu()
            return "break"
        self.close(restore_focus=bool(_KB_FOCUS_ACTIVE))
        return "break"

    def _on_key_move(self, delta: int) -> str:
        target = self._submenu if (self._submenu is not None and self._submenu.is_open()) else self
        idxs = target._enabled_indices()
        if not idxs:
            return "break"
        if target._hover_idx in idxs:
            pos = idxs.index(target._hover_idx)
            target._set_hover_idx(idxs[(pos + delta) % len(idxs)])
        else:
            target._set_hover_idx(idxs[0 if delta > 0 else -1])
        return "break"

    def _on_key_right(self) -> str:
        if self._hover_idx is not None and 0 <= self._hover_idx < len(self._row_meta):
            if self._row_meta[self._hover_idx]["kind"] == "submenu":
                self._open_submenu_for_row(self._hover_idx)
                if self._submenu is not None:
                    sub_idxs = self._submenu._enabled_indices()
                    if sub_idxs:
                        self._submenu._set_hover_idx(sub_idxs[0])
        return "break"

    def _on_key_left(self) -> str:
        if self._submenu is not None and self._submenu.is_open():
            self._close_submenu()
        return "break"

    def _on_key_confirm(self) -> str:
        if self._submenu is not None and self._submenu.is_open() and self._submenu._hover_idx is not None:
            self._submenu._activate_row(self._submenu._hover_idx)
            return "break"
        if self._hover_idx is not None:
            self._activate_row(self._hover_idx)
        return "break"

    def _check_focus_close(self) -> None:
        if self._closed or self._popup is None or not self._popup.winfo_exists():
            return
        try:
            f = self._popup.focus_get()
            valid_tops = [self._popup]
            if self._submenu is not None and self._submenu._popup is not None:
                valid_tops.append(self._submenu._popup)
            if f is None or f.winfo_toplevel() not in valid_tops:
                px, py = _get_pointer_screen_pos(self._popup)
                if not any(x0 <= px <= x1 and y0 <= py <= y1 for x0, y0, x1, y1 in self._all_card_rects()):
                    self.close(restore_focus=False)
        except Exception:
            pass

    def _on_pop_destroy(self, e=None) -> None:
        if e is not None and e.widget is not self._popup:
            return
        if not self._closed:
            self.close(restore_focus=False)

    def close(self, restore_focus: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        self._cancel_sub_timers()
        if self._submenu is not None:
            sub = self._submenu
            self._submenu = None
            sub.close(restore_focus=False)
        if self._is_root:
            try:
                _ACTIVE_POPUP_MENUS.remove(self)
            except ValueError:
                pass
            watcher = self._outside_watcher
            self._outside_watcher = None
            if watcher is not None:
                try:
                    watcher.stop()
                except Exception:
                    pass
            try:
                if self._outside_binds and self._master.winfo_exists():
                    top = self._master.winfo_toplevel()
                    for seq, bid in self._outside_binds:
                        try:
                            if top.winfo_exists():
                                top.unbind(seq, bid)
                        except Exception:
                            pass
                self._outside_binds.clear()
            except Exception:
                pass
        pop = self._popup
        self._popup = None
        self._canvas = None
        self._draw_fn = None
        if pop is not None:
            try:
                if pop.winfo_exists():
                    if self._is_root:
                        try:
                            pop.grab_release()
                        except Exception:
                            pass
                    pop.destroy()
            except Exception:
                pass
        self._photos = []
        if self._is_root:
            try:
                if self._master.winfo_exists():
                    modal = getattr(self._master.winfo_toplevel(), "_neu_active_modal", None)
                    if modal is not None and hasattr(modal, "restore_grab"):
                        modal.restore_grab()
            except Exception:
                pass
            if self._on_close is not None:
                cb = self._on_close
                self._on_close = None
                try:
                    cb()
                except Exception:
                    pass


class NeuTabs(tk.Frame):
    """新拟物标签栏：选中项呈清晰紫罗兰圆点标记与 inset 凹陷对比质感。"""

    def __init__(self, parent: tk.Widget, titles: list[str], on_switch: Optional[Callable[[int], Any]] = None) -> None:
        super().__init__(parent, bg=BASE, bd=0, highlightthickness=0)
        self._titles = titles
        self._on_switch = on_switch
        self._current = 0
        self._btns: list[NeuButton] = []
        for idx, title in enumerate(titles):
            btn = NeuButton(
                self,
                text=title,
                command=lambda i=idx: self.select(i),
                accent=(idx == 0),
                dot_color=BLURPLE if idx == 0 else None,
                inset_active=(idx == 0),
                radius=14,
                pad=7,
                font_size=FONT_BODY,
                weight="bold" if idx == 0 else "normal",
                padx=13,
                pady=6,
            )
            btn.pack(side="left", padx=(0, sx(4, parent)))
            self._btns.append(btn)

    def select(self, idx: int) -> None:
        self._current = idx
        for i, btn in enumerate(self._btns):
            active = i == idx
            btn._accent = active
            btn._font = ui_font(FONT_BODY, "bold" if active else "normal", root=self)
            btn.configure(dot_color=BLURPLE if active else None, inset_active=active)
        if self._on_switch:
            _safe_invoke(lambda: self._on_switch(idx), "标签切换")


class NeuSegmentedSwitch(tk.Canvas):
    """新拟物圆角分段胶囊切换器（用于 24h / 7天 切换，支持悬停高亮与键盘焦点）。"""

    def __init__(
        self,
        parent: tk.Widget,
        options: list[tuple[str, Any]],
        initial_value: Any,
        on_change: Optional[Callable[[Any], None]] = None,
    ) -> None:
        self._choices = options
        self._value = initial_value
        self._on_change = on_change
        self._hover_idx: Optional[int] = None
        self._focused = False
        self._font_norm = ui_font(FONT_CAPTION, root=parent)
        self._font_bold = ui_font(FONT_CAPTION, "bold", root=parent)
        self._fobj = tkfont.Font(root=parent, font=self._font_bold)

        pad_x = sx(9, parent)
        gap = sx(2, parent)
        self._seg_h = max(sx(20, parent), self._fobj.metrics("linespace") + sx(5, parent))
        self._rects: list[tuple[float, float, Any, str]] = []
        cur_x = 2.0
        for val, lbl in options:
            sw = max(sx(30, parent), self._fobj.measure(lbl) + pad_x * 2)
            self._rects.append((cur_x, cur_x + sw, val, lbl))
            cur_x += sw + gap
        tot_w = int(math.ceil(cur_x - gap + 2.0))
        tot_h = self._seg_h + 4
        super().__init__(
            parent, width=tot_w, height=tot_h, bg=BASE, bd=0, highlightthickness=0, cursor="hand2", takefocus=1
        )
        self._seg_w = tot_w
        self._seg_tot_h = tot_h
        _, _, self._bg_rows = _CACHE.render_rgb_rows(
            tot_w, tot_h, radius=tot_h // 2, off=3, box_r=2, pad=0, inset=True, shadow_strength=0.65
        )
        self._frame_photo: Optional[tk.PhotoImage] = None
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<KeyPress-Return>", self._on_key_toggle)
        self.bind("<KeyPress-space>", self._on_key_toggle)
        self._redraw()

    def set_surface_bg(self, surface_bg: str) -> None:
        if str(self.cget("bg")) != surface_bg:
            super().configure(bg=surface_bg)
            rgb = hex_to_rgb(surface_bg)
            _, _, self._bg_rows = _CACHE.render_rgb_rows(
                self._seg_w, self._seg_tot_h, radius=self._seg_tot_h // 2, off=3, box_r=2, pad=0, inset=True,
                fill_rgb=rgb, outer_rgb=rgb, shadow_strength=0.65,
            )
            self._redraw()

    def set_value(self, value: Any) -> None:
        if value != self._value:
            self._value = value
            self._redraw()

    def _set_focus(self, val: bool) -> None:
        self._focused = bool(val) and bool(_KB_FOCUS_ACTIVE)
        self._redraw()

    def _idx_at(self, x: float) -> Optional[int]:
        for idx, (x0, x1, _v, _l) in enumerate(self._rects):
            if x0 <= x <= x1:
                return idx
        return None

    def _on_motion(self, e) -> None:
        idx = self._idx_at(e.x)
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._redraw()

    def _on_leave(self, _e=None) -> None:
        if self._hover_idx is not None:
            self._hover_idx = None
            self._redraw()

    def _on_click(self, e) -> None:
        _mark_pointer_focus()
        self._focused = False
        idx = self._idx_at(e.x)
        if idx is not None:
            val = self._rects[idx][2]
            if val != self._value:
                self._value = val
                self._redraw()
                if self._on_change:
                    _safe_invoke(lambda: self._on_change(val), "时间窗切换")

    def _on_key_toggle(self, _e=None) -> None:
        if not self._rects:
            return
        vals = [r[2] for r in self._rects]
        cur = vals.index(self._value) if self._value in vals else 0
        nxt = vals[(cur + 1) % len(vals)]
        self._value = nxt
        self._redraw()
        if self._on_change:
            _safe_invoke(lambda: self._on_change(nxt), "时间窗切换")

    def _redraw(self) -> None:
        rows = [bytearray(rw) for rw in self._bg_rows]
        y0, y1 = 2.0, float(self._seg_tot_h - 2)
        r = (y1 - y0) * 0.5
        for idx, (x0, x1, val, lbl) in enumerate(self._rects):
            active = val == self._value
            hovered = idx == self._hover_idx
            if active or hovered:
                fill_col = TAB_ACTIVE_BG if active else HOVER_PILL_BG
                _blend_aa_capsule_rows(
                    rows, self._seg_w, self._seg_tot_h, x0, y0, x1, y1, r, hex_to_rgb(fill_col)
                )
        if self._focused:
            r_out = self._seg_tot_h * 0.5
            _blend_aa_capsule_rows(
                rows, self._seg_w, self._seg_tot_h,
                1.2, 1.2, self._seg_w - 1.2, self._seg_tot_h - 1.2, r_out, FOCUS_RGB,
                stroke_only=True, half_stroke=0.72,
            )
        data = (
            f"P6\n{self._seg_w} {self._seg_tot_h}\n255\n".encode("ascii")
            + b"".join(bytes(rw) for rw in rows)
        )
        self._frame_photo = tk.PhotoImage(data=data, format="PPM", master=self)
        self.delete("all")
        self.create_image(0, 0, anchor="nw", image=self._frame_photo)
        for idx, (x0, x1, val, lbl) in enumerate(self._rects):
            active = val == self._value
            hovered = idx == self._hover_idx
            self.create_text(
                (x0 + x1) / 2.0,
                (y0 + y1) / 2.0,
                text=lbl,
                fill=BLURPLE_LINK if active else (TEXT if hovered else TEXT_DIM),
                font=self._font_bold if active else self._font_norm,
            )


class NeuScrollbar(tk.Canvas):
    """极简新拟物抗锯齿胶囊滚动条（随主题基底色动态适配，消除默认灰白滑块）。"""

    def __init__(self, parent: tk.Widget, command: Callable[..., Any]) -> None:
        super().__init__(parent, width=sx(14, parent), bg=BASE, bd=0, highlightthickness=0, cursor="hand2", takefocus=0)
        self._command = command
        self._lo = 0.0
        self._hi = 1.0
        self._dragging = False
        self._drag_y0 = 0
        self._drag_lo0 = 0.0
        self._hover = False
        self._thumb_photo: Optional[tk.PhotoImage] = None

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _set_hover(self, h: bool) -> None:
        self._hover = h
        self._redraw()

    def set(self, lo: str | float, hi: str | float) -> None:
        self._lo = max(0.0, min(1.0, float(lo)))
        self._hi = max(0.0, min(1.0, float(hi)))
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w = max(8, self.winfo_width())
        h = max(20, self.winfo_height())
        if self._hi - self._lo >= 0.999:
            return
        y0 = int(self._lo * h)
        y1 = max(y0 + 26, int(self._hi * h))
        if y1 > h:
            y1 = h
            y0 = max(0, h - 26)
        col = BLURPLE_BRAND if (self._hover or self._dragging) else SCROLL_TRACK_IDLE
        pw = max(8, w - 2)
        ph = max(12, y1 - y0 + 2)
        self._thumb_photo = _make_pill_photo(
            max(6, pw - 2), ph, hex_to_rgb(col), hex_to_rgb(BASE), pad_px=1.0, master=self
        )
        self.create_image(max(1, (w - max(6, pw - 2)) // 2), max(0, y0 - 1), anchor="nw", image=self._thumb_photo)

    def _on_press(self, event) -> None:
        h = max(1, self.winfo_height())
        y0 = self._lo * h
        y1 = max(y0 + 26, self._hi * h)
        if y0 <= event.y <= y1:
            self._dragging = True
            self._drag_y0 = event.y
            self._drag_lo0 = self._lo
            self._redraw()
        else:
            span = self._hi - self._lo
            frac = max(0.0, min(max(0.0, 1.0 - span), event.y / h - span / 2.0))
            self._command("moveto", frac)
            self._dragging = True
            self._drag_y0 = event.y
            self._drag_lo0 = frac
            self._redraw()

    def _on_drag(self, event) -> None:
        if not self._dragging:
            return
        h = max(1, self.winfo_height())
        delta = (event.y - self._drag_y0) / h
        span = self._hi - self._lo
        new_lo = max(0.0, min(1.0 - span, self._drag_lo0 + delta))
        self._command("moveto", new_lo)

    def _on_release(self, _e=None) -> None:
        self._dragging = False
        self._redraw()


def _clear_ws_ex_layered(win: tk.Wm) -> None:
    """窗口淡入完全不透明后移除 WS_EX_LAYERED，确保 Win11 原生 DWM 圆角与投影保持锐利。"""
    try:
        hwnd = _root_hwnd(win)
        if not hwnd:
            return
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        get_long = u32.GetWindowLongPtrW if ptr_size == 8 else u32.GetWindowLongW
        set_long = u32.SetWindowLongPtrW if ptr_size == 8 else u32.SetWindowLongW
        ex = int(get_long(wintypes.HWND(hwnd), GWL_EXSTYLE))
        if ex & WS_EX_LAYERED:
            set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
            u32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, 0x0027)
    except Exception:
        pass


def _keep_colorkey_only(win: tk.Wm) -> None:
    """带 -transparentcolor 的对话框淡入完毕后移除 LWA_ALPHA 标志，仅保留 1-bit ColorKey 确保原生键鼠命中灵敏。"""
    if sys.platform != "win32":
        return
    try:
        hwnd = _root_hwnd(win)
        if not hwnd:
            return
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        cr = wintypes.COLORREF(0)
        al = ctypes.c_ubyte(0)
        fl = wintypes.DWORD(0)
        if u32.GetLayeredWindowAttributes(wintypes.HWND(hwnd), ctypes.byref(cr), ctypes.byref(al), ctypes.byref(fl)):
            if (int(fl.value) & 0x1) and (int(fl.value) & 0x2):
                u32.SetLayeredWindowAttributes(wintypes.HWND(hwnd), cr, 255, 0x1)
    except Exception:
        pass


def _activate_floating_dialog(
    dlg: tk.Toplevel,
    target_focus: Optional[tk.Misc],
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    select_all: bool = False,
) -> None:
    """置顶并强制激活 overrideredirect 模态对话框焦点（防止右键菜单销毁后焦点丢失导致输入框/按钮不让点）。"""
    try:
        dlg.attributes("-topmost", True)
        dlg.lift()
    except Exception:
        pass
    hwnd = _root_hwnd(dlg)
    _set_window_pos_topmost(hwnd, x, y, w, h, no_activate=False)
    try:
        dlg.grab_set()
    except Exception:
        pass
    u32 = _setup_win32_popup_funcs()
    if u32 is not None and hwnd:
        try:
            u32.SetForegroundWindow(wintypes.HWND(hwnd))
        except Exception:
            pass
    try:
        dlg.focus_force()
    except Exception:
        pass
    if target_focus is not None and target_focus.winfo_exists():
        try:
            target_focus.focus_force()
            if select_all and isinstance(target_focus, tk.Entry):
                target_focus.icursor("end")
                target_focus.select_range(0, "end")
        except Exception:
            try:
                target_focus.focus_set()
            except Exception:
                pass

    def _reassert() -> None:
        if not dlg.winfo_exists():
            return
        if target_focus is not None and not target_focus.winfo_exists():
            return
        try:
            if dlg.focus_get() is None:
                if u32 is not None and hwnd:
                    u32.SetForegroundWindow(wintypes.HWND(hwnd))
                dlg.focus_force()
                if target_focus is not None and target_focus.winfo_exists():
                    target_focus.focus_force()
                    if select_all and isinstance(target_focus, tk.Entry):
                        target_focus.icursor("end")
                        target_focus.select_range(0, "end")
        except Exception:
            pass

    try:
        dlg.after(30, _reassert)
    except Exception:
        pass


def _restore_parent_modal_grab(parent: tk.Misc) -> None:
    try:
        if parent.winfo_exists():
            top = parent.winfo_toplevel()
            modal = getattr(top, "_neu_active_modal", None)
            if modal is not None and hasattr(modal, "restore_grab"):
                modal.restore_grab()
    except Exception:
        pass


def _present_floating_dialog(
    dlg: tk.Toplevel,
    parent: tk.Misc,
    focus_target: Optional[tk.Misc],
    closing_flag: dict[str, bool],
    *,
    animate: bool = True,
    select_all: bool = False,
) -> None:
    """居中定位、激活置顶焦点并播放可选平滑动效，用于消息确认与重命名弹窗。"""
    dlg.update_idletasks()
    top = parent.winfo_toplevel()
    dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
    wl, wt, wr, wb = get_work_area()
    px = top.winfo_rootx() + max(0, (top.winfo_width() - dw) // 2)
    py = top.winfo_rooty() + max(0, (top.winfo_height() - dh) // 2)
    px = max(wl + 12, min(wr - dw - 12, px))
    py = max(wt + 12, min(wb - dh - 12, py))
    rise = sx(8, dlg) if animate else 0
    dlg.geometry(f"{dw}x{dh}+{px}+{py + rise}")
    if animate:
        try:
            dlg.attributes("-alpha", 0.08)
        except Exception:
            pass
    dlg.deiconify()
    try:
        dlg.attributes("-transparentcolor", TRANSP_KEY)
    except Exception:
        pass
    _activate_floating_dialog(
        dlg,
        focus_target,
        x=px,
        y=py + rise,
        w=dw,
        h=dh,
        select_all=select_all,
    )
    if animate:
        def _slide_enter(step: int = 1, total: int = 6) -> None:
            if closing_flag["done"] or not dlg.winfo_exists():
                return
            t = min(1.0, step / float(total))
            ease = 1.0 - (1.0 - t) ** 3
            cy = int(round(py + rise * (1.0 - ease)))
            try:
                dlg.geometry(f"+{px}+{cy}")
            except Exception:
                pass
            if step < total:
                dlg.after(14, lambda: _slide_enter(step + 1, total))

        _fade_toplevel(dlg, 0.08, 1.0, steps=6, step_ms=14, keep_colorkey_on_done=True)
        _slide_enter()


def _fade_toplevel(
    win: tk.Wm,
    start_alpha: float,
    end_alpha: float,
    *,
    steps: int = 6,
    step_ms: int = 15,
    clear_layered_on_done: bool = False,
    keep_colorkey_on_done: bool = False,
    on_done: Optional[Callable[[], None]] = None,
) -> None:
    """平滑过渡窗体透明度，用于独立对话框、下拉菜单与主窗口显隐的柔润淡入淡出。"""
    prev_aid = getattr(win, "_neu_fade_aid", None)
    if prev_aid is not None:
        try:
            win.after_cancel(prev_aid)  # type: ignore[attr-defined]
        except Exception:
            pass
        setattr(win, "_neu_fade_aid", None)

    def _cancel_fade_aid(event=None) -> None:
        if event is not None and event.widget is not win:
            return
        aid = getattr(win, "_neu_fade_aid", None)
        if aid is not None:
            try:
                win.after_cancel(aid)  # type: ignore[attr-defined]
            except Exception:
                pass
            setattr(win, "_neu_fade_aid", None)

    if not getattr(win, "_neu_fade_destroy_bound", False):
        try:
            win.bind("<Destroy>", _cancel_fade_aid, add="+")  # type: ignore[attr-defined]
            setattr(win, "_neu_fade_destroy_bound", True)
        except Exception:
            pass

    def _finish() -> None:
        setattr(win, "_neu_fade_aid", None)
        if clear_layered_on_done and end_alpha >= 0.99:
            _clear_ws_ex_layered(win)
        elif keep_colorkey_on_done and end_alpha >= 0.99:
            _keep_colorkey_only(win)
        if on_done:
            on_done()

    def _tick(i: int) -> None:
        try:
            if not win.winfo_exists():  # type: ignore[attr-defined]
                setattr(win, "_neu_fade_aid", None)
                return
        except Exception:
            return
        t = min(1.0, i / float(max(1, steps)))
        ease = 1.0 - (1.0 - t) ** 3 if end_alpha >= start_alpha else t * t
        a = start_alpha + (end_alpha - start_alpha) * ease
        try:
            win.attributes("-alpha", max(0.0, min(1.0, a)))  # type: ignore[attr-defined]
        except Exception:
            pass
        if i < steps:
            aid = win.after(step_ms, lambda: _tick(i + 1))  # type: ignore[attr-defined]
            setattr(win, "_neu_fade_aid", aid)
        else:
            _finish()

    try:
        win.attributes("-alpha", max(0.0, min(1.0, start_alpha)))  # type: ignore[attr-defined]
        aid = win.after(step_ms, lambda: _tick(1))  # type: ignore[attr-defined]
        setattr(win, "_neu_fade_aid", aid)
    except Exception:
        _finish()


def fade_in_window(win: tk.Wm, *, steps: int = 7, step_ms: int = 14) -> None:
    """主/调试窗口从透明温和淡入后恢复原生非分层状态。"""
    _fade_toplevel(win, 0.0, 1.0, steps=steps, step_ms=step_ms, clear_layered_on_done=True)


def fade_out_window(win: tk.Wm, *, steps: int = 5, step_ms: int = 14, on_done: Optional[Callable[[], None]] = None) -> None:
    """主窗口温和淡出至托盘。"""
    _fade_toplevel(win, 1.0, 0.0, steps=steps, step_ms=step_ms, on_done=on_done)


def ask_message_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    *,
    kind: str = "info",
    buttons: Optional[list[str]] = None,
    default_idx: int = 0,
    cancel_value: Optional[str] = None,
    danger_button: Optional[str] = None,
    animate: bool = True,
) -> Optional[str]:
    """统一新拟物圆角透明去角模态消息/确认弹窗（支持平滑淡入淡出）。"""
    btn_list = list(buttons or ["确定"])
    result: dict[str, Optional[str]] = {"value": cancel_value}
    closing_flag = {"done": False}

    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    dlg.configure(bg=TRANSP_KEY)
    dlg.transient(parent.winfo_toplevel())
    dlg.overrideredirect(True)
    try:
        dlg.attributes("-transparentcolor", TRANSP_KEY)
    except Exception:
        pass

    card = NeuCard(
        dlg, radius=22, pad=12, inner_pad=(22, 20), off=6, box_r=3, inset=False, outer_bg=TRANSP_KEY
    )
    card.pack(fill="both", expand=True)

    head = tk.Frame(card.content, bg=BASE)
    head.pack(fill="x", pady=(0, sx(8, dlg)))

    col_map = {
        "info": INFO,
        "success": SUCCESS,
        "warn": AMBER_CHARGE,
        "error": RED_DANGER,
        "question": BLURPLE,
    }
    glyph_col = col_map.get(kind, BLURPLE)
    icon_sz = sx(26, dlg)
    badge = tk.Canvas(head, width=icon_sz, height=icon_sz, bg=BASE, bd=0, highlightthickness=0)
    badge.pack(side="left", padx=(0, sx(10, dlg)))
    draw_linear_glyph(badge, icon_sz / 2.0, icon_sz / 2.0, kind, color=glyph_col, scale=icon_sz / 22.0)

    tk.Label(
        head,
        text=title,
        bg=BASE,
        fg=TEXT,
        font=ui_font(FONT_SUBTITLE, "bold", root=dlg),
        anchor="w",
    ).pack(side="left", fill="x", expand=True)

    tk.Label(
        card.content,
        text=message,
        bg=BASE,
        fg=TEXT_MUTED,
        font=ui_font(FONT_CAPTION, root=dlg),
        anchor="w",
        justify="left",
        wraplength=sx(360, dlg),
    ).pack(fill="x", pady=(0, sx(16, dlg)))

    btn_row = tk.Frame(card.content, bg=BASE)
    btn_row.pack(fill="x")

    def _finish_destroy() -> None:
        try:
            dlg.grab_release()
        except Exception:
            pass
        try:
            if dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass
        _restore_parent_modal_grab(parent)

    def _choose(val: Optional[str]) -> None:
        if closing_flag["done"]:
            return
        closing_flag["done"] = True
        result["value"] = val
        if animate:
            _fade_toplevel(dlg, 1.0, 0.0, steps=4, step_ms=14, on_done=_finish_destroy)
        else:
            _finish_destroy()

    focus_btn: Optional[NeuButton] = None
    for idx, label in enumerate(reversed(btn_list)):
        orig_idx = len(btn_list) - 1 - idx
        is_primary = orig_idx == default_idx
        is_danger = (danger_button is not None and label == danger_button)
        btn = NeuButton(
            btn_row,
            text=label,
            command=lambda v=label: _choose(v),
            accent=is_primary and not is_danger,
            danger_fill=is_danger,
            radius=14,
            pad=7,
            padx=15,
            pady=5,
            font_size=FONT_CAPTION,
            weight="bold" if (is_primary or is_danger) else "normal",
        )
        btn.pack(side="right", padx=(sx(6, dlg), 0) if idx > 0 else 0)
        if orig_idx == default_idx and not is_danger:
            focus_btn = btn
        elif orig_idx == default_idx and is_danger:
            # 高危按钮默认不聚焦，防止误触回车
            pass

    def _on_enter(_e=None) -> None:
        idx = max(0, min(len(btn_list) - 1, default_idx))
        _choose(btn_list[idx])

    dlg.bind("<Return>", _on_enter)
    dlg.bind("<Escape>", lambda _e: _choose(cancel_value))
    dlg.protocol("WM_DELETE_WINDOW", lambda: _choose(cancel_value))

    _present_floating_dialog(dlg, parent, focus_btn, closing_flag, animate=animate, select_all=False)
    parent.wait_window(dlg)
    return result["value"]


def ask_confirm_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    *,
    confirm_text: str = "确认",
    cancel_text: str = "取消",
    kind: str = "question",
    danger: bool = False,
) -> bool:
    """统一新拟物风格确认弹窗（主操作在左、取消在右，高危操作默认安全取消聚焦）。"""
    picked = ask_message_dialog(
        parent,
        title,
        message,
        kind=kind,
        buttons=[confirm_text, cancel_text],
        default_idx=1 if danger else 0,
        cancel_value=cancel_text,
        danger_button=confirm_text if danger else None,
    )
    return picked == confirm_text


def ask_rename_dialog(
    parent: tk.Misc,
    title: str,
    prompt: str,
    initialvalue: str = "",
    *,
    animate: bool = True,
) -> Optional[str]:
    """新拟物圆角透明去角模态输入弹窗（支持平滑淡入淡出），用于自定义外设显示名称。"""
    result: dict[str, Optional[str]] = {"value": None}
    closing_flag = {"done": False}
    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    dlg.configure(bg=TRANSP_KEY)
    dlg.transient(parent.winfo_toplevel())
    dlg.overrideredirect(True)
    try:
        dlg.attributes("-transparentcolor", TRANSP_KEY)
    except Exception:
        pass

    card = NeuCard(
        dlg, radius=22, pad=12, inner_pad=(22, 20), off=6, box_r=3, inset=False, outer_bg=TRANSP_KEY
    )
    card.pack(fill="both", expand=True)

    tk.Label(
        card.content,
        text=title,
        bg=BASE,
        fg=TEXT,
        font=ui_font(FONT_SUBTITLE, "bold", root=dlg),
        anchor="w",
    ).pack(fill="x")
    tk.Label(
        card.content,
        text=prompt,
        bg=BASE,
        fg=TEXT_MUTED,
        font=ui_font(FONT_CAPTION, root=dlg),
        anchor="w",
    ).pack(fill="x", pady=(6, 12))

    var = tk.StringVar(value=initialvalue)
    entry_box = NeuEntry(card.content, textvariable=var, width=32)
    entry_box.pack(fill="x", pady=(0, 14))

    btn_row = tk.Frame(card.content, bg=BASE)
    btn_row.pack(fill="x")

    def _finish_destroy() -> None:
        try:
            dlg.grab_release()
        except Exception:
            pass
        try:
            if dlg.winfo_exists():
                dlg.destroy()
        except Exception:
            pass
        _restore_parent_modal_grab(parent)

    def _close_with(val: Optional[str]) -> None:
        if closing_flag["done"]:
            return
        closing_flag["done"] = True
        result["value"] = val
        if animate:
            _fade_toplevel(dlg, 1.0, 0.0, steps=4, step_ms=14, on_done=_finish_destroy)
        else:
            _finish_destroy()

    def _ok(_e=None) -> None:
        _close_with(var.get())

    def _cancel(_e=None) -> None:
        _close_with(None)

    NeuButton(btn_row, text="取消", command=_cancel, radius=14, pad=7, padx=14, pady=5, font_size=FONT_CAPTION).pack(side="right")
    NeuButton(
        btn_row, text="保存名称", command=_ok, accent=True, radius=14, pad=7, padx=16, pady=5, font_size=FONT_CAPTION, weight="bold"
    ).pack(side="right", padx=(0, sx(6, dlg)))

    for w in (card, card._bg_label, card.content):
        try:
            w.bind("<ButtonPress-1>", lambda _e: entry_box.entry.focus_force() if entry_box.entry.winfo_exists() else None, add="+")
        except Exception:
            pass

    dlg.bind("<Return>", _ok)
    dlg.bind("<Escape>", _cancel)
    dlg.protocol("WM_DELETE_WINDOW", _cancel)

    _present_floating_dialog(dlg, parent, entry_box.entry, closing_flag, animate=animate, select_all=True)
    parent.wait_window(dlg)
    return result["value"]


# ==============================================================================
# 无边框统一标题栏 / 线性窗口控制图标 / 模态浮层 / 趋势折线小卡
# ==============================================================================

_SCRIM_PNG_CACHE: dict[tuple[int, int, tuple[int, int, int, int]], str] = {}


def make_alpha_rgba_photo(
    w: int = 32,
    h: int = 32,
    rgba: tuple[int, int, int, int] = (20, 28, 42, 118),
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    """生成带真实 Alpha 通道的纯色 PNG PhotoImage，用于模态半透明暗化遮罩。"""
    key = (w, h, rgba)
    b64 = _SCRIM_PNG_CACHE.get(key)
    if b64 is None:
        if len(_SCRIM_PNG_CACHE) > 24:
            _SCRIM_PNG_CACHE.clear()

        def _chunk(tag: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + tag
                + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
            )

        r, g, b, a = rgba
        row = b"\x00" + bytes((r, g, b, a)) * w
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
        idat = zlib.compress(row * h, level=6)
        png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
        b64 = base64.b64encode(png).decode("ascii")
        _SCRIM_PNG_CACHE[key] = b64
    return tk.PhotoImage(data=b64, master=master)


def get_work_area() -> tuple[int, int, int, int]:
    """返回主显示器工作区 (left, top, right, bottom)，避免浮层或最大化遮挡任务栏。"""
    rect = wintypes.RECT()
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        if u32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        pass
    return 0, 0, 1920, 1040


def _root_hwnd(win: tk.Wm) -> int:
    try:
        win.update_idletasks()
        hwnd = int(win.winfo_id())
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        parent = u32.GetParent(wintypes.HWND(hwnd))
        return int(parent) if parent else hwnd
    except Exception:
        return 0


def apply_window_rounded_corners(
    win: tk.Wm,
    *,
    rounded: bool = True,
    border_color: Optional[str] = None,
) -> bool:
    """为无边框窗口启用与 Windows 11 原生现代应用一致的平滑圆角、边框与深浅色同步。"""
    eff_border = POPUP_BORDER if border_color is None else border_color
    hwnd = _root_hwnd(win)
    if not hwnd:
        return False
    is_win11 = False
    try:
        is_win11 = int(sys.getwindowsversion().build) >= 22000
    except Exception:
        is_win11 = False

    if is_win11:
        try:
            dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            DWMWA_WINDOW_CORNER_PREFERENCE = 33
            DWMWA_BORDER_COLOR = 34
            DWMWCP_DONOTROUND = 1
            DWMWCP_ROUND = 2
            DWMWA_COLOR_DEFAULT = 0xFFFFFFFF

            dark_val = ctypes.c_int(1 if is_dark_theme() else 0)
            dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(dark_val),
                ctypes.sizeof(dark_val),
            )
            pref = ctypes.c_int(DWMWCP_ROUND if rounded else DWMWCP_DONOTROUND)
            hr = dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(pref),
                ctypes.sizeof(pref),
            )
            if rounded and eff_border:
                rr, gg, bb = hex_to_rgb(eff_border)
                colorref = (bb << 16) | (gg << 8) | rr
            else:
                colorref = DWMWA_COLOR_DEFAULT
            bcol = ctypes.c_uint32(colorref)
            dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                DWMWA_BORDER_COLOR,
                ctypes.byref(bcol),
                ctypes.sizeof(bcol),
            )
            if hr == 0:
                return True
        except Exception:
            pass

    # Win10 等旧版回退：通过 CreateRoundRectRgn 裁剪圆角区域
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        g32 = ctypes.WinDLL("gdi32", use_last_error=True)
        if not rounded:
            u32.SetWindowRgn(wintypes.HWND(hwnd), None, True)
            return True
        rc = wintypes.RECT()
        if u32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rc)):
            pw = max(1, rc.right - rc.left)
            ph = max(1, rc.bottom - rc.top)
        else:
            pw = max(1, int(win.winfo_width()))  # type: ignore[attr-defined]
            ph = max(1, int(win.winfo_height()))  # type: ignore[attr-defined]
        dia = max(16, sx(10, win) * 2)  # type: ignore[arg-type]
        hrgn = g32.CreateRoundRectRgn(0, 0, pw + 1, ph + 1, dia, dia)
        if hrgn:
            if not u32.SetWindowRgn(wintypes.HWND(hwnd), hrgn, True):
                g32.DeleteObject(hrgn)
                return False
            return True
    except Exception:
        pass
    return False


def enable_frameless_taskbar(
    win: tk.Wm,
    no_activate: bool = False,
    *,
    round_corners: bool = False,
) -> int:
    """将 Tk 窗口设为无边框且正确出现在 Windows 任务栏（或无焦点浮层）。"""
    win.overrideredirect(True)
    win.update_idletasks()
    hwnd = _root_hwnd(win)
    if not hwnd:
        return 0
    try:
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        GWL_STYLE = -16
        GWL_EXSTYLE = -20
        WS_MINIMIZEBOX = 0x00020000
        WS_SYSMENU = 0x00080000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        WS_EX_NOACTIVATE = 0x08000000

        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        get_long = u32.GetWindowLongPtrW if ptr_size == 8 else u32.GetWindowLongW
        set_long = u32.SetWindowLongPtrW if ptr_size == 8 else u32.SetWindowLongW
        get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

        ex = int(get_long(wintypes.HWND(hwnd), GWL_EXSTYLE))
        st = int(get_long(wintypes.HWND(hwnd), GWL_STYLE))
        if no_activate:
            ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW
        else:
            ex = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            st = st | WS_MINIMIZEBOX | WS_SYSMENU
            set_long(wintypes.HWND(hwnd), GWL_STYLE, st)
        set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, ex)
        # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED | (NOACTIVATE)
        flags = 0x0002 | 0x0001 | 0x0004 | 0x0020 | (0x0010 if no_activate else 0)
        u32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, flags)
    except Exception:
        pass

    if round_corners:
        try:
            setattr(win, "_neu_round_corners", True)
            is_max_fn = getattr(win, "_neu_is_maximized", None)
            should_round = not bool(is_max_fn() if callable(is_max_fn) else False)
            apply_window_rounded_corners(win, rounded=should_round)

            if not getattr(win, "_neu_round_map_bound", False):
                setattr(win, "_neu_round_map_bound", True)

                def _cancel_round_aid() -> None:
                    aid = getattr(win, "_neu_round_aid", None)
                    if aid is not None:
                        try:
                            win.after_cancel(aid)  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        setattr(win, "_neu_round_aid", None)

                def _sync_round(_e=None) -> None:
                    setattr(win, "_neu_round_aid", None)
                    try:
                        if not win.winfo_exists():  # type: ignore[attr-defined]
                            return
                        fn = getattr(win, "_neu_is_maximized", None)
                        rnd = not bool(fn() if callable(fn) else False)
                        apply_window_rounded_corners(win, rounded=rnd)
                    except Exception:
                        pass

                def _on_win_map(event=None) -> None:
                    if event is not None and event.widget is not win:
                        return
                    _cancel_round_aid()
                    try:
                        aid = win.after(20, _sync_round)  # type: ignore[attr-defined]
                        setattr(win, "_neu_round_aid", aid)
                    except Exception:
                        pass

                win.bind("<Map>", _on_win_map, add="+")  # type: ignore[attr-defined]
                win.bind("<Destroy>", lambda e: _cancel_round_aid() if e.widget is win else None, add="+")  # type: ignore[attr-defined]
                try:
                    if int(sys.getwindowsversion().build) < 22000:
                        win.bind("<Configure>", lambda e: _sync_round() if e.widget is win else None, add="+")  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            pass
    return hwnd


def minimize_frameless_window(win: tk.Wm) -> bool:
    """将无边框（overrideredirect）窗口最小化到 Windows 任务栏，避免 Tk iconify 报错。"""
    try:
        dismiss_active_popup_menus(win)  # type: ignore[arg-type]
    except Exception:
        pass
    try:
        win.update_idletasks()  # type: ignore[attr-defined]
    except Exception:
        pass

    hwnd = _root_hwnd(win)
    if hwnd and sys.platform == "win32":
        try:
            u32 = ctypes.WinDLL("user32", use_last_error=True)
            GWL_STYLE = -16
            GWL_EXSTYLE = -20
            WS_MINIMIZEBOX = 0x00020000
            WS_SYSMENU = 0x00080000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            SW_MINIMIZE = 6

            ptr_size = ctypes.sizeof(ctypes.c_void_p)
            get_long = u32.GetWindowLongPtrW if ptr_size == 8 else u32.GetWindowLongW
            set_long = u32.SetWindowLongPtrW if ptr_size == 8 else u32.SetWindowLongW
            get_long.restype = ctypes.c_ssize_t
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            set_long.restype = ctypes.c_ssize_t
            set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]

            st = int(get_long(wintypes.HWND(hwnd), GWL_STYLE))
            ex = int(get_long(wintypes.HWND(hwnd), GWL_EXSTYLE))
            need_style = (st & (WS_MINIMIZEBOX | WS_SYSMENU)) != (WS_MINIMIZEBOX | WS_SYSMENU)
            need_ex = not (ex & WS_EX_APPWINDOW) or bool(ex & WS_EX_TOOLWINDOW)
            if need_style:
                set_long(wintypes.HWND(hwnd), GWL_STYLE, st | WS_MINIMIZEBOX | WS_SYSMENU)
            if need_ex:
                set_long(wintypes.HWND(hwnd), GWL_EXSTYLE, (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW)
            if need_style or need_ex:
                # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
                u32.SetWindowPos(wintypes.HWND(hwnd), None, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0020)

            u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            u32.ShowWindow.restype = wintypes.BOOL
            u32.ShowWindow(wintypes.HWND(hwnd), SW_MINIMIZE)
            return True
        except Exception:
            pass

    try:
        if not win.overrideredirect():
            win.iconify()
            return True
    except Exception:
        pass
    return False


def enable_frameless_resize(
    win: tk.Wm,
    *,
    min_w: int = 640,
    min_h: int = 500,
    border_px: int = 5,
    is_maximized: Optional[Callable[[], bool]] = None,
) -> None:
    """为 overrideredirect(True) 无边框窗口提供四边与四角平滑拖拽缩放支持。"""
    w_root: tk.Misc = win  # type: ignore[assignment]
    corner = max(10, border_px * 2 + 2)
    b = max(4, border_px)
    drag_state: dict[str, Any] = {"active": False}

    def _get_min_wh() -> tuple[int, int]:
        try:
            mw, mh = w_root.minsize()
            return max(320, int(mw or min_w)), max(240, int(mh or min_h))
        except Exception:
            return min_w, min_h

    def _start(mode: str, event) -> None:
        if is_maximized and is_maximized():
            return
        drag_state.update(
            {
                "active": True,
                "mode": mode,
                "rx": int(event.x_root),
                "ry": int(event.y_root),
                "wx": int(w_root.winfo_x()),
                "wy": int(w_root.winfo_y()),
                "ww": int(w_root.winfo_width()),
                "wh": int(w_root.winfo_height()),
            }
        )

    def _motion(event) -> None:
        if not drag_state.get("active"):
            return
        mode = drag_state["mode"]
        dx = int(event.x_root) - drag_state["rx"]
        dy = int(event.y_root) - drag_state["ry"]
        x, y = drag_state["wx"], drag_state["wy"]
        w, h = drag_state["ww"], drag_state["wh"]
        mw, mh = _get_min_wh()

        if "e" in mode:
            w = max(mw, w + dx)
        if "s" in mode:
            h = max(mh, h + dy)
        if "w" in mode:
            nw = max(mw, w - dx)
            x = x + (w - nw)
            w = nw
        if "n" in mode:
            nh = max(mh, h - dy)
            y = y + (h - nh)
            h = nh
        try:
            win.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _end(_event=None) -> None:
        drag_state["active"] = False

    handles: list[tuple[str, str, dict[str, Any]]] = [
        ("n", "size_ns", {"x": corner, "y": 0, "relwidth": 1.0, "width": -2 * corner, "height": b}),
        ("s", "size_ns", {"x": corner, "rely": 1.0, "y": -b, "relwidth": 1.0, "width": -2 * corner, "height": b}),
        ("w", "size_we", {"x": 0, "y": corner, "width": b, "relheight": 1.0, "height": -2 * corner}),
        ("e", "size_we", {"relx": 1.0, "x": -b, "y": corner, "width": b, "relheight": 1.0, "height": -2 * corner}),
        ("nw", "size_nw_se", {"x": 0, "y": 0, "width": corner, "height": corner}),
        ("ne", "size_ne_sw", {"relx": 1.0, "x": -corner, "y": 0, "width": corner, "height": corner}),
        ("sw", "size_ne_sw", {"x": 0, "rely": 1.0, "y": -corner, "width": corner, "height": corner}),
        ("se", "size_nw_se", {"relx": 1.0, "x": -corner, "rely": 1.0, "y": -corner, "width": corner, "height": corner}),
    ]
    edge_frames: list[tk.Frame] = []
    for mode, cur, plc in handles:
        f = tk.Frame(w_root, bg=BASE, bd=0, highlightthickness=0, cursor=cur)
        f.place(**plc)
        f.bind("<ButtonPress-1>", lambda e, m=mode: _start(m, e))
        f.bind("<B1-Motion>", _motion)
        f.bind("<ButtonRelease-1>", _end)
        edge_frames.append(f)

    def _keep_edges_accessible(_e=None) -> None:
        maximized = bool(is_maximized and is_maximized())
        for f in edge_frames:
            try:
                f.configure(cursor="arrow" if maximized else f._orig_cursor)  # type: ignore[attr-defined]
            except Exception:
                pass

    for f, (_, cur, _) in zip(edge_frames, handles):
        f._orig_cursor = cur  # type: ignore[attr-defined]
    w_root.bind("<Configure>", _keep_edges_accessible, add="+")


class _LinearWinBtn(tk.Canvas):
    """简约线性窗口控制按钮（最小化 / 最大化-还原 / 关闭），带悬停提示且无 Windows 方块感。"""

    _TIP_MAP = {
        "min": "最小化",
        "max": "最大化",
        "restore": "向下还原",
        "close": "关闭",
    }

    def __init__(
        self,
        parent: tk.Widget,
        kind: str,
        command: Callable[[], None],
        *,
        size_dp: int = 32,
    ) -> None:
        sz = sx(size_dp, parent)
        w = sx(size_dp + 6, parent)
        super().__init__(
            parent,
            width=w,
            height=sz,
            bg=BASE,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=1,
        )
        self._btn_w = w
        self._btn_h = sz
        self._kind = kind
        self._command = command
        self._restored_mode = False
        self._hover = False
        self._pressed = False
        self._focused = False
        self._photos: dict[str, tk.PhotoImage] = {}

        self._tip = attach_tooltip(self, self._TIP_MAP.get(kind, ""))
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<KeyPress-Return>", lambda _e: self._on_key_activate())
        self.bind("<KeyPress-space>", lambda _e: self._on_key_activate())
        self._redraw()

    def _set_focus(self, focused: bool) -> None:
        self._focused = bool(focused) and bool(_KB_FOCUS_ACTIVE)
        self._redraw()

    def _on_key_activate(self) -> None:
        self._pressed = True
        self._redraw()
        self.after(90, self._finish_key_activate)

    def _finish_key_activate(self) -> None:
        if not self.winfo_exists():
            return
        self._pressed = False
        self._redraw()
        self._command()

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        self._tip.set_text(self._TIP_MAP.get(kind, ""))
        self._redraw()

    def _pill_photo(self, name: str, fill_rgb: tuple[int, int, int], inset: bool) -> tk.PhotoImage:
        key = f"{name}_{_ACTIVE_THEME}"
        if key not in self._photos:
            pad = max(2, sx(2, self))
            bw = max(8, self._btn_w - pad * 2)
            bh = max(8, self._btn_h - pad * 2)
            r = max(6, bh // 3)
            self._photos[key] = make_surface_photo(
                bw,
                bh,
                radius=r,
                pad=pad,
                off=2,
                box_r=2,
                inset=inset,
                fill_rgb=fill_rgb,
                shadow_strength=0.55 if (self._hover or inset) else 0.0,
                light_strength=0.65 if (self._hover or inset) else 0.0,
            )
        return self._photos[key]

    def _on_enter(self, _e=None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _e=None) -> None:
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _e=None) -> None:
        try:
            self.focus_set()
        except Exception:
            pass
        self._focused = False
        self._pressed = True
        self._redraw()

    def _on_release(self, event=None) -> None:
        was = self._pressed
        self._pressed = False
        self._focused = False
        self._redraw()
        if was and event is not None and 0 <= event.x <= self._btn_w and 0 <= event.y <= self._btn_h:
            self._command()

    def _redraw(self) -> None:
        self.delete("all")
        if self._pressed:
            bg_fill = hex_to_rgb(WIN_BTN_CLOSE_PRESS_BG if self._kind == "close" else WIN_BTN_PRESS_BG)
            self.create_image(0, 0, anchor="nw", image=self._pill_photo(f"p_{self._kind}", bg_fill, True))
        elif self._hover:
            bg_fill = hex_to_rgb(WIN_BTN_CLOSE_HOVER_BG if self._kind == "close" else WIN_BTN_HOVER_BG)
            self.create_image(0, 0, anchor="nw", image=self._pill_photo(f"h_{self._kind}", bg_fill, False))

        if self._focused:
            o = max(2, sx(2, self))
            r = max(4.0, (self._btn_h - o * 2) * 0.35)
            sw = 1.4
            pad_px = sw + 2.0
            fw = max(4, int(round(self._btn_w - o * 2 + pad_px)))
            fh = max(4, int(round(self._btn_h - o * 2 + pad_px)))
            self._focus_photo = _make_aa_rounded_outline_photo(
                fw, fh, radius=r, color_hex=BLURPLE_FOCUS, stroke_width=sw, master=self
            )
            self.create_image(o - pad_px / 2.0, o - pad_px / 2.0, anchor="nw", image=self._focus_photo)

        sc = max(1.0, sx(10, self) / 10.0)
        dy = 0.6 if self._pressed else 0.0
        stroke = WIN_BTN_CLOSE_FG if (self._kind == "close" and (self._hover or self._pressed)) else TEXT_MUTED
        self._glyph_photo = _make_win_btn_glyph_photo(
            self._btn_w, self._btn_h, kind=self._kind, color_hex=stroke, sc=sc, dy=dy, master=self
        )
        self.create_image(0, 0, anchor="nw", image=self._glyph_photo)


class NeuTitleBar(tk.Frame):
    """统一新拟物风格无边框顶部标题栏：支持拖拽移动、拖顶贴边最大化、双击最大化与简约线性控制图标。"""

    def __init__(
        self,
        parent: tk.Widget,
        title: str,
        *,
        subtitle: str = "",
        on_minimize: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        allow_maximize: bool = True,
        icon_role: str = "controller",
    ) -> None:
        super().__init__(parent, bg=BASE, bd=0, highlightthickness=0)
        self._title_win = parent.winfo_toplevel()
        setattr(self._title_win, "_neu_is_maximized", self.is_maximized)
        self._on_close_cb = on_close
        self._on_min_cb = on_minimize
        self._allow_max = allow_maximize
        self._maximized = False
        self._normal_geom = ""
        self._drag_origin: Optional[tuple[int, int, int, int]] = None
        self._raw_subtitle = subtitle

        row = tk.Frame(self, bg=BASE, bd=0, highlightthickness=0)
        row.pack(fill="x", padx=sx(18, self), pady=(sx(10, self), sx(4, self)))

        # 右侧控制区先 pack，确保缩放或高 DPI 下永不被标题挤压
        ctrl_box = tk.Frame(row, bg=BASE)
        ctrl_box.pack(side="right", padx=(sx(10, self), 0))

        self._btn_min = _LinearWinBtn(ctrl_box, "min", self._handle_minimize)
        self._btn_min.pack(side="left", padx=(0, sx(4, self)))

        if self._allow_max:
            self._btn_max = _LinearWinBtn(ctrl_box, "max", self._handle_toggle_max)
            self._btn_max.pack(side="left", padx=(0, sx(4, self)))
        else:
            self._btn_max = None

        self._btn_close = _LinearWinBtn(ctrl_box, "close", self._handle_close)
        self._btn_close.pack(side="left")

        left = tk.Frame(row, bg=BASE)
        left.pack(side="left", fill="x", expand=True)

        badge_sz = sx(24, self)
        self._badge = tk.Canvas(
            left, width=badge_sz, height=badge_sz, bg=BASE, bd=0, highlightthickness=0, takefocus=0
        )
        self._badge.pack(side="left", padx=(0, sx(9, self)))
        draw_vector_role_glyph(
            self._badge, badge_sz / 2.0, badge_sz / 2.0, icon_role, color=BLURPLE_BRAND, scale=badge_sz / 26.0
        )

        self._title_lbl = tk.Label(
            left,
            text=title,
            bg=BASE,
            fg=TEXT,
            font=ui_font(FONT_BODY, "bold", root=self),
            anchor="w",
        )
        self._title_lbl.pack(side="left")

        if subtitle:
            self._sub_lbl = tk.Label(
                left,
                text=f"{TEXT_SEP.strip()} {subtitle}",
                bg=BASE,
                fg=TEXT_DIM,
                font=ui_font(FONT_CAPTION, root=self),
                anchor="w",
            )
            self._sub_lbl.pack(side="left", padx=(sx(8, self), 0))
        else:
            self._sub_lbl = None

        # 绑定左键拖拽移动与双击最大化（仅标题区，不影响右侧线性图标按钮）
        drag_widgets: list[tk.Widget] = [self, row, left, self._badge, self._title_lbl]
        if self._sub_lbl is not None:
            drag_widgets.append(self._sub_lbl)
        for w in drag_widgets:
            w.bind("<ButtonPress-1>", self._start_drag)
            w.bind("<B1-Motion>", self._on_drag)
            w.bind("<ButtonRelease-1>", self._end_drag)
            if self._allow_max:
                w.bind("<Double-Button-1>", lambda _e: self._handle_toggle_max())

        # 底部极淡的雕刻分割微光（仅 1px 高光与暗线，保留统一同色基底感）
        sep = tk.Frame(self, bg=SEP_DARK, height=1)
        sep.pack(fill="x", padx=sx(20, self))
        sep_hl = tk.Frame(self, bg=SEP_LIGHT, height=1)
        sep_hl.pack(fill="x", padx=sx(20, self))
        self.bind("<Configure>", self._on_resize, add="+")

    def is_maximized(self) -> bool:
        return self._maximized

    def _on_resize(self, event=None) -> None:
        if self._sub_lbl is None:
            return
        w = int(getattr(event, "width", 0) or self.winfo_width())
        if w <= 0:
            return
        if w < sx(460, self):
            if self._sub_lbl.winfo_manager():
                self._sub_lbl.pack_forget()
        elif not self._sub_lbl.winfo_manager():
            self._sub_lbl.pack(side="left", padx=(sx(8, self), 0))

    def _start_drag(self, event) -> None:
        if self._maximized:
            # 从最大化状态开始拖拽时，先按鼠标位置比例还原至正常窗口尺寸
            self._drag_origin = (event.x_root, event.y_root, -1, -1)
            return
        self._drag_origin = (
            event.x_root,
            event.y_root,
            self._title_win.winfo_x(),
            self._title_win.winfo_y(),
        )

    def _on_drag(self, event) -> None:
        if not self._drag_origin:
            return
        x0, y0, wx0, wy0 = self._drag_origin
        if self._maximized:
            if abs(event.x_root - x0) + abs(event.y_root - y0) < 6:
                return
            self._maximized = False
            if self._btn_max:
                self._btn_max.set_kind("max")
            if getattr(self._title_win, "_neu_round_corners", False):
                apply_window_rounded_corners(self._title_win, rounded=True)
            if self._normal_geom:
                try:
                    wh_part = self._normal_geom.split("+", 1)[0]
                    nw, nh = [int(v) for v in wh_part.split("x")]
                except Exception:
                    nw, nh = 960, 820
                cur_w = max(1, self._title_win.winfo_width())
                ratio = max(0.15, min(0.85, (event.x_root - self._title_win.winfo_x()) / float(cur_w)))
                nx = int(event.x_root - nw * ratio)
                ny = max(0, int(event.y_root - 18))
                self._title_win.geometry(f"{nw}x{nh}+{nx}+{ny}")
                self._drag_origin = (event.x_root, event.y_root, nx, ny)
                return
        nx = wx0 + (event.x_root - x0)
        wl, wt, wr, wb = get_work_area()
        win_w = max(sx(240, self), self._title_win.winfo_width())
        min_vis = min(win_w, sx(160, self))
        nx = max(wl - win_w + min_vis, min(wr - min_vis, nx))
        ny = max(wt - 4, min(wb - sx(42, self), wy0 + (event.y_root - y0)))
        self._title_win.geometry(f"+{nx}+{ny}")

    def _end_drag(self, event=None) -> None:
        was_dragging = self._drag_origin is not None
        self._drag_origin = None
        if was_dragging and self._allow_max and not self._maximized and event is not None:
            _wl, wt, _wr, _wb = get_work_area()
            if event.y_root <= wt + 4:
                self._handle_toggle_max()

    def _handle_minimize(self) -> None:
        if self._on_min_cb:
            self._on_min_cb()
        else:
            minimize_frameless_window(self._title_win)

    def _handle_toggle_max(self) -> None:
        if not self._allow_max:
            return
        if not self._maximized:
            self._normal_geom = self._title_win.geometry()
            left, top, right, bottom = get_work_area()
            w = max(400, right - left)
            h = max(300, bottom - top)
            self._title_win.geometry(f"{w}x{h}+{left}+{top}")
            self._maximized = True
            if self._btn_max:
                self._btn_max.set_kind("restore")
            if getattr(self._title_win, "_neu_round_corners", False):
                apply_window_rounded_corners(self._title_win, rounded=False)
        else:
            if self._normal_geom:
                self._title_win.geometry(self._normal_geom)
            self._maximized = False
            if self._btn_max:
                self._btn_max.set_kind("max")
            if getattr(self._title_win, "_neu_round_corners", False):
                apply_window_rounded_corners(self._title_win, rounded=True)

    def _handle_close(self) -> None:
        if self._on_close_cb:
            self._on_close_cb()
        else:
            self._title_win.destroy()


def draw_linear_glyph(
    canvas: tk.Canvas,
    cx: float,
    cy: float,
    kind: str,
    color: str = TEXT,
    scale: float = 1.0,
) -> None:
    """绘制线性设置齿轮 / 返回箭头等矢量功能图标。"""
    s = max(0.8, scale)
    lw = max(1.6, 1.75 * s)
    if kind == "gear":
        # 8 齿精细线条齿轮
        r_in = 4.6 * s
        r_out = 7.4 * s
        for i in range(8):
            ang = math.radians(i * 45.0)
            ca, sa = math.cos(ang), math.sin(ang)
            canvas.create_line(
                cx + ca * (r_in + 0.3 * s),
                cy + sa * (r_in + 0.3 * s),
                cx + ca * r_out,
                cy + sa * r_out,
                fill=color,
                width=max(1.8, 2.0 * s),
                capstyle="round",
            )
        canvas.create_oval(
            cx - r_in, cy - r_in, cx + r_in, cy + r_in,
            outline=color, width=lw, fill=BASE,
        )
        r_core = 2.0 * s
        canvas.create_oval(
            cx - r_core, cy - r_core, cx + r_core, cy + r_core,
            outline=color, width=max(1.3, 1.4 * s),
        )
    elif kind == "back":
        canvas.create_line(cx - 5.0 * s, cy, cx + 5.0 * s, cy, fill=color, width=lw, capstyle="round")
        canvas.create_line(cx - 1.2 * s, cy - 3.8 * s, cx - 5.0 * s, cy, fill=color, width=lw, capstyle="round")
        canvas.create_line(cx - 1.2 * s, cy + 3.8 * s, cx - 5.0 * s, cy, fill=color, width=lw, capstyle="round")
    elif kind == "refresh":
        canvas.create_arc(
            cx - 5.5 * s, cy - 5.5 * s, cx + 5.5 * s, cy + 5.5 * s,
            start=40, extent=280, style="arc", outline=color, width=lw,
        )
        canvas.create_line(cx + 3.6 * s, cy - 5.8 * s, cx + 5.2 * s, cy - 2.6 * s, fill=color, width=lw, capstyle="round")
        canvas.create_line(cx + 2.0 * s, cy - 2.6 * s, cx + 5.2 * s, cy - 2.6 * s, fill=color, width=lw, capstyle="round")
    elif kind == "info":
        r = 6.8 * s
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=lw)
        canvas.create_oval(cx - 1.0 * s, cy - 3.6 * s, cx + 1.0 * s, cy - 1.6 * s, fill=color, outline="")
        canvas.create_line(cx, cy - 0.3 * s, cx, cy + 4.0 * s, fill=color, width=lw, capstyle="round")
    elif kind == "success":
        r = 6.8 * s
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=lw)
        canvas.create_line(cx - 3.2 * s, cy + 0.3 * s, cx - 0.7 * s, cy + 2.9 * s, fill=color, width=lw, capstyle="round")
        canvas.create_line(cx - 0.7 * s, cy + 2.9 * s, cx + 3.5 * s, cy - 2.4 * s, fill=color, width=lw, capstyle="round")
    elif kind == "warn":
        canvas.create_polygon(
            cx, cy - 6.6 * s, cx + 6.8 * s, cy + 5.6 * s, cx - 6.8 * s, cy + 5.6 * s,
            outline=color, fill="", width=lw, joinstyle="round",
        )
        canvas.create_line(cx, cy - 2.2 * s, cx, cy + 1.6 * s, fill=color, width=lw, capstyle="round")
        canvas.create_oval(cx - 1.0 * s, cy + 3.0 * s, cx + 1.0 * s, cy + 4.8 * s, fill=color, outline="")
    elif kind == "error":
        r = 6.8 * s
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=lw)
        canvas.create_line(cx - 2.8 * s, cy - 2.8 * s, cx + 2.8 * s, cy + 2.8 * s, fill=color, width=lw, capstyle="round")
        canvas.create_line(cx + 2.8 * s, cy - 2.8 * s, cx - 2.8 * s, cy + 2.8 * s, fill=color, width=lw, capstyle="round")
    elif kind == "question":
        r = 6.8 * s
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=lw)
        canvas.create_arc(
            cx - 2.6 * s, cy - 4.0 * s, cx + 2.6 * s, cy + 0.2 * s,
            start=-10, extent=200, style="arc", outline=color, width=lw,
        )
        canvas.create_line(cx, cy + 0.1 * s, cx, cy + 1.6 * s, fill=color, width=lw, capstyle="round")
        canvas.create_oval(cx - 1.0 * s, cy + 3.0 * s, cx + 1.0 * s, cy + 4.8 * s, fill=color, outline="")


class NeuIconButton(tk.Canvas):
    """带线性矢量图标的新拟物凸起按钮（支持禁用态、聚焦留驻、清晰焦点环、按压位移与异常保护）。"""

    def __init__(
        self,
        parent: tk.Widget,
        glyph: str,
        command: Callable[[], None],
        *,
        text: str = "",
        radius: int = 16,
        pad: int = 10,
        padx: int = 12,
        pady: int = 7,
        glyph_color: Optional[str] = None,
        font_size: int = FONT_BODY,
        weight: str = "bold",
        tooltip: str = "",
        accessible_name: str = "",
    ) -> None:
        self._glyph = glyph
        self._text = text
        self._command = command
        self._glyph_color = TEXT if glyph_color is None else glyph_color
        self._radius = max(4, sx(radius, parent))
        self._pad = max(3, sx(pad, parent))
        self._accessible_name = accessible_name or text or tooltip or glyph
        self._font = ui_font(font_size, weight, root=parent)
        self._state = "normal"
        self._hovered = False
        self._pressed = False
        self._focused = False
        self._photos: dict[tuple, tk.PhotoImage] = {}

        f = tkfont.Font(root=parent, font=self._font)
        icon_w = sx(18, parent)
        text_w = f.measure(text) if text else 0
        gap = sx(6, parent) if text else 0
        th = f.metrics("linespace")
        w = max(sx(40, parent), icon_w + gap + text_w + sx(padx, parent) * 2 + self._pad * 2)
        h = max(sx(40, parent), max(icon_w, th) + sx(pady, parent) * 2 + self._pad * 2)

        super().__init__(
            parent,
            width=w,
            height=h,
            bg=BASE,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=1,
        )
        self._btn_w = w
        self._btn_h = h
        self._gap = gap
        self._icon_w = icon_w
        self._text_w = text_w

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", lambda _e: self._set_focus(True))
        self.bind("<FocusOut>", lambda _e: self._set_focus(False))
        self.bind("<Return>", lambda _e: self._on_key_activate())
        self.bind("<space>", lambda _e: self._on_key_activate())
        if tooltip or accessible_name:
            attach_tooltip(self, tooltip or accessible_name)
        _ensure_focus_hooks(self)
        self._redraw()

    def set_disabled(self, disabled: bool) -> None:
        self._state = "disabled" if disabled else "normal"
        if disabled:
            self._hovered = False
            self._pressed = False
        super().configure(cursor="arrow" if disabled else "hand2")
        self._redraw()

    def _on_enter(self, _e=None) -> None:
        if self._state == "disabled":
            return
        self._hovered = True
        self._redraw()

    def _on_leave(self, _e=None) -> None:
        self._hovered = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _e=None) -> None:
        if self._state == "disabled":
            return
        _mark_pointer_focus()
        self._focused = False
        self._pressed = True
        self._redraw()

    def _on_release(self, event=None) -> None:
        was = self._pressed
        self._pressed = False
        self._focused = False
        self._redraw()
        if was and self._state != "disabled" and event is not None and 0 <= event.x <= self._btn_w and 0 <= event.y <= self._btn_h:
            _safe_invoke(self._command, self._accessible_name)

    def _set_focus(self, val: bool) -> None:
        self._focused = bool(val) and bool(_KB_FOCUS_ACTIVE)
        self._redraw()

    def _on_key_activate(self) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        self._redraw()
        self.after(90, self._finish_key_activate)

    def _finish_key_activate(self) -> None:
        if not self.winfo_exists():
            return
        self._pressed = False
        self._redraw()
        if self._state != "disabled":
            _safe_invoke(self._command, self._accessible_name)

    def _draw_rounded_outline(
        self, x0: float, y0: float, x1: float, y1: float, r: float, color: str, width: float = 1.6
    ) -> None:
        pad_px = width + 2.0
        w = max(4, int(round(x1 - x0 + pad_px)))
        h = max(4, int(round(y1 - y0 + pad_px)))
        photo = _make_aa_rounded_outline_photo(
            w, h, radius=r, color_hex=color, stroke_width=width, master=self
        )
        self._outline_photo = photo
        self.create_image(x0 - pad_px / 2.0, y0 - pad_px / 2.0, anchor="nw", image=photo)

    def _redraw(self) -> None:
        self.delete("all")
        if self._state == "disabled":
            off, box_r, inset, ss, ls, dy = 2, 2, False, 0.30, 0.35, 0
        elif self._pressed:
            off, box_r, inset, ss, ls, dy = 4, 3, True, 0.90, 0.90, max(1, sx(1.3, self))
        elif self._hovered:
            off, box_r, inset, ss, ls, dy = 4, 2, False, 0.85, 0.90, 0
        else:
            off, box_r, inset, ss, ls, dy = 6, 4, False, 0.88, 0.95, 0

        key = (off, box_r, inset, self._focused, self._state, _ACTIVE_THEME)
        if key not in self._photos:
            bw = max(8, self._btn_w - self._pad * 2)
            bh = max(8, self._btn_h - self._pad * 2)
            self._photos[key] = make_surface_photo(
                bw,
                bh,
                radius=self._radius,
                pad=self._pad,
                off=off,
                box_r=box_r,
                inset=inset,
                shadow_rgb=FOCUS_SHADOW_RGB if (self._focused and self._state != "disabled") else SHADOW_RGB,
                shadow_strength=ss,
                light_strength=ls,
            )
        self.create_image(0, 0, anchor="nw", image=self._photos[key])

        if self._focused and self._state != "disabled":
            o = max(2, self._pad - sx(3, self))
            self._draw_rounded_outline(
                o, o, self._btn_w - o, self._btn_h - o, self._radius + sx(2, self), BLURPLE_FOCUS, width=1.7
            )

        content_w = self._icon_w + (self._gap + self._text_w if self._text else 0)
        start_x = (self._btn_w - content_w) / 2.0
        gx = start_x + self._icon_w / 2.0
        gy = self._btn_h / 2.0 + dy
        sc = max(0.95, self._icon_w / 18.0)
        if self._state == "disabled":
            col = TEXT_DISABLED
        elif self._hovered and not self._text:
            col = BLURPLE_LINK
        else:
            col = self._glyph_color
        draw_linear_glyph(self, gx, gy, self._glyph, color=col, scale=sc)
        if self._text:
            self.create_text(
                start_x + self._icon_w + self._gap,
                gy,
                text=self._text,
                anchor="w",
                fill=TEXT_DISABLED if self._state == "disabled" else TEXT,
                font=self._font,
            )


_MODAL_SEED_CACHE: dict[tuple, tuple[int, int, list[bytes]]] = {}


def _capture_dimmed_window_stages(
    win: tk.Misc,
    dim_rgb: Optional[tuple[int, int, int]] = None,
    alphas: tuple[int, ...] = (55, 65, 72),
) -> list[tk.PhotoImage]:
    """仅执行 1 次窗口抓屏并用 GDI 柔化模糊 + 多阶磨砂遮罩（自动随浅/深主题适配雾化底色）。"""
    eff_dim = hex_to_rgb(OVERLAY_BACKDROP_BG) if dim_rgb is None else dim_rgb
    out: list[tk.PhotoImage] = []
    try:
        top = win.winfo_toplevel()
        w = int(top.winfo_width())
        h = int(top.winfo_height())
        if w < 64 or h < 64 or not top.winfo_ismapped():
            return out
        wid = int(top.winfo_id())
        hwnd = _root_hwnd(top) or wid
        if not hwnd:
            return out

        u32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        msimg32 = ctypes.WinDLL("msimg32", use_last_error=True)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        hdc_win = u32.GetDC(wintypes.HWND(wid))
        hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
        old_bmp = gdi32.SelectObject(hdc_mem, hbmp)

        # PrintWindow(..., flag=0) 对带子控件的 overrideredirect Tk 窗体最稳定，避免捕获到全黑帧
        ok_pw = bool(u32.PrintWindow(wintypes.HWND(hwnd), hdc_mem, 0))
        if ok_pw:
            has_nonblack = any(
                gdi32.GetPixel(hdc_mem, sx_p, sy_p) not in (0, -1)
                for sx_p, sy_p in ((24, 24), (w // 2, h // 2), (w // 3, h // 3))
            )
            if not has_nonblack:
                ok_pw = False
        if not ok_pw:
            gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_win, 0, 0, 0x00CC0020)
            has_nonblack = any(
                gdi32.GetPixel(hdc_mem, sx_p, sy_p) not in (0, -1)
                for sx_p, sy_p in ((24, 24), (w // 2, h // 2), (w // 3, h // 3))
            )
            if not has_nonblack:
                gdi32.SelectObject(hdc_mem, old_bmp)
                gdi32.DeleteObject(hbmp)
                gdi32.DeleteDC(hdc_mem)
                u32.ReleaseDC(wintypes.HWND(wid), hdc_win)
                return out

        # GDI HALFTONE 缩放大降采样，生成柔和磨砂虚化质感
        sw = max(24, w // 5)
        sh = max(24, h // 5)
        hdc_small = gdi32.CreateCompatibleDC(hdc_win)
        hbmp_small = gdi32.CreateCompatibleBitmap(hdc_win, sw, sh)
        old_small = gdi32.SelectObject(hdc_small, hbmp_small)
        gdi32.SetStretchBltMode(hdc_small, 4)
        gdi32.SetBrushOrgEx(hdc_small, 0, 0, None)
        gdi32.StretchBlt(hdc_small, 0, 0, sw, sh, hdc_mem, 0, 0, w, h, 0x00CC0020)
        gdi32.SetStretchBltMode(hdc_mem, 4)
        gdi32.SetBrushOrgEx(hdc_mem, 0, 0, None)
        gdi32.StretchBlt(hdc_mem, 0, 0, w, h, hdc_small, 0, 0, sw, sh, 0x00CC0020)
        gdi32.SelectObject(hdc_small, old_small)
        gdi32.DeleteObject(hbmp_small)
        gdi32.DeleteDC(hdc_small)

        hdc_tint = gdi32.CreateCompatibleDC(hdc_win)
        hbmp_tint = gdi32.CreateCompatibleBitmap(hdc_win, 1, 1)
        old_tint = gdi32.SelectObject(hdc_tint, hbmp_tint)
        colorref = eff_dim[0] | (eff_dim[1] << 8) | (eff_dim[2] << 16)
        gdi32.SetPixel(hdc_tint, 0, 0, colorref)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0
        raw_bgra = ctypes.create_string_buffer(w * h * 4)
        header = f"P6\n{w} {h}\n255\n".encode("ascii")

        for step_alpha in alphas:
            blend_fn = (step_alpha & 0xFF) << 16
            msimg32.AlphaBlend(hdc_mem, 0, 0, w, h, hdc_tint, 0, 0, 1, 1, blend_fn)
            gdi32.GetDIBits(hdc_mem, hbmp, 0, h, raw_bgra, ctypes.byref(bmi), 0)
            bgra_mv = memoryview(raw_bgra.raw)
            rgb = bytearray(w * h * 3)
            rgb[0::3] = bgra_mv[2::4]
            rgb[1::3] = bgra_mv[1::4]
            rgb[2::3] = bgra_mv[0::4]
            out.append(tk.PhotoImage(data=header + bytes(rgb), master=top))

        gdi32.SelectObject(hdc_tint, old_tint)
        gdi32.DeleteObject(hbmp_tint)
        gdi32.DeleteDC(hdc_tint)
        gdi32.SelectObject(hdc_mem, old_bmp)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        u32.ReleaseDC(wintypes.HWND(wid), hdc_win)
        return out
    except Exception:
        return []


def _capture_dimmed_window_ppm(
    win: tk.Misc,
    dim_rgb: Optional[tuple[int, int, int]] = None,
    dim_alpha: int = 160,
) -> Optional[tk.PhotoImage]:
    """捕获当前窗口画面并生成磨砂模糊遮罩，用作模态背景层。"""
    stages = _capture_dimmed_window_stages(win, dim_rgb=dim_rgb, alphas=(dim_alpha,))
    return stages[-1] if stages else None


def _make_rounded_modal_panel_png(
    w: int,
    h: int,
    *,
    radius: int = 28,
    pad: int = 18,
    box_r: int = 5,
    off_y: int = 6,
    master: Optional[tk.Misc] = None,
) -> tk.PhotoImage:
    """生成带真实透明 Alpha 外阴影的无直角矩形圆角浮层卡片 PNG。"""
    key = (radius, pad, box_r, off_y, BASE_RGB, _ACTIVE_THEME)
    seed = _MODAL_SEED_CACHE.get(key)
    if seed is None:
        reach = abs(off_y) + 3 * box_r
        cut = pad + radius + reach
        span = max(2 * (radius + reach) + 8, 2 * radius + 16)
        sz = pad * 2 + span
        if cut * 2 + 2 >= sz:
            sz = cut * 2 + 4
            span = sz - pad * 2
        cx = cy = sz / 2.0
        hw = hh = span / 2.0
        rad = min(float(radius), hw - 1.0, hh - 1.0)
        cov: list[list[float]] = []
        for y in range(sz):
            py = y + 0.5
            row: list[float] = []
            for x in range(sz):
                d = _sdf_rounded_box(x + 0.5, py, cx, cy, hw, hh, rad)
                row.append(max(0.0, min(1.0, 0.5 - d)))
            cov.append(row)
        blur = _blur_2d(cov, box_r, passes=3)
        rows_rgba: list[bytes] = []
        fr, fg, fb = BASE_RGB
        sr, sg, sb = (8, 10, 16) if is_dark_theme() else (72, 84, 108)
        for y in range(sz):
            rb = bytearray()
            sy = y - off_y
            for x in range(sz):
                c = cov[y][x]
                sh = blur[sy][x] if 0 <= sy < sz else 0.0
                # 轻柔通透的冷灰扩散外影（适配浅雾磨砂背景，不产生厚重黑边）
                sa = min(1.0, sh * 0.30)
                if c >= 0.999:
                    rb.extend((fr, fg, fb, 255))
                elif c > 0.0:
                    out_a = c + sa * (1.0 - c)
                    if out_a <= 1e-6:
                        rb.extend((0, 0, 0, 0))
                    else:
                        rr = int((fr * c + sr * sa * (1.0 - c)) / out_a + 0.5)
                        rg = int((fg * c + sg * sa * (1.0 - c)) / out_a + 0.5)
                        rbl = int((fb * c + sb * sa * (1.0 - c)) / out_a + 0.5)
                        rb.extend((rr, rg, rbl, int(out_a * 255.0 + 0.5)))
                else:
                    ai = int(sa * 255.0 + 0.5)
                    rb.extend((sr, sg, sb, ai) if ai > 0 else (0, 0, 0, 0))
            rows_rgba.append(bytes(rb))
        seed = (sz, cut, rows_rgba)
        _MODAL_SEED_CACHE[key] = seed

    sz, cut, rows_rgba = seed
    dw = max(cut * 2 + 2, w)
    dh = max(cut * 2 + 2, h)
    mid_w = dw - cut * 2
    mid_h = dh - cut * 2
    cut_b = cut * 4

    def _assemble_row(src: bytes) -> bytes:
        mid_px = src[cut_b : cut_b + 4]
        return b"\x00" + src[:cut_b] + mid_px * mid_w + src[-cut_b:]

    raw_rows: list[bytes] = []
    for y in range(cut):
        raw_rows.append(_assemble_row(rows_rgba[y]))
    mid_row = _assemble_row(rows_rgba[cut])
    for _ in range(mid_h):
        raw_rows.append(mid_row)
    for y in range(sz - cut, sz):
        raw_rows.append(_assemble_row(rows_rgba[y]))

    def _chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", dw, dh, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"".join(raw_rows), level=1)
    png = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    return tk.PhotoImage(data=base64.b64encode(png).decode("ascii"), master=master)


class NeuModalOverlay(tk.Canvas):
    """主窗口内嵌磨砂暗化模态浮层：带真焦点捕获与回绕循环、防逃逸、可滚动正文与顺滑入退场。"""

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        *,
        subtitle: str = "",
        max_width_dp: int = 540,
        min_height_dp: int = 0,
        dim_photo: Optional[tk.PhotoImage] = None,
        dim_stages: Optional[list[tk.PhotoImage]] = None,
        fade_scrim_on_close: bool = True,
        on_close: Optional[Callable[[], None]] = None,
        close_on_backdrop: bool = True,
        animate: bool = True,
    ) -> None:
        super().__init__(master, bd=0, highlightthickness=0, bg=OVERLAY_BACKDROP_BG, takefocus=0)
        self._master = master
        self._on_close = on_close
        self._close_on_backdrop = close_on_backdrop
        self._max_w = sx(max_width_dp, master)
        self._min_h = sx(min_height_dp, master) if min_height_dp > 0 else 0
        self._animate = bool(animate)
        self._fresh_scrim = (dim_photo is None and dim_stages is None)
        self._fade_scrim_on_close = bool(fade_scrim_on_close)
        try:
            top = master.winfo_toplevel()
            self._captured_size = (max(1, int(top.winfo_width())), max(1, int(top.winfo_height())))
            self._prev_focus = master.focus_get()
        except Exception:
            top = None
            self._captured_size = (0, 0)
            self._prev_focus = None
        if dim_stages:
            self._dim_stages = list(dim_stages)
            self._dim_photo: Optional[tk.PhotoImage] = dim_photo or self._dim_stages[-1]
        elif dim_photo is not None:
            self._dim_stages = [dim_photo]
            self._dim_photo = dim_photo
        elif animate:
            self._dim_stages = _capture_dimmed_window_stages(master)
            self._dim_photo = self._dim_stages[-1] if self._dim_stages else None
        else:
            p_one = _capture_dimmed_window_ppm(master)
            self._dim_stages = [p_one] if p_one is not None else []
            self._dim_photo = p_one
        self._scrim_idx = 0 if (animate and self._fresh_scrim and len(self._dim_stages) > 1) else max(0, len(self._dim_stages) - 1)
        self._scrim_fallback_sz: tuple[int, int] = (-1, -1)
        self._scrim_fallback_photo: Optional[tk.PhotoImage] = None
        self._panel_photo: Optional[tk.PhotoImage] = None
        self._panel_cache_sz: tuple[int, int, str] = (-1, -1, _ACTIVE_THEME)
        self._card_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._closed = False
        self._closing_anim = False
        self._presented = not animate
        self._present_id: Optional[str] = None
        self._opened_at = time.monotonic()
        self._enter_span = float(sx(20 if self._fresh_scrim else 14, master))
        self._anim_dy: float = self._enter_span if animate else 0.0
        self._anim_id: Optional[str] = None
        self._wheel_accum: float = 0.0
        self._esc_bind_id: Optional[str] = None
        self._tab_bind_id: Optional[str] = None
        self._stab_bind_id: Optional[str] = None

        self.place(relx=0.0, rely=0.0, relwidth=1.0, relheight=1.0)
        tk.Widget.tkraise(self)

        for seq in ("<Button-1>", "<Button-2>", "<Button-3>", "<MouseWheel>", "<Double-Button-1>"):
            self.bind(seq, self._on_backdrop_event)

        # 内容 Frame 居中放在透明圆角 PNG 阴影卡片内（零直角边角溢色）
        self._content_wrap = tk.Frame(self, bg=BASE, bd=0, highlightthickness=0)
        self._win_id = self.create_window(0, 0, window=self._content_wrap, anchor="center")

        header = tk.Frame(self._content_wrap, bg=BASE)
        header.pack(fill="x", pady=(0, sx(10, self)))

        self._btn_back = NeuIconButton(
            header,
            "back",
            self.close_animated,
            text="返回",
            radius=16,
            pad=8,
            padx=12,
            pady=5,
            font_size=FONT_BODY,
            tooltip="返回上一界面 (Esc)",
            accessible_name="返回",
        )
        self._btn_back.pack(side="left")

        title_col = tk.Frame(header, bg=BASE)
        title_col.pack(side="left", fill="x", expand=True, padx=(sx(12, self), 0))
        lbl_title = tk.Label(
            title_col,
            text=title,
            bg=BASE,
            fg=TEXT,
            font=ui_font(FONT_TITLE, "bold", root=self),
            anchor="w",
        )
        lbl_title.pack(fill="x")
        lbl_sub = None
        if subtitle:
            lbl_sub = tk.Label(
                title_col,
                text=subtitle,
                bg=BASE,
                fg=TEXT_MUTED,
                font=ui_font(FONT_CAPTION, root=self),
                anchor="w",
            )
            lbl_sub.pack(fill="x", pady=(2, 0))

        sep1 = tk.Frame(self._content_wrap, bg=SEP_DARK, height=1)
        sep1.pack(fill="x", pady=(0, 1))
        sep2 = tk.Frame(self._content_wrap, bg=SEP_LIGHT, height=1)
        sep2.pack(fill="x", pady=(0, sx(8, self)))

        # 自适应可滚动 body：小窗口高度下超长内容自动启用滚轮与胶囊滚动条
        self._body_host = tk.Frame(self._content_wrap, bg=BASE)
        self._body_host.pack(fill="both", expand=True, pady=(0, sx(4, self)))
        self._body_canvas = tk.Canvas(
            self._body_host, bg=BASE, bd=0, highlightthickness=0, takefocus=0, yscrollincrement=sx(16, self)
        )
        self._body_sb = NeuScrollbar(self._body_host, command=self._body_canvas.yview)
        self._body_canvas.configure(yscrollcommand=self._body_sb.set)
        self._body_canvas.pack(side="left", fill="both", expand=True)

        self.body = tk.Frame(self._body_canvas, bg=BASE)
        self._body_win_id = self._body_canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._last_body_req_h: int = 0
        self._relayout_aid: Optional[str] = None
        self.body.bind("<Configure>", self._on_body_configure)
        for wheel_w in (
            self._content_wrap, header, title_col, lbl_title, sep1, sep2, self._body_host, self._body_canvas, self._body_sb, self.body
        ):
            wheel_w.bind("<MouseWheel>", self._on_body_wheel, add="+")
            setattr(wheel_w, "_neu_wheel_bound", True)
        if lbl_sub is not None:
            lbl_sub.bind("<MouseWheel>", self._on_body_wheel, add="+")
            setattr(lbl_sub, "_neu_wheel_bound", True)

        self.bind("<Configure>", self._relayout)
        if top is not None:
            setattr(top, "_neu_active_modal", self)
            self._esc_bind_id = top.bind("<Escape>", self._on_escape, add="+")
            self._tab_bind_id = top.bind("<KeyPress-Tab>", lambda e: self._on_tab_cycle(False), add="+")
            self._stab_bind_id = top.bind("<Shift-KeyPress-Tab>", lambda e: self._on_tab_cycle(True), add="+")

        self.restore_grab()
        self._focus_init_aid: Optional[str] = None
        self.bind("<Destroy>", self._on_overlay_destroy, add="+")
        self._sync_wheel_tree()
        if animate:
            # 延后至调用方把主体控件全部构建完成后的首个空闲帧再一并显形滑入，避免半成品高度闪烁
            self._present_id = self.after_idle(self._begin_present)
        else:
            self._focus_init_aid = self.after_idle(self._focus_initial_child)

    def _on_overlay_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        for attr in ("_present_id", "_anim_id", "_focus_init_aid", "_relayout_aid"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._closed = True

    def _sync_wheel_tree(self) -> None:
        """递归为 modal.body 内全部后代控件补齐滚轮转发，彻底消除标签/卡片死区。"""
        if self._closed or not self.winfo_exists():
            return
        try:
            stack: list[tk.Widget] = [self.body]
            while stack:
                cur = stack.pop()
                if not getattr(cur, "_neu_wheel_bound", False):
                    setattr(cur, "_neu_wheel_bound", True)
                    try:
                        cur.bind("<MouseWheel>", self._on_body_wheel, add="+")
                    except Exception:
                        pass
                try:
                    stack.extend(cur.winfo_children())
                except Exception:
                    pass
        except Exception:
            pass

    def restore_grab(self) -> None:
        if self._closed or not self.winfo_exists():
            return
        try:
            self.grab_set()
        except Exception:
            pass

    def _collect_focusables(self, parent_w: Optional[tk.Widget] = None) -> list[tk.Widget]:
        root_w = self if parent_w is None else parent_w
        items: list[tk.Widget] = []
        try:
            for ch in root_w.winfo_children():
                if not ch.winfo_exists():
                    continue
                if ch is self._body_sb:
                    continue
                try:
                    tf = str(ch.cget("takefocus"))
                except Exception:
                    tf = "0"
                is_entry = isinstance(ch, tk.Entry)
                disabled = False
                try:
                    if str(getattr(ch, "_state", "normal")) == "disabled" or str(ch.cget("state")) == "disabled":
                        disabled = True
                except Exception:
                    pass
                if not disabled and (is_entry or tf == "1"):
                    items.append(ch)
                items.extend(self._collect_focusables(ch))
        except Exception:
            pass
        return items

    def _focus_initial_child(self) -> None:
        if self._closed or not self.winfo_exists():
            return
        if not _KB_FOCUS_ACTIVE:
            try:
                self.winfo_toplevel().focus_set()
            except Exception:
                pass
            return
        body_focusables = self._collect_focusables(self.body)
        target = body_focusables[0] if body_focusables else self._btn_back
        try:
            target.focus_set()
        except Exception:
            pass

    def _on_tab_cycle(self, backward: bool = False) -> str:
        if self._closed or not self.winfo_exists():
            return "break"
        _mark_keyboard_focus()
        items = self._collect_focusables(self)
        if not items:
            return "break"
        try:
            cur = self.focus_get()
        except Exception:
            cur = None
        if cur in items:
            idx = items.index(cur)
            nxt = items[(idx - 1) % len(items)] if backward else items[(idx + 1) % len(items)]
        else:
            nxt = items[-1] if backward else items[0]
        try:
            nxt.focus_set()
        except Exception:
            pass
        return "break"

    def _close_child_selects(self) -> None:
        try:
            dismiss_active_popup_menus(self)
        except Exception:
            pass

    def _begin_present(self) -> None:
        self._present_id = None
        if self._closed or self._closing_anim or not self.winfo_exists():
            return
        self._presented = True
        self._sync_wheel_tree()
        self._relayout()
        self.restore_grab()
        self._focus_initial_child()
        self._start_enter_anim()

    def _on_body_configure(self, _e=None) -> None:
        if self._closed:
            return
        self._sync_wheel_tree()
        try:
            self._body_canvas.configure(scrollregion=self._body_canvas.bbox("all"))
            req_h = max(1, int(self.body.winfo_reqheight()))
            if req_h != self._last_body_req_h and self._presented:
                self._last_body_req_h = req_h
                if self._relayout_aid is None:
                    self._relayout_aid = self.after_idle(self._run_deferred_relayout)
        except Exception:
            pass

    def _run_deferred_relayout(self) -> None:
        self._relayout_aid = None
        if not self._closed and self.winfo_exists():
            self._relayout()

    def _on_body_wheel(self, e=None) -> Optional[str]:
        if self._closed:
            return "break"
        self._close_child_selects()
        if not self._body_sb.winfo_manager():
            return "break"
        try:
            w = getattr(e, "widget", None)
            delta = float(getattr(e, "delta", 0))
            while w is not None and w is not self.body:
                if isinstance(w, tk.Text) or (hasattr(w, "yview") and w.winfo_class() in ("Treeview", "Listbox", "Text")):
                    lo, hi = (float(x) for x in w.yview()[:2])
                    if (hi - lo) < 0.999:
                        if (delta > 0 and lo > 0.001) or (delta < 0 and hi < 0.999):
                            return None
                    break
                w = getattr(w, "master", None)
        except Exception:
            pass
        self._wheel_accum += (-float(getattr(e, "delta", 0)) / 120.0) * 3.0
        steps = int(self._wheel_accum)
        if steps != 0:
            self._wheel_accum -= steps
            self._body_canvas.yview_scroll(steps, "units")
        return "break"

    def _set_scrim_stage(self, idx: int) -> None:
        if not self._dim_stages:
            return
        idx = max(0, min(len(self._dim_stages) - 1, idx))
        if idx != self._scrim_idx:
            self._scrim_idx = idx
            try:
                self.itemconfigure("scrim", image=self._dim_stages[idx])
            except Exception:
                pass

    def _start_enter_anim(self) -> None:
        steps = 10
        span = self._enter_span
        n_stages = len(self._dim_stages)

        def _tick(step: int) -> None:
            if self._closed or self._closing_anim or not self.winfo_exists():
                return
            t = min(1.0, step / float(steps))
            ease = 1.0 - (1.0 - t) ** 4
            self._anim_dy = span * (1.0 - ease)
            if self._fresh_scrim and n_stages > 1:
                st = min(n_stages - 1, int(step * (n_stages - 1) / 4))
                self._set_scrim_stage(st)
            self._apply_center_offset()
            if step < steps:
                self._anim_id = self.after(15, lambda: _tick(step + 1))
            else:
                self._anim_dy = 0.0
                if self._fresh_scrim and n_stages > 1:
                    self._set_scrim_stage(n_stages - 1)
                self._apply_center_offset()
                self._anim_id = None

        self._anim_id = self.after(15, lambda: _tick(1))

    def close_animated(self) -> None:
        """播放柔和滑出与渐进退暗动画后关闭，保持打开与返回均自然顺滑。"""
        if self._closed or self._closing_anim:
            return
        self._close_child_selects()
        self._closing_anim = True
        self._presented = True
        if self._present_id is not None:
            try:
                self.after_cancel(self._present_id)
            except Exception:
                pass
            self._present_id = None
        if self._anim_id is not None:
            try:
                self.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None
        steps = 7
        span = float(sx(18, self))
        n_stages = len(self._dim_stages)

        def _tick(step: int) -> None:
            if self._closed or not self.winfo_exists():
                return
            t = min(1.0, step / float(steps))
            ease = t * t
            self._anim_dy = span * ease
            if self._fade_scrim_on_close and n_stages > 1 and step >= 3:
                rem = steps - step
                st = max(0, min(n_stages - 1, rem))
                self._set_scrim_stage(st)
            self._apply_center_offset()
            if step < steps:
                self._anim_id = self.after(14, lambda: _tick(step + 1))
            else:
                self._anim_id = None
                self.close()

        self._anim_id = self.after(14, lambda: _tick(1))

    def _apply_center_offset(self) -> None:
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())
        if w <= 1 or h <= 1:
            return
        cy = (h / 2.0 + self._anim_dy) if self._presented else -10000.0
        try:
            self.coords("panel_bg", w / 2.0, cy)
            self.coords(self._win_id, w / 2.0, cy)
        except Exception:
            pass

    def _on_backdrop_event(self, event=None) -> str:
        if event is not None and event.type == tk.EventType.MouseWheel:
            return self._on_body_wheel(event)
        if (
            self._close_on_backdrop
            and not self._closing_anim
            and (time.monotonic() - self._opened_at) >= 0.18
            and event is not None
            and event.type == tk.EventType.ButtonPress
            and getattr(event, "num", 0) == 1
        ):
            x0, y0, x1, y1 = self._card_bounds
            if not (x0 <= event.x <= x1 and y0 <= event.y <= y1):
                self.close_animated()
        return "break"

    def _on_escape(self, _e=None) -> Optional[str]:
        if not self._closed and (time.monotonic() - self._opened_at) > 0.08:
            self.close_animated()
            return "break"
        return None

    def _relayout(self, _e=None) -> None:
        if self._closed:
            return
        w = max(1, self.winfo_width())
        h = max(1, self.winfo_height())

        # 窗口尺寸改变时丢弃过期的静帧截图，无缝切换至全屏尺寸实时半透明雾化遮罩，杜绝留白或错位
        if self._dim_stages or self._dim_photo is not None:
            cw, ch = self._captured_size
            if cw > 1 and ch > 1 and (abs(w - cw) > 2 or abs(h - ch) > 2):
                self._dim_stages = []
                self._dim_photo = None

        # 1) 绘制磨砂背景遮罩（窗口未改变尺寸时用捕获帧，尺寸改变或缺失时用同尺寸半透明雾化图）
        self.delete("scrim")
        if self._dim_stages:
            idx = max(0, min(len(self._dim_stages) - 1, self._scrim_idx))
            self.create_image(0, 0, anchor="nw", image=self._dim_stages[idx], tags="scrim")
        elif self._dim_photo is not None:
            self.create_image(0, 0, anchor="nw", image=self._dim_photo, tags="scrim")
        else:
            if (w, h) != self._scrim_fallback_sz:
                self._scrim_fallback_sz = (w, h)
                br, bg_c, bb = hex_to_rgb(OVERLAY_BACKDROP_BG)
                self._scrim_fallback_photo = make_alpha_rgba_photo(w, h, (br, bg_c, bb, 182), master=self)
            if self._scrim_fallback_photo is not None:
                self.create_image(0, 0, anchor="nw", image=self._scrim_fallback_photo, tags="scrim")

        # 2) 计算圆角浮层尺寸与真实透明外影底图（遵循 8dp/16dp 留白节奏），并支持超限高度自动开启内部滚动条
        shadow_pad = sx(16, self)
        inner_pad = sx(16, self)
        content_w = min(self._max_w, max(sx(340, self), w - sx(64, self)))
        max_h = max(sx(220, self), h - sx(72, self))

        self.body.update_idletasks()
        body_req_h = max(1, self.body.winfo_reqheight())
        # 先测得 header 高度
        chrome_h = sx(66, self)
        try:
            kids = self._content_wrap.winfo_children()
            if len(kids) >= 3:
                chrome_h = sum(k.winfo_reqheight() for k in kids[:3]) + sx(28, self)
        except Exception:
            pass
        max_body_h = max(sx(120, self), max_h - chrome_h)
        min_body_h = max(0, self._min_h - chrome_h) if self._min_h > 0 else 0
        need_scroll = body_req_h > max_body_h
        if need_scroll:
            if not self._body_sb.winfo_manager():
                self._body_sb.pack(side="right", fill="y", padx=(4, 0))
            sb_w = sx(18, self)
            body_vis_h = max_body_h
            self._body_canvas.configure(width=max(120, content_w - sb_w), height=body_vis_h)
            self._body_canvas.itemconfigure(
                self._body_win_id, width=max(120, content_w - sb_w), height=body_req_h
            )
            self._body_canvas.configure(scrollregion=(0, 0, max(120, content_w - sb_w), body_req_h))
        else:
            if self._body_sb.winfo_manager():
                self._body_sb.pack_forget()
            body_vis_h = min(max_body_h, max(min_body_h, body_req_h))
            self._body_canvas.configure(width=content_w, height=body_vis_h)
            self._body_canvas.itemconfigure(self._body_win_id, width=content_w, height=body_vis_h)
            self._body_canvas.configure(scrollregion=(0, 0, content_w, body_vis_h))

        content_h = min(max_h, chrome_h + body_vis_h)
        self.itemconfigure(self._win_id, width=content_w, height=content_h)

        panel_w = content_w + inner_pad * 2 + shadow_pad * 2
        panel_h = content_h + inner_pad * 2 + shadow_pad * 2
        if (panel_w, panel_h, _ACTIVE_THEME) != self._panel_cache_sz:
            self._panel_cache_sz = (panel_w, panel_h, _ACTIVE_THEME)
            self._panel_photo = _make_rounded_modal_panel_png(
                panel_w, panel_h, radius=28, pad=shadow_pad, box_r=5, off_y=6, master=self
            )

        cy = (h / 2.0 + self._anim_dy) if self._presented else -10000.0
        self.delete("panel_bg")
        if self._panel_photo is not None:
            self.create_image(w / 2.0, cy, anchor="center", image=self._panel_photo, tags="panel_bg")

        self._card_bounds = (
            int(w / 2.0 - content_w / 2.0 - inner_pad),
            int(h / 2.0 - content_h / 2.0 - inner_pad),
            int(w / 2.0 + content_w / 2.0 + inner_pad),
            int(h / 2.0 + content_h / 2.0 + inner_pad),
        )
        self.coords(self._win_id, w / 2.0, cy)
        self.tag_lower("scrim")
        self.tag_raise("panel_bg", "scrim")
        self.tag_raise(self._win_id)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_child_selects()
        for attr in ("_present_id", "_anim_id", "_focus_init_aid", "_relayout_aid"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            top = self.winfo_toplevel()
            if getattr(top, "_neu_active_modal", None) is self:
                delattr(top, "_neu_active_modal")
            for seq, attr in (("<Escape>", "_esc_bind_id"), ("<KeyPress-Tab>", "_tab_bind_id"), ("<Shift-KeyPress-Tab>", "_stab_bind_id")):
                bid = getattr(self, attr, None)
                if bid is not None:
                    try:
                        if top.winfo_exists():
                            top.unbind(seq, bid)
                    except Exception:
                        pass
                    setattr(self, attr, None)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        if _KB_FOCUS_ACTIVE and self._prev_focus is not None:
            try:
                if self._prev_focus.winfo_exists():
                    self._prev_focus.focus_set()
            except Exception:
                pass
        else:
            try:
                top_w = self._master.winfo_toplevel()
                if top_w.winfo_exists():
                    top_w.focus_set()
            except Exception:
                pass
        if self._on_close:
            self._on_close()


class NeuSparkline(tk.Frame):
    """外设电量历史趋势微卡片：字号自适应高度、24h/7d 分段切换、悬停数据点读数与极值提示。"""

    @staticmethod
    def _normalize_hours(hours: int) -> int:
        val = int(hours)
        if val >= 720:
            return 720
        if val >= 336:
            return 336
        if val >= 168:
            return 168
        return 24

    def _more_btn_label(self) -> str:
        if self._hours == 336:
            return "14天 ▾"
        if self._hours == 720:
            return "30天 ▾"
        return "更多 ▾"

    def _sync_more_btn(self) -> None:
        is_more = self._hours in (336, 720)
        self._more_btn._text = self._more_btn_label()
        self._more_btn._custom_fg = BLURPLE_BRAND if is_more else TEXT_MUTED
        self._more_btn._weight = "bold" if is_more else "normal"
        self._more_btn._font = ui_font(FONT_CAPTION, weight=self._more_btn._weight, root=self._more_btn)
        self._more_btn._inset_active = is_more
        self._more_btn._resize_and_render()

    def _open_more_menu(self) -> None:
        if self._more_menu is not None:
            try:
                if self._more_menu.is_open():
                    self._more_menu.close(restore_focus=False)
                    return
            except Exception:
                pass
        items = [
            {
                "label": "14天",
                "checked": self._hours == 336,
                "command": lambda: self.set_hours(336),
            },
            {
                "label": "30天",
                "checked": self._hours == 720,
                "command": lambda: self.set_hours(720),
            },
        ]
        bx = self._more_btn.winfo_rootx() + self._more_btn.winfo_width()
        by = self._more_btn.winfo_rooty() + self._more_btn.winfo_height() + sx(2, self)
        self._more_menu = NeuPopupMenu(
            self.winfo_toplevel(),
            items,
            min_width_dp=92,
            right_align=True,
        )
        self._more_menu.open_at(bx, by)

    def __init__(
        self,
        parent: tk.Widget,
        *,
        on_window_change: Optional[Callable[[int], None]] = None,
        initial_hours: int = 24,
        surface_bg: Optional[str] = None,
    ) -> None:
        eff_bg = BASE if surface_bg is None else surface_bg
        super().__init__(parent, bg=eff_bg, bd=0, highlightthickness=0, takefocus=0)
        self._surface_bg = eff_bg
        self._hours = self._normalize_hours(initial_hours)
        self._on_window_change = on_window_change
        self._samples: list[dict[str, Any]] = []
        self._accent = BAR_GREEN
        self._offline = False
        self._plotted_pts: list[tuple[float, float, int, float]] = []
        self._hover_idx: Optional[int] = None
        self._more_menu: Optional[NeuPopupMenu] = None

        self._top = tk.Frame(self, bg=eff_bg)
        self._top.pack(fill="x", pady=(0, sx(4, self)))

        self._lbl_title = tk.Label(
            self._top,
            text="电量变化趋势",
            bg=eff_bg,
            fg=TEXT_MUTED,
            font=ui_font(FONT_CAPTION, "bold", root=self),
            anchor="w",
        )
        self._lbl_title.pack(side="left")

        self._switch = NeuSegmentedSwitch(
            self._top,
            options=[(24, "24h"), (168, "7天")],
            initial_value=self._hours,
            on_change=self.set_hours,
        )
        is_more = self._hours in (336, 720)
        self._more_btn = NeuButton(
            self._top,
            text=self._more_btn_label(),
            command=self._open_more_menu,
            radius=11,
            pad=3,
            padx=8,
            pady=2,
            height=max(16, int(round(self._switch._seg_tot_h - 6))),
            font_size=FONT_CAPTION,
            weight="bold" if is_more else "normal",
            fg=BLURPLE_BRAND if is_more else TEXT_MUTED,
            inset_active=is_more,
            surface_bg=eff_bg,
            tooltip="更多时间范围（14天 / 30天）",
        )
        self._more_btn.pack(side="right", padx=(sx(4, self), 0))
        self._switch.pack(side="right")

        self._lbl_stat = tk.Label(
            self._top,
            text="",
            bg=eff_bg,
            fg=TEXT_DIM,
            font=ui_font(FONT_CAPTION, root=self),
            anchor="e",
        )
        self._lbl_stat.pack(side="right", padx=(sx(4, self), sx(8, self)))

        self._well = NeuCard(
            self,
            radius=14,
            pad=0,
            inner_pad=(8, 6),
            off=4,
            box_r=3,
            inset=True,
            outer_bg=eff_bg,
        )
        self._well.pack(fill="x")
        chart_h = max(sx(50, self), int(round(sx(54, self) * font_scale_ratio())))
        self._canvas = tk.Canvas(
            self._well.content,
            height=chart_h,
            bg=eff_bg,
            bd=0,
            highlightthickness=0,
            takefocus=0,
        )
        self._canvas.pack(fill="x")
        self._canvas.bind("<Configure>", lambda _e: self._draw())
        self._canvas.bind("<Motion>", self._on_chart_motion)
        self._canvas.bind("<Leave>", self._on_chart_leave)

    def _on_chart_motion(self, event) -> None:
        if len(self._plotted_pts) < 2:
            return
        mx = float(event.x)
        best_i = 0
        best_d = abs(self._plotted_pts[0][0] - mx)
        for i, (px, _py, _lvl, _ts) in enumerate(self._plotted_pts[1:], start=1):
            d = abs(px - mx)
            if d < best_d:
                best_d = d
                best_i = i
        if best_i != self._hover_idx:
            self._hover_idx = best_i
            self._draw_hover_overlay()

    def _on_chart_leave(self, _e=None) -> None:
        if self._hover_idx is not None:
            self._hover_idx = None
            self._canvas.delete("hover")

    def _draw_hover_overlay(self) -> None:
        c = self._canvas
        c.delete("hover")
        if self._hover_idx is None or not (0 <= self._hover_idx < len(self._plotted_pts)):
            return
        px, py, lvl, ts = self._plotted_pts[self._hover_idx]
        w = max(60, c.winfo_width())
        h = max(28, c.winfo_height())
        pad_y = sx(5, self)
        c.create_line(px, pad_y, px, h - pad_y, fill=TEXT_DIM, width=1, dash=(2, 2), tags="hover")
        r = max(3.5, sx(4, self))
        dot_col = BLURPLE_BRAND if not self._offline else TEXT_DISABLED
        self._hover_dot_photo = _make_aa_dot_photo(
            r, fill_hex=dot_col, ring_hex=self._surface_bg, ring_w=1.6, master=c
        )
        c.create_image(px, py, anchor="center", image=self._hover_dot_photo, tags="hover")
        try:
            lt = time.localtime(ts)
            t_str = time.strftime("%m-%d %H:%M" if self._hours > 24 else "%H:%M", lt)
        except Exception:
            t_str = ""
        lbl = f"{t_str}{TEXT_SEP}{lvl}%" if t_str else f"{lvl}%"
        fnt = ui_font(FONT_CAPTION, "bold", root=self)
        ty = max(sx(12, self), min(h - sx(12, self), py - sx(14, self) if py > sx(24, self) else py + sx(14, self)))
        tid = c.create_text(px, ty, text=lbl, fill=TEXT, font=fnt, tags="hover")
        bx = c.bbox(tid)
        if bx is not None:
            pad_x = sx(8, self)
            pad_v = sx(3, self)
            sh_l, sh_t, sh_r, sh_b = 3, 2, 3, 4
            pill_w = int(round((bx[2] - bx[0]) + pad_x * 2 + sh_l + sh_r))
            pill_h = int(round((bx[3] - bx[1]) + pad_v * 2 + sh_t + sh_b))
            x0 = bx[0] - pad_x - sh_l
            y0 = bx[1] - pad_v - sh_t
            x1 = x0 + pill_w
            y1 = y0 + pill_h
            dx = 0.0
            if x0 < 1:
                dx = 1 - x0
            elif x1 > w - 1:
                dx = (w - 1) - x1
            dy = 0.0
            if y0 < 1:
                dy = 1 - y0
            elif y1 > h - 1:
                dy = (h - 1) - y1
            if dx != 0.0 or dy != 0.0:
                c.move(tid, dx, dy)
                x0 += dx
                y0 += dy
            self._hover_pill_photo = _make_aa_pill_badge_photo(
                pill_w,
                pill_h,
                fill_hex=POPUP_BG,
                border_hex=POPUP_BORDER,
                radius=(pill_h - sh_t - sh_b) * 0.5,
                dark_theme=is_dark_theme(),
                master=c,
            )
            bg_id = c.create_image(x0, y0, anchor="nw", image=self._hover_pill_photo, tags="hover")
            c.tag_raise(tid, bg_id)

    def set_surface_bg(self, surface_bg: str) -> None:
        if surface_bg != self._surface_bg:
            self._surface_bg = surface_bg
            super().configure(bg=surface_bg)
            self._top.configure(bg=surface_bg)
            self._lbl_title.configure(bg=surface_bg)
            self._lbl_stat.configure(bg=surface_bg)
            self._switch.set_surface_bg(surface_bg)
            self._more_btn.set_surface_bg(surface_bg)
            self._well.set_outer_bg(surface_bg)
            self._well.set_muted(self._offline, fill_hex=surface_bg)
            self._canvas.configure(bg=surface_bg)
            self._draw()

    def set_hours(self, hours: int) -> None:
        target = self._normalize_hours(hours)
        if target == self._hours:
            return
        self._hours = target
        self._switch.set_value(target)
        self._sync_more_btn()
        if self._on_window_change:
            self._on_window_change(self._hours)
        else:
            self._draw()

    @property
    def window_hours(self) -> int:
        return self._hours

    def update_series(
        self,
        samples: list[dict[str, Any]],
        accent_color: str,
        *,
        offline: bool = False,
    ) -> None:
        self._samples = list(samples or [])
        self._accent = GRAY_DISABLED if offline else (accent_color or BAR_GREEN)
        self._offline = offline
        self._draw()

    def _draw(self) -> None:
        c = self._canvas
        c.delete("all")
        self._plotted_pts = []
        w = max(60, c.winfo_width())
        h = max(28, c.winfo_height())

        pad_x = sx(6, self)
        pad_y = sx(6, self)
        plot_w = max(20, w - pad_x * 2)
        plot_h = max(12, h - pad_y * 2)

        # 微弱参考刻度线 (75%, 50%, 25%)
        for frac in (0.25, 0.5, 0.75):
            yy = pad_y + plot_h * (1.0 - frac)
            c.create_line(pad_x, yy, w - pad_x, yy, fill=SEP_DARK, width=1, dash=(2, 4))

        pts = [pt for pt in self._samples if pt.get("lvl") is not None]
        if len(pts) < 2:
            if len(pts) == 1:
                only = int(pts[0]["lvl"])
                self._lbl_stat.configure(text=f"当前 {only}%")
            else:
                self._lbl_stat.configure(text="")
            msg = "等待采集第二条采样点，自动生成变化曲线" if len(pts) == 1 else "暂无历史记录，运行后自动采样"
            c.create_text(
                w / 2.0,
                h / 2.0,
                text=msg,
                fill=TEXT_DIM,
                font=ui_font(FONT_CAPTION, root=self),
            )
            return

        t_min = float(pts[0]["t"])
        t_max = float(pts[-1]["t"])
        t_span = max(60.0, t_max - t_min)

        lvls = [int(pt["lvl"]) for pt in pts]
        hi_v, lo_v, cur_v = max(lvls), min(lvls), lvls[-1]
        span_tag = {24: "24h", 168: "7天", 336: "14天", 720: "30天"}.get(self._hours, f"{self._hours // 24}天")
        stat_full = f"{span_tag} 高 {hi_v}%{TEXT_SEP}低 {lo_v}%{TEXT_SEP}当前 {cur_v}%"
        stat_short = f"高 {hi_v}%{TEXT_SEP}低 {lo_v}%"
        self._lbl_stat.configure(text=stat_full if w >= sx(260, self) else stat_short)

        poly_pts: list[tuple[float, float]] = []
        raw_pt_meta: list[tuple[float, float, int, float]] = []
        for pt in pts:
            frac_x = (float(pt["t"]) - t_min) / t_span
            lvl_i = int(pt["lvl"])
            frac_y = max(0.0, min(100.0, float(lvl_i))) / 100.0
            px = pad_x + frac_x * plot_w
            py = pad_y + (1.0 - frac_y) * plot_h
            poly_pts.append((px, py))
            raw_pt_meta.append((px, py, lvl_i, float(pt["t"])))

        anchors = _simplify_sparkline_anchors(poly_pts)
        y_col, x_min_c, x_max_c = _monotone_cubic_eval(anchors, w)
        for px, py, lvl_i, t_val in raw_pt_meta:
            xi = max(0, min(w - 1, int(round(px - 0.5))))
            yc = y_col[xi]
            self._plotted_pts.append((px, yc if yc is not None else py, lvl_i, t_val))

        lw = max(1.8, sx(2, self) * 0.95)
        self._curve_photo = _make_aa_sparkline_photo(
            w,
            h,
            y_col=y_col,
            x_min=x_min_c,
            x_max=x_max_c,
            accent_hex=self._accent,
            bottom_y=float(h - pad_y),
            lw=lw,
            master=c,
        )
        c.create_image(0, 0, anchor="nw", image=self._curve_photo)

        # 终点抗锯齿圆点与悬停十字读数同步
        ex, ey = poly_pts[-1]
        ey_smooth = y_col[max(0, min(w - 1, int(round(ex - 0.5))))]
        if ey_smooth is not None:
            ey = ey_smooth
        r = max(2.8, sx(3, self))
        self._end_dot_photo = _make_aa_dot_photo(
            r, fill_hex=self._accent, ring_hex=self._surface_bg, ring_w=1.3, master=c
        )
        c.create_image(ex, ey, anchor="center", image=self._end_dot_photo)
        if self._hover_idx is not None:
            self._draw_hover_overlay()


class NeuSkeletonCard(NeuCard):
    """首次扫描前的柔和灰阶新拟物骨架占位卡片，带 6 步正弦平滑微光扫描动效。"""

    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(
            parent,
            radius=24,
            pad=12,
            inner_pad=(14, 14),
            off=7,
            box_r=4,
            inset=False,
            hoverable=False,
        )
        inner = self.content
        self._pulse_step = 0
        self._pulse_id: Optional[str] = None
        self._pulse_frames: list[tk.Frame] = []

        top = tk.Frame(inner, bg=BASE)
        top.pack(fill="x", pady=(0, sx(8, parent)))
        self._cv_icon = tk.Canvas(
            top, width=sx(36, parent), height=sx(36, parent), bg=BASE, bd=0, highlightthickness=0, takefocus=0
        )
        self._cv_icon.pack(side="left", padx=(0, sx(10, parent)))
        d = sx(34, parent)
        self._oval_id = self._cv_icon.create_oval(2, 2, d, d, fill=SKELETON_BG, outline="")

        meta = tk.Frame(top, bg=BASE)
        meta.pack(side="left", fill="x", expand=True)
        b1 = tk.Frame(meta, bg=SKELETON_BG, height=sx(11, parent), width=sx(120, parent))
        b1.pack(anchor="w", pady=(2, 5))
        tk.Frame(meta, bg=SEP_DARK, height=sx(9, parent), width=sx(70, parent)).pack(anchor="w")

        b2 = tk.Frame(inner, bg=SKELETON_BG, height=sx(16, parent))
        b2.pack(fill="x", pady=(0, sx(8, parent)))

        mid = tk.Frame(inner, bg=BASE)
        mid.pack(fill="x", pady=(0, sx(8, parent)))
        b3 = tk.Frame(mid, bg=SKELETON_BG, height=sx(28, parent), width=sx(76, parent))
        b3.pack(side="left")
        tk.Frame(mid, bg=SEP_DARK, height=sx(12, parent), width=sx(140, parent)).pack(side="left", padx=(sx(12, parent), 0))

        bar_ph = NeuProgressBar(inner, height=sx(13, parent))
        bar_ph.pack(fill="x", pady=(0, sx(8, parent)))
        bar_ph.set_value(0, GRAY_DISABLED)

        spark_well = NeuCard(inner, radius=14, pad=0, inner_pad=(8, 6), off=4, box_r=3, inset=True)
        spark_well.pack(fill="x", pady=(0, sx(6, parent)))
        spark_h = max(sx(50, parent), int(round(sx(54, parent) * font_scale_ratio())))
        tk.Frame(spark_well.content, bg=BASE, height=spark_h).pack(fill="x")

        tk.Frame(inner, bg=SEP_DARK, height=sx(10, parent), width=sx(210, parent)).pack(anchor="w")

        self._pulse_frames.extend([b1, b2, b3])
        self.bind("<Destroy>", self._stop_pulse, add="+")
        self._pulse_id = self.after(140, self._tick_pulse)

    def _stop_pulse(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._pulse_id is not None:
            try:
                self.after_cancel(self._pulse_id)
            except Exception:
                pass
            self._pulse_id = None

    def _tick_pulse(self) -> None:
        if not self.winfo_exists():
            self._pulse_id = None
            return
        self._pulse_step = (self._pulse_step + 1) % 6
        alpha = 0.5 - 0.5 * math.cos(self._pulse_step * math.pi / 3.0)
        rgb = blend_rgb(hex_to_rgb(SKELETON_BG), hex_to_rgb(SKELETON_PULSE_BG), alpha)
        col = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
        try:
            self._cv_icon.itemconfigure(self._oval_id, fill=col)
            for f in self._pulse_frames:
                f.configure(bg=col)
        except Exception:
            self._pulse_id = None
            return
        self._pulse_id = self.after(130, self._tick_pulse)

