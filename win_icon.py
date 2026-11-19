# -*- coding: utf-8 -*-
"""win_icon.py —— 纯 Python 渲染系统托盘数字 HICON（零第三方依赖）

内置 3x5 点阵字模，按电量和外设角色生成 16/24/32 多分辨率 RGBA -> ICO -> HICON，
Windows 根据系统 DPI 自动匹配最清晰的一档。
"""

from __future__ import annotations

import ctypes
import math
import os
import struct
import tempfile
from ctypes import wintypes
from typing import Optional

DIGITS = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "-": ("000", "000", "111", "000", "000"),
    "?": ("110", "001", "010", "000", "010"),
    "%": ("101", "001", "010", "100", "101"),
}

GLYPH_W, GLYPH_H = 3, 5

ROLE_STYLES = {
    "mouse": {"plate": False, "plate_color": (28, 30, 34, 235), "bar_color": None},
    "mouse2": {"plate": True, "plate_color": (26, 30, 38, 235), "bar_color": (124, 156, 196, 255)},
    "keyboard": {"plate": True, "plate_color": (24, 30, 42, 235), "bar_color": (110, 168, 254, 255)},
    "headset": {"plate": True, "plate_color": (34, 26, 40, 235), "bar_color": (196, 146, 255, 255)},
    "controller": {"plate": True, "plate_color": (24, 34, 34, 235), "bar_color": (94, 212, 196, 255)},
    "other": {"plate": True, "plate_color": (28, 30, 34, 235), "bar_color": None},
}


def render_rgba(
    text: str,
    size: int,
    color: tuple[int, int, int],
    plate: bool = False,
    plate_color: tuple[int, int, int, int] = (28, 30, 34, 235),
    role: str = "",
) -> bytes:
    """把文字渲染成 size×size RGBA 位图。"""
    style = ROLE_STYLES.get(role)
    if style is not None:
        plate = bool(style["plate"])
        plate_color = style["plate_color"]
        bar_color = style["bar_color"]
    else:
        bar_color = None

    text = text or "--"
    n = len(text)
    gap = 1
    scale = 1
    max_h = size - (4 if bar_color else 2)
    while True:
        w = n * GLYPH_W * (scale + 1) + max(0, n - 1) * gap
        h = GLYPH_H * (scale + 1)
        if w > size - 2 or h > max_h:
            break
        scale += 1

    gw, gh = GLYPH_W * scale, GLYPH_H * scale
    total_w = n * gw + max(0, n - 1) * gap
    ox = (size - total_w) // 2
    oy = max(1, (size - gh - (2 if bar_color else 0)) // 2)

    px = bytearray(size * size * 4)

    def put(x: int, y: int, rgba: tuple[int, int, int, int]) -> None:
        if 0 <= x < size and 0 <= y < size:
            i = (y * size + x) * 4
            px[i : i + 4] = bytes(rgba)

    def get(x: int, y: int) -> tuple[int, int, int, int]:
        i = (y * size + x) * 4
        return (px[i], px[i + 1], px[i + 2], px[i + 3])

    if size <= 16:
        # 16px 极小系统托盘尺寸改用高对比简洁电量色块/进度条替代噪点文字，24px+ 再绘制高清点阵数字
        pct = None
        clean = text.strip().rstrip("%")
        if clean.isdigit():
            pct = max(0, min(100, int(clean)))
        for y in range(size):
            for x in range(size):
                cx = min(x, size - 1 - x)
                cy = min(y, size - 1 - y)
                if cx + cy < 2 and cx < 2 and cy < 2:
                    continue
                put(x, y, plate_color if plate else (24, 28, 36, 235))
        # 绘制 3..12 横向实心圆角电量条与居中边框
        for y in range(4, 12):
            for x in range(2, 14):
                if (x in (2, 13)) or (y in (4, 11)):
                    put(x, y, (*color, 210))
                else:
                    put(x, y, (38, 44, 56, 255))
        fill_cols = 5 if pct is None else max(1, min(10, round(pct / 10.0)))
        for y in range(5, 11):
            for x in range(3, 3 + fill_cols):
                put(x, y, (*color, 255))
        if bar_color:
            for x in range(3, 13):
                put(x, size - 1, bar_color)
        return bytes(px)

    if plate:
        r = max(1, size // 8)
        for y in range(size):
            for x in range(size):
                cx = min(x, size - 1 - x)
                cy = min(y, size - 1 - y)
                if cx + cy < r and cx < r and cy < r:
                    continue
                put(x, y, plate_color)

    for k, ch in enumerate(text):
        glyph = DIGITS.get(ch)
        if not glyph:
            continue
        gx = ox + k * (gw + gap)
        for gy, row in enumerate(glyph):
            for gxx, bit in enumerate(row):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        put(gx + gxx * scale + dx, oy + gy * scale + dy, (*color, 255))

    # 给数字外缘描一圈深色轮廓，浅/深色任务栏均清晰
    outline = (14, 16, 20, 195)
    edge = []
    for y in range(size):
        for x in range(size):
            if get(x, y)[3] == 0:
                continue
            if plate and get(x, y) == plate_color:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < size and 0 <= ny < size:
                    cur = get(nx, ny)
                    if cur[3] == 0 or (plate and cur == plate_color):
                        edge.append((nx, ny))
    for x, y in edge:
        cur = get(x, y)
        if cur[3] == 0 or (plate and cur == plate_color):
            put(x, y, outline)

    # 底部细识别条，方便多外设并排区分鼠标/键盘/耳机/手柄
    if bar_color:
        bar_h = 2 if size >= 24 else 1
        pad = max(2, size // 6)
        for y in range(size - bar_h, size):
            for x in range(pad, size - pad):
                put(x, y, bar_color)

    return bytes(px)


def build_ico(images: list[tuple[int, bytes]]) -> bytes:
    """将多尺寸 RGBA 位图打包为 Windows ICO 二进制。"""
    entries = []
    blobs = []
    offset = 6 + 16 * len(images)
    for size, rgba in images:
        bih = struct.pack(
            "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0
        )
        xor = bytearray()
        for y in range(size - 1, -1, -1):
            row = rgba[y * size * 4 : (y + 1) * size * 4]
            for x in range(size):
                r, g, b, a = row[x * 4 : x * 4 + 4]
                xor += bytes((b, g, r, a))
        row_bytes = ((size + 31) // 32) * 4
        mask = b"\x00" * (row_bytes * size)
        blob = bih + bytes(xor) + mask
        blobs.append(blob)
        dim = size if size < 256 else 0
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(images)) + b"".join(entries) + b"".join(blobs)


user32 = ctypes.WinDLL("user32", use_last_error=True)
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010

user32.LoadImageW.restype = ctypes.c_void_p
user32.LoadImageW.argtypes = [
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.DestroyIcon.restype = wintypes.BOOL
user32.DestroyIcon.argtypes = [ctypes.c_void_p]

SIZES = (16, 24, 32)
_icon_cache: dict[tuple, int] = {}
_tmp_dir: Optional[str] = None


def _ensure_tmp() -> str:
    global _tmp_dir
    if _tmp_dir is None:
        _tmp_dir = os.path.join(tempfile.gettempdir(), "peripheral_battery_icons")
        os.makedirs(_tmp_dir, exist_ok=True)
    return _tmp_dir


def _sd_round_box(x: float, y: float, w: float, h: float, r: float) -> float:
    dx = abs(x) - (w - r)
    dy = abs(y) - (h - r)
    return math.hypot(max(dx, 0.0), max(dy, 0.0)) + min(max(dx, dy), 0.0) - r


def _sd_capsule(px: float, py: float, ax: float, ay: float, bx: float, by: float, r: float) -> float:
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    bb = bax * bax + bay * bay
    h = 0.0 if bb <= 1e-9 else max(0.0, min(1.0, (pax * bax + pay * bay) / bb))
    return math.hypot(pax - bax * h, pay - bay * h) - r


def _blend_px(dst: bytearray, idx: int, r: int, g: int, b: int, a: float) -> None:
    if a <= 0.0:
        return
    a = min(1.0, max(0.0, a))
    sa = int(round(a * 255.0))
    da = dst[idx + 3] / 255.0
    out_a = a + da * (1.0 - a)
    if out_a <= 1e-6:
        dst[idx : idx + 4] = b"\x00\x00\x00\x00"
        return
    for ch, src_v in enumerate((r, g, b)):
        dst_v = dst[idx + ch]
        dst[idx + ch] = max(0, min(255, int(round((src_v * a + dst_v * da * (1.0 - a)) / out_a))))
    dst[idx + 3] = max(sa, min(255, int(round(out_a * 255.0))))


def render_controller_logo_rgba(size: int, alert: bool = False) -> bytes:
    """渲染矢量抗锯齿手柄 Logo RGBA，适配浅色/深色 Windows 任务栏托盘区。"""
    buf = bytearray(size * size * 4)
    aa = 1.35 / max(size, 16)

    for py in range(size):
        ny = (py + 0.5) / size * 2.0 - 1.0
        for px in range(size):
            nx = (px + 0.5) / size * 2.0 - 1.0
            idx = (py * size + px) * 4

            # 手柄几何主体：横向圆角主体 + 左右斜向握把 + 中桥
            d_top = _sd_round_box(nx, ny + 0.07, 0.54, 0.25, 0.19)
            d_grip_l = _sd_capsule(nx, ny, -0.45, 0.05, -0.60, 0.44, 0.165)
            d_grip_r = _sd_capsule(nx, ny, 0.45, 0.05, 0.60, 0.44, 0.165)
            d_body = min(d_top, d_grip_l, d_grip_r)

            # 外圈柔和深描边/光环，保证深浅任务栏都清晰可辨
            d_outline = d_body - (0.055 if size >= 24 else 0.075)
            if d_outline < aa:
                a_out = max(0.0, min(1.0, (aa - d_outline) / (2.0 * aa)))
                _blend_px(buf, idx, 244, 247, 252, a_out * 0.92)

            if d_body < aa:
                a_body = max(0.0, min(1.0, (aa - d_body) / (2.0 * aa)))
                grad = max(0.0, min(1.0, (ny + 0.45) / 1.05))
                cr = int(round(88 - 22 * grad))
                cg = int(round(98 - 22 * grad))
                cb = int(round(122 - 22 * grad))
                # 轻高光从左上投射，延续新拟物金属质感
                hl = max(0.0, 1.0 - max(0.0, nx + ny + 0.4) * 1.1)
                cr = min(255, int(cr + 22 * hl))
                cg = min(255, int(cg + 24 * hl))
                cb = min(255, int(cb + 26 * hl))
                _blend_px(buf, idx, cr, cg, cb, a_body)

                # 左侧十字方向键（明亮灰白）
                d_h = _sd_round_box(nx + 0.30, ny + 0.06, 0.125, 0.042, 0.02)
                d_v = _sd_round_box(nx + 0.30, ny + 0.06, 0.042, 0.125, 0.02)
                d_dpad = min(d_h, d_v)
                if d_dpad < aa:
                    a_dp = max(0.0, min(1.0, (aa - d_dpad) / (2.0 * aa)))
                    _blend_px(buf, idx, 232, 237, 245, a_dp)

                # 右侧双肩按键圆点（香槟/柔和青蓝与紫蓝强调）
                d_b1 = math.hypot(nx - 0.25, ny + 0.01) - 0.058
                d_b2 = math.hypot(nx - 0.37, ny + 0.11) - 0.058
                if d_b1 < aa:
                    a_b = max(0.0, min(1.0, (aa - d_b1) / (2.0 * aa)))
                    _blend_px(buf, idx, 127, 175, 199, a_b)
                if d_b2 < aa:
                    a_b = max(0.0, min(1.0, (aa - d_b2) / (2.0 * aa)))
                    _blend_px(buf, idx, 212, 178, 140, a_b)

            # 任一外设低电量 (<20%) 时，右上角绘制醒目红粉指示圆点
            if alert:
                d_dot = math.hypot(nx - 0.62, ny + 0.62) - 0.20
                if d_dot < aa:
                    a_dot = max(0.0, min(1.0, (aa - d_dot) / (2.0 * aa)))
                    _blend_px(buf, idx, 255, 255, 255, a_dot)
                d_in = math.hypot(nx - 0.62, ny + 0.62) - 0.14
                if d_in < aa:
                    a_in = max(0.0, min(1.0, (aa - d_in) / (2.0 * aa)))
                    _blend_px(buf, idx, 224, 114, 114, a_in)

    return bytes(buf)


def render_ring_rgba(
    size: int,
    level: Optional[int],
    color: tuple[int, int, int],
    *,
    alert: bool = False,
    charging: bool = False,
) -> bytes:
    """渲染矢量抗锯齿环形电量图标（#37 环形样式）。"""
    buf = bytearray(size * size * 4)
    aa = 1.45 / max(size, 16)
    pct = 0.0 if level is None else max(0.0, min(100.0, float(level)))
    sweep = (pct / 100.0) * 2.0 * math.pi
    r_mid = 0.62
    half_thick = 0.165 if size >= 20 else 0.19

    for py in range(size):
        ny = (py + 0.5) / size * 2.0 - 1.0
        for px in range(size):
            nx = (px + 0.5) / size * 2.0 - 1.0
            idx = (py * size + px) * 4
            dist = math.hypot(nx, ny)
            d_ring = abs(dist - r_mid) - half_thick

            # 柔和圆盘底衬，保证浅/深任务栏对比度
            d_bg = dist - (r_mid + half_thick + 0.09)
            if d_bg < aa:
                a_bg = max(0.0, min(1.0, (aa - d_bg) / (2.0 * aa)))
                _blend_px(buf, idx, 26, 30, 38, a_bg * 0.86)

            if d_ring < aa:
                a_ring = max(0.0, min(1.0, (aa - d_ring) / (2.0 * aa)))
                _blend_px(buf, idx, 68, 76, 92, a_ring * 0.90)

                # 从 12 点钟方向顺时针映射角度 [0, 2*pi)
                ang = (math.atan2(nx, -ny)) % (2.0 * math.pi)
                if sweep > 0.0 and ang <= sweep:
                    # 在末端做柔和覆盖抗锯齿
                    end_cov = 1.0
                    if sweep < 2.0 * math.pi:
                        arc_dist = (sweep - ang) * r_mid
                        if arc_dist < aa:
                            end_cov = max(0.0, min(1.0, arc_dist / aa))
                    _blend_px(buf, idx, color[0], color[1], color[2], a_ring * end_cov)

            # 中心指示小点（充电琥珀 / 告警红 / 离线空心）
            if charging or alert:
                d_center = dist - 0.18
                if d_center < aa:
                    a_c = max(0.0, min(1.0, (aa - d_center) / (2.0 * aa)))
                    dot_rgb = (224, 114, 114) if alert else (218, 178, 112)
                    _blend_px(buf, idx, dot_rgb[0], dot_rgb[1], dot_rgb[2], a_c)

    return bytes(buf)


def render_dot_rgba(
    size: int,
    color: tuple[int, int, int],
    *,
    alert: bool = False,
    charging: bool = False,
) -> bytes:
    """渲染极简状态圆点图标（#37 极简点样式）。"""
    buf = bytearray(size * size * 4)
    aa = 1.45 / max(size, 16)
    outer_r = 0.66
    inner_r = 0.48
    core_rgb = (214, 96, 102) if alert else color

    for py in range(size):
        ny = (py + 0.5) / size * 2.0 - 1.0
        for px in range(size):
            nx = (px + 0.5) / size * 2.0 - 1.0
            idx = (py * size + px) * 4
            dist = math.hypot(nx, ny)
            d_halo = dist - outer_r
            if d_halo < aa:
                a_h = max(0.0, min(1.0, (aa - d_halo) / (2.0 * aa)))
                _blend_px(buf, idx, 244, 247, 252, a_h * 0.88)
            d_core = dist - inner_r
            if d_core < aa:
                a_c = max(0.0, min(1.0, (aa - d_core) / (2.0 * aa)))
                # 微立体顶部高光
                hl = max(0.0, 1.0 - max(0.0, nx + ny + 0.35) * 1.1)
                cr = min(255, int(core_rgb[0] + 28 * hl))
                cg = min(255, int(core_rgb[1] + 28 * hl))
                cb = min(255, int(core_rgb[2] + 28 * hl))
                _blend_px(buf, idx, cr, cg, cb, a_c)
            if charging:
                d_ring = abs(dist - 0.57) - 0.045
                if d_ring < aa:
                    a_r = max(0.0, min(1.0, (aa - d_ring) / (2.0 * aa)))
                    _blend_px(buf, idx, 242, 200, 128, a_r)

    return bytes(buf)


def render_device_emblem_rgba(
    size: int,
    role: str,
    level: Optional[int],
    color: tuple[int, int, int],
    *,
    alert: bool = False,
) -> bytes:
    """按最低电量设备类型绘制专属矢量徽标 + 底部电量彩条（#37 lowest_device 样式）。"""
    r_norm = "controller" if role == "gamepad" else role
    if r_norm == "controller":
        buf = bytearray(render_controller_logo_rgba(size, alert=alert))
    else:
        buf = bytearray(size * size * 4)
        aa = 1.4 / max(size, 16)
        for py in range(size):
            ny = (py + 0.5) / size * 2.0 - 1.0
            for px in range(size):
                nx = (px + 0.5) / size * 2.0 - 1.0
                idx = (py * size + px) * 4
                if r_norm == "mouse":
                    d_shape = _sd_round_box(nx, ny + 0.06, 0.36, 0.52, 0.28)
                    d_detail = _sd_capsule(nx, ny, 0.0, -0.34, 0.0, -0.10, 0.055)
                elif r_norm == "keyboard":
                    d_shape = _sd_round_box(nx, ny + 0.05, 0.66, 0.34, 0.12)
                    # 两行键帽条
                    d_k1 = _sd_round_box(nx, ny - 0.03, 0.48, 0.045, 0.02)
                    d_k2 = _sd_round_box(nx, ny + 0.13, 0.40, 0.045, 0.02)
                    d_detail = min(d_k1, d_k2)
                elif r_norm == "headset":
                    dist = math.hypot(nx, ny + 0.06)
                    d_arc = max(abs(dist - 0.44) - 0.08, ny - 0.06)
                    d_l = _sd_round_box(nx + 0.44, ny - 0.12, 0.13, 0.22, 0.08)
                    d_r = _sd_round_box(nx - 0.44, ny - 0.12, 0.13, 0.22, 0.08)
                    d_shape = min(d_arc, d_l, d_r)
                    d_detail = 1.0
                else:
                    d_shape = _sd_round_box(nx, ny + 0.05, 0.48, 0.42, 0.16)
                    d_detail = math.hypot(nx, ny + 0.05) - 0.14

                d_out = d_shape - 0.065
                if d_out < aa:
                    _blend_px(buf, idx, 244, 247, 252, max(0.0, min(1.0, (aa - d_out) / (2.0 * aa))) * 0.90)
                if d_shape < aa:
                    a_s = max(0.0, min(1.0, (aa - d_shape) / (2.0 * aa)))
                    _blend_px(buf, idx, 74, 86, 108, a_s)
                    if d_detail < aa:
                        a_d = max(0.0, min(1.0, (aa - d_detail) / (2.0 * aa)))
                        _blend_px(buf, idx, 232, 238, 248, a_d)

                if alert:
                    d_dot = math.hypot(nx - 0.62, ny + 0.62) - 0.20
                    if d_dot < aa:
                        _blend_px(buf, idx, 255, 255, 255, max(0.0, min(1.0, (aa - d_dot) / (2.0 * aa))))
                    d_in = math.hypot(nx - 0.62, ny + 0.62) - 0.14
                    if d_in < aa:
                        _blend_px(buf, idx, 224, 114, 114, max(0.0, min(1.0, (aa - d_in) / (2.0 * aa))))

    # 底部叠加当前最低设备百分比进度条
    bar_h = max(2, round(size * 0.13))
    pad = max(2, round(size * 0.14))
    bar_w = max(2, size - 2 * pad)
    pct = 0.0 if level is None else max(0.0, min(1.0, float(level) / 100.0))
    fill_w = max(1 if level is not None else 0, round(bar_w * pct))
    for y in range(size - bar_h - 1, size - 1):
        for x in range(pad, size - pad):
            idx = (y * size + x) * 4
            if x - pad < fill_w:
                _blend_px(buf, idx, color[0], color[1], color[2], 1.0)
            else:
                _blend_px(buf, idx, 36, 42, 54, 0.92)
    return bytes(buf)


def render_style_rgba(
    style: str,
    size: int,
    level: Optional[int],
    color: tuple[int, int, int],
    *,
    alert: bool = False,
    role: str = "controller",
    charging: bool = False,
) -> bytes:
    st = style if style in ("digit", "ring", "dot", "lowest_device") else "digit"
    if st == "ring":
        return render_ring_rgba(size, level, color, alert=alert, charging=charging)
    if st == "dot":
        return render_dot_rgba(size, color, alert=alert, charging=charging)
    if st == "lowest_device":
        return render_device_emblem_rgba(size, role, level, color, alert=alert)
    # digit 模式：有有效电量显示数字/条，否则回退经典手柄徽标
    if level is not None:
        return render_rgba(str(level), size, color, plate=True, role=role)
    return render_controller_logo_rgba(size, alert=alert)


def make_tray_style_hicon(
    style: str,
    level: Optional[int],
    color: tuple[int, int, int],
    *,
    alert: bool = False,
    role: str = "controller",
    charging: bool = False,
) -> int:
    """根据托盘图标样式生成并缓存多分辨率 HICON。"""
    st = style if style in ("digit", "ring", "dot", "lowest_device") else "digit"
    q_lvl = level if (st in ("digit", "lowest_device") or level is None) else round(level / 5) * 5
    cache_key = ("style", st, q_lvl, color, bool(alert), role, bool(charging))
    if cache_key in _icon_cache:
        return _icon_cache[cache_key]

    sizes = (16, 20, 24, 32, 48)
    images = [
        (s, render_style_rgba(st, s, level, color, alert=alert, role=role, charging=charging))
        for s in sizes
    ]
    ico = build_ico(images)
    safe_r = "".join(ch for ch in (role or "x") if ch.isalnum()) or "x"
    lvl_tag = "na" if q_lvl is None else str(q_lvl)
    path = os.path.join(
        _ensure_tmp(),
        f"ico_st_{st}_{lvl_tag}_{color[0]:02x}{color[1]:02x}{color[2]:02x}_{int(bool(alert))}_{safe_r}_{int(bool(charging))}.ico",
    )
    try:
        with open(path, "wb") as f:
            f.write(ico)
    except OSError:
        return 0
    h = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
    hicon = int(h) if h else 0
    _icon_cache[cache_key] = hicon
    return hicon


def render_style_preview_ppm(
    style: str,
    level: Optional[int],
    color: tuple[int, int, int],
    *,
    alert: bool = False,
    role: str = "mouse",
    charging: bool = False,
    bg_rgb: tuple[int, int, int] = (232, 236, 243),
    size: int = 36,
) -> bytes:
    """生成可直接传给 tk.PhotoImage(data=...) 的 PPM P6 实时预览图像。"""
    rgba = render_style_rgba(
        style, size, level, color, alert=alert, role=role, charging=charging
    )
    rgb = bytearray(size * size * 3)
    br, bg, bb = bg_rgb
    for i in range(size * size):
        sr, sg, sb, sa = rgba[i * 4 : i * 4 + 4]
        a = sa / 255.0
        ia = 1.0 - a
        rgb[i * 3] = max(0, min(255, int(round(sr * a + br * ia))))
        rgb[i * 3 + 1] = max(0, min(255, int(round(sg * a + bg * ia))))
        rgb[i * 3 + 2] = max(0, min(255, int(round(sb * a + bb * ia))))
    header = f"P6\n{size} {size}\n255\n".encode("ascii")
    return header + bytes(rgb)


def make_controller_hicon(alert: bool = False) -> int:
    """生成常驻任务栏手柄 Logo 托盘 HICON（支持低电量红点徽标）。"""
    key = ("controller_logo", bool(alert))
    if key in _icon_cache:
        return _icon_cache[key]
    images = [(s, render_controller_logo_rgba(s, alert=alert)) for s in (16, 20, 24, 32, 48)]
    ico = build_ico(images)
    path = os.path.join(_ensure_tmp(), f"ico_controller_{int(bool(alert))}.ico")
    try:
        with open(path, "wb") as f:
            f.write(ico)
    except OSError:
        return 0
    h = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
    hicon = int(h) if h else 0
    _icon_cache[key] = hicon
    return hicon


def make_hicon(
    text: str,
    color: tuple[int, int, int],
    plate: bool = False,
    role: str = "",
) -> int:
    """按文字+颜色+角色生成带缓存的 HICON；失败返回 0。"""
    key = (text, color, bool(plate), role)
    if key in _icon_cache:
        return _icon_cache[key]
    images = [(s, render_rgba(text, s, color, plate=plate, role=role)) for s in SIZES]
    ico = build_ico(images)
    safe_txt = "".join(ch if ch.isalnum() else "d" for ch in text)
    safe_role = "".join(ch for ch in role if ch.isalnum()) or "none"
    path = os.path.join(
        _ensure_tmp(),
        f"ico_{safe_txt}_{color[0]:02x}{color[1]:02x}{color[2]:02x}_{int(plate)}_{safe_role}.ico",
    )
    try:
        with open(path, "wb") as f:
            f.write(ico)
    except OSError:
        return 0
    h = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
    hicon = int(h) if h else 0
    _icon_cache[key] = hicon
    return hicon


def destroy_all() -> None:
    for h in _icon_cache.values():
        if h:
            try:
                user32.DestroyIcon(ctypes.c_void_p(h))
            except Exception:
                pass
    _icon_cache.clear()


def cleanup_files() -> None:
    if _tmp_dir and os.path.isdir(_tmp_dir):
        for name in os.listdir(_tmp_dir):
            try:
                os.remove(os.path.join(_tmp_dir, name))
            except OSError:
                pass
