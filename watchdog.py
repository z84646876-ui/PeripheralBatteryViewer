# -*- coding: utf-8 -*-
"""watchdog.py —— 轻量级后台看门狗守护进程（零 Tk 依赖，异常退出/心跳卡死自动重启）"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from typing import Any, Callable, Optional

import tray_common


class WatchdogRunner:
    """监控主进程存活与 UI 心跳；用户主动退出时安静结束，崩溃或卡死则带限流退避自重启。"""

    def __init__(
        self,
        *,
        launch_fn: Callable[[], Any],
        is_alive_fn: Callable[[Any], bool],
        poll_code_fn: Callable[[Any], Optional[int]],
        terminate_fn: Callable[[Any], None],
        heartbeat_age_fn: Callable[[], Optional[float]],
        has_quit_fn: Callable[[], bool],
        clear_quit_fn: Callable[[], None],
        sleep_fn: Callable[[float], None] = time.sleep,
        clock_fn: Callable[[], float] = time.monotonic,
        heartbeat_timeout_s: float = 90.0,
        startup_grace_s: float = 45.0,
        check_interval_s: float = 2.0,
        max_restarts: int = 3,
        window_sec: float = 300.0,
    ) -> None:
        self.launch_fn = launch_fn
        self.is_alive_fn = is_alive_fn
        self.poll_code_fn = poll_code_fn
        self.terminate_fn = terminate_fn
        self.heartbeat_age_fn = heartbeat_age_fn
        self.has_quit_fn = has_quit_fn
        self.clear_quit_fn = clear_quit_fn
        self.sleep_fn = sleep_fn
        self.clock_fn = clock_fn
        self.heartbeat_timeout_s = float(heartbeat_timeout_s)
        self.startup_grace_s = float(startup_grace_s)
        self.check_interval_s = float(check_interval_s)
        self.max_restarts = max(1, int(max_restarts))
        self.window_sec = float(window_sec)
        self.restart_count = 0
        self.stop_reason = ""
        self._restart_times: list[float] = []

    def _can_restart(self, now_t: float) -> bool:
        cutoff = now_t - self.window_sec
        self._restart_times = [t for t in self._restart_times if t >= cutoff]
        return len(self._restart_times) < self.max_restarts

    def run(self, *, max_cycles: Optional[int] = None) -> int:
        self.clear_quit_fn()
        proc = self.launch_fn()
        started_at = self.clock_fn()
        cycles = 0

        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            self.sleep_fn(self.check_interval_s)
            if self.has_quit_fn():
                self.stop_reason = "quit_flag"
                return 0

            now_t = self.clock_fn()
            need_restart = False
            reason = ""

            if not self.is_alive_fn(proc):
                rc = self.poll_code_fn(proc)
                if rc == 0:
                    self.stop_reason = "clean_exit"
                    return 0
                need_restart = True
                reason = f"进程异常退出 (exit={rc})"
            elif (now_t - started_at) >= self.startup_grace_s:
                age = self.heartbeat_age_fn()
                if age is not None and age > self.heartbeat_timeout_s:
                    need_restart = True
                    reason = f"主线程心跳超时 ({age:.0f}s > {self.heartbeat_timeout_s:.0f}s)"
                    try:
                        self.terminate_fn(proc)
                    except Exception:
                        pass

            if not need_restart:
                continue

            if not self._can_restart(now_t):
                self.stop_reason = "restart_limit"
                tray_common.log_event(
                    "ERROR",
                    "watchdog",
                    f"{reason}，且 {int(self.window_sec)}s 内重启已达上限 ({self.max_restarts} 次)，守护停止",
                )
                return 1

            self._restart_times.append(now_t)
            self.restart_count += 1
            backoff_s = min(10.0, 2.0 * len(self._restart_times))
            tray_common.log_event(
                "WARN",
                "watchdog",
                f"检测到 {reason}，将在 {backoff_s:.0f}s 后执行第 {self.restart_count} 次自动恢复重启",
            )
            self.sleep_fn(backoff_s)
            if self.has_quit_fn():
                self.stop_reason = "quit_flag"
                return 0
            self.clear_quit_flag_safe()
            proc = self.launch_fn()
            started_at = self.clock_fn()

        self.stop_reason = "max_cycles"
        return 0

    def clear_quit_flag_safe(self) -> None:
        try:
            self.clear_quit_fn()
        except Exception:
            pass


def _spawn_target_process(forward_args: list[str]) -> subprocess.Popen:
    clean_args = [a for a in forward_args if a != "--watchdog"]
    if "--minimized" not in clean_args and "--tray" not in clean_args:
        clean_args = ["--minimized"] + clean_args
    if tray_common.IS_FROZEN:
        cmd = [sys.executable] + clean_args
    else:
        cmd = [tray_common.pythonw_exe(), tray_common.MAIN_SCRIPT] + clean_args
    return subprocess.Popen(
        cmd,
        cwd=tray_common.BASE_DIR,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )


def main(argv: Optional[list[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex = k32.CreateMutexW(None, False, tray_common.WATCHDOG_MUTEX_NAME)
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        if mutex:
            k32.CloseHandle(mutex)
        return 0

    try:
        def _launch() -> subprocess.Popen:
            return _spawn_target_process(raw)

        def _is_alive(p: Any) -> bool:
            return p is not None and p.poll() is None

        def _poll_code(p: Any) -> Optional[int]:
            return None if p is None else p.poll()

        def _terminate(p: Any) -> None:
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3.0)
                except Exception:
                    p.kill()

        tray_common.log_event("INFO", "watchdog", f"守护进程启动 (pid={os.getpid()})")
        runner = WatchdogRunner(
            launch_fn=_launch,
            is_alive_fn=_is_alive,
            poll_code_fn=_poll_code,
            terminate_fn=_terminate,
            heartbeat_age_fn=tray_common.read_heartbeat_age,
            has_quit_fn=tray_common.has_quit_flag,
            clear_quit_fn=tray_common.clear_quit_flag,
        )
        return runner.run()
    finally:
        if mutex:
            k32.CloseHandle(mutex)
