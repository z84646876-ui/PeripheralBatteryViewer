# -*- coding: utf-8 -*-
"""生成高清多分辨率应用图标 assets/app_icon.ico（纯 Python，零第三方依赖）。"""

from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import win_icon


def _blend(dst: bytearray, x: int, y: int, size: int, r: int, g: int, b: int, a: float) -> None:
    if not (0 <= x < size and 0 <= y < size) or a <= 0.0:
        return
    a = min(1.0, max(0.0, a))
    i = (y * size + x) * 4
    sr, sg, sb, sa = dst[i], dst[i + 1], dst[i + 2], dst[i + 3] / 255.0
    out_a = a + sa * (1.0 - a)
    if out_a <= 1e-6:
        return
    dst[i] = min(255, int(round((r * a + sr * sa * (1.0 - a)) / out_a)))
    dst[i + 1] = min(255, int(round((g * a + sg * sa * (1.0 - a)) / out_a)))
    dst[i + 2] = min(255, int(round((b * a + sb * sa * (1.0 - a)) / out_a)))
    dst[i + 3] = min(255, int(round(out_a * 255)))


def _sdf_rounded_box(px: float, py: float, cx: float, cy: float, hw: float, hh: float, rad: float) -> float:
    dx = abs(px - cx) - hw + rad
    dy = abs(py - cy) - hh + rad
    ox = max(dx, 0.0)
    oy = max(dy, 0.0)
    return math.hypot(ox, oy) + min(max(dx, dy), 0.0) - rad


def render_app_icon(size: int) -> bytes:
    """绘制新拟物风浅灰基底 `#e0e5ec` + 左上白高光 / 右下冷灰暗影 + 紫罗兰与薄荷电量条徽章。"""
    buf = bytearray(size * size * 4)
    c = size / 2.0
    bg_half = size * 0.44
    bg_rad = size * 0.22

    # 电池外框与格条几何比例
    bat_cx, bat_cy = c - size * 0.02, c
    bat_hw, bat_hh = size * 0.255, size * 0.165
    bat_rad = max(1.5, size * 0.045)
    stroke = max(1.3, size * 0.044)

    cap_cx = bat_cx + bat_hw + size * 0.045
    cap_cy = bat_cy
    cap_hw = max(1.0, size * 0.032)
    cap_hh = size * 0.068

    for y in range(size):
        py = y + 0.5
        for x in range(size):
            px = x + 0.5
            d_bg = _sdf_rounded_box(px, py, c, c, bg_half, bg_half, bg_rad)
            cov_bg = max(0.0, min(1.0, 0.5 - d_bg))
            if cov_bg > 0:
                # 极柔和左上高光向右下基底微过渡：#e9edf3 -> #e0e5ec -> #d6dce6
                t = ((x / max(1, size - 1)) + (y / max(1, size - 1))) * 0.5
                br = int(233 * (1 - t) + 214 * t)
                bg = int(237 * (1 - t) + 220 * t)
                bb = int(243 * (1 - t) + 230 * t)
                _blend(buf, x, y, size, br, bg, bb, cov_bg)
                # 左上白色内高光边 / 右下冷灰内暗缘
                if -1.5 <= d_bg <= 0.2:
                    nx = px - c
                    ny = py - c
                    if nx + ny < 0:
                        _blend(buf, x, y, size, 255, 255, 255, cov_bg * 0.72)
                    else:
                        _blend(buf, x, y, size, 184, 188, 194, cov_bg * 0.78)

            # 电池外壳圆润描边 (#3d4451)
            d_bat = _sdf_rounded_box(px, py, bat_cx, bat_cy, bat_hw, bat_hh, bat_rad)
            ring = max(0.0, min(1.0, (stroke / 2.0 + 0.5) - abs(d_bat)))
            if ring > 0:
                _blend(buf, x, y, size, 61, 68, 81, ring)

            # 电池正极凸起 (#3d4451)
            d_cap = _sdf_rounded_box(px, py, cap_cx, cap_cy, cap_hw, cap_hh, max(0.8, size * 0.015))
            cov_cap = max(0.0, min(1.0, 0.5 - d_cap))
            if cov_cap > 0:
                _blend(buf, x, y, size, 61, 68, 81, cov_cap)

            # 电池内部三格电量条（#6d5dfc 紫罗兰 + #4ecdc4 薄荷绿）
            inner_w = bat_hw * 2 - stroke * 2.2
            inner_h = bat_hh * 2 - stroke * 2.2
            gap = max(1.0, size * 0.03)
            bar_w = (inner_w - 2 * gap) / 3.0
            bar_hh = inner_h * 0.40
            start_x = bat_cx - inner_w / 2.0 + bar_w / 2.0
            for idx, (cr, cg, cb) in enumerate(((109, 93, 252), (78, 205, 196), (78, 205, 196))):
                bx = start_x + idx * (bar_w + gap)
                d_bar = _sdf_rounded_box(px, py, bx, bat_cy, bar_w / 2.0, bar_hh, max(0.8, size * 0.018))
                cov_bar = max(0.0, min(1.0, 0.5 - d_bar))
                if cov_bar > 0:
                    _blend(buf, x, y, size, cr, cg, cb, cov_bar)

    return bytes(buf)


def generate(out_path: str | None = None) -> str:
    if out_path is None:
        out_path = os.path.join(ROOT, "assets", "app_icon.ico")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    sizes = (16, 24, 32, 48, 64, 128)
    imgs = [(s, render_app_icon(s)) for s in sizes]
    data = win_icon.build_ico(imgs)
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


if __name__ == "__main__":
    p = generate()
    print("Generated icon:", p, os.path.getsize(p), "bytes")
