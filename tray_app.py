# -*- coding: utf-8 -*-
"""tray_app.py —— 托盘后台入口转发器（转发至统一单实例主程序与常驻手柄托盘）

保留 `--status` / `--refresh` / `--quit` CLI 管理开关以兼容已有脚本与自启动快捷方式。
"""

from __future__ import annotations

import my_devices


def main() -> int:
    return my_devices.main(start_minimized_default=True)


if __name__ == "__main__":
    raise SystemExit(main())
