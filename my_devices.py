# -*- coding: utf-8 -*-
"""my_devices.py —— 我的 2.4G / 无线外设电量主面板（纯 tkinter + ctypes，零第三方依赖）

支持模式：
  - pythonw my_devices.py               启动桌面主面板（含常驻托盘与悬浮四宫格）
  - pythonw my_devices.py --minimized   启动时最小化至右下角手柄托盘图标
  - python  my_devices.py --once        命令行输出全部外设电量后退出
  - python  my_devices.py --json        JSON 输出全部外设读数与续航预估
  - python  my_devices.py --autostart on|off
  - python  my_devices.py --install-links
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog
from typing import Any, Optional

import battery_history
import devices
import neu_ui
import tray_common
import tray_service
import win_icon
import win_link
import win_shell

try:
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.WinDLL("user32").SetProcessDPIAware()
    except Exception:
        pass

def _sync_palette_globals() -> None:
    """在主题切换后刷新本模块派生色引用，保证与 neu_ui 双主题令牌表实时一致。"""
    global BG, FG, MUTED, DIM, DISABLED_TXT, BLURPLE, BLURPLE_LINK, CORAL
    global RED_DANGER, AMBER_CHARGE, SUCCESS, INFO, GRAY_DISABLED, SEP_DARK, SEP_LIGHT, STATUS_PILL_STYLE
    BG = neu_ui.BASE
    FG = neu_ui.TEXT
    MUTED = neu_ui.TEXT_MUTED
    DIM = neu_ui.TEXT_DIM
    DISABLED_TXT = neu_ui.TEXT_DISABLED
    BLURPLE = neu_ui.BLURPLE_BRAND
    BLURPLE_LINK = neu_ui.BLURPLE_LINK
    CORAL = neu_ui.CORAL
    RED_DANGER = neu_ui.RED_DANGER
    AMBER_CHARGE = neu_ui.AMBER_CHARGE
    SUCCESS = neu_ui.SUCCESS
    INFO = neu_ui.INFO
    GRAY_DISABLED = neu_ui.GRAY_DISABLED
    SEP_DARK = neu_ui.SEP_DARK
    SEP_LIGHT = neu_ui.SEP_LIGHT
    STATUS_PILL_STYLE = neu_ui.STATUS_PILL_STYLES


def install_global_exception_hooks(app: Optional["BatteryPanelApp"] = None):
    """Install sys, threading, and Tk exception hooks that log and alert via app UI queue."""
    import traceback

    prev_sys = sys.excepthook
    prev_thread = getattr(threading, "excepthook", None)
    root = getattr(app, "root", None) if app is not None else None
    prev_tk = getattr(root, "report_callback_exception", None) if root is not None else None

    def _dispatch_ui_error(where: str, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        tb_txt = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        try:
            _log_app_error(where, tb_txt)
        except Exception:
            try:
                tray_common.log_event("ERROR", where, tb_txt)
            except Exception:
                pass
        if app is not None and not getattr(app, "_closing", True):
            summary = f"{getattr(exc_type, '__name__', 'Error')}: {exc_val}"
            try:
                app.ui_queue.put(("unhandled_error", {"where": where, "summary": summary, "detail": tb_txt}))
                app.request_pump()
            except Exception:
                pass

    def _sys_hook(exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            try:
                prev_sys(exc_type, exc_val, exc_tb)
            except Exception:
                pass
            return
        _dispatch_ui_error("sys.excepthook", exc_type, exc_val, exc_tb)

    def _thread_hook(args: Any) -> None:
        exc_type = getattr(args, "exc_type", Exception)
        if issubclass(exc_type, SystemExit):
            return
        th_name = getattr(getattr(args, "thread", None), "name", "worker")
        _dispatch_ui_error(
            f"thread:{th_name}",
            exc_type,
            getattr(args, "exc_value", None),
            getattr(args, "exc_traceback", None),
        )

    def _tk_hook(exc: Any, val: Any, tb: Any) -> None:
        _dispatch_ui_error("tk.callback", exc, val, tb)

    sys.excepthook = _sys_hook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _thread_hook
    if root is not None:
        try:
            root.report_callback_exception = _tk_hook
        except Exception:
            pass

    def _restore() -> None:
        if sys.excepthook is _sys_hook:
            sys.excepthook = prev_sys
        if prev_thread is not None and getattr(threading, "excepthook", None) is _thread_hook:
            threading.excepthook = prev_thread
        if root is not None and prev_tk is not None:
            try:
                if getattr(root, "report_callback_exception", None) is _tk_hook:
                    root.report_callback_exception = prev_tk
            except Exception:
                pass

    return _restore


BG = neu_ui.BASE
FG = neu_ui.TEXT
MUTED = neu_ui.TEXT_MUTED
DIM = neu_ui.TEXT_DIM
DISABLED_TXT = neu_ui.TEXT_DISABLED
BLURPLE = neu_ui.BLURPLE_BRAND
BLURPLE_LINK = neu_ui.BLURPLE_LINK
CORAL = neu_ui.CORAL
RED_DANGER = neu_ui.RED_DANGER
AMBER_CHARGE = neu_ui.AMBER_CHARGE
SUCCESS = neu_ui.SUCCESS
INFO = neu_ui.INFO
GRAY_DISABLED = neu_ui.GRAY_DISABLED
SEP_DARK = neu_ui.SEP_DARK
SEP_LIGHT = neu_ui.SEP_LIGHT

# 统一布局基准常量：页眉/页脚/卡片栅格共用 OUTER_CONTENT_INSET，杜绝 2dp 对齐错位
CARD_PAD = 14
CARD_GAP = 6
OUTER_CONTENT_INSET = CARD_PAD + CARD_GAP
CARD_MIN_WIDTH_DP = 320
CARD_MAX_SINGLE_DP = 560

# 统一动作词表（刷新=读电量，扫描=枚举设备）
ACTION_REFRESHING = "正在刷新外设电量..."
ACTION_SCANNING = "正在扫描已接入的无线 / 有线外设..."

INTERVAL_CHOICES = [
    ("30 秒", 30),
    ("1 分钟", 60),
    ("2 分钟", 120),
    ("5 分钟", 300),
    ("10 分钟", 600),
]

BG_INTERVAL_CHOICES = [
    ("1 分钟", 60),
    ("2 分钟", 120),
    ("5 分钟 (默认)", 300),
    ("10 分钟", 600),
    ("15 分钟", 900),
]

THRESHOLD_CHOICES = [
    ("10%", 10),
    ("15%", 15),
    ("20% (默认)", 20),
    ("25%", 25),
    ("30%", 30),
]

COOLDOWN_CHOICES = [
    ("10 分钟", 10),
    ("30 分钟 (默认)", 30),
    ("60 分钟", 60),
]

SORT_MODE_CHOICES = [
    ("默认顺序", "default"),
    ("低电优先", "low_first"),
    ("名称排序", "name"),
    ("最近使用", "recent"),
]

TRAY_STYLE_CHOICES = [
    ("数字", "digit"),
    ("环形", "ring"),
    ("极简点", "dot"),
    ("按最低设备", "lowest_device"),
]

TRAY_VIS_CHOICES = [
    ("常驻托盘", "always"),
    ("仅告警时显示", "alert_only"),
]

LOG_LEVEL_CHOICES = [
    ("DEBUG", "DEBUG"),
    ("INFO (默认)", "INFO"),
    ("WARN", "WARN"),
    ("ERROR", "ERROR"),
]

LOG_FILTER_CHOICES = [
    ("全部级别", "ALL"),
    ("DEBUG+", "DEBUG"),
    ("INFO+", "INFO"),
    ("WARN+", "WARN"),
    ("仅 ERROR", "ERROR"),
]

ROLE_CHOICES = [
    ("鼠标", "mouse"),
    ("键盘", "keyboard"),
    ("耳机", "headset"),
    ("手柄", "controller"),
    ("其他", "other"),
]

TOAST_ALPHA_CHOICES = [
    ("60% (默认)", 60),
    ("80%", 80),
    ("92%", 92),
]

THEME_CHOICES = [
    ("跟随系统", "system"),
    ("浅色", "light"),
    ("深色", "dark"),
]

FONT_SCALE_CHOICES = [
    ("标准", "standard"),
    ("大", "large"),
    ("特大", "xlarge"),
]

TRAY_HOVER_CHOICES = [
    ("四宫格浮层 (默认)", "grid"),
    ("系统原生气泡", "native"),
]

RETENTION_DAY_CHOICES = [
    ("1 天", 1),
    ("3 天", 3),
    ("7 天 (默认)", 7),
    ("14 天", 14),
    ("30 天", 30),
]

FILTER_MODE_CHOICES = [
    ("全部", "all"),
    ("在线", "online"),
    ("低电", "low"),
    ("充电中", "charging"),
    ("离线", "offline"),
]


def filter_readings(
    readings: list[devices.PeripheralReading],
    *,
    query: str = "",
    status_filter: str = "all",
    cfg: Optional[dict[str, Any]] = None,
) -> list[devices.PeripheralReading]:
    """按关键字（名称/型号/VID:PID/连接）与状态过滤外设列表。"""
    q = str(query or "").strip().lower()
    st = str(status_filter or "all").strip().lower()
    out: list[devices.PeripheralReading] = []
    for r in readings:
        if q:
            hay = " ".join((r.name, r.product, r.vidpid_text, r.connection, r.transport, r.key)).lower()
            if q not in hay:
                continue
        if st == "online" and (r.offline or r.level is None):
            continue
        elif st == "offline" and not r.offline:
            continue
        elif st == "charging" and (r.offline or not r.charging):
            continue
        elif st == "low":
            th = tray_common.get_device_threshold(cfg, r.key) if cfg is not None else 20
            if r.offline or r.level is None or r.charging or r.full or int(r.level) >= th:
                continue
        out.append(r)
    return out

STATUS_PILL_STYLE = neu_ui.STATUS_PILL_STYLES

LOG_FILE = tray_common.LOG_FILE


def _truncate_visual(text: str, max_units: int = 58) -> str:
    """按全半角视觉宽度截断长说明，配合动态脚注保持卡片对齐。"""
    s = " ".join((text or "").split())
    acc = 0
    for idx, ch in enumerate(s):
        acc += 2 if ord(ch) > 127 else 1
        if acc > max_units:
            return s[: max(1, idx - 1)].rstrip() + "…"
    return s


def _log_app_error(source: str, detail: str) -> None:
    tray_common.log_event("ERROR", source, detail)


def _classify_scan_error(raw: str) -> tuple[str, str]:
    """将扫描阶段底层异常归纳为原因 + 建议动作，详细栈保留在本地日志中。"""
    low = (raw or "").lower()
    if any(k in low for k in ("access is denied", "permission", "拒绝访问", "winerror 5")):
        return "系统权限不足或设备接口被占用", "可尝试以管理员身份重启，或关闭其他外设驱动后重试"
    if any(k in low for k in ("bth", "bluetooth", "蓝牙", "gatt", "0x8007")):
        return "蓝牙服务或连接通道暂不可用", "请确认系统蓝牙已开启并完成外设配对"
    if any(k in low for k in ("timeout", "timed out", "超时")):
        return "读取外设响应超时", "请轻晃鼠标/唤醒外设后点击重试"
    if any(k in low for k in ("not found", "no such", "cannot find", "hid")) or ("device" in low and "no" in low):
        return "HID / 无线接收器通道打开失败", "请重新插拔 2.4G 接收器或检查线缆连接"
    return "刷新外设电量时发生异常", "可查看运行日志或点击重试"


def _friendly_error_message(raw: str) -> str:
    reason, action = _classify_scan_error(raw)
    return f"{reason}，{action}"


def _format_relative_age(ts: Optional[float], now: Optional[float] = None) -> tuple[str, bool]:
    """将同步时间戳转为轻量相对时间文案，超过两个周期（>5 分钟）标记为过期弱化。"""
    if not ts:
        return "", False
    cur = time.time() if now is None else float(now)
    diff = max(0.0, cur - float(ts))
    if diff < 60:
        return "刚刚", False
    mins = int(diff // 60)
    if mins < 60:
        return f"{mins} 分钟前", mins >= 5
    hrs = int(mins // 60)
    if hrs < 24:
        return f"{hrs} 小时前", True
    days = int(hrs // 24)
    return f"{days} 天前", True


def install_desktop_links() -> list[str]:
    """在桌面创建指向当前程序的安全链接/快捷方式。"""
    desk = win_shell.desktop_dir()
    results: list[str] = []
    panel_lnk = os.path.join(desk, "我的外设电量.lnk")
    target = sys.executable if tray_common.IS_FROZEN else tray_common.pythonw_exe()
    args = "" if tray_common.IS_FROZEN else f'"{tray_common.PANEL_SCRIPT}"'
    ico = sys.executable if tray_common.IS_FROZEN else (tray_common.app_icon_path() or "")
    if win_shell.create_shortcut(
        lnk_path=panel_lnk,
        target=target,
        arguments=args,
        workdir=tray_common.BASE_DIR,
        description="打开 2.4G 无线外设电量面板",
        icon=ico,
    ):
        results.append(f"桌面快捷方式: {panel_lnk}")

    if not tray_common.IS_FROZEN:
        bat_src = os.path.join(tray_common.BASE_DIR, "我的设备电量.bat")
        if os.path.isfile(bat_src):
            bat_dst = os.path.join(desk, "我的设备电量.bat")
            try:
                kind = win_link.create_file_link(bat_src, bat_dst)
                results.append(f"启动脚本链接({kind}): {bat_dst}")
            except OSError:
                pass
    return results


class DeviceCard(neu_ui.NeuCard):
    """新拟物派外设电量 Soft Card：支持标准详情卡与单行紧凑卡 (#22)、置顶 (#18)、类型覆写 (#21)、电压/充放 ETA (#46-48) 与排障向导 (#41)。"""

    def __init__(
        self,
        parent: tk.Widget,
        reading: devices.PeripheralReading,
        history_data: dict[str, Any],
        on_rename,
        on_toggle_hide,
        on_open_debug,
        *,
        is_hidden: bool = False,
        spark_hours: int = 24,
        on_spark_hours_change=None,
        low_threshold: int = 20,
        custom_threshold: Optional[int] = None,
        global_threshold: int = 20,
        compact: bool = False,
        on_pin=None,
        on_set_role=None,
        on_forget=None,
        on_snooze=None,
        on_why_unread=None,
        on_open_history=None,
        on_set_threshold=None,
        on_set_capacity=None,
    ) -> None:
        self._compact = bool(compact)
        super().__init__(
            parent,
            radius=18 if self._compact else 24,
            pad=CARD_PAD,
            inner_pad=(16, 10) if self._compact else (20, 14),
            off=5 if self._compact else 6,
            box_r=3 if self._compact else 4,
            inset=False,
            hoverable=True,
        )
        self.reading = reading
        self._history_data = history_data
        self._is_hidden = is_hidden
        self._low_threshold = int(low_threshold)
        self._custom_threshold = custom_threshold
        self._global_threshold = int(global_threshold)
        self._surface_bg = neu_ui.BASE
        self._foot_raw = ""
        self._name_raw = reading.name
        self._pending_sync = False
        self._error_stale_ts = ""
        self._num_level: Optional[int] = None
        self._num_prefix = ""
        self._num_tween_id: Optional[str] = None
        self._num_fg_target = neu_ui.TEXT
        self.on_rename = on_rename
        self.on_toggle_hide = on_toggle_hide
        self.on_open_debug = on_open_debug
        self._on_spark_hours_change = on_spark_hours_change
        self.on_pin = on_pin
        self.on_set_role = on_set_role
        self.on_forget = on_forget
        self.on_snooze = on_snooze
        self.on_why_unread = on_why_unread
        self.on_open_history = on_open_history
        self.on_set_threshold = on_set_threshold
        self.on_set_capacity = on_set_capacity

        inner = self.content
        self._has_probe = bool(
            ((not reading.offline) and reading.level is None and reading.transport == "HID")
            or (reading.manual and reading.level is None)
        )

        self._btn_probe = None
        self._btn_why = None
        self._probe_row = None
        self.spark: Optional[neu_ui.NeuSparkline] = None

        if self._compact:
            row = tk.Frame(inner, bg=neu_ui.BASE)
            row.pack(fill="x")

            self._badge_icon = neu_ui.NeuRoleBadge(row, reading.role, color=neu_ui.TEXT_MUTED, inset=True)
            self._badge_icon.pack(side="left", padx=(0, neu_ui.sx(8, parent)))

            left_col = tk.Frame(row, bg=neu_ui.BASE)
            left_col.pack(side="left", fill="x", expand=True, padx=(0, neu_ui.sx(8, parent)))

            top_line = tk.Frame(left_col, bg=neu_ui.BASE)
            top_line.pack(fill="x")
            self._name_font = neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=parent)
            self._name_font_obj = tk.font.Font(root=parent, font=self._name_font)
            self._lbl_name = tk.Label(
                top_line, text=reading.name, bg=neu_ui.BASE, fg=neu_ui.TEXT,
                font=self._name_font, anchor="w",
            )
            self._lbl_name.pack(side="left")
            self._name_tip = neu_ui.attach_tooltip(self._lbl_name, "")

            self._lbl_role = tk.Label(
                top_line, text="", bg=neu_ui.BASE, fg=neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=parent),
            )
            self._lbl_role.pack(side="left", padx=(neu_ui.sx(6, parent), 0))
            self._lbl_role_sep = tk.Label(top_line, text="·", bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent))
            self._lbl_role_sep.pack(side="left", padx=(neu_ui.sx(2, parent), neu_ui.sx(2, parent)))
            self._lbl_vidpid = tk.Label(top_line, text="", bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=parent))
            self._lbl_vidpid.pack(side="left")
            self._lbl_conn = tk.Label(top_line, text="", bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent))
            self._lbl_conn.pack(side="left", padx=(neu_ui.sx(4, parent), 0))

            sub_line = tk.Frame(left_col, bg=neu_ui.BASE)
            sub_line.pack(fill="x", pady=(1, 0))
            self._lbl_stat = tk.Label(sub_line, text="", bg=neu_ui.BASE, fg=neu_ui.TEXT_MUTED, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent), anchor="w")
            self._lbl_stat.pack(side="left")
            self._lbl_est = tk.Label(sub_line, text="", bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent), anchor="w")
            self._lbl_est.pack(side="left", padx=(neu_ui.sx(8, parent), 0))

            mid_bar = tk.Frame(row, bg=neu_ui.BASE, width=neu_ui.sx(130, parent))
            mid_bar.pack(side="left", padx=(neu_ui.sx(6, parent), neu_ui.sx(8, parent)))
            mid_bar.pack_propagate(False)
            self._bar = neu_ui.NeuProgressBar(mid_bar, height=neu_ui.sx(10, parent))
            self._bar.pack(fill="x", pady=neu_ui.sx(10, parent))

            right_col = tk.Frame(row, bg=neu_ui.BASE)
            right_col.pack(side="right")

            num_font = neu_ui.display_font(neu_ui.FONT_SUBTITLE + 4, "bold", root=parent)
            num_fobj = tk.font.Font(root=parent, font=num_font)
            self._lbl_num = tk.Label(
                right_col, text="--", width=5, bg=neu_ui.BASE, fg=neu_ui.TEXT, font=num_font, anchor="e"
            )
            self._lbl_num.pack(side="left", padx=(0, neu_ui.sx(6, parent)))
            self._num_tip = neu_ui.attach_tooltip(self._lbl_num, "")

            self._pill = neu_ui.NeuStatusPill(right_col, text="", min_width_dp=56)
            self._pill.pack(side="left", padx=(0, neu_ui.sx(6, parent)))
            self._pill_tip = neu_ui.attach_tooltip(self._pill, "")

            self._btn_pin = neu_ui.NeuButton(
                right_col,
                text="★" if reading.pinned else "置顶",
                command=lambda: self.on_pin(self.reading) if self.on_pin else None,
                radius=10, pad=4, font_size=neu_ui.FONT_CAPTION, padx=8, pady=4,
                fg=neu_ui.BLURPLE_LINK if reading.pinned else neu_ui.TEXT_MUTED,
                tooltip="置顶到第一行（长按或右键更多操作）",
            )
            self._btn_pin.pack(side="left", padx=(0, neu_ui.sx(3, parent)))

            self._btn_rename = neu_ui.NeuButton(
                right_col, text="改名", command=lambda: self.on_rename(self.reading),
                radius=10, pad=4, font_size=neu_ui.FONT_CAPTION, padx=8, pady=4, fg=neu_ui.TEXT_MUTED,
            )
            self._btn_hide = neu_ui.NeuButton(
                right_col,
                text="取消隐藏" if is_hidden else "隐藏",
                command=lambda: self.on_toggle_hide(self.reading),
                radius=10, pad=4, font_size=neu_ui.FONT_CAPTION, padx=8, pady=4,
                fg=neu_ui.BLURPLE_LINK if is_hidden else neu_ui.TEXT_MUTED,
            )

            self._btn_more = neu_ui.NeuButton(
                right_col,
                text="更多",
                command=self._open_context_menu_from_btn,
                radius=10, pad=4, font_size=neu_ui.FONT_CAPTION, padx=7, pady=4,
                fg=neu_ui.TEXT_MUTED,
                tooltip="设备类型 / 暂缓提醒 / 排障 / 不再显示",
            )
            self._btn_more.pack(side="left")

            if self._has_probe and self.on_why_unread:
                self._btn_why = neu_ui.NeuButton(
                    right_col,
                    text="排障",
                    command=lambda: self.on_why_unread(self.reading),
                    accent=True,
                    radius=10, pad=4, font_size=neu_ui.FONT_CAPTION, padx=8, pady=4,
                    tooltip="为什么读不到电量？三步快速排查",
                )
                self._btn_why.pack(side="left", padx=(neu_ui.sx(3, parent), 0))

            self._foot_font = neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent)
            self._foot_font_obj = tk.font.Font(root=parent, font=self._foot_font)
            self._lbl_foot = tk.Label(left_col, text="", bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=self._foot_font, anchor="w", wraplength=neu_ui.sx(420, parent))
            self._foot_tip = neu_ui.attach_tooltip(self._lbl_name, "")
            self._plain_bg_widgets = [
                inner, row, left_col, top_line, sub_line, mid_bar, right_col,
                self._lbl_name, self._lbl_role, self._lbl_role_sep, self._lbl_vidpid,
                self._lbl_conn, self._lbl_stat, self._lbl_est, self._lbl_num, self._lbl_foot,
            ]
        else:
            # ---- 头部：角色图标 + 类型/等宽VIDPID + 置顶/改名/隐藏/更多 ----
            top = tk.Frame(inner, bg=neu_ui.BASE)
            top.pack(fill="x")

            self._badge_icon = neu_ui.NeuRoleBadge(top, reading.role, color=neu_ui.TEXT_MUTED, inset=True)
            self._badge_icon.pack(side="left", padx=(0, neu_ui.sx(8, parent)))

            role_meta = tk.Frame(top, bg=neu_ui.BASE)
            role_meta.pack(side="left", fill="y")
            role_top = tk.Frame(role_meta, bg=neu_ui.BASE)
            role_top.pack(anchor="w")
            self._lbl_role = tk.Label(
                role_top,
                text="",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=parent),
                anchor="w",
            )
            self._lbl_role.pack(side="left")
            self._lbl_role_sep = tk.Label(
                role_top,
                text=neu_ui.TEXT_SEP,
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent),
            )
            self._lbl_role_sep.pack(side="left")
            self._lbl_vidpid = tk.Label(
                role_top,
                text="",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=parent),
                anchor="w",
            )
            self._lbl_vidpid.pack(side="left")

            self._lbl_conn = tk.Label(
                role_meta,
                text="",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent),
                anchor="w",
            )
            self._lbl_conn.pack(anchor="w")

            btn_box = tk.Frame(top, bg=neu_ui.BASE)
            btn_box.pack(side="right")
            self._btn_pin = neu_ui.NeuButton(
                btn_box,
                text="★已置顶" if reading.pinned else "置顶",
                command=lambda: self.on_pin(self.reading) if self.on_pin else None,
                radius=12,
                pad=6,
                font_size=neu_ui.FONT_CAPTION,
                padx=9,
                pady=5,
                fg=neu_ui.BLURPLE_LINK if reading.pinned else neu_ui.TEXT_MUTED,
                tooltip="取消置顶" if reading.pinned else "置顶此设备到卡片首位",
            )
            self._btn_pin.pack(side="left", padx=(0, neu_ui.sx(4, parent)))
            self._btn_rename = neu_ui.NeuButton(
                btn_box,
                text="改名",
                command=lambda: self.on_rename(self.reading),
                radius=12,
                pad=6,
                font_size=neu_ui.FONT_CAPTION,
                padx=10,
                pady=5,
                fg=neu_ui.TEXT_MUTED,
                tooltip="自定义设备显示名称",
            )
            self._btn_rename.pack(side="left", padx=(0, neu_ui.sx(4, parent)))
            self._btn_hide = neu_ui.NeuButton(
                btn_box,
                text="取消隐藏" if is_hidden else "隐藏",
                command=lambda: self.on_toggle_hide(self.reading),
                radius=12,
                pad=6,
                font_size=neu_ui.FONT_CAPTION,
                padx=10,
                pady=5,
                fg=neu_ui.BLURPLE_LINK if is_hidden else neu_ui.TEXT_MUTED,
                tooltip="恢复在主界面展示" if is_hidden else "暂时隐藏此设备卡片",
            )
            self._btn_hide.pack(side="left", padx=(0, neu_ui.sx(4, parent)))
            self._btn_more = neu_ui.NeuButton(
                btn_box,
                text="更多",
                command=self._open_context_menu_from_btn,
                radius=12,
                pad=6,
                font_size=neu_ui.FONT_CAPTION,
                padx=9,
                pady=5,
                fg=neu_ui.TEXT_MUTED,
                tooltip="修改设备类型 / 暂缓提醒 / 排障 / 不再显示",
            )
            self._btn_more.pack(side="left")

            # ---- 设备名称 + 最小宽度对称状态胶囊 ----
            name_row = tk.Frame(inner, bg=neu_ui.BASE)
            name_row.pack(fill="x", pady=(neu_ui.sx(8, parent), neu_ui.sx(4, parent)))

            self._pill = neu_ui.NeuStatusPill(name_row, text="", min_width_dp=64)
            self._pill.pack(side="right", padx=(neu_ui.sx(8, parent), 0))
            self._pill_tip = neu_ui.attach_tooltip(self._pill, "")

            self._name_font = neu_ui.ui_font(neu_ui.FONT_SUBTITLE, "bold", root=parent)
            self._name_font_obj = tk.font.Font(root=parent, font=self._name_font)
            self._lbl_name = tk.Label(
                name_row,
                text=reading.name,
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT,
                font=self._name_font,
                anchor="w",
            )
            self._lbl_name.pack(side="left", fill="x", expand=True)
            self._name_tip = neu_ui.attach_tooltip(self._lbl_name, "")

            # ---- 电量数字 + 状态/电压 + 充放电 ETA ----
            mid = tk.Frame(inner, bg=neu_ui.BASE)
            mid.pack(fill="x", pady=(0, neu_ui.sx(8, parent)))

            num_font = neu_ui.display_font(neu_ui.FONT_NUMERIC, "bold", root=parent)
            num_fobj = tk.font.Font(root=parent, font=num_font)
            num_px_w = max(num_fobj.measure("100%"), num_fobj.measure("~99%")) + neu_ui.sx(6, parent)
            num_px_h = num_fobj.metrics("linespace") + neu_ui.sx(2, parent)
            num_slot = tk.Frame(mid, width=num_px_w, height=num_px_h, bg=neu_ui.BASE)
            num_slot.pack_propagate(False)
            num_slot.pack(side="left")

            self._lbl_num = tk.Label(
                num_slot,
                text="--",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT,
                font=num_font,
                anchor="w",
            )
            self._lbl_num.pack(fill="both", expand=True)
            self._num_tip = neu_ui.attach_tooltip(self._lbl_num, "")

            meta = tk.Frame(mid, bg=neu_ui.BASE)
            meta.pack(side="left", padx=(neu_ui.sx(8, parent), 0), pady=(neu_ui.sx(2, parent), 0), fill="x", expand=True)

            self._lbl_stat = tk.Label(
                meta,
                text="",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent),
                anchor="w",
            )
            self._lbl_stat.pack(fill="x")

            self._lbl_est = tk.Label(
                meta,
                text="",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent),
                anchor="w",
            )
            self._lbl_est.pack(fill="x", pady=(1, 0))

            # ---- 电量进度条 ----
            bar_h = max(neu_ui.sx(12, parent), int(round(neu_ui.sx(13, parent) * neu_ui.font_scale_ratio())))
            self._bar = neu_ui.NeuProgressBar(inner, height=bar_h)
            self._bar.pack(fill="x", pady=(0, neu_ui.sx(8, parent)))

            # ---- 历史电量折线微卡片 ----
            self.spark = neu_ui.NeuSparkline(
                inner,
                initial_hours=spark_hours,
                on_window_change=self._handle_spark_window,
            )
            self.spark.pack(fill="x", pady=(0, neu_ui.sx(8, parent)))

            self._foot_font = neu_ui.ui_font(neu_ui.FONT_CAPTION, root=parent)
            self._foot_font_obj = tk.font.Font(root=parent, font=self._foot_font)
            try:
                init_wrap = max(neu_ui.sx(240, parent), int(parent.winfo_width() or neu_ui.sx(320, parent)) - neu_ui.sx(68, parent))
            except Exception:
                init_wrap = neu_ui.sx(300, parent)
            self._lbl_foot = tk.Label(
                inner,
                text="",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=self._foot_font,
                anchor="nw",
                justify="left",
                wraplength=init_wrap,
            )
            self._lbl_foot.pack(fill="x")
            self._foot_tip = neu_ui.attach_tooltip(self._lbl_foot, "")
            inner.bind("<Configure>", self._on_inner_resize, add="+")

            if self._has_probe:
                self._probe_row = tk.Frame(inner, bg=neu_ui.BASE)
                self._probe_row.pack(fill="x", pady=(neu_ui.sx(8, parent), 0))
                self._btn_probe = neu_ui.NeuButton(
                    self._probe_row,
                    text="在调试工具中探测 / 标定 →",
                    command=lambda: self.on_open_debug(self.reading),
                    accent=True,
                    radius=14,
                    pad=6,
                    font_size=neu_ui.FONT_CAPTION,
                    padx=11,
                    pady=4,
                )
                self._btn_probe.pack(side="left", padx=(0, neu_ui.sx(6, parent)))
                if self.on_why_unread:
                    self._btn_why = neu_ui.NeuButton(
                        self._probe_row,
                        text="为什么读不到",
                        command=lambda: self.on_why_unread(self.reading),
                        radius=14,
                        pad=6,
                        font_size=neu_ui.FONT_CAPTION,
                        padx=10,
                        pady=4,
                        fg=neu_ui.TEXT_MUTED,
                        tooltip="启动三步诊断向导定位未能读取的原因",
                    )
                    self._btn_why.pack(side="left")

            self._plain_bg_widgets = [
                inner, top, role_meta, role_top, self._lbl_role, self._lbl_role_sep,
                self._lbl_vidpid, self._lbl_conn, btn_box, name_row, self._lbl_name,
                mid, num_slot, self._lbl_num, meta, self._lbl_stat, self._lbl_est, self._lbl_foot,
            ]
            if self._probe_row is not None:
                self._plain_bg_widgets.append(self._probe_row)

        for w in [self, *self._plain_bg_widgets]:
            try:
                w.bind("<Button-3>", self._open_context_menu_event, add="+")
            except Exception:
                pass

        self.bind("<Destroy>", self._on_device_card_destroy, add="+")
        self.update_reading(
            reading,
            history_data,
            is_hidden=is_hidden,
            spark_hours=spark_hours,
            low_threshold=low_threshold,
        )
        self.sync_hover_tree()

    def _open_context_menu_from_btn(self) -> None:
        cur_menu = getattr(self, "_card_menu", None)
        if cur_menu is not None and cur_menu.is_open():
            cur_menu.close(restore_focus=False)
            self._card_menu = None
            return
        try:
            x = self._btn_more.winfo_rootx() + self._btn_more.winfo_width()
            y = self._btn_more.winfo_rooty() + self._btn_more.winfo_height() + 4
            right_align = True
        except Exception:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            right_align = False
        self._show_card_menu(x, y, right_align=right_align)

    def _open_context_menu_event(self, event) -> str:
        self._show_card_menu(int(event.x_root), int(event.y_root), right_align=False)
        return "break"

    def _show_card_menu(self, x_root: int, y_root: int, *, right_align: bool = False) -> neu_ui.NeuPopupMenu:
        r = self.reading
        items: list[dict[str, Any]] = []
        if self.on_pin:
            items.append({
                "type": "item",
                "label": "取消置顶" if r.pinned else "★ 置顶到首位",
                "checked": bool(r.pinned),
                "command": lambda: self.on_pin(r),
            })
        items.append({
            "type": "item",
            "label": "重命名设备…",
            "command": lambda: self.on_rename(r),
        })
        if self.on_open_history:
            items.append({
                "type": "item",
                "label": "查看电量历史…",
                "command": lambda: self.on_open_history(r),
            })
        items.append({
            "type": "item",
            "label": "恢复显示此设备" if self._is_hidden else "暂时隐藏此设备",
            "command": lambda: self.on_toggle_hide(r),
        })
        if self.on_set_role:
            role_sub = [
                {
                    "type": "item",
                    "label": lbl,
                    "checked": (r.role == val),
                    "command": lambda v=val: self.on_set_role(r, v),
                }
                for lbl, val in ROLE_CHOICES
            ]
            items.append({
                "type": "submenu",
                "label": "修改设备类型",
                "items": role_sub,
            })
        if self.on_snooze and not r.offline:
            items.append({
                "type": "item",
                "label": "暂缓低电提醒 30 分钟",
                "command": lambda: self.on_snooze(r, 30),
            })
        if self.on_set_threshold:
            th_sub = [
                {
                    "type": "item",
                    "label": f"跟随全局 ({self._global_threshold}%)",
                    "checked": (self._custom_threshold is None),
                    "command": lambda: self.on_set_threshold(r, None),
                },
            ]
            for v in (10, 15, 20, 25, 30):
                th_sub.append({
                    "type": "item",
                    "label": f"{v}%",
                    "checked": (self._custom_threshold == v),
                    "command": lambda val=v: self.on_set_threshold(r, val),
                })
            th_sub.append({"type": "separator"})
            th_sub.append({
                "type": "item",
                "label": "自定义阈值…",
                "checked": (self._custom_threshold is not None and self._custom_threshold not in (10, 15, 20, 25, 30)),
                "command": lambda: self.on_set_threshold(r, "custom"),
            })
            items.append({
                "type": "submenu",
                "label": f"低电提醒阈值 ({self._low_threshold}%)" if self._custom_threshold is not None else "低电提醒阈值",
                "items": th_sub,
            })
        if self.on_set_capacity:
            items.append({
                "type": "item",
                "label": "设置电池容量 (mAh)…",
                "command": lambda: self.on_set_capacity(r),
            })
        if self.on_why_unread and (r.offline or r.level is None or r.transport == "HID"):
            items.append({"type": "separator"})
            items.append({
                "type": "item",
                "label": "为什么读不到电量？(3步向导)",
                "command": lambda: self.on_why_unread(r),
            })
            items.append({
                "type": "item",
                "label": "在调试面板探测 / 标定…",
                "command": lambda: self.on_open_debug(r),
            })
        if self.on_forget:
            items.append({"type": "separator"})
            items.append({
                "type": "item",
                "label": "不再显示此设备（遗忘并移除）",
                "danger": True,
                "command": lambda: self.on_forget(r),
            })
        menu = neu_ui.NeuPopupMenu(
            self,
            items,
            min_width_dp=188,
            topmost=True,
            right_align=right_align,
            on_close=lambda: setattr(self, "_card_menu", None) if self.winfo_exists() else None,
        )
        self._card_menu = menu
        menu.open_at(x_root, y_root)
        return menu

    def _on_device_card_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        if self._num_tween_id is not None:
            try:
                self.after_cancel(self._num_tween_id)
            except Exception:
                pass
            self._num_tween_id = None

    def set_pending_sync(self, pending: bool, new_name: Optional[str] = None) -> None:
        """就地更新待同步状态（例如刷新扫描进行中完成了改名）。"""
        self._pending_sync = bool(pending)
        if new_name is not None:
            self._name_raw = new_name
            try:
                avail_w = max(neu_ui.sx(100, self), self.content.winfo_width() - self._pill.winfo_reqwidth() - neu_ui.sx(28, self))
                disp = neu_ui.fit_ellipsis(new_name, avail_w, self._name_font_obj) if self.content.winfo_width() > 60 else new_name
            except Exception:
                disp = new_name
            self._lbl_name.configure(text=disp)
            self._name_tip.set_text(new_name if disp != new_name else "")
        if self._pending_sync:
            bg_c, fg_c, bd_c = neu_ui.get_status_pill_style("stale")
            self._pill.set_pill("待刷新同步", bg_color=bg_c, fg_color=fg_c, surface_bg=self._surface_bg, border_color=bd_c, icon_kind="")

    def set_stale_error(self, stale_err: bool, ts_str: str = "") -> None:
        """整轮刷新失败时标灰缓存卡片并展示旧值时间戳角标，防止把过期电量误读为当前值。"""
        self._error_stale_ts = ts_str if stale_err else ""
        if stale_err:
            target_bg = neu_ui.MUTED_CARD_BG
            self.set_muted(True, fill_hex=target_bg)
            self._apply_surface_bg(target_bg)
            bg_c, fg_c, bd_c = neu_ui.get_status_pill_style("stale")
            tag = f"数据为 {ts_str} 旧值" if ts_str else "历史缓存旧值"
            self._pill.set_pill(tag, bg_color=bg_c, fg_color=fg_c, surface_bg=target_bg, border_color=bd_c, icon_kind="")
            self._lbl_num.configure(fg=neu_ui.TEXT_DISABLED)
        else:
            self.update_reading(self.reading, self._history_data, is_hidden=self._is_hidden)

    def _animate_num_to(self, target_lvl: int, fg_hex: str, prefix: str = "") -> None:
        if self._num_tween_id is not None:
            try:
                self.after_cancel(self._num_tween_id)
            except Exception:
                pass
            self._num_tween_id = None
        start_lvl = self._num_level
        self._num_level = target_lvl
        self._num_prefix = prefix
        self._num_fg_target = fg_hex
        if start_lvl is None or start_lvl == target_lvl or not self.winfo_ismapped():
            self._lbl_num.configure(text=f"{prefix}{target_lvl}%", fg=fg_hex)
            return
        steps = 7

        def _tick(step: int) -> None:
            if not self.winfo_exists():
                self._num_tween_id = None
                return
            t = min(1.0, step / float(steps))
            ease = 1.0 - (1.0 - t) ** 3
            val = int(round(start_lvl + (target_lvl - start_lvl) * ease))
            try:
                self._lbl_num.configure(text=f"{prefix}{val}%", fg=fg_hex)
            except Exception:
                self._num_tween_id = None
                return
            if step < steps:
                self._num_tween_id = self.after(40, lambda: _tick(step + 1))
            else:
                self._num_tween_id = None

        _tick(1)

    def _apply_surface_bg(self, bg_hex: str) -> None:
        if bg_hex == self._surface_bg:
            return
        self._surface_bg = bg_hex
        for w in self._plain_bg_widgets:
            try:
                w.configure(bg=bg_hex)
            except Exception:
                pass
        for b in (self._btn_pin, self._btn_rename, self._btn_hide, self._btn_more, self._btn_probe, self._btn_why):
            if b is not None:
                try:
                    b.set_surface_bg(bg_hex)
                except Exception:
                    pass
        self._bar.set_surface_bg(bg_hex)
        if self.spark is not None:
            self.spark.set_surface_bg(bg_hex)

    def _fit_foot_text(self, width_px: int) -> str:
        raw = self._foot_raw
        if not raw:
            return ""
        cap_px = max(neu_ui.sx(180, self), int(width_px * 1.88))
        return neu_ui.fit_ellipsis(raw, cap_px, self._foot_font_obj)

    def _on_inner_resize(self, event) -> None:
        w = max(neu_ui.sx(180, self), int(getattr(event, "width", 0)) - neu_ui.sx(4, self))
        try:
            if not self._compact and int(self._lbl_foot.cget("wraplength")) != w:
                self._lbl_foot.configure(wraplength=w, text=self._fit_foot_text(w))
            name_avail = max(neu_ui.sx(90, self), w - self._pill.winfo_reqwidth() - neu_ui.sx(24, self))
            disp_name = neu_ui.fit_ellipsis(self._name_raw, name_avail, self._name_font_obj)
            if self._lbl_name.cget("text") != disp_name:
                self._lbl_name.configure(text=disp_name)
                self._name_tip.set_text(self._name_raw if disp_name != self._name_raw else "")
        except Exception:
            pass

    def needs_rebuild(
        self,
        reading: devices.PeripheralReading,
        is_hidden: bool,
        compact: Optional[bool] = None,
    ) -> bool:
        has_probe = bool(
            ((not reading.offline) and reading.level is None and reading.transport == "HID")
            or (reading.manual and reading.level is None)
        )
        comp_chg = (compact is not None and bool(compact) != self._compact)
        return has_probe != self._has_probe or bool(is_hidden) != self._is_hidden or comp_chg

    def update_reading(
        self,
        reading: devices.PeripheralReading,
        history_data: dict[str, Any],
        *,
        is_hidden: bool = False,
        spark_hours: Optional[int] = None,
        low_threshold: Optional[int] = None,
        custom_threshold: Optional[int] = None,
        global_threshold: Optional[int] = None,
    ) -> None:
        self.reading = reading
        self._history_data = history_data
        self._is_hidden = is_hidden
        self._pending_sync = False
        self._error_stale_ts = ""
        if low_threshold is not None:
            self._low_threshold = int(low_threshold)
        self._custom_threshold = custom_threshold
        if global_threshold is not None:
            self._global_threshold = int(global_threshold)

        offline = reading.offline or (reading.level is None)
        target_bg = neu_ui.MUTED_CARD_BG if reading.offline else neu_ui.BASE
        self.set_muted(reading.offline, fill_hex=target_bg)
        self._apply_surface_bg(target_bg)

        bar_col = devices.battery_color_hex(
            reading.level,
            low_threshold=self._low_threshold,
            asleep=reading.asleep,
            stale=reading.stale,
            charging=reading.charging,
            offline=reading.offline,
            full=reading.full,
        ).lower()
        is_low = (
            (not offline)
            and reading.level is not None
            and (not reading.charging)
            and (not reading.full)
            and reading.level < self._low_threshold
        )

        if offline:
            icon_col = neu_ui.TEXT_DISABLED
        elif reading.charging:
            icon_col = neu_ui.AMBER_CHARGE
        elif is_low:
            icon_col = neu_ui.RED_DANGER
        else:
            icon_col = neu_ui.TEXT_MUTED

        self._badge_icon.set_style(reading.role, icon_col, surface_bg=target_bg)

        role_txt = devices.ROLE_LABEL.get(reading.role, "外设")
        hdr_fg = neu_ui.TEXT_DISABLED if offline else neu_ui.TEXT_MUTED
        sep_fg = neu_ui.TEXT_DISABLED if offline else neu_ui.TEXT_DIM
        self._lbl_role.configure(text=role_txt, fg=hdr_fg)
        self._lbl_role_sep.configure(fg=sep_fg)
        self._lbl_vidpid.configure(text=reading.vidpid_text, fg=sep_fg)

        conn_tag = reading.connection or ("蓝牙" if reading.transport == "BT" else "无线")
        sub_tag = f"{conn_tag}{neu_ui.TEXT_SEP}已隐藏" if is_hidden else conn_tag
        self._lbl_conn.configure(
            text=sub_tag,
            fg=neu_ui.BLURPLE_LINK if is_hidden else (neu_ui.TEXT_DISABLED if offline else neu_ui.TEXT_DIM),
        )
        try:
            pin_lbl = "★" if self._compact else ("★已置顶" if reading.pinned else "置顶")
            if self._compact and not reading.pinned:
                pin_lbl = "置顶"
            self._btn_pin.configure(
                text=pin_lbl,
                fg=neu_ui.BLURPLE_LINK if reading.pinned else neu_ui.TEXT_MUTED,
            )
        except Exception:
            pass

        icon_kind = ""
        if is_hidden:
            p_state = "hidden"
            pill_txt = "已隐藏"
        elif reading.offline:
            p_state = "offline"
            pill_txt = "已离线"
        elif reading.stale:
            p_state = "stale"
            pill_txt = "2.4G 在线" if "2.4g" in (reading.connection or "").lower() else "数据陈旧"
        else:
            p_state = reading.power_state
            if p_state == "charging":
                pill_txt = "充电中"
                icon_kind = "charge"
            elif p_state == "full":
                pill_txt = "已充满"
                icon_kind = "check"
            elif p_state == "wired_power":
                pill_txt = "有线供电"
            elif reading.level is None:
                pill_txt = "未读取"
            else:
                pill_txt = "在线"

        pill_bg, pill_fg, pill_border = neu_ui.get_status_pill_style(p_state)
        self._pill.set_pill(
            pill_txt,
            bg_color=pill_bg,
            fg_color=pill_fg,
            surface_bg=target_bg,
            border_color=pill_border,
            icon_kind=icon_kind,
        )
        rel_age, age_old = _format_relative_age(reading.updated_at)
        pill_tips: list[str] = []
        if reading.updated_at:
            upd_str = time.strftime("%m-%d %H:%M:%S", time.localtime(float(reading.updated_at)))
            pill_tips.append(f"最后同步时间：{upd_str}（{rel_age}）")
        if reading.last_read_error:
            pill_tips.append(f"最近读取失败原因：{reading.last_read_error}")
        self._pill_tip.set_text("\n".join(pill_tips))

        self._name_raw = reading.name
        try:
            avail_w = max(neu_ui.sx(100, self), self.content.winfo_width() - self._pill.winfo_reqwidth() - neu_ui.sx(28, self))
            disp_name = neu_ui.fit_ellipsis(self._name_raw, avail_w, self._name_font_obj) if self.content.winfo_width() > 60 else self._name_raw
        except Exception:
            disp_name = self._name_raw
        self._lbl_name.configure(text=disp_name, fg=neu_ui.TEXT_DISABLED if offline else neu_ui.TEXT)
        self._name_tip.set_text(self._name_raw if disp_name != self._name_raw else "")

        num_fg = neu_ui.battery_accent_color(
            reading.level,
            threshold=self._low_threshold,
            charging=reading.charging,
            offline=offline,
            full=reading.full,
            asleep=reading.asleep,
            stale=reading.stale,
        )
        if reading.level is not None and not reading.offline:
            self._animate_num_to(int(reading.level), num_fg, prefix=("~" if reading.interpolated else ""))
            if reading.interpolated and reading.raw_level is not None:
                self._num_tip.set_text(
                    f"蓝牙 10% 步进平滑推算：硬件当前上报 {reading.raw_level}%，"
                    f"按活跃在线时长推算为 ~{reading.level}%（硬件掉档时自动校准）"
                )
            else:
                self._num_tip.set_text("")
        else:
            if self._num_tween_id is not None:
                try:
                    self.after_cancel(self._num_tween_id)
                except Exception:
                    pass
                self._num_tween_id = None
            self._num_level = None
            self._num_prefix = ""
            self._lbl_num.configure(text="--", fg=num_fg)
            self._num_tip.set_text("")

        stat_fg = (
            neu_ui.TEXT_DISABLED
            if offline
            else (neu_ui.AMBER_CHARGE if reading.charging else (neu_ui.RED_DANGER if is_low else neu_ui.TEXT_MUTED))
        )
        stat_str = reading.status_text
        if reading.voltage_text:
            stat_str = f"{stat_str}{neu_ui.TEXT_SEP}{reading.voltage_text}"
        self._lbl_stat.configure(
            text=stat_str,
            fg=stat_fg,
            font=neu_ui.ui_font(
                neu_ui.FONT_CAPTION,
                "bold" if (reading.charging or is_low or reading.offline) else "normal",
                root=self,
            ),
        )

        if offline:
            est_disp = "离线无预估"
            has_est = False
        elif reading.full:
            est_disp = "已充满"
            has_est = False
        elif reading.charging:
            est_disp = reading.estimate_text or "正在充电中 · 估算中"
            has_est = reading.charge_eta_minutes is not None
        elif reading.estimate_hours is not None:
            est_disp = reading.estimate_text
            has_est = True
        else:
            est_disp = "数据不足"
            has_est = False
        est_fg = neu_ui.TEXT_DISABLED if offline else (neu_ui.INFO if has_est else neu_ui.TEXT_DIM)
        self._lbl_est.configure(
            text=est_disp,
            fg=est_fg,
            font=neu_ui.ui_font(
                neu_ui.FONT_CAPTION,
                "bold" if has_est else "normal",
                root=self,
            ),
        )

        pct = 0 if (reading.offline or reading.level is None) else max(0, min(100, reading.level))
        self._bar.set_value(pct, neu_ui.GRAY_DISABLED if offline else bar_col)

        if self.spark is not None:
            if spark_hours is not None and spark_hours != self.spark.window_hours:
                self.spark.set_hours(spark_hours)
            series = battery_history.get_series(history_data, reading.key, window_hours=self.spark.window_hours)
            self.spark.update_series(series, bar_col, offline=offline)

        # 分级友好脚注 + 电压 + 失败原因提示
        raw_diag = f"{reading.detail or ''} {reading.error or ''}".strip()
        if reading.manual and reading.level is None and not reading.offline:
            foot_base = "未匹配协议，可点击下方去标定或三步诊断"
        elif reading.offline or reading.level is None:
            _kind, friendly_issue = devices.classify_device_issue(reading.error or "", reading.detail or "")
            foot_base = friendly_issue
        else:
            volt_part = f" · 电压 {reading.voltage_text}" if reading.voltage_text else ""
            parts_list = list(getattr(reading, "parts", None) or [])
            if len(parts_list) > 1:
                parts_txt = " / ".join(f"{p.get('label', '')} {p.get('level', '--')}%" for p in parts_list)
                foot_base = f"分部电量：{parts_txt}{volt_part}"
            elif reading.stale and "2.4g" in (reading.connection or "").lower():
                foot_base = f"2.4G 接收器在线 · 沿用上次蓝牙 {reading.level}%（切蓝牙刷新）"
            elif reading.interpolated and reading.raw_level is not None:
                foot_base = f"平滑推算 ~{reading.level}% · 硬件上报 {reading.raw_level}%（掉档自动校准）"
            else:
                foot_base = (
                    f"无线连接正常 · 电量 {reading.level}%{volt_part}"
                    if not reading.charging
                    else f"正在充电 · 当前 {reading.level}%{volt_part}"
                )
        foot_raw = f"{foot_base}{neu_ui.TEXT_SEP}同步于 {rel_age}" if rel_age else foot_base
        self._foot_raw = foot_raw
        if not self._compact:
            try:
                cur_w = int(self._lbl_foot.cget("wraplength"))
            except Exception:
                cur_w = neu_ui.sx(300, self)
            disp_foot = self._fit_foot_text(cur_w)
            foot_fg = neu_ui.TEXT_DISABLED if (offline or age_old) else neu_ui.TEXT_DIM
            self._lbl_foot.configure(text=disp_foot, fg=foot_fg)
        else:
            disp_foot = foot_raw
        tip_parts = []
        if disp_foot != foot_raw:
            tip_parts.append(foot_raw)
        if reading.last_read_error:
            tip_parts.append(f"最近读取失败原因：{reading.last_read_error}")
        if raw_diag and raw_diag != reading.last_read_error:
            tip_parts.append(f"诊断详情：{raw_diag}")
        self._foot_tip.set_text("\n".join(tip_parts))

    def _handle_spark_window(self, hours: int) -> None:
        if self._on_spark_hours_change:
            self._on_spark_hours_change(self.reading.key, hours)
        if self.spark is None:
            return
        series = battery_history.get_series(self._history_data, self.reading.key, window_hours=hours)
        bar_col = devices.battery_color_hex(
            self.reading.level,
            low_threshold=self._low_threshold,
            asleep=self.reading.asleep,
            stale=self.reading.stale,
            charging=self.reading.charging,
            offline=self.reading.offline,
            full=self.reading.full,
        ).lower()
        self.spark.update_series(
            series, bar_col, offline=(self.reading.offline or self.reading.level is None)
        )


class BatteryPanelApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        start_minimized: bool = False,
        disable_tray_host: bool = False,
        auto_scan: bool = True,
    ) -> None:
        self.root = root
        self.cfg = tray_common.load_config()
        neu_ui.apply_theme(str(self.cfg.get("theme_mode", "light")))
        neu_ui.set_font_scale_mode(str(self.cfg.get("font_scale_mode", "standard")))
        _sync_palette_globals()

        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.tray_events: queue.SimpleQueue[tuple[str, Any]] = queue.SimpleQueue()
        self.scanning = False
        self._scan_token = 0
        self._scan_started_at = 0.0
        self._scan_progress_aid: Optional[str] = None
        self.show_hidden = bool(self.cfg.get("show_hidden", False))
        self.readings: list[devices.PeripheralReading] = []
        self.history_data: dict[str, Any] = battery_history.load_history()
        self._spark_windows: dict[str, int] = {}
        self._card_widgets: list[DeviceCard] = []
        self._grid_cols = 2
        self._closing = False
        self._after_ids: list[str] = []
        self._refresh_timer_id: Optional[str] = None
        self._note_clear_aid: Optional[str] = None
        self._settings_modal: Optional[neu_ui.NeuModalOverlay] = None
        self._debug_modal: Optional[neu_ui.NeuModalOverlay] = None
        self._log_modal: Optional[neu_ui.NeuModalOverlay] = None
        self._wizard_modal: Optional[neu_ui.NeuModalOverlay] = None
        self._wheel_accum: float = 0.0
        self._pending_focus_key: Optional[str] = None
        self._last_device_key: Optional[str] = None
        self._last_sync_ts_str: str = ""
        self._last_sync_epoch: Optional[float] = None
        self._last_scan_stats: Optional[devices.ScanStats] = None
        self._retry_attempts: dict[str, int] = {}
        self._retry_timer_ids: list[str] = []
        self._retry_cycle_id: int = 0
        self._rebuild_pending = False
        self._disable_tray_host = disable_tray_host
        self._low_monitor = tray_service.LowBatteryMonitor()
        self._search_query = ""
        self._filter_mode = "all"
        self._filter_btns: dict[str, neu_ui.NeuButton] = {}
        self._group_headers: list[tk.Widget] = []
        self._alert_report_modal: Optional[neu_ui.NeuModalOverlay] = None
        self._heartbeat_aid: Optional[str] = None
        if not disable_tray_host:
            tray_common.clear_quit_flag()

        if not disable_tray_host:
            views = int(self.cfg.get("footer_help_views", 0))
            if views < 4:
                self.cfg["footer_help_views"] = views + 1
                tray_common.save_config(self.cfg)
        self._collapse_footer_help = int(self.cfg.get("footer_help_views", 0)) > 3

        self.root.title(tray_common.UI_TITLE)
        self.root.configure(bg=neu_ui.BASE)
        if not disable_tray_host:
            self.root.withdraw()
        self._restore_or_fit_geometry()
        ico = tray_common.app_icon_path()
        if ico:
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass

        # 统一无边框新拟物顶部标题栏 + Win11 原生圆角外框 + 四边八向拖拽缩放
        neu_ui.enable_frameless_taskbar(self.root, round_corners=True)
        neu_ui.set_command_error_handler(self._on_command_error)
        self.root.protocol("WM_DELETE_WINDOW", self.on_request_close)
        self.root.bind("<Destroy>", self._on_destroy, add="+")
        self.root.bind("<Configure>", self._on_root_configure, add="+")

        self._build_ui()
        if self.cfg.get("window_maximized") and not disable_tray_host:
            self.root.after(25, lambda: self.titlebar._handle_toggle_max() if not self._closing and not self.titlebar.is_maximized() else None)
        neu_ui.apply_window_rounded_corners(self.root, rounded=not self.titlebar.is_maximized())
        neu_ui.enable_frameless_resize(
            self.root,
            min_w=neu_ui.sx(740, self.root),
            min_h=neu_ui.sx(560, self.root),
            border_px=5,
            is_maximized=self.titlebar.is_maximized,
        )
        self._render_skeletons()

        # 常驻托盘（手柄图标 + 悬停四宫格 + 右键新拟物菜单 + 右下角低电量弹窗）
        self._tray_menu: Optional[neu_ui.NeuPopupMenu] = None
        self.tray_host: Optional[tray_service.TrayHostThread] = None
        if not disable_tray_host:
            self.tray_host = tray_service.TrayHostThread(self.tray_events, on_wake=self.request_pump)
            self.tray_host.start()

        self.hover_tooltip = tray_service.TrayHoverTooltip(
            self.root,
            get_icon_rect=(self.tray_host.get_icon_rect if self.tray_host else (lambda: (1600, 1000, 1632, 1040))),
            on_open_main=self.show_main_window,
            on_open_device=self.focus_device,
        )
        self._history_modal = None
        self._about_modal = None
        self._pump_aid: Optional[str] = None
        self._pump_idle_ticks: int = 0
        self._ui_thread_id: int = threading.get_ident()
        self._warn_banner_last_key: str = ""
        self._warn_banner_last_ts: float = 0.0
        self._restore_exc_hooks = install_global_exception_hooks(self)
        self.low_toast = tray_service.LowBatteryToastPopup(
            self.root,
            on_open_main=self.show_main_window,
            on_open_device=self.focus_device,
            on_snooze=self._snooze_device_by_key,
        )
        if not disable_tray_host:
            self._schedule_heartbeat()

        if start_minimized:
            self.root.withdraw()
        elif not disable_tray_host:
            try:
                self.root.update_idletasks()
                self.root.attributes("-alpha", 0.0)
                self.root.deiconify()
                neu_ui.enable_frameless_taskbar(self.root, round_corners=True)
                neu_ui.fade_in_window(self.root, steps=7, step_ms=14)
            except Exception:
                self.root.deiconify()

        self._sync_footer()
        try:
            self.root.bind("<<PBVWakePump>>", lambda _e: self._schedule_pump(10), add="+")
        except Exception:
            pass
        self._schedule_pump(70)
        if auto_scan:
            self._after_ids.append(self.root.after(150, self.refresh_now))
            self._schedule_auto_refresh()

    def _restore_or_fit_geometry(self) -> None:
        """恢复上次窗口几何尺寸与位置（若校验在当前桌面工作区内），否则安全居中。"""
        geom = self.cfg.get("window_geometry")
        if isinstance(geom, dict):
            try:
                w = int(geom.get("w", 0))
                h = int(geom.get("h", 0))
                x = int(geom.get("x", 0))
                y = int(geom.get("y", 0))
                if w >= 600 and h >= 440:
                    try:
                        u32 = ctypes.WinDLL("user32", use_last_error=True)
                        vx = int(u32.GetSystemMetrics(76))
                        vy = int(u32.GetSystemMetrics(77))
                        vw = int(u32.GetSystemMetrics(78)) or 1920
                        vh = int(u32.GetSystemMetrics(79)) or 1080
                    except Exception:
                        vx, vy, wr, wb = neu_ui.get_work_area()
                        vw, vh = wr - vx, wb - vy
                    inter_w = min(x + w, vx + vw) - max(x, vx)
                    inter_h = min(y + h, vy + vh) - max(y, vy)
                    if inter_w >= 160 and inter_h >= 80:
                        self.root.geometry(f"{w}x{h}+{x}+{y}")
                        self.root.minsize(neu_ui.sx(740, self.root), neu_ui.sx(560, self.root))
                        return
            except Exception:
                pass
        neu_ui.fit_geometry(self.root, 980, 830, 740, 560)

    def _save_window_geometry(self) -> None:
        if self._closing or not self.root.winfo_exists():
            return
        try:
            is_max = bool(self.titlebar.is_maximized())
            self.cfg["window_maximized"] = is_max
            if not is_max and str(self.root.state()) == "normal":
                w = int(self.root.winfo_width())
                h = int(self.root.winfo_height())
                if w >= 600 and h >= 440:
                    self.cfg["window_geometry"] = {
                        "w": w,
                        "h": h,
                        "x": int(self.root.winfo_x()),
                        "y": int(self.root.winfo_y()),
                    }
            tray_common.save_config(self.cfg)
        except Exception:
            pass

    def _rebuild_ui(self, *, reopen_settings: bool = False) -> None:
        """DPI 切换 / 主题切换 / 字号调整后整树重建 UI，保留滚动位置与浮层连续性。"""
        if self._closing or not self.root.winfo_exists():
            return
        self._rebuild_pending = True
        try:
            try:
                scroll_top = float(self.canvas.yview()[0])
            except Exception:
                scroll_top = 0.0
            summary_txt = self.lbl_summary.cget("text") if hasattr(self, "lbl_summary") else ""

            self._suppress_settings_return = True
            try:
                if self._history_modal is not None:
                    m, self._history_modal = self._history_modal, None
                    m.close()
                if self._about_modal is not None:
                    m, self._about_modal = self._about_modal, None
                    m.close()
                if getattr(self, "_alert_report_modal", None) is not None:
                    m, self._alert_report_modal = self._alert_report_modal, None
                    m.close()
                if self._log_modal is not None:
                    m, self._log_modal = self._log_modal, None
                    m.close()
                if self._wizard_modal is not None:
                    m, self._wizard_modal = self._wizard_modal, None
                    m.close()
                if self._debug_modal is not None:
                    m, self._debug_modal = self._debug_modal, None
                    m.close()
                if self._settings_modal is not None:
                    m, self._settings_modal = self._settings_modal, None
                    m.close()
            finally:
                self._suppress_settings_return = False

            for ch in list(self.root.winfo_children()):
                try:
                    ch.destroy()
                except Exception:
                    pass
            self._card_widgets.clear()
            self._group_headers.clear()
            neu_ui.clear_render_caches()
            _sync_palette_globals()
            self.root.configure(bg=neu_ui.BASE)
            self._build_ui()
            self.root.minsize(neu_ui.sx(740, self.root), neu_ui.sx(560, self.root))
            neu_ui.apply_window_rounded_corners(self.root, rounded=not self.titlebar.is_maximized())
            neu_ui.enable_frameless_resize(
                self.root,
                min_w=neu_ui.sx(740, self.root),
                min_h=neu_ui.sx(560, self.root),
                border_px=5,
                is_maximized=self.titlebar.is_maximized,
            )
            if self.readings:
                self._render_cards(self.readings)
            else:
                self._render_skeletons()
                if summary_txt:
                    self.lbl_summary.configure(text=summary_txt)
            self._sync_footer()
            self.hover_tooltip.update_data(
                self.readings,
                threshold=int(self.cfg.get("notify_threshold", 20)),
                device_thresholds=self.cfg.get("device_thresholds") or {},
            )
            if scroll_top > 0.0:
                self.root.after_idle(
                    lambda y=scroll_top: self.canvas.yview_moveto(y) if (not self._closing and self.canvas.winfo_exists()) else None
                )
            if reopen_settings:
                self.root.after_idle(self.open_settings_modal)
        finally:
            self._rebuild_pending = False

    def _build_ui(self) -> None:
        # ---- 顶部自定义新拟物窗口标题栏 ----
        self.titlebar = neu_ui.NeuTitleBar(
            self.root,
            title=tray_common.UI_TITLE,
            subtitle="2.4G / 蓝牙外设实时监测",
            on_minimize=self._minimize_window,
            on_close=self.on_request_close,
            allow_maximize=True,
            icon_role="controller",
        )
        self.titlebar.pack(fill="x")

        # ---- 页面主标题与操作区（OUTER_CONTENT_INSET 与卡片左缘严格对齐） ----
        header_outer = tk.Frame(self.root, bg=neu_ui.BASE, pady=neu_ui.sx(12, self.root))
        header_outer.pack(fill="x")
        self._header_outer = header_outer
        header_outer.columnconfigure(0, weight=1)
        header_outer.columnconfigure(1, weight=0)
        header_outer.columnconfigure(2, weight=1)

        header = tk.Frame(header_outer, bg=neu_ui.BASE)
        header.grid(row=0, column=1, sticky="nsew")
        self._header_inner = header

        inset_px = neu_ui.sx(OUTER_CONTENT_INSET, self.root)
        right_pad = max(neu_ui.sx(2, self.root), inset_px - neu_ui.sx(10, self.root))
        header_top = tk.Frame(header, bg=neu_ui.BASE)
        header_top.pack(fill="x", padx=(inset_px, right_pad))

        tk.Frame(header, bg=neu_ui.SEP_DARK, height=1).pack(
            fill="x", side="bottom", padx=inset_px, pady=(neu_ui.sx(8, self.root), 0)
        )

        actions = tk.Frame(header_top, bg=neu_ui.BASE)
        actions.pack(side="right")

        self.btn_settings = neu_ui.NeuIconButton(
            actions,
            glyph="gear",
            command=self.open_settings_modal,
            text="",
            radius=16,
            pad=10,
            padx=10,
            pady=7,
            tooltip="设置与偏好",
            accessible_name="打开设置",
        )
        self.btn_settings.pack(side="right")

        self.btn_refresh = neu_ui.NeuButton(
            actions,
            text="立即刷新",
            command=self.refresh_now,
            accent=True,
            radius=16,
            pad=10,
            font_size=neu_ui.FONT_BODY,
            weight="bold",
            padx=18,
            pady=7,
            tooltip="立即刷新全部外设电量",
        )
        self.btn_refresh.pack(side="right", padx=(0, neu_ui.sx(4, self.root)))

        compact_on = bool(self.cfg.get("compact_view", False))
        self._btn_compact = neu_ui.NeuButton(
            actions,
            text="标准卡片" if compact_on else "紧凑视图",
            command=self._toggle_compact_view,
            radius=14,
            pad=8,
            font_size=neu_ui.FONT_CAPTION,
            padx=11,
            pady=7,
            fg=neu_ui.BLURPLE_LINK if compact_on else neu_ui.TEXT_MUTED,
            tooltip="切换大卡 / 单行紧凑视图（适合多设备总览）",
        )
        self._btn_compact.pack(side="right", padx=(0, neu_ui.sx(4, self.root)))

        cur_sort = str(self.cfg.get("sort_mode", "default"))
        cur_sort_lbl = next((lbl for lbl, val in SORT_MODE_CHOICES if val == cur_sort), "默认顺序")
        self._var_toolbar_sort = tk.StringVar(value=cur_sort_lbl)
        self._sel_toolbar_sort = neu_ui.NeuSelect(
            actions,
            variable=self._var_toolbar_sort,
            values=[lbl for lbl, _ in SORT_MODE_CHOICES],
            on_change=self._on_toolbar_sort_change,
            width_px=neu_ui.sx(98, self.root),
        )
        self._sel_toolbar_sort.pack(side="right", padx=(0, neu_ui.sx(4, self.root)))

        self._btn_cancel_scan = neu_ui.NeuButton(
            actions,
            text="取消",
            command=self._cancel_scan,
            radius=14,
            pad=8,
            font_size=neu_ui.FONT_CAPTION,
            padx=10,
            pady=5,
            fg=neu_ui.RED_DANGER,
            tooltip="停止等待当前漫长硬件枚举",
        )

        title_box = tk.Frame(header_top, bg=neu_ui.BASE)
        title_box.pack(side="left", fill="x", expand=True, padx=(0, neu_ui.sx(12, self.root)))
        tk.Label(
            title_box,
            text="外设电量总览",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT,
            font=neu_ui.ui_font(neu_ui.FONT_H1, "bold", root=self.root),
            anchor="w",
        ).pack(fill="x")

        summary_row = tk.Frame(title_box, bg=neu_ui.BASE)
        summary_row.pack(fill="x", pady=(neu_ui.sx(4, self.root), 0))
        self._summary_left = tk.Frame(summary_row, bg=neu_ui.BASE)
        self._summary_left.pack(side="left", fill="x", expand=True)

        self.lbl_summary = tk.Label(
            self._summary_left,
            text=ACTION_SCANNING,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_BODY, root=self.root),
            anchor="w",
            justify="left",
            wraplength=neu_ui.sx(440, self.root),
        )
        self.lbl_summary.pack(side="left")

        self._link_font_norm = neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=self.root)
        self._link_font_hover = (*self._link_font_norm[:2], "bold underline")
        self._lbl_hidden_hint = tk.Label(
            self._summary_left,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.BLURPLE_LINK,
            font=self._link_font_norm,
            cursor="hand2",
        )
        self._lbl_hidden_hint.bind("<Button-1>", lambda _e: self._reveal_hidden_from_hint())
        self._lbl_hidden_hint.bind("<Enter>", lambda _e: self._lbl_hidden_hint.configure(font=self._link_font_hover, fg=neu_ui.BLURPLE_BRAND))
        self._lbl_hidden_hint.bind("<Leave>", lambda _e: self._lbl_hidden_hint.configure(font=self._link_font_norm, fg=neu_ui.BLURPLE_LINK))
        neu_ui.attach_tooltip(self._lbl_hidden_hint, "点击立即显示已隐藏的设备卡片")

        self._lbl_skipped_hint = tk.Label(
            self._summary_left,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.BLURPLE_LINK,
            font=self._link_font_norm,
            cursor="hand2",
        )
        self._lbl_skipped_hint.bind("<Button-1>", lambda _e: self._open_skipped_devices_action())
        self._lbl_skipped_hint.bind("<Enter>", lambda _e: self._lbl_skipped_hint.configure(font=self._link_font_hover, fg=neu_ui.BLURPLE_BRAND))
        self._lbl_skipped_hint.bind("<Leave>", lambda _e: self._lbl_skipped_hint.configure(font=self._link_font_norm, fg=neu_ui.BLURPLE_LINK))
        neu_ui.attach_tooltip(self._lbl_skipped_hint, "系统发现这些 HID 设备暂未命中电量协议，点击可预填并前往标定")

        # 固定宽度占位槽防止错误发生/清除时左侧摘要文字横向抖动
        self._err_btn_slot = tk.Frame(
            summary_row, bg=neu_ui.BASE, width=neu_ui.sx(132, self.root), height=neu_ui.sx(28, self.root)
        )
        self._err_btn_slot.pack_propagate(False)
        self._err_btn_slot.pack(side="right", padx=(neu_ui.sx(8, self.root), 0))

        self._btn_retry = neu_ui.NeuButton(
            self._err_btn_slot,
            text="重试",
            command=self.refresh_now,
            accent=True,
            radius=12,
            pad=4,
            font_size=neu_ui.FONT_CAPTION,
            padx=10,
            pady=2,
            tooltip="重新刷新所有外设",
        )
        self._btn_view_log = neu_ui.NeuButton(
            self._err_btn_slot,
            text="查看日志",
            command=self.open_error_log_modal,
            radius=12,
            pad=4,
            font_size=neu_ui.FONT_CAPTION,
            padx=9,
            pady=2,
            fg=neu_ui.TEXT_MUTED,
            tooltip="查看本地异常日志详情",
        )

        # ---- 快捷搜索与状态筛选栏（#4） ----
        filter_outer = tk.Frame(self.root, bg=neu_ui.BASE, pady=neu_ui.sx(4, self.root))
        filter_outer.pack(fill="x")
        self._filter_outer = filter_outer
        filter_outer.columnconfigure(0, weight=1)
        filter_outer.columnconfigure(1, weight=0)
        filter_outer.columnconfigure(2, weight=1)

        filter_bar = tk.Frame(filter_outer, bg=neu_ui.BASE, padx=inset_px)
        filter_bar.grid(row=0, column=1, sticky="nsew")
        self._filter_inner = filter_bar

        search_wrap = tk.Frame(filter_bar, bg=neu_ui.BASE)
        search_wrap.pack(side="left", padx=(0, neu_ui.sx(8, self.root)))
        if not hasattr(self, "_var_search"):
            self._var_search = tk.StringVar(value=self._search_query)
            self._var_search.trace_add("write", lambda *_a: self._on_search_changed())
        else:
            self._var_search.set(self._search_query)
        self._search_entry = neu_ui.NeuEntry(
            search_wrap,
            textvariable=self._var_search,
            width=18,
            font_tuple=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self.root),
        )
        self._search_entry.pack(side="left")
        neu_ui.attach_tooltip(self._search_entry, "按名称、型号、VID:PID 或通道搜索")
        self._btn_clear_search = neu_ui.NeuButton(
            search_wrap,
            text="×",
            command=self._clear_search,
            radius=10,
            pad=3,
            padx=6,
            pady=2,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.TEXT_MUTED,
            tooltip="清空搜索与筛选",
        )
        self._btn_clear_search.pack(side="left", padx=(neu_ui.sx(4, self.root), 0))

        self._filter_btns = {}
        pills_box = tk.Frame(filter_bar, bg=neu_ui.BASE)
        pills_box.pack(side="left", fill="x", expand=True)
        for f_lbl, f_key in FILTER_MODE_CHOICES:
            active = (self._filter_mode == f_key)
            fb = neu_ui.NeuButton(
                pills_box,
                text=f_lbl,
                command=lambda fk=f_key: self._set_filter_mode(fk),
                radius=12,
                pad=4,
                padx=9,
                pady=3,
                font_size=neu_ui.FONT_CAPTION,
                fg=neu_ui.BLURPLE_LINK if active else neu_ui.TEXT_MUTED,
                tooltip=f"仅显示「{f_lbl}」状态设备",
            )
            fb.pack(side="left", padx=(0, neu_ui.sx(4, self.root)))
            self._filter_btns[f_key] = fb

        group_on = bool(self.cfg.get("group_by_role", False))
        self._btn_group = neu_ui.NeuButton(
            filter_bar,
            text="分组：开" if group_on else "按类型分组",
            command=self._toggle_group_by_role,
            radius=12,
            pad=4,
            padx=10,
            pady=3,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.BLURPLE_LINK if group_on else neu_ui.TEXT_MUTED,
            tooltip="按设备类型（鼠标/键盘/耳机/手柄）分组展示并支持折叠",
        )
        self._btn_group.pack(side="right")

        # ---- 非阻塞运行告警 / I/O 恢复横幅（默认隐藏，遇到异常或备份恢复时展示） ----
        self._warn_banner_wrap = tk.Frame(self.root, bg=neu_ui.BASE, padx=inset_px)
        self._warn_banner = neu_ui.NeuCard(
            self._warn_banner_wrap, radius=14, pad=4, inner_pad=(12, 8), off=4, box_r=2, inset=True
        )
        self._warn_banner.pack(fill="x", pady=(0, neu_ui.sx(8, self.root)))
        self._warn_banner_lbl = tk.Label(
            self._warn_banner.content,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.RED_DANGER,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=self.root),
            anchor="w",
            justify="left",
            wraplength=neu_ui.sx(620, self.root),
        )
        self._warn_banner_lbl.pack(side="left", fill="x", expand=True)
        neu_ui.NeuButton(
            self._warn_banner.content,
            text="查看日志",
            command=self.open_error_log_modal,
            radius=10,
            pad=3,
            padx=8,
            pady=3,
            font_size=neu_ui.FONT_CAPTION,
        ).pack(side="right", padx=(neu_ui.sx(6, self.root), 0))
        neu_ui.NeuButton(
            self._warn_banner.content,
            text="忽略",
            command=self._dismiss_warning_banner,
            radius=10,
            pad=3,
            padx=8,
            pady=3,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.TEXT_MUTED,
        ).pack(side="right", padx=(neu_ui.sx(6, self.root), 0))
        self._warn_banner_visible = False

        # ---- 可滚动卡片区域（宽屏居中并把滚动条紧贴内容列右侧，消灭孤立滚动条） ----
        wrap_outer = tk.Frame(self.root, bg=neu_ui.BASE, padx=neu_ui.sx(12, self.root))
        wrap_outer.pack(fill="both", expand=True)
        self._wrap_outer = wrap_outer
        wrap_outer.columnconfigure(0, weight=1)
        wrap_outer.columnconfigure(1, weight=0)
        wrap_outer.columnconfigure(2, weight=1)
        wrap_outer.rowconfigure(0, weight=1)

        content_wrap = tk.Frame(wrap_outer, bg=neu_ui.BASE)
        content_wrap.grid(row=0, column=1, sticky="nsew")
        self._content_wrap = content_wrap

        self.canvas = tk.Canvas(content_wrap, bg=neu_ui.BASE, bd=0, highlightthickness=0, takefocus=0)
        sb = neu_ui.NeuScrollbar(content_wrap, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(neu_ui.sx(4, self.root), 0))
        self.canvas.pack(side="left", fill="both", expand=True)

        self.cards_frame = tk.Frame(self.canvas, bg=neu_ui.BASE)
        self.canvas_win = self.canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")
        self.cards_frame.bind("<Configure>", self._on_cards_configure)
        wrap_outer.bind("<Configure>", self._on_wrap_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # ---- 底部状态信息条（首启 3 次后将托盘引导折叠为 ? 徽章） ----
        footer_outer = tk.Frame(self.root, bg=neu_ui.BASE, pady=neu_ui.sx(12, self.root))
        footer_outer.pack(fill="x", side="bottom")
        self._footer_outer = footer_outer
        footer_outer.columnconfigure(0, weight=1)
        footer_outer.columnconfigure(1, weight=0)
        footer_outer.columnconfigure(2, weight=1)

        footer = tk.Frame(footer_outer, bg=neu_ui.BASE, padx=inset_px)
        footer.grid(row=0, column=1, sticky="nsew")
        self._footer_inner = footer
        tk.Frame(footer, bg=neu_ui.SEP_DARK, height=1).pack(fill="x", pady=(0, neu_ui.sx(8, self.root)))

        if self._collapse_footer_help:
            help_row = tk.Frame(footer, bg=neu_ui.BASE)
            help_row.pack(side="left")
            self.lbl_footer_left = tk.Label(
                help_row,
                text="常驻托盘  (?)",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self.root),
                cursor="question_arrow",
                anchor="w",
            )
            self.lbl_footer_left.pack(side="left")
            neu_ui.attach_tooltip(
                self.lbl_footer_left,
                "手柄图标常驻右下角托盘：悬停可弹出四宫格分页速览，左键单击打开主窗，双击跳转最近设备。",
            )
        else:
            self.lbl_footer_left = tk.Label(
                footer,
                text="手柄图标常驻托盘 · 悬停图标可弹出速览（左键打开）",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_DIM,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self.root),
                anchor="w",
            )
            self.lbl_footer_left.pack(side="left")

        self.lbl_footer_right = tk.Label(
            footer,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self.root),
            anchor="e",
        )
        self.lbl_footer_right.pack(side="right")

    def _reveal_hidden_from_hint(self) -> None:
        if not self.show_hidden:
            self.show_hidden = True
            self.cfg = tray_common.load_config()
            self.cfg["show_hidden"] = True
            tray_common.save_config(self.cfg)
            if hasattr(self, "_var_hidden"):
                try:
                    self._var_hidden.set(True)
                except Exception:
                    pass
            self.refresh_now()

    # ---- 窗口显示 / 最小化 / 切换显隐 / 关闭-最小化至托盘 ----
    def _minimize_window(self) -> None:
        neu_ui.minimize_frameless_window(self.root)

    def show_main_window(self) -> None:
        if self._closing:
            return
        neu_ui.dismiss_active_popup_menus(self.root)
        self.hover_tooltip.hide()
        try:
            was_hidden = str(self.root.state()) in ("withdrawn", "iconic")
        except Exception:
            was_hidden = False
        if was_hidden:
            try:
                self.root.attributes("-alpha", 0.0)
            except Exception:
                pass
        self.root.deiconify()
        neu_ui.enable_frameless_taskbar(self.root, round_corners=True)
        try:
            self.root.lift()
            self.root.focus_force()
            hwnd = neu_ui._root_hwnd(self.root)
            if hwnd:
                u32 = ctypes.WinDLL("user32", use_last_error=True)
                u32.ShowWindow(ctypes.c_void_p(hwnd), 9)  # SW_RESTORE
                u32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        except Exception:
            pass
        if was_hidden:
            neu_ui.fade_in_window(self.root, steps=6, step_ms=14)
        self._schedule_auto_refresh()

    def toggle_main_window(self) -> None:
        if self._closing:
            return
        try:
            st = str(self.root.state())
            hwnd = neu_ui._root_hwnd(self.root)
            is_fg = False
            if hwnd and st == "normal":
                u32 = ctypes.WinDLL("user32", use_last_error=True)
                is_fg = int(u32.GetForegroundWindow() or 0) == int(hwnd)
            if st == "normal" and is_fg:
                self.hide_to_tray()
                return
        except Exception:
            pass
        self.show_main_window()

    def focus_device(self, key: str) -> None:
        self._last_device_key = key
        self.show_main_window()
        self._pending_focus_key = key
        self._apply_pending_focus()

    def _apply_pending_focus(self) -> None:
        key = self._pending_focus_key
        if not key:
            return
        target: Optional[DeviceCard] = next((c for c in self._card_widgets if c.reading.key == key), None)
        if target is None:
            return
        self._pending_focus_key = None
        try:
            self.canvas.update_idletasks()
            total_h = max(1, self.cards_frame.winfo_height())
            cy = max(0, target.winfo_y() - neu_ui.sx(12, self.root))
            frac = max(0.0, min(1.0, cy / float(total_h)))
            self.canvas.yview_moveto(frac)
            target.set_focus_glow(True)
            self.root.after(
                1400,
                lambda c=target: c.set_focus_glow(False) if (not self._closing and c.winfo_exists()) else None,
            )
        except Exception:
            pass

    def hide_to_tray(self, *, from_user: bool = True) -> None:
        self._save_window_geometry()
        self._suppress_settings_return = True
        try:
            if self._log_modal is not None:
                m = self._log_modal
                self._log_modal = None
                m.close()
            if self._wizard_modal is not None:
                m = self._wizard_modal
                self._wizard_modal = None
                m.close()
            if self._debug_modal is not None:
                m = self._debug_modal
                self._debug_modal = None
                m.close()
            self.close_settings_modal()
        finally:
            self._suppress_settings_return = False

        if from_user and (not self._closing) and (not self._disable_tray_host):
            try:
                c = tray_common.load_config()
                if not c.get("tray_guide_shown", False):
                    c["tray_guide_shown"] = True
                    tray_common.save_config(c)
                    self.cfg = c
                    self.low_toast.show_notice(
                        "已最小化到系统托盘",
                        "程序将在后台继续监测电量；点击右下角托盘图标可随时打开面板或右键快速查看。",
                    )
            except Exception:
                pass

        try:
            if str(self.root.state()) == "normal":
                neu_ui.fade_out_window(
                    self.root,
                    steps=5,
                    step_ms=13,
                    on_done=lambda: (
                        self.root.withdraw(),
                        self._schedule_auto_refresh(hidden_override=True),
                    ) if (not self._closing and self.root.winfo_exists()) else None,
                )
                return
        except Exception:
            pass
        self.root.withdraw()
        self._schedule_auto_refresh(hidden_override=True)

    def on_request_close(self) -> None:
        cfg = tray_common.load_config()
        if cfg.get("minimize_on_close", True):
            self.hide_to_tray(from_user=True)
        else:
            self.quit_app()

    def quit_app(self) -> None:
        if self._closing:
            return
        if not self._disable_tray_host:
            tray_common.write_quit_flag()
        self._save_window_geometry()
        self._closing = True
        self._clear_retry_timers()
        if getattr(self, "_heartbeat_aid", None) is not None:
            try:
                self.root.after_cancel(self._heartbeat_aid)
            except Exception:
                pass
            self._heartbeat_aid = None
        try:
            neu_ui.dismiss_active_popup_menus(self.root)
            self.hover_tooltip.hide()
            self.low_toast.dismiss()
        except Exception:
            pass
        if self.tray_host:
            self.tray_host.stop()
        try:
            win_icon.destroy_all()
            win_icon.cleanup_files()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---- 错误日志查看浮层 ----
    def open_error_log_modal(self) -> None:
        if self._log_modal is not None:
            return
        prev_settings = self._settings_modal
        reuse_dim = getattr(prev_settings, "_dim_photo", None) if prev_settings is not None else None
        reuse_stages = list(getattr(prev_settings, "_dim_stages", []) or []) if prev_settings is not None else None
        opened_from_settings = prev_settings is not None

        def _on_closed() -> None:
            self._log_modal = None
            if (
                opened_from_settings
                and not self._closing
                and not getattr(self, "_suppress_settings_return", False)
                and self.root.winfo_exists()
            ):
                self.open_settings_modal(dim_photo=reuse_dim, dim_stages=reuse_stages)

        modal = neu_ui.NeuModalOverlay(
            self.root,
            title="运行日志与诊断",
            subtitle="多级轮转日志（2MB×3）与扫描诊断摘要 · 支持按级别筛选与一键导出",
            max_width_dp=720,
            min_height_dp=450,
            dim_photo=reuse_dim,
            dim_stages=reuse_stages,
            fade_scrim_on_close=not opened_from_settings,
            on_close=_on_closed,
            close_on_backdrop=True,
        )
        self._log_modal = modal

        # 扫描诊断摘要条（#44）
        last_st = self._last_scan_stats or devices.get_last_scan_stats()
        st_txt = (
            f"最近扫描诊断：{last_st.summary_text()}"
            if last_st.ended_at > 0
            else "最近扫描诊断：暂无本次会话扫描统计"
        )
        stat_well = neu_ui.NeuCard(modal.body, radius=12, pad=0, inner_pad=(10, 6), off=3, box_r=2, inset=True)
        stat_well.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))
        tk.Label(
            stat_well.content,
            text=st_txt,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
            wraplength=neu_ui.sx(640, modal.body),
            justify="left",
        ).pack(fill="x")

        # 独立等宽文件路径行 + 日志级别筛选 + 一键复制（#45）
        path_row = tk.Frame(modal.body, bg=neu_ui.BASE)
        path_row.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))
        path_well = neu_ui.NeuCard(
            path_row, radius=12, pad=0, inner_pad=(10, 5), off=3, box_r=2, inset=True
        )
        path_well.pack(side="left", fill="x", expand=True, padx=(0, neu_ui.sx(8, modal.body)))
        lbl_path = tk.Label(
            path_well.content,
            text=LOG_FILE,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
        )
        lbl_path.pack(fill="x")
        neu_ui.attach_tooltip(lbl_path, LOG_FILE)

        log_filter_var = tk.StringVar(value="全部级别")

        lbl_copy_tip = tk.Label(
            path_row,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.SUCCESS,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
        )

        def _copy_log_path() -> None:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(LOG_FILE)
                lbl_copy_tip.configure(text="已复制")
                lbl_copy_tip.pack(side="right", padx=(neu_ui.sx(6, modal.body), 0))
                self.root.after(
                    2200,
                    lambda: lbl_copy_tip.pack_forget() if lbl_copy_tip.winfo_exists() else None,
                )
            except Exception:
                pass

        neu_ui.NeuButton(
            path_row,
            text="复制路径",
            command=_copy_log_path,
            radius=12,
            pad=5,
            padx=10,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            tooltip="复制日志文件路径到剪贴板",
        ).pack(side="right", padx=(neu_ui.sx(6, modal.body), 0))

        neu_ui.NeuSelect(
            path_row,
            variable=log_filter_var,
            values=[lbl for lbl, _ in LOG_FILTER_CHOICES],
            on_change=lambda _e=None: _load_lines(),
            width_px=neu_ui.sx(100, modal.body),
        ).pack(side="right")

        well = neu_ui.NeuCard(modal.body, radius=16, pad=0, inner_pad=(12, 10), off=4, box_r=3, inset=True)
        well.pack(fill="both", expand=True, pady=(0, neu_ui.sx(12, modal.body)))
        txt = tk.Text(
            well.content,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=modal.body),
            wrap="word",
        )
        sb = neu_ui.NeuScrollbar(well.content, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(4, 0))
        txt.pack(side="left", fill="both", expand=True)

        def _load_lines() -> None:
            filt_lbl = log_filter_var.get()
            filt_val = next((v for k, v in LOG_FILTER_CHOICES if k == filt_lbl), "ALL")
            lines = tray_common.read_log_entries(level_filter=filt_val, max_lines=300)
            content = "\n".join(lines).strip() if lines else "暂无符合当前筛选级别的日志记录。"
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", content)
            txt.see("end")
            txt.configure(state="disabled")

        def _clear_log() -> None:
            if not neu_ui.ask_confirm_dialog(
                self.root,
                "清空运行日志",
                "清空后将同时移除当前与轮转备份日志文件，确认立即清空吗？",
                confirm_text="确认清空",
                cancel_text="取消",
                kind="warn",
                danger=True,
            ):
                return
            tray_common.clear_log_files()
            _load_lines()

        def _export_diagnostics() -> None:
            out_path = filedialog.asksaveasfilename(
                parent=self.root,
                title="导出诊断摘要与运行日志",
                defaultextension=".json",
                initialfile=f"battery_diagnostics_{time.strftime('%Y%m%d_%H%M%S')}.json",
                filetypes=[("JSON 诊断报告", "*.json"), ("文本文件", "*.txt")],
            )
            if not out_path:
                return
            st = self._last_scan_stats or devices.get_last_scan_stats()
            logs = tray_common.read_log_entries("ALL", max_lines=500)
            payload = {
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "log_level": tray_common.get_log_level(),
                "scan_stats": {
                    "started_at": st.started_at,
                    "ended_at": st.ended_at,
                    "duration_ms": st.duration_ms,
                    "hid_iface_count": st.hid_iface_count,
                    "hid_group_count": st.hid_group_count,
                    "bt_count": st.bt_count,
                    "matched_count": st.matched_count,
                    "online_count": st.online_count,
                    "offline_count": st.offline_count,
                    "failed_count": st.failed_count,
                    "skipped_count": st.skipped_count,
                    "protocol_counts": st.protocol_counts,
                    "skipped_devices": st.skipped_devices,
                    "per_device": st.per_device,
                },
                "logs": logs,
            }
            try:
                if out_path.lower().endswith(".txt"):
                    body_txt = (
                        f"=== 扫描诊断摘要 ({payload['exported_at']}) ===\n"
                        + st.summary_text()
                        + "\n\n=== 跳过未识别设备 ===\n"
                        + json.dumps(st.skipped_devices, ensure_ascii=False, indent=2)
                        + "\n\n=== 最近日志 ===\n"
                        + "\n".join(logs)
                    )
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(body_txt)
                else:
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                neu_ui.ask_message_dialog(self.root, "导出成功", f"已保存诊断文件：\n{out_path}", kind="success")
            except Exception as exc:
                neu_ui.ask_message_dialog(self.root, "导出失败", f"未能写入诊断文件：{exc}", kind="error")

        _load_lines()
        btn_row = tk.Frame(modal.body, bg=neu_ui.BASE)
        btn_row.pack(fill="x")
        neu_ui.NeuButton(
            btn_row,
            text="清空日志",
            command=_clear_log,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.RED_DANGER,
        ).pack(side="left", padx=(0, neu_ui.sx(8, modal.body)))
        neu_ui.NeuButton(
            btn_row,
            text="导出诊断包…",
            command=_export_diagnostics,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            tooltip="导出 ScanStats 扫描诊断摘要与最近多级日志",
        ).pack(side="left", padx=(0, neu_ui.sx(8, modal.body)))
        neu_ui.NeuButton(
            btn_row,
            text="告警统计报表",
            command=lambda: (modal.close(), self.open_alert_report_modal()),
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.BLURPLE_LINK,
            tooltip="查看低电/充满电告警历史记录与统计",
        ).pack(side="left")
        neu_ui.NeuButton(
            btn_row,
            text="刷新",
            command=_load_lines,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            accent=True,
        ).pack(side="right")

        # 先在新浮层就位并覆盖同一暗化遮罩，再关闭旧设置浮层，避免界面跳转时露出主屏闪烁
        if prev_settings is not None:
            try:
                modal.update_idletasks()
            except Exception:
                pass
            if self._settings_modal is prev_settings:
                self._settings_modal = None
            prev_settings.close()

    # ---- 设置模态浮层（不打开新窗口，背景变暗且不可点击，左上带返回按钮） ----
    def close_settings_modal(self) -> None:
        if self._settings_modal is not None:
            m = self._settings_modal
            self._settings_modal = None
            m.close()

    def open_settings_modal(
        self,
        *,
        dim_photo: Optional[Any] = None,
        dim_stages: Optional[list[Any]] = None,
    ) -> None:
        if self._settings_modal is not None or self._debug_modal is not None:
            return

        self.cfg = tray_common.load_config()
        threshold = int(self.cfg.get("notify_threshold", 20))

        def _on_closed() -> None:
            self._settings_modal = None
            self._sync_footer()

        modal = neu_ui.NeuModalOverlay(
            self.root,
            title="设置",
            subtitle="配置实时生效 · 仅作用于当前面板与常驻托盘",
            max_width_dp=600,
            dim_photo=dim_photo,
            dim_stages=dim_stages,
            fade_scrim_on_close=True,
            on_close=_on_closed,
            close_on_backdrop=True,
        )
        self._settings_modal = modal
        body = modal.body
        select_w = neu_ui.sx(132, body)

        def make_section(sec_title: str) -> tuple[neu_ui.NeuCard, Any, Any]:
            tk.Label(
                body, text=sec_title, bg=neu_ui.BASE, fg=neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=body), anchor="w",
            ).pack(fill="x", pady=(neu_ui.sx(4, body), neu_ui.sx(4, body)))
            card = neu_ui.NeuCard(body, radius=18, pad=0, inner_pad=(16, 10), off=5, box_r=3, inset=True)
            card.pack(fill="x", pady=(0, neu_ui.sx(10, body)))
            first_row = [True]

            def add_row(title: str, desc: str) -> tk.Frame:
                if not first_row[0]:
                    tk.Frame(card.content, bg=neu_ui.SEP_LIGHT, height=1).pack(fill="x", pady=neu_ui.sx(4, body))
                first_row[0] = False
                row = tk.Frame(card.content, bg=neu_ui.BASE)
                row.pack(fill="x", pady=neu_ui.sx(6, body))
                right = tk.Frame(row, bg=neu_ui.BASE)
                right.pack(side="right", padx=(neu_ui.sx(12, body), 0))
                left = tk.Frame(row, bg=neu_ui.BASE)
                left.pack(side="left", fill="x", expand=True)
                tk.Label(
                    left, text=title, bg=neu_ui.BASE, fg=neu_ui.TEXT,
                    font=neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=body), anchor="w",
                ).pack(fill="x")
                tk.Label(
                    left, text=desc, bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM,
                    font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=body), anchor="w",
                    wraplength=neu_ui.sx(330, body), justify="left",
                ).pack(fill="x", pady=(1, 0))
                return right

            return card, add_row, first_row

        # ---- 分组 1：外观与视图 ----
        _s1, add_row1, _ = make_section("外观与视图")

        r_theme = add_row1("外观主题", "支持浅色、深色或自动跟随 Windows 系统深浅主题")
        cur_theme_key = str(self.cfg.get("theme_mode", "light"))
        cur_theme_lbl = next((lbl for lbl, val in THEME_CHOICES if val == cur_theme_key), "浅色")
        self._var_theme = tk.StringVar(value=cur_theme_lbl)
        neu_ui.NeuSelect(
            r_theme, variable=self._var_theme, values=[lbl for lbl, _ in THEME_CHOICES],
            on_change=self._on_theme_change, width_px=select_w,
        ).pack()

        r_fs = add_row1("界面字号", "调整文字与卡片控件整体尺寸（大/特大适合高分屏或远距阅读）")
        cur_fs_key = str(self.cfg.get("font_scale_mode", "standard"))
        cur_fs_lbl = next((lbl for lbl, val in FONT_SCALE_CHOICES if val == cur_fs_key), "标准")
        self._var_font_scale = tk.StringVar(value=cur_fs_lbl)
        neu_ui.NeuSelect(
            r_fs, variable=self._var_font_scale, values=[lbl for lbl, _ in FONT_SCALE_CHOICES],
            on_change=self._on_font_scale_change, width_px=select_w,
        ).pack()

        r_sort = add_row1("设备排序方式", "置顶设备始终排在最前；可按默认角色、低电优先、名称或最近活跃排序")
        cur_sort = str(self.cfg.get("sort_mode", "default"))
        cur_sort_lbl = next((lbl for lbl, val in SORT_MODE_CHOICES if val == cur_sort), "默认顺序")
        self._var_sort_mode = tk.StringVar(value=cur_sort_lbl)
        neu_ui.NeuSelect(
            r_sort, variable=self._var_sort_mode, values=[lbl for lbl, _ in SORT_MODE_CHOICES],
            on_change=self._on_settings_sort_change, width_px=select_w,
        ).pack()

        r_cmp = add_row1("紧凑视图模式", "单行紧凑列表展示，6-8 台设备一屏总览，无需频繁翻页")
        self._var_compact = tk.BooleanVar(value=bool(self.cfg.get("compact_view", False)))
        neu_ui.NeuToggle(
            r_cmp, text="", variable=self._var_compact, command=self._on_toggle_compact,
        ).pack()

        r_hid = add_row1("显示已隐藏外设", "在主界面展示手动隐藏的外设以便恢复或管理")
        self._var_hidden = tk.BooleanVar(value=self.show_hidden)
        neu_ui.NeuToggle(
            r_hid, text="", variable=self._var_hidden, command=self._on_toggle_show_hidden,
        ).pack()

        # ---- 分组 2：采集频率与驻留 ----
        _s2, add_row2, _ = make_section("采集与后台节能")

        r_iv = add_row2("前台刷新间隔", "主窗口展开可见时的自动扫描周期")
        cur_iv = int(self.cfg.get("interval", 120))
        cur_lbl = next((lbl for lbl, sec in INTERVAL_CHOICES if sec == cur_iv), "2 分钟")
        self._var_interval = tk.StringVar(value=cur_lbl)
        neu_ui.NeuSelect(
            r_iv, variable=self._var_interval, values=[lbl for lbl, _ in INTERVAL_CHOICES],
            on_change=self._on_interval_change, width_px=select_w,
        ).pack()

        r_bg_iv = add_row2("后台刷新间隔", "最小化隐藏至托盘后自动切换为低功耗间隔并跳过离线蓝牙枚举")
        cur_bg_iv = int(self.cfg.get("bg_interval", 300))
        cur_bg_lbl = next((lbl for lbl, sec in BG_INTERVAL_CHOICES if sec == cur_bg_iv), "5 分钟 (默认)")
        self._var_bg_interval = tk.StringVar(value=cur_bg_lbl)
        neu_ui.NeuSelect(
            r_bg_iv, variable=self._var_bg_interval, values=[lbl for lbl, _ in BG_INTERVAL_CHOICES],
            on_change=self._on_bg_interval_change, width_px=select_w,
        ).pack()

        r_min = add_row2("关闭软件最小化", "点击右上角 × 关闭按钮时隐藏至右下角托盘，不中断后台监测")
        self._var_min_close = tk.BooleanVar(value=bool(self.cfg.get("minimize_on_close", True)))
        neu_ui.NeuToggle(
            r_min, text="", variable=self._var_min_close, command=self._on_toggle_minimize_close,
        ).pack()

        r_auto = add_row2("开机自启", "Windows 登录时自动最小化启动并驻留托盘")
        auto_on, auto_msg = tray_common.autostart_status()
        self._var_autostart = tk.BooleanVar(value=auto_on)
        neu_ui.NeuToggle(
            r_auto, text="", variable=self._var_autostart, command=self._on_toggle_autostart,
        ).pack()

        # ---- 分组 3：告警、通知与系统托盘 ----
        _s3, add_row3, _ = make_section("低电告警与系统托盘")

        r_low = add_row3("低电量系统通知", "通过 Windows 原生通知中心发送提醒（支持专注助手与回看历史）")
        self._var_low_notify = tk.BooleanVar(value=bool(self.cfg.get("notify_enabled", True)))
        neu_ui.NeuToggle(
            r_low, text="", variable=self._var_low_notify, command=self._on_toggle_low_notify,
        ).pack()

        r_full = add_row3("充满电完成提醒", "当设备从「充电中」转为「已充满/100%」时发送一次提醒")
        self._var_full_notify = tk.BooleanVar(value=bool(self.cfg.get("notify_full_charge", True)))
        neu_ui.NeuToggle(
            r_full, text="", variable=self._var_full_notify, command=self._on_toggle_full_notify,
        ).pack()

        r_thresh = add_row3("默认低电告警阈值", "未单独设置阈值的外设低于该值触发提醒（可在卡片「更多」菜单按设备独立设置）")
        cur_th_lbl = next((lbl for lbl, val in THRESHOLD_CHOICES if val == threshold), f"{threshold}%")
        self._var_threshold = tk.StringVar(value=cur_th_lbl)
        neu_ui.NeuSelect(
            r_thresh, variable=self._var_threshold, values=[lbl for lbl, _ in THRESHOLD_CHOICES],
            on_change=self._on_threshold_change, width_px=select_w,
        ).pack()

        r_cool = add_row3("常规提醒冷却频率", "同一设备触发普通低电通知后的静默防骚扰冷却间隔")
        cur_cd = int(self.cfg.get("notify_cooldown_min", 30))
        cur_cd_lbl = next((lbl for lbl, val in COOLDOWN_CHOICES if val == cur_cd), "30 分钟 (默认)")
        self._var_cooldown = tk.StringVar(value=cur_cd_lbl)
        neu_ui.NeuSelect(
            r_cool, variable=self._var_cooldown, values=[lbl for lbl, _ in COOLDOWN_CHOICES],
            on_change=self._on_cooldown_change, width_px=select_w,
        ).pack()

        r_alpha = add_row3("低电弹窗不透明度", "调节右下角低电量提醒弹窗的不透明度（默认 60%）")
        cur_alpha = int(self.cfg.get("notify_alpha", 60))
        cur_alpha_lbl = next((lbl for lbl, val in TOAST_ALPHA_CHOICES if val == cur_alpha), "60% (默认)")
        self._var_toast_alpha = tk.StringVar(value=cur_alpha_lbl)
        neu_ui.NeuSelect(
            r_alpha, variable=self._var_toast_alpha, values=[lbl for lbl, _ in TOAST_ALPHA_CHOICES],
            on_change=self._on_toast_alpha_change, width_px=select_w,
        ).pack()

        # 托盘图标样式 + 实时预览 (#37)
        r_style = add_row3("托盘图标样式", "可选数字电量、环形电弧、极简状态点或按最低电量设备图标")
        style_box = tk.Frame(r_style, bg=neu_ui.BASE)
        style_box.pack()
        self._lbl_tray_preview = tk.Label(style_box, bg=neu_ui.BASE, bd=0, highlightthickness=0)
        self._lbl_tray_preview.pack(side="left", padx=(0, neu_ui.sx(8, body)))
        cur_st = str(self.cfg.get("tray_icon_style", "digit"))
        cur_st_lbl = next((lbl for lbl, val in TRAY_STYLE_CHOICES if val == cur_st), "数字")
        self._var_tray_style = tk.StringVar(value=cur_st_lbl)
        neu_ui.NeuSelect(
            style_box, variable=self._var_tray_style, values=[lbl for lbl, _ in TRAY_STYLE_CHOICES],
            on_change=self._on_tray_style_change, width_px=neu_ui.sx(108, body),
        ).pack(side="left")
        self._update_tray_style_preview(cur_st)

        r_vis = add_row3("托盘图标显示时机", "选择始终驻留系统托盘，或仅当外设触发低电告警时自动显示")
        cur_vis = str(self.cfg.get("tray_visibility", "always"))
        cur_vis_lbl = next((lbl for lbl, val in TRAY_VIS_CHOICES if val == cur_vis), "常驻托盘")
        self._var_tray_vis = tk.StringVar(value=cur_vis_lbl)
        neu_ui.NeuSelect(
            r_vis, variable=self._var_tray_vis, values=[lbl for lbl, _ in TRAY_VIS_CHOICES],
            on_change=self._on_tray_vis_change, width_px=select_w,
        ).pack()

        r_hover = add_row3("托盘悬停提示样式", "四宫格浮层与 Windows 系统原生气泡互斥展示，避免双弹窗遮挡")
        cur_hm = str(self.cfg.get("tray_hover_mode", "grid"))
        cur_hm_lbl = next((lbl for lbl, val in TRAY_HOVER_CHOICES if val == cur_hm), "四宫格浮层 (默认)")
        self._var_tray_hover = tk.StringVar(value=cur_hm_lbl)
        neu_ui.NeuSelect(
            r_hover, variable=self._var_tray_hover, values=[lbl for lbl, _ in TRAY_HOVER_CHOICES],
            on_change=self._on_tray_hover_change, width_px=select_w,
        ).pack()

        r_wd = add_row3("后台看门狗守护", "开机自启由轻量守护进程托管，UI 卡死或异常崩溃时自动恢复重启")
        self._var_watchdog = tk.BooleanVar(value=bool(self.cfg.get("watchdog_enabled", True)))
        neu_ui.NeuToggle(
            r_wd, text="", variable=self._var_watchdog, command=self._on_toggle_watchdog,
        ).pack()

        r_log = add_row3("日志记录级别", "记录关键枚举与协议命中过程，轮转上限 2MB × 3 份")
        cur_ll = str(self.cfg.get("log_level", "INFO")).upper()
        cur_ll_lbl = next((lbl for lbl, val in LOG_LEVEL_CHOICES if val == cur_ll), "INFO (默认)")
        self._var_log_level = tk.StringVar(value=cur_ll_lbl)
        neu_ui.NeuSelect(
            r_log, variable=self._var_log_level, values=[lbl for lbl, _ in LOG_LEVEL_CHOICES],
            on_change=self._on_log_level_change, width_px=select_w,
        ).pack()

        # ---- 分组 3b：历史保留与数据管理 (#5 / #7 / #13) ----
        sec_hist, add_row_h, _ = make_section("历史记录与数据管理")
        r_ret = add_row_h("历史采样保留天数", "控制电量折线与能耗估算的历史保留窗口（默认 7 天，支持 1~30 天）")
        cur_ret = tray_common.get_retention_days(self.cfg)
        cur_ret_lbl = next((lbl for lbl, val in RETENTION_DAY_CHOICES if val == cur_ret), "7 天 (默认)")
        self._var_retention = tk.StringVar(value=cur_ret_lbl)
        neu_ui.NeuSelect(
            r_ret, variable=self._var_retention, values=[lbl for lbl, _ in RETENTION_DAY_CHOICES],
            on_change=self._on_retention_change, width_px=select_w,
        ).pack()

        hist_btns = tk.Frame(sec_hist.content, bg=neu_ui.BASE)
        hist_btns.pack(fill="x", pady=(neu_ui.sx(6, body), neu_ui.sx(2, body)))
        neu_ui.NeuButton(
            hist_btns, text="告警统计报表", command=self.open_alert_report_modal,
            accent=True, radius=12, pad=5, padx=10, pady=4, font_size=neu_ui.FONT_CAPTION,
            tooltip="查看 7 天 / 30 天 / 本月低电告警次数与持续时长",
        ).pack(side="left", padx=(0, neu_ui.sx(6, body)))
        neu_ui.NeuButton(
            hist_btns, text="导出全部历史", command=self._export_all_history,
            radius=12, pad=5, padx=10, pady=4, font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left", padx=(0, neu_ui.sx(6, body)))
        neu_ui.NeuButton(
            hist_btns, text="导入合并历史", command=self._import_all_history,
            radius=12, pad=5, padx=10, pady=4, font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left", padx=(0, neu_ui.sx(6, body)))
        neu_ui.NeuButton(
            hist_btns, text="清空全部历史", command=self._clear_all_history,
            radius=12, pad=5, padx=10, pady=4, font_size=neu_ui.FONT_CAPTION, fg=neu_ui.RED_DANGER,
        ).pack(side="right")

        if tray_common.is_portable_mode():
            mig_row = tk.Frame(sec_hist.content, bg=neu_ui.BASE)
            mig_row.pack(fill="x", pady=(neu_ui.sx(6, body), 0))
            tk.Label(
                mig_row,
                text="当前数据存放在程序目录（便携模式），可一键迁入 %APPDATA% 标准用户目录",
                bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=body), anchor="w",
            ).pack(side="left", fill="x", expand=True)
            neu_ui.NeuButton(
                mig_row, text="迁移至 %APPDATA%", command=self._on_migrate_to_appdata,
                radius=12, pad=4, padx=9, pady=3, font_size=neu_ui.FONT_CAPTION, fg=neu_ui.BLURPLE_LINK,
            ).pack(side="right")

        # ---- 分组 4：手动设备与已忽略设备管理 (#19 / #20) ----
        sec4, _add4, _ = make_section("手动接入设备与忽略列表")
        man_row = tk.Frame(sec4.content, bg=neu_ui.BASE)
        man_row.pack(fill="x", pady=(neu_ui.sx(4, body), neu_ui.sx(4, body)))
        tk.Label(
            man_row, text="手动添加 VID:PID", bg=neu_ui.BASE, fg=neu_ui.TEXT,
            font=neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=body),
        ).pack(side="left")
        self._var_manual_vp = tk.StringVar(value="")
        ent_vp = neu_ui.NeuEntry(
            man_row, textvariable=self._var_manual_vp, width=14,
            font_tuple=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=body),
        )
        ent_vp.pack(side="left", padx=(neu_ui.sx(10, body), neu_ui.sx(6, body)))
        ent_vp.entry.bind("<Return>", lambda _e: self._on_add_manual_device())
        neu_ui.NeuButton(
            man_row, text="强制纳入扫描", command=self._on_add_manual_device,
            accent=True, radius=12, pad=5, padx=10, pady=4, font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left")

        self._manual_tags_frame = tk.Frame(sec4.content, bg=neu_ui.BASE)
        self._manual_tags_frame.pack(fill="x", pady=(neu_ui.sx(4, body), neu_ui.sx(6, body)))

        tk.Frame(sec4.content, bg=neu_ui.SEP_LIGHT, height=1).pack(fill="x", pady=neu_ui.sx(4, body))

        ign_hdr = tk.Frame(sec4.content, bg=neu_ui.BASE)
        ign_hdr.pack(fill="x", pady=(neu_ui.sx(4, body), neu_ui.sx(2, body)))
        tk.Label(
            ign_hdr, text="已不再显示的设备", bg=neu_ui.BASE, fg=neu_ui.TEXT,
            font=neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=body),
        ).pack(side="left")
        self._ignored_tags_frame = tk.Frame(sec4.content, bg=neu_ui.BASE)
        self._ignored_tags_frame.pack(fill="x", pady=(neu_ui.sx(2, body), neu_ui.sx(4, body)))

        self._refresh_settings_device_lists()

        # ---- 工具与快捷操作 ----
        tools_row = tk.Frame(body, bg=neu_ui.BASE)
        tools_row.pack(fill="x", pady=(neu_ui.sx(4, body), neu_ui.sx(8, body)))

        neu_ui.NeuButton(
            tools_row,
            text="调试与标定",
            command=lambda: self.open_debug_tool(None),
            accent=True,
            weight="bold",
            radius=14,
            pad=8,
            padx=14,
            pady=6,
            font_size=neu_ui.FONT_BODY,
            tooltip="打开 HID/蓝牙接口探测与协议标定面板",
        ).pack(side="left", padx=(0, neu_ui.sx(6, body)))

        neu_ui.NeuButton(
            tools_row,
            text="桌面快捷方式",
            command=self._on_install_links,
            radius=14,
            pad=8,
            padx=12,
            pady=6,
            font_size=neu_ui.FONT_BODY,
            fg=neu_ui.TEXT,
            tooltip="在桌面生成快捷方式",
        ).pack(side="left", padx=(0, neu_ui.sx(6, body)))

        neu_ui.NeuButton(
            tools_row,
            text="查看日志与诊断",
            command=self.open_error_log_modal,
            radius=14,
            pad=8,
            padx=12,
            pady=6,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.TEXT_MUTED,
            tooltip="查看多级日志与扫描诊断摘要",
        ).pack(side="left", padx=(0, neu_ui.sx(6, body)))

        neu_ui.NeuButton(
            tools_row,
            text="关于与更新",
            command=self.open_about_modal,
            radius=14,
            pad=8,
            padx=12,
            pady=6,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.TEXT_MUTED,
            tooltip="查看当前版本、运行目录与更新状态",
        ).pack(side="left")

        neu_ui.NeuButton(
            tools_row,
            text="退出软件",
            command=self._confirm_quit_app,
            radius=14,
            pad=8,
            padx=12,
            pady=6,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.RED_DANGER,
            tooltip="关闭主界面与常驻系统托盘",
        ).pack(side="right")

        ret_days_info = tray_common.get_retention_days(self.cfg)
        self._lbl_modal_info = tk.Label(
            body,
            text=(
                f"版本 v{tray_common.APP_VERSION} ({tray_common.APP_BUILD_DATE}){neu_ui.TEXT_SEP}"
                f"开机自启状态：{auto_msg}{neu_ui.TEXT_SEP}历史记录自动保留最近 {ret_days_info} 天"
            ),
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_DIM,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=body),
            anchor="w",
            wraplength=neu_ui.sx(530, body),
            justify="left",
        )
        self._lbl_modal_info.pack(fill="x", pady=(neu_ui.sx(4, body), neu_ui.sx(2, body)))

        self._lbl_modal_msg = tk.Label(
            body,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.SUCCESS,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=body),
            anchor="w",
            wraplength=neu_ui.sx(530, body),
            justify="left",
        )
        self._lbl_modal_msg.pack(fill="x", pady=(neu_ui.sx(2, body), neu_ui.sx(4, body)))

    def _confirm_quit_app(self) -> None:
        if neu_ui.ask_confirm_dialog(
            self.root,
            "退出软件",
            "确定要退出外设电量查看器吗？\n退出后将关闭常驻托盘图标，并停止低电量后台提醒。",
            confirm_text="确认退出",
            cancel_text="取消",
            kind="warn",
            danger=True,
        ):
            self.quit_app()

    # ---- 布局、DPI 同步与卡片刷新 ----
    def _max_content_width(self) -> int:
        return neu_ui.sx(1720, self.root)

    def _sync_chrome_widths(self, content_w: Optional[int] = None) -> None:
        try:
            avail = self._wrap_outer.winfo_width() - neu_ui.sx(24, self.root) if hasattr(self, "_wrap_outer") else self.canvas.winfo_width()
            cw = content_w if content_w is not None else min(
                self._max_content_width(),
                max(neu_ui.sx(400, self.root), avail),
            )
            self._header_outer.columnconfigure(1, minsize=cw)
            if hasattr(self, "_filter_outer") and self._filter_outer.winfo_exists():
                self._filter_outer.columnconfigure(1, minsize=cw)
            self._footer_outer.columnconfigure(1, minsize=cw)
            self._wrap_outer.columnconfigure(1, minsize=cw)
            # 摘要动态限宽折行（保留右侧按钮与操作区余量，最多两行防截断）
            if hasattr(self, "lbl_summary"):
                sum_wrap = max(neu_ui.sx(220, self.root), cw - neu_ui.sx(420, self.root))
                if int(self.lbl_summary.cget("wraplength")) != sum_wrap:
                    self.lbl_summary.configure(wraplength=sum_wrap)
        except Exception:
            pass

    def _on_wrap_configure(self, event=None) -> None:
        if self._rebuild_pending or event is None:
            return
        avail = max(neu_ui.sx(320, self.root), int(event.width) - neu_ui.sx(24, self.root))
        cw = min(self._max_content_width(), avail)
        try:
            self._wrap_outer.columnconfigure(1, minsize=cw)
            self._sync_chrome_widths(cw)
        except Exception:
            pass

    def _on_root_configure(self, event=None) -> None:
        if event is not None and event.widget is not self.root:
            return
        if neu_ui.refresh_dpi_scale(self.root):
            reopen_settings = self._settings_modal is not None
            self.root.after_idle(lambda: self._rebuild_ui(reopen_settings=reopen_settings))

    def _on_cards_configure(self, _e=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, e) -> None:
        win_w = max(neu_ui.sx(300, self.root), int(e.width))
        self.canvas.coords(self.canvas_win, 0, 0)
        self.canvas.itemconfigure(self.canvas_win, width=win_w)
        if win_w >= neu_ui.sx(1540, self.root):
            cols = 4
        elif win_w >= neu_ui.sx(1160, self.root):
            cols = 3
        elif win_w >= neu_ui.sx(760, self.root):
            cols = 2
        else:
            cols = 1
        if cols != self._grid_cols:
            self._grid_cols = cols
            self._layout_card_grid()
        elif len(self._card_widgets or [w for w in self.cards_frame.winfo_children() if isinstance(w, neu_ui.NeuSkeletonCard)]) == 1:
            self._layout_card_grid()

    def _layout_card_grid(self) -> None:
        gx = neu_ui.sx(CARD_GAP, self.root)
        gy = neu_ui.sx(CARD_GAP, self.root)
        for c in range(4):
            self.cards_frame.columnconfigure(c, weight=0, minsize=0, uniform="")

        if bool(self.cfg.get("group_by_role", False)) and self._card_widgets and self._group_headers:
            collapsed = set(self.cfg.get("collapsed_roles") or [])
            try:
                grid_w = max(neu_ui.sx(360, self.root), int(self.canvas.winfo_width()) or neu_ui.sx(760, self.root))
            except Exception:
                grid_w = neu_ui.sx(760, self.root)
            compact_on = bool(self.cfg.get("compact_view", False))
            if compact_on:
                eff_cols = 2 if grid_w >= neu_ui.sx(1320, self.root) else 1
            else:
                eff_cols = max(1, min(4, self._grid_cols))
            for c in range(eff_cols):
                self.cards_frame.columnconfigure(
                    c, weight=1, minsize=neu_ui.sx(CARD_MIN_WIDTH_DP, self.root), uniform="col"
                )
            by_role: dict[str, list[DeviceCard]] = {}
            for card in self._card_widgets:
                by_role.setdefault(card.reading.role, []).append(card)
            row_cursor = 0
            for hdr in self._group_headers:
                role = str(getattr(hdr, "_group_role", "other"))
                hdr.grid(row=row_cursor, column=0, columnspan=eff_cols, sticky="ew", padx=gx, pady=(gy, neu_ui.sx(4, self.root)))
                row_cursor += 1
                cards = by_role.get(role) or []
                if role in collapsed:
                    for card in cards:
                        card.grid_forget()
                    continue
                for idx, card in enumerate(cards):
                    r_off, c_idx = divmod(idx, eff_cols)
                    card.grid(row=row_cursor + r_off, column=c_idx, columnspan=1, sticky="nsew" if not compact_on else "ew", padx=gx, pady=(gy // 2 if compact_on else gy, gy))
                row_cursor += (len(cards) + eff_cols - 1) // eff_cols
            return

        children = self._card_widgets or [
            w for w in self.cards_frame.winfo_children() if isinstance(w, neu_ui.NeuSkeletonCard)
        ]
        if not children:
            return

        if bool(self.cfg.get("compact_view", False)) and self._card_widgets:
            try:
                grid_w = max(neu_ui.sx(360, self.root), int(self.canvas.winfo_width()) or neu_ui.sx(760, self.root))
            except Exception:
                grid_w = neu_ui.sx(760, self.root)
            c_cols = 2 if grid_w >= neu_ui.sx(1320, self.root) else 1
            for c in range(c_cols):
                self.cards_frame.columnconfigure(c, weight=1, minsize=neu_ui.sx(CARD_MIN_WIDTH_DP, self.root), uniform="col")
            total = len(children)
            row_gy = max(2, gy // 2)
            for idx, card in enumerate(children):
                r_idx, c_idx = divmod(idx, c_cols)
                is_last = (idx // c_cols) == ((total - 1) // c_cols)
                card.grid(
                    row=r_idx, column=c_idx, columnspan=1, sticky="ew",
                    padx=gx, pady=(row_gy, row_gy + (neu_ui.sx(8, self.root) if is_last else 0)),
                )
            return

        cols = max(1, min(4, self._grid_cols))
        if len(children) == 1:
            # 单台设备居中按比例限宽展示（不超过 CARD_MAX_SINGLE_DP），窄窗与宽屏皆舒展平衡
            self.cards_frame.columnconfigure(0, weight=1, uniform="")
            try:
                grid_w = max(neu_ui.sx(360, self.root), int(self.canvas.winfo_width()) or neu_ui.sx(720, self.root))
            except Exception:
                grid_w = neu_ui.sx(720, self.root)
            max_single = neu_ui.sx(CARD_MAX_SINGLE_DP, self.root)
            target_w = min(max_single, max(neu_ui.sx(CARD_MIN_WIDTH_DP, self.root), int(grid_w * 0.78)))
            pad_side = max(gx, (grid_w - target_w) // 2)
            children[0].grid(
                row=0, column=0, columnspan=1, sticky="ew", padx=pad_side, pady=(gy, gy + neu_ui.sx(8, self.root))
            )
            return

        eff_cols = min(cols, len(children))
        for c in range(eff_cols):
            self.cards_frame.columnconfigure(
                c, weight=1, minsize=neu_ui.sx(CARD_MIN_WIDTH_DP, self.root), uniform="col"
            )
        total = len(children)
        for idx, card in enumerate(children):
            r_idx, c_idx = divmod(idx, eff_cols)
            is_last_row = (idx // eff_cols) == ((total - 1) // eff_cols)
            bot_pad = gy + (neu_ui.sx(8, self.root) if is_last_row else 0)
            card.grid(row=r_idx, column=c_idx, columnspan=1, sticky="nsew", padx=gx, pady=(gy, bot_pad))

    def _render_skeletons(self) -> None:
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self._card_widgets.clear()
        known = self.cfg.get("known_devices") or {}
        if isinstance(known, dict) and len(known) > 0:
            cnt = max(1, min(4, len(known)))
        else:
            cnt = 2
        for _ in range(cnt):
            neu_ui.NeuSkeletonCard(self.cards_frame)
        self._layout_card_grid()

    def _on_command_error(self, source: str, exc: Exception) -> None:
        import traceback
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        _log_app_error(source or "UI", detail)
        msg = _friendly_error_message(str(exc))
        self._set_modal_note(f"操作失败：{msg}", neu_ui.RED_DANGER)
        try:
            self.lbl_summary.configure(text=f"操作失败 ({source or '界面交互'})：{msg}", fg=neu_ui.RED_DANGER)
            self._btn_retry.pack(side="left")
            self._btn_view_log.pack(side="left", padx=(neu_ui.sx(4, self.root), 0))
        except Exception:
            pass

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self.root:
            return
        self._closing = True
        if getattr(self, "_pump_aid", None) is not None:
            try:
                self.root.after_cancel(self._pump_aid)
            except Exception:
                pass
            self._pump_aid = None
        if getattr(self, "_restore_exc_hooks", None) is not None:
            try:
                self._restore_exc_hooks()
            except Exception:
                pass
            self._restore_exc_hooks = None
        for aid in self._after_ids:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self._after_ids.clear()
        self._stop_scan_progress_timer()
        if self._note_clear_aid is not None:
            try:
                self.root.after_cancel(self._note_clear_aid)
            except Exception:
                pass
            self._note_clear_aid = None
        if self._refresh_timer_id is not None:
            try:
                self.root.after_cancel(self._refresh_timer_id)
            except Exception:
                pass
            self._refresh_timer_id = None
        if getattr(self, "_heartbeat_aid", None) is not None:
            try:
                self.root.after_cancel(self._heartbeat_aid)
            except Exception:
                pass
            self._heartbeat_aid = None
        try:
            self.hover_tooltip.hide()
            self.low_toast.dismiss()
        except Exception:
            pass
        if self.tray_host:
            self.tray_host.stop()

    def _on_mousewheel(self, e) -> None:
        active_modal = getattr(self.root, "_neu_active_modal", None)
        if active_modal is not None and not getattr(active_modal, "_closed", True):
            active_modal._on_body_wheel(e)
            return
        if (
            self._settings_modal is not None
            or self._debug_modal is not None
            or self._log_modal is not None
            or self._wizard_modal is not None
            or getattr(self, "_history_modal", None) is not None
            or getattr(self, "_about_modal", None) is not None
            or getattr(self, "_alert_report_modal", None) is not None
        ):
            return
        if not self._closing and self.canvas.winfo_exists():
            # 累计精确触控板增量，避免小幅度 delta 被 int() 取整丢弃
            self._wheel_accum += (-float(getattr(e, "delta", 0)) / 120.0) * 3.0
            steps = int(self._wheel_accum)
            if steps != 0:
                self._wheel_accum -= steps
                self.canvas.yview_scroll(steps, "units")

    def _sync_footer(self) -> None:
        iv_s = int(self.cfg.get("interval", 120))
        bg_s = int(self.cfg.get("bg_interval", 300))
        iv_lbl = next((lbl for lbl, sec in INTERVAL_CHOICES if sec == iv_s), f"{iv_s} 秒")
        bg_lbl = next((lbl for lbl, sec in BG_INTERVAL_CHOICES if sec == bg_s), f"{bg_s} 秒").replace(" (默认)", "")
        notify_on = bool(self.cfg.get("notify_enabled", True))
        threshold = int(self.cfg.get("notify_threshold", 20))
        min_txt = "最小化至托盘" if self.cfg.get("minimize_on_close", True) else "关闭即退出"
        self.lbl_footer_right.configure(
            text=f"前台 {iv_lbl}/后台 {bg_lbl}{neu_ui.TEXT_SEP}低电(<{threshold}%) {'开启' if notify_on else '关闭'}{neu_ui.TEXT_SEP}{min_txt}"
        )

    def _is_window_hidden(self) -> bool:
        try:
            return str(self.root.state()) == "withdrawn"
        except Exception:
            return False

    def _schedule_auto_refresh(self, hidden_override: Optional[bool] = None) -> None:
        if self._closing:
            return
        if self._refresh_timer_id is not None:
            try:
                self.root.after_cancel(self._refresh_timer_id)
            except Exception:
                pass
            self._refresh_timer_id = None
        if getattr(self, "_heartbeat_aid", None) is not None:
            try:
                self.root.after_cancel(self._heartbeat_aid)
            except Exception:
                pass
            self._heartbeat_aid = None
        hidden = self._is_window_hidden() if hidden_override is None else bool(hidden_override)
        iv_s = max(
            30 if hidden else 15,
            int(self.cfg.get("bg_interval", 300) if hidden else self.cfg.get("interval", 120)),
        )
        self._last_scheduled_interval = iv_s
        self._refresh_timer_id = self.root.after(iv_s * 1000, self._auto_tick)

    def _auto_tick(self) -> None:
        self._refresh_timer_id = None
        if self._closing:
            return
        self.refresh_now(background=self._is_window_hidden())
        self._schedule_auto_refresh()

    def _clear_retry_timers(self) -> None:
        for aid in list(self._retry_timer_ids):
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self._retry_timer_ids.clear()

    def _schedule_failed_retries(self, res: list[devices.PeripheralReading], cycle_id: int) -> None:
        """失败设备本轮短间隔重试队列：第 1 次 5s，第 2 次 15s（#15）。"""
        for r in res:
            is_transient_fail = (
                (not r.offline)
                and (not r.asleep)
                and (r.level is None)
                and r.transport == "HID"
                and (not r.manual)
            )
            if not is_transient_fail:
                self._retry_attempts.pop(r.key, None)
                continue
            tries = self._retry_attempts.get(r.key, 0)
            if tries >= 2:
                continue
            delay_ms = 5000 if tries == 0 else 15000
            self._retry_attempts[r.key] = tries + 1
            aid = self.root.after(
                delay_ms,
                lambda k=r.key, cid=cycle_id, att=tries + 1: self._run_single_retry(k, cid, att),
            )
            self._retry_timer_ids.append(aid)

    def _run_single_retry(self, key: str, cycle_id: int, attempt: int) -> None:
        if self._closing or cycle_id != self._retry_cycle_id:
            return
        tray_common.log_event("INFO", "retry", f"失败设备短重试第 {attempt} 次: {key}")

        def worker() -> None:
            try:
                cfg = tray_common.load_config()
                r = devices.read_single_device(key, cfg)
                hist = battery_history.load_history()
                if r is not None:
                    self.ui_queue.put(("single_retry", cycle_id, attempt, r, hist, cfg))
            except Exception as exc:
                tray_common.log_event("WARN", "retry", f"单设备重试异常 {key}: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _stop_scan_progress_timer(self) -> None:
        if self._scan_progress_aid is not None:
            try:
                self.root.after_cancel(self._scan_progress_aid)
            except Exception:
                pass
            self._scan_progress_aid = None
        try:
            if hasattr(self, "_btn_cancel_scan") and self._btn_cancel_scan.winfo_manager():
                self._btn_cancel_scan.pack_forget()
        except Exception:
            pass

    def _update_scan_progress(self) -> None:
        self._scan_progress_aid = None
        if self._closing or not self.scanning:
            return
        elapsed = max(0, int(time.monotonic() - self._scan_started_at))
        known_n = len(self.readings) or (len(self.cfg.get("known_devices") or {}) if isinstance(self.cfg.get("known_devices"), dict) else 0)
        count_hint = f"（{known_n} 台）" if known_n > 0 else ""
        try:
            self.lbl_summary.configure(
                text=f"{ACTION_REFRESHING} {count_hint}{neu_ui.TEXT_SEP}已用 {elapsed}s".strip(),
                fg=neu_ui.TEXT_MUTED,
            )
            if elapsed >= 8 and hasattr(self, "_btn_cancel_scan") and not self._btn_cancel_scan.winfo_manager():
                self._btn_cancel_scan.pack(side="right", padx=(0, neu_ui.sx(4, self.root)))
        except Exception:
            pass
        self._scan_progress_aid = self.root.after(500, self._update_scan_progress)

    def _cancel_scan(self) -> None:
        if not self.scanning:
            return
        self._scan_token += 1
        self.scanning = False
        self._stop_scan_progress_timer()
        self.btn_refresh.set_loading(False, "立即刷新")
        self.lbl_summary.configure(text="已取消本轮刷新（当前展示上次结果）", fg=neu_ui.TEXT_MUTED)

    def refresh_now(self, *, background: bool = False) -> None:
        if self._closing or self.scanning:
            return
        self.scanning = True
        self._scan_token += 1
        token = self._scan_token
        self._retry_cycle_id += 1
        self._clear_retry_timers()
        if not background:
            self._retry_attempts.clear()
        self._scan_started_at = time.monotonic()
        self._btn_retry.pack_forget()
        self._btn_view_log.pack_forget()
        self.btn_refresh.set_loading(True, "读取中")
        if self._card_widgets:
            self.lbl_summary.configure(text=ACTION_REFRESHING, fg=neu_ui.TEXT_MUTED)
        else:
            self.lbl_summary.configure(text=ACTION_SCANNING, fg=neu_ui.TEXT_MUTED)
        self._stop_scan_progress_timer()
        self._scan_progress_aid = self.root.after(500, self._update_scan_progress)

        def worker() -> None:
            try:
                cfg = tray_common.load_config()
                res, stats = devices.scan_peripherals_with_stats(
                    cfg, include_hidden=self.show_hidden, save_cache=True, background=background
                )
                hist = battery_history.load_history()
                self.ui_queue.put(("readings", token, cfg, res, hist, stats))
                self.request_pump()
            except Exception as e:
                import traceback
                _log_app_error("scan_peripherals", traceback.format_exc())
                self.ui_queue.put(("error", token, str(e)))
                self.request_pump()

        threading.Thread(target=worker, daemon=True).start()

    def apply_readings_snapshot(
        self,
        res: list[devices.PeripheralReading],
        history_data: Optional[dict[str, Any]] = None,
        cfg: Optional[dict[str, Any]] = None,
    ) -> None:
        if cfg is not None:
            self.cfg = cfg
        latest_cfg = tray_common.load_config()
        # 覆盖扫描中途用户实时发起的改名/隐藏/忽略/置顶/类型/排序偏好，保证最新设置不回弹
        names_map = dict(latest_cfg.get("device_names") or {})
        hidden_set = set(latest_cfg.get("hidden_devices") or [])
        ignored_set = set(latest_cfg.get("ignored_devices") or [])
        pinned_set = set(latest_cfg.get("pinned") or [])
        roles_map = dict(latest_cfg.get("device_roles") or {})
        sort_mode = str(latest_cfg.get("sort_mode") or "default")

        for k in (
            "device_names", "hidden_devices", "ignored_devices", "pinned",
            "device_roles", "device_thresholds", "device_capacities_mah",
            "sort_mode", "compact_view", "group_by_role", "collapsed_roles",
            "manual_devices", "tray_icon_style", "tray_visibility", "tray_hover_mode",
            "notify_threshold", "notify_cooldown_min", "notify_enabled", "notify_full_charge",
            "history_retention_days", "update_url", "watchdog_enabled",
            "interval", "bg_interval",
        ):
            if k in latest_cfg:
                self.cfg[k] = latest_cfg[k]
        self.cfg["device_names"] = names_map
        self.cfg["hidden_devices"] = sorted(hidden_set)

        final_res: list[devices.PeripheralReading] = []
        for r in res:
            if r.key in ignored_set:
                continue
            if (not self.show_hidden) and (r.key in hidden_set):
                continue
            if r.key in names_map and names_map[r.key]:
                r.name = str(names_map[r.key])
            if r.key in roles_map and roles_map[r.key] in devices.ROLE_ORDER:
                r.role = str(roles_map[r.key])
            r.pinned = r.key in pinned_set
            final_res.append(r)

        devices.sort_readings(final_res, sort_mode, pinned_set)

        if history_data is not None:
            self.history_data = history_data
        self.readings = final_res
        self._render_cards(final_res)
        self._sync_footer()
        self._apply_pending_focus()

        # 同步常驻托盘与悬停分页概览
        threshold = int(self.cfg.get("notify_threshold", 20))
        dev_ths = dict(self.cfg.get("device_thresholds") or {})
        tray_style = str(self.cfg.get("tray_icon_style", "digit"))
        tray_vis = str(self.cfg.get("tray_visibility", "always"))
        hover_mode = str(self.cfg.get("tray_hover_mode", "grid"))
        if self.tray_host:
            self.tray_host.update_readings(
                final_res,
                threshold=threshold,
                style=tray_style,
                visibility=tray_vis,
                snooze_active=self._low_monitor.has_active_snooze(),
                device_thresholds=dev_ths,
                hover_mode=hover_mode,
            )
        self.hover_tooltip.update_data(final_res, threshold=threshold, device_thresholds=dev_ths)

        tray_common.write_tray_state(
            {
                "pid": os.getpid(),
                "updated_at": time.time(),
                "heartbeat_at": time.time(),
                "devices": [
                    {
                        "key": r.key,
                        "name": r.name,
                        "role": r.role,
                        "level": r.level,
                        "raw_level": r.raw_level,
                        "interpolated": r.interpolated,
                        "voltage_mv": r.voltage_mv,
                        "charging": r.charging,
                        "full": r.full,
                        "offline": r.offline,
                        "status": r.status_text,
                        "estimate": r.estimate_text,
                        "discharge_rate_per_h": r.discharge_rate_per_h,
                        "charge_eta_minutes": r.charge_eta_minutes,
                        "last_read_error": r.last_read_error,
                    }
                    for r in final_res
                ],
            }
        )

        try:
            battery_history.resolve_open_low_alerts(final_res, cfg=self.cfg)
        except Exception:
            pass

        # 低电量通知：支持每设备独立阈值、点击定位、一键暂缓与告警持久化记录
        tiered_alerts = self._low_monitor.check_with_tiers(final_res, self.cfg)
        if tiered_alerts:
            rank = {"normal": 1, "urgent": 2, "critical": 3}
            top_tier = max(tiered_alerts, key=lambda item: rank.get(item[1], 1))[1]
            primary_r = min(tiered_alerts, key=lambda item: 999 if item[0].level is None else int(item[0].level))[0]
            eff_primary_th = tray_common.get_device_threshold(self.cfg, primary_r.key)
            names_part = "、".join(f"{r.name} {r.level_text}" for r, _t in tiered_alerts[:3])
            if len(tiered_alerts) > 3:
                names_part += f" 等 {len(tiered_alerts)} 台"
            if top_tier == "critical":
                title = "紧急低电量警报 (≤5%)"
                body_msg = f"{names_part} 即将耗尽，请立即充电！"
                is_warn = True
            elif top_tier == "urgent":
                title = "低电量警告 (≤10%)"
                body_msg = f"{names_part} 电量极低，请及时连接充电线。"
                is_warn = True
            else:
                title = f"外设低电量提醒 (<{eff_primary_th}%)"
                body_msg = f"{names_part} 当前电量偏低（点击通知可定位，可在卡片暂缓）。"
                is_warn = False

            for ar, atier in tiered_alerts:
                try:
                    battery_history.record_alert_event(
                        key=ar.key,
                        name=ar.name,
                        kind="low",
                        tier=atier,
                        level=ar.level,
                        threshold=tray_common.get_device_threshold(self.cfg, ar.key),
                    )
                except Exception:
                    pass

            if self.tray_host:
                self.tray_host.show_notification(title, body_msg, warning=is_warn, device_key=primary_r.key)
            else:
                tray_common.log_event("INFO", "notify", f"[{title}] {body_msg}")
            if getattr(self, "low_toast", None) is not None and not self._closing:
                try:
                    alpha_val = max(0.1, min(1.0, float(self.cfg.get("notify_alpha", 60)) / 100.0))
                    self.low_toast.show(
                        [r for r, _t in tiered_alerts],
                        auto_close_ms=8000,
                        alpha=alpha_val,
                        threshold=threshold,
                        device_thresholds=dev_ths,
                    )
                except Exception:
                    pass

        # 充满电完成提醒 (#1)
        full_alerts = self._low_monitor.check_full_charges(final_res, self.cfg)
        if full_alerts:
            full_names = "、".join(r.name for r in full_alerts[:3])
            if len(full_alerts) > 3:
                full_names += f" 等 {len(full_alerts)} 台"
            for fr in full_alerts:
                try:
                    battery_history.record_alert_event(
                        key=fr.key,
                        name=fr.name,
                        kind="full",
                        tier="full",
                        level=fr.level,
                        threshold=100,
                    )
                except Exception:
                    pass
            f_title = "外设已充满电 (100%)"
            f_msg = f"{full_names} 已充满，可随时拔下充电线。"
            if self.tray_host:
                self.tray_host.show_notification(f_title, f_msg, warning=False, device_key=full_alerts[0].key)
            else:
                tray_common.log_event("INFO", "notify", f"[{f_title}] {f_msg}")
            if (not tiered_alerts) and getattr(self, "low_toast", None) is not None and not self._closing and not self._disable_tray_host:
                try:
                    alpha_val = max(0.1, min(1.0, float(self.cfg.get("notify_alpha", 60)) / 100.0))
                    self.low_toast.show_notice(
                        f_title, f_msg, auto_close_ms=5000, alpha=alpha_val, device_key=full_alerts[0].key
                    )
                except Exception:
                    pass

    def _pump_queues(self) -> None:
        if self._closing:
            return
        handled = 0
        try:
            try:
                while True:
                    item = self.ui_queue.get_nowait()
                    handled += 1
                    try:
                        kind = item[0]
                        if kind == "readings":
                            # 兼容旧队列格式 (4-tuple / 5-tuple) 与带 ScanStats 新格式 (6-tuple)
                            if len(item) >= 6:
                                _, tok, cfg, res, hist, stats = item[:6]
                                self._last_scan_stats = stats
                                if tok != self._scan_token:
                                    continue
                            elif len(item) == 5:
                                _, tok, cfg, res, hist = item
                                if tok != self._scan_token:
                                    continue
                            else:
                                _, cfg, res, hist = item
                            self.scanning = False
                            self._stop_scan_progress_timer()
                            self.btn_refresh.set_loading(False, "立即刷新")
                            self._btn_retry.pack_forget()
                            self._btn_view_log.pack_forget()
                            self.lbl_summary.configure(fg=neu_ui.TEXT_MUTED)
                            self.apply_readings_snapshot(res, hist, cfg)
                            self._schedule_failed_retries(self.readings, self._retry_cycle_id)
                        elif kind == "single_retry":
                            _, cid, attempt, ret_r, hist, cfg = item
                            if cid != self._retry_cycle_id:
                                continue
                            self.history_data = hist
                            updated = False
                            new_list: list[devices.PeripheralReading] = []
                            for cur_r in self.readings:
                                if cur_r.key == ret_r.key:
                                    if ret_r.level is not None or attempt >= 2:
                                        new_list.append(ret_r)
                                    else:
                                        cur_r.last_read_error = ret_r.last_read_error or ret_r.error
                                        new_list.append(cur_r)
                                    updated = True
                                else:
                                    new_list.append(cur_r)
                            if updated:
                                self.apply_readings_snapshot(new_list, hist, cfg)
                                still_fail = (not ret_r.offline) and (not ret_r.asleep) and (ret_r.level is None)
                                if still_fail and attempt < 2:
                                    self._schedule_failed_retries([ret_r], cid)
                        elif kind == "error":
                            if len(item) == 3:
                                _, tok, err_raw = item
                                if tok != self._scan_token:
                                    continue
                            else:
                                _, err_raw = item
                            self.scanning = False
                            self._stop_scan_progress_timer()
                            self.btn_refresh.set_loading(False, "立即刷新")
                            friendly = _friendly_error_message(str(err_raw))
                            self.lbl_summary.configure(text=f"读取失败：{friendly}", fg=neu_ui.RED_DANGER)
                            self._lbl_hidden_hint.pack_forget()
                            self._btn_retry.pack(side="left")
                            self._btn_view_log.pack(side="left", padx=(neu_ui.sx(4, self.root), 0))
                            # 将缓存中尚在显示的卡片标示旧值时间戳并淡化饱和度，避免误读
                            for card in self._card_widgets:
                                card.set_stale_error(True, self._last_sync_ts_str)
                        elif kind == "unhandled_error":
                            payload = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
                            summary = str(payload.get("summary") or (item[1] if len(item) > 1 else "未知错误"))
                            self._show_warning_banner(
                                f"后台检测到异常：{summary}（详情已记录至运行日志）",
                                key=f"exc:{summary}",
                                warn_level="error",
                            )
                    except Exception:
                        import traceback
                        _log_app_error("_pump_queues.ui_item", traceback.format_exc())
            except queue.Empty:
                pass

            try:
                while True:
                    ev, payload = self.tray_events.get_nowait()
                    handled += 1
                    try:
                        if ev == "hover_icon":
                            if neu_ui.has_active_popup_menu():
                                continue
                            if str(self.cfg.get("tray_hover_mode", "grid")) == "native":
                                continue
                            threshold = int(self.cfg.get("notify_threshold", 20))
                            self.hover_tooltip.update_data(
                                self.readings,
                                threshold=threshold,
                                device_thresholds=self.cfg.get("device_thresholds") or {},
                            )
                            self.hover_tooltip.show_near_rect(payload)
                        elif ev == "hide_tooltip":
                            self.hover_tooltip.hide()
                        elif ev == "tray_menu":
                            self.hover_tooltip.hide()
                            self._show_tray_context_menu(payload)
                        elif ev == "open_main":
                            self.show_main_window()
                        elif ev == "open_last_device":
                            target_key = self._last_device_key or (self.readings[0].key if self.readings else None)
                            if target_key:
                                self.focus_device(target_key)
                            else:
                                self.show_main_window()
                        elif ev == "toggle_main":
                            self.toggle_main_window()
                        elif ev == "open_device":
                            self.focus_device(str(payload))
                        elif ev == "open_settings":
                            self.show_main_window()
                            self.open_settings_modal()
                        elif ev == "toggle_autostart":
                            self._toggle_autostart_setting()
                        elif ev == "toggle_hidden":
                            self.show_hidden = not self.show_hidden
                            self.cfg = tray_common.load_config()
                            self.cfg["show_hidden"] = self.show_hidden
                            tray_common.save_config(self.cfg)
                            if hasattr(self, "_var_hidden"):
                                try:
                                    self._var_hidden.set(self.show_hidden)
                                except Exception:
                                    pass
                            self.refresh_now()
                        elif ev == "toggle_notify":
                            self._toggle_notify_setting()
                        elif ev == "refresh" or ev == "device_change":
                            self.refresh_now(background=(self._is_window_hidden() and ev == "device_change"))
                        elif ev == "toggle_compact":
                            self._toggle_compact_view()
                        elif ev == "snooze_all":
                            self._snooze_all_devices(int(payload or 30))
                        elif ev == "open_debug":
                            self.show_main_window()
                            self.open_debug_tool(None)
                        elif ev == "quit_app":
                            self.quit_app()
                            return
                    except Exception:
                        import traceback
                        _log_app_error("_pump_queues.tray_event", traceback.format_exc())
            except queue.Empty:
                pass

            while True:
                io_warn = tray_common.pop_io_warning()
                if not io_warn:
                    break
                handled += 1
                self._show_warning_banner(
                    str(io_warn.get("message", "")),
                    key=f"io:{io_warn.get('kind', '')}",
                    warn_level="warn" if io_warn.get("recovered") else "error",
                )
        except Exception:
            import traceback
            _log_app_error("_pump_queues", traceback.format_exc())
        finally:
            if not self._closing:
                try:
                    is_hidden = str(self.root.state()) in ("withdrawn", "iconic")
                except Exception:
                    is_hidden = False
                if handled > 0 or self.scanning:
                    self._pump_idle_ticks = 0
                    interval_ms = 100
                else:
                    self._pump_idle_ticks = getattr(self, "_pump_idle_ticks", 0) + 1
                    if is_hidden:
                        interval_ms = 800
                    else:
                        interval_ms = 600 if self._pump_idle_ticks >= 5 else 100
                self._schedule_pump(interval_ms)

    def _toggle_notify_setting(self) -> None:
        c = tray_common.load_config()
        c["notify_enabled"] = not bool(c.get("notify_enabled", True))
        tray_common.save_config(c)
        self.cfg = c
        self._sync_footer()
        if hasattr(self, "_var_low_notify"):
            try:
                self._var_low_notify.set(bool(c["notify_enabled"]))
            except Exception:
                pass

    def _toggle_autostart_setting(self) -> None:
        cur = tray_common.is_autostart_enabled()
        ok, msg = tray_common.set_autostart(not cur)
        if hasattr(self, "_var_autostart"):
            try:
                self._var_autostart.set(ok)
            except Exception:
                pass
        self._set_modal_note(msg, neu_ui.SUCCESS if ok != cur else neu_ui.RED_DANGER)

    def _snooze_all_devices(self, mins: int = 30) -> None:
        self._low_monitor.snooze_global(minutes=mins)
        if self.tray_host:
            self.tray_host.update_readings(
                self.readings,
                threshold=int(self.cfg.get("notify_threshold", 20)),
                snooze_active=True,
            )
        tray_common.log_event("INFO", "notify", f"已在托盘菜单开启暂缓低电提醒 {mins} 分钟")

    def _show_tray_context_menu(self, pos: Optional[tuple[int, int]] = None) -> neu_ui.NeuPopupMenu:
        """响应托盘图标右键点击，弹出与主界面完全一致的新拟物圆角快捷菜单。"""
        cfg = tray_common.load_config()
        self.cfg = cfg
        notify_on = bool(cfg.get("notify_enabled", True))
        threshold = int(cfg.get("notify_threshold", 20))
        compact_on = bool(cfg.get("compact_view", False))
        autostart_on = tray_common.is_autostart_enabled()
        snooze_on = self._low_monitor.has_active_snooze()

        items: list[dict[str, Any]] = []
        if self.readings:
            dev_sub = []
            for r in self.readings[:8]:
                lvl_txt = "离线" if (r.offline or r.level is None) else r.level_text
                dot_c = (
                    neu_ui.GRAY_DISABLED
                    if (r.offline or r.level is None)
                    else devices.battery_color_hex(
                        r.level,
                        low_threshold=threshold,
                        asleep=r.asleep,
                        stale=r.stale,
                        charging=r.charging,
                        offline=r.offline,
                        full=r.full,
                    )
                )
                dev_sub.append({
                    "type": "item",
                    "label": f"{r.name}  {lvl_txt}",
                    "dot_color": dot_c,
                    "command": lambda k=r.key: self.focus_device(k),
                })
            items.append({"type": "submenu", "label": "查看外设", "items": dev_sub})
            items.append({"type": "separator"})

        items.extend([
            {"type": "item", "label": "打开主界面", "command": self.show_main_window},
            {"type": "item", "label": "立即刷新电量", "command": self.refresh_now},
            {"type": "item", "label": "紧凑卡片视图", "checked": compact_on, "command": self._toggle_compact_view},
            {"type": "item", "label": f"低电量系统通知 (<{threshold}%)", "checked": notify_on, "command": self._toggle_notify_setting},
            {"type": "item", "label": "暂缓低电提醒 30 分钟", "checked": snooze_on, "command": lambda: self._snooze_all_devices(30)},
            {"type": "item", "label": "开机自启", "checked": autostart_on, "command": self._toggle_autostart_setting},
            {"type": "separator"},
            {"type": "item", "label": "设置…", "command": lambda: (self.show_main_window(), self.open_settings_modal())},
            {"type": "item", "label": "调试与协议标定", "command": lambda: (self.show_main_window(), self.open_debug_tool(None))},
            {"type": "separator"},
            {"type": "item", "label": "退出软件", "danger": True, "command": self.quit_app},
        ])

        cx, cy = (pos if (isinstance(pos, (tuple, list)) and len(pos) == 2) else tray_service.get_cursor_pos())
        if cx < -4000 or cy < -4000:
            il, it, ir, _ib = self.hover_tooltip._get_icon_rect()
            cx, cy = (il + ir) // 2, it
        menu = neu_ui.NeuPopupMenu(
            self.root,
            items,
            min_width_dp=188,
            topmost=True,
            anchor_bottom=True,
            on_close=lambda: setattr(self, "_tray_menu", None) if self.root.winfo_exists() else None,
        )
        self._tray_menu = menu
        menu.open_at(int(cx), int(cy))
        return menu

    def _render_cards(self, res: list[devices.PeripheralReading]) -> None:
        ok_cnt = sum(1 for r in res if (not r.offline) and r.level is not None)
        off_cnt = sum(1 for r in res if r.offline)
        self._last_sync_epoch = time.time()
        self._last_sync_ts_str = time.strftime("%H:%M")
        ts = time.strftime("%m-%d %H:%M:%S")
        off_note = f"{neu_ui.TEXT_SEP}{off_cnt} 台已离线" if off_cnt else ""
        self._btn_retry.pack_forget()
        self._btn_view_log.pack_forget()
        self.lbl_summary.configure(
            text=f"已监测 {len(res)} 台外设（{ok_cnt} 台在线{off_note}）{neu_ui.TEXT_SEP}更新于 {ts} (刚刚)",
            fg=neu_ui.TEXT_MUTED,
        )

        hidden_set = set(self.cfg.get("hidden_devices") or [])
        threshold = int(self.cfg.get("notify_threshold", 20))
        dev_ths = dict(self.cfg.get("device_thresholds") or {})
        compact_on = bool(self.cfg.get("compact_view", False))
        group_on = bool(self.cfg.get("group_by_role", False))
        self._update_filter_bar_counts(res)
        vis_res = filter_readings(res, query=self._search_query, status_filter=self._filter_mode, cfg=self.cfg)
        if hidden_set and not self.show_hidden:
            self._lbl_hidden_hint.configure(text=f"{neu_ui.TEXT_SEP.strip()} 已隐藏 {len(hidden_set)} 台 · 查看 →")
            self._lbl_hidden_hint.pack(side="left", padx=(neu_ui.sx(6, self.root), 0))
        else:
            self._lbl_hidden_hint.pack_forget()

        st = self._last_scan_stats or devices.get_last_scan_stats()
        skipped_n = int(getattr(st, "skipped_count", 0) or 0)
        if skipped_n > 0:
            self._lbl_skipped_hint.configure(text=f"{neu_ui.TEXT_SEP.strip()} 另有 {skipped_n} 台未识别，去标定 →")
            self._lbl_skipped_hint.pack(side="left", padx=(neu_ui.sx(6, self.root), 0))
        else:
            self._lbl_skipped_hint.pack_forget()

        gap = neu_ui.sx(CARD_GAP, self.root)
        if res and not vis_res:
            for w in self.cards_frame.winfo_children():
                w.destroy()
            self._card_widgets.clear()
            self._group_headers.clear()
            for c in range(4):
                self.cards_frame.columnconfigure(c, weight=0, uniform="")
            self.cards_frame.columnconfigure(0, weight=1, uniform="")
            empty_f = neu_ui.NeuCard(
                self.cards_frame, radius=24, pad=CARD_PAD, inner_pad=(24, 20), off=7, box_r=4
            )
            empty_f.grid(row=0, column=0, sticky="ew", padx=gap, pady=(neu_ui.sx(8, self.root), gap))
            tk.Label(
                empty_f.content,
                text="没有匹配当前搜索或筛选条件的外设",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT,
                font=neu_ui.ui_font(neu_ui.FONT_SUBTITLE, "bold", root=self.root),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                empty_f.content,
                text=f"已共监测 {len(res)} 台外设，可清空关键字或切回「全部」查看。",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_BODY, root=self.root),
                anchor="w",
            ).pack(fill="x", pady=(neu_ui.sx(4, self.root), neu_ui.sx(10, self.root)))
            neu_ui.NeuButton(
                empty_f.content,
                text="清除筛选并显示全部",
                command=self._clear_search,
                accent=True,
                radius=14,
                pad=8,
                padx=14,
                pady=5,
                font_size=neu_ui.FONT_BODY,
            ).pack(anchor="w")
            return

        if not res:
            for w in self.cards_frame.winfo_children():
                w.destroy()
            self._card_widgets.clear()
            self._group_headers.clear()
            for c in range(4):
                self.cards_frame.columnconfigure(c, weight=0, uniform="")
            self.cards_frame.columnconfigure(0, weight=1, uniform="")
            empty = neu_ui.NeuCard(
                self.cards_frame, radius=24, pad=CARD_PAD, inner_pad=(24, 20), off=7, box_r=4
            )
            empty.grid(row=0, column=0, sticky="ew", padx=gap, pady=(neu_ui.sx(8, self.root), gap))

            e_head = tk.Frame(empty.content, bg=neu_ui.BASE)
            e_head.pack(fill="x", pady=(0, neu_ui.sx(8, self.root)))
            neu_ui.NeuRoleBadge(
                e_head, "controller", size=neu_ui.sx(28, self.root), color=neu_ui.INFO, inset=True
            ).pack(side="left", padx=(0, neu_ui.sx(10, self.root)))
            e_txt = tk.Frame(e_head, bg=neu_ui.BASE)
            e_txt.pack(side="left", fill="x", expand=True)
            tk.Label(
                e_txt,
                text="暂未检测到可识别的 2.4G / 蓝牙无线外设",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT,
                font=neu_ui.ui_font(neu_ui.FONT_SUBTITLE, "bold", root=self.root),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                e_txt,
                text="请确认 2.4G 接收器已插上且外设处于唤醒状态；若是新品牌外设，可打开调试工具自行探测协议。",
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_BODY, root=self.root),
                anchor="w",
                wraplength=neu_ui.sx(560, self.root),
                justify="left",
            ).pack(fill="x", pady=(4, 0))

            e_btns = tk.Frame(empty.content, bg=neu_ui.BASE)
            e_btns.pack(fill="x", pady=(neu_ui.sx(8, self.root), 0))
            neu_ui.NeuButton(
                e_btns,
                text="立即重新扫描",
                command=self.refresh_now,
                accent=True,
                weight="bold",
                radius=14,
                pad=8,
                padx=14,
                pady=5,
                font_size=neu_ui.FONT_BODY,
            ).pack(side="left", padx=(0, neu_ui.sx(8, self.root)))
            neu_ui.NeuButton(
                e_btns,
                text="打开调试与标定 →",
                command=lambda: self.open_debug_tool(None),
                radius=14,
                pad=8,
                padx=14,
                pady=5,
                font_size=neu_ui.FONT_BODY,
                fg=neu_ui.TEXT,
            ).pack(side="left")
            if hidden_set and not self.show_hidden:
                neu_ui.NeuButton(
                    e_btns,
                    text=f"显示已隐藏设备 ({len(hidden_set)})",
                    command=self._reveal_hidden_from_hint,
                    radius=14,
                    pad=8,
                    padx=12,
                    pady=5,
                    font_size=neu_ui.FONT_CAPTION,
                    fg=neu_ui.BLURPLE_LINK,
                ).pack(side="right")
            return

        # 差量原地更新：顺序、设备键、视图模式、分组模式与卡片结构一致时不销毁控件，消除闪烁与滚动条回跳
        can_patch = (
            (not group_on)
            and (not self._group_headers)
            and len(self._card_widgets) == len(vis_res)
            and all(
                c.reading.key == r.key and (not c.needs_rebuild(r, r.key in hidden_set, compact=compact_on))
                for c, r in zip(self._card_widgets, vis_res)
            )
        )
        if can_patch:
            for card, r in zip(self._card_widgets, vis_res):
                eff_th = tray_common.get_device_threshold(self.cfg, r.key)
                cust_th = dev_ths.get(r.key)
                card.update_reading(
                    r,
                    self.history_data,
                    is_hidden=(r.key in hidden_set),
                    spark_hours=self._spark_windows.get(r.key, 24),
                    low_threshold=eff_th,
                    custom_threshold=int(cust_th) if cust_th is not None else None,
                    global_threshold=threshold,
                )
            return

        try:
            scroll_top = float(self.canvas.yview()[0])
        except Exception:
            scroll_top = 0.0

        for w in self.cards_frame.winfo_children():
            w.destroy()
        self._card_widgets.clear()
        self._group_headers.clear()

        if group_on:
            collapsed = set(self.cfg.get("collapsed_roles") or [])
            by_role: dict[str, list[devices.PeripheralReading]] = {}
            for r in vis_res:
                by_role.setdefault(r.role, []).append(r)
            ordered_roles = sorted(by_role.keys(), key=lambda rk: devices.ROLE_ORDER.get(rk, 99))
            for rk in ordered_roles:
                grp_items = by_role[rk]
                is_col = rk in collapsed
                arrow = "▸" if is_col else "▾"
                hdr = neu_ui.NeuButton(
                    self.cards_frame,
                    text=f"{arrow}  {devices.ROLE_LABEL.get(rk, '其他')}（{len(grp_items)} 台）",
                    command=lambda rkey=rk: self._toggle_role_collapse(rkey),
                    radius=12,
                    pad=4,
                    padx=12,
                    pady=4,
                    font_size=neu_ui.FONT_CAPTION,
                    fg=neu_ui.TEXT_MUTED,
                    tooltip="点击折叠 / 展开该类型外设",
                )
                hdr._group_role = rk  # type: ignore[attr-defined]
                self._group_headers.append(hdr)
                for r in grp_items:
                    eff_th = tray_common.get_device_threshold(self.cfg, r.key)
                    cust_th = dev_ths.get(r.key)
                    card = DeviceCard(
                        self.cards_frame,
                        r,
                        self.history_data,
                        on_rename=self._rename_device,
                        on_toggle_hide=self._toggle_hide_device,
                        on_open_debug=self.open_debug_tool,
                        is_hidden=(r.key in hidden_set),
                        spark_hours=self._spark_windows.get(r.key, 24),
                        on_spark_hours_change=self._remember_spark_hours,
                        low_threshold=eff_th,
                        custom_threshold=int(cust_th) if cust_th is not None else None,
                        global_threshold=threshold,
                        compact=compact_on,
                        on_pin=self._pin_device,
                        on_set_role=self._set_device_role,
                        on_forget=self._forget_device,
                        on_snooze=self._snooze_device,
                        on_why_unread=self.open_why_unread_wizard,
                        on_open_history=self.open_history_modal,
                        on_set_threshold=self._set_device_threshold,
                        on_set_capacity=self._set_device_capacity,
                    )
                    self._card_widgets.append(card)
        else:
            for r in vis_res:
                eff_th = tray_common.get_device_threshold(self.cfg, r.key)
                cust_th = dev_ths.get(r.key)
                card = DeviceCard(
                    self.cards_frame,
                    r,
                    self.history_data,
                    on_rename=self._rename_device,
                    on_toggle_hide=self._toggle_hide_device,
                    on_open_debug=self.open_debug_tool,
                    is_hidden=(r.key in hidden_set),
                    spark_hours=self._spark_windows.get(r.key, 24),
                    on_spark_hours_change=self._remember_spark_hours,
                    low_threshold=eff_th,
                    custom_threshold=int(cust_th) if cust_th is not None else None,
                    global_threshold=threshold,
                    compact=compact_on,
                    on_pin=self._pin_device,
                    on_set_role=self._set_device_role,
                    on_forget=self._forget_device,
                    on_snooze=self._snooze_device,
                    on_why_unread=self.open_why_unread_wizard,
                    on_open_history=self.open_history_modal,
                    on_set_threshold=self._set_device_threshold,
                    on_set_capacity=self._set_device_capacity,
                )
                self._card_widgets.append(card)
        self._layout_card_grid()
        if scroll_top > 0.0:
            self.root.after_idle(
                lambda y=scroll_top: self.canvas.yview_moveto(y) if (not self._closing and self.canvas.winfo_exists()) else None
            )

    def _remember_spark_hours(self, key: str, hours: int) -> None:
        self._spark_windows[key] = hours

    def _schedule_pump(self, delay_ms: int = 100) -> None:
        if self._closing:
            return
        try:
            if not self.root.winfo_exists():
                return
        except Exception:
            return
        if self._pump_aid is not None:
            try:
                self.root.after_cancel(self._pump_aid)
            except Exception:
                pass
            self._pump_aid = None
        try:
            self._pump_aid = self.root.after(max(10, int(delay_ms)), self._pump_queues)
        except Exception:
            self._pump_aid = None

    def request_pump(self) -> None:
        """Thread-safe wake request so event pump drains queues promptly when active."""
        if self._closing:
            return
        self._pump_idle_ticks = 0
        try:
            if threading.get_ident() == getattr(self, "_ui_thread_id", threading.get_ident()):
                self._schedule_pump(10)
            elif self.root is not None:
                self.root.event_generate("<<PBVWakePump>>", when="tail")
        except Exception:
            pass

    def _show_warning_banner(self, message: str, *, key: str = "", warn_level: str = "error") -> None:
        if self._closing or not hasattr(self, "_warn_banner_wrap"):
            return
        now_m = time.monotonic()
        eff_key = key or message
        if eff_key == getattr(self, "_warn_banner_last_key", "") and (now_m - getattr(self, "_warn_banner_last_ts", 0.0)) < 5.0:
            return
        self._warn_banner_last_key = eff_key
        self._warn_banner_last_ts = now_m
        col = neu_ui.AMBER_CHARGE if warn_level == "warn" else neu_ui.RED_DANGER
        try:
            self._warn_banner_lbl.configure(text=message, fg=col)
            if not getattr(self, "_warn_banner_visible", False):
                self._warn_banner_wrap.pack(fill="x", before=self._wrap_outer)
                self._warn_banner_visible = True
        except Exception:
            pass

    def _dismiss_warning_banner(self) -> None:
        if not hasattr(self, "_warn_banner_wrap"):
            return
        try:
            if getattr(self, "_warn_banner_visible", False):
                self._warn_banner_wrap.pack_forget()
                self._warn_banner_visible = False
        except Exception:
            pass

    def open_about_modal(self) -> None:
        if self._about_modal is not None:
            return
        prev_settings = self._settings_modal
        reuse_dim = getattr(prev_settings, "_dim_photo", None) if prev_settings is not None else None
        reuse_stages = list(getattr(prev_settings, "_dim_stages", []) or []) if prev_settings is not None else None
        opened_from_settings = prev_settings is not None

        def _on_closed() -> None:
            self._about_modal = None
            if (
                opened_from_settings
                and not self._closing
                and not getattr(self, "_suppress_settings_return", False)
                and self.root.winfo_exists()
            ):
                self.open_settings_modal(dim_photo=reuse_dim, dim_stages=reuse_stages)

        modal = neu_ui.NeuModalOverlay(
            self.root,
            title=f"关于 {tray_common.APP_DISPLAY_NAME}",
            subtitle=f"版本 v{tray_common.APP_VERSION} · 构建日期 {tray_common.APP_BUILD_DATE}",
            max_width_dp=590,
            min_height_dp=420,
            dim_photo=reuse_dim,
            dim_stages=reuse_stages,
            fade_scrim_on_close=not opened_from_settings,
            on_close=_on_closed,
            close_on_backdrop=True,
        )
        self._about_modal = modal

        card = neu_ui.NeuCard(modal.body, radius=16, pad=0, inner_pad=(16, 14), off=4, box_r=3, inset=True)
        card.pack(fill="x", pady=(0, neu_ui.sx(10, modal.body)))

        mode_tag = "便携模式" if tray_common.is_portable_mode() else "标准安装模式 (%APPDATA%)"
        info_lines = [
            f"软件名称：{tray_common.APP_DISPLAY_NAME} ({tray_common.APP_NAME})",
            f"当前版本：v{tray_common.APP_VERSION}（构建 {tray_common.APP_BUILD_DATE}）",
            f"运行环境：Python {sys.version.split()[0]} · Pure tkinter + ctypes（零第三方依赖）",
            "支持通道：HID 2.4G / 蓝牙 HFP PnP / BLE GATT / Sony DualSense / Razer / SteelSeries",
            f"数据目录：{tray_common.get_data_dir()}（{mode_tag}）",
        ]
        for idx, line in enumerate(info_lines):
            tk.Label(
                card.content,
                text=line,
                bg=neu_ui.BASE,
                fg=neu_ui.TEXT if idx <= 1 else neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_BODY if idx <= 1 else neu_ui.FONT_CAPTION, "bold" if idx == 0 else "normal", root=modal.body),
                anchor="w",
                justify="left",
                wraplength=neu_ui.sx(520, modal.body),
            ).pack(fill="x", pady=(0, neu_ui.sx(4, modal.body)))

        # 在线更新源配置（支持 GitHub Releases API 或自定义 manifest JSON）
        url_row = tk.Frame(modal.body, bg=neu_ui.BASE)
        url_row.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))
        tk.Label(
            url_row, text="更新源 URL：", bg=neu_ui.BASE, fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=modal.body),
        ).pack(side="left")
        cur_url = str(self.cfg.get("update_url") or "")
        url_var = tk.StringVar(value=cur_url)
        btn_save_url = neu_ui.NeuButton(
            url_row, text="保存源", command=lambda: (_save_url(), upd_var.set("已保存更新源配置")),
            radius=12, pad=4, padx=9, pady=3, font_size=neu_ui.FONT_CAPTION,
        )
        btn_save_url.pack(side="right")
        url_entry = neu_ui.NeuEntry(
            url_row, textvariable=url_var, width=18,
            font_tuple=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=modal.body),
        )
        url_entry.pack(side="left", fill="x", expand=True, padx=(neu_ui.sx(6, modal.body), neu_ui.sx(6, modal.body)))
        neu_ui.attach_tooltip(url_entry, "填写 GitHub Releases API (/releases/latest) 或包含 version/download_url/sha256 的 JSON 地址")

        def _save_url() -> str:
            u = url_var.get().strip()
            c = tray_common.load_config()
            c["update_url"] = u
            tray_common.save_config(c)
            self.cfg = c
            return u

        init_status = (
            {
                "ok": True,
                "status": "ready",
                "current_version": tray_common.APP_VERSION,
                "latest_version": tray_common.APP_VERSION,
                "update_available": False,
                "message": f"当前版本 v{tray_common.APP_VERSION} · 点击「检查更新」查询最新版本",
            }
            if cur_url
            else tray_common.check_update_status()
        )
        upd_var = tk.StringVar(value=init_status.get("message", ""))
        lbl_upd = tk.Label(
            modal.body,
            textvariable=upd_var,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
            justify="left",
            wraplength=neu_ui.sx(540, modal.body),
        )
        lbl_upd.pack(fill="x", pady=(0, neu_ui.sx(6, modal.body)))

        notes_var = tk.StringVar(value="")
        lbl_notes = tk.Label(
            modal.body,
            textvariable=notes_var,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_DIM,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
            justify="left",
            wraplength=neu_ui.sx(540, modal.body),
        )
        lbl_notes.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))

        latest_res: dict[str, Any] = dict(init_status)
        btn_row = tk.Frame(modal.body, bg=neu_ui.BASE)
        btn_row.pack(fill="x")

        btn_install_upd: Optional[neu_ui.NeuButton] = None

        def _refresh_upd_actions() -> None:
            if btn_install_upd is None:
                return
            avail = bool(latest_res.get("update_available"))
            dl_url = str(latest_res.get("download_url") or "")
            sha = str(latest_res.get("sha256") or "")
            html_url = str(latest_res.get("html_url") or "")
            if avail and dl_url and len(sha) == 64:
                btn_install_upd.configure(text="下载并安装更新")
                if not btn_install_upd.winfo_manager():
                    btn_install_upd.pack(side="left", padx=(0, neu_ui.sx(8, modal.body)))
            elif avail and (dl_url or html_url):
                btn_install_upd.configure(text="去发布页下载")
                if not btn_install_upd.winfo_manager():
                    btn_install_upd.pack(side="left", padx=(0, neu_ui.sx(8, modal.body)))
            else:
                if btn_install_upd.winfo_manager():
                    btn_install_upd.pack_forget()

        def _do_check_update() -> None:
            saved_url = _save_url()
            upd_var.set("正在检查版本状态…")
            notes_var.set("")
            lbl_upd.configure(fg=neu_ui.TEXT_MUTED)
            def _bg() -> None:
                res_upd = tray_common.check_update_status(update_url=saved_url if saved_url else None)
                def _apply() -> None:
                    if not modal.winfo_exists():
                        return
                    latest_res.clear()
                    latest_res.update(res_upd)
                    msg = str(res_upd.get("message", ""))
                    if res_upd.get("update_available") and res_upd.get("download_url") and not res_upd.get("sha256"):
                        msg += "（清单缺少 SHA-256 校验值，为安全起见仅支持浏览器手动打开）"
                    upd_var.set(msg)
                    notes_s = str(res_upd.get("notes") or "").strip()
                    notes_var.set(f"更新说明：{notes_s[:600]}" if notes_s else "")
                    if res_upd.get("update_available"):
                        lbl_upd.configure(fg=neu_ui.SUCCESS)
                    elif not res_upd.get("ok"):
                        lbl_upd.configure(fg=neu_ui.RED_DANGER)
                    else:
                        lbl_upd.configure(fg=neu_ui.TEXT_MUTED)
                    _refresh_upd_actions()
                    try:
                        modal.update_idletasks()
                        modal._relayout()
                    except Exception:
                        pass
                try:
                    self.root.after(0, _apply)
                except Exception:
                    pass
            threading.Thread(target=_bg, daemon=True).start()

        def _do_install_update() -> None:
            dl_url = str(latest_res.get("download_url") or "")
            sha = str(latest_res.get("sha256") or "")
            html_url = str(latest_res.get("html_url") or "")
            if not (dl_url and len(sha) == 64):
                target_web = html_url or dl_url
                if target_web:
                    webbrowser.open(target_web)
                return
            upd_var.set("正在下载更新包并校验 SHA-256…")
            def _prog(done: int, total: int) -> None:
                pct = f" ({min(100, int(done * 100 / total))}%)" if total > 0 else f" ({done // 1024} KB)"
                try:
                    self.root.after(0, lambda p=pct: upd_var.set(f"正在下载更新包{p}…") if modal.winfo_exists() else None)
                except Exception:
                    pass
            def _dl_bg() -> None:
                tmp_dst = os.path.join(tempfile.gettempdir(), f"pbv_update_{os.getpid()}.exe")
                res_dl = tray_common.download_and_verify_update(dl_url, sha, tmp_dst, progress_cb=_prog)
                def _after_dl() -> None:
                    if not modal.winfo_exists():
                        return
                    if not res_dl.get("ok"):
                        lbl_upd.configure(fg=neu_ui.RED_DANGER)
                        upd_var.set(str(res_dl.get("message") or "下载校验失败"))
                        return
                    lbl_upd.configure(fg=neu_ui.SUCCESS)
                    if not tray_common.IS_FROZEN:
                        upd_var.set(f"校验通过并已下载至：{res_dl['path']}（源码运行模式不自替换脚本）")
                        return
                    ok_up, msg_up = tray_common.prepare_and_launch_updater(res_dl["path"])
                    if ok_up:
                        modal.close()
                        self.quit_app()
                    else:
                        lbl_upd.configure(fg=neu_ui.RED_DANGER)
                        upd_var.set(msg_up)
                try:
                    self.root.after(0, _after_dl)
                except Exception:
                    pass
            threading.Thread(target=_dl_bg, daemon=True).start()

        def _copy_version() -> None:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(
                    f"{tray_common.APP_NAME} v{tray_common.APP_VERSION} ({tray_common.APP_BUILD_DATE}) | Python {sys.version.split()[0]}"
                )
                upd_var.set("已复制版本信息到剪贴板")
            except Exception:
                pass

        neu_ui.NeuButton(
            btn_row,
            text="检查更新",
            command=_do_check_update,
            accent=True,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left", padx=(0, neu_ui.sx(8, modal.body)))
        btn_install_upd = neu_ui.NeuButton(
            btn_row,
            text="下载并安装更新",
            command=_do_install_update,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.SUCCESS,
        )
        neu_ui.NeuButton(
            btn_row,
            text="复制版本信息",
            command=_copy_version,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left")
        neu_ui.NeuButton(
            btn_row,
            text="关闭",
            command=modal.close,
            radius=14,
            pad=6,
            padx=14,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
        ).pack(side="right")
        _refresh_upd_actions()

    def open_history_modal(self, reading: devices.PeripheralReading) -> None:
        if self._history_modal is not None:
            try:
                self._history_modal.close()
            except Exception:
                pass
            self._history_modal = None

        ret_days = tray_common.get_retention_days(self.cfg)
        cap_mah, cap_custom = tray_common.get_device_capacity_mah(self.cfg, reading.key, reading.role)
        modal = neu_ui.NeuModalOverlay(
            self.root,
            title=f"{reading.name} · 电量历史与能耗分析",
            subtitle=f"{reading.vidpid_text} · 通道：{reading.connection or reading.transport} · 自动保留最近 {ret_days} 天采样",
            max_width_dp=780,
            min_height_dp=530,
            on_close=lambda: setattr(self, "_history_modal", None),
            close_on_backdrop=True,
        )
        self._history_modal = modal

        init_hrs = min(float(self._spark_windows.get(reading.key, 24) or 24), float(ret_days * 24))
        win_state = {"hours": init_hrs}
        top_ctrl = tk.Frame(modal.body, bg=neu_ui.BASE)
        top_ctrl.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))

        lbl_range = tk.Label(
            top_ctrl,
            text="时间范围：",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=modal.body),
        )
        lbl_range.pack(side="left", padx=(0, neu_ui.sx(6, modal.body)))

        range_candidates = [(24.0, "最近 24 小时")]
        for d in (3, 7, 14, 30):
            if d <= ret_days:
                range_candidates.append((float(d * 24), f"最近 {d} 天"))
        range_btns: dict[float, neu_ui.NeuButton] = {}
        for r_hrs, r_lbl in range_candidates:
            rb = neu_ui.NeuButton(
                top_ctrl,
                text=r_lbl,
                command=lambda h=r_hrs: _set_window(h),
                radius=12,
                pad=5,
                padx=9,
                pady=4,
                font_size=neu_ui.FONT_CAPTION,
            )
            rb.pack(side="left", padx=(0, neu_ui.sx(5, modal.body)))
            range_btns[r_hrs] = rb

        lbl_hover = tk.Label(
            top_ctrl,
            text="移动鼠标到曲线上可查看单点详细数值",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_DIM,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="e",
        )
        lbl_hover.pack(side="right")

        stats_well = neu_ui.NeuCard(modal.body, radius=14, pad=0, inner_pad=(12, 8), off=3, box_r=2, inset=True)
        stats_well.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))
        lbl_stats = tk.Label(
            stats_well.content,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
            justify="left",
            wraplength=neu_ui.sx(720, modal.body),
        )
        lbl_stats.pack(fill="x")
        lbl_power = tk.Label(
            stats_well.content,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
            justify="left",
            wraplength=neu_ui.sx(720, modal.body),
        )
        lbl_power.pack(fill="x", pady=(neu_ui.sx(3, modal.body), 0))
        lbl_dev_alerts = tk.Label(
            stats_well.content,
            text="",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_DIM,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
            justify="left",
            wraplength=neu_ui.sx(720, modal.body),
        )
        lbl_dev_alerts.pack(fill="x", pady=(neu_ui.sx(3, modal.body), 0))

        chart_well = neu_ui.NeuCard(modal.body, radius=16, pad=0, inner_pad=(10, 10), off=4, box_r=3, inset=True)
        chart_well.pack(fill="both", expand=True, pady=(0, neu_ui.sx(10, modal.body)))
        cv_h = neu_ui.sx(200, modal.body)
        cv = tk.Canvas(chart_well.content, bg=neu_ui.BASE, height=cv_h, bd=0, highlightthickness=0)
        cv.pack(fill="both", expand=True)

        drawn_points: list[tuple[float, float, dict[str, Any]]] = []

        def _current_samples() -> list[dict[str, Any]]:
            return battery_history.get_series(self.history_data, reading.key, window_hours=win_state["hours"])

        def _redraw_chart(_e=None) -> None:
            if not cv.winfo_exists():
                return
            w = max(neu_ui.sx(420, modal.body), cv.winfo_width() or neu_ui.sx(660, modal.body))
            h = max(neu_ui.sx(180, modal.body), cv.winfo_height() or cv_h)
            cv.delete("all")
            drawn_points.clear()

            samples = _current_samples()
            st = battery_history.summarize_series_stats(samples)
            ph = battery_history.estimate_power_and_health(samples, capacity_mah=cap_mah)
            cap_tag = f"{cap_mah} mAh" + (" (已设定)" if cap_custom else " (参考值)")
            pwr_parts = [f"标称容量 {cap_tag}"]
            if ph["est_current_ma"] is not None:
                pwr_parts.append(f"估算电流 ~{ph['est_current_ma']} mA (~{ph['est_power_mw']} mW)")
            pwr_parts.append(f"窗口耗电 ~{ph['total_discharged_mah']} mAh ({ph['total_discharged_wh']} Wh)")
            pwr_parts.append(f"等效充电循环 {ph['equivalent_cycles']} 次")
            if ph["rate_trend_pct"] is not None:
                sign = "+" if ph["rate_trend_pct"] > 0 else ""
                pwr_parts.append(f"耗电斜率变化 {sign}{ph['rate_trend_pct']}%")
            lbl_power.configure(text="   |   ".join(pwr_parts))

            alert_doc = battery_history.load_alert_history()
            a_st = battery_history.summarize_alert_stats(alert_doc, window_days=30, key=reading.key)
            if a_st["total_events"] > 0:
                latest_ev = a_st["events"][0]
                lt_s = time.strftime("%m-%d %H:%M", time.localtime(float(latest_ev.get("t", 0.0))))
                kind_s = "充满" if latest_ev.get("kind") == "full" else f"低电 {latest_ev.get('level')}%"
                lbl_dev_alerts.configure(
                    text=f"近 30 天告警：低电 {a_st['low_count']} 次（累计约 {a_st['total_low_minutes']} 分钟）· 充满 {a_st['full_count']} 次 · 最近：{lt_s} {kind_s}"
                )
            else:
                lbl_dev_alerts.configure(text="近 30 天该设备暂无低电/充满告警记录")

            if st["online_count"] == 0 and st["offline_count"] == 0:
                lbl_stats.configure(text="当前时间窗口内暂无历史采样数据；保持运行后将按刷新间隔自动累计。")
                cv.create_text(
                    w / 2.0,
                    h / 2.0,
                    text="暂无当前时间段内的电量采样记录",
                    fill=neu_ui.TEXT_DIM,
                    font=neu_ui.ui_font(neu_ui.FONT_BODY, root=modal.body),
                )
                return

            parts_summary = [
                f"样本：在线 {st['online_count']} / 离线 {st['offline_count']} / 充电 {st['charging_count']}",
                f"最高 {st['max_level'] if st['max_level'] is not None else '--'}% · 最低 {st['min_level'] if st['min_level'] is not None else '--'}% · 均值 {st['avg_level'] if st['avg_level'] is not None else '--'}%",
            ]
            if st["min_mv"] is not None and st["max_mv"] is not None:
                parts_summary.append(f"电压 {st['min_mv']/1000.0:.2f}V~{st['max_mv']/1000.0:.2f}V")
            if st["drain_rate_pct_per_h"] is not None:
                est_txt = f" · 预估可用 {st['est_hours']:.1f}h" if st["est_hours"] is not None else ""
                parts_summary.append(f"平均耗电 {st['drain_rate_pct_per_h']:.2f}%/h{est_txt}")
            lbl_stats.configure(text="   |   ".join(parts_summary))

            pad_l = neu_ui.sx(38, modal.body)
            pad_r = neu_ui.sx(16, modal.body)
            pad_t = neu_ui.sx(14, modal.body)
            pad_b = neu_ui.sx(24, modal.body)
            plot_w = max(40.0, w - pad_l - pad_r)
            plot_h = max(40.0, h - pad_t - pad_b)

            for pct in (0, 25, 50, 75, 100):
                yy = pad_t + plot_h * (1.0 - pct / 100.0)
                cv.create_line(pad_l, yy, pad_l + plot_w, yy, fill=neu_ui.SEP_DARK, dash=(2, 4))
                cv.create_text(
                    pad_l - neu_ui.sx(6, modal.body),
                    yy,
                    text=f"{pct}%",
                    fill=neu_ui.TEXT_DIM,
                    font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
                    anchor="e",
                )

            ordered = sorted(samples, key=lambda p: float(p.get("t", 0.0)))
            t_now = time.time()
            t_min = min(float(ordered[0].get("t", t_now)), t_now - win_state["hours"] * 3600.0)
            t_max = max(float(ordered[-1].get("t", t_now)), t_min + 60.0)
            span = max(1.0, t_max - t_min)

            fmt = "%H:%M" if win_state["hours"] <= 24.5 else "%m-%d %H:%M"
            for frac in (0.0, 0.5, 1.0):
                tx = pad_l + plot_w * frac
                ts_lbl = time.strftime(fmt, time.localtime(t_min + span * frac))
                anchor = "w" if frac == 0.0 else ("e" if frac == 1.0 else "center")
                cv.create_text(
                    tx,
                    h - neu_ui.sx(8, modal.body),
                    text=ts_lbl,
                    fill=neu_ui.TEXT_DIM,
                    font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
                    anchor=anchor,
                )

            last_online_xy: Optional[tuple[float, float]] = None
            last_xy: Optional[tuple[float, float]] = None
            last_was_off = False
            for pt in ordered:
                ts = float(pt.get("t", t_now))
                px = pad_l + ((ts - t_min) / span) * plot_w
                is_off = bool(pt.get("off")) or pt.get("lvl") is None
                if is_off:
                    py = last_online_xy[1] if last_online_xy is not None else (pad_t + plot_h)
                    if last_xy is not None:
                        cv.create_line(last_xy[0], last_xy[1], px, py, fill=neu_ui.TEXT_DISABLED, width=1.5, dash=(4, 4))
                    cv.create_oval(px - 2.5, py - 2.5, px + 2.5, py + 2.5, fill=neu_ui.TEXT_DISABLED, outline="")
                    drawn_points.append((px, py, pt))
                    last_xy = (px, py)
                    last_was_off = True
                    continue

                lvl = max(0, min(100, int(round(float(pt["lvl"])))))
                py = pad_t + plot_h * (1.0 - lvl / 100.0)
                is_chg = bool(pt.get("chg") or pt.get("charging") or pt.get("charge"))
                seg_col = neu_ui.AMBER_CHARGE if is_chg else devices.BAR_GREEN
                if last_xy is not None:
                    if last_was_off:
                        cv.create_line(last_xy[0], last_xy[1], px, py, fill=neu_ui.TEXT_DISABLED, width=1.5, dash=(4, 4))
                    else:
                        cv.create_line(last_xy[0], last_xy[1], px, py, fill=seg_col, width=2.4, capstyle="round")
                dot_r = 3.0 if len(ordered) <= 120 else 1.8
                cv.create_oval(px - dot_r, py - dot_r, px + dot_r, py + dot_r, fill=seg_col, outline="")
                drawn_points.append((px, py, pt))
                last_xy = (px, py)
                last_online_xy = (px, py)
                last_was_off = False

        def _on_mouse_move(event) -> None:
            cv.delete("hover_tag")
            if not drawn_points:
                return
            mx = float(event.x)
            nearest = min(drawn_points, key=lambda item: abs(item[0] - mx))
            px, py, pt = nearest
            cv.create_line(px, 0, px, max(1, cv.winfo_height()), fill=neu_ui.BLURPLE_LINK, dash=(2, 2), tags="hover_tag")
            cv.create_oval(px - 4.5, py - 4.5, px + 4.5, py + 4.5, outline=neu_ui.BLURPLE_LINK, width=2, fill=neu_ui.BASE, tags="hover_tag")
            ts_txt = time.strftime("%m-%d %H:%M:%S", time.localtime(float(pt.get("t", 0.0))))
            if pt.get("off") or pt.get("lvl") is None:
                val_txt = "离线"
            else:
                state_s = "充电中" if (pt.get("chg") or pt.get("charging")) else "正常"
                mv_s = f" · {int(pt['mv'])/1000.0:.2f}V" if isinstance(pt.get("mv"), (int, float)) else ""
                val_txt = f"{int(round(float(pt['lvl'])))}%（{state_s}{mv_s}）"
            lbl_hover.configure(text=f"{ts_txt}  |  {val_txt}", fg=neu_ui.TEXT)

        def _on_mouse_leave(_e=None) -> None:
            cv.delete("hover_tag")
            lbl_hover.configure(text="移动鼠标到曲线上可查看单点详细数值", fg=neu_ui.TEXT_DIM)

        def _set_window(hrs: float) -> None:
            win_state["hours"] = float(hrs)
            for h_val, rbtn in range_btns.items():
                try:
                    rbtn.configure(fg=neu_ui.BLURPLE_LINK if abs(h_val - float(hrs)) < 0.5 else neu_ui.TEXT_MUTED)
                except Exception:
                    pass
            _redraw_chart()

        def _clear_dev_history() -> None:
            if not neu_ui.ask_confirm_dialog(
                self.root,
                "清空本设备历史",
                f"确定清空「{reading.name}」的所有历史电量采样吗？清空后无法恢复。",
                confirm_text="确认清空",
                cancel_text="取消",
                kind="warn",
                danger=True,
            ):
                return
            battery_history.clear_device_history(reading.key)
            self.history_data = battery_history.load_history()
            _redraw_chart()
            if self.readings:
                self._render_cards(self.readings)

        def _export(fmt: str) -> None:
            samples = _current_samples()
            if not samples:
                neu_ui.ask_message_dialog(self.root, "暂无可导出数据", "当前时间窗口内没有历史电量样本。", kind="info")
                return
            ext = ".csv" if fmt == "csv" else ".json"
            safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in reading.name).strip("_") or "device"
            out_path = filedialog.asksaveasfilename(
                parent=self.root,
                title=f"导出电量历史 ({fmt.upper()})",
                defaultextension=ext,
                initialfile=f"battery_{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}{ext}",
                filetypes=[("CSV 表格", "*.csv")] if fmt == "csv" else [("JSON 数据", "*.json")],
            )
            if not out_path:
                return
            try:
                if fmt == "csv":
                    cnt = battery_history.export_history_csv(samples, out_path, key=reading.key, device_name=reading.name)
                else:
                    cnt = battery_history.export_history_json(
                        samples, out_path, key=reading.key, device_name=reading.name, window_hours=win_state["hours"]
                    )
                neu_ui.ask_message_dialog(self.root, "导出成功", f"已导出 {cnt} 条历史样本到：\n{out_path}", kind="success")
            except Exception as exc:
                neu_ui.ask_message_dialog(self.root, "导出失败", f"未能写入文件：{exc}", kind="error")

        cv.bind("<Configure>", _redraw_chart, add="+")
        cv.bind("<Motion>", _on_mouse_move, add="+")
        cv.bind("<Leave>", _on_mouse_leave, add="+")

        legend_row = tk.Frame(modal.body, bg=neu_ui.BASE)
        legend_row.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))
        tk.Label(
            legend_row,
            text="图例：  ── 正常放电   ── 充电中(琥珀)   --- 离线断点",
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT_DIM,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body),
            anchor="w",
        ).pack(side="left")

        btn_row = tk.Frame(modal.body, bg=neu_ui.BASE)
        btn_row.pack(fill="x")
        neu_ui.NeuButton(
            btn_row,
            text="导出 CSV",
            command=lambda: _export("csv"),
            accent=True,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left", padx=(0, neu_ui.sx(8, modal.body)))
        neu_ui.NeuButton(
            btn_row,
            text="导出 JSON",
            command=lambda: _export("json"),
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left", padx=(0, neu_ui.sx(8, modal.body)))
        neu_ui.NeuButton(
            btn_row,
            text="清空本设备历史",
            command=_clear_dev_history,
            radius=14,
            pad=6,
            padx=12,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
            fg=neu_ui.RED_DANGER,
        ).pack(side="left")
        neu_ui.NeuButton(
            btn_row,
            text="关闭",
            command=modal.close,
            radius=14,
            pad=6,
            padx=14,
            pady=4,
            font_size=neu_ui.FONT_CAPTION,
        ).pack(side="right")

        _set_window(win_state["hours"])

    # ---- 设置回调 & 设备操作 ----
    def _set_modal_note(self, text: str, color: Optional[str] = None) -> None:
        eff_col = neu_ui.TEXT_MUTED if color is None else color
        if self._note_clear_aid is not None:
            try:
                self.root.after_cancel(self._note_clear_aid)
            except Exception:
                pass
            self._note_clear_aid = None
        if self._settings_modal is not None and hasattr(self, "_lbl_modal_msg"):
            try:
                if self._lbl_modal_msg.winfo_exists():
                    self._lbl_modal_msg.configure(text=text, fg=eff_col)
                    if text:
                        self._note_clear_aid = self.root.after(
                            2800,
                            lambda: self._lbl_modal_msg.configure(text="") if (not self._closing and self._lbl_modal_msg.winfo_exists()) else None,
                        )
            except Exception:
                pass

    def _rename_device(self, r: devices.PeripheralReading) -> None:
        self._last_device_key = r.key
        new_name = neu_ui.ask_rename_dialog(
            self.root,
            "自定义设备名称",
            f"为 {r.vidpid_text} 输入显示名称（留空恢复默认）：",
            initialvalue=r.name,
        )
        if new_name is None:
            return
        cfg = tray_common.load_config()
        names = dict(cfg.get("device_names") or {})
        clean = new_name.strip()
        if clean:
            names[r.key] = clean
        else:
            names.pop(r.key, None)
        cfg["device_names"] = names
        tray_common.save_config(cfg)
        self.cfg = cfg

        disp_name = clean if clean else (r.name or r.vidpid_text)
        r.name = disp_name
        for card in self._card_widgets:
            if card.reading.key == r.key:
                card.set_pending_sync(self.scanning, disp_name)
        if self.scanning:
            self.lbl_summary.configure(text="已就地更新设备名称，将在本轮刷新完成后同步", fg=neu_ui.INFO)
        else:
            self.refresh_now()

    def _toggle_hide_device(self, r: devices.PeripheralReading) -> None:
        self._last_device_key = r.key
        cfg = tray_common.load_config()
        hidden = set(cfg.get("hidden_devices") or [])
        if r.key in hidden:
            hidden.remove(r.key)
            now_hidden = False
        else:
            hidden.add(r.key)
            now_hidden = True
        cfg["hidden_devices"] = sorted(hidden)
        tray_common.save_config(cfg)
        self.cfg = cfg
        if self.scanning:
            for card in self._card_widgets:
                if card.reading.key == r.key:
                    card.set_pending_sync(True)
            if now_hidden and not self.show_hidden:
                self.readings = [item for item in self.readings if item.key != r.key]
                self._render_cards(self.readings)
            self.lbl_summary.configure(text="已就地更新隐藏状态，将在本轮刷新完成后最终同步", fg=neu_ui.INFO)
        else:
            if now_hidden and not self.show_hidden:
                self.readings = [item for item in self.readings if item.key != r.key]
                self._render_cards(self.readings)
            self.refresh_now()

    def _on_theme_change(self, _e=None) -> None:
        lbl = self._var_theme.get()
        mode = next((val for name, val in THEME_CHOICES if name == lbl), "light")
        if mode == str(self.cfg.get("theme_mode", "light")):
            return
        cfg = tray_common.load_config()
        cfg["theme_mode"] = mode
        tray_common.save_config(cfg)
        self.cfg = cfg
        neu_ui.apply_theme(mode)
        self._rebuild_ui(reopen_settings=True)

    def _on_font_scale_change(self, _e=None) -> None:
        lbl = self._var_font_scale.get()
        mode = next((val for name, val in FONT_SCALE_CHOICES if name == lbl), "standard")
        if mode == str(self.cfg.get("font_scale_mode", "standard")):
            return
        cfg = tray_common.load_config()
        cfg["font_scale_mode"] = mode
        tray_common.save_config(cfg)
        self.cfg = cfg
        neu_ui.set_font_scale_mode(mode)
        self._rebuild_ui(reopen_settings=True)

    def _on_toggle_show_hidden(self) -> None:
        self.show_hidden = bool(self._var_hidden.get())
        cfg = tray_common.load_config()
        cfg["show_hidden"] = self.show_hidden
        tray_common.save_config(cfg)
        self.cfg = cfg
        self.refresh_now()
        self._set_modal_note(
            "已切换显示已隐藏设备" if self.show_hidden else "已隐藏被屏蔽的外设",
            neu_ui.INFO,
        )

    def _on_toast_alpha_change(self, _e=None) -> None:
        if not hasattr(self, "_var_toast_alpha"):
            return
        lbl = self._var_toast_alpha.get()
        val = next((v for name, v in TOAST_ALPHA_CHOICES if name == lbl), 60)
        if val == int(self.cfg.get("notify_alpha", 60)):
            return
        cfg = tray_common.load_config()
        cfg["notify_alpha"] = val
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._set_modal_note(f"低电量弹窗不透明度已设置为 {val}%", SUCCESS)

    def _on_interval_change(self, _e=None) -> None:
        lbl = self._var_interval.get()
        sec = next((s for name, s in INTERVAL_CHOICES if name == lbl), 120)
        if sec == int(self.cfg.get("interval", 120)):
            return
        cfg = tray_common.load_config()
        cfg["interval"] = sec
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._schedule_auto_refresh()
        self._sync_footer()
        self._set_modal_note(f"刷新间隔已更新为 {lbl}", SUCCESS)

    def _on_toggle_minimize_close(self) -> None:
        val = bool(self._var_min_close.get())
        cfg = tray_common.load_config()
        cfg["minimize_on_close"] = val
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._set_modal_note(
            "已启用关闭窗口时最小化到右下角托盘图标" if val else "关闭窗口将直接退出软件",
            SUCCESS,
        )

    def _on_toggle_autostart(self) -> None:
        want = bool(self._var_autostart.get())
        ok, msg = tray_common.set_autostart(want)
        self._var_autostart.set(ok)
        self._set_modal_note(msg, SUCCESS if (ok == want) else RED_DANGER)

    def _on_toggle_low_notify(self) -> None:
        val = bool(self._var_low_notify.get())
        cfg = tray_common.load_config()
        cfg["notify_enabled"] = val
        tray_common.save_config(cfg)
        self.cfg = cfg
        threshold = int(cfg.get("notify_threshold", 20))
        self._sync_footer()
        self._set_modal_note(
            f"已开启低电量系统通知（电量 < {threshold}% 时进入 Windows 通知中心）" if val else "已关闭低电量系统通知",
            SUCCESS,
        )

    def _on_threshold_change(self, _e=None) -> None:
        lbl = self._var_threshold.get()
        th = next((v for name, v in THRESHOLD_CHOICES if name == lbl), 20)
        if th == int(self.cfg.get("notify_threshold", 20)):
            return
        cfg = tray_common.load_config()
        cfg["notify_threshold"] = th
        tray_common.save_config(cfg)
        self.cfg = cfg
        dev_ths = dict(cfg.get("device_thresholds") or {})
        if self.readings:
            self._render_cards(self.readings)
        self._sync_footer()
        if self.tray_host:
            self.tray_host.update_readings(self.readings, threshold=th, device_thresholds=dev_ths)
        self.hover_tooltip.update_data(self.readings, threshold=th, device_thresholds=dev_ths)
        self._set_modal_note(f"默认低电告警阈值已设置为 {th}%", SUCCESS)

    def _on_cooldown_change(self, _e=None) -> None:
        lbl = self._var_cooldown.get()
        mins = next((v for name, v in COOLDOWN_CHOICES if name == lbl), 30)
        if mins == int(self.cfg.get("notify_cooldown_min", 30)):
            return
        cfg = tray_common.load_config()
        cfg["notify_cooldown_min"] = mins
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._set_modal_note(f"常规提醒冷却频率已调整为 {mins} 分钟", SUCCESS)

    def _on_bg_interval_change(self, _e=None) -> None:
        lbl = self._var_bg_interval.get()
        sec = next((s for name, s in BG_INTERVAL_CHOICES if name == lbl), 300)
        if sec == int(self.cfg.get("bg_interval", 300)):
            return
        cfg = tray_common.load_config()
        cfg["bg_interval"] = sec
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._schedule_auto_refresh()
        self._sync_footer()
        self._set_modal_note(f"后台节能刷新间隔已更新为 {lbl}", SUCCESS)

    def _apply_sort_mode(self, mode: str, *, note: bool = False) -> None:
        norm_mode = mode if mode in tray_common.VALID_SORT_MODES else "default"
        if norm_mode == str(self.cfg.get("sort_mode", "default")):
            return
        cfg = tray_common.load_config()
        cfg["sort_mode"] = norm_mode
        tray_common.save_config(cfg)
        self.cfg = cfg
        lbl = next((n for n, v in SORT_MODE_CHOICES if v == cfg["sort_mode"]), "默认顺序")
        if hasattr(self, "_var_toolbar_sort"):
            try:
                self._var_toolbar_sort.set(lbl)
            except Exception:
                pass
        if hasattr(self, "_var_sort_mode"):
            try:
                self._var_sort_mode.set(lbl)
            except Exception:
                pass
        if self.readings:
            devices.sort_readings(self.readings, cfg["sort_mode"], set(cfg.get("pinned") or []))
            self._render_cards(self.readings)
        if note:
            self._set_modal_note(f"已切换排序方式：{lbl}", SUCCESS)

    def _on_toolbar_sort_change(self, _e=None) -> None:
        lbl = self._var_toolbar_sort.get()
        mode = next((v for n, v in SORT_MODE_CHOICES if n == lbl), "default")
        self._apply_sort_mode(mode, note=False)

    def _on_settings_sort_change(self, _e=None) -> None:
        lbl = self._var_sort_mode.get()
        mode = next((v for n, v in SORT_MODE_CHOICES if n == lbl), "default")
        self._apply_sort_mode(mode, note=True)

    def _set_compact_view(self, enabled: bool, *, from_settings: bool = False) -> None:
        cfg = tray_common.load_config()
        cfg["compact_view"] = bool(enabled)
        tray_common.save_config(cfg)
        self.cfg = cfg
        if hasattr(self, "_btn_compact"):
            try:
                self._btn_compact.configure(
                    text="标准卡片" if enabled else "紧凑视图",
                    fg=neu_ui.BLURPLE_LINK if enabled else neu_ui.TEXT_MUTED,
                )
            except Exception:
                pass
        if hasattr(self, "_var_compact"):
            try:
                self._var_compact.set(bool(enabled))
            except Exception:
                pass
        if self.readings:
            self._render_cards(self.readings)
        if from_settings:
            self._set_modal_note("已切换为单行紧凑视图" if enabled else "已恢复标准详情卡片", SUCCESS)

    def _toggle_compact_view(self) -> None:
        self._set_compact_view(not bool(self.cfg.get("compact_view", False)), from_settings=False)

    def _on_toggle_compact(self) -> None:
        self._set_compact_view(bool(self._var_compact.get()), from_settings=True)

    def _update_tray_style_preview(self, style: str) -> None:
        if not hasattr(self, "_lbl_tray_preview"):
            return
        try:
            if not self._lbl_tray_preview.winfo_exists():
                return
            hx = neu_ui.BASE.lstrip("#")
            bg_rgb = (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))
            lowest = next((r for r in self.readings if not r.offline and r.level is not None), None)
            lvl = lowest.level if lowest else 72
            role = lowest.role if lowest else "mouse"
            chg = lowest.charging if lowest else False
            th = int(self.cfg.get("notify_threshold", 20))
            col = devices.battery_color_rgb(lvl, charging=chg, low_threshold=th)
            ppm = win_icon.render_style_preview_ppm(
                style, lvl, col, alert=(lvl is not None and lvl < th and not chg),
                role=role, charging=chg, bg_rgb=bg_rgb, size=neu_ui.sx(28, self.root),
            )
            photo = tk.PhotoImage(data=ppm)
            self._tray_preview_photo = photo
            self._lbl_tray_preview.configure(image=photo, bg=neu_ui.BASE)
        except Exception:
            pass

    def _on_tray_style_change(self, _e=None) -> None:
        lbl = self._var_tray_style.get()
        st = next((v for n, v in TRAY_STYLE_CHOICES if n == lbl), "digit")
        if st == str(self.cfg.get("tray_icon_style", "digit")):
            return
        cfg = tray_common.load_config()
        cfg["tray_icon_style"] = st
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._update_tray_style_preview(st)
        if self.tray_host:
            self.tray_host.update_readings(
                self.readings, threshold=int(cfg.get("notify_threshold", 20)), style=st
            )
        self._set_modal_note(f"托盘图标样式已切换为「{lbl}」", SUCCESS)

    def _on_tray_vis_change(self, _e=None) -> None:
        lbl = self._var_tray_vis.get()
        vis = next((v for n, v in TRAY_VIS_CHOICES if n == lbl), "always")
        if vis == str(self.cfg.get("tray_visibility", "always")):
            return
        cfg = tray_common.load_config()
        cfg["tray_visibility"] = vis
        tray_common.save_config(cfg)
        self.cfg = cfg
        if self.tray_host:
            self.tray_host.update_readings(
                self.readings, threshold=int(cfg.get("notify_threshold", 20)), visibility=vis
            )
        self._set_modal_note(f"托盘图标显示时机已设置为「{lbl}」", SUCCESS)

    def _on_log_level_change(self, _e=None) -> None:
        lbl = self._var_log_level.get()
        lvl = next((v for n, v in LOG_LEVEL_CHOICES if n == lbl), "INFO")
        if lvl == str(self.cfg.get("log_level", "INFO")).upper():
            return
        cfg = tray_common.load_config()
        cfg["log_level"] = lvl
        tray_common.save_config(cfg)
        tray_common.set_log_level(lvl)
        self.cfg = cfg
        self._set_modal_note(f"日志级别已切换为 {lvl}", SUCCESS)

    def _refresh_settings_device_lists(self) -> None:
        cfg = tray_common.load_config()
        self.cfg = cfg
        if hasattr(self, "_manual_tags_frame") and self._manual_tags_frame.winfo_exists():
            for ch in list(self._manual_tags_frame.winfo_children()):
                ch.destroy()
            manuals = list(cfg.get("manual_devices") or [])
            if not manuals:
                tk.Label(
                    self._manual_tags_frame, text="暂无手动添加的 VID:PID",
                    bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self.root),
                ).pack(anchor="w")
            else:
                for vp in manuals:
                    neu_ui.NeuButton(
                        self._manual_tags_frame, text=f"{vp} ×",
                        command=lambda v=vp: self._remove_manual_device(v),
                        radius=10, pad=3, padx=8, pady=2, font_size=neu_ui.FONT_CAPTION,
                        fg=neu_ui.TEXT_MUTED, tooltip="点击移除该手动 VID:PID",
                    ).pack(side="left", padx=(0, neu_ui.sx(4, self.root)))

        if hasattr(self, "_ignored_tags_frame") and self._ignored_tags_frame.winfo_exists():
            for ch in list(self._ignored_tags_frame.winfo_children()):
                ch.destroy()
            ignored = list(cfg.get("ignored_devices") or [])
            names = dict(cfg.get("device_names") or {})
            known = dict(cfg.get("known_devices") or {})
            if not ignored:
                tk.Label(
                    self._ignored_tags_frame, text="暂无已“不再显示”的外设",
                    bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self.root),
                ).pack(anchor="w")
            else:
                for dk in ignored:
                    disp = names.get(dk) or (known.get(dk) or {}).get("name") or dk
                    neu_ui.NeuButton(
                        self._ignored_tags_frame, text=f"{disp} · 恢复",
                        command=lambda k=dk: self._restore_ignored_device(k),
                        radius=10, pad=3, padx=8, pady=2, font_size=neu_ui.FONT_CAPTION,
                        fg=neu_ui.BLURPLE_LINK, tooltip=f"恢复监测 {dk}",
                    ).pack(side="left", padx=(0, neu_ui.sx(4, self.root)))

    def _on_add_manual_device(self) -> None:
        raw = self._var_manual_vp.get().strip() if hasattr(self, "_var_manual_vp") else ""
        norm = tray_common.normalize_vid_pid(raw)
        if not norm:
            self._set_modal_note("请输入合法的 VID:PID 格式，例如 046D:C547 或 3554:F510", neu_ui.RED_DANGER)
            return
        cfg = tray_common.load_config()
        manuals = list(cfg.get("manual_devices") or [])
        if norm not in manuals:
            manuals.append(norm)
            cfg["manual_devices"] = manuals
            tray_common.save_config(cfg)
            self.cfg = cfg
        if hasattr(self, "_var_manual_vp"):
            self._var_manual_vp.set("")
        self._refresh_settings_device_lists()
        self._set_modal_note(f"已强制纳入 {norm}，正在重新扫描…", SUCCESS)
        self.refresh_now()

    def _remove_manual_device(self, vp: str) -> None:
        cfg = tray_common.load_config()
        manuals = [x for x in (cfg.get("manual_devices") or []) if x != vp]
        cfg["manual_devices"] = manuals
        tray_common.save_config(cfg)
        self._refresh_settings_device_lists()
        self._set_modal_note(f"已移除手动设备 {vp}", neu_ui.INFO)
        self.refresh_now()

    def _restore_ignored_device(self, key: str) -> None:
        cfg = tray_common.load_config()
        ign = [x for x in (cfg.get("ignored_devices") or []) if x != key]
        cfg["ignored_devices"] = ign
        tray_common.save_config(cfg)
        self._refresh_settings_device_lists()
        self._set_modal_note("已恢复显示该设备，正在刷新…", SUCCESS)
        self.refresh_now()

    def _pin_device(self, r: devices.PeripheralReading) -> None:
        self._last_device_key = r.key
        cfg = tray_common.load_config()
        pinned = list(cfg.get("pinned") or [])
        if r.key in pinned:
            pinned.remove(r.key)
            now_pin = False
        else:
            pinned.append(r.key)
            now_pin = True
        cfg["pinned"] = pinned
        tray_common.save_config(cfg)
        self.cfg = cfg
        for item in self.readings:
            if item.key == r.key:
                item.pinned = now_pin
        devices.sort_readings(self.readings, str(cfg.get("sort_mode") or "default"), set(pinned))
        self._render_cards(self.readings)

    def _set_device_role(self, r: devices.PeripheralReading, role: str) -> None:
        if role not in devices.ROLE_ORDER:
            return
        cfg = tray_common.load_config()
        roles = dict(cfg.get("device_roles") or {})
        if r.role == role and roles.get(r.key) == role:
            return
        roles[r.key] = role
        cfg["device_roles"] = roles
        tray_common.save_config(cfg)
        self.cfg = cfg
        if r.role == role:
            return
        for item in self.readings:
            if item.key == r.key:
                item.role = role
        devices.sort_readings(self.readings, str(cfg.get("sort_mode") or "default"), set(cfg.get("pinned") or []))
        self._render_cards(self.readings)

    def _forget_device(self, r: devices.PeripheralReading) -> None:
        ok = neu_ui.ask_confirm_dialog(
            self.root,
            "不再显示此设备",
            f"确定要遗忘「{r.name}」吗？\n移除后将清理其缓存记录，且不再自动提示（可在设置中恢复）。",
            confirm_text="不再显示",
            cancel_text="取消",
            kind="warn",
            danger=True,
        )
        if not ok:
            return
        cfg = tray_common.load_config()
        ign = list(cfg.get("ignored_devices") or [])
        if r.key not in ign:
            ign.append(r.key)
        cfg["ignored_devices"] = ign
        known = dict(cfg.get("known_devices") or {})
        known.pop(r.key, None)
        cfg["known_devices"] = known
        pinned = [x for x in (cfg.get("pinned") or []) if x != r.key]
        cfg["pinned"] = pinned
        tray_common.save_config(cfg)
        self.cfg = cfg
        self.readings = [item for item in self.readings if item.key != r.key]
        self._render_cards(self.readings)

    def _snooze_device(self, r: devices.PeripheralReading, minutes: int = 30) -> None:
        self._snooze_device_by_key(r.key, minutes, display_name=r.name)

    def _snooze_device_by_key(self, key: str, minutes: int = 30, *, display_name: str = "") -> None:
        self._low_monitor.snooze(key, minutes=float(minutes))
        if self.tray_host:
            self.tray_host.update_readings(
                self.readings,
                threshold=int(self.cfg.get("notify_threshold", 20)),
                snooze_active=True,
                device_thresholds=self.cfg.get("device_thresholds") or {},
            )
        label = display_name or next((item.name for item in self.readings if item.key == key), key)
        tray_common.log_event("INFO", "notify", f"已为 {label} 暂缓低电提醒 {minutes} 分钟")

    # ---- 新增交互回调：心跳、搜索/筛选/分组、每设备阈值与容量、保留策略、告警统计报表 (#1-#13) ----
    def _schedule_heartbeat(self) -> None:
        if self._closing or self._disable_tray_host:
            return
        if self._heartbeat_aid is not None:
            try:
                self.root.after_cancel(self._heartbeat_aid)
            except Exception:
                pass
        self._heartbeat_aid = self.root.after(15000, self._heartbeat_tick)

    def _heartbeat_tick(self) -> None:
        self._heartbeat_aid = None
        if self._closing:
            return
        try:
            tray_common.write_heartbeat(os.getpid())
        except Exception:
            pass
        self._schedule_heartbeat()

    def _on_search_changed(self) -> None:
        if self._rebuild_pending or self._closing or not hasattr(self, "_var_search"):
            return
        new_q = self._var_search.get()
        if new_q == self._search_query:
            return
        self._search_query = new_q
        if self.readings:
            self._render_cards(self.readings)

    def _clear_search(self) -> None:
        self._search_query = ""
        self._filter_mode = "all"
        if hasattr(self, "_var_search"):
            self._var_search.set("")
        if self.readings:
            self._render_cards(self.readings)

    def _set_filter_mode(self, mode: str) -> None:
        self._filter_mode = mode if mode in {v for _, v in FILTER_MODE_CHOICES} else "all"
        if self.readings:
            self._render_cards(self.readings)

    def _update_filter_bar_counts(self, res: list[devices.PeripheralReading]) -> None:
        if not self._filter_btns:
            return
        counts = {
            "all": len(res),
            "online": sum(1 for r in res if (not r.offline) and r.level is not None),
            "low": sum(
                1 for r in res
                if (not r.offline) and r.level is not None and (not r.charging) and (not r.full)
                and int(r.level) < tray_common.get_device_threshold(self.cfg, r.key)
            ),
            "charging": sum(1 for r in res if (not r.offline) and r.charging),
            "offline": sum(1 for r in res if r.offline),
        }
        for lbl, fkey in FILTER_MODE_CHOICES:
            btn = self._filter_btns.get(fkey)
            if btn is None:
                continue
            cnt = counts.get(fkey, 0)
            try:
                btn.configure(
                    text=f"{lbl} ({cnt})",
                    fg=neu_ui.BLURPLE_LINK if self._filter_mode == fkey else neu_ui.TEXT_MUTED,
                )
            except Exception:
                pass

    def _toggle_group_by_role(self) -> None:
        cfg = tray_common.load_config()
        new_val = not bool(cfg.get("group_by_role", False))
        cfg["group_by_role"] = new_val
        tray_common.save_config(cfg)
        self.cfg = cfg
        if hasattr(self, "_btn_group"):
            try:
                self._btn_group.configure(
                    text="分组：开" if new_val else "按类型分组",
                    fg=neu_ui.BLURPLE_LINK if new_val else neu_ui.TEXT_MUTED,
                )
            except Exception:
                pass
        if self.readings:
            self._render_cards(self.readings)

    def _toggle_role_collapse(self, role: str) -> None:
        cfg = tray_common.load_config()
        collapsed = list(cfg.get("collapsed_roles") or [])
        if role in collapsed:
            collapsed.remove(role)
        else:
            collapsed.append(role)
        cfg["collapsed_roles"] = collapsed
        tray_common.save_config(cfg)
        self.cfg = cfg
        if self.readings:
            self._render_cards(self.readings)

    def _set_device_threshold(self, r: devices.PeripheralReading, value: Any) -> None:
        target_val: Optional[int]
        if value is None:
            target_val = None
        elif value == "custom":
            cur = tray_common.get_device_threshold(self.cfg, r.key)
            raw = neu_ui.ask_rename_dialog(
                self.root,
                "设置设备低电阈值",
                f"为「{r.name}」输入低电提醒阈值（5~50%，留空恢复跟随全局）：",
                initialvalue=str(cur),
            )
            if raw is None:
                return
            raw_s = raw.strip().rstrip("%")
            if not raw_s:
                target_val = None
            else:
                try:
                    num = int(raw_s)
                    if not (5 <= num <= 50):
                        raise ValueError("out of range")
                    target_val = num
                except Exception:
                    neu_ui.ask_message_dialog(self.root, "输入无效", "请输入 5 到 50 之间的整数百分比。", kind="error")
                    return
        else:
            target_val = max(5, min(50, int(value)))

        cfg = tray_common.load_config()
        per = dict(cfg.get("device_thresholds") or {})
        if target_val is None:
            per.pop(r.key, None)
        else:
            per[r.key] = target_val
        cfg["device_thresholds"] = per
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._low_monitor._notified.pop(r.key, None)
        if self.readings:
            self._render_cards(self.readings)
        g_th = int(cfg.get("notify_threshold", 20))
        if self.tray_host:
            self.tray_host.update_readings(self.readings, threshold=g_th, device_thresholds=per)
        self.hover_tooltip.update_data(self.readings, threshold=g_th, device_thresholds=per)

    def _set_device_capacity(self, r: devices.PeripheralReading) -> None:
        cur_cap, _ = tray_common.get_device_capacity_mah(self.cfg, r.key, r.role)
        raw = neu_ui.ask_rename_dialog(
            self.root,
            "设置电池标称容量 (mAh)",
            f"为「{r.name}」输入标称容量（50~20000 mAh，留空恢复角色参考值）：",
            initialvalue=str(cur_cap),
        )
        if raw is None:
            return
        raw_s = raw.strip().lower().replace("mah", "").strip()
        cfg = tray_common.load_config()
        caps = dict(cfg.get("device_capacities_mah") or {})
        if not raw_s:
            caps.pop(r.key, None)
        else:
            try:
                num = int(raw_s)
                if not (50 <= num <= 20000):
                    raise ValueError("out of range")
                caps[r.key] = num
            except Exception:
                neu_ui.ask_message_dialog(self.root, "输入无效", "请输入 50 到 20000 之间的整数 mAh。", kind="error")
                return
        cfg["device_capacities_mah"] = caps
        tray_common.save_config(cfg)
        self.cfg = cfg

    def _on_toggle_full_notify(self) -> None:
        val = bool(self._var_full_notify.get())
        cfg = tray_common.load_config()
        cfg["notify_full_charge"] = val
        tray_common.save_config(cfg)
        self.cfg = cfg
        self._set_modal_note("已开启充满电完成提醒" if val else "已关闭充满电完成提醒", SUCCESS)

    def _on_tray_hover_change(self, _e=None) -> None:
        lbl = self._var_tray_hover.get()
        hm = next((v for n, v in TRAY_HOVER_CHOICES if n == lbl), "grid")
        if hm == str(self.cfg.get("tray_hover_mode", "grid")):
            return
        cfg = tray_common.load_config()
        cfg["tray_hover_mode"] = hm
        tray_common.save_config(cfg)
        self.cfg = cfg
        if hm == "native":
            self.hover_tooltip.hide()
        if self.tray_host:
            self.tray_host.update_readings(
                self.readings,
                threshold=int(cfg.get("notify_threshold", 20)),
                device_thresholds=cfg.get("device_thresholds") or {},
                hover_mode=hm,
            )
        self._set_modal_note(f"托盘悬停样式已切换为「{lbl}」", SUCCESS)

    def _on_toggle_watchdog(self) -> None:
        want = bool(self._var_watchdog.get())
        cfg = tray_common.load_config()
        cfg["watchdog_enabled"] = want
        tray_common.save_config(cfg)
        self.cfg = cfg
        if tray_common.is_autostart_enabled():
            tray_common.set_autostart(True)
        self._set_modal_note(
            "已启用后台看门狗守护（开机自启生效）" if want else "已关闭看门狗托管",
            SUCCESS,
        )

    def _on_retention_change(self, _e=None) -> None:
        lbl = self._var_retention.get()
        days = next((v for n, v in RETENTION_DAY_CHOICES if n == lbl), 7)
        if days == tray_common.get_retention_days(self.cfg):
            return
        cfg = tray_common.load_config()
        cfg["history_retention_days"] = days
        tray_common.save_config(cfg)
        self.cfg = cfg
        self.history_data = battery_history.save_history(
            battery_history.load_history(retention_days=days),
            retention_days=days,
        )
        if self.readings:
            self._render_cards(self.readings)
        self._set_modal_note(f"历史采样保留天数已调整为 {days} 天", SUCCESS)

    def _export_all_history(self) -> None:
        out_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出全部设备电量历史",
            defaultextension=".json",
            initialfile=f"battery_history_all_{time.strftime('%Y%m%d_%H%M%S')}.json",
            filetypes=[("JSON 历史库", "*.json"), ("CSV 表格", "*.csv")],
        )
        if not out_path:
            return
        try:
            days = tray_common.get_retention_days(self.cfg)
            data = battery_history.load_history(retention_days=days)
            if out_path.lower().endswith(".csv"):
                cnt = battery_history.export_history_csv(data, out_path, window_hours=float(days * 24))
            else:
                cnt = battery_history.export_history_json(data, out_path, window_hours=float(days * 24))
            self._set_modal_note(f"已导出 {cnt} 条历史样本", SUCCESS)
        except Exception as exc:
            self._set_modal_note(f"导出历史失败：{exc}", RED_DANGER)

    def _import_all_history(self) -> None:
        in_path = filedialog.askopenfilename(
            parent=self.root,
            title="导入并合并历史电量库 (JSON)",
            filetypes=[("JSON 历史库", "*.json")],
        )
        if not in_path:
            return
        try:
            days = tray_common.get_retention_days(self.cfg)
            res = battery_history.import_history_json(in_path, retention_days=days)
            self.history_data = battery_history.load_history(retention_days=days)
            if self.readings:
                self._render_cards(self.readings)
            self._set_modal_note(f"已合并 {res['added']} 条新样本（共 {res['device_count']} 台设备）", SUCCESS)
        except Exception as exc:
            self._set_modal_note(f"导入失败：{exc}", RED_DANGER)

    def _clear_all_history(self) -> None:
        if not neu_ui.ask_confirm_dialog(
            self.root,
            "清空全部历史",
            "确定清空所有外设的历史电量采样记录吗？清空后折线图与耗电速率将重新开始累计。",
            confirm_text="确认清空",
            cancel_text="取消",
            kind="warn",
            danger=True,
        ):
            return
        battery_history.clear_all_history()
        self.history_data = {"version": 1, "devices": {}}
        if self.readings:
            self._render_cards(self.readings)
        self._set_modal_note("已清空全部历史采样数据", neu_ui.INFO)

    def _on_migrate_to_appdata(self) -> None:
        target = tray_common.get_appdata_dir()
        if not neu_ui.ask_confirm_dialog(
            self.root,
            "迁移数据至 %APPDATA%",
            f"将把配置文件、电量历史、告警记录与自定义协议复制迁移到：\n{target}\n\n确认执行迁移吗？",
            confirm_text="开始迁移",
            cancel_text="取消",
            kind="info",
        ):
            return
        res = tray_common.migrate_data_dir(target)
        self._set_modal_note(str(res.get("message") or "迁移完成"), SUCCESS)

    def open_alert_report_modal(self) -> None:
        if getattr(self, "_alert_report_modal", None) is not None:
            return
        prev_settings = self._settings_modal
        reuse_dim = getattr(prev_settings, "_dim_photo", None) if prev_settings is not None else None
        reuse_stages = list(getattr(prev_settings, "_dim_stages", []) or []) if prev_settings is not None else None
        opened_from_settings = prev_settings is not None

        def _on_closed() -> None:
            self._alert_report_modal = None
            if (
                opened_from_settings
                and not self._closing
                and not getattr(self, "_suppress_settings_return", False)
                and self.root.winfo_exists()
            ):
                self.open_settings_modal(dim_photo=reuse_dim, dim_stages=reuse_stages)

        modal = neu_ui.NeuModalOverlay(
            self.root,
            title="告警历史与统计报表",
            subtitle="统计过去 7 天 / 30 天 / 本月的低电量次数、累计低电时长与充满电记录",
            max_width_dp=720,
            min_height_dp=470,
            dim_photo=reuse_dim,
            dim_stages=reuse_stages,
            fade_scrim_on_close=not opened_from_settings,
            on_close=_on_closed,
            close_on_backdrop=True,
        )
        self._alert_report_modal = modal

        ctrl = tk.Frame(modal.body, bg=neu_ui.BASE)
        ctrl.pack(fill="x", pady=(0, neu_ui.sx(8, modal.body)))
        mode_state = {"mode": "7d"}
        tab_btns: dict[str, neu_ui.NeuButton] = {}

        sum_lbl = tk.Label(
            modal.body, text="", bg=neu_ui.BASE, fg=neu_ui.TEXT,
            font=neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=modal.body), anchor="w", justify="left",
            wraplength=neu_ui.sx(660, modal.body),
        )
        sum_lbl.pack(fill="x", pady=(0, neu_ui.sx(6, modal.body)))

        text_well = neu_ui.NeuCard(modal.body, radius=16, pad=0, inner_pad=(12, 10), off=4, box_r=3, inset=True)
        text_well.pack(fill="both", expand=True, pady=(0, neu_ui.sx(10, modal.body)))
        txt = tk.Text(
            text_well.content, height=15, bg=neu_ui.BASE, fg=neu_ui.TEXT_MUTED,
            bd=0, highlightthickness=0, wrap="word",
            font=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=modal.body),
        )
        txt.pack(fill="both", expand=True)

        def _load_report(m_key: str) -> None:
            mode_state["mode"] = m_key
            for tk_k, tb in tab_btns.items():
                try:
                    tb.configure(fg=neu_ui.BLURPLE_LINK if tk_k == m_key else neu_ui.TEXT_MUTED)
                except Exception:
                    pass
            doc = battery_history.load_alert_history()
            if m_key == "mtd":
                st = battery_history.summarize_alert_stats(doc, month_to_date=True)
            elif m_key == "30d":
                st = battery_history.summarize_alert_stats(doc, window_days=30)
            else:
                st = battery_history.summarize_alert_stats(doc, window_days=7)
            sum_lbl.configure(
                text=f"共 {st['total_events']} 条通知 · 低电告警 {st['low_count']} 次（累计低电时长约 {st['total_low_minutes']} 分钟）· 充满电提醒 {st['full_count']} 次"
            )
            lines: list[str] = ["=== 按设备统计 ==="]
            if not st["per_device"]:
                lines.append("（该时段暂无告警记录）")
            else:
                for rec in st["per_device"]:
                    lines.append(
                        f"- {rec['name']}: 低电 {rec['low_count']} 次 (紧急≤5%: {rec['critical_count']}) | "
                        f"累计低电 {rec['low_duration_min']} min | 充满 {rec['full_count']} 次"
                    )
            lines.append("\n=== 最近告警流水 ===")
            if not st["events"]:
                lines.append("（暂无明细）")
            else:
                for ev in st["events"][:80]:
                    t_s = time.strftime("%m-%d %H:%M:%S", time.localtime(float(ev.get("t", 0.0))))
                    kind_s = "充满100%" if ev.get("kind") == "full" else f"低电[{ev.get('tier')}] {ev.get('level')}% (<{ev.get('threshold')}%)"
                    dur_s = f" · 持续 {ev['duration_min']} min" if ev.get("duration_min") is not None else ""
                    lines.append(f"[{t_s}] {ev.get('name')} | {kind_s}{dur_s}")
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("end", "\n".join(lines))
            txt.configure(state="disabled")

        for mk, mlbl in (("7d", "最近 7 天"), ("30d", "最近 30 天"), ("mtd", "本月至今")):
            b = neu_ui.NeuButton(
                ctrl, text=mlbl, command=lambda m=mk: _load_report(m),
                radius=12, pad=5, padx=10, pady=4, font_size=neu_ui.FONT_CAPTION,
            )
            b.pack(side="left", padx=(0, neu_ui.sx(6, modal.body)))
            tab_btns[mk] = b

        def _export_alerts_csv() -> None:
            out_path = filedialog.asksaveasfilename(
                parent=self.root,
                title="导出告警历史 (CSV)",
                defaultextension=".csv",
                initialfile=f"battery_alerts_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                filetypes=[("CSV 表格", "*.csv")],
            )
            if not out_path:
                return
            doc = battery_history.load_alert_history()
            cnt = battery_history.export_alert_history_csv(doc.get("events") or [], out_path)
            neu_ui.ask_message_dialog(self.root, "导出成功", f"已导出 {cnt} 条告警记录到：\n{out_path}", kind="success")

        btn_row = tk.Frame(modal.body, bg=neu_ui.BASE)
        btn_row.pack(fill="x")
        neu_ui.NeuButton(
            btn_row, text="导出告警 CSV", command=_export_alerts_csv,
            accent=True, radius=14, pad=6, padx=12, pady=4, font_size=neu_ui.FONT_CAPTION,
        ).pack(side="left")
        neu_ui.NeuButton(
            btn_row, text="关闭", command=modal.close,
            radius=14, pad=6, padx=14, pady=4, font_size=neu_ui.FONT_CAPTION,
        ).pack(side="right")

        _load_report("7d")
        if prev_settings is not None:
            try:
                modal.update_idletasks()
            except Exception:
                pass
            if self._settings_modal is prev_settings:
                self._settings_modal = None
            prev_settings.close()

    def open_why_unread_wizard(self, r: devices.PeripheralReading) -> None:
        """三步引导排障向导 (#41)：枚举接口 -> 标准电量页/私有通道 -> 协议匹配结论。"""
        if self._wizard_modal is not None:
            return
        steps = devices.diagnose_device_steps(r.vid, r.pid, reading=r)
        modal = neu_ui.NeuModalOverlay(
            self.root,
            title=f"为什么读不到：{r.name}",
            subtitle=f"{r.vidpid_text} · 三步自动化硬件通道与协议诊断",
            max_width_dp=600,
            on_close=lambda: setattr(self, "_wizard_modal", None),
            close_on_backdrop=True,
        )
        self._wizard_modal = modal
        body = modal.body
        for st in steps:
            card = neu_ui.NeuCard(body, radius=16, pad=0, inner_pad=(14, 10), off=4, box_r=3, inset=True)
            card.pack(fill="x", pady=(0, neu_ui.sx(8, body)))
            hdr = tk.Frame(card.content, bg=neu_ui.BASE)
            hdr.pack(fill="x")
            badge_txt = "✓ 通过" if st["ok"] else "! 异常"
            badge_fg = neu_ui.SUCCESS if st["ok"] else neu_ui.RED_DANGER
            tk.Label(
                hdr, text=badge_txt, bg=neu_ui.BASE, fg=badge_fg,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=body),
            ).pack(side="right")
            tk.Label(
                hdr, text=st["title"], bg=neu_ui.BASE, fg=neu_ui.TEXT,
                font=neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=body), anchor="w",
            ).pack(side="left", fill="x", expand=True)
            tk.Label(
                card.content, text=st["summary"], bg=neu_ui.BASE, fg=neu_ui.TEXT_MUTED,
                font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=body), anchor="w",
                wraplength=neu_ui.sx(520, body), justify="left",
            ).pack(fill="x", pady=(neu_ui.sx(4, body), neu_ui.sx(2, body)))
            if st["details"]:
                det_txt = "\n".join(st["details"])
                tk.Label(
                    card.content, text=det_txt, bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM,
                    font=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=body), anchor="w",
                    wraplength=neu_ui.sx(520, body), justify="left",
                ).pack(fill="x", pady=(2, 0))
            if st["action_type"] == "rescan":
                neu_ui.NeuButton(
                    card.content, text=st["action_label"],
                    command=lambda: (modal.close(), self.refresh_now()),
                    radius=12, pad=5, padx=10, pady=4, font_size=neu_ui.FONT_CAPTION,
                ).pack(anchor="w", pady=(neu_ui.sx(6, body), 0))
            elif st["action_type"] == "open_debug":
                vp_pre = f"{r.vid:04X}:{r.pid:04X}" if (r.vid is not None and r.pid is not None) else ""
                neu_ui.NeuButton(
                    card.content, text=st["action_label"],
                    command=lambda v=vp_pre: (modal.close(), self.open_debug_tool(r, select_vidpid=v, initial_tab=3)),
                    accent=True, radius=12, pad=5, padx=12, pady=4, font_size=neu_ui.FONT_CAPTION,
                ).pack(anchor="w", pady=(neu_ui.sx(6, body), 0))

    def _open_skipped_devices_action(self) -> None:
        """点击“另有 N 台未识别，去标定”后展示候选未识别列表并支持带 VID:PID 跳转标定 (#42)。"""
        st = self._last_scan_stats or devices.get_last_scan_stats()
        skipped = list(getattr(st, "skipped_devices", None) or [])
        if not skipped:
            self.open_debug_tool(None, initial_tab=3)
            return
        if len(skipped) == 1:
            vp = skipped[0].get("vidpid", "")
            self.open_debug_tool(None, select_vidpid=vp, initial_tab=3)
            return
        modal = neu_ui.NeuModalOverlay(
            self.root,
            title=f"另有 {len(skipped)} 台 HID 设备暂未识别",
            subtitle="以下设备存在厂商或读写接口但未命中内置协议，可一键预填并标定",
            max_width_dp=560,
            on_close=lambda: setattr(self, "_wizard_modal", None),
            close_on_backdrop=True,
        )
        self._wizard_modal = modal
        for item in skipped[:12]:
            vp = str(item.get("vidpid", ""))
            card = neu_ui.NeuCard(modal.body, radius=14, pad=0, inner_pad=(12, 8), off=3, box_r=2, inset=True)
            card.pack(fill="x", pady=(0, neu_ui.sx(6, modal.body)))
            neu_ui.NeuButton(
                card.content, text=f"标定 ({vp}) →",
                command=lambda v=vp: (modal.close(), self.open_debug_tool(None, select_vidpid=v, initial_tab=3)),
                accent=True, radius=12, pad=4, padx=10, pady=3, font_size=neu_ui.FONT_CAPTION,
            ).pack(side="right")
            tk.Label(
                card.content, text=f"{item.get('name', vp)} ({vp})",
                bg=neu_ui.BASE, fg=neu_ui.TEXT, font=neu_ui.ui_font(neu_ui.FONT_BODY, "bold", root=modal.body), anchor="w",
            ).pack(fill="x")
            tk.Label(
                card.content, text=f"{item.get('reason', '')} · 接口数 {item.get('iface_count', 1)}",
                bg=neu_ui.BASE, fg=neu_ui.TEXT_DIM, font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=modal.body), anchor="w",
            ).pack(fill="x")

    def _on_install_links(self) -> None:
        try:
            res = install_desktop_links()
            msg = "已创建 " + ("、".join(os.path.basename(p.split(": ")[-1]) for p in res) or "桌面快捷方式")
            self._set_modal_note(msg, SUCCESS)
        except Exception as e:
            _log_app_error("install_desktop_links", str(e))
            self._set_modal_note(f"创建桌面快捷方式失败：{e}", RED_DANGER)

    def open_debug_tool(
        self,
        reading: Optional[devices.PeripheralReading],
        *,
        select_vidpid: str = "",
        initial_tab: int = 0,
    ) -> None:
        if self._debug_modal is not None:
            return

        prev_settings = self._settings_modal
        reuse_dim = getattr(prev_settings, "_dim_photo", None) if prev_settings is not None else None
        reuse_stages = list(getattr(prev_settings, "_dim_stages", []) or []) if prev_settings is not None else None
        opened_from_settings = prev_settings is not None
        if not opened_from_settings:
            try:
                if str(self.root.state()) == "withdrawn":
                    self.show_main_window()
                    self.root.update()
            except Exception:
                pass

        select_vp = select_vidpid.upper().strip() or (
            f"{reading.vid:04X}:{reading.pid:04X}"
            if (reading and reading.vid is not None and reading.pid is not None)
            else ""
        )

        import app as debug_app

        ws_holder: list[Any] = []

        def _on_debug_closed() -> None:
            saved = bool(ws_holder and ws_holder[0].saved_protocol)
            if ws_holder:
                try:
                    ws_holder[0].cleanup()
                except Exception:
                    pass
            self._debug_modal = None
            if saved:
                self.refresh_now()
            if (
                opened_from_settings
                and not self._closing
                and not getattr(self, "_suppress_settings_return", False)
                and self.root.winfo_exists()
            ):
                self.open_settings_modal(dim_photo=reuse_dim, dim_stages=reuse_stages)
                return
            try:
                self.root.focus_set()
            except Exception:
                pass

        modal = neu_ui.NeuModalOverlay(
            self.root,
            title="调试与标定",
            subtitle="HID / 2.4G 接口遍历 · 原始探测 · 协议标定",
            max_width_dp=890,
            min_height_dp=540,
            dim_photo=reuse_dim,
            dim_stages=reuse_stages,
            fade_scrim_on_close=not opened_from_settings,
            on_close=_on_debug_closed,
            close_on_backdrop=True,
        )
        self._debug_modal = modal
        ws = debug_app.DebugWorkspaceFrame(
            modal.body,
            select_vidpid=select_vp,
            compact=True,
            show_header=False,
            initial_tab=initial_tab,
        )
        ws_holder.append(ws)
        ws.pack(fill="both", expand=True)

        # 等新调试浮层与遮罩同步创建完成之后，再平滑撤销旧设置层，彻底消除闪烁
        if prev_settings is not None:
            try:
                modal.update_idletasks()
            except Exception:
                pass
            if self._settings_modal is prev_settings:
                self._settings_modal = None
            prev_settings.close()


def print_cli_readings(as_json: bool = False) -> int:
    cfg = tray_common.load_config()
    res = devices.scan_peripherals(cfg, include_hidden=False, save_cache=True)
    if as_json:
        payload = [
            {
                "key": r.key,
                "name": r.name,
                "role": r.role,
                "vidpid": r.vidpid_text,
                "connected": r.connected,
                "offline": r.offline,
                "level": r.level,
                "raw_level": r.raw_level,
                "interpolated": r.interpolated,
                "voltage_mv": r.voltage_mv,
                "voltage_text": r.voltage_text,
                "charging": r.charging,
                "full": r.full,
                "power_state": r.power_state,
                "estimate_hours": r.estimate_hours,
                "discharge_rate_per_h": r.discharge_rate_per_h,
                "charge_eta_minutes": r.charge_eta_minutes,
                "estimate_text": r.estimate_text,
                "connection": r.connection,
                "transport": r.transport,
                "parts": list(getattr(r, "parts", None) or []),
                "source": r.source,
                "detail": r.detail,
                "error": r.error,
                "last_read_error": r.last_read_error,
            }
            for r in res
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not res:
        print("未检测到可识别的无线外设。")
        return 0
    for r in res:
        lvl = r.level_text
        role = devices.ROLE_LABEL.get(r.role, r.role)
        info = r.detail if r.level is not None else r.error
        print(f"[{role}] {r.name} ({r.vidpid_text}) -> {lvl} | {r.status_text} | {r.estimate_text}")
        if info:
            print(f"    {info}")
    return 0


def main(start_minimized_default: bool = False) -> int:
    ap = argparse.ArgumentParser(description="我的 2.4G / 无线外设电量面板")
    ap.add_argument("--version", "-V", action="store_true", help="输出软件版本信息并退出")
    ap.add_argument("--once", action="store_true", help="扫描并输出当前所有外设电量后退出")
    ap.add_argument("--json", action="store_true", help="与 --once 同用，输出 JSON")
    ap.add_argument("--autostart", choices=["on", "off"], help="配置开机自启")
    ap.add_argument("--install-links", action="store_true", help="在桌面创建快捷方式")
    ap.add_argument("--minimized", action="store_true", help="启动时最小化到系统托盘图标")
    ap.add_argument("--status", action="store_true", help="查看运行中托盘状态")
    ap.add_argument("--refresh", action="store_true", help="通知运行中程序立即刷新")
    ap.add_argument("--quit", action="store_true", help="退出运行中的驻留进程")
    args = ap.parse_args()

    if args.version:
        if args.json:
            print(
                json.dumps(
                    {
                        "name": tray_common.APP_NAME,
                        "version": tray_common.APP_VERSION,
                        "build_date": tray_common.APP_BUILD_DATE,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(f"{tray_common.APP_NAME} v{tray_common.APP_VERSION} ({tray_common.APP_BUILD_DATE})")
        return 0

    if args.autostart:
        ok, msg = tray_common.set_autostart(args.autostart == "on")
        print(msg)
        return 0 if (ok == (args.autostart == "on")) else 1
    if args.install_links:
        for line in install_desktop_links():
            print(line)
        return 0
    if args.status:
        hwnd = tray_common.find_tray_hwnd()
        pids = tray_common.find_tray_pids()
        st = tray_common.read_tray_state()
        print(f"运行中: {bool(hwnd or pids)} | hwnd=0x{hwnd:X} | pids={pids}")
        for d in st.get("devices", []):
            if d.get("level") is not None and not d.get("offline"):
                lvl = f"~{d.get('level')}%" if d.get("interpolated") else f"{d.get('level')}%"
            else:
                lvl = "--"
            print(f"  - {d.get('name')}: {lvl} ({d.get('status')})")
        return 0
    if args.refresh:
        ok = tray_common.notify_tray_refresh()
        print("已发送刷新信号" if ok else "后台进程未运行")
        return 0 if ok else 1
    if args.quit:
        ok = tray_common.stop_tray()
        print("程序已退出" if ok else "未找到驻留进程或退出超时")
        return 0 if ok else 1
    if args.once or args.json:
        return print_cli_readings(as_json=args.json)

    # 单实例互斥：再次点击图标或快捷方式时直接唤起已有主窗口
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = k32.CreateMutexW(None, False, tray_common.MUTEX_NAME)
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if not (args.minimized or start_minimized_default):
            tray_common.notify_tray_show()
        else:
            tray_common.notify_tray_refresh()
        if mutex:
            k32.CloseHandle(mutex)
        return 0

    try:
        root = tk.Tk()
        BatteryPanelApp(
            root,
            start_minimized=bool(args.minimized or start_minimized_default),
        )
        root.mainloop()
    finally:
        if mutex:
            k32.CloseHandle(mutex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
