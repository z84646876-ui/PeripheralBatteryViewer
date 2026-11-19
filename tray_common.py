# -*- coding: utf-8 -*-
"""tray_common.py —— 托盘后台进程管理、配置读写与开机自启（纯 ctypes，零第三方依赖）"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from datetime import datetime
from typing import Any, Callable, Optional

import win_shell

__version__ = "1.1.2"
APP_VERSION = __version__
APP_BUILD_DATE = "2026-11-20"
APP_DISPLAY_NAME = "外设电量查看器"
APP_NAME = "PeripheralBatteryViewer"

IS_FROZEN = bool(getattr(sys, "frozen", False))
CODE_DIR = (
    getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    if IS_FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)
BASE_DIR = (
    os.path.dirname(os.path.abspath(sys.executable))
    if IS_FROZEN
    else os.path.dirname(os.path.abspath(__file__))
)


def get_appdata_dir() -> str:
    """返回标准 %APPDATA%\\PeripheralBatteryViewer 数据目录路径。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(os.path.abspath(base), APP_NAME)


def _is_dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, f".write_probe_{os.getpid()}")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def is_portable_mode() -> bool:
    """当前有效数据目录是否位于程序所在目录（便携模式）。"""
    return os.path.normcase(os.path.abspath(DATA_DIR)) == os.path.normcase(os.path.abspath(BASE_DIR))


def _resolve_initial_data_dir() -> str:
    env_dir = (os.environ.get("PBV_DATA_DIR") or "").strip()
    if env_dir:
        target = os.path.abspath(env_dir)
        try:
            os.makedirs(target, exist_ok=True)
        except OSError:
            pass
        return target

    redirect_file = os.path.join(BASE_DIR, "data_dir_redirect.txt")
    if os.path.isfile(redirect_file):
        try:
            with open(redirect_file, "r", encoding="utf-8") as f:
                redir = f.read().strip()
            if redir:
                target = os.path.abspath(redir)
                os.makedirs(target, exist_ok=True)
                return target
        except OSError:
            pass

    portable_markers = (
        os.path.join(BASE_DIR, "portable.flag"),
        os.path.join(BASE_DIR, "tray_config.json"),
        os.path.join(BASE_DIR, "battery_history.json"),
    )
    if any(os.path.exists(p) for p in portable_markers) and _is_dir_writable(BASE_DIR):
        return BASE_DIR

    # 仅测试/调试环境或源码目录未启用重定向时，源码目录有配置文件则沿用 BASE_DIR；安装版优先落入 %APPDATA%
    if not IS_FROZEN and _is_dir_writable(BASE_DIR):
        return BASE_DIR

    appdata = get_appdata_dir()
    try:
        os.makedirs(appdata, exist_ok=True)
        return appdata
    except OSError:
        return BASE_DIR


DATA_DIR = _resolve_initial_data_dir()
CONFIG_FILE = os.path.join(DATA_DIR, "tray_config.json")
STATE_FILE = os.path.join(DATA_DIR, "tray_state.json")
QUIT_FLAG_FILE = os.path.join(DATA_DIR, "tray_quit.flag")

MAIN_SCRIPT = os.path.join(CODE_DIR, "main.py")
PANEL_SCRIPT = os.path.join(CODE_DIR, "my_devices.py")
TRAY_SCRIPT = os.path.join(CODE_DIR, "tray_app.py")
DEBUG_SCRIPT = os.path.join(CODE_DIR, "app.py")


def app_icon_path() -> Optional[str]:
    for cand in (
        os.path.join(CODE_DIR, "assets", "app_icon.ico"),
        os.path.join(BASE_DIR, "assets", "app_icon.ico"),
        os.path.join(BASE_DIR, "app_icon.ico"),
    ):
        if os.path.isfile(cand):
            return cand
    return None


def build_launch_args(
    mode: str, extra: Optional[list[str]] = None, console: bool = False
) -> list[str]:
    """根据源码/单文件 EXE 环境构建启动子进程命令行参数。"""
    tail = list(extra or [])
    if IS_FROZEN:
        if mode == "panel":
            return [sys.executable] + tail
        if mode == "minimized":
            return [sys.executable, "--minimized"] + tail
        if mode == "tray":
            return [sys.executable, "--tray"] + tail
        if mode == "watchdog":
            return [sys.executable, "--watchdog"] + tail
        if mode == "debug":
            return [sys.executable, "--debug-ui"] + tail
        return [sys.executable] + tail

    exe = sys.executable if console else pythonw_exe()
    script_map = {
        "panel": PANEL_SCRIPT,
        "minimized": PANEL_SCRIPT,
        "tray": TRAY_SCRIPT,
        "watchdog": MAIN_SCRIPT,
        "debug": DEBUG_SCRIPT,
    }
    script = script_map.get(mode, PANEL_SCRIPT)
    if mode == "minimized":
        return [exe, script, "--minimized"] + tail
    if mode == "watchdog":
        return [exe, MAIN_SCRIPT, "--watchdog"] + tail
    return [exe, script] + tail


UI_TITLE = "我的无线外设电量"
WND_CLASS = "PeripheralBatteryTrayWnd"
MUTEX_NAME = "Local\\PeripheralBatteryViewerTrayMutex"
WATCHDOG_MUTEX_NAME = "Local\\PeripheralBatteryViewerWatchdogMutex"
STARTUP_LNK_NAME = "2.4G外设电量托盘.lnk"

DEFAULT_CAPACITY_MAH_BY_ROLE: dict[str, int] = {
    "mouse": 500,
    "keyboard": 4000,
    "headset": 400,
    "controller": 1000,
    "other": 500,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "interval": 120,
    "bg_interval": 300,
    "notify_enabled": True,
    "notify_full_charge": True,
    "notify_threshold": 20,
    "notify_cooldown_min": 30,
    "notify_alpha": 60,
    "minimize_on_close": True,
    "watchdog_enabled": True,
    "tray_icon_style": "digit",
    "tray_visibility": "always",
    "tray_hover_mode": "grid",
    "history_retention_days": 7,
    "theme_mode": "light",
    "font_scale_mode": "standard",
    "compact_view": False,
    "group_by_role": False,
    "collapsed_roles": [],
    "sort_mode": "default",
    "window_geometry": "",
    "window_maximized": False,
    "tray_guide_shown": False,
    "footer_help_views": 0,
    "update_url": "",
    "hidden_devices": [],
    "pinned": [],
    "ignored_devices": [],
    "manual_devices": [],
    "device_roles": {},
    "device_names": {},
    "device_thresholds": {},
    "device_capacities_mah": {},
    "known_devices": {},
    "last_readings": {},
    "log_level": "INFO",
}

VALID_SORT_MODES = ("default", "low_first", "name", "recent")
VALID_TRAY_STYLES = ("digit", "ring", "dot", "lowest_device")
VALID_TRAY_VISIBILITY = ("always", "alert_only")
VALID_TRAY_HOVER_MODES = ("grid", "native")
VALID_RETENTION_DAYS = (1, 3, 7, 14, 30)
VALID_ROLES = ("mouse", "keyboard", "headset", "controller", "other")
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
_VID_PID_RE = re.compile(
    r"(?:VID[_\s:=]*)?(?:0x)?([0-9a-fA-F]{4})\s*[:\-&_\s]+\s*(?:PID[_\s:=]*)?(?:0x)?([0-9a-fA-F]{4})",
    re.IGNORECASE,
)


def normalize_vid_pid(raw: str) -> Optional[str]:
    """将用户输入或文本片段规范化为 'VVVV:PPPP' 大写十六进制；无效返回 None。"""
    s = str(raw or "").strip()
    if not s:
        return None
    m = _VID_PID_RE.search(s)
    if m:
        return f"{m.group(1).upper()}:{m.group(2).upper()}"
    return None


def parse_vid_pid(raw: str) -> Optional[tuple[int, int]]:
    norm = normalize_vid_pid(raw)
    if not norm:
        return None
    v_str, p_str = norm.split(":")
    return int(v_str, 16), int(p_str, 16)


def is_windows_dark_mode() -> bool:
    """读取 Windows 系统应用明暗主题设置（AppsUseLightTheme == 0 表示深色）。"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return int(val) == 0
    except Exception:
        return False


# ==============================================================================
# 配置 / 运行时状态
# ==============================================================================

import shutil
import urllib.request

_IO_WARN_LOCK = threading.Lock()
_IO_WARNINGS: list[dict[str, Any]] = []
_LAST_IO_WARNING: Optional[dict[str, Any]] = None


def record_io_warning(kind: str, message: str, path: str = "", recovered: bool = False) -> None:
    """Record a persistence I/O or corruption recovery warning for UI notification."""
    global _LAST_IO_WARNING
    entry = {
        "kind": str(kind),
        "message": str(message),
        "path": str(path),
        "recovered": bool(recovered),
        "ts": time.time(),
    }
    with _IO_WARN_LOCK:
        _LAST_IO_WARNING = entry
        _IO_WARNINGS.append(entry)
        if len(_IO_WARNINGS) > 20:
            del _IO_WARNINGS[:-20]


def pop_io_warning() -> Optional[dict[str, Any]]:
    """Pop the oldest pending persistence warning, or None when empty."""
    with _IO_WARN_LOCK:
        if not _IO_WARNINGS:
            return None
        return _IO_WARNINGS.pop(0)


def get_last_io_warning() -> Optional[dict[str, Any]]:
    """Return a copy of the latest persistence warning without removing it."""
    with _IO_WARN_LOCK:
        return dict(_LAST_IO_WARNING) if _LAST_IO_WARNING else None


def get_data_dir() -> str:
    """Return the active runtime data directory."""
    return DATA_DIR


def set_data_dir(path: Optional[str] = None) -> str:
    """Redirect config, state, logs, history, alerts, quit flag, and protocol files to `path`."""
    global DATA_DIR, CONFIG_FILE, STATE_FILE, LOG_FILE, QUIT_FLAG_FILE
    if path is None:
        target = _resolve_initial_data_dir()
    else:
        target = os.path.abspath(path)
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        pass
    DATA_DIR = target
    CONFIG_FILE = os.path.join(target, "tray_config.json")
    STATE_FILE = os.path.join(target, "tray_state.json")
    QUIT_FLAG_FILE = os.path.join(target, "tray_quit.flag")
    cur_log = globals().get("LOG_FILE")
    log_name = os.path.basename(cur_log) if isinstance(cur_log, str) and cur_log else "app_error.log"
    LOG_FILE = os.path.join(target, log_name)
    # Sync already-imported sibling modules that resolved paths at import time.
    bh = sys.modules.get("battery_history")
    if bh is not None and hasattr(bh, "HISTORY_FILE"):
        bh_name = os.path.basename(getattr(bh, "HISTORY_FILE", "") or "battery_history.json")
        bh.HISTORY_FILE = os.path.join(target, bh_name)
    if bh is not None and hasattr(bh, "ALERT_HISTORY_FILE"):
        ah_name = os.path.basename(getattr(bh, "ALERT_HISTORY_FILE", "") or "alert_history.json")
        bh.ALERT_HISTORY_FILE = os.path.join(target, ah_name)
    bat = sys.modules.get("battery")
    if bat is not None and hasattr(bat, "USER_PROTO_FILE"):
        proto_name = os.path.basename(getattr(bat, "USER_PROTO_FILE", "") or "protocols_user.json")
        bat.USER_PROTO_FILE = os.path.join(target, proto_name)
    dev = sys.modules.get("devices")
    if dev is not None and hasattr(dev, "CACHE_FILE"):
        cache_name = os.path.basename(getattr(dev, "CACHE_FILE", "") or "device_cache.json")
        dev.CACHE_FILE = os.path.join(target, cache_name)
    return target


def migrate_data_dir(target_dir: Optional[str] = None, *, write_redirect: bool = True) -> dict[str, Any]:
    """将当前数据目录下的配置、历史、告警日志与自定义协议原子迁移至目标目录（如 %APPDATA%）。"""
    dst = os.path.abspath(target_dir or get_appdata_dir())
    src = os.path.abspath(DATA_DIR)
    os.makedirs(dst, exist_ok=True)
    if os.path.normcase(src) == os.path.normcase(dst):
        return {"ok": True, "target": dst, "copied": [], "message": "数据目录已在目标位置"}

    copied: list[str] = []
    for fname in (
        "tray_config.json",
        "tray_config.json.bak",
        "battery_history.json",
        "battery_history.json.bak",
        "alert_history.json",
        "protocols_user.json",
    ):
        sp = os.path.join(src, fname)
        dp = os.path.join(dst, fname)
        if os.path.isfile(sp):
            tmp = f"{dp}.mig.{os.getpid()}"
            shutil.copy2(sp, tmp)
            os.replace(tmp, dp)
            copied.append(fname)

    if write_redirect:
        redir = os.path.join(BASE_DIR, "data_dir_redirect.txt")
        try:
            with open(redir, "w", encoding="utf-8") as f:
                f.write(dst)
        except OSError:
            pass
    set_data_dir(dst)
    return {"ok": True, "target": dst, "copied": copied, "message": f"已迁移 {len(copied)} 个文件至 {dst}"}


def _parse_semver(ver: str) -> tuple[int, ...]:
    nums: list[int] = []
    for part in str(ver or "").lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        nums.append(int(digits))
    return tuple(nums) if nums else (0,)


def check_update_status(timeout_s: float = 10.0, update_url: Optional[str] = None) -> dict[str, Any]:
    """查询更新清单（支持自定义 JSON 清单与 GitHub Releases API），返回完整版本、下载地址与 SHA-256。"""
    if update_url is not None:
        url = str(update_url).strip()
    else:
        env_url = (os.environ.get("PBV_UPDATE_URL") or "").strip()
        if env_url:
            url = env_url
        else:
            try:
                cfg_url = ""
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        raw_c = json.load(f)
                    if isinstance(raw_c, dict):
                        cfg_url = str(raw_c.get("update_url") or "").strip()
                url = cfg_url
            except Exception:
                url = ""

    if not url:
        return {
            "ok": True,
            "status": "local_only",
            "current_version": APP_VERSION,
            "latest_version": APP_VERSION,
            "build_date": APP_BUILD_DATE,
            "update_available": False,
            "download_url": "",
            "sha256": "",
            "notes": "",
            "message": f"当前已是本地发布版本 v{APP_VERSION}（构建日期 {APP_BUILD_DATE}，未配置在线更新源）",
        }
    if not (url.startswith("https://") or url.startswith("http://")):
        return {
            "ok": False,
            "status": "invalid_url",
            "current_version": APP_VERSION,
            "latest_version": APP_VERSION,
            "build_date": APP_BUILD_DATE,
            "update_available": False,
            "download_url": "",
            "sha256": "",
            "notes": "",
            "message": "更新源地址无效，仅支持 http:// 或 https://",
        }
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"PeripheralBatteryViewer/{APP_VERSION}",
                "Accept": "application/vnd.github+json, application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=max(0.2, float(timeout_s))) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        if not isinstance(payload, dict):
            raise ValueError("更新清单格式不是 JSON 对象")

        # 兼容 GitHub Releases API (/releases/latest) 与自定义 manifest JSON
        if "tag_name" in payload and "version" not in payload and "latest_version" not in payload:
            latest_raw = str(payload.get("tag_name") or APP_VERSION).strip()
            latest = latest_raw.lstrip("vV") or APP_VERSION
            notes = str(payload.get("body") or "").strip()
            html_url = str(payload.get("html_url") or "").strip()
            dl_url = ""
            sha256_hex = ""
            assets = payload.get("assets") or []
            if isinstance(assets, list):
                exe_assets = [a for a in assets if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".exe")]
                chosen = exe_assets[0] if exe_assets else (assets[0] if assets and isinstance(assets[0], dict) else None)
                if chosen:
                    dl_url = str(chosen.get("browser_download_url") or "").strip()
                    digest = str(chosen.get("digest") or "").strip()
                    if digest.lower().startswith("sha256:"):
                        sha256_hex = digest.split(":", 1)[1].strip().lower()
        else:
            latest = str(payload.get("version") or payload.get("latest_version") or APP_VERSION).strip().lstrip("vV")
            notes = str(payload.get("notes") or "").strip()
            dl_url = str(payload.get("url") or payload.get("download_url") or "").strip()
            sha256_hex = str(payload.get("sha256") or "").strip().lower()
            html_url = str(payload.get("html_url") or "").strip()

        avail = _parse_semver(latest) > _parse_semver(APP_VERSION)
        msg = (
            f"发现新版本 v{latest}（当前 v{APP_VERSION}）"
            if avail
            else f"当前已安装最新版本 v{APP_VERSION}"
        )
        return {
            "ok": True,
            "status": "update_available" if avail else "up_to_date",
            "current_version": APP_VERSION,
            "latest_version": latest,
            "build_date": APP_BUILD_DATE,
            "update_available": avail,
            "notes": notes,
            "download_url": dl_url,
            "sha256": sha256_hex,
            "html_url": html_url,
            "message": msg,
        }
    except Exception as exc:
        err_s = str(exc)
        if "timed out" in err_s.lower() or "timeout" in err_s.lower():
            msg = f"连接更新源超时（如访问 GitHub 较慢请检查网络/代理后重试）：{err_s}"
        else:
            msg = f"检查更新失败：{err_s}"
        return {
            "ok": False,
            "status": "error",
            "current_version": APP_VERSION,
            "latest_version": APP_VERSION,
            "build_date": APP_BUILD_DATE,
            "update_available": False,
            "download_url": "",
            "sha256": "",
            "notes": "",
            "message": msg,
        }


def download_and_verify_update(
    download_url: str,
    expected_sha256: str,
    dest_path: str,
    *,
    timeout_s: float = 60.0,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> dict[str, Any]:
    """流式下载安装包/可执行文件并强制校验 SHA-256；校验失败自动删除临时文件。"""
    url = str(download_url or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        return {"ok": False, "path": "", "sha256": "", "bytes": 0, "message": "下载地址无效，仅支持 http:// 或 https://"}
    exp = str(expected_sha256 or "").strip().lower()
    if exp.startswith("sha256:"):
        exp = exp.split(":", 1)[1].strip()
    if len(exp) != 64 or not all(c in "0123456789abcdef" for c in exp):
        return {"ok": False, "path": "", "sha256": "", "bytes": 0, "message": "更新清单缺少有效 SHA-256 校验值，已拒绝下载"}

    dest_abs = os.path.abspath(dest_path)
    parent = os.path.dirname(dest_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)
    part_path = f"{dest_abs}.part.{os.getpid()}"
    hasher = hashlib.sha256()
    total_written = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": f"PeripheralBatteryViewer/{APP_VERSION}"})
        with urllib.request.urlopen(req, timeout=max(2.0, float(timeout_s))) as resp:
            try:
                content_len = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                content_len = 0
            with open(part_path, "wb") as wf:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    wf.write(chunk)
                    hasher.update(chunk)
                    total_written += len(chunk)
                    if progress_cb is not None:
                        try:
                            progress_cb(total_written, content_len)
                        except Exception:
                            pass
        actual_hex = hasher.hexdigest().lower()
        if actual_hex != exp:
            try:
                os.remove(part_path)
            except OSError:
                pass
            return {
                "ok": False,
                "path": "",
                "sha256": actual_hex,
                "bytes": total_written,
                "message": f"SHA-256 校验失败（期望 {exp[:12]}…，实际 {actual_hex[:12]}…）",
            }
        os.replace(part_path, dest_abs)
        return {
            "ok": True,
            "path": dest_abs,
            "sha256": actual_hex,
            "bytes": total_written,
            "message": f"下载并校验通过（{total_written} 字节）",
        }
    except Exception as exc:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        return {"ok": False, "path": "", "sha256": "", "bytes": total_written, "message": f"下载失败：{exc}"}


def prepare_and_launch_updater(
    new_exe_path: str,
    *,
    current_exe: Optional[str] = None,
    wait_pid: Optional[int] = None,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """生成带备份与失败自动回滚的无缝替换批处理脚本；dry_run=True 时仅生成并返回路径。"""
    src_new = os.path.abspath(new_exe_path)
    if not os.path.isfile(src_new):
        return False, "待更新文件不存在"
    target_exe = os.path.abspath(current_exe or sys.executable)
    pid = int(wait_pid if wait_pid is not None else os.getpid())
    bak_exe = f"{target_exe}.old"
    bat_path = os.path.join(tempfile.gettempdir(), f"pbv_updater_{pid}.cmd")
    script = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f"set \"WAIT_PID={pid}\"\r\n"
        f"set \"SRC_NEW={src_new}\"\r\n"
        f"set \"DST_EXE={target_exe}\"\r\n"
        f"set \"BAK_EXE={bak_exe}\"\r\n"
        "for /l %%i in (1,1,30) do (\r\n"
        "  tasklist /FI \"PID eq %WAIT_PID%\" 2>nul | find \"%WAIT_PID%\" >nul\r\n"
        "  if errorlevel 1 goto :ready\r\n"
        "  timeout /t 1 /nobreak >nul\r\n"
        ")\r\n"
        ":ready\r\n"
        "if exist \"%BAK_EXE%\" del /f /q \"%BAK_EXE%\" >nul 2>nul\r\n"
        "if exist \"%DST_EXE%\" move /y \"%DST_EXE%\" \"%BAK_EXE%\" >nul 2>nul\r\n"
        "move /y \"%SRC_NEW%\" \"%DST_EXE%\" >nul 2>nul\r\n"
        "if not exist \"%DST_EXE%\" (\r\n"
        "  if exist \"%BAK_EXE%\" move /y \"%BAK_EXE%\" \"%DST_EXE%\" >nul 2>nul\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "start \"\" \"%DST_EXE%\"\r\n"
        "timeout /t 3 /nobreak >nul\r\n"
        "for %%A in (\"%DST_EXE%\") do set \"EXE_NAME=%%~nxA\"\r\n"
        "tasklist /FI \"IMAGENAME eq %EXE_NAME%\" 2>nul | find /i \"%EXE_NAME%\" >nul\r\n"
        "if errorlevel 1 (\r\n"
        "  if exist \"%BAK_EXE%\" (\r\n"
        "    del /f /q \"%DST_EXE%\" >nul 2>nul\r\n"
        "    move /y \"%BAK_EXE%\" \"%DST_EXE%\" >nul 2>nul\r\n"
        "    start \"\" \"%DST_EXE%\"\r\n"
        "  )\r\n"
        ")\r\n"
        "del /f /q \"%~f0\" >nul 2>nul\r\n"
    )
    try:
        with open(bat_path, "w", encoding="utf-8") as f:
            f.write(script)
    except OSError as exc:
        return False, f"生成更新脚本失败：{exc}"

    if not dry_run:
        try:
            subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
            )
        except Exception as exc:
            return False, f"启动更新程序失败：{exc}"
    return True, bat_path


def _backup_file_before_replace(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        if os.path.getsize(path) <= 0:
            return
        bak = f"{path}.bak"
        bak_tmp = f"{path}.bak.tmp.{os.getpid()}"
        shutil.copy2(path, bak_tmp)
        os.replace(bak_tmp, bak)
    except OSError:
        try:
            bak_tmp = f"{path}.bak.tmp.{os.getpid()}"
            if os.path.exists(bak_tmp):
                os.remove(bak_tmp)
        except OSError:
            pass


def _atomic_write_json(path: str, data: Any, backup: bool = False) -> bool:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass
    if backup:
        _backup_file_before_replace(path)
    tmp = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _read_json_dict(path: str) -> tuple[bool, Optional[dict[str, Any]], Optional[Exception]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return False, None, ValueError("JSON root is not an object")
        return True, raw, None
    except Exception as exc:
        return False, None, exc


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        ok, raw, exc = _read_json_dict(CONFIG_FILE)
        if ok and raw is not None:
            cfg.update(raw)
        else:
            bak_path = f"{CONFIG_FILE}.bak"
            recovered = False
            if os.path.exists(bak_path):
                ok_bak, raw_bak, _ = _read_json_dict(bak_path)
                if ok_bak and raw_bak is not None:
                    cfg.update(raw_bak)
                    recovered = True
            if recovered:
                _atomic_write_json(CONFIG_FILE, cfg, backup=False)
                msg = f"配置文件已损坏（{exc}），已从备份自动恢复"
                record_io_warning("config_recovered", msg, path=CONFIG_FILE, recovered=True)
                try:
                    log_event("WARNING", "config_recovered", msg, path=CONFIG_FILE)
                except Exception:
                    pass
            else:
                msg = f"配置文件损坏且无可用备份（{exc}），已回退默认配置"
                record_io_warning("config_reset", msg, path=CONFIG_FILE, recovered=False)
                try:
                    log_event("ERROR", "config_reset", msg, path=CONFIG_FILE)
                except Exception:
                    pass
    for k in ("hidden_devices", "pinned", "ignored_devices", "manual_devices", "collapsed_roles"):
        if not isinstance(cfg.get(k), list):
            cfg[k] = []
        else:
            seen: list[str] = []
            for item in cfg[k]:
                s = str(item or "").strip()
                if k == "manual_devices":
                    s = normalize_vid_pid(s) or ""
                if s and s not in seen:
                    seen.append(s)
            cfg[k] = seen
    if isinstance(cfg.get("pinned_devices"), list) and not cfg["pinned"]:
        cfg["pinned"] = [str(x).strip() for x in cfg["pinned_devices"] if str(x).strip()]
    for k in ("device_names", "last_readings", "known_devices", "device_roles", "device_thresholds", "device_capacities_mah"):
        if not isinstance(cfg.get(k), dict):
            cfg[k] = {}
    if cfg["device_roles"]:
        norm_roles: dict[str, str] = {}
        for k, v in cfg["device_roles"].items():
            rv = "controller" if str(v) == "gamepad" else str(v)
            if rv in VALID_ROLES:
                norm_roles[str(k)] = rv
        cfg["device_roles"] = norm_roles
    if cfg["device_thresholds"]:
        norm_th: dict[str, int] = {}
        for k, v in cfg["device_thresholds"].items():
            try:
                norm_th[str(k)] = max(5, min(50, int(v)))
            except Exception:
                continue
        cfg["device_thresholds"] = norm_th
    if cfg["device_capacities_mah"]:
        norm_cap: dict[str, int] = {}
        for k, v in cfg["device_capacities_mah"].items():
            try:
                norm_cap[str(k)] = max(50, min(20000, int(v)))
            except Exception:
                continue
        cfg["device_capacities_mah"] = norm_cap
    if cfg.get("theme_mode") not in ("light", "dark", "system"):
        cfg["theme_mode"] = "light"
    if cfg.get("font_scale_mode") not in ("standard", "large", "xlarge"):
        cfg["font_scale_mode"] = "standard"
    if cfg.get("sort_mode") not in VALID_SORT_MODES:
        cfg["sort_mode"] = "default"
    if cfg.get("tray_icon_style") not in VALID_TRAY_STYLES:
        cfg["tray_icon_style"] = "digit"
    if cfg.get("tray_visibility") not in VALID_TRAY_VISIBILITY:
        cfg["tray_visibility"] = "always"
    if cfg.get("tray_hover_mode") not in VALID_TRAY_HOVER_MODES:
        cfg["tray_hover_mode"] = "grid"
    try:
        ret_d = int(cfg.get("history_retention_days", 7))
        cfg["history_retention_days"] = ret_d if ret_d in VALID_RETENTION_DAYS else 7
    except Exception:
        cfg["history_retention_days"] = 7
    lvl = str(cfg.get("log_level", "INFO") or "INFO").upper()
    if lvl == "WARNING":
        lvl = "WARN"
    cfg["log_level"] = lvl if lvl in VALID_LOG_LEVELS else "INFO"
    cfg["compact_view"] = bool(cfg.get("compact_view", False))
    cfg["group_by_role"] = bool(cfg.get("group_by_role", False))
    cfg["notify_full_charge"] = bool(cfg.get("notify_full_charge", True))
    cfg["watchdog_enabled"] = bool(cfg.get("watchdog_enabled", True))
    cfg["update_url"] = str(cfg.get("update_url") or "").strip()
    try:
        cfg["interval"] = max(10, min(3600, int(cfg.get("interval", 120))))
    except Exception:
        cfg["interval"] = 120
    try:
        cfg["bg_interval"] = max(30, min(7200, int(cfg.get("bg_interval", 300))))
    except Exception:
        cfg["bg_interval"] = 300
    try:
        cfg["notify_threshold"] = max(5, min(50, int(cfg.get("notify_threshold", 20))))
    except Exception:
        cfg["notify_threshold"] = 20
    try:
        cfg["notify_cooldown_min"] = max(1, min(1440, int(cfg.get("notify_cooldown_min", 30))))
    except Exception:
        cfg["notify_cooldown_min"] = 30
    try:
        cfg["notify_alpha"] = max(40, min(100, int(cfg.get("notify_alpha", 60))))
    except Exception:
        cfg["notify_alpha"] = 60
    try:
        cfg["footer_help_views"] = max(0, int(cfg.get("footer_help_views", 0)))
    except Exception:
        cfg["footer_help_views"] = 0
    cfg.pop("tray_max_icons", None)
    return cfg


def save_config(cfg: dict[str, Any]) -> bool:
    to_save = dict(cfg)
    to_save.pop("tray_max_icons", None)
    ok = _atomic_write_json(CONFIG_FILE, to_save, backup=True)
    if not ok:
        record_io_warning("config_save_failed", "保存配置文件失败，请检查磁盘空间或写入权限", path=CONFIG_FILE, recovered=False)
    try:
        set_log_level(str(to_save.get("log_level", "INFO")))
    except Exception:
        pass
    return ok


def get_device_threshold(cfg: Optional[dict[str, Any]], key: str) -> int:
    """获取单设备低电告警阈值（未单独设定时回退全局 notify_threshold）。"""
    c = cfg if isinstance(cfg, dict) else {}
    global_th = 20
    try:
        global_th = max(5, min(50, int(c.get("notify_threshold", 20))))
    except Exception:
        pass
    per = c.get("device_thresholds") or {}
    if isinstance(per, dict) and key in per:
        try:
            return max(5, min(50, int(per[key])))
        except Exception:
            pass
    return global_th


def get_device_capacity_mah(cfg: Optional[dict[str, Any]], key: str, role: str = "other") -> tuple[int, bool]:
    """返回 (标称容量 mAh, 是否为用户显式配置)；未设置时按设备类型给出参考值。"""
    c = cfg if isinstance(cfg, dict) else {}
    per = c.get("device_capacities_mah") or {}
    if isinstance(per, dict) and key in per:
        try:
            return max(50, min(20000, int(per[key]))), True
        except Exception:
            pass
    return int(DEFAULT_CAPACITY_MAH_BY_ROLE.get(role, 500)), False


def get_retention_days(cfg: Optional[dict[str, Any]] = None) -> int:
    """读取历史采样保留天数（支持 1 / 3 / 7 / 14 / 30，默认 7）。"""
    src = cfg
    if not isinstance(src, dict) and os.path.exists(CONFIG_FILE):
        ok, raw, _ = _read_json_dict(CONFIG_FILE)
        if ok and raw is not None:
            src = raw
    if isinstance(src, dict):
        try:
            d = int(src.get("history_retention_days", 7))
            if d in VALID_RETENTION_DAYS:
                return d
        except Exception:
            pass
    return 7


def write_tray_state(state: dict[str, Any]) -> None:
    payload = dict(state or {})
    if "heartbeat_at" not in payload:
        payload["heartbeat_at"] = time.time()
    _atomic_write_json(STATE_FILE, payload)


def write_heartbeat(pid: Optional[int] = None, now: Optional[float] = None) -> None:
    """更新主进程心跳时间戳供 --watchdog 守护进程监测 UI 存活。"""
    ts = float(time.time() if now is None else now)
    cur = read_tray_state()
    if not isinstance(cur, dict):
        cur = {}
    cur["pid"] = int(pid if pid is not None else os.getpid())
    cur["heartbeat_at"] = ts
    _atomic_write_json(STATE_FILE, cur)


def read_heartbeat_age(now: Optional[float] = None) -> Optional[float]:
    st = read_tray_state()
    if not st:
        return None
    ts_val = st.get("heartbeat_at")
    if not isinstance(ts_val, (int, float)):
        ts_val = st.get("updated_at")
    if not isinstance(ts_val, (int, float)):
        return None
    ts_now = float(time.time() if now is None else now)
    return max(0.0, ts_now - float(ts_val))


def write_quit_flag() -> None:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(QUIT_FLAG_FILE)), exist_ok=True)
        with open(QUIT_FLAG_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def clear_quit_flag() -> None:
    try:
        if os.path.exists(QUIT_FLAG_FILE):
            os.remove(QUIT_FLAG_FILE)
    except OSError:
        pass


def has_quit_flag() -> bool:
    return os.path.exists(QUIT_FLAG_FILE)


def read_tray_state() -> dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


# ==============================================================================
# 4 级轮转日志（DEBUG / INFO / WARN / ERROR，单文件 2MB × 3 备份）
# ==============================================================================

LOG_FILE = os.path.join(BASE_DIR, "app_error.log")
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3
_LOG_PRIORITY = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ERROR": 40}
_LOG_LOCK = threading.RLock()
_CURRENT_LOG_LEVEL: Optional[str] = None


def _normalize_level(level: str) -> str:
    s = str(level or "INFO").strip().upper()
    if s == "WARNING":
        return "WARN"
    return s if s in VALID_LOG_LEVELS else "INFO"


def get_log_level() -> str:
    global _CURRENT_LOG_LEVEL
    if _CURRENT_LOG_LEVEL is None:
        lvl = "INFO"
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    lvl = _normalize_level(str(raw.get("log_level", "INFO")))
            except Exception:
                pass
        _CURRENT_LOG_LEVEL = lvl
    return _CURRENT_LOG_LEVEL


def set_log_level(level: str) -> str:
    global _CURRENT_LOG_LEVEL
    norm = _normalize_level(level)
    _CURRENT_LOG_LEVEL = norm
    return norm


def _rotate_log_files(path: str, backup_count: int) -> None:
    if backup_count <= 0:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    oldest = f"{path}.{backup_count}"
    if os.path.exists(oldest):
        try:
            os.remove(oldest)
        except OSError:
            pass
    for idx in range(backup_count - 1, 0, -1):
        src = f"{path}.{idx}"
        dst = f"{path}.{idx + 1}"
        if os.path.exists(src):
            try:
                os.replace(src, dst)
            except OSError:
                pass
    if os.path.exists(path):
        try:
            os.replace(path, f"{path}.1")
        except OSError:
            pass


def log_event(
    level: str,
    source: str,
    message: str,
    *,
    log_file: Optional[str] = None,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> bool:
    """按配置级别过滤并写入轮转日志。实际写入返回 True，被级别过滤返回 False。"""
    norm = _normalize_level(level)
    cur = get_log_level()
    if _LOG_PRIORITY[norm] < _LOG_PRIORITY.get(cur, 20):
        return False
    target = log_file or LOG_FILE
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    src = (source or "APP").strip()
    body = str(message or "").rstrip()
    line = f"[{ts}] [{norm}] [{src}] {body}\n"
    encoded_len = len(line.encode("utf-8", errors="replace"))
    with _LOG_LOCK:
        try:
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            if max_bytes > 0 and os.path.exists(target):
                if os.path.getsize(target) + encoded_len > max_bytes:
                    _rotate_log_files(target, backup_count)
            with open(target, "a", encoding="utf-8") as f:
                f.write(line)
            return True
        except OSError:
            return False


def read_log_entries(
    level_filter: str = "ALL",
    max_lines: int = 400,
    log_file: Optional[str] = None,
) -> list[str]:
    """读取最近日志行（兼容旧格式无级别行，旧行视作 ERROR）。"""
    target = log_file or LOG_FILE
    if not os.path.exists(target):
        return []
    filt = str(level_filter or "ALL").strip().upper()
    if filt == "WARNING":
        filt = "WARN"
    with _LOG_LOCK:
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return []
    if filt != "ALL" and filt in VALID_LOG_LEVELS:
        min_prio = _LOG_PRIORITY[filt]
        kept: list[str] = []
        for ln in lines:
            m = re.match(r"^\[[^\]]+\]\s*\[(DEBUG|INFO|WARN|ERROR)\]", ln)
            prio = _LOG_PRIORITY.get(m.group(1), 40) if m else 40
            if prio >= min_prio:
                kept.append(ln)
        lines = kept
    return lines[-max(1, max_lines):]


def clear_log_files(log_file: Optional[str] = None, backup_count: int = LOG_BACKUP_COUNT) -> None:
    target = log_file or LOG_FILE
    with _LOG_LOCK:
        for p in [target] + [f"{target}.{i}" for i in range(1, backup_count + 1)]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass


# ==============================================================================
# PythonW 路径解析
# ==============================================================================

def pythonw_exe() -> str:
    exe = sys.executable
    d = os.path.dirname(exe)
    cand = os.path.join(d, "pythonw.exe")
    if os.path.isfile(cand):
        return cand
    return exe


# ==============================================================================
# 单例窗口查找 / 关闭（纯 ctypes Win32 + NtQueryInformationProcess）
# ==============================================================================

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")

WM_CLOSE = 0x0010
WM_USER = 0x0400
WM_APP_REFRESH = WM_USER + 11
WM_APP_QUIT = WM_USER + 12
WM_APP_SHOW = WM_USER + 13

user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.PostMessageW.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_TERMINATE = 0x0001
ProcessCommandLineInformation = 60


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class UNICODE_STRING(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.TerminateProcess.restype = wintypes.BOOL
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

ntdll.NtQueryInformationProcess.restype = ctypes.c_long
ntdll.NtQueryInformationProcess.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG),
]

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def find_tray_hwnd() -> int:
    hwnd = user32.FindWindowW(WND_CLASS, None)
    return int(hwnd) if hwnd else 0


def _proc_cmdline(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        need = wintypes.ULONG(0)
        buf = ctypes.create_string_buffer(2048)
        st = ntdll.NtQueryInformationProcess(
            h, ProcessCommandLineInformation, buf, len(buf), ctypes.byref(need)
        )
        if st != 0 and need.value > len(buf) and need.value < 65536:
            buf = ctypes.create_string_buffer(need.value)
            st = ntdll.NtQueryInformationProcess(
                h, ProcessCommandLineInformation, buf, len(buf), ctypes.byref(need)
            )
        if st == 0:
            us = UNICODE_STRING.from_buffer_copy(buf.raw[: ctypes.sizeof(UNICODE_STRING)])
            if us.Buffer:
                return str(us.Buffer)
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(h)
    return ""


def _is_tray_daemon_cmd(cmd: str) -> bool:
    low = (cmd or "").lower()
    if any(
        flag in low
        for flag in ("--status", "--quit", "--refresh", "--help", "--version", "--once", "--debug-ui", "--list", "--battery", "--bt", "--dump", "--select")
    ):
        return False
    if IS_FROZEN:
        return True
    target_tray = os.path.normcase(os.path.abspath(TRAY_SCRIPT))
    target_panel = os.path.normcase(os.path.abspath(PANEL_SCRIPT))
    target_main = os.path.normcase(os.path.abspath(MAIN_SCRIPT))
    cmd_norm = os.path.normcase(cmd)
    return (
        target_tray in cmd_norm
        or target_panel in cmd_norm
        or target_main in cmd_norm
        or "tray_app.py" in low
        or "my_devices.py" in low
        or "main.py" in low
        or "--tray" in low
        or "--minimized" in low
        or "--watchdog" in low
    )


def find_tray_pids() -> list[int]:
    """查找正在运行当前托盘后台的进程 PID（排除当前进程及其 --onefile 启动器父进程）。"""
    ignore_pids = {os.getpid()}
    try:
        ignore_pids.add(os.getppid())
    except Exception:
        pass
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    pids: list[int] = []
    frozen_exe_name = os.path.basename(sys.executable).lower() if IS_FROZEN else ""
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if kernel32.Process32FirstW(snap, ctypes.byref(pe)):
            while True:
                pid = int(pe.th32ProcessID)
                exe = (pe.szExeFile or "").lower()
                if pid not in ignore_pids:
                    if (IS_FROZEN and exe == frozen_exe_name) or (
                        (not IS_FROZEN) and exe in ("python.exe", "pythonw.exe")
                    ):
                        cmd = _proc_cmdline(pid)
                        if cmd and _is_tray_daemon_cmd(cmd):
                            pids.append(pid)
                if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(snap)
    return pids


def is_tray_running() -> bool:
    return find_tray_hwnd() != 0 or len(find_tray_pids()) > 0


def start_tray(debug: bool = False) -> bool:
    if is_tray_running():
        return True
    args = build_launch_args("tray", ["--debug"] if debug else None, console=debug)
    flags = 0 if debug else getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    subprocess.Popen(args, cwd=BASE_DIR, creationflags=flags)
    for _ in range(25):
        time.sleep(0.1)
        if find_tray_hwnd():
            return True
    return is_tray_running()


def stop_tray(timeout: float = 3.0) -> bool:
    write_quit_flag()
    hwnd = find_tray_hwnd()
    if hwnd:
        user32.PostMessageW(wintypes.HWND(hwnd), WM_APP_QUIT, 0, 0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not is_tray_running():
                return True
            time.sleep(0.1)

    for pid in find_tray_pids():
        h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if h:
            try:
                kernel32.TerminateProcess(h, 0)
            finally:
                kernel32.CloseHandle(h)
    time.sleep(0.15)
    return not is_tray_running()


def notify_tray_refresh() -> bool:
    hwnd = find_tray_hwnd()
    if hwnd:
        return bool(user32.PostMessageW(wintypes.HWND(hwnd), WM_APP_REFRESH, 0, 0))
    return False


def notify_tray_show() -> bool:
    hwnd = find_tray_hwnd()
    if hwnd:
        return bool(user32.PostMessageW(wintypes.HWND(hwnd), WM_APP_SHOW, 0, 0))
    return False


# ==============================================================================
# 开机自启 (.lnk in Startup folder + StartupApproved status check)
# ==============================================================================

def startup_lnk_path() -> str:
    return os.path.join(win_shell.startup_dir(), STARTUP_LNK_NAME)


def _startup_approved_enabled(name: str) -> Optional[bool]:
    try:
        import winreg
        subkey = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, name)
        if isinstance(val, (bytes, bytearray)) and len(val) >= 4:
            # Windows 10/11: 0x02/0x06 表示启用，0x03/0x07 表示用户在任务管理器禁用
            return (val[0] & 0x01) == 0
    except OSError:
        pass
    return None


def autostart_status() -> tuple[bool, str]:
    lnk = startup_lnk_path()
    if not win_shell.shortcut_exists(lnk):
        return False, "未启用"
    approved = _startup_approved_enabled(STARTUP_LNK_NAME)
    if approved is False:
        return False, "已被系统任务管理器禁用"
    return True, "已启用（开机自动最小化到托盘）"


def is_autostart_enabled() -> bool:
    return bool(autostart_status()[0])


def set_autostart(enabled: bool) -> tuple[bool, str]:
    lnk = startup_lnk_path()
    if not enabled:
        win_shell.remove_shortcut(lnk)
        return False, "已取消开机自启"
    cfg = load_config()
    use_wd = bool(cfg.get("watchdog_enabled", True))
    target = sys.executable if IS_FROZEN else pythonw_exe()
    if use_wd:
        args = "--watchdog --minimized" if IS_FROZEN else f'"{MAIN_SCRIPT}" --watchdog --minimized'
    else:
        args = "--minimized" if IS_FROZEN else f'"{PANEL_SCRIPT}" --minimized'
    ok = win_shell.create_shortcut(
        lnk_path=lnk,
        target=target,
        arguments=args,
        workdir=BASE_DIR,
        description="2.4G 外设电量查看器（开机驻留托盘）",
        icon=sys.executable if IS_FROZEN else (app_icon_path() or ""),
    )
    if not ok:
        return False, "创建开机启动快捷方式失败"
    approved = _startup_approved_enabled(STARTUP_LNK_NAME)
    if approved is False:
        return False, "快捷方式已写入，但任务管理器「启动」页将其禁用了，请去开启"
    return True, "已启用开机自启"


if (os.environ.get("PBV_DATA_DIR") or "").strip():
    set_data_dir(None)
