# -*- coding: utf-8 -*-
"""tray_service.py —— 常驻手柄托盘图标、四宫格悬浮 Tooltip 与低电量右下角弹窗

特性：
  1. 常驻单个矢量手柄 Logo 托盘图标（左键点击打开软件，右键弹出快捷菜单）
  2. 鼠标悬停托盘图标（无需点击）弹出 2x2 四宫格悬浮 Tooltip 展示外设名称与电量；
     鼠标移出托盘图标与四宫格弹窗区域后自动消失（WS_EX_NOACTIVATE 非置顶常驻 OSD）
  3. 当任一在线非充电外设电量低于 20% 时，在桌面右下角弹出新拟物低电量提示弹窗
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from typing import Any, Callable, Optional

import devices
import neu_ui
import tray_common
import win_icon

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_USER = 0x0400
WM_TRAY = WM_USER + 20
WM_MOUSEMOVE = 0x0200
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_CONTEXTMENU = 0x007B
WM_DEVICECHANGE = 0x0219
DBT_DEVNODES_CHANGED = 0x0007
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE = 0x00000005
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000

NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401
NIN_BALLOONUSERCLICK = 0x0405
NIN_POPUPOPEN = 0x0406
NIN_POPUPCLOSE = 0x0407

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NOTIFYICON_VERSION_4 = 4

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

NIIF_NONE = 0x00000000
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002
NIIF_ERROR = 0x00000003

TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_DISABLED = 0x00000002
MF_GRAYED = 0x00000001
MF_CHECKED = 0x00000008

CMD_OPEN = 1001
CMD_REFRESH = 1002
CMD_DEBUG = 1003
CMD_TOGGLE_NOTIFY = 1004
CMD_SETTINGS = 1005
CMD_TOGGLE_AUTOSTART = 1006
CMD_TOGGLE_HIDDEN = 1007
CMD_SNOOZE_30 = 1008
CMD_TOGGLE_COMPACT = 1009
CMD_QUIT = 1099


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


GUID_DEVINTERFACE_HID = GUID(
    0x4D1E55B2, 0xF16F, 0x11CF, (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30)
)
GUID_DEVINTERFACE_USB_DEVICE = GUID(
    0xA5DCBF10, 0x6530, 0x11D2, (ctypes.c_ubyte * 8)(0x90, 0x1F, 0x00, 0xC0, 0x4F, 0xB9, 0x51, 0xED)
)


class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
    _fields_ = [
        ("dbcc_size", wintypes.DWORD),
        ("dbcc_devicetype", wintypes.DWORD),
        ("dbcc_reserved", wintypes.DWORD),
        ("dbcc_classguid", GUID),
        ("dbcc_name", wintypes.WCHAR * 1),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HANDLE),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("guidItem", GUID),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


user32.RegisterClassExW.restype = wintypes.ATOM
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.GetMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.CreatePopupMenu.argtypes = []
user32.DestroyMenu.restype = wintypes.BOOL
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.AppendMenuW.restype = wintypes.BOOL
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, ctypes.c_void_p,
]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.AllowSetForegroundWindow.restype = wintypes.BOOL
user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterDeviceNotificationW.restype = ctypes.c_void_p
user32.RegisterDeviceNotificationW.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.DWORD]
user32.UnregisterDeviceNotification.restype = wintypes.BOOL
user32.UnregisterDeviceNotification.argtypes = [ctypes.c_void_p]

shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
shell32.Shell_NotifyIconGetRect.argtypes = [
    ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(wintypes.RECT)
]

kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def get_cursor_pos() -> tuple[int, int]:
    pt = POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return int(pt.x), int(pt.y)
    return -9999, -9999


class DeviceChangeDebouncer:
    """硬件热插拔防抖器：收到 WM_DEVICECHANGE 后延迟 delay_sec 触发，且两次触发至少间隔 debounce_sec。"""

    def __init__(
        self,
        delay_sec: float = 1.5,
        debounce_sec: float = 3.0,
        on_trigger: Optional[Callable[[int], None]] = None,
        time_fn: Optional[Callable[[], float]] = None,
    ) -> None:
        self.delay_sec = max(0.0, float(delay_sec))
        self.debounce_sec = max(0.0, float(debounce_sec))
        self._on_trigger = on_trigger
        self._now = time_fn or time.monotonic
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._last_fire: float = -1e9
        self._last_event: int = 0
        self._fire_count: int = 0

    def notify(self, event_code: int = 0, *, async_timer: bool = True) -> float:
        """记录一次硬件变更信号，返回计划延迟触发的等待秒数。"""
        with self._lock:
            now = self._now()
            self._last_event = int(event_code)
            target = max(now + self.delay_sec, self._last_fire + self.debounce_sec)
            wait_sec = max(0.01, target - now) if async_timer else max(0.0, target - now)
            if self._timer is not None:
                try:
                    self._timer.cancel()
                except Exception:
                    pass
                self._timer = None
            if async_timer:
                t = threading.Timer(wait_sec, self._fire)
                t.daemon = True
                self._timer = t
                t.start()
            return wait_sec

    def flush_due(self) -> bool:
        """同步判定是否到达触发时间并执行（主要用于零等待单元测试）。"""
        with self._lock:
            now = self._now()
            if self._last_event == 0 and self._fire_count == 0 and self._last_fire < -1e8:
                return False
            if now - self._last_fire < self.debounce_sec:
                return False
        self._fire()
        return True

    def _fire(self) -> None:
        cb = None
        ev = 0
        with self._lock:
            self._timer = None
            self._last_fire = self._now()
            self._fire_count += 1
            cb = self._on_trigger
            ev = self._last_event
        if cb is not None:
            try:
                cb(ev)
            except Exception:
                pass

    def cancel(self) -> None:
        with self._lock:
            if self._timer is not None:
                try:
                    self._timer.cancel()
                except Exception:
                    pass
                self._timer = None


class TrayHostThread:
    """常驻后台 Win32 托盘消息宿主：悬停浮层、左键打开、双击定位最近外设、右键扩展菜单、NIF_INFO 通知与硬件插拔监听。"""

    def __init__(
        self,
        event_queue: queue.SimpleQueue[tuple[str, Any]],
        on_wake: Any = None,
    ) -> None:
        self._q = event_queue
        self._on_wake = on_wake
        self._hwnd: int = 0
        self._hinst = kernel32.GetModuleHandleW(None)
        self._wm_taskbar = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc_ref = WNDPROC(self._wndproc)
        self._icon_lock = threading.RLock()
        self._icon_added = False
        self._alert = False
        self._threshold = 20
        self._device_thresholds: dict[str, int] = {}
        cfg = tray_common.load_config()
        self._style = str(cfg.get("tray_icon_style", "digit"))
        self._visibility = str(cfg.get("tray_visibility", "always"))
        self._hover_mode = str(cfg.get("tray_hover_mode", "grid"))
        self._snooze_active = False
        self._last_icon_sig: Any = None
        self._last_alert_key: Optional[str] = None
        self._tip_text = tray_common.UI_TITLE
        self._last_hover_ts = 0.0
        self._last_click_ts = 0.0
        self._last_rclick_ts = 0.0
        self._readings: list[devices.PeripheralReading] = []
        self._dev_notify_handles: list[int] = []
        self._dev_debouncer = DeviceChangeDebouncer(
            delay_sec=1.5,
            debounce_sec=3.0,
            on_trigger=self._on_device_change_debounced,
        )
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

    def _put_event(self, kind: str, payload: Any = None) -> None:
        self._q.put((kind, payload))
        cb = getattr(self, "_on_wake", None)
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    def _on_device_change_debounced(self, event_code: int) -> None:
        tray_common.log_event("INFO", "tray", f"检测到硬件插拔事件 (0x{event_code:04X})，触发延迟防抖刷新")
        self._put_event("device_change", event_code)

    def start(self) -> bool:
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return self._hwnd != 0

    def _threshold_for(self, key: str) -> int:
        val = self._device_thresholds.get(key)
        if val is not None:
            return max(5, min(50, int(val)))
        return self._threshold

    def _build_tip_text(
        self,
        readings: list[devices.PeripheralReading],
        threshold: int,
        device_thresholds: Optional[dict[str, int]] = None,
    ) -> str:
        """按整台设备边界截断的简洁摘要文本（超长时追加 '等 N 台'，不切断电量数字）。"""
        if not readings:
            return f"{tray_common.UI_TITLE}{neu_ui.TEXT_SEP}暂未接入外设"
        th_map = device_thresholds if device_thresholds is not None else self._device_thresholds
        online = [r for r in readings if (not r.offline) and r.level is not None]
        low = [
            r for r in online
            if (not r.charging)
            and (not r.full)
            and r.level is not None
            and r.level < int(th_map.get(r.key, threshold))
        ]
        target = low or online
        if not target:
            return f"{tray_common.UI_TITLE}{neu_ui.TEXT_SEP}{len(readings)} 台均离线"
        prefix = "低电量: " if low else f"在线({len(online)}/{len(readings)}): "
        parts: list[str] = []
        max_chars = 110
        for idx, r in enumerate(target):
            lvl_str = r.level_text
            item = f"{r.name} {lvl_str}"
            cand = prefix + " / ".join(parts + [item])
            rem = len(target) - idx - 1
            suffix = f" 等 {rem + 1} 台" if rem > 0 else ""
            if len(cand) + (len(f" 等 {rem} 台") if rem > 0 else 0) <= max_chars:
                parts.append(item)
            else:
                if not parts:
                    budget = max(8, max_chars - len(prefix) - len(f" {lvl_str}") - len(suffix) - 1)
                    parts.append(f"{r.name[:budget]}… {lvl_str}")
                rem_left = len(target) - len(parts)
                if rem_left > 0:
                    return (prefix + " / ".join(parts) + f" 等 {rem_left} 台")[:127]
                break
        return (prefix + " / ".join(parts))[:127]

    def _pick_lowest_reading(self) -> Optional[devices.PeripheralReading]:
        online = [r for r in self._readings if (not r.offline) and r.level is not None]
        if not online:
            return None
        low = [
            r for r in online
            if (not r.charging)
            and (not r.full)
            and r.level is not None
            and r.level < self._threshold_for(r.key)
        ]
        pool = low or online
        return min(pool, key=lambda r: int(r.level if r.level is not None else 999))

    def _build_current_hicon(self) -> int:
        lowest = self._pick_lowest_reading()
        if lowest is not None and lowest.level is not None:
            th = self._threshold_for(lowest.key)
            color = devices.battery_color_rgb(
                lowest.level,
                stale=lowest.stale,
                charging=lowest.charging,
                offline=False,
                full=lowest.full,
                low_threshold=th,
            )
            return win_icon.make_tray_style_hicon(
                self._style,
                lowest.level,
                color,
                alert=self._alert,
                role=lowest.role,
                charging=lowest.charging,
            )
        if self._style == "digit":
            return win_icon.make_controller_hicon(alert=self._alert)
        return win_icon.make_tray_style_hicon(
            self._style,
            None,
            (138, 147, 162),
            alert=self._alert,
            role="controller",
            charging=False,
        )

    def _style_signature(self) -> tuple[Any, ...]:
        lowest = self._pick_lowest_reading()
        return (
            self._style,
            self._visibility,
            self._hover_mode,
            self._tip_text if self._hover_mode == "native" else "",
            self._alert,
            lowest.level if lowest else None,
            lowest.role if lowest else "",
            lowest.charging if lowest else False,
            self._threshold_for(lowest.key) if lowest else self._threshold,
        )

    def update_readings(
        self,
        readings: list[devices.PeripheralReading],
        threshold: int = 20,
        *,
        style: Optional[str] = None,
        visibility: Optional[str] = None,
        snooze_active: Optional[bool] = None,
        device_thresholds: Optional[dict[str, int]] = None,
        hover_mode: Optional[str] = None,
    ) -> None:
        self._readings = list(readings)
        self._threshold = int(threshold)
        if device_thresholds is not None:
            self._device_thresholds = {str(k): int(v) for k, v in device_thresholds.items()}
        if style is not None:
            self._style = style if style in tray_common.VALID_TRAY_STYLES else "digit"
        if visibility is not None:
            self._visibility = visibility if visibility in tray_common.VALID_TRAY_VISIBILITY else "always"
        if hover_mode is not None:
            self._hover_mode = hover_mode if hover_mode in tray_common.VALID_TRAY_HOVER_MODES else "grid"
        if snooze_active is not None:
            self._snooze_active = bool(snooze_active)

        has_low = any(
            (not r.offline)
            and (not r.charging)
            and (not r.full)
            and (r.level is not None)
            and (r.level < self._threshold_for(r.key))
            for r in readings
        )
        new_tip = self._build_tip_text(self._readings, self._threshold, self._device_thresholds)
        self._alert = has_low
        self._tip_text = new_tip
        if self._hwnd:
            if self._visibility == "alert_only" and not self._alert:
                self._remove_icon()
            else:
                sig = self._style_signature()
                if (not self._icon_added) or (sig != self._last_icon_sig):
                    self._sync_icon(modify_only=self._icon_added)
                    self._last_icon_sig = sig

    def show_notification(
        self,
        title: str,
        message: str,
        warning: bool = False,
        *,
        device_key: Optional[str] = None,
    ) -> bool:
        """发送原生 Windows NIF_INFO 系统通知（进入 Win10/Win11 通知中心）。"""
        if device_key is not None:
            self._last_alert_key = str(device_key)
        if not self._hwnd:
            return False
        with self._icon_lock:
            if not self._icon_added:
                self._sync_icon(modify_only=False)
            hicon = self._build_current_hicon()
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = wintypes.HWND(self._hwnd)
            nid.uID = 1
            nid.uFlags = NIF_INFO | (NIF_ICON if hicon else 0)
            nid.szInfo = str(message or "")[:255]
            nid.szInfoTitle = str(title or tray_common.UI_TITLE)[:63]
            nid.dwInfoFlags = NIIF_WARNING if warning else NIIF_INFO
            if hicon:
                nid.hIcon = ctypes.c_void_p(hicon)
            ok = bool(shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)))
            tray_common.log_event(
                "INFO" if ok else "WARN",
                "tray",
                f"原生系统通知 ({'警告' if warning else '提示'}) {'成功' if ok else '失败'}: {title} - {message}",
            )
            return ok

    def stop(self) -> None:
        self._dev_debouncer.cancel()
        hwnd = self._hwnd
        if hwnd:
            user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
            self._thread.join(timeout=0.4)

    def get_icon_rect(self) -> tuple[int, int, int, int]:
        if self._hwnd:
            ident = NOTIFYICONIDENTIFIER()
            ident.cbSize = ctypes.sizeof(NOTIFYICONIDENTIFIER)
            ident.hWnd = wintypes.HWND(self._hwnd)
            ident.uID = 1
            rect = wintypes.RECT()
            hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(ident), ctypes.byref(rect))
            if hr == 0 and rect.right > rect.left and rect.bottom > rect.top:
                return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
        try:
            _wl, _wt, wr, wb = neu_ui.get_work_area()
            return (wr - 48, wb - 2, wr - 14, wb + 28)
        except Exception:
            cx, cy = get_cursor_pos()
            return (cx - 16, cy - 16, cx + 16, cy + 16)

    def _sync_icon(self, modify_only: bool = False) -> None:
        if not self._hwnd:
            return
        with self._icon_lock:
            if self._visibility == "alert_only" and not self._alert:
                self._remove_icon()
                return
            hicon = self._build_current_hicon()
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = wintypes.HWND(self._hwnd)
            nid.uID = 1
            use_native_tip = self._hover_mode == "native"
            nid.uFlags = NIF_MESSAGE | (NIF_ICON if hicon else 0) | (NIF_TIP if use_native_tip else 0)
            nid.uCallbackMessage = WM_TRAY
            nid.szTip = (self._tip_text[:127] if use_native_tip else "")
            if hicon:
                nid.hIcon = ctypes.c_void_p(hicon)
            cmd = NIM_MODIFY if (modify_only and self._icon_added) else NIM_ADD
            ok = bool(shell32.Shell_NotifyIconW(cmd, ctypes.byref(nid)))
            if cmd == NIM_MODIFY and not ok:
                ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
            if ok:
                if not self._icon_added or cmd == NIM_ADD:
                    nid_ver = NOTIFYICONDATAW()
                    nid_ver.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                    nid_ver.hWnd = wintypes.HWND(self._hwnd)
                    nid_ver.uID = 1
                    nid_ver.uVersion = NOTIFYICON_VERSION_4
                    shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid_ver))
                self._icon_added = True

    def _remove_icon(self) -> None:
        with self._icon_lock:
            if self._hwnd and self._icon_added:
                nid = NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                nid.hWnd = wintypes.HWND(self._hwnd)
                nid.uID = 1
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                self._icon_added = False

    def _show_context_menu(self) -> None:
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        try:
            snap = list(self._readings)[:6]
            if snap:
                for idx, r in enumerate(snap):
                    lvl = r.level_text
                    tag = "已离线" if r.offline else ("充电中" if r.charging else "")
                    line = f"{r.name}{neu_ui.TEXT_SEP}{lvl}" + (f" ({tag})" if tag else "") + "  → 查看"
                    user32.AppendMenuW(hmenu, MF_STRING, 2000 + idx, line)
                if len(self._readings) > 6:
                    extra = len(self._readings) - 6
                    user32.AppendMenuW(hmenu, MF_STRING, CMD_OPEN, f"另有 {extra} 台外设，打开主面板查看…")
            else:
                user32.AppendMenuW(hmenu, MF_STRING | MF_DISABLED | MF_GRAYED, 2000, "暂未检测到外设")

            cfg = tray_common.load_config()
            notify_on = bool(cfg.get("notify_enabled", True))
            threshold = int(cfg.get("notify_threshold", 20))
            show_hidden = bool(cfg.get("show_hidden", False))
            compact_on = bool(cfg.get("compact_view", False))
            autostart_on = tray_common.is_autostart_enabled()

            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, CMD_OPEN, "打开主窗口")
            user32.AppendMenuW(hmenu, MF_STRING, CMD_REFRESH, "立即刷新电量")
            user32.AppendMenuW(
                hmenu,
                MF_STRING | (MF_CHECKED if compact_on else 0),
                CMD_TOGGLE_COMPACT,
                "紧凑卡片视图",
            )
            user32.AppendMenuW(hmenu, MF_STRING, CMD_SETTINGS, "设置…")
            user32.AppendMenuW(
                hmenu,
                MF_STRING | (MF_CHECKED if notify_on else 0),
                CMD_TOGGLE_NOTIFY,
                f"低电量系统通知 (<{threshold}%)",
            )
            user32.AppendMenuW(
                hmenu,
                MF_STRING | (MF_CHECKED if self._snooze_active else 0),
                CMD_SNOOZE_30,
                "暂缓低电提醒 30 分钟",
            )
            user32.AppendMenuW(
                hmenu,
                MF_STRING | (MF_CHECKED if autostart_on else 0),
                CMD_TOGGLE_AUTOSTART,
                "开机自启",
            )
            user32.AppendMenuW(
                hmenu,
                MF_STRING | (MF_CHECKED if show_hidden else 0),
                CMD_TOGGLE_HIDDEN,
                "显示已隐藏外设",
            )
            user32.AppendMenuW(hmenu, MF_STRING, CMD_DEBUG, "调试与协议标定")
            user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(hmenu, MF_STRING, CMD_QUIT, "退出软件")

            cx, cy = get_cursor_pos()
            user32.SetForegroundWindow(wintypes.HWND(self._hwnd))
            cmd = user32.TrackPopupMenu(
                hmenu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                cx,
                cy,
                0,
                wintypes.HWND(self._hwnd),
                None,
            )
            if 2000 <= cmd < 2000 + len(snap):
                self._put_event("open_device", snap[cmd - 2000].key)
            elif cmd == CMD_OPEN:
                self._put_event("open_main", None)
            elif cmd == CMD_REFRESH:
                self._put_event("refresh", None)
            elif cmd == CMD_TOGGLE_COMPACT:
                self._put_event("toggle_compact", None)
            elif cmd == CMD_SETTINGS:
                self._put_event("open_settings", None)
            elif cmd == CMD_TOGGLE_NOTIFY:
                self._put_event("toggle_notify", None)
            elif cmd == CMD_SNOOZE_30:
                self._put_event("snooze_all", 30)
            elif cmd == CMD_TOGGLE_AUTOSTART:
                self._put_event("toggle_autostart", None)
            elif cmd == CMD_TOGGLE_HIDDEN:
                self._put_event("toggle_hidden", None)
            elif cmd == CMD_DEBUG:
                self._put_event("open_debug", None)
            elif cmd == CMD_QUIT:
                self._put_event("quit_app", None)
        finally:
            user32.DestroyMenu(hmenu)

    def _register_dev_notifications(self, hwnd: int) -> None:
        for guid in (GUID_DEVINTERFACE_HID, GUID_DEVINTERFACE_USB_DEVICE):
            try:
                filt = DEV_BROADCAST_DEVICEINTERFACE_W()
                filt.dbcc_size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE_W)
                filt.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
                filt.dbcc_classguid = guid
                h = user32.RegisterDeviceNotificationW(
                    wintypes.HWND(hwnd), ctypes.byref(filt), DEVICE_NOTIFY_WINDOW_HANDLE
                )
                if h:
                    self._dev_notify_handles.append(int(h))
            except Exception:
                pass

    def _unregister_dev_notifications(self) -> None:
        for h in list(self._dev_notify_handles):
            try:
                user32.UnregisterDeviceNotification(ctypes.c_void_p(h))
            except Exception:
                pass
        self._dev_notify_handles.clear()

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == self._wm_taskbar and self._wm_taskbar != 0:
            self._icon_added = False
            self._sync_icon(modify_only=False)
            return 0
        if msg == WM_DEVICECHANGE:
            if wparam in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE, DBT_DEVNODES_CHANGED):
                self._dev_debouncer.notify(int(wparam))
                return 1
        if msg == WM_TRAY:
            ev = lparam & 0xFFFF
            if ev in (WM_MOUSEMOVE, NIN_POPUPOPEN):
                now = time.monotonic()
                if ev == NIN_POPUPOPEN or (now - self._last_hover_ts >= 0.12):
                    self._last_hover_ts = now
                    self._put_event("hover_icon", self.get_icon_rect())
            elif ev in (WM_LBUTTONUP, NIN_SELECT, NIN_KEYSELECT):
                now = time.monotonic()
                if now - self._last_click_ts >= 0.08:
                    self._last_click_ts = now
                    self._put_event("open_main", None)
            elif ev == NIN_BALLOONUSERCLICK:
                now = time.monotonic()
                if now - self._last_click_ts >= 0.08:
                    self._last_click_ts = now
                    if self._last_alert_key:
                        self._put_event("open_device", self._last_alert_key)
                    else:
                        self._put_event("open_main", None)
            elif ev == WM_LBUTTONDBLCLK:
                self._last_click_ts = time.monotonic()
                self._put_event("open_last_device", None)
            elif ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                now = time.monotonic()
                if now - self._last_rclick_ts >= 0.15:
                    self._last_rclick_ts = now
                    try:
                        user32.AllowSetForegroundWindow(wintypes.DWORD(0xFFFFFFFF))
                        user32.SetForegroundWindow(wintypes.HWND(self._hwnd))
                    except Exception:
                        pass
                    self._put_event("hide_tooltip", None)
                    self._put_event("tray_menu", get_cursor_pos())
            return 0
        if msg == tray_common.WM_APP_SHOW:
            self._put_event("open_main", None)
            return 0
        if msg == tray_common.WM_APP_REFRESH:
            self._put_event("refresh", None)
            return 0
        if msg in (tray_common.WM_APP_QUIT, WM_CLOSE):
            if msg == tray_common.WM_APP_QUIT:
                self._put_event("quit_app", None)
            user32.DestroyWindow(wintypes.HWND(hwnd))
            return 0
        if msg == WM_DESTROY:
            self._dev_debouncer.cancel()
            self._unregister_dev_notifications()
            self._remove_icon()
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(wintypes.HWND(hwnd), msg, wparam, lparam)

    def _run_loop(self) -> None:
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = self._hinst
        wc.lpszClassName = tray_common.WND_CLASS
        user32.RegisterClassExW(ctypes.byref(wc))

        self._hwnd = int(
            user32.CreateWindowExW(
                0,
                tray_common.WND_CLASS,
                tray_common.UI_TITLE,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                self._hinst,
                None,
            )
            or 0
        )
        if self._hwnd:
            self._register_dev_notifications(self._hwnd)
            if self._visibility != "alert_only" or self._alert:
                self._sync_icon(modify_only=False)
        self._ready.set()

        if not self._hwnd:
            return
        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = 0


def _sort_devices_priority(
    readings: list[devices.PeripheralReading],
    threshold: int = 20,
    device_thresholds: Optional[dict[str, int]] = None,
) -> list[devices.PeripheralReading]:
    """按低电量报警 > 在线 > 离线顺序排列外设列表，供托盘悬浮分页与四宫格共用。"""
    th_map = device_thresholds or {}

    def _rank(r: devices.PeripheralReading) -> int:
        if r.offline or r.level is None:
            return 2
        eff_th = int(th_map.get(r.key, threshold))
        if (not r.charging) and (not r.full) and r.level < eff_th:
            return 0
        return 1

    return sorted(readings, key=_rank)


def _order_for_quad_grid(
    readings: list[devices.PeripheralReading],
    threshold: int = 20,
    device_thresholds: Optional[dict[str, int]] = None,
) -> list[Optional[devices.PeripheralReading]]:
    """优先展示低电量报警设备与在线设备，避免报警外设被离线项挤出四宫格。"""
    ordered = _sort_devices_priority(readings, threshold=threshold, device_thresholds=device_thresholds)
    picked: list[Optional[devices.PeripheralReading]] = list(ordered[:4])
    while len(picked) < 4:
        picked.append(None)
    return picked


class TrayHoverTooltip:
    """鼠标悬停手柄托盘图标时出现的悬浮胶囊概览（低电优先、支持超过 4 台翻页、单色矢量符号）。"""

    def __init__(
        self,
        root: tk.Tk,
        get_icon_rect: Callable[[], tuple[int, int, int, int]],
        on_open_main: Callable[[], None],
        on_open_device: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._root = root
        self._get_icon_rect = get_icon_rect
        self._on_open_main = on_open_main
        self._on_open_device = on_open_device
        self._win: Optional[tk.Toplevel] = None
        self._grid: Optional[tk.Frame] = None
        self._visible = False
        self._leave_ticks = 0
        self._poll_id: Optional[str] = None
        self._anim_id: Optional[str] = None
        self._readings: list[devices.PeripheralReading] = []
        self._threshold: int = 20
        self._device_thresholds: dict[str, int] = {}
        self._page: int = 0
        self._total_pages: int = 1
        self._theme_sig: str = ""
        self._cell_slots: list[dict[str, Any]] = []
        self._summary_cv: Optional[tk.Canvas] = None
        self._summary_text_id: Optional[int] = None
        self._summary_photo: Optional[tk.PhotoImage] = None
        self._lbl_summary: Optional[tk.Label] = None
        self._lbl_more: Optional[tk.Label] = None
        self._more_row: Optional[tk.Frame] = None
        self._btn_prev: Optional[tk.Canvas] = None
        self._btn_next: Optional[tk.Canvas] = None
        self._more_pill: Optional[tk.Canvas] = None
        self._more_text_id: Optional[int] = None
        self._pill_photo: Optional[tk.PhotoImage] = None
        self._pill_hover_photo: Optional[tk.PhotoImage] = None
        self._more_photo: Optional[tk.PhotoImage] = None
        self._nav_photo: Optional[tk.PhotoImage] = None
        self._name_font_obj: Optional[tkfont.Font] = None
        self._pct_font_obj: Optional[tkfont.Font] = None
        self._name_max_px: int = 120
        self._size_px: tuple[int, int] = (360, 120)
        self._last_rect: Optional[tuple[int, int, int, int]] = None

    def _fit_pill_name(self, name: str) -> str:
        font_obj = self._name_font_obj
        if font_obj is None or self._name_max_px <= 0:
            return name
        return neu_ui.fit_ellipsis(name, self._name_max_px, font_obj)

    def update_data(
        self,
        readings: list[devices.PeripheralReading],
        threshold: int = 20,
        *,
        device_thresholds: Optional[dict[str, int]] = None,
    ) -> None:
        self._readings = list(readings)
        self._threshold = int(threshold)
        if device_thresholds is not None:
            self._device_thresholds = {str(k): int(v) for k, v in device_thresholds.items()}
        total_pages = max(1, (len(self._readings) + 3) // 4)
        if self._page >= total_pages:
            self._page = max(0, total_pages - 1)
        if self._visible and self._win and self._win.winfo_exists():
            self._populate()

    def _step_page(self, delta: int) -> None:
        if self._total_pages <= 1:
            return
        self._page = (self._page + delta) % self._total_pages
        self._populate()

    def _on_wheel_page(self, event) -> str:
        if self._total_pages > 1:
            self._step_page(-1 if getattr(event, "delta", 0) > 0 else 1)
        return "break"

    def _ensure_window(self) -> None:
        cur_theme = neu_ui.get_active_theme()
        if self._win is not None and self._win.winfo_exists():
            if cur_theme == self._theme_sig:
                return
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
        self._theme_sig = cur_theme
        transp = neu_ui.TRANSP_KEY
        self._transp_key = transp
        win = tk.Toplevel(self._root)
        win.withdraw()
        win.configure(bg=transp)
        try:
            win.attributes("-transparentcolor", transp)
            win.attributes("-topmost", True)
        except Exception:
            pass
        neu_ui.enable_frameless_taskbar(win, no_activate=True)

        name_font = neu_ui.ui_font(neu_ui.FONT_CAPTION, root=win)
        pct_font = neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=win)
        self._name_font_obj = tkfont.Font(win, font=name_font)
        self._pct_font_obj = tkfont.Font(win, font=pct_font)

        self._summary_photo = None
        self._summary_cv = None
        self._summary_text_id = None
        self._lbl_summary = None

        # 独立悬浮椭圆胶囊网格（无内层矩形裁剪与外框）
        grid = tk.Frame(win, bg=transp, padx=neu_ui.sx(2, win), pady=neu_ui.sx(2, win))
        grid.pack(fill="both", expand=True)
        self._grid = grid
        self._cell_slots = []

        cell_w = neu_ui.sx(236, win)
        cell_h = max(32, (neu_ui.sx(38, win) // 2) * 2)
        dot_sz = max(7, neu_ui.sx(7, win))
        pill_radius = cell_h // 2

        self._pill_photo = neu_ui.make_surface_photo(
            cell_w,
            cell_h,
            radius=pill_radius,
            off=3,
            box_r=2,
            pad=0,
            inset=True,
            fill_rgb=neu_ui.BASE_RGB,
            outer_rgb=neu_ui.hex_to_rgb(transp),
            shadow_strength=0.80,
            light_strength=0.90,
        )
        self._pill_hover_photo = neu_ui.make_surface_photo(
            cell_w,
            cell_h,
            radius=pill_radius,
            off=2,
            box_r=2,
            pad=0,
            inset=False,
            fill_rgb=neu_ui.hex_to_rgb(neu_ui.HOVER_PILL_BG),
            outer_rgb=neu_ui.hex_to_rgb(transp),
            shadow_strength=0.78,
            light_strength=0.92,
        )

        cy = cell_h / 2.0
        dot_cx = float(neu_ui.sx(16, win))
        dot_r = dot_sz / 2.0
        name_x = dot_cx + dot_r + float(neu_ui.sx(7, win))
        pct_x = float(cell_w - neu_ui.sx(16, win))
        pct_reserve = max(self._pct_font_obj.measure("100%") + neu_ui.sx(16, win), neu_ui.sx(50, win))
        self._name_max_px = max(48, int(pct_x - pct_reserve - neu_ui.sx(8, win) - name_x))

        for i in range(4):
            r_idx, c_idx = divmod(i, 2)
            cv = tk.Canvas(
                grid,
                width=cell_w,
                height=cell_h,
                bg=transp,
                bd=0,
                highlightthickness=0,
                cursor="hand2",
                takefocus=0,
            )
            cv.grid(
                row=r_idx,
                column=c_idx,
                sticky="nsew",
                padx=neu_ui.sx(5, win),
                pady=neu_ui.sx(5, win),
            )
            grid.grid_columnconfigure(c_idx, weight=1, minsize=cell_w)
            bg_id = cv.create_image(0, 0, anchor="nw", image=self._pill_photo)
            dot_id = cv.create_oval(
                dot_cx - dot_r,
                cy - dot_r,
                dot_cx + dot_r,
                cy + dot_r,
                fill=neu_ui.GRAY_DISABLED,
                outline="",
            )
            name_id = cv.create_text(
                name_x,
                cy,
                anchor="w",
                text="--",
                font=name_font,
                fill=neu_ui.TEXT_DISABLED,
            )
            pct_id = cv.create_text(
                pct_x,
                cy,
                anchor="e",
                text="--",
                font=pct_font,
                fill=neu_ui.TEXT_DISABLED,
            )
            slot: dict[str, Any] = {
                "canvas": cv,
                "bg_id": bg_id,
                "dot_id": dot_id,
                "name_id": name_id,
                "pct_id": pct_id,
                "pct_x": pct_x,
                "cy": cy,
                "key": None,
                "row": r_idx,
                "col": c_idx,
            }
            cv.bind("<Enter>", lambda _e, s=slot: self._on_slot_hover(s, True))
            cv.bind("<Leave>", lambda _e, s=slot: self._on_slot_hover(s, False))
            cv.bind("<Button-1>", lambda _e, s=slot: self._handle_click(s.get("key")))
            cv.bind("<MouseWheel>", self._on_wheel_page)
            self._cell_slots.append(slot)

        # 底部分页控制 + 主面板跳转胶囊（超过 4 台外设时支持上一页/下一页切换）
        more_row = tk.Frame(win, bg=transp)
        nav_sz = max(26, (neu_ui.sx(28, win) // 2) * 2)
        self._nav_photo = neu_ui.make_surface_photo(
            nav_sz,
            nav_sz,
            radius=nav_sz // 2,
            off=2,
            box_r=2,
            pad=0,
            inset=True,
            fill_rgb=neu_ui.BASE_RGB,
            outer_rgb=neu_ui.hex_to_rgb(transp),
            shadow_strength=0.78,
            light_strength=0.88,
        )
        btn_prev = tk.Canvas(
            more_row, width=nav_sz, height=nav_sz, bg=transp, bd=0, highlightthickness=0, cursor="hand2", takefocus=0
        )
        btn_prev.create_image(0, 0, anchor="nw", image=self._nav_photo)
        nc, ds = nav_sz / 2.0, neu_ui.sx(4, win)
        btn_prev.create_line(
            nc + ds * 0.4, nc - ds, nc - ds * 0.5, nc, nc + ds * 0.4, nc + ds,
            fill=neu_ui.BLURPLE_LINK, width=1.8, capstyle="round", joinstyle="round",
        )
        btn_prev.bind("<Button-1>", lambda _e: self._step_page(-1))
        btn_prev.pack(side="left", padx=(0, neu_ui.sx(5, win)))

        more_w = neu_ui.sx(226, win)
        more_h = nav_sz
        self._more_photo = neu_ui.make_surface_photo(
            more_w,
            more_h,
            radius=more_h // 2,
            off=2,
            box_r=2,
            pad=0,
            inset=True,
            fill_rgb=neu_ui.BASE_RGB,
            outer_rgb=neu_ui.hex_to_rgb(transp),
            shadow_strength=0.78,
            light_strength=0.88,
        )
        more_cv = tk.Canvas(
            more_row,
            width=more_w,
            height=more_h,
            bg=transp,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=0,
        )
        more_cv.create_image(0, 0, anchor="nw", image=self._more_photo)
        self._more_text_id = more_cv.create_text(
            more_w / 2.0,
            more_h / 2.0,
            anchor="center",
            text="",
            font=name_font,
            fill=neu_ui.BLURPLE_LINK,
        )
        more_cv.bind("<Button-1>", lambda _e: self._handle_click(None))
        more_cv.pack(side="left")

        btn_next = tk.Canvas(
            more_row, width=nav_sz, height=nav_sz, bg=transp, bd=0, highlightthickness=0, cursor="hand2", takefocus=0
        )
        btn_next.create_image(0, 0, anchor="nw", image=self._nav_photo)
        btn_next.create_line(
            nc - ds * 0.4, nc - ds, nc + ds * 0.5, nc, nc - ds * 0.4, nc + ds,
            fill=neu_ui.BLURPLE_LINK, width=1.8, capstyle="round", joinstyle="round",
        )
        btn_next.bind("<Button-1>", lambda _e: self._step_page(1))
        btn_next.pack(side="left", padx=(neu_ui.sx(5, win), 0))

        self._more_row = more_row
        self._btn_prev = btn_prev
        self._btn_next = btn_next
        self._more_pill = more_cv
        # 代理标签：保持与单元测试对 _lbl_more.cget("text") 断言的兼容
        self._lbl_more = tk.Label(win, text="")

        win.bind("<MouseWheel>", self._on_wheel_page)
        self._win = win

    def _on_slot_hover(self, slot: dict[str, Any], hovered: bool) -> None:
        cv: tk.Canvas = slot["canvas"]
        if not slot.get("key"):
            return
        img = self._pill_hover_photo if (hovered and self._pill_hover_photo) else self._pill_photo
        if img is not None:
            cv.itemconfigure(slot["bg_id"], image=img)

    def _handle_click(self, device_key: Optional[str] = None) -> None:
        self.hide()
        if device_key and self._on_open_device is not None:
            self._on_open_device(device_key)
        else:
            self._on_open_main()

    def _populate(self) -> None:
        ordered = _sort_devices_priority(
            self._readings, threshold=self._threshold, device_thresholds=self._device_thresholds
        )
        total_cnt = len(ordered)
        self._total_pages = max(1, (total_cnt + 3) // 4)
        if self._page >= self._total_pages:
            self._page = self._total_pages - 1
        start = self._page * 4
        page_items: list[Optional[devices.PeripheralReading]] = list(ordered[start : start + 4])
        while len(page_items) < 4:
            page_items.append(None)

        visible_cnt = sum(1 for r in page_items if r is not None) if total_cnt > 0 else 1
        cols_used = 1 if visible_cnt <= 1 else 2
        if self._grid is not None and self._win is not None:
            cell_w = neu_ui.sx(236, self._win)
            for c_idx in range(2):
                self._grid.grid_columnconfigure(
                    c_idx, weight=(1 if c_idx < cols_used else 0), minsize=(cell_w if c_idx < cols_used else 0)
                )

        for idx, slot in enumerate(self._cell_slots):
            cv: tk.Canvas = slot["canvas"]
            cv.delete("bolt")
            r = page_items[idx]
            if total_cnt > 0 and r is None:
                slot["key"] = None
                cv.grid_forget()
                continue
            if total_cnt == 0 and idx > 0:
                slot["key"] = None
                cv.grid_forget()
                continue

            if not cv.winfo_manager():
                cv.grid(
                    row=slot["row"],
                    column=slot["col"],
                    sticky="nsew",
                    padx=neu_ui.sx(5, cv),
                    pady=neu_ui.sx(5, cv),
                )
            if self._pill_photo is not None:
                cv.itemconfigure(slot["bg_id"], image=self._pill_photo)

            if r is None:
                slot["key"] = None
                cv.itemconfigure(slot["dot_id"], fill=neu_ui.GRAY_DISABLED)
                cv.itemconfigure(slot["name_id"], text="暂未接入外设", fill=neu_ui.TEXT_DISABLED)
                cv.itemconfigure(slot["pct_id"], text="--", fill=neu_ui.TEXT_DISABLED)
                continue

            slot["key"] = r.key
            short_name = self._fit_pill_name(r.name)
            if r.offline or r.level is None:
                cv.itemconfigure(slot["dot_id"], fill=neu_ui.GRAY_DISABLED)
                cv.itemconfigure(slot["name_id"], text=short_name, fill=neu_ui.TEXT_DISABLED)
                cv.itemconfigure(slot["pct_id"], text="离线", fill=neu_ui.TEXT_DISABLED)
            else:
                eff_th = int(self._device_thresholds.get(r.key, self._threshold))
                col = devices.battery_color_hex(
                    r.level,
                    low_threshold=eff_th,
                    asleep=r.asleep,
                    stale=r.stale,
                    charging=r.charging,
                    offline=r.offline,
                    full=r.full,
                ).lower()
                cv.itemconfigure(slot["dot_id"], fill=col)
                cv.itemconfigure(slot["name_id"], text=short_name, fill=neu_ui.TEXT)
                val_text = r.level_text
                if r.charging:
                    val_fg = neu_ui.AMBER_CHARGE
                    pct_w = self._pct_font_obj.measure(val_text) if self._pct_font_obj else 28
                    bx = slot["pct_x"] - pct_w - float(neu_ui.sx(8, cv))
                    by = slot["cy"]
                    s = max(0.85, neu_ui.dpi_scale(cv) * 0.9)
                    cv.create_polygon(
                        bx + 1.2 * s, by - 4.4 * s,
                        bx - 2.6 * s, by + 0.5 * s,
                        bx + 0.2 * s, by + 0.5 * s,
                        bx - 1.2 * s, by + 4.4 * s,
                        bx + 2.8 * s, by - 0.6 * s,
                        bx - 0.1 * s, by - 0.6 * s,
                        fill=neu_ui.AMBER_CHARGE,
                        outline="",
                        tags="bolt",
                    )
                elif r.level < eff_th:
                    val_fg = neu_ui.RED_DANGER
                else:
                    val_fg = neu_ui.TEXT
                cv.itemconfigure(slot["pct_id"], text=val_text, fill=val_fg)

        if self._more_row is not None and self._more_text_id is not None and self._more_pill is not None:
            extra_cnt = max(0, total_cnt - 4)
            if extra_cnt > 0:
                more_txt = f"+{extra_cnt} 台更多{neu_ui.TEXT_SEP}第 {self._page + 1}/{self._total_pages} 页"
                self._more_pill.itemconfigure(self._more_text_id, text=more_txt)
                if self._lbl_more is not None:
                    self._lbl_more.configure(text=more_txt)
                self._more_row.pack(pady=(neu_ui.sx(2, self._more_row), neu_ui.sx(4, self._more_row)))
            else:
                self._more_row.pack_forget()

    def _bring_topmost(self, x: Optional[int] = None, y: Optional[int] = None, w: Optional[int] = None, h: Optional[int] = None) -> None:
        if self._win is None or not self._win.winfo_exists():
            return
        try:
            self._win.attributes("-topmost", True)
            self._win.lift()
        except Exception:
            pass
        hwnd = neu_ui._root_hwnd(self._win)
        if hwnd:
            flags = 0x0010 | 0x0040  # SWP_NOACTIVATE | SWP_SHOWWINDOW
            if x is None or y is None or w is None or h is None:
                flags |= 0x0002 | 0x0001  # SWP_NOMOVE | SWP_NOSIZE
                user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1), 0, 0, 0, 0, flags)
            else:
                user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1), int(x), int(y), int(w), int(h), flags)

    def show_near_rect(self, icon_rect: tuple[int, int, int, int], pin_for_preview: bool = False) -> None:
        if self._visible and self._last_rect is not None:
            if all(abs(a - b) <= 2 for a, b in zip(icon_rect, self._last_rect)):
                self._leave_ticks = 0
                self._bring_topmost()
                return
        self._last_rect = icon_rect
        self._ensure_window()
        assert self._win is not None
        if self._anim_id is not None:
            try:
                self._root.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None
        self._populate()
        self._win.update_idletasks()
        req_w = max(neu_ui.sx(240, self._win), self._win.winfo_reqwidth())
        req_h = max(neu_ui.sx(44, self._win), self._win.winfo_reqheight())
        self._size_px = (req_w, req_h)

        wl, wt, wr, wb = neu_ui.get_work_area()
        il, it, ir, ib = icon_rect
        icx = (il + ir) // 2
        icy = (it + ib) // 2

        gap = neu_ui.sx(6, self._win)
        margin = neu_ui.sx(10, self._win)

        x = max(wl + margin, min(wr - req_w - margin, icx - req_w // 2))
        if icy >= (wt + wb) // 2:
            y = max(wt + margin, min(wb - req_h - margin, it - req_h - gap))
        else:
            y = max(wt + margin, min(wb - req_h - margin, ib + gap))

        was_visible = self._visible
        self._win.geometry(f"{req_w}x{req_h}+{x}+{y}")
        if not was_visible:
            try:
                self._win.attributes("-alpha", 0.12)
            except Exception:
                pass
        self._win.deiconify()
        neu_ui.enable_frameless_taskbar(self._win, no_activate=True)
        try:
            self._win.attributes("-transparentcolor", getattr(self, "_transp_key", neu_ui.TRANSP_KEY))
        except Exception:
            pass
        # 鼠标指向托盘图标弹出的数据严格置顶（HWND_TOPMOST），不被任务栏或普通应用遮挡
        self._bring_topmost(x, y, req_w, req_h)
        self._visible = True
        self._leave_ticks = 0
        if not was_visible:
            self._animate_alpha(0.12, 1.0, on_done=self._bring_topmost)
        if not pin_for_preview:
            self._schedule_poll()

    def _animate_alpha(self, a0: float, a1: float, on_done: Optional[Callable[[], None]] = None) -> None:
        steps = 5

        def _tick(step: int) -> None:
            if self._win is None or not self._win.winfo_exists():
                self._anim_id = None
                return
            t = min(1.0, step / float(steps))
            ease = 1.0 - (1.0 - t) ** 2 if a1 >= a0 else t * t
            a = a0 + (a1 - a0) * ease
            try:
                self._win.attributes("-alpha", max(0.0, min(1.0, a)))
            except Exception:
                pass
            if step < steps:
                self._anim_id = self._root.after(14, lambda: _tick(step + 1))
            else:
                self._anim_id = None
                if on_done:
                    on_done()

        self._anim_id = self._root.after(14, lambda: _tick(1))

    def hide(self) -> None:
        self._visible = False
        self._last_rect = None
        if self._anim_id is not None:
            try:
                self._root.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None
        if self._poll_id is not None:
            try:
                self._root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        if self._win is not None and self._win.winfo_exists():
            self._win.withdraw()

    def _schedule_poll(self) -> None:
        if self._poll_id is not None:
            return
        self._poll_id = self._root.after(150, self._poll_cursor)

    def _poll_cursor(self) -> None:
        self._poll_id = None
        if not self._visible or self._win is None or not self._win.winfo_exists():
            return

        cx, cy = get_cursor_pos()
        il, it, ir, ib = self._get_icon_rect()
        px = self._win.winfo_rootx()
        py = self._win.winfo_rooty()
        pw = max(1, self._win.winfo_width())
        ph = max(1, self._win.winfo_height())

        pad = 6
        in_icon = (il - pad <= cx <= ir + pad) and (it - 10 <= cy <= ib + pad)
        in_popup = (px - pad <= cx <= px + pw + pad) and (py - pad <= cy <= py + ph + 12)
        if in_icon or in_popup:
            self._leave_ticks = 0
        else:
            self._leave_ticks += 1
            if self._leave_ticks >= 2:
                self.hide()
                return

        self._poll_id = self._root.after(150, self._poll_cursor)


class LowBatteryToastPopup:
    """设备电量降至阈值以下时，在桌面右下角弹出的圆角低电量提醒弹窗（支持 8s 倒计时进度条与悬停冻结）。"""

    def __init__(
        self,
        root: tk.Tk,
        on_open_main: Callable[[], None],
        on_open_device: Optional[Callable[[str], None]] = None,
        on_snooze: Optional[Callable[[str, int], None]] = None,
    ) -> None:
        self._root = root
        self._on_open_main = on_open_main
        self._on_open_device = on_open_device
        self._on_snooze = on_snooze
        self._primary_key: Optional[str] = None
        self._win: Optional[tk.Toplevel] = None
        self._timer_id: Optional[str] = None
        self._countdown_id: Optional[str] = None
        self._anim_id: Optional[str] = None
        self._auto_close_ms: int = 8000
        self._remaining_ms: int = 8000
        self._last_tick_ts: float = 0.0
        self._bar_cv: Optional[tk.Canvas] = None
        self._hovered: bool = False
        self._alpha_val: float = 0.60
        self._geom_pos: tuple[int, int, int, int] = (0, 0, 336, 152)

    def show(
        self,
        reading: devices.PeripheralReading | list[devices.PeripheralReading],
        *,
        auto_close_ms: int = 8000,
        alpha: float = 0.60,
        animate: bool = True,
        threshold: int = 20,
        device_thresholds: Optional[dict[str, int]] = None,
    ) -> None:
        self.dismiss()
        items = list(reading) if isinstance(reading, (list, tuple)) else [reading]
        if not items:
            return
        items.sort(key=lambda r: (999 if r.level is None else r.level, r.name))
        primary = items[0]
        others = items[1:]
        self._primary_key = primary.key
        eff_th = int((device_thresholds or {}).get(primary.key, threshold))

        transp = neu_ui.TRANSP_KEY
        win = tk.Toplevel(self._root)
        win.withdraw()
        win.configure(bg=transp)
        alpha_val = max(0.1, min(1.0, float(alpha)))
        self._alpha_val = alpha_val
        use_anim = bool(animate and auto_close_ms > 0)
        try:
            win.attributes("-transparentcolor", transp)
            win.attributes("-alpha", 0.05 if use_anim else alpha_val)
        except Exception:
            pass
        neu_ui.enable_frameless_taskbar(win, no_activate=True)

        card = neu_ui.NeuCard(
            win,
            radius=20,
            pad=10,
            inner_pad=(16, 14),
            off=6,
            box_r=3,
            inset=False,
            outer_bg=transp,
        )
        card.pack(fill="both", expand=True)

        top = tk.Frame(card.content, bg=neu_ui.BASE)
        top.pack(fill="x")

        badge = neu_ui.NeuRoleBadge(top, primary.role, color=neu_ui.RED_DANGER)
        badge.pack(side="left", padx=(0, neu_ui.sx(10, win)))

        info = tk.Frame(top, bg=neu_ui.BASE)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(
            info,
            text="外设低电量提醒" if not others else f"外设低电量提醒（共 {len(items)} 台）",
            bg=neu_ui.BASE,
            fg=neu_ui.RED_DANGER,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=win),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            info,
            text=primary.name,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT,
            font=neu_ui.ui_font(neu_ui.FONT_SUBTITLE, "bold", root=win),
            anchor="w",
        ).pack(fill="x", pady=(1, 0))

        lvl = 0 if primary.level is None else int(primary.level)
        lvl_disp = primary.level_text if primary.level is not None else "0%"
        tk.Label(
            card.content,
            text=f"当前电量仅剩 {lvl_disp}（低于 {eff_th}%），请及时充电。",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=win),
            anchor="w",
        ).pack(fill="x", pady=(neu_ui.sx(8, win), neu_ui.sx(6, win)))

        bar = neu_ui.NeuProgressBar(card.content, height=neu_ui.sx(10, win))
        bar.pack(fill="x", pady=(0, neu_ui.sx(8, win)))
        bar.set_value(lvl, neu_ui.BAR_RED)

        if others:
            summary_items = [
                f"{r.name} {r.level_text if r.level is not None else '0%'}"
                for r in others[:3]
            ]
            more_suffix = f" 等 {len(others)} 台" if len(others) > 3 else ""
            tk.Label(
                card.content,
                text="同时偏低：" + neu_ui.TEXT_SEP.join(summary_items) + more_suffix,
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=win),
                anchor="w",
                justify="left",
                wraplength=neu_ui.sx(290, win),
            ).pack(fill="x", pady=(0, neu_ui.sx(8, win)))

        btns = tk.Frame(card.content, bg=neu_ui.BASE)
        btns.pack(fill="x")
        neu_ui.NeuButton(
            btns,
            text="知道了",
            command=self.dismiss_animated,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.TEXT_MUTED,
        ).pack(side="right")
        if self._on_snooze is not None and primary.key:
            neu_ui.NeuButton(
                btns,
                text="暂缓 30m",
                command=lambda pk=primary.key: self._snooze_and_close(pk, 30),
                radius=14,
                pad=6,
                padx=10,
                pady=4,
                font_size=neu_ui.FONT_CAPTION,
                fg=neu_ui.TEXT_MUTED,
            ).pack(side="right", padx=(0, neu_ui.sx(6, win)))
        neu_ui.NeuButton(
            btns,
            text="打开查看",
            command=self._open_and_close,
            radius=14,
            pad=6,
            padx=14,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            accent=True,
            weight="bold",
        ).pack(side="right", padx=(0, neu_ui.sx(6, win)))

        if auto_close_ms > 0:
            bar_cv = tk.Canvas(
                card.content, height=max(3, neu_ui.sx(3, win)), bg=neu_ui.SEP_DARK, bd=0, highlightthickness=0, takefocus=0
            )
            bar_cv.pack(fill="x", pady=(neu_ui.sx(8, win), 0))
            self._bar_cv = bar_cv
        else:
            self._bar_cv = None

        win.update_idletasks()
        w = max(neu_ui.sx(336, win), win.winfo_reqwidth())
        h = max(neu_ui.sx(152, win), win.winfo_reqheight())
        _wl, _wt, wr, wb = neu_ui.get_work_area()
        margin = neu_ui.sx(16, win)
        x = max(16, wr - w - margin)
        target_y = max(16, wb - h - margin)
        start_y = target_y + (neu_ui.sx(24, win) if use_anim else 0)
        self._geom_pos = (x, target_y, w, h)

        win.geometry(f"{w}x{h}+{x}+{start_y}")
        win.deiconify()
        neu_ui.enable_frameless_taskbar(win, no_activate=True)
        try:
            win.attributes("-transparentcolor", transp)
            win.attributes("-topmost", True)
            win.attributes("-alpha", 0.05 if use_anim else alpha_val)
        except Exception:
            pass
        hwnd = neu_ui._root_hwnd(win)
        if hwnd:
            user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(-1),
                x,
                start_y,
                w,
                h,
                0x0010 | 0x0040,
            )

        self._win = win
        self._hovered = False
        self._auto_close_ms = int(auto_close_ms)
        self._remaining_ms = int(auto_close_ms)
        self._draw_countdown_bar()
        win.bind("<Enter>", self._on_enter, add="+")
        win.bind("<Leave>", self._on_leave, add="+")

        if use_anim:
            self._animate_in(x, start_y, target_y, w, h, alpha_val)
        elif auto_close_ms > 0:
            self._schedule_auto_close()

    def _animate_in(
        self, x: int, start_y: int, target_y: int, w: int, h: int, target_alpha: float
    ) -> None:
        steps = 10

        def _tick(step: int) -> None:
            if self._win is None or not self._win.winfo_exists():
                self._anim_id = None
                return
            t = min(1.0, step / float(steps))
            ease = 1.0 - (1.0 - t) ** 3
            cy = int(round(start_y + (target_y - start_y) * ease))
            ca = target_alpha * ease
            try:
                self._win.attributes("-alpha", ca)
                self._win.geometry(f"{w}x{h}+{x}+{cy}")
            except Exception:
                pass
            if step < steps:
                self._anim_id = self._root.after(20, lambda: _tick(step + 1))
            else:
                self._anim_id = None
                if self._auto_close_ms > 0 and not self._hovered:
                    self._schedule_auto_close()

        self._anim_id = self._root.after(16, lambda: _tick(1))

    def dismiss_animated(self) -> None:
        """播放约 110ms 向下微滑与渐隐动画后销毁低电量弹窗。"""
        if self._win is None or not self._win.winfo_exists():
            self.dismiss()
            return
        if self._timer_id is not None:
            try:
                self._root.after_cancel(self._timer_id)
            except Exception:
                pass
            self._timer_id = None
        if self._anim_id is not None:
            try:
                self._root.after_cancel(self._anim_id)
            except Exception:
                pass
            self._anim_id = None

        x, target_y, w, h = self._geom_pos
        end_y = target_y + neu_ui.sx(18, self._win)
        start_alpha = self._alpha_val
        steps = 6

        def _tick(step: int) -> None:
            if self._win is None or not self._win.winfo_exists():
                self._anim_id = None
                return
            t = min(1.0, step / float(steps))
            ease = t * t
            cy = int(round(target_y + (end_y - target_y) * ease))
            ca = max(0.0, start_alpha * (1.0 - ease))
            try:
                self._win.attributes("-alpha", ca)
                self._win.geometry(f"{w}x{h}+{x}+{cy}")
            except Exception:
                pass
            if step < steps:
                self._anim_id = self._root.after(18, lambda: _tick(step + 1))
            else:
                self._anim_id = None
                self.dismiss()

        self._anim_id = self._root.after(16, lambda: _tick(1))

    def _draw_countdown_bar(self) -> None:
        cv = self._bar_cv
        if cv is None or not cv.winfo_exists() or self._auto_close_ms <= 0:
            return
        w = max(24, cv.winfo_width() or neu_ui.sx(280, cv))
        h = max(3, cv.winfo_height() or neu_ui.sx(3, cv))
        ratio = max(0.0, min(1.0, self._remaining_ms / float(self._auto_close_ms)))
        cv.delete("all")
        fill_col = neu_ui.AMBER_CHARGE if self._hovered else neu_ui.RED_DANGER
        if ratio > 0.01:
            cv.create_rectangle(0, 0, w * ratio, h, fill=fill_col, outline="")

    def _schedule_auto_close(self) -> None:
        for attr in ("_timer_id", "_countdown_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self._root.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._auto_close_ms > 0 and self._win is not None and self._win.winfo_exists():
            if self._remaining_ms <= 0:
                self.dismiss_animated()
                return
            self._last_tick_ts = time.monotonic()
            self._timer_id = self._root.after(max(50, self._remaining_ms), self.dismiss_animated)
            self._countdown_id = self._root.after(100, self._tick_countdown)

    def _tick_countdown(self) -> None:
        self._countdown_id = None
        if self._win is None or not self._win.winfo_exists() or self._hovered:
            return
        now = time.monotonic()
        elapsed = int((now - self._last_tick_ts) * 1000.0)
        self._last_tick_ts = now
        self._remaining_ms = max(0, self._remaining_ms - max(0, elapsed))
        self._draw_countdown_bar()
        if self._remaining_ms > 0:
            self._countdown_id = self._root.after(100, self._tick_countdown)

    def _on_enter(self, _e=None) -> None:
        self._hovered = True
        for attr in ("_timer_id", "_countdown_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self._root.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._draw_countdown_bar()

    def _on_leave(self, _e=None) -> None:
        if self._win is None or not self._win.winfo_exists():
            return
        try:
            px, py = self._win.winfo_pointerxy()
            over = self._win.winfo_containing(px, py)
            if over is not None and over.winfo_toplevel() is self._win:
                return
        except Exception:
            pass
        self._hovered = False
        self._draw_countdown_bar()
        if self._anim_id is None and self._auto_close_ms > 0:
            self._schedule_auto_close()

    def show_notice(
        self,
        title: str,
        message: str,
        *,
        auto_close_ms: int = 4500,
        alpha: float = 0.88,
        device_key: Optional[str] = None,
    ) -> None:
        """首次最小化至托盘或充满电时弹出的轻提示气泡，告知用户后台常驻与左键唤起/定位方式。"""
        self.dismiss()
        self._primary_key = device_key
        transp = neu_ui.TRANSP_KEY
        win = tk.Toplevel(self._root)
        win.withdraw()
        win.configure(bg=transp)
        alpha_val = max(0.2, min(1.0, float(alpha)))
        self._alpha_val = alpha_val
        try:
            win.attributes("-transparentcolor", transp)
            win.attributes("-alpha", 0.08)
        except Exception:
            pass
        neu_ui.enable_frameless_taskbar(win, no_activate=True)

        card = neu_ui.NeuCard(
            win, radius=18, pad=10, inner_pad=(16, 12), off=6, box_r=3, inset=False, outer_bg=transp
        )
        card.pack(fill="both", expand=True)
        top = tk.Frame(card.content, bg=neu_ui.BASE)
        top.pack(fill="x")
        neu_ui.NeuRoleBadge(top, "controller", size=neu_ui.sx(28, win), color=neu_ui.BLURPLE_BRAND).pack(
            side="left", padx=(0, neu_ui.sx(10, win))
        )
        col = tk.Frame(top, bg=neu_ui.BASE)
        col.pack(side="left", fill="x", expand=True)
        tk.Label(
            col, text=title, bg=neu_ui.BASE, fg=neu_ui.TEXT,
            font=neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=win), anchor="w",
        ).pack(fill="x")
        tk.Label(
            col, text=message, bg=neu_ui.BASE, fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=win), anchor="w",
            justify="left", wraplength=neu_ui.sx(260, win),
        ).pack(fill="x", pady=(2, 0))

        bar_cv = tk.Canvas(
            card.content, height=max(3, neu_ui.sx(3, win)), bg=neu_ui.SEP_DARK, bd=0, highlightthickness=0, takefocus=0
        )
        bar_cv.pack(fill="x", pady=(neu_ui.sx(8, win), 0))
        self._bar_cv = bar_cv

        win.update_idletasks()
        w = max(neu_ui.sx(310, win), win.winfo_reqwidth())
        h = max(neu_ui.sx(90, win), win.winfo_reqheight())
        _wl, _wt, wr, wb = neu_ui.get_work_area()
        margin = neu_ui.sx(16, win)
        x = max(16, wr - w - margin)
        target_y = max(16, wb - h - margin)
        start_y = target_y + neu_ui.sx(18, win)
        self._geom_pos = (x, target_y, w, h)
        win.geometry(f"{w}x{h}+{x}+{start_y}")
        win.deiconify()
        neu_ui.enable_frameless_taskbar(win, no_activate=True)
        try:
            win.attributes("-transparentcolor", transp)
            win.attributes("-topmost", True)
        except Exception:
            pass
        hwnd = neu_ui._root_hwnd(win)
        if hwnd:
            user32.SetWindowPos(
                wintypes.HWND(hwnd), wintypes.HWND(-1), x, start_y, w, h, 0x0010 | 0x0040
            )
        self._win = win
        self._hovered = False
        self._auto_close_ms = int(auto_close_ms)
        self._remaining_ms = int(auto_close_ms)
        self._draw_countdown_bar()
        win.bind("<Enter>", self._on_enter, add="+")
        win.bind("<Leave>", self._on_leave, add="+")
        card.bind("<Button-1>", lambda _e: self._open_and_close(), add="+")
        self._animate_in(x, start_y, target_y, w, h, alpha_val)

    def _open_and_close(self) -> None:
        pk = self._primary_key
        self.dismiss()
        if pk and self._on_open_device is not None:
            self._on_open_device(pk)
        else:
            self._on_open_main()

    def _snooze_and_close(self, key: str, minutes: int = 30) -> None:
        self.dismiss_animated()
        if self._on_snooze is not None:
            self._on_snooze(key, minutes)

    def dismiss(self) -> None:
        for attr in ("_anim_id", "_timer_id", "_countdown_id"):
            aid = getattr(self, attr, None)
            if aid is not None:
                try:
                    self._root.after_cancel(aid)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._bar_cv = None
        if self._win is not None and self._win.winfo_exists():
            try:
                self._win.destroy()
            except Exception:
                pass
        self._win = None


class LowBatteryMonitor:
    """
    分级防骚扰低电量检测器（#3 分级升级 + 可配冷却 + #9 暂缓 snooze）：
      - critical (level <= 5%): 紧急持续提醒（突破常规冷却）
      - urgent   (level <= min(10%, threshold)): 强调提醒（级别升高时立即破冷却）
      - normal   (level < threshold): 常规提醒，按 notify_cooldown_min 冷却（默认 30 分钟）
    """

    def __init__(self, cooldown_sec: Optional[float] = None) -> None:
        self._cooldown_override = float(cooldown_sec) if cooldown_sec is not None else None
        self._notified: dict[str, tuple[float, int]] = {}
        self._snoozed_until: dict[str, float] = {}
        self._power_states: dict[str, str] = {}
        self._full_notified: set[str] = set()

    def _effective_cooldown(self, cfg: dict[str, Any]) -> float:
        if self._cooldown_override is not None:
            return self._cooldown_override
        try:
            mins = max(1, int(cfg.get("notify_cooldown_min", 30)))
            return float(mins) * 60.0
        except Exception:
            return 1800.0

    @staticmethod
    def _classify_tier(level: int, threshold: int) -> tuple[int, str]:
        if level <= 5:
            return 3, "critical"
        if level <= min(10, threshold):
            return 2, "urgent"
        return 1, "normal"

    def snooze(self, key: str, minutes: float = 30.0, now: Optional[float] = None) -> float:
        ts_now = float(time.time() if now is None else now)
        until = ts_now + max(1.0, float(minutes)) * 60.0
        self._snoozed_until[key] = until
        return until

    def snooze_global(self, minutes: float = 30.0, now: Optional[float] = None) -> float:
        return self.snooze("__ALL__", minutes=minutes, now=now)

    def clear_snooze(self, key: Optional[str] = None) -> None:
        if key is None:
            self._snoozed_until.clear()
        else:
            self._snoozed_until.pop(key, None)

    def is_snoozed(self, key: str = "__ALL__", now: Optional[float] = None) -> bool:
        ts_now = float(time.time() if now is None else now)
        for k in ("__ALL__", key):
            until = self._snoozed_until.get(k)
            if until is not None:
                if ts_now < until:
                    return True
                self._snoozed_until.pop(k, None)
        return False

    def has_active_snooze(self, now: Optional[float] = None) -> bool:
        ts_now = float(time.time() if now is None else now)
        for k, until in list(self._snoozed_until.items()):
            if ts_now < until:
                return True
            self._snoozed_until.pop(k, None)
        return False

    def snooze_remaining_min(self, key: str = "__ALL__", now: Optional[float] = None) -> int:
        ts_now = float(time.time() if now is None else now)
        best = max(
            float(self._snoozed_until.get("__ALL__", 0.0)),
            float(self._snoozed_until.get(key, 0.0)),
        )
        if best <= ts_now:
            return 0
        return max(1, int((best - ts_now + 59.0) // 60.0))

    def check_with_tiers(
        self,
        readings: list[devices.PeripheralReading],
        cfg: dict[str, Any],
        now: Optional[float] = None,
    ) -> list[tuple[devices.PeripheralReading, str]]:
        if not cfg.get("notify_enabled", True):
            return []
        ts_now = float(time.time() if now is None else now)
        base_cooldown = self._effective_cooldown(cfg)
        alerts: list[tuple[devices.PeripheralReading, str]] = []

        for r in readings:
            if r.offline or r.level is None or r.charging or r.full:
                continue
            threshold = tray_common.get_device_threshold(cfg, r.key)
            if r.level < threshold:
                if self.is_snoozed(r.key, now=ts_now):
                    continue
                tier_num, tier_name = self._classify_tier(int(r.level), threshold)
                prev = self._notified.get(r.key)
                if prev is None:
                    fire = True
                else:
                    last_ts, last_tier = prev
                    if tier_num > last_tier:
                        # 跨档升级（如 20% -> 10% -> 5%）立即突破冷却提醒
                        fire = True
                    elif tier_num >= 3:
                        # <=5% 临界电量：持续告警（至少每隔 min(base_cooldown, 60s) 提醒一次，兼容每轮心跳）
                        crit_gap = min(base_cooldown, 60.0) if self._cooldown_override is None else min(self._cooldown_override, 60.0)
                        fire = (ts_now - last_ts) >= max(1.0, crit_gap)
                    elif tier_num == 2:
                        urg_gap = min(base_cooldown, max(60.0, base_cooldown * 0.5)) if self._cooldown_override is None else self._cooldown_override
                        fire = (ts_now - last_ts) >= urg_gap
                    else:
                        fire = (ts_now - last_ts) >= base_cooldown

                if fire:
                    self._notified[r.key] = (ts_now, tier_num)
                    alerts.append((r, tier_name))
            elif r.level >= threshold + 2:
                self._notified.pop(r.key, None)
        return alerts

    def check_full_charges(
        self,
        readings: list[devices.PeripheralReading],
        cfg: dict[str, Any],
        now: Optional[float] = None,
    ) -> list[devices.PeripheralReading]:
        """仅在检测到真实的『充电中 -> 已充满/100%』边沿变化时触发一次充满提醒。"""
        enabled = bool(cfg.get("notify_enabled", True)) and bool(cfg.get("notify_full_charge", True))
        filled: list[devices.PeripheralReading] = []
        for r in readings:
            if r.offline or r.level is None:
                continue
            prev = self._power_states.get(r.key)
            if r.full or (prev == "charging" and int(r.level) >= 100):
                cur_state = "full"
            elif r.charging:
                cur_state = "charging"
            else:
                cur_state = "other"

            if prev == "charging" and cur_state == "full":
                if r.key not in self._full_notified:
                    self._full_notified.add(r.key)
                    if enabled and not self.is_snoozed(r.key, now=now):
                        filled.append(r)
            elif cur_state == "other" and int(r.level) <= 97:
                self._full_notified.discard(r.key)
            self._power_states[r.key] = cur_state
        return filled

    def check(
        self,
        readings: list[devices.PeripheralReading],
        cfg: dict[str, Any],
        now: Optional[float] = None,
    ) -> list[devices.PeripheralReading]:
        return [r for r, _tier in self.check_with_tiers(readings, cfg, now=now)]
