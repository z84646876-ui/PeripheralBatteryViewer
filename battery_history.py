# -*- coding: utf-8 -*-
"""battery_history.py —— 外设电量历史记录、7 天自动清理与续航预估"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import tray_common

HISTORY_FILE = os.path.join(tray_common.get_data_dir(), "battery_history.json")
ALERT_HISTORY_FILE = os.path.join(tray_common.get_data_dir(), "alert_history.json")
RETENTION_SECONDS = 7 * 24 * 3600
ALERT_MAX_EVENTS = 1000
MIN_SAMPLE_GAP_SECONDS = 180
MAX_SAMPLES_PER_DEVICE = 2500
DEFAULT_BT_QUANTIZED_RATE_PER_H = 2.0
BT_QUANTIZED_MAX_GAP_SEC = 1200.0


def get_retention_seconds(retention_days: Optional[int] = None) -> float:
    days = int(retention_days) if retention_days is not None else tray_common.get_retention_days()
    days = max(1, min(90, days))
    return float(days * 24 * 3600)


def get_max_samples_per_device(retention_days: Optional[int] = None) -> int:
    days = int(retention_days) if retention_days is not None else tray_common.get_retention_days()
    days = max(1, min(90, days))
    return max(MAX_SAMPLES_PER_DEVICE, days * 350)


@dataclass
class SmoothInterpolationResult:
    level: int
    raw_level: int
    interpolated: bool = False
    quantized: bool = False
    elapsed_online_h: float = 0.0
    rate_per_h: Optional[float] = None
    rate_source: str = "none"  # "learned" | "default" | "none"


def prune_history(
    data: dict[str, Any],
    now: Optional[float] = None,
    *,
    retention_days: Optional[int] = None,
) -> dict[str, Any]:
    """按配置保留天数清理过期历史点，仅保留有效时间窗口。"""
    ts_now = float(time.time() if now is None else now)
    retention_sec = (
        float(RETENTION_SECONDS)
        if retention_days is None and tray_common.get_retention_days() == 7
        else get_retention_seconds(retention_days)
    )
    if retention_days is not None:
        retention_sec = get_retention_seconds(retention_days)
    max_samples = get_max_samples_per_device(retention_days)
    cutoff = ts_now - retention_sec
    raw_devices = data.get("devices") if isinstance(data, dict) else None
    cleaned: dict[str, list[dict[str, Any]]] = {}
    if isinstance(raw_devices, dict):
        for key, items in raw_devices.items():
            if not isinstance(key, str) or not isinstance(items, list):
                continue
            kept: list[dict[str, Any]] = []
            for pt in items:
                if not isinstance(pt, dict):
                    continue
                t_val = pt.get("t")
                if not isinstance(t_val, (int, float)):
                    continue
                if float(t_val) < cutoff:
                    continue
                lvl = pt.get("lvl")
                if lvl is not None:
                    try:
                        lvl_i = int(lvl)
                    except (TypeError, ValueError):
                        continue
                    if not (0 <= lvl_i <= 100):
                        continue
                    lvl = lvl_i
                item = {
                    "t": round(float(t_val), 1),
                    "lvl": lvl,
                    "chg": bool(pt.get("chg", False)),
                    "off": bool(pt.get("off", False)),
                }
                mv = pt.get("mv")
                if isinstance(mv, (int, float)) and 2500 <= int(mv) <= 4500:
                    item["mv"] = int(mv)
                kept.append(item)
            kept.sort(key=lambda x: x["t"])
            if len(kept) > max_samples:
                kept = kept[-max_samples:]
            if kept:
                cleaned[key] = kept
    return {"version": 1, "devices": cleaned}


import csv as _csv
import shutil as _shutil


def _resolve_history_target(path: Optional[str] = None) -> str:
    if path:
        return path
    try:
        import tray_common
        default_base = os.path.join(tray_common.BASE_DIR, "battery_history.json")
        if HISTORY_FILE == default_base:
            return os.path.join(tray_common.get_data_dir(), "battery_history.json")
    except Exception:
        pass
    return HISTORY_FILE


def _notify_history_io(kind: str, message: str, path: str, recovered: bool = False) -> None:
    try:
        import tray_common
        tray_common.record_io_warning(kind, message, path=path, recovered=recovered)
        tray_common.log_event("WARNING" if recovered else "ERROR", kind, message, path=path)
    except Exception:
        pass


def load_history(
    path: Optional[str] = None,
    now: Optional[float] = None,
    *,
    retention_days: Optional[int] = None,
) -> dict[str, Any]:
    target = _resolve_history_target(path)
    if not os.path.exists(target):
        return {"version": 1, "devices": {}}
    load_err: Optional[Exception] = None
    try:
        with open(target, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict) or not isinstance(raw.get("devices"), dict):
            raise ValueError("历史数据格式异常")
        return prune_history(raw, now=now, retention_days=retention_days)
    except Exception as exc:
        load_err = exc

    bak = f"{target}.bak"
    if os.path.exists(bak):
        try:
            with open(bak, "r", encoding="utf-8") as f:
                raw_bak = json.load(f)
            if isinstance(raw_bak, dict) and isinstance(raw_bak.get("devices"), dict):
                pruned = prune_history(raw_bak, now=now, retention_days=retention_days)
                tmp = f"{target}.recover.tmp.{os.getpid()}"
                try:
                    with open(tmp, "w", encoding="utf-8") as wf:
                        json.dump(pruned, wf, ensure_ascii=False)
                    os.replace(tmp, target)
                except OSError:
                    pass
                _notify_history_io(
                    "history_recovered",
                    f"历史电量数据损坏（{load_err}），已从备份自动恢复",
                    path=target,
                    recovered=True,
                )
                return pruned
        except Exception:
            pass

    _notify_history_io(
        "history_reset",
        f"历史电量数据损坏且无可用备份（{load_err}），已重置为空记录",
        path=target,
        recovered=False,
    )
    return {"version": 1, "devices": {}}


def save_history(
    data: dict[str, Any],
    path: Optional[str] = None,
    now: Optional[float] = None,
    *,
    retention_days: Optional[int] = None,
) -> dict[str, Any]:
    target = _resolve_history_target(path)
    pruned = prune_history(data, now=now, retention_days=retention_days)
    tmp = f"{target}.tmp.{os.getpid()}"
    try:
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        if os.path.exists(target) and os.path.getsize(target) > 0:
            bak_tmp = f"{target}.bak.tmp.{os.getpid()}"
            try:
                _shutil.copy2(target, bak_tmp)
                os.replace(bak_tmp, f"{target}.bak")
            except OSError:
                if os.path.exists(bak_tmp):
                    try:
                        os.remove(bak_tmp)
                    except OSError:
                        pass
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(pruned, f, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, target)
    except OSError as exc:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        _notify_history_io("history_save_failed", f"保存电量历史失败：{exc}", path=target, recovered=False)
    return pruned


def append_sample(
    data: dict[str, Any],
    key: str,
    level: Optional[int],
    charging: bool = False,
    offline: bool = False,
    now: Optional[float] = None,
    voltage_mv: Optional[int] = None,
) -> dict[str, Any]:
    ts_now = round(float(time.time() if now is None else now), 1)
    devices_map = data.setdefault("devices", {})
    series = devices_map.setdefault(key, [])
    pt: dict[str, Any] = {
        "t": ts_now,
        "lvl": None if (offline or level is None) else max(0, min(100, int(level))),
        "chg": bool(charging and not offline),
        "off": bool(offline or level is None),
    }
    if voltage_mv is not None and not offline and 2500 <= int(voltage_mv) <= 4500:
        pt["mv"] = int(voltage_mv)
    if series:
        prev = series[-1]
        same_state = (
            prev.get("lvl") == pt["lvl"]
            and bool(prev.get("chg")) == pt["chg"]
            and bool(prev.get("off")) == pt["off"]
        )
        if same_state and (ts_now - float(prev.get("t", 0.0))) < MIN_SAMPLE_GAP_SECONDS:
            if "mv" in pt and "mv" not in prev:
                prev["mv"] = pt["mv"]
            return data
    series.append(pt)
    max_samples = get_max_samples_per_device()
    if len(series) > max_samples:
        del series[:-max_samples]
    return data


def record_readings(
    readings: list[Any],
    path: Optional[str] = None,
    now: Optional[float] = None,
    *,
    retention_days: Optional[int] = None,
) -> dict[str, Any]:
    ts_now = float(time.time() if now is None else now)
    hist = load_history(path=path, now=ts_now, retention_days=retention_days)
    for r in readings:
        key = getattr(r, "key", "")
        if not key:
            continue
        # 沿用蓝牙缓存电量的 2.4G 合并态不写入真实掉电采样，避免拉平斜率
        if bool(getattr(r, "stale", False)):
            continue
        lvl = getattr(r, "level", None)
        offline = bool(getattr(r, "offline", False)) or (lvl is None)
        charging = bool(getattr(r, "charging", False)) and not bool(getattr(r, "full", False))
        voltage_mv = getattr(r, "voltage_mv", None)
        # 从未读到过有效电量且本轮也没读到时，不写入无意义全空占位
        if offline and key not in hist["devices"]:
            continue
        append_sample(
            hist,
            key,
            lvl,
            charging=charging,
            offline=offline,
            now=ts_now,
            voltage_mv=voltage_mv,
        )
    return save_history(hist, path=path, now=ts_now, retention_days=retention_days)


def clear_device_history(key: str, path: Optional[str] = None) -> bool:
    """清空单台设备的历史采样点。"""
    hist = load_history(path=path)
    devs = hist.get("devices") or {}
    existed = key in devs
    devs.pop(key, None)
    save_history(hist, path=path)
    return existed


def clear_all_history(path: Optional[str] = None) -> bool:
    """清空全部设备历史采样点。"""
    save_history({"version": 1, "devices": {}}, path=path)
    return True


def import_history_json(
    import_path: str,
    path: Optional[str] = None,
    now: Optional[float] = None,
    *,
    retention_days: Optional[int] = None,
) -> dict[str, Any]:
    """从导出 JSON（单设备 samples 或多设备 devices）导入并与现存历史按设备+时间戳去重合并。"""
    ts_now = float(time.time() if now is None else now)
    with open(import_path, "r", encoding="utf-8") as f:
        incoming = json.load(f)
    if not isinstance(incoming, dict):
        raise ValueError("导入文件根节点必须是 JSON 对象")

    inc_devices: dict[str, list[dict[str, Any]]] = {}
    if isinstance(incoming.get("devices"), dict):
        for k, lst in incoming["devices"].items():
            if isinstance(k, str) and isinstance(lst, list):
                inc_devices[k] = lst
    elif isinstance(incoming.get("samples"), list) and isinstance(incoming.get("device_key"), str) and incoming["device_key"]:
        inc_devices[incoming["device_key"]] = incoming["samples"]
    else:
        raise ValueError("导入 JSON 缺少 devices 或 device_key/samples 字段")

    cur = load_history(path=path, now=ts_now, retention_days=retention_days)
    devs = cur.setdefault("devices", {})
    added = 0
    for k, items in inc_devices.items():
        bucket_map: dict[int, dict[str, Any]] = {}
        for pt in devs.get(k, []):
            if isinstance(pt, dict) and isinstance(pt.get("t"), (int, float)):
                bucket_map[int(round(float(pt["t"])))] = dict(pt)
        before_n = len(bucket_map)
        for pt in items:
            if not isinstance(pt, dict) or not isinstance(pt.get("t"), (int, float)):
                continue
            bkey = int(round(float(pt["t"])))
            if bkey not in bucket_map:
                bucket_map[bkey] = dict(pt)
        added += max(0, len(bucket_map) - before_n)
        devs[k] = sorted(bucket_map.values(), key=lambda x: float(x.get("t", 0.0)))

    saved = save_history(cur, path=path, now=ts_now, retention_days=retention_days)
    return {"added": added, "device_count": len(saved.get("devices", {}))}


def get_series(
    data: dict[str, Any],
    key: str,
    window_hours: float = 24.0,
    now: Optional[float] = None,
) -> list[dict[str, Any]]:
    ts_now = float(time.time() if now is None else now)
    cutoff = ts_now - float(window_hours) * 3600.0
    items = (data.get("devices") or {}).get(key) or []
    return [dict(pt) for pt in items if float(pt.get("t", 0.0)) >= cutoff]


def _is_charging_sample(pt: dict[str, Any]) -> bool:
    return bool(pt.get("chg") or pt.get("charging") or pt.get("charge"))


def summarize_series_stats(samples: list[dict[str, Any]], now: Optional[float] = None) -> dict[str, Any]:
    """Compute summary statistics (min/max/avg/latest, counts, voltage, discharge rate) for a series."""
    ordered = sorted(samples or [], key=lambda p: float(p.get("t", 0.0)))
    online_pts = [
        pt for pt in ordered
        if not pt.get("off") and isinstance(pt.get("lvl"), (int, float))
    ]
    levels = [max(0, min(100, int(round(float(pt["lvl"]))))) for pt in online_pts]
    mvs = [
        int(round(float(pt["mv"])))
        for pt in online_pts
        if isinstance(pt.get("mv"), (int, float)) and 2000 <= float(pt["mv"]) <= 5000
    ]
    offline_count = sum(1 for pt in ordered if pt.get("off"))
    charging_count = sum(1 for pt in ordered if not pt.get("off") and _is_charging_sample(pt))

    latest_lvl = levels[-1] if levels else None
    latest_charging = bool(ordered and not ordered[-1].get("off") and _is_charging_sample(ordered[-1]))
    latest_offline = bool(ordered and ordered[-1].get("off"))
    est_hours: Optional[float] = None
    drain_rate: Optional[float] = None
    empty_ts: Optional[float] = None
    if ordered and latest_lvl is not None:
        try:
            est_hours, drain_rate, empty_ts = estimate_discharge_details(
                ordered,
                current_level=latest_lvl,
                charging=latest_charging,
                offline=latest_offline,
                full=(latest_lvl >= 100 and not latest_charging),
                now=now,
            )
        except Exception:
            est_hours, drain_rate, empty_ts = None, None, None

    return {
        "total_count": len(ordered),
        "online_count": len(online_pts),
        "offline_count": offline_count,
        "charging_count": charging_count,
        "min_level": min(levels) if levels else None,
        "max_level": max(levels) if levels else None,
        "avg_level": round(sum(levels) / len(levels), 1) if levels else None,
        "latest_level": latest_lvl,
        "min_mv": min(mvs) if mvs else None,
        "max_mv": max(mvs) if mvs else None,
        "latest_mv": mvs[-1] if mvs else None,
        "first_ts": float(ordered[0].get("t", 0.0)) if ordered else None,
        "last_ts": float(ordered[-1].get("t", 0.0)) if ordered else None,
        "est_hours": est_hours,
        "drain_rate_pct_per_h": drain_rate,
        "est_empty_ts": empty_ts,
    }


def _collect_export_rows(
    samples_or_data: Any,
    key: str = "",
    device_name: str = "",
    window_hours: Optional[float] = None,
    now: Optional[float] = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    rows: list[tuple[str, str, dict[str, Any]]] = []
    if isinstance(samples_or_data, dict):
        devs = samples_or_data.get("devices") or {}
        target_keys = [key] if key else sorted(devs.keys())
        win = float(window_hours) if window_hours is not None else 7.0 * 24.0
        for dev_key in target_keys:
            pts = get_series(samples_or_data, dev_key, window_hours=win, now=now)
            for pt in pts:
                rows.append((str(dev_key), str(device_name or dev_key), dict(pt)))
    elif isinstance(samples_or_data, list):
        ordered = sorted(samples_or_data, key=lambda p: float(p.get("t", 0.0)))
        for pt in ordered:
            rows.append((str(key), str(device_name or key), dict(pt)))
    return rows


def export_history_csv(
    samples_or_data: Any,
    output_path: str,
    key: str = "",
    device_name: str = "",
    window_hours: Optional[float] = None,
    now: Optional[float] = None,
) -> int:
    """Export device battery history to a UTF-8 BOM CSV file; returns exported sample count."""
    rows = _collect_export_rows(samples_or_data, key=key, device_name=device_name, window_hours=window_hours, now=now)
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{output_path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = _csv.writer(f)
            writer.writerow(["device_key", "device_name", "timestamp", "local_time", "level_pct", "charging", "offline", "voltage_mv"])
            for dev_key, dev_name, pt in rows:
                ts = float(pt.get("t", 0.0))
                try:
                    t_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                except Exception:
                    t_str = ""
                off = bool(pt.get("off"))
                lvl = "" if off or pt.get("lvl") is None else str(int(round(float(pt["lvl"]))))
                chg = "1" if (not off and _is_charging_sample(pt)) else "0"
                mv = "" if pt.get("mv") is None else str(int(round(float(pt["mv"]))))
                writer.writerow([dev_key, dev_name, f"{ts:.3f}", t_str, lvl, chg, "1" if off else "0", mv])
        os.replace(tmp, output_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return len(rows)


def export_history_json(
    samples_or_data: Any,
    output_path: str,
    key: str = "",
    device_name: str = "",
    window_hours: Optional[float] = None,
    now: Optional[float] = None,
) -> int:
    """Export device battery history and summary statistics to JSON; returns exported sample count."""
    rows = _collect_export_rows(samples_or_data, key=key, device_name=device_name, window_hours=window_hours, now=now)
    samples = [pt for _, _, pt in rows]
    payload: dict[str, Any] = {
        "version": 1,
        "exported_at": time.time(),
        "device_key": key,
        "device_name": device_name,
        "window_hours": window_hours,
        "stats": summarize_series_stats(samples, now=now),
    }
    if key or isinstance(samples_or_data, list):
        payload["samples"] = samples
    else:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for dev_key, _, pt in rows:
            grouped.setdefault(dev_key, []).append(pt)
        payload["devices"] = grouped

    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{output_path}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, output_path)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise
    return len(rows)


def filter_voltage_outliers(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """借助电压读数（2500-4500mV）剔除明显的单点毛刺异常值，保护斜率估算。"""
    if len(samples) < 3:
        return list(samples)
    ordered = sorted(samples, key=lambda p: float(p.get("t", 0.0)))
    mv_indices = [
        idx
        for idx, pt in enumerate(ordered)
        if not pt.get("off")
        and isinstance(pt.get("mv"), (int, float))
        and 2500 <= int(pt["mv"]) <= 4500
    ]
    if len(mv_indices) < 3:
        return ordered

    drop_indices: set[int] = set()
    for pos in range(1, len(mv_indices) - 1):
        i_prev = mv_indices[pos - 1]
        i_cur = mv_indices[pos]
        i_next = mv_indices[pos + 1]
        t_prev = float(ordered[i_prev].get("t", 0.0))
        t_cur = float(ordered[i_cur].get("t", 0.0))
        t_next = float(ordered[i_next].get("t", 0.0))
        mv_prev = float(ordered[i_prev]["mv"])
        mv_cur = float(ordered[i_cur]["mv"])
        mv_next = float(ordered[i_next]["mv"])
        ratio = (t_cur - t_prev) / max(1e-6, t_next - t_prev)
        interp = mv_prev + (mv_next - mv_prev) * ratio
        if abs(mv_cur - interp) > 250.0 and abs(mv_cur - mv_prev) > 250.0 and abs(mv_cur - mv_next) > 250.0:
            drop_indices.add(i_cur)

    # 尾点若在 5 分钟内突发 >350mV 电压跳变且电量同步剧烈跳变（>=15%），亦视作毛刺剔除
    if len(mv_indices) >= 3:
        i_p2, i_p1, i_last = mv_indices[-3], mv_indices[-2], mv_indices[-1]
        if i_p1 not in drop_indices and i_p2 not in drop_indices:
            dt_tail = float(ordered[i_last].get("t", 0.0)) - float(ordered[i_p1].get("t", 0.0))
            dmv_tail = abs(float(ordered[i_last]["mv"]) - float(ordered[i_p1]["mv"]))
            dmv_prev = abs(float(ordered[i_p1]["mv"]) - float(ordered[i_p2]["mv"]))
            lvl_last = ordered[i_last].get("lvl")
            lvl_p1 = ordered[i_p1].get("lvl")
            if (
                dt_tail <= 300.0
                and dmv_tail > 350.0
                and dmv_prev <= 120.0
                and isinstance(lvl_last, (int, float))
                and isinstance(lvl_p1, (int, float))
                and abs(float(lvl_last) - float(lvl_p1)) >= 15.0
            ):
                drop_indices.add(i_last)

    if not drop_indices:
        return ordered
    return [pt for idx, pt in enumerate(ordered) if idx not in drop_indices]


def _fit_segment_slope(seg: list[tuple[float, float]]) -> float:
    """对 (timestamp_sec, level_pct) 片段做线性回归，返回每小时斜率（%/h）。"""
    t0 = seg[0][0]
    dt_hours = (seg[-1][0] - t0) / 3600.0
    xs = [(t - t0) / 3600.0 for t, _ in seg]
    ys = [lvl for _, lvl in seg]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom > 1e-9:
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return (seg[-1][1] - seg[0][1]) / max(1e-6, dt_hours)


def estimate_discharge_details(
    samples: list[dict[str, Any]],
    current_level: Optional[int],
    charging: bool = False,
    offline: bool = False,
    full: bool = False,
    now: Optional[float] = None,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    计算当前放电片段详细信息：
    返回 (剩余小时数, 耗电速率 %/h, 预计耗尽时间戳 epoch_sec)；数据不足返回 (None, None, None)。
    """
    if offline or charging or full or current_level is None:
        return None, None, None
    ts_now = float(time.time() if now is None else now)
    if current_level <= 0:
        return 0.0, None, ts_now

    cleaned = filter_voltage_outliers(samples)
    valid: list[tuple[float, float, bool]] = []
    for pt in cleaned:
        if pt.get("off") or pt.get("lvl") is None:
            continue
        valid.append((float(pt["t"]), float(pt["lvl"]), bool(pt.get("chg", False))))

    if not valid:
        return None, None, None

    valid.sort(key=lambda item: item[0])
    # 从最新点向前截取连续“未充电且未发生充回升”的最近一段放电历史
    seg: list[tuple[float, float]] = []
    last_t, last_lvl = ts_now, float(current_level)
    if ts_now - valid[-1][0] >= 1.0 or abs(last_lvl - valid[-1][1]) > 0.01:
        seg.append((last_t, last_lvl))

    prev_lvl = last_lvl
    for t_val, lvl_val, chg_val in reversed(valid):
        if chg_val:
            break
        # 电量明显跳升 (>= 3%) 视作期间曾充电或更换电池，停止回溯
        if prev_lvl - lvl_val >= 3.0:
            break
        if seg and abs(seg[-1][0] - t_val) < 1e-3:
            prev_lvl = lvl_val
            continue
        seg.append((t_val, lvl_val))
        prev_lvl = lvl_val

    seg.reverse()
    if len(seg) < 2:
        return None, None, None

    dt_hours = (seg[-1][0] - seg[0][0]) / 3600.0
    net_drop = seg[0][1] - seg[-1][1]
    if dt_hours < 0.25 or net_drop < 2.0:
        return None, None, None

    slope = _fit_segment_slope(seg)
    rate_per_hour = -slope
    if rate_per_hour < 0.1:
        fallback_rate = net_drop / dt_hours
        if fallback_rate < 0.1:
            return None, None, None
        rate_per_hour = fallback_rate

    rate_per_hour = min(50.0, max(0.1, rate_per_hour))
    est_hours = round(max(0.2, min(500.0, float(current_level) / rate_per_hour)), 1)
    rate_rounded = round(rate_per_hour, 1)
    deplete_ts = ts_now + est_hours * 3600.0
    return est_hours, rate_rounded, deplete_ts


def estimate_remaining_hours(
    samples: list[dict[str, Any]],
    current_level: Optional[int],
    charging: bool = False,
    offline: bool = False,
    full: bool = False,
    now: Optional[float] = None,
) -> Optional[float]:
    """根据近期连续非充电放电斜率估算剩余续航小时数；数据不足时返回 None。"""
    est, _, _ = estimate_discharge_details(
        samples,
        current_level,
        charging=charging,
        offline=offline,
        full=full,
        now=now,
    )
    return est


def estimate_charge_details(
    samples: list[dict[str, Any]],
    current_level: Optional[int],
    charging: bool = False,
    offline: bool = False,
    full: bool = False,
    now: Optional[float] = None,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """
    根据近期连续充电历史斜率估算充满所需分钟数：
    返回 (预计充满分钟数, 充电速率 %/h, 预计充满时间戳)；样本不足返回 (None, None, None)。
    """
    if offline or not charging or full or current_level is None:
        return None, None, None
    ts_now = float(time.time() if now is None else now)
    if current_level >= 100:
        return 0.0, None, ts_now

    cleaned = filter_voltage_outliers(samples)
    valid: list[tuple[float, float, bool]] = []
    for pt in cleaned:
        if pt.get("off") or pt.get("lvl") is None:
            continue
        valid.append((float(pt["t"]), float(pt["lvl"]), bool(pt.get("chg", False))))

    if not valid:
        return None, None, None

    valid.sort(key=lambda item: item[0])
    seg: list[tuple[float, float]] = []
    last_t, last_lvl = ts_now, float(current_level)
    if ts_now - valid[-1][0] >= 1.0 or abs(last_lvl - valid[-1][1]) > 0.01:
        seg.append((last_t, last_lvl))

    prev_lvl = last_lvl
    for t_val, lvl_val, chg_val in reversed(valid):
        if not chg_val:
            break
        # 电量大幅跌落（说明跨越了拔掉充电器放电的旧区间）停止回溯
        if lvl_val - prev_lvl >= 3.0:
            break
        if seg and abs(seg[-1][0] - t_val) < 1e-3:
            prev_lvl = lvl_val
            continue
        seg.append((t_val, lvl_val))
        prev_lvl = lvl_val

    seg.reverse()
    if len(seg) < 2:
        return None, None, None

    dt_hours = (seg[-1][0] - seg[0][0]) / 3600.0
    net_rise = seg[-1][1] - seg[0][1]
    if dt_hours < 0.08 or net_rise < 2.0:
        return None, None, None

    slope = _fit_segment_slope(seg)
    rate_per_hour = slope if slope >= 1.0 else (net_rise / dt_hours)
    if rate_per_hour < 1.0:
        return None, None, None
    rate_per_hour = min(200.0, max(1.0, rate_per_hour))
    mins = ((100.0 - float(current_level)) / rate_per_hour) * 60.0
    mins_clamped = round(max(1.0, min(600.0, mins)), 1)
    return mins_clamped, round(rate_per_hour, 1), ts_now + mins_clamped * 60.0


def estimate_charge_minutes(
    samples: list[dict[str, Any]],
    current_level: Optional[int],
    charging: bool = False,
    offline: bool = False,
    full: bool = False,
    now: Optional[float] = None,
) -> Optional[float]:
    """根据近期充电斜率估算距离 100% 还需分钟数；数据不足返回 None。"""
    mins, _, _ = estimate_charge_details(
        samples,
        current_level,
        charging=charging,
        offline=offline,
        full=full,
        now=now,
    )
    return mins


def format_charge_eta_text(charge_mins: Optional[float]) -> str:
    if charge_mins is None:
        return "正在充电中 · 估算中"
    if charge_mins <= 1.0:
        return "预计很快充满"
    total = max(1, round(charge_mins))
    if total < 60:
        return f"预计 {total}m 后充满"
    h = total // 60
    m = total % 60
    if m == 0:
        return f"预计 {h}h 后充满"
    return f"预计 {h}h{m:02d}m 后充满"


def format_remaining_text(
    est_hours: Optional[float],
    offline: bool = False,
    charging: bool = False,
    full: bool = False,
    *,
    discharge_rate_per_h: Optional[float] = None,
    deplete_ts: Optional[float] = None,
    charge_mins: Optional[float] = None,
    show_charge_eta: bool = False,
) -> str:
    if offline:
        return "设备已离线"
    if full:
        return "已充满"
    if charging:
        if show_charge_eta or charge_mins is not None:
            return format_charge_eta_text(charge_mins)
        return "正在充电中"
    if est_hours is None:
        return "暂无预估数据"
    if est_hours < 1.0:
        mins = max(1, round(est_hours * 60))
        base = f"预估剩余 {mins} 分钟"
    elif est_hours < 10.0:
        base = f"预估剩余 {est_hours:.1f}h"
    else:
        base = f"预估剩余 {round(est_hours)}h"

    extras: list[str] = []
    if discharge_rate_per_h is not None and discharge_rate_per_h > 0:
        rate_s = f"{discharge_rate_per_h:.1f}%/h" if discharge_rate_per_h < 10.0 else f"{round(discharge_rate_per_h)}%/h"
        extras.append(rate_s)
    if deplete_ts is not None:
        try:
            hhmm = datetime.fromtimestamp(float(deplete_ts)).strftime("%H:%M")
            extras.append(f"预计 {hhmm} 耗尽")
        except Exception:
            pass
    if extras:
        return f"{base}（{' · '.join(extras)}）"
    return base


def _learn_quantized_step_rate(
    ordered: list[dict[str, Any]],
    *,
    ts_now: float,
    step: int = 10,
    max_gap_sec: float = BT_QUANTIZED_MAX_GAP_SEC,
) -> tuple[Optional[float], str]:
    """从 10% 步进蓝牙历史中按活跃在线时长学习真实耗电速率（%/h），自动剔除离线/休眠与跨模式间隙。"""
    sessions: list[list[tuple[float, int, bool]]] = []
    cur_session: list[tuple[float, int, bool]] = []
    cum_active_s = 0.0
    prev_online_t: Optional[float] = None
    prev_valid_lvl: Optional[int] = None

    for pt in ordered:
        t_val = float(pt.get("t", 0.0))
        if t_val > ts_now + 1.0:
            continue
        if pt.get("chg"):
            if cur_session:
                sessions.append(cur_session)
                cur_session = []
            cum_active_s = 0.0
            prev_online_t = None
            prev_valid_lvl = None
            continue
        if pt.get("off") or pt.get("lvl") is None:
            prev_online_t = None
            continue
        lvl = int(round(float(pt["lvl"])))
        if prev_valid_lvl is not None and lvl > prev_valid_lvl:
            # 电量回升说明期间充过电，开启新的放电会话
            if cur_session:
                sessions.append(cur_session)
                cur_session = []
            cum_active_s = 0.0
            prev_online_t = t_val
            prev_valid_lvl = lvl
            cur_session.append((0.0, lvl, False))
            continue

        is_contiguous = False
        if prev_online_t is not None:
            dt = t_val - prev_online_t
            if 0.0 < dt <= max_gap_sec:
                cum_active_s += dt
                is_contiguous = True
        prev_online_t = t_val
        prev_valid_lvl = lvl
        cur_session.append((cum_active_s, lvl, is_contiguous))

    if cur_session:
        sessions.append(cur_session)

    valid_pairs: list[tuple[float, float]] = []
    single_drops: list[tuple[float, float]] = []
    for sess in sessions:
        if len(sess) < 2:
            continue
        # 记录每一次硬件向下掉档 (累计活跃秒数, 新电量, 是否为连续在线期间的精确跳变边界)
        transitions: list[tuple[float, int, bool]] = []
        p_lvl = sess[0][1]
        for a_s, lvl, is_contig in sess[1:]:
            if lvl < p_lvl:
                transitions.append((a_s, lvl, is_contig))
            p_lvl = lvl

        exact_transitions = [tr for tr in transitions if tr[2]]
        if len(exact_transitions) >= 2:
            for idx in range(len(exact_transitions) - 1):
                a0, l0, _ = exact_transitions[idx]
                a1, l1, _ = exact_transitions[idx + 1]
                drop = float(l0 - l1)
                dt_h = (a1 - a0) / 3600.0
                if drop >= float(step) and dt_h >= 0.35:
                    pair_rate = drop / dt_h
                    if 0.3 <= pair_rate <= 18.0:
                        valid_pairs.append((drop, dt_h))
        elif len(exact_transitions) == 1:
            a_drop, l_drop, _ = exact_transitions[0]
            drop = float(sess[0][1] - l_drop)
            dt_h = a_drop / 3600.0
            # 只有首次掉档前已累计活跃观测 >= 1.5h 才参与学习，防止半途接入造成速率虚高
            if drop >= float(step) and dt_h >= 1.5:
                est_rate = drop / dt_h
                if 0.3 <= est_rate <= 12.0:
                    single_drops.append((drop, dt_h))

    if valid_pairs:
        recent = valid_pairs[-6:]
        tot_drop = sum(d for d, _ in recent)
        tot_h = sum(h for _, h in recent)
        if tot_h > 0:
            rate = min(15.0, max(0.4, tot_drop / tot_h))
            return round(rate, 2), "learned"

    if single_drops:
        recent = single_drops[-4:]
        tot_drop = sum(d for d, _ in recent)
        tot_h = sum(h for _, h in recent)
        if tot_h > 0:
            rate = min(10.0, max(0.4, tot_drop / tot_h))
            return round(rate, 2), "learned"

    return None, "none"


def interpolate_quantized_bt_level(
    samples: list[dict[str, Any]],
    raw_level: Optional[int],
    *,
    now: Optional[float] = None,
    step: int = 10,
    default_rate_per_h: float = DEFAULT_BT_QUANTIZED_RATE_PER_H,
    max_gap_sec: float = BT_QUANTIZED_MAX_GAP_SEC,
) -> SmoothInterpolationResult:
    """
    针对 10% 步进的经典蓝牙 HFP 设备做软件 1% 平滑插值推算：
      - 若历史中出现过任意非 10% 整数倍真值（如 87%、85%），说明设备本身具备更细精度，直接返回原值不插值；
      - 仅累计当前 raw_level 平台期内的活跃在线时长（自动剔除关机/断连/休眠间隙）；
      - 优先使用从真实 10% 掉档历史中学习到的活跃耗电速率，尚未掉档前采用保守默认值；
      - 推算结果严格钳位在 [max(1, raw_level - (step - 1)), raw_level]，硬件真正掉档后自动归零校准。
    """
    if raw_level is None:
        return SmoothInterpolationResult(level=0, raw_level=0, interpolated=False, quantized=False)
    raw = int(raw_level)
    if step <= 1 or raw < step or raw > 100 or (raw % step != 0):
        return SmoothInterpolationResult(level=raw, raw_level=raw, interpolated=False, quantized=False)

    ts_now = float(time.time() if now is None else now)
    ordered = sorted(
        (pt for pt in (samples or []) if isinstance(pt, dict) and isinstance(pt.get("t"), (int, float))),
        key=lambda p: float(p["t"]),
    )

    for pt in ordered:
        if float(pt["t"]) > ts_now + 1.0:
            continue
        if pt.get("off") or pt.get("lvl") is None:
            continue
        lvl_int = int(round(float(pt["lvl"])))
        if 0 <= lvl_int <= 100 and (lvl_int % step != 0):
            return SmoothInterpolationResult(level=raw, raw_level=raw, interpolated=False, quantized=False)

    plateau_events_rev: list[Optional[float]] = []
    for pt in reversed(ordered):
        t_val = float(pt["t"])
        if t_val > ts_now + 1.0:
            continue
        if pt.get("chg"):
            break
        if pt.get("off") or pt.get("lvl") is None:
            plateau_events_rev.append(None)
            continue
        lvl_int = int(round(float(pt["lvl"])))
        if lvl_int != raw:
            break
        plateau_events_rev.append(t_val)

    plateau_events = list(reversed(plateau_events_rev))
    plateau_events.append(ts_now)

    active_sec = 0.0
    prev_t: Optional[float] = None
    for ev in plateau_events:
        if ev is None:
            prev_t = None
            continue
        if prev_t is not None:
            dt = ev - prev_t
            if 0.0 < dt <= max_gap_sec:
                active_sec += dt
        prev_t = ev

    elapsed_h_exact = active_sec / 3600.0
    elapsed_online_h = round(elapsed_h_exact, 3)

    learned_rate, rate_src = _learn_quantized_step_rate(
        ordered, ts_now=ts_now, step=step, max_gap_sec=max_gap_sec
    )
    if learned_rate is not None:
        used_rate = learned_rate
        used_src = rate_src
    else:
        used_rate = max(0.2, float(default_rate_per_h))
        used_src = "default"

    est_drop = int(math.floor(elapsed_h_exact * used_rate + 1e-9))
    est_drop = max(0, min(step - 1, est_drop))
    floor_lvl = max(1, raw - (step - 1))
    disp_lvl = max(floor_lvl, raw - est_drop)
    return SmoothInterpolationResult(
        level=disp_lvl,
        raw_level=raw,
        interpolated=(disp_lvl != raw),
        quantized=True,
        elapsed_online_h=elapsed_online_h,
        rate_per_h=round(used_rate, 1),
        rate_source=used_src,
    )


# ==============================================================================
# 告警历史记录与统计报表 (#5)
# ==============================================================================

def _resolve_alert_target(path: Optional[str] = None) -> str:
    if path:
        return path
    return ALERT_HISTORY_FILE or os.path.join(tray_common.get_data_dir(), "alert_history.json")


def _prune_alert_events(events: list[dict[str, Any]], now: Optional[float] = None) -> list[dict[str, Any]]:
    ts_now = float(time.time() if now is None else now)
    days = max(30, tray_common.get_retention_days())
    cutoff = ts_now - float(days * 24 * 3600)
    kept: list[dict[str, Any]] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        t_val = ev.get("t")
        if not isinstance(t_val, (int, float)) or float(t_val) < cutoff:
            continue
        kept.append(dict(ev))
    kept.sort(key=lambda e: float(e.get("t", 0.0)))
    if len(kept) > ALERT_MAX_EVENTS:
        kept = kept[-ALERT_MAX_EVENTS:]
    return kept


def load_alert_history(path: Optional[str] = None, now: Optional[float] = None) -> dict[str, Any]:
    target = _resolve_alert_target(path)
    if not os.path.exists(target):
        return {"version": 1, "events": []}
    try:
        with open(target, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and isinstance(raw.get("events"), list):
            return {"version": 1, "events": _prune_alert_events(raw["events"], now=now)}
    except Exception:
        pass
    return {"version": 1, "events": []}


def save_alert_history(data: dict[str, Any], path: Optional[str] = None, now: Optional[float] = None) -> dict[str, Any]:
    target = _resolve_alert_target(path)
    cleaned = {"version": 1, "events": _prune_alert_events(list((data or {}).get("events") or []), now=now)}
    tray_common._atomic_write_json(target, cleaned, backup=False)
    return cleaned


def record_alert_event(
    *,
    key: str,
    name: str,
    kind: str,
    tier: str,
    level: Optional[int],
    threshold: int = 20,
    now: Optional[float] = None,
    path: Optional[str] = None,
) -> dict[str, Any]:
    """追加一条告警（low / full）并持久化至 alert_history.json。"""
    ts_now = float(time.time() if now is None else now)
    doc = load_alert_history(path=path, now=ts_now)
    events: list[dict[str, Any]] = doc["events"]
    # 同一设备短时间内同级别同电量不重复写入完全相同记录
    for prev in reversed(events):
        if prev.get("key") == key and prev.get("kind") == kind and prev.get("tier") == tier:
            if abs(ts_now - float(prev.get("t", 0.0))) < 50.0 and prev.get("level") == level:
                return doc
            break
    events.append({
        "id": f"{int(ts_now * 1000)}_{len(events) + 1}",
        "t": round(ts_now, 1),
        "key": str(key),
        "name": str(name),
        "kind": str(kind),  # "low" | "full"
        "tier": str(tier),  # "normal" | "urgent" | "critical" | "full"
        "level": None if level is None else int(level),
        "threshold": int(threshold),
        "resolved_at": None,
        "duration_min": None,
    })
    return save_alert_history(doc, path=path, now=ts_now)


def resolve_open_low_alerts(
    readings: list[Any],
    now: Optional[float] = None,
    path: Optional[str] = None,
    *,
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """当设备开始充电、充满或电量回升至阈值以上时，自动结束其未闭合的低电告警并计算持续时长。"""
    ts_now = float(time.time() if now is None else now)
    target = _resolve_alert_target(path)
    if not os.path.exists(target):
        return {"version": 1, "events": []}
    doc = load_alert_history(path=target, now=ts_now)
    events = doc["events"]
    if not any(e.get("kind") == "low" and e.get("resolved_at") is None for e in events):
        return doc

    by_key = {getattr(r, "key", ""): r for r in (readings or []) if getattr(r, "key", "")}
    changed = False
    for ev in events:
        if ev.get("kind") != "low" or ev.get("resolved_at") is not None:
            continue
        r = by_key.get(str(ev.get("key") or ""))
        if r is None or getattr(r, "offline", False) or getattr(r, "level", None) is None:
            continue
        th = tray_common.get_device_threshold(cfg, str(ev.get("key") or "")) if cfg is not None else int(ev.get("threshold") or 20)
        lvl = int(r.level)
        if bool(getattr(r, "charging", False)) or bool(getattr(r, "full", False)) or lvl >= th + 2:
            ev["resolved_at"] = round(ts_now, 1)
            ev["duration_min"] = round(max(0.0, (ts_now - float(ev.get("t", ts_now))) / 60.0), 1)
            changed = True
    if changed:
        return save_alert_history(doc, path=target, now=ts_now)
    return doc


def summarize_alert_stats(
    events_or_doc: Any,
    *,
    window_days: Optional[float] = None,
    month_to_date: bool = False,
    key: str = "",
    now: Optional[float] = None,
) -> dict[str, Any]:
    """按 7 天 / 30 天 / 本月汇总低电与充满告警次数、累计低电时长及按设备排行。"""
    ts_now = float(time.time() if now is None else now)
    events = list(events_or_doc.get("events", []) if isinstance(events_or_doc, dict) else (events_or_doc or []))
    if month_to_date:
        dt = datetime.fromtimestamp(ts_now)
        cutoff = datetime(dt.year, dt.month, 1).timestamp()
    elif window_days is not None:
        cutoff = ts_now - float(window_days) * 86400.0
    else:
        cutoff = 0.0

    filtered = [
        ev for ev in events
        if isinstance(ev, dict)
        and isinstance(ev.get("t"), (int, float))
        and float(ev["t"]) >= cutoff
        and (not key or ev.get("key") == key)
    ]
    filtered.sort(key=lambda e: float(e.get("t", 0.0)), reverse=True)

    per_device: dict[str, dict[str, Any]] = {}
    low_count = 0
    full_count = 0
    total_low_min = 0.0

    for ev in filtered:
        dk = str(ev.get("key") or "")
        dname = str(ev.get("name") or dk)
        rec = per_device.setdefault(dk, {
            "key": dk, "name": dname, "low_count": 0, "full_count": 0,
            "critical_count": 0, "low_duration_min": 0.0, "last_alert_ts": 0.0,
        })
        rec["last_alert_ts"] = max(rec["last_alert_ts"], float(ev.get("t", 0.0)))
        if ev.get("kind") == "full":
            full_count += 1
            rec["full_count"] += 1
        else:
            low_count += 1
            rec["low_count"] += 1
            if ev.get("tier") == "critical":
                rec["critical_count"] += 1
            dur = ev.get("duration_min")
            if dur is None and ev.get("resolved_at") is None:
                # 仅合并未解决事件时的粗估在位时长，封顶 24h
                dur = min(1440.0, max(0.0, (ts_now - float(ev.get("t", ts_now))) / 60.0))
            if isinstance(dur, (int, float)):
                total_low_min += float(dur)
                rec["low_duration_min"] = round(rec["low_duration_min"] + float(dur), 1)

    ranked = sorted(per_device.values(), key=lambda r: (r["low_count"], r["low_duration_min"]), reverse=True)
    return {
        "total_events": len(filtered),
        "low_count": low_count,
        "full_count": full_count,
        "total_low_minutes": round(total_low_min, 1),
        "per_device": ranked,
        "events": filtered,
    }


def export_alert_history_csv(events: list[dict[str, Any]], output_path: str) -> int:
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    ordered = sorted(events or [], key=lambda e: float(e.get("t", 0.0)), reverse=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["timestamp", "local_time", "device_key", "device_name", "kind", "tier", "level_pct", "threshold_pct", "duration_min"])
        for ev in ordered:
            t_val = float(ev.get("t", 0.0))
            local_s = datetime.fromtimestamp(t_val).strftime("%Y-%m-%d %H:%M:%S")
            w.writerow([
                t_val,
                local_s,
                ev.get("key", ""),
                ev.get("name", ""),
                ev.get("kind", ""),
                ev.get("tier", ""),
                "" if ev.get("level") is None else ev.get("level"),
                ev.get("threshold", ""),
                "" if ev.get("duration_min") is None else ev.get("duration_min"),
            ])
    return len(ordered)


# ==============================================================================
# 功耗 (mA/mW/mAh/Wh) 与等效循环寿命估算 (#10)
# ==============================================================================

def estimate_power_and_health(
    samples: list[dict[str, Any]],
    *,
    capacity_mah: int = 500,
    nominal_v: float = 3.7,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """根据标称容量与历史采样推算平均功耗（mA/mW）、累计消耗（mAh/Wh）、等效循环次数与速率趋势。"""
    cap = max(50, int(capacity_mah))
    ts_now = float(time.time() if now is None else now)
    ordered = sorted(
        (pt for pt in (samples or []) if isinstance(pt, dict) and isinstance(pt.get("t"), (int, float))),
        key=lambda p: float(p["t"]),
    )
    online = [pt for pt in ordered if not pt.get("off") and isinstance(pt.get("lvl"), (int, float))]

    mvs = [float(pt["mv"]) / 1000.0 for pt in online if isinstance(pt.get("mv"), (int, float)) and 2500 <= int(pt["mv"]) <= 4500]
    eff_v = round(sum(mvs) / len(mvs), 2) if mvs else float(nominal_v)

    total_discharged_pct = 0.0
    total_charged_pct = 0.0
    seg_rates: list[float] = []
    cur_seg: list[tuple[float, float]] = []

    for idx, pt in enumerate(online):
        t_val = float(pt["t"])
        lvl = float(pt["lvl"])
        chg = _is_charging_sample(pt)
        if idx > 0:
            prev_pt = online[idx - 1]
            prev_lvl = float(prev_pt["lvl"])
            diff = lvl - prev_lvl
            if chg or diff > 0:
                if diff > 0:
                    total_charged_pct += diff
            elif diff < 0:
                total_discharged_pct += abs(diff)

        if not chg and t_val <= ts_now + 1.0:
            if cur_seg and (t_val - cur_seg[-1][0] > 3600.0 or lvl - cur_seg[-1][1] >= 3.0):
                if (cur_seg[-1][0] - cur_seg[0][0]) >= 1800.0 and (cur_seg[0][1] - cur_seg[-1][1]) >= 2.0:
                    dt_h = (cur_seg[-1][0] - cur_seg[0][0]) / 3600.0
                    seg_rates.append((cur_seg[0][1] - cur_seg[-1][1]) / dt_h)
                cur_seg = [(t_val, lvl)]
            else:
                cur_seg.append((t_val, lvl))
        else:
            if cur_seg and (cur_seg[-1][0] - cur_seg[0][0]) >= 1800.0 and (cur_seg[0][1] - cur_seg[-1][1]) >= 2.0:
                dt_h = (cur_seg[-1][0] - cur_seg[0][0]) / 3600.0
                seg_rates.append((cur_seg[0][1] - cur_seg[-1][1]) / dt_h)
            cur_seg = []

    if cur_seg and (cur_seg[-1][0] - cur_seg[0][0]) >= 1800.0 and (cur_seg[0][1] - cur_seg[-1][1]) >= 2.0:
        dt_h = (cur_seg[-1][0] - cur_seg[0][0]) / 3600.0
        seg_rates.append((cur_seg[0][1] - cur_seg[-1][1]) / dt_h)

    st = summarize_series_stats(samples, now=ts_now)
    rate_pct_h = st.get("drain_rate_pct_per_h")
    if rate_pct_h is None and seg_rates:
        recent_rates = seg_rates[-2:]
        rate_pct_h = round(sum(recent_rates) / len(recent_rates), 2)
    est_current_ma = round((float(rate_pct_h) / 100.0) * cap, 1) if rate_pct_h is not None else None
    est_power_mw = round(est_current_ma * eff_v, 1) if est_current_ma is not None else None

    total_discharged_mah = round((total_discharged_pct / 100.0) * cap, 1)
    total_discharged_wh = round((total_discharged_mah / 1000.0) * eff_v, 3)
    eq_cycles = round(total_charged_pct / 100.0, 2)

    rate_trend_pct: Optional[float] = None
    if len(seg_rates) >= 2:
        early = seg_rates[0]
        recent = sum(seg_rates[-2:]) / len(seg_rates[-2:])
        if early > 0.05:
            rate_trend_pct = round(((recent - early) / early) * 100.0, 1)

    return {
        "capacity_mah": cap,
        "nominal_v": eff_v,
        "est_current_ma": est_current_ma,
        "est_power_mw": est_power_mw,
        "total_discharged_mah": total_discharged_mah,
        "total_discharged_wh": total_discharged_wh,
        "total_charged_pct": round(total_charged_pct, 1),
        "equivalent_cycles": eq_cycles,
        "rate_trend_pct": rate_trend_pct,
        "health_note": "注：外设固件仅上报百分比，功耗与等效循环按设定容量及历史充放统计推算",
    }
