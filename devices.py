# -*- coding: utf-8 -*-
"""devices.py —— 外设发现、分组、电量读取与休眠缓存管理

供主界面 (my_devices.py) 与系统托盘 (tray_app.py) 共用，确保显示一致、零重复逻辑。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import battery
import battery_history
import bt_battery
import tray_common
from hid_win import HidInterface, enumerate_hid

BAR_GREEN = "#7FAFC7"
BAR_YELLOW = "#D8C68A"
BAR_RED = "#C07A80"       # 与文本红 #a84848 同色相的中明度警示条色
BAR_AMBER = "#D4B28C"
GRAY_DISABLED = "#8a93a2"

ROLE_ORDER = {"mouse": 0, "keyboard": 1, "headset": 2, "controller": 3, "other": 4}
ROLE_LABEL = {
    "mouse": "鼠标",
    "keyboard": "键盘",
    "headset": "耳机",
    "controller": "手柄",
    "other": "外设",
}

# 明确不属于无线电池外设的系统/灯控虚拟 VID
IGNORED_VIDS = {0x0B05, 0x00FF}
KNOWN_PROTOCOL_VIDS = {
    0x3837, 0x5253, 0x258A, 0x3554, 0x41E4,
    0x35BB, 0x37D7, 0x373B, 0x3654, 0x046D,
    0x054C, 0x1532, 0x1038, 0x045E,
}
WIRELESS_KEYWORDS = (
    "2.4g", "dongle", "receiver", "无线", "接收器",
    "hecate", "mchose", "flydigi", "atk", "vxe", "vgn",
    "razer", "steelseries", "dualsense", "surface",
)
NON_PERIPH_KEYWORDS = ("aura", "led controller", "virtual", "hid-compliant consumer", "system control")


@dataclass
class PeripheralReading:
    key: str
    role: str
    name: str
    product: str
    vid: Optional[int] = None
    pid: Optional[int] = None
    serial: str = ""
    connected: bool = True
    offline: bool = False
    level: Optional[int] = None
    raw_level: Optional[int] = None
    interpolated: bool = False
    voltage_mv: Optional[int] = None
    temperature_c: Optional[float] = None
    charging: bool = False
    full: bool = False
    asleep: bool = False
    stale: bool = False
    pinned: bool = False
    manual: bool = False
    connection: str = ""
    source: str = ""
    detail: str = ""
    error: str = ""
    last_read_error: str = ""
    raw_hex: str = ""
    updated_at: Optional[float] = None
    transport: str = "HID"  # HID / BT / BLE
    estimate_hours: Optional[float] = None
    discharge_rate_per_h: Optional[float] = None
    deplete_at: Optional[float] = None
    charge_eta_minutes: Optional[float] = None
    charge_eta_at: Optional[float] = None
    estimate_text: str = "暂无预估数据"
    parts: list[dict[str, Any]] = field(default_factory=list)
    ifaces: list[HidInterface] = field(default_factory=list, repr=False)

    @property
    def vidpid_text(self) -> str:
        if self.vid is None or self.pid is None:
            return "Bluetooth"
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def voltage_text(self) -> str:
        if not self.offline and self.level is not None and self.voltage_mv is not None:
            if 2500 <= int(self.voltage_mv) <= 4500:
                return f"{int(self.voltage_mv) / 1000.0:.2f}V"
        return ""

    @property
    def is_wired(self) -> bool:
        c = (self.connection or "").lower()
        return ("有线" in c) or ("usb" in c)

    @property
    def power_state(self) -> str:
        if self.offline or self.level is None:
            return "offline"
        if self.full:
            return "full"
        if self.charging:
            return "charging"
        if self.is_wired:
            return "wired_power"
        return "wireless"

    @property
    def level_text(self) -> str:
        if self.offline or self.level is None:
            return "--"
        return f"~{self.level}%" if self.interpolated else f"{self.level}%"

    @property
    def status_text(self) -> str:
        if self.offline or not self.connected or self.asleep:
            return "已离线"
        if self.level is not None:
            if self.stale:
                return f"{self.connection + ' · ' if self.connection else ''}沿用蓝牙电量"
            if self.full:
                return f"{self.connection + ' · ' if self.connection else ''}已充满"
            if self.charging:
                return f"{self.connection + ' · ' if self.connection else ''}充电中"
            if self.is_wired:
                return "有线供电（未充电）"
            if self.interpolated:
                return f"{self.connection or '蓝牙'} · 平滑估算"
            return f"{self.connection or '已连接'} · 正常"
        if self.connected and "2.4g" in (self.connection or "").lower():
            return "2.4G · 不支持电量回传"
        err = self.error or ""
        if any(k in err for k in ("休眠", "未回应", "未收到", "断开")):
            return "已离线"
        return "暂未读到电量"


@dataclass
class ScanStats:
    started_at: float = 0.0
    ended_at: float = 0.0
    duration_ms: float = 0.0
    hid_iface_count: int = 0
    hid_group_count: int = 0
    bt_count: int = 0
    ble_gatt_count: int = 0
    matched_count: int = 0
    online_count: int = 0
    offline_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    skipped_offline_enum: bool = False
    background_mode: bool = False
    skipped_devices: list[dict[str, Any]] = field(default_factory=list)
    protocol_counts: dict[str, int] = field(default_factory=dict)
    per_device: list[dict[str, Any]] = field(default_factory=list)

    def summary_text(self) -> str:
        parts = [
            f"耗时 {self.duration_ms:.0f}ms",
            f"HID接口 {self.hid_iface_count}（分组 {self.hid_group_count}）",
            f"蓝牙 {self.bt_count}" + (f"（含 BLE {self.ble_gatt_count}）" if self.ble_gatt_count else ""),
            f"在线 {self.online_count}/离线 {self.offline_count}",
        ]
        if self.failed_count:
            parts.append(f"读取失败 {self.failed_count}")
        if self.skipped_count:
            parts.append(f"跳过 {self.skipped_count}")
        if self.protocol_counts:
            top = ", ".join(f"{k}:{v}" for k, v in list(self.protocol_counts.items())[:4])
            parts.append(f"协议命中[{top}]")
        return " · ".join(parts)


_LAST_SCAN_STATS: Optional[ScanStats] = None


def get_last_scan_stats() -> ScanStats:
    if _LAST_SCAN_STATS is None:
        return ScanStats()
    return _LAST_SCAN_STATS


def _device_key(vid: int, pid: int, serial: str) -> str:
    s = (serial or "").strip()
    return f"hid:{vid:04X}:{pid:04X}:{s}" if s else f"hid:{vid:04X}:{pid:04X}"


def _classify_skip_reason(vid: int, pid: int, ifaces: list[HidInterface]) -> tuple[bool, str]:
    """返回 (是否值得提示用户去标定, 跳过原因说明)。"""
    if vid in IGNORED_VIDS:
        return False, "系统虚拟/灯控设备 (已自动排除)"
    prod = " ".join((i.product or "") for i in ifaces).lower()
    if any(bad in prod for bad in NON_PERIPH_KEYWORDS):
        return False, "非外设通用系统集合"
    has_vendor = any(i.usage_page >= 0xFF00 for i in ifaces)
    has_rw = any(i.feature_len > 0 or i.output_len > 0 for i in ifaces)
    has_prod = any(bool((i.product or "").strip()) for i in ifaces)
    if vid > 0 and (has_vendor or (has_rw and has_prod)):
        return True, "未命中内置/自定义电量协议与关键词"
    return False, "常规有线/系统 HID 接口"


def _is_candidate_hid_group(
    vid: int,
    pid: int,
    ifaces: list[HidInterface],
    user_specs: list[battery.ProtocolSpec],
    manual_vidpids: Optional[set[tuple[int, int]]] = None,
) -> bool:
    if manual_vidpids and (vid, pid) in manual_vidpids:
        return True
    if any((sp.vid is None or sp.vid == vid) and (sp.pid is None or sp.pid == pid) for sp in user_specs):
        return True
    if (vid, pid) in battery.KNOWN_DEVICES or vid in KNOWN_PROTOCOL_VIDS:
        return True
    if vid in IGNORED_VIDS:
        return False
    if any(i.usage_page in (0x0006, 0x0085) for i in ifaces):
        return True
    prod = " ".join((i.product or "") for i in ifaces).lower()
    if any(bad in prod for bad in NON_PERIPH_KEYWORDS):
        return False
    if any(kw in prod for kw in WIRELESS_KEYWORDS):
        return True
    return False


def _manual_vidpid_set(cfg: dict[str, Any]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    for raw in cfg.get("manual_devices") or []:
        vp = tray_common.parse_vid_pid(str(raw))
        if vp is not None:
            out.add(vp)
    return out


def sort_readings(
    readings: list[PeripheralReading],
    sort_mode: str = "default",
    pinned_keys: Optional[set[str]] = None,
) -> list[PeripheralReading]:
    """按置顶状态与用户排序模式排列读取结果。"""
    p_set = set(pinned_keys) if pinned_keys is not None else None
    for r in readings:
        if p_set is not None:
            r.pinned = r.key in p_set

    mode = sort_mode if sort_mode in tray_common.VALID_SORT_MODES else "default"

    def _key_fn(r: PeripheralReading) -> tuple[Any, ...]:
        pin_rank = 0 if r.pinned else 1
        online_rank = 0 if (r.level is not None and not r.offline) else 1
        if mode == "low_first":
            lvl_rank = r.level if (r.level is not None and not r.offline) else 999
            return (pin_rank, online_rank, lvl_rank, ROLE_ORDER.get(r.role, 9), r.name.lower(), r.key)
        if mode == "name":
            return (pin_rank, r.name.lower(), ROLE_ORDER.get(r.role, 9), r.key)
        if mode == "recent":
            return (pin_rank, online_rank, -(float(r.updated_at or 0.0)), ROLE_ORDER.get(r.role, 9), r.name.lower(), r.key)
        return (pin_rank, ROLE_ORDER.get(r.role, 9), online_rank, r.name.lower(), r.key)

    readings.sort(key=_key_fn)
    return readings


_BT_PROFILE_SUFFIX_RE = re.compile(
    r"(?:\s*[-_·]?\s*(?:hands[- ]?free(?:\s+ag)?(?:\s+audio)?|ag\s+audio|avrcp\s*传输|avrcp|stereo|免提))+\s*$",
    re.IGNORECASE,
)

_DUAL_MODE_NOISE_RE = re.compile(
    r"(?:hands[- ]?free(?:\s+ag)?(?:\s+audio)?|ag\s+audio|avrcp|stereo|"
    r"headset|headphone|earphone|wireless|bluetooth|dongle|receiver|2\.4g|ble|"
    r"无线耳机|无线版|耳机|接收器|蓝牙|免提)",
    re.IGNORECASE,
)


def _strip_bt_profile_suffix(name: str) -> str:
    """去除 Windows 蓝牙节点尾部的 Hands-Free AG / 免提 等后缀。"""
    cleaned = _BT_PROFILE_SUFFIX_RE.sub("", (name or "").strip()).strip(" -_·")
    return cleaned or (name or "").strip()


def _dual_mode_canonical_tokens(name: str, product: str = "") -> set[str]:
    """提取用于识别同设备双模（蓝牙 + 2.4G HID）的规范化型号标识。"""
    tokens: set[str] = set()
    for raw in (name, product):
        if not raw:
            continue
        stripped = _DUAL_MODE_NOISE_RE.sub(" ", raw.lower())
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", stripped)
        if len(compact) >= 4:
            tokens.add(compact)
    return tokens


def _is_dual_mode_pair(hid_r: PeripheralReading, bt_r: PeripheralReading) -> bool:
    """判断一条 HID（2.4G）记录与一条蓝牙记录是否属于同一台双模外设。"""
    if hid_r.transport != "HID" or bt_r.transport not in ("BT", "BLE"):
        return False
    if hid_r.role != bt_r.role:
        return False
    hid_tokens = _dual_mode_canonical_tokens(hid_r.name, hid_r.product)
    bt_tokens = _dual_mode_canonical_tokens(bt_r.name, bt_r.product)
    return bool(hid_tokens & bt_tokens)


def _last_valid_history_level(hist: dict[str, Any], key: str) -> tuple[Optional[int], Optional[float]]:
    devices_map = hist.get("devices") if isinstance(hist, dict) else None
    if not isinstance(devices_map, dict):
        return None, None
    series = devices_map.get(key)
    if not isinstance(series, list):
        return None, None
    for pt in reversed(series):
        if not isinstance(pt, dict):
            continue
        if pt.get("off"):
            continue
        lvl = pt.get("lvl")
        if isinstance(lvl, (int, float)) and 0 <= int(lvl) <= 100:
            t_val = pt.get("t")
            return int(lvl), float(t_val) if isinstance(t_val, (int, float)) else None
    return None, None


def _merge_dual_mode_readings(
    readings: list[PeripheralReading],
    *,
    known_map: dict[str, dict[str, Any]],
    names_map: dict[str, str],
    bt_cached_levels: dict[str, int],
    hist: dict[str, Any],
    stats: ScanStats,
    now: float,
) -> tuple[list[PeripheralReading], bool]:
    """合并同一台双模外设的蓝牙卡片与 2.4G HID 卡片为单张卡片。"""
    bt_indices = [i for i, r in enumerate(readings) if r.transport in ("BT", "BLE")]
    hid_indices = [i for i, r in enumerate(readings) if r.transport == "HID"]
    if not bt_indices or not hid_indices:
        return readings, False

    drop_indices: set[int] = set()
    used_hid: set[int] = set()
    cfg_dirty = False

    for bi in bt_indices:
        bt_r = readings[bi]
        matched_hi: Optional[int] = None
        for hi in hid_indices:
            if hi in used_hid:
                continue
            if _is_dual_mode_pair(readings[hi], bt_r):
                matched_hi = hi
                break
        if matched_hi is None:
            continue

        hid_r = readings[matched_hi]
        used_hid.add(matched_hi)
        drop_indices.add(matched_hi)

        # 合并自定义名称 / 置顶状态，并保留 2.4G 硬件信息便于标定调试
        custom_name = names_map.get(bt_r.key) or names_map.get(hid_r.key)
        if custom_name:
            bt_r.name = custom_name
        else:
            bt_r.name = _strip_bt_profile_suffix(bt_r.name) or hid_r.name
        bt_r.pinned = bool(bt_r.pinned or hid_r.pinned)
        bt_r.vid = hid_r.vid
        bt_r.pid = hid_r.pid
        bt_r.serial = hid_r.serial
        bt_r.ifaces = list(hid_r.ifaces)

        meta = known_map.get(bt_r.key)
        if isinstance(meta, dict):
            if meta.get("paired_hid_key") != hid_r.key:
                meta["paired_hid_key"] = hid_r.key
                cfg_dirty = True
            clean_meta_name = _strip_bt_profile_suffix(str(meta.get("name") or ""))
            if clean_meta_name and meta.get("name") != clean_meta_name:
                meta["name"] = clean_meta_name
                cfg_dirty = True

        hid_present = bool(hid_r.ifaces)
        bt_online = (not bt_r.offline) and (bt_r.level is not None)
        hid_live_level = (not hid_r.offline) and (hid_r.level is not None)

        if bt_online:
            # 蓝牙在线：优先采用实时蓝牙电量，同时标注已合并 2.4G 接收器
            bt_r.connection = "蓝牙 + 2.4G" if hid_present else (bt_r.connection or "蓝牙")
            bt_r.stale = False
            bt_r.offline = False
            bt_r.connected = True
            bt_r.last_read_error = ""
            bt_r.error = ""
            if hid_present:
                step_hint = "蓝牙 HFP 10% 步进" if bt_r.transport == "BT" else "BLE GATT"
                bt_r.detail = f"电量 {bt_r.level}%（{step_hint} · 已合并 2.4G 接收器）"
        elif hid_live_level:
            # 蓝牙断开但 2.4G 读到真实电量
            bt_r.level = hid_r.level
            bt_r.voltage_mv = hid_r.voltage_mv
            bt_r.charging = hid_r.charging
            bt_r.full = hid_r.full
            bt_r.asleep = False
            bt_r.offline = False
            bt_r.connected = True
            bt_r.stale = False
            bt_r.connection = hid_r.connection or "2.4G"
            bt_r.source = hid_r.source
            bt_r.detail = hid_r.detail
            bt_r.error = ""
            bt_r.last_read_error = ""
            bt_r.raw_hex = hid_r.raw_hex
            bt_r.updated_at = hid_r.updated_at or now
        elif hid_present:
            # 蓝牙断开但 2.4G 接收器在线且不支持硬件电量回传：合并显示 2.4G 在线并沿用最近一次蓝牙读数
            cached_lvl: Optional[int] = bt_cached_levels.get(bt_r.key)
            cached_ts: Optional[float] = None
            if isinstance(meta, dict):
                if cached_lvl is None and isinstance(meta.get("last_level"), (int, float)):
                    cached_lvl = int(meta["last_level"])
                if isinstance(meta.get("last_level_at"), (int, float)):
                    cached_ts = float(meta["last_level_at"])
                elif isinstance(meta.get("last_seen"), (int, float)):
                    cached_ts = float(meta["last_seen"])
            if cached_lvl is None:
                hist_lvl, hist_ts = _last_valid_history_level(hist, bt_r.key)
                cached_lvl = hist_lvl
                if hist_ts is not None:
                    cached_ts = hist_ts

            bt_r.connected = True
            bt_r.offline = False
            bt_r.asleep = False
            bt_r.charging = False
            bt_r.full = False
            bt_r.connection = hid_r.connection or "2.4G"
            bt_r.level = cached_lvl
            bt_r.stale = cached_lvl is not None
            bt_r.updated_at = cached_ts or bt_r.updated_at
            bt_r.error = ""
            bt_r.last_read_error = ""
            if cached_lvl is not None:
                bt_r.source = "2.4G 在线（沿用上次蓝牙电量）"
                bt_r.detail = f"2.4G 接收器不支持电量回传 · 沿用上次蓝牙读数 {cached_lvl}%（切蓝牙可刷新）"
            else:
                bt_r.source = "2.4G 在线（不支持电量回传）"
                bt_r.detail = "2.4G 接收器已连接（固件不支持回传电量，切蓝牙可同步电量）"
        else:
            # 蓝牙与 2.4G 均离线
            bt_r.connection = "蓝牙 / 2.4G"

        # 合并后统计去重
        if hid_r.offline:
            stats.offline_count = max(0, stats.offline_count - 1)
        elif hid_live_level:
            stats.online_count = max(0, stats.online_count - 1)
        else:
            stats.failed_count = max(0, stats.failed_count - 1)

        if not bt_online and (hid_live_level or hid_present):
            stats.offline_count = max(0, stats.offline_count - 1)
            stats.online_count += 1

    if not drop_indices:
        return readings, cfg_dirty

    merged_list = [r for i, r in enumerate(readings) if i not in drop_indices]
    return merged_list, cfg_dirty


HEADSET_24G_POWER_FACTOR = 1.5


def _populate_history_metrics(
    readings: list[PeripheralReading],
    hist: dict[str, Any],
    now: float,
    *,
    max_gap_sec: float = battery_history.BT_QUANTIZED_MAX_GAP_SEC,
) -> None:
    for r in readings:
        series = battery_history.get_series(hist, r.key, window_hours=7 * 24, now=now)
        raw_lvl = r.level
        r.raw_level = raw_lvl
        r.interpolated = False
        interp_res: Optional[battery_history.SmoothInterpolationResult] = None
        if (
            r.transport == "BT"
            and not r.offline
            and not r.stale
            and not r.charging
            and not r.full
            and raw_lvl is not None
        ):
            interp_res = battery_history.interpolate_quantized_bt_level(
                series,
                raw_lvl,
                now=now,
                max_gap_sec=max_gap_sec,
            )
            if interp_res.interpolated:
                r.level = interp_res.level
                r.interpolated = True
                rate_val = interp_res.rate_per_h or battery_history.DEFAULT_BT_QUANTIZED_RATE_PER_H
                if interp_res.rate_source == "learned":
                    rate_desc = f"已学习 {rate_val:.1f}%/h 平滑推算"
                else:
                    rate_desc = f"默认 {rate_val:.1f}%/h 推算，待首次掉档校准"
                merged_tag = " · 已合并 2.4G" if "2.4g" in (r.connection or "").lower() else ""
                r.detail = f"估算 ~{interp_res.level}%（硬件上报 {interp_res.raw_level}% · {rate_desc}{merged_tag}）"

        est, rate_h, dep_ts = battery_history.estimate_discharge_details(
            series, raw_lvl, charging=r.charging, offline=r.offline, full=r.full, now=now
        )
        if (
            interp_res is not None
            and interp_res.quantized
            and interp_res.rate_source == "learned"
            and interp_res.rate_per_h
            and interp_res.rate_per_h > 0
            and r.level is not None
        ):
            rate_h = round(float(interp_res.rate_per_h), 1)
            est = round(max(0.2, min(500.0, float(r.level) / rate_h)), 1)
            dep_ts = now + est * 3600.0
        elif est is not None and rate_h and rate_h > 0 and r.interpolated and r.level is not None:
            est = round(max(0.2, min(500.0, float(r.level) / rate_h)), 1)
            dep_ts = now + est * 3600.0

        # 耳机切换至纯 2.4G 模式时功耗增加 50%：按蓝牙基线速率 × 1.5 重新估算剩余续航
        conn_l = (r.connection or "").lower()
        is_headset_24g_only = (
            r.role == "headset"
            and "2.4g" in conn_l
            and "蓝牙" not in conn_l
            and not r.offline
            and not r.charging
            and not r.full
            and r.level is not None
        )
        if is_headset_24g_only:
            base_rate = float(rate_h) if (rate_h and rate_h > 0) else None
            if base_rate is None and r.transport in ("BT", "BLE"):
                learned_q, _ = battery_history._learn_quantized_step_rate(
                    series, ts_now=now, max_gap_sec=max_gap_sec
                )
                base_rate = (
                    float(learned_q)
                    if (learned_q and learned_q > 0)
                    else float(battery_history.DEFAULT_BT_QUANTIZED_RATE_PER_H)
                )
            if base_rate is not None and base_rate > 0:
                rate_h = round(base_rate * HEADSET_24G_POWER_FACTOR, 1)
                est = round(max(0.2, min(500.0, float(r.level) / rate_h)), 1)
                dep_ts = now + est * 3600.0
                if r.stale and "沿用上次蓝牙读数" in (r.detail or ""):
                    r.detail = (
                        f"2.4G 接收器不支持电量回传 · 沿用上次蓝牙读数 {int(r.level)}%"
                        f"（2.4G 功耗 +50% · 按 {rate_h:.1f}%/h 估算）"
                    )

        chg_mins, _, chg_ts = battery_history.estimate_charge_details(
            series, raw_lvl, charging=r.charging, offline=r.offline, full=r.full, now=now
        )
        r.estimate_hours = est
        r.discharge_rate_per_h = rate_h
        r.deplete_at = dep_ts
        r.charge_eta_minutes = chg_mins
        r.charge_eta_at = chg_ts
        r.estimate_text = battery_history.format_remaining_text(
            est,
            offline=r.offline,
            charging=r.charging,
            full=r.full,
            discharge_rate_per_h=rate_h,
            deplete_ts=dep_ts,
            charge_mins=chg_mins,
            show_charge_eta=r.charging,
        )


def scan_peripherals_with_stats(
    cfg: Optional[dict[str, Any]] = None,
    include_hidden: bool = False,
    save_cache: bool = True,
    *,
    background: bool = False,
    include_ignored: bool = False,
    only_keys: Optional[set[str]] = None,
) -> tuple[list[PeripheralReading], ScanStats]:
    """扫描外设电量并返回 (readings, ScanStats)。"""
    global _LAST_SCAN_STATS
    if cfg is None:
        cfg = tray_common.load_config()

    t_start = time.time()
    t_mono0 = time.monotonic()

    names_map: dict[str, str] = dict(cfg.get("device_names") or {})
    roles_map: dict[str, str] = dict(cfg.get("device_roles") or {})
    hidden_set = set(cfg.get("hidden_devices") or [])
    ignored_set = set(cfg.get("ignored_devices") or [])
    pinned_set = set(cfg.get("pinned") or [])
    manual_vidpids = _manual_vidpid_set(cfg)
    known_map: dict[str, dict[str, Any]] = dict(cfg.get("known_devices") or {})
    user_specs = battery.load_user_protocols()

    readings: list[PeripheralReading] = []
    seen_keys: set[str] = set()
    online_keys_now: set[str] = set()
    bt_cached_levels: dict[str, int] = {}
    cfg_dirty = False
    now = t_start

    stats = ScanStats(started_at=t_start, background_mode=bool(background))
    all_ifaces = enumerate_hid()
    stats.hid_iface_count = len(all_ifaces)
    groups = battery.group_hid_devices(all_ifaces)
    stats.hid_group_count = len(groups)

    skipped_seen_vp: set[tuple[int, int]] = set()

    for (vid, pid, serial), ifaces in groups.items():
        is_manual = (vid, pid) in manual_vidpids
        key = _device_key(vid, pid, serial)
        if only_keys is not None and key not in only_keys:
            continue

        if key in ignored_set and not include_ignored:
            stats.per_device.append({
                "key": key, "name": key, "vidpid": f"{vid:04X}:{pid:04X}",
                "transport": "HID", "result": "ignored", "level": None, "error": "用户已忽略",
            })
            continue

        if not _is_candidate_hid_group(vid, pid, ifaces, user_specs, manual_vidpids):
            notify_skip, reason = _classify_skip_reason(vid, pid, ifaces)
            if notify_skip and (vid, pid) not in skipped_seen_vp:
                skipped_seen_vp.add((vid, pid))
                first_sk = ifaces[0]
                sk_name = battery.friendly_device_name(vid, pid, first_sk.product or "", first_sk.manufacturer)
                stats.skipped_devices.append({
                    "vid": vid,
                    "pid": pid,
                    "vidpid": f"{vid:04X}:{pid:04X}",
                    "name": sk_name,
                    "product": first_sk.product or "",
                    "usage_page": first_sk.usage_page,
                    "usage": first_sk.usage,
                    "iface_count": len(ifaces),
                    "reason": reason,
                })
            continue

        seen_keys.add(key)
        if not include_hidden and key in hidden_set:
            continue

        first = ifaces[0]
        raw_prod = first.product or ""
        inferred_role = battery.infer_role(vid, pid, raw_prod, first.usage_page, first.usage)
        role = roles_map.get(key) if roles_map.get(key) in ROLE_ORDER else inferred_role
        default_name = battery.friendly_device_name(vid, pid, raw_prod, first.manufacturer)

        res = battery.read_device_battery(ifaces, user_specs)
        if res.detected_model and (vid, pid) not in battery.KNOWN_DEVICES:
            default_name = res.detected_model

        display_name = names_map.get(key) or default_name
        known_conn = battery.KNOWN_DEVICES.get((vid, pid), ("", "", ""))[2]

        level = res.level
        full = bool(res.full)
        charging = bool(res.charging) and not full
        asleep = bool(res.asleep)
        connection = res.connection or known_conn
        source = res.source
        detail = res.detail
        error = res.error
        voltage_mv = (
            int(res.voltage_mv)
            if (level is not None and res.voltage_mv is not None and 2500 <= int(res.voltage_mv) <= 4500)
            else None
        )
        updated_at: Optional[float] = now if level is not None else None
        prev_known = known_map.get(key) or {}
        had_battery = bool(prev_known.get("had_battery"))

        if level is not None:
            offline = False
            online_keys_now.add(key)
            stats.online_count += 1
            stats.matched_count += 1
            proto_key = source.split(" ")[0] if source else "HID"
            stats.protocol_counts[proto_key] = stats.protocol_counts.get(proto_key, 0) + 1
            last_read_err = ""
            known_map[key] = {
                "name": default_name,
                "role": role,
                "connection": connection,
                "vid": vid,
                "pid": pid,
                "last_seen": now,
                "had_battery": True,
                "transport": "HID",
            }
            cfg_dirty = True
            stats.per_device.append({
                "key": key, "name": display_name, "vidpid": f"{vid:04X}:{pid:04X}",
                "transport": "HID", "result": "ok", "level": level, "voltage_mv": voltage_mv,
                "source": source, "error": "",
            })
        else:
            if is_manual and not asleep and not had_battery:
                offline = False
                if not detail:
                    detail = "未匹配协议，可去标定"
                if not error:
                    error = "未匹配协议，可去标定"
            else:
                offline = bool(
                    asleep
                    or had_battery
                    or any(k in (error or "") for k in ("休眠", "未回应", "未收到", "断开"))
                )
            level = None
            voltage_mv = None
            charging = False
            full = False
            last_read_err = error or ("设备休眠" if asleep else "")
            if offline:
                stats.offline_count += 1
            else:
                stats.failed_count += 1
            stats.per_device.append({
                "key": key, "name": display_name, "vidpid": f"{vid:04X}:{pid:04X}",
                "transport": "HID",
                "result": "offline" if offline else ("manual_unmatched" if is_manual else "failed"),
                "level": None, "voltage_mv": None, "source": source, "error": last_read_err,
            })

        readings.append(
            PeripheralReading(
                key=key,
                role=role,
                name=display_name,
                product=raw_prod,
                vid=vid,
                pid=pid,
                serial=serial,
                connected=not offline,
                offline=offline,
                level=level,
                voltage_mv=voltage_mv,
                temperature_c=getattr(res, "temperature_c", None),
                charging=charging,
                full=full,
                asleep=asleep,
                stale=False,
                pinned=(key in pinned_set),
                manual=is_manual,
                connection=connection,
                source=source,
                detail=detail,
                error=error,
                last_read_error=last_read_err,
                raw_hex=res.raw_hex,
                updated_at=updated_at,
                transport="HID",
                ifaces=ifaces,
            )
        )

    # 附加带电量的蓝牙外设（后台节能模式可跳过纯离线 BT 项枚举）
    try:
        bt_list = bt_battery.find_battery_devices()
        stats.bt_count = len(bt_list)
        for bt in bt_list:
            key = f"bt:{bt.instance_id}"
            if only_keys is not None and key not in only_keys:
                continue
            if key in ignored_set and not include_ignored:
                continue
            seen_keys.add(key)
            if not include_hidden and key in hidden_set:
                continue
            prev_known = known_map.get(key) or {}
            had_bt = bool(prev_known.get("had_battery"))
            if bt.battery is not None and 0 <= int(bt.battery) <= 100:
                bt_cached_levels[key] = int(bt.battery)
            is_online = (bt.battery is not None) and bool(getattr(bt, "online", True))
            if background and not is_online:
                stats.skipped_offline_enum = True
                continue
            if bt.battery is None and not had_bt:
                continue
            inferred_role = battery.infer_role(0, 0, bt.name)
            role = roles_map.get(key) if roles_map.get(key) in ROLE_ORDER else inferred_role
            clean_bt_name = _strip_bt_profile_suffix(bt.name)
            display_name = names_map.get(key) or clean_bt_name
            bt_src = (getattr(bt, "source", "") or "Windows 蓝牙 HFP PnP").strip()
            bt_parts = list(getattr(bt, "parts", None) or [])
            is_ble = "BLE" in bt_src.upper()
            conn_label = "BLE GATT" if is_ble else "蓝牙"
            tr_label = "BLE" if is_ble else "BT"
            proto_key = "BLE_GATT" if is_ble else "BT_HFP"
            if is_online and bt.battery is not None:
                online_keys_now.add(key)
                stats.online_count += 1
                stats.matched_count += 1
                if is_ble:
                    stats.ble_gatt_count += 1
                stats.protocol_counts[proto_key] = stats.protocol_counts.get(proto_key, 0) + 1
                prev_paired_hid = prev_known.get("paired_hid_key")
                known_entry = {
                    "name": clean_bt_name,
                    "role": role,
                    "connection": conn_label,
                    "vid": None,
                    "pid": None,
                    "last_seen": now,
                    "last_level": int(bt.battery),
                    "last_level_at": now,
                    "had_battery": True,
                    "transport": tr_label,
                }
                if prev_paired_hid:
                    known_entry["paired_hid_key"] = prev_paired_hid
                known_map[key] = known_entry
                cfg_dirty = True
                stats.per_device.append({
                    "key": key, "name": display_name, "vidpid": "Bluetooth",
                    "transport": tr_label, "result": "ok", "level": bt.battery,
                    "voltage_mv": None, "source": bt_src, "parts": bt_parts, "error": "",
                })
            else:
                stats.offline_count += 1
                stats.per_device.append({
                    "key": key, "name": display_name, "vidpid": "Bluetooth",
                    "transport": tr_label, "result": "offline", "level": None,
                    "voltage_mv": None, "source": bt_src, "parts": [], "error": "蓝牙已断开",
                })
            if not is_online:
                detail_txt = "蓝牙设备已断开或离线"
            elif len(bt_parts) > 1:
                parts_txt = " / ".join(f"{p.get('label', '')} {p.get('level', '--')}%" for p in bt_parts)
                detail_txt = f"分部电量：{parts_txt}"
            else:
                detail_txt = f"电量 {bt.battery}%（{bt.reason}）" if bt.reason else f"电量 {bt.battery}%"
            readings.append(
                PeripheralReading(
                    key=key,
                    role=role,
                    name=display_name,
                    product=bt.name,
                    connected=is_online,
                    offline=not is_online,
                    level=bt.battery if is_online else None,
                    pinned=(key in pinned_set),
                    connection=conn_label,
                    source=bt_src,
                    detail=detail_txt,
                    last_read_error="" if is_online else "蓝牙设备已断开或离线",
                    updated_at=now if is_online else prev_known.get("last_seen"),
                    transport=tr_label,
                    parts=bt_parts if is_online else [],
                )
            )
    except Exception:
        pass

    # 自动清理超过 30 天未在线且非置顶的幽灵 known_devices（#19）
    if save_cache and only_keys is None:
        cutoff_30d = now - 30 * 86400.0
        for k in list(known_map.keys()):
            if k in online_keys_now or k in pinned_set:
                continue
            meta = known_map.get(k) or {}
            last_seen_ts = float(meta.get("last_seen") or 0.0)
            if last_seen_ts <= 0.0 or last_seen_ts < cutoff_30d:
                known_map.pop(k, None)
                cfg_dirty = True
                tray_common.log_event("INFO", "devices", f"自动清理 30 天以上未在线历史设备: {k}")

    # 曾识别到电量、当前物理断开/关机的设备：自动置灰标注【已离线】，重连后自动恢复
    if only_keys is None and not background:
        for k, meta in known_map.items():
            if k in seen_keys or (not include_ignored and k in ignored_set) or (not include_hidden and k in hidden_set):
                continue
            if not meta.get("had_battery"):
                continue
            disp = names_map.get(k) or str(meta.get("name") or k)
            raw_role = str(meta.get("role") or "other")
            role = roles_map.get(k) if roles_map.get(k) in ROLE_ORDER else raw_role
            stats.offline_count += 1
            readings.append(
                PeripheralReading(
                    key=k,
                    role=role,
                    name=disp,
                    product=str(meta.get("name") or ""),
                    vid=meta.get("vid"),
                    pid=meta.get("pid"),
                    connected=False,
                    offline=True,
                    level=None,
                    pinned=(k in pinned_set),
                    connection=str(meta.get("connection") or ""),
                    source="离线自动标记",
                    detail="设备已关机、断开连接或超出接收范围",
                    last_read_error="设备已关机或断开连接",
                    updated_at=meta.get("last_seen"),
                    transport=str(meta.get("transport") or "HID"),
                )
            )
    elif only_keys is None and background:
        stats.skipped_offline_enum = True
        # 后台模式下只读内存中已缓存的置顶或已启用离线设备（不额外唤醒/枚举离线外设）
        for k, meta in known_map.items():
            if k in seen_keys or (not include_ignored and k in ignored_set) or (not include_hidden and k in hidden_set):
                continue
            if not meta.get("had_battery"):
                continue
            disp = names_map.get(k) or str(meta.get("name") or k)
            raw_role = str(meta.get("role") or "other")
            role = roles_map.get(k) if roles_map.get(k) in ROLE_ORDER else raw_role
            readings.append(
                PeripheralReading(
                    key=k,
                    role=role,
                    name=disp,
                    product=str(meta.get("name") or ""),
                    vid=meta.get("vid"),
                    pid=meta.get("pid"),
                    connected=False,
                    offline=True,
                    level=None,
                    pinned=(k in pinned_set),
                    connection=str(meta.get("connection") or ""),
                    source="离线自动标记",
                    detail="设备已关机、断开连接或超出接收范围",
                    last_read_error="设备已关机或断开连接",
                    updated_at=meta.get("last_seen"),
                    transport=str(meta.get("transport") or "HID"),
                )
            )

    hist_pre = battery_history.load_history(now=now)
    readings, merge_dirty = _merge_dual_mode_readings(
        readings,
        known_map=known_map,
        names_map=names_map,
        bt_cached_levels=bt_cached_levels,
        hist=hist_pre,
        stats=stats,
        now=now,
    )
    if merge_dirty:
        cfg_dirty = True

    if save_cache:
        hist = battery_history.record_readings(readings, now=now)
        if cfg_dirty:
            cfg["known_devices"] = known_map
            # 合并写回磁盘配置，防止耗时扫描期间用户实时修改的主题/视图/阈值等设置被旧快照覆盖
            disk_cfg = tray_common.load_config()
            disk_cfg["known_devices"] = known_map
            tray_common.save_config(disk_cfg)
    else:
        hist = hist_pre

    try:
        bg_iv = float(cfg.get("bg_interval", 300) or 300)
    except Exception:
        bg_iv = 300.0
    max_gap_sec = min(3600.0, max(battery_history.BT_QUANTIZED_MAX_GAP_SEC, bg_iv * 2.5))
    _populate_history_metrics(readings, hist, now, max_gap_sec=max_gap_sec)
    sort_readings(readings, str(cfg.get("sort_mode") or "default"), pinned_set)

    stats.skipped_count = len(stats.skipped_devices)
    stats.ended_at = time.time()
    stats.duration_ms = round((time.monotonic() - t_mono0) * 1000.0, 1)
    _LAST_SCAN_STATS = stats
    tray_common.log_event("INFO", "devices", f"扫描完成: {stats.summary_text()}")
    return readings, stats


def scan_peripherals(
    cfg: Optional[dict[str, Any]] = None,
    include_hidden: bool = False,
    save_cache: bool = True,
    *,
    background: bool = False,
    include_ignored: bool = False,
) -> list[PeripheralReading]:
    """扫描当前连接的无线/HID/蓝牙外设并读取电量，合并休眠缓存。"""
    readings, _ = scan_peripherals_with_stats(
        cfg=cfg,
        include_hidden=include_hidden,
        save_cache=save_cache,
        background=background,
        include_ignored=include_ignored,
    )
    return readings


def read_single_device(key: str, cfg: Optional[dict[str, Any]] = None) -> Optional[PeripheralReading]:
    """针对单台指定设备执行短间隔重读（用于失败重试队列，避免重扫其他设备）。"""
    effective_cfg = cfg if cfg is not None else tray_common.load_config()
    target_keys = {key}
    meta = (effective_cfg.get("known_devices") or {}).get(key)
    if isinstance(meta, dict) and meta.get("paired_hid_key"):
        target_keys.add(str(meta["paired_hid_key"]))
    readings, _ = scan_peripherals_with_stats(
        cfg=effective_cfg,
        include_hidden=True,
        save_cache=True,
        include_ignored=False,
        only_keys=target_keys,
    )
    for r in readings:
        if r.key == key:
            return r
    return None


def diagnose_device_steps(
    vid: Optional[int],
    pid: Optional[int],
    *,
    reading: Optional[PeripheralReading] = None,
    user_specs: Optional[list[battery.ProtocolSpec]] = None,
) -> list[dict[str, Any]]:
    """
    三步排查“为什么读不到电量”（供主界面排障向导渲染，纯数据逻辑可单测）：
    ① 是否被 Windows HID 枚举到（列出接口）
    ② 是否暴露 0x0006/0x0085 标准电量页或 0xFFxx 厂商通道
    ③ 当前内置 / 自定义协议匹配情况与下一步动作建议
    """
    specs = battery.load_user_protocols() if user_specs is None else user_specs
    matched_ifaces: list[HidInterface] = []
    if vid is not None and pid is not None:
        matched_ifaces = [i for i in enumerate_hid() if i.vid == vid and i.pid == pid]
    elif reading is not None and reading.ifaces:
        matched_ifaces = list(reading.ifaces)

    # 第一步：枚举检查
    if matched_ifaces:
        lines = [
            f"usage_page=0x{i.usage_page:04X} usage=0x{i.usage:04X} "
            f"(in={i.input_len} out={i.output_len} feat={i.feature_len})"
            for i in matched_ifaces[:8]
        ]
        if len(matched_ifaces) > 8:
            lines.append(f"... 共 {len(matched_ifaces)} 个接口")
        step1 = {
            "step": 1,
            "title": "步骤 1：检查设备是否被 Windows 枚举",
            "ok": True,
            "summary": f"已枚举到 {len(matched_ifaces)} 个 HID 接口。",
            "details": lines,
            "action_label": "",
            "action_type": "none",
        }
    elif reading is not None and reading.transport == "BT":
        step1 = {
            "step": 1,
            "title": "步骤 1：检查蓝牙设备枚举状态",
            "ok": True,
            "summary": "该设备通过 Windows 蓝牙 PnP 枚举。",
            "details": [f"来源: {reading.source or 'Windows Bluetooth'}"],
            "action_label": "",
            "action_type": "none",
        }
    else:
        step1 = {
            "step": 1,
            "title": "步骤 1：检查设备是否被 Windows 枚举",
            "ok": False,
            "summary": "当前未枚举到对应 VID:PID 的在线 HID 接口（请确认 2.4G 接收器已插好且外设已开机）。",
            "details": ["若使用其他接收器或换口重插，请先重新扫描或手动添加 VID:PID。"],
            "action_label": "重新扫描设备",
            "action_type": "rescan",
        }

    # 第二步：标准电量页与厂商通道检查
    std_ifaces = [i for i in matched_ifaces if i.usage_page in (0x0006, 0x0085)]
    vendor_ifaces = [i for i in matched_ifaces if i.usage_page >= 0xFF00 and (i.feature_len > 0 or i.output_len > 0 or i.input_len > 0)]
    if std_ifaces:
        step2 = {
            "step": 2,
            "title": "步骤 2：检查 0x0006 / 0x0085 电量页",
            "ok": True,
            "summary": f"检测到 {len(std_ifaces)} 个标准 HID Battery 接口（0x0006/0x0085）。",
            "details": [f"0x{i.usage_page:04X}:0x{i.usage:04X} (in={i.input_len}, val_caps={i.num_input_value_caps})" for i in std_ifaces[:4]],
            "action_label": "",
            "action_type": "none",
        }
    elif vendor_ifaces:
        step2 = {
            "step": 2,
            "title": "步骤 2：检查 0x0006 / 0x0085 电量页",
            "ok": True,
            "summary": f"未暴露标准 0x0006/0x0085，但发现 {len(vendor_ifaces)} 个厂商私有通道 (0xFFxx)，可通过标定抓取电量。",
            "details": [f"0x{i.usage_page:04X}:0x{i.usage:04X} (feat={i.feature_len}, out={i.output_len}, in={i.input_len})" for i in vendor_ifaces[:4]],
            "action_label": "",
            "action_type": "none",
        }
    else:
        step2 = {
            "step": 2,
            "title": "步骤 2：检查 0x0006 / 0x0085 电量页",
            "ok": False,
            "summary": "未发现标准电量页 (0x0006/0x0085) 或可读写厂商通道 (0xFFxx)。",
            "details": ["部分接收器在设备休眠时不响应私有报告，或仅走蓝牙低功耗 GATT。"],
            "action_label": "去调试面板抓包探测",
            "action_type": "open_debug",
        }

    # 第三步：协议匹配结论
    has_builtin = (vid, pid) in battery.KNOWN_DEVICES or (vid in KNOWN_PROTOCOL_VIDS if vid else False)
    has_custom = any((sp.vid == vid and (sp.pid is None or sp.pid == pid)) for sp in specs) if vid is not None else False
    has_live_val = bool(reading and reading.level is not None and not reading.offline)
    err_msg = (reading.last_read_error or reading.error) if reading else ""

    if has_live_val:
        step3 = {
            "step": 3,
            "title": "步骤 3：协议匹配结果与下一步",
            "ok": True,
            "summary": f"协议已成功读取电量 ({reading.level}%)，来源: {reading.source or '内置策略'}。",
            "details": [f"详细帧: {reading.raw_hex}" if reading.raw_hex else "运行正常"],
            "action_label": "",
            "action_type": "none",
        }
    elif has_custom or has_builtin:
        step3 = {
            "step": 3,
            "title": "步骤 3：协议匹配结果与下一步",
            "ok": False,
            "summary": f"已匹配{'自定义' if has_custom else '内置'}协议规则，但本次未返回有效百分比。",
            "details": [err_msg or "请晃动鼠标/按下按键唤醒外设后重试，或点击下方在调试面板查看原始报文。"],
            "action_label": "带 VID:PID 去调试面板校准",
            "action_type": "open_debug",
        }
    else:
        vp_str = f"{vid:04X}:{pid:04X}" if vid is not None and pid is not None else "--:--"
        step3 = {
            "step": 3,
            "title": "步骤 3：协议匹配结果与下一步",
            "ok": False,
            "summary": f"当前 {vp_str} 未命中内置协议或 protocols_user.json。",
            "details": [err_msg or "点击下方按钮将自动预填 VID/PID 打开「调试与标定」，可一键抓取 Feature / Output 响应生成自定义协议。"],
            "action_label": f"去标定并预填 ({vp_str})",
            "action_type": "open_debug",
        }

    return [step1, step2, step3]


def classify_device_issue(
    error: str = "",
    detail: str = "",
    *,
    offline: bool = False,
    transport: str = "HID",
    asleep: bool = False,
) -> tuple[str, str]:
    """将底层异常/离线原因归类为清晰的人话，不在主卡片直接暴露英文路径或错误码。"""
    raw = f"{error or ''} {detail or ''}".strip().lower()
    if asleep or "休眠" in raw or "sleep" in raw:
        return "asleep", "设备处于省电休眠状态，唤醒移动后自动恢复读取"
    if any(k in raw for k in ("access is denied", "拒绝访问", "权限", "permission")):
        return "permission", "系统拒绝访问 HID 接口，可尝试以管理员身份运行"
    if any(k in raw for k in ("未匹配", "未知", "protocol", "协议", "未找到兼容")):
        return "unmatched", "暂未匹配到内置电量协议，可前往调试与标定面板适配"
    if any(k in raw for k in ("超时", "timeout", "未回应", "无应答")):
        return "timeout", "设备响应超时或尚未唤醒，稍后会自动重试"
    if any(k in raw for k in ("接收器", "断开", "范围", "offline", "离线")) or offline:
        if transport == "BT":
            return "disconnected", "蓝牙外设当前未连接或超出通信距离"
        return "disconnected", "未检测到在线信号（接收器未就绪或设备已关机）"
    return "unknown", "暂未能读取到实时电量，可在调试工具中查看原始报文"


def battery_color_hex(
    level: Optional[int],
    asleep: bool = False,
    stale: bool = False,
    charging: bool = False,
    offline: bool = False,
    full: bool = False,
    low_threshold: int = 20,
) -> str:
    th = max(5, min(50, int(low_threshold)))
    if offline or stale or asleep or level is None:
        return GRAY_DISABLED
    if charging and not full:
        return BAR_AMBER
    if full or level >= max(40, th):
        return BAR_GREEN
    if level >= th:
        return BAR_YELLOW
    return BAR_RED


def battery_color_rgb(
    level: Optional[int],
    stale: bool = False,
    charging: bool = False,
    offline: bool = False,
    full: bool = False,
    low_threshold: int = 20,
) -> tuple[int, int, int]:
    hx = battery_color_hex(
        level,
        stale=stale,
        charging=charging,
        offline=offline,
        full=full,
        low_threshold=low_threshold,
    ).lstrip("#")
    return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))
