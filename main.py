# -*- coding: utf-8 -*-
"""main.py —— 统一多模式入口（供打包为单文件 EXE 或直接运行）

模式分发：
  无模式参数          -> 打开主电量面板 (my_devices)
  --tray [...]       -> 启动系统托盘后台或管理运行中托盘 (tray_app)
  --debug-ui [...]   -> 打开 HID 调试与协议标定工具 (app)
  --once / --json / --autostart / --install-links -> 由 my_devices CLI 处理
"""

from __future__ import annotations

import ctypes
import sys

try:
    ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID(
        "PeripheralBatteryViewer.App"
    )
except Exception:
    pass


def _configure_console_io(args: list[str]) -> None:
    cli_flags = {
        "--once", "--json", "--autostart", "--install-links",
        "--status", "--refresh", "--quit", "--debug", "--minimized",
        "--watchdog", "--list", "--battery", "--bt", "--dump", "-h", "--help",
    }
    if not any(a in cli_flags for a in args):
        return

    k32 = ctypes.WinDLL("kernel32")
    if sys.stdout is None or sys.stderr is None:
        try:
            k32.AttachConsole(0xFFFFFFFF)
        except Exception:
            pass

    try:
        cp = int(k32.GetConsoleOutputCP() or 0)
    except Exception:
        cp = 0

    target_enc = "utf-8" if (cp == 65001 or cp == 0) else "mbcs"
    try:
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding=target_enc, errors="replace", buffering=1)
        elif (not sys.stdout.isatty()) or cp == 65001:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding=target_enc, errors="replace", buffering=1)
        elif (not sys.stderr.isatty()) or cp == 65001:
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    raw_args = list(sys.argv[1:])
    _configure_console_io(raw_args)

    if "--watchdog" in raw_args:
        rem_args = [a for a in raw_args if a != "--watchdog"]
        import watchdog
        return watchdog.main(rem_args)

    if "--tray" in raw_args:
        sys.argv = [sys.argv[0]] + [a for a in raw_args if a != "--tray"]
        import my_devices
        return my_devices.main(start_minimized_default=True)

    if "--debug-ui" in raw_args:
        sys.argv = [sys.argv[0]] + [a for a in raw_args if a != "--debug-ui"]
        import app
        return app.main()

    import my_devices
    return my_devices.main()


if __name__ == "__main__":
    raise SystemExit(main())
