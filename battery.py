# -*- coding: utf-8 -*-
"""battery.py —— 2.4G / HID 外设电量读取与协议引擎（零第三方依赖）

支持（含实机协议验证）：
  - 迈从 MCHOSE A5 Ultra / A5 V2 Ultra / K7 等 (3837:* / 5253:*, FF01 65B Feature 0x11/0x06)
  - SinoWealth / MCHOSE Z75 系列 2.4G 键盘 (258A:01BB / 3554:FA09, FF02 20B 0x13 0x05/0x4A)
  - 飞智 Flydigi APEX 6 Pro 等手柄 (37D7:2502 / 2401, FFA0 33B 5A A5 01)
  - ATK / VXE / VGN 2.4G 鼠标 (373B:* / 3554:F5xx, FF02 17B rid=0x08)
  - 罗技 Logitech HID++ 2.0 (046D:*, FF00 0x1000/0x1004)
  - DARKRIM / 杰理 Jieli RCSP 耳机 (3654:6901 / 35BB:*, FF02 CRC16-CCITT)
  - 标准 HID 电量用法 (BATTERY_USAGES: 0x0006/0x0020, 0x0085/*)
  - 用户通过调试工具标定保存的自定义协议 (protocols_user.json)
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

import darkrim_proto
import tray_common
from hid_win import (
    BATTERY_USAGES,
    HidDevice,
    HidError,
    HidInterface,
    HidP_Feature,
    HidP_Input,
    enumerate_hid,
)

USER_PROTO_FILE = os.path.join(tray_common.get_data_dir(), "protocols_user.json")

KNOWN_DEVICES: dict[tuple[int, int], tuple[str, str, str]] = {
    (0x3837, 0x100C): ("MCHOSE A5 Ultra", "mouse", "2.4G"),
    (0x3837, 0x1101): ("MCHOSE 鼠标", "mouse", "USB"),
    (0x3837, 0x1018): ("MCHOSE K7 V2 Ultra+", "mouse", "2.4G"),
    (0x3837, 0x3033): ("MCHOSE G87 V2", "keyboard", "2.4G"),
    (0x258A, 0x01BB): ("MCHOSE 2.4G 无线键盘", "keyboard", "2.4G"),
    (0x3554, 0xFA09): ("2.4G 无线机械键盘", "keyboard", "2.4G"),
    (0x41E4, 0x2001): ("MCHOSE 2.4G 无线键盘", "keyboard", "2.4G"),
    (0x35BB, 0xA209): ("HECATE G2 无线耳机", "headset", "2.4G"),
    (0x35BB, 0xA208): ("HECATE G2 无线耳机", "headset", "2.4G"),
    (0x35BB, 0xA22B): ("HECATE G3 Max 无线耳机", "headset", "2.4G"),
    (0x35BB, 0xA008): ("HECATE G5 无线耳机", "headset", "2.4G"),
    (0x3654, 0x6901): ("DARKRIM 无线耳机", "headset", "2.4G"),
    (0x37D7, 0x2502): ("Flydigi APEX 6 Pro", "controller", "USB/2.4G"),
    (0x37D7, 0x2401): ("Flydigi 手柄", "controller", "USB/2.4G"),
    (0x373B, 0x1216): ("ATK A9 Ultra", "mouse", "2.4G"),
    (0x3554, 0xF510): ("VXE / VGN 2.4G 鼠标", "mouse", "2.4G"),
    (0x3554, 0xF58A): ("VXE / MCHOSE 2.4G 鼠标", "mouse", "2.4G"),
    (0x054C, 0x0CE6): ("Sony DualSense 手柄", "controller", "USB/蓝牙"),
    (0x054C, 0x0DF2): ("Sony DualSense Edge 手柄", "controller", "USB/蓝牙"),
    (0x054C, 0x05C4): ("Sony DualShock 4 手柄", "controller", "USB/蓝牙"),
    (0x054C, 0x09CC): ("Sony DualShock 4 v2 手柄", "controller", "USB/蓝牙"),
    (0x1532, 0x007B): ("Razer Viper Ultimate 无线", "mouse", "2.4G"),
    (0x1532, 0x008A): ("Razer DeathAdder V2 Pro", "mouse", "2.4G"),
    (0x1532, 0x00A6): ("Razer Viper V2 Pro", "mouse", "2.4G"),
    (0x1532, 0x00B7): ("Razer DeathAdder V3 Pro", "mouse", "2.4G"),
    (0x1532, 0x00AA): ("Razer Basilisk V3 Pro", "mouse", "2.4G"),
    (0x1532, 0x00C3): ("Razer Viper V3 Pro", "mouse", "2.4G"),
    (0x1038, 0x12D5): ("SteelSeries Arctis Nova 7", "headset", "2.4G"),
    (0x1038, 0x12E0): ("SteelSeries Arctis Nova Pro", "headset", "2.4G"),
    (0x1038, 0x12AD): ("SteelSeries Arctis 7+", "headset", "2.4G"),
    (0x1038, 0x183A): ("SteelSeries Aerox 3 Wireless", "mouse", "2.4G"),
    (0x1038, 0x1854): ("SteelSeries Aerox 5 Wireless", "mouse", "2.4G"),
    (0x045E, 0x0922): ("Microsoft Surface Pen", "other", "蓝牙"),
    (0x045E, 0x091A): ("Microsoft Surface Precision Mouse", "mouse", "蓝牙"),
    (0x045E, 0x099B): ("Microsoft Surface Keyboard", "keyboard", "蓝牙"),
}

FLYDIGI_TYPES = {
    149: "Flydigi APEX 6",
    150: "Flydigi APEX 6 Pro",
    151: "Flydigi VADER 5",
    152: "Flydigi DIREWOLF 4",
}


@dataclass
class BatteryResult:
    level: Optional[int] = None
    voltage_mv: Optional[int] = None
    charging: Optional[bool] = None
    full: Optional[bool] = None
    asleep: bool = False
    connection: str = ""
    source: str = ""
    raw_hex: str = ""
    detail: str = ""
    error: str = ""
    detected_model: str = ""
    temperature_c: Optional[float] = None


@dataclass
class ProtocolSpec:
    name: str
    vid: Optional[int] = None
    pid: Optional[int] = None
    usage_page: Optional[int] = None
    usage: Optional[int] = None
    method: str = "output_then_input"  # feature / output_then_input / passive_input
    report_id: int = 0
    report_len: int = 64
    command_hex: str = ""
    use_hidd_output: bool = True
    expect_prefix_hex: str = ""
    level_offset: int = 0
    level_max: int = 100
    charging_offset: Optional[int] = None
    charging_mask: int = 0xFF
    voltage_offset: Optional[int] = None
    voltage_endian: str = "big"
    note: str = ""


def _fmt_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def scale_level(raw: int, max_val: int) -> int:
    if max_val <= 0:
        return max(0, min(100, raw))
    return max(0, min(100, round(raw * 100 / max_val)))


def _try_get_feature(dev: HidDevice, rid: int, length: int) -> Optional[bytes]:
    try:
        return dev.get_feature_report(rid, length)
    except HidError:
        return None


def _try_set_feature(dev: HidDevice, data: bytes) -> bool:
    try:
        dev.set_feature_report(data)
        return True
    except HidError:
        return False


def _try_write_output(dev: HidDevice, data: bytes, prefer_hidd: bool = False) -> bool:
    for use_hidd in (prefer_hidd, not prefer_hidd):
        try:
            dev.write_output(data, use_hidd=use_hidd)
            return True
        except HidError:
            continue
    return False


def infer_role(vid: int, pid: int, product: str = "", usage_page: int = 0, usage: int = 0) -> str:
    known = KNOWN_DEVICES.get((vid, pid))
    if known:
        return known[1]
    p = (product or "").lower()
    if any(k in p for k in ("headset", "headphone", "hecate", "ear", "audio", "darkrim", "耳机", "g2", "g3", "g5")):
        return "headset"
    if any(k in p for k in ("keyboard", "kbd", "键盘", "z75", "g75", "g87", "vk87", "key")):
        return "keyboard"
    if any(k in p for k in ("apex", "vader", "direwolf", "gamepad", "controller", "手柄", "flydigi", "xbox", "dualsense", "dualshock")):
        return "controller"
    if any(k in p for k in ("mouse", "鼠标", "a5", "a7", "k7", "g304", "gpw", "viper", "deathadder", "basilisk", "aerox")):
        return "mouse"
    if "surface" in p:
        if "pen" in p or "触控笔" in p:
            return "other"
    if "arctis" in p or "steelseries" in p:
        if any(k in p for k in ("arctis", "nova")):
            return "headset"
    if usage_page == 0x0001 and usage == 0x0002:
        return "mouse"
    if usage_page == 0x0001 and usage == 0x0006:
        return "keyboard"
    if usage_page == 0x0001 and usage in (0x0004, 0x0005):
        return "controller"
    if vid in (0x35BB, 0x3654):
        return "headset"
    return "other"


def friendly_device_name(vid: int, pid: int, product: str = "", manufacturer: str = "") -> str:
    known = KNOWN_DEVICES.get((vid, pid))
    if known:
        return known[0]
    p = (product or "").strip()
    if p and p.lower() not in ("2.4g wireless receiver", "usb receiver", "wireless receiver"):
        return p
    if manufacturer:
        return f"{manufacturer.strip()} ({vid:04X}:{pid:04X})"
    return f"无线外设 ({vid:04X}:{pid:04X})"


# ==============================================================================
# 用户自定义协议加载/保存
# ==============================================================================

def _clean_hex_str(val: Any) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    tokens = s.replace(",", " ").replace("-", " ").split()
    cleaned = "".join(tokens)
    if len(cleaned) % 2 != 0:
        raise ValueError(f"十六进制指令长度必须为偶数: {s!r}")
    bytes.fromhex(cleaned)
    return " ".join(cleaned[i : i + 2].upper() for i in range(0, len(cleaned), 2))


def validate_protocol_dict(raw: Any) -> ProtocolSpec:
    """校验导入的单条协议 JSON 字段并返回标准化 ProtocolSpec；非法字段抛出 ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("协议条目必须是 JSON 对象")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("缺少协议名称 name")
    method = str(raw.get("method") or "output_then_input").strip()
    if method not in ("feature", "output_then_input", "passive_input"):
        raise ValueError(f"不支持的采集方式 method={method!r}")

    def _opt_int(key: str, lo: int = 0, hi: int = 0xFFFF) -> Optional[int]:
        v = raw.get(key)
        if v is None or v == "":
            return None
        iv = int(v, 0) if isinstance(v, str) else int(v)
        if not (lo <= iv <= hi):
            raise ValueError(f"{key}={iv} 超出范围 [{lo}, {hi}]")
        return iv

    vid = _opt_int("vid", 0, 0xFFFF)
    pid = _opt_int("pid", 0, 0xFFFF)
    usage_page = _opt_int("usage_page", 0, 0xFFFF)
    usage = _opt_int("usage", 0, 0xFFFF)
    report_id = int(raw.get("report_id", 0))
    if not (0 <= report_id <= 0xFF):
        raise ValueError(f"report_id={report_id} 无效")
    report_len = int(raw.get("report_len", 64))
    if not (1 <= report_len <= 1024):
        raise ValueError(f"report_len={report_len} 无效")
    level_offset = int(raw.get("level_offset", 0))
    if level_offset < 0 or level_offset >= report_len:
        raise ValueError(f"level_offset={level_offset} 超出 report_len={report_len}")
    level_max = int(raw.get("level_max", 100))
    if level_max <= 0 or level_max > 65535:
        raise ValueError(f"level_max={level_max} 无效")
    chg_off = _opt_int("charging_offset", 0, report_len - 1)
    chg_mask = int(raw.get("charging_mask", 0xFF))
    volt_off = _opt_int("voltage_offset", 0, max(0, report_len - 2))
    volt_end = str(raw.get("voltage_endian") or "big").strip().lower()
    if volt_end not in ("big", "little"):
        raise ValueError(f"voltage_endian={volt_end!r} 必须是 big 或 little")

    return ProtocolSpec(
        name=name,
        vid=vid,
        pid=pid,
        usage_page=usage_page,
        usage=usage,
        method=method,
        report_id=report_id,
        report_len=report_len,
        command_hex=_clean_hex_str(raw.get("command_hex", "")),
        use_hidd_output=bool(raw.get("use_hidd_output", True)),
        expect_prefix_hex=_clean_hex_str(raw.get("expect_prefix_hex", "")),
        level_offset=level_offset,
        level_max=level_max,
        charging_offset=chg_off,
        charging_mask=chg_mask,
        voltage_offset=volt_off,
        voltage_endian=volt_end,
        note=str(raw.get("note") or ""),
    )


def protocol_conflict_key(spec: ProtocolSpec) -> tuple[Optional[int], Optional[int], Optional[int]]:
    return (spec.vid, spec.pid, spec.usage_page)


def load_user_protocols(proto_file: Optional[str] = None) -> list[ProtocolSpec]:
    target = proto_file or USER_PROTO_FILE
    if not os.path.exists(target):
        return []
    try:
        with open(target, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return []
    items = raw.get("protocols") if isinstance(raw, dict) else raw
    out: list[ProtocolSpec] = []
    for item in items if isinstance(items, list) else []:
        try:
            out.append(ProtocolSpec(**item))
        except TypeError:
            pass
    return out


def save_user_protocol(spec: ProtocolSpec, proto_file: Optional[str] = None) -> str:
    target = proto_file or USER_PROTO_FILE
    existing = load_user_protocols(target)
    existing = [
        p for p in existing
        if protocol_conflict_key(p) != protocol_conflict_key(spec)
    ]
    existing.append(spec)
    with open(target, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in existing], f, ensure_ascii=False, indent=2)
    tray_common.log_event(
        "INFO",
        "battery",
        f"保存自定义协议: {spec.name} ({spec.vid or 0:04X}:{spec.pid or 0:04X})",
    )
    return target


def export_user_protocols(
    export_path: str,
    specs: Optional[list[ProtocolSpec]] = None,
    *,
    proto_file: Optional[str] = None,
) -> int:
    """导出全部或指定条自定义协议至 JSON 文件，返回导出条数。"""
    to_export = load_user_protocols(proto_file) if specs is None else list(specs)
    payload = {
        "format": "peripheral-battery-protocols",
        "version": 1,
        "exported_at": int(time.time()),
        "protocols": [asdict(p) for p in to_export],
    }
    os.makedirs(os.path.dirname(export_path) or ".", exist_ok=True)
    with open(export_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tray_common.log_event("INFO", "battery", f"导出 {len(to_export)} 条协议到 {export_path}")
    return len(to_export)


def parse_protocols_file(import_path: str) -> tuple[list[ProtocolSpec], list[str]]:
    """解析并校验待导入的协议 JSON（兼容单对象、数组及导出包装格式）。"""
    with open(import_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        if "protocols" in raw:
            if not isinstance(raw["protocols"], list):
                raise ValueError("protocols 字段必须是列表")
            raw_list = raw["protocols"]
        else:
            raw_list = [raw]
    elif isinstance(raw, list):
        raw_list = raw
    else:
        raise ValueError("不支持的协议文件结构，根元素必须是对象或列表")

    parsed: list[ProtocolSpec] = []
    errors: list[str] = []
    for idx, entry in enumerate(raw_list, start=1):
        try:
            parsed.append(validate_protocol_dict(entry))
        except Exception as e:
            errors.append(f"第 {idx} 条无效: {e}")
    return parsed, errors


def import_user_protocols(
    import_path: str,
    *,
    overwrite: bool = True,
    proto_file: Optional[str] = None,
) -> dict[str, Any]:
    """
    导入外部协议文件并合并进 protocols_user.json。
    返回 {imported: int, skipped: int, conflicts: list[str], errors: list[str]}。
    """
    target = proto_file or USER_PROTO_FILE
    incoming, errors = parse_protocols_file(import_path)
    existing = load_user_protocols(target)
    by_key: dict[tuple[Optional[int], Optional[int], Optional[int]], ProtocolSpec] = {
        protocol_conflict_key(p): p for p in existing
    }

    imported = 0
    skipped = 0
    conflicts: list[str] = []
    for sp in incoming:
        ck = protocol_conflict_key(sp)
        if ck in by_key:
            conflicts.append(f"{sp.name} ({sp.vid or 0:04X}:{sp.pid or 0:04X})")
            if not overwrite:
                skipped += 1
                continue
        by_key[ck] = sp
        imported += 1

    merged = list(by_key.values())
    with open(target, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in merged], f, ensure_ascii=False, indent=2)
    tray_common.log_event(
        "INFO",
        "battery",
        f"导入协议完成: 新增/覆盖 {imported} 条，跳过 {skipped} 条，异常 {len(errors)} 条",
    )
    return {
        "imported": imported,
        "skipped": skipped,
        "conflicts": conflicts,
        "errors": errors,
        "total": len(merged),
    }


# ==============================================================================
# 1. MCHOSE 迈从鼠标 (RealTek 65B Feature 0x11 / 0x06) —— 实机验证 (3837:100C)
# ==============================================================================

# 响应载荷内嵌芯片商 VID（0x0BDA 为 Realtek 芯片内部标识，非 USB 设备 VID；仅在已匹配 0x3837/0x5253 接口后校验）
MCHOSE_PAYLOAD_VIDS = (0x3837, 0x5253, 0x0BDA)
MCHOSE_ACCEPT_VIDS = MCHOSE_PAYLOAD_VIDS


def parse_mchose_feature_payload(resp: bytes) -> Optional[dict]:
    """解析 0x11/0xF9（按位取反）或 0x11/0x06（明文）响应载荷。"""
    if not resp or len(resp) < 13 or resp[0] != 0x11:
        return None
    if resp[1] == (0x06 ^ 0xFF):
        pay = bytes(b ^ 0xFF for b in resp[2:])
    elif resp[1] == 0x06:
        pay = resp[2:]
    else:
        return None

    donor_vid = pay[0] | (pay[1] << 8)
    donor_pid = pay[2] | (pay[3] << 8)
    if donor_vid not in MCHOSE_PAYLOAD_VIDS:
        return None

    if not any(pay[:11]):
        return {"asleep": True, "donor_vid": donor_vid, "donor_pid": donor_pid, "pay": pay}

    flags = pay[8]
    level = pay[9]
    charging = bool(pay[10])
    if flags == 0 and level == 0 and not charging:
        return {"asleep": True, "donor_vid": donor_vid, "donor_pid": donor_pid, "pay": pay}
    if not (0 <= level <= 100):
        return None

    return {
        "asleep": False,
        "level": level,
        "charging": charging,
        "flags": flags,
        "donor_vid": donor_vid,
        "donor_pid": donor_pid,
        "pay": pay,
    }


def strategy_mchose_feature(iface: HidInterface) -> BatteryResult:
    if iface.usage_page not in (0xFF01, 0xFFA0) or iface.feature_len < 13:
        return BatteryResult(error="非 MCHOSE Feature 接口")

    sz = iface.feature_len or 65
    req = bytes([0x11, 0x06 ^ 0xFF]) + bytes([0xFF] * (sz - 2))
    saw_asleep = False
    last_hex = ""

    try:
        with HidDevice(iface.path, write=True) as dev:
            for _ in range(3):
                _try_set_feature(dev, req)
                time.sleep(0.08)
                for rid in (0x12, 0x11):
                    r = _try_get_feature(dev, rid, sz)
                    if not r:
                        continue
                    last_hex = _fmt_hex(r[:24])
                    parsed = parse_mchose_feature_payload(r)
                    if not parsed:
                        continue
                    if parsed["asleep"]:
                        saw_asleep = True
                        continue
                    flags = parsed["flags"]
                    link = "有线 USB" if iface.pid == 0x1101 else ("2.4G" if flags else "无线")
                    return BatteryResult(
                        level=parsed["level"],
                        charging=parsed["charging"],
                        connection=link,
                        source="MCHOSE RealTek Feature 0x11/0x06",
                        raw_hex=last_hex,
                        detail=(
                            f"电量 {parsed['level']}%（flags=0x{flags:02X}, "
                            f"chip={parsed['donor_vid']:04X}:{parsed['donor_pid']:04X}）"
                        ),
                    )
                time.sleep(0.10)
    except OSError as e:
        return BatteryResult(error=f"打开 MCHOSE 设备失败: {e}")

    if saw_asleep:
        return BatteryResult(
            asleep=True,
            raw_hex=last_hex,
            error="鼠标处于休眠状态（负载全 0），请移动或点击鼠标后重试",
        )
    return BatteryResult(raw_hex=last_hex, error="MCHOSE Feature 0x11/0x06 未返回有效电量")


# ==============================================================================
# 2. SinoWealth / MCHOSE 20 字节 2.4G 键盘协议 —— 实机验证 (258A:01BB)
# ==============================================================================

def build_sinowealth_cmd(cmd: int, arg: int = 0x01, length: int = 20) -> bytes:
    buf = bytearray(length)
    buf[0] = 0x13
    buf[1] = cmd & 0xFF
    buf[2] = arg & 0xFF
    buf[-1] = sum(buf[:-1]) & 0xFF
    return bytes(buf)


def parse_sinowealth_battery(resp: bytes) -> Optional[dict]:
    if not resp or len(resp) < 20:
        return None
    if resp[0] != 0x13 or resp[1] != 0x4A or resp[2] != 0x01 or resp[4] < 0x02:
        return None
    if resp[19] != (sum(resp[:19]) & 0xFF):
        return None
    level = resp[5]
    if not (0 <= level <= 100):
        return None
    st = resp[6]
    full = (st & 0x0F) != 0
    return {
        "level": level,
        "charging": bool((st & 0xF0) != 0 and not full),
        "full": full,
        "status_byte": st,
    }


def strategy_sinowealth_20b(iface: HidInterface) -> BatteryResult:
    if iface.usage_page != 0xFF02 or iface.input_len < 20 or iface.output_len < 20:
        return BatteryResult(error="非 20 字节键盘协议接口")

    out_len = iface.output_len or 20
    in_len = iface.input_len or 20
    cmd_model = build_sinowealth_cmd(0x05, 0x01, out_len)
    cmd_bat = build_sinowealth_cmd(0x4A, 0x01, out_len)

    model_hex = ""
    bat_raw: Optional[bytes] = None

    def query_once(dev: HidDevice, cmd: bytes, expected_cmd: int) -> Optional[bytes]:
        dev.drain()
        if not _try_write_output(dev, cmd, prefer_hidd=False):
            return None
        deadline = time.monotonic() + 0.35
        while time.monotonic() < deadline:
            r = dev.read_input(timeout_ms=100, length=in_len)
            if not r:
                continue
            if len(r) >= 20 and r[0] == 0x13 and r[1] == expected_cmd and r[19] == (sum(r[:19]) & 0xFF):
                return r[:20]
        return None

    try:
        with HidDevice(iface.path, write=True) as dev:
            m = query_once(dev, cmd_model, 0x05)
            if m:
                model_hex = _fmt_hex(m[4:11])
            for _ in range(3):
                bat_raw = query_once(dev, cmd_bat, 0x4A)
                if bat_raw:
                    break
                time.sleep(0.06)
    except OSError as e:
        return BatteryResult(error=f"打开键盘 2.4G 接口失败: {e}")

    if not bat_raw:
        return BatteryResult(
            asleep=True,
            error="键盘未回复电量（可能休眠中或电源开关未开，请按任意键唤醒后重试）",
        )

    parsed = parse_sinowealth_battery(bat_raw)
    if not parsed:
        return BatteryResult(
            raw_hex=_fmt_hex(bat_raw),
            error="收到键盘回复但电量帧校验未通过",
        )

    return BatteryResult(
        level=parsed["level"],
        charging=parsed["charging"],
        full=parsed["full"],
        connection="2.4G",
        source="SinoWealth/MCHOSE 20B (0x13 0x4A)",
        raw_hex=_fmt_hex(bat_raw),
        detail=f"电量 {parsed['level']}%, 状态=0x{parsed['status_byte']:02X}"
        + (f"（型号码 {model_hex}）" if model_hex else ""),
    )


# ==============================================================================
# 3. 飞智 Flydigi 手柄协议 (0xFFA0 5A A5 01) —— 实机验证 (37D7:2502)
# ==============================================================================

def build_flydigi_cmd(length: int = 33) -> bytes:
    """构建 Flydigi 状态查询帧：33B 时首字节为 Report ID 0x00。"""
    buf = bytearray(max(32, length))
    if len(buf) >= 33:
        buf[0] = 0x00
        buf[1], buf[2], buf[3], buf[4] = 0x5A, 0xA5, 0x01, 0x02
        buf[32] = sum(buf[3:32]) & 0xFF
    else:
        buf[0], buf[1], buf[2], buf[3] = 0x5A, 0xA5, 0x01, 0x02
        buf[31] = sum(buf[2:31]) & 0xFF
    return bytes(buf)


def parse_flydigi_report(resp: bytes) -> Optional[dict]:
    if not resp or len(resp) < 14:
        return None
    magic = -1
    for idx in (0, 1):
        if len(resp) >= idx + 12 and resp[idx] == 0x5A and resp[idx + 1] == 0xA5 and resp[idx + 2] == 0x01:
            magic = idx
            break
    if magic < 0:
        return None
    pay = magic + 5
    if pay + 6 >= len(resp):
        return None
    dev_type = resp[pay]
    conn_code = resp[pay + 1]
    bat_byte = resp[pay + 6]
    raw_bat = bat_byte & 0x0F
    charge_state = (bat_byte >> 4) & 0x0F
    full = charge_state == 2
    charging = (charge_state != 0) and (not full)

    if raw_bat <= 5:
        level = raw_bat * 20
    elif raw_bat <= 10:
        level = raw_bat * 10
    elif raw_bat <= 100:
        level = raw_bat
    else:
        level = 100

    # 实机核对（2026-11-20，APEX 6 Pro）：
    #   有线 USB（插线充电中）：conn_code=0x00, bat_byte=0x13 (60%, charge_state=1)
    #   2.4G 接收器（未插线）  ：conn_code=0x01, bat_byte=0x04 (80%, charge_state=0)
    conn_map = {0: "有线 USB", 1: "2.4G", 2: "2.4G", 3: "蓝牙"}
    return {
        "level": level,
        "raw_bat": raw_bat,
        "bat_byte": bat_byte,
        "charge_state": charge_state,
        "charging": charging,
        "full": full,
        "dev_type": dev_type,
        "model": FLYDIGI_TYPES.get(dev_type, f"Flydigi ({dev_type})"),
        "connection": conn_map.get(conn_code, f"模式 {conn_code}"),
    }


def strategy_flydigi(iface: HidInterface) -> BatteryResult:
    if iface.usage_page != 0xFFA0 or iface.input_len < 32 or iface.output_len < 32:
        return BatteryResult(error="非 Flydigi 0xFFA0 控制接口")

    req = build_flydigi_cmd(iface.output_len or 33)
    try:
        with HidDevice(iface.path, write=True) as dev:
            dev.drain()
            if not _try_write_output(dev, req, prefer_hidd=False):
                return BatteryResult(error="发送 Flydigi 查询指令失败")
            for _ in range(6):
                r = dev.read_input(timeout_ms=150, length=iface.input_len or 33)
                if not r:
                    continue
                parsed = parse_flydigi_report(r)
                if parsed:
                    return BatteryResult(
                        level=parsed["level"],
                        charging=parsed["charging"],
                        full=bool(parsed.get("full", False)),
                        connection=parsed["connection"],
                        source="Flydigi FFA0 (5A A5 01)",
                        raw_hex=_fmt_hex(r[:20]),
                        detected_model=parsed["model"],
                        detail=(
                            f"{parsed['model']} · {parsed['connection']} · "
                            f"电量 {parsed['level']}% (bat=0x{parsed.get('bat_byte', parsed['raw_bat']):02X})"
                        ),
                    )
    except OSError as e:
        return BatteryResult(error=f"打开 Flydigi 接口失败: {e}")

    return BatteryResult(error="未收到 Flydigi 状态回复（请确认手柄已开机或插入 2.4G 接收器）")


# ==============================================================================
# 4. ATK / VXE / VGN 鼠标协议
# ==============================================================================

def _build_atk_cmd(rid: int, cmd: int, length: int = 17) -> bytes:
    buf = bytearray(length)
    buf[0] = rid & 0xFF
    buf[1] = cmd & 0xFF
    buf[16] = (sum(buf[:16]) ^ 0xFF) & 0xFF
    return bytes(buf)


def parse_atk_vgn_report(raw: bytes) -> Optional[dict[str, Any]]:
    """解析 ATK/VGN 17 字节电量回复包。"""
    if not raw or len(raw) < 10 or raw[0] != 0x08 or raw[1] != 0x04:
        return None
    lvl = raw[6]
    if not (0 <= lvl <= 100):
        return None
    charging = bool(raw[7])
    mv = (raw[8] << 8) | raw[9]
    return {
        "level": lvl,
        "charging": charging,
        "voltage_mv": mv if 2500 <= mv <= 4500 else None,
    }


def strategy_atk_vgn(iface: HidInterface) -> BatteryResult:
    if iface.usage_page != 0xFF02 or iface.input_len < 16 or iface.output_len < 16:
        return BatteryResult(error="非 ATK/VGN 17 字节协议接口")
    req = _build_atk_cmd(0x08, 0x04, max(17, iface.output_len))
    try:
        with HidDevice(iface.path, write=True) as dev:
            dev.drain()
            if not _try_write_output(dev, req, prefer_hidd=True):
                return BatteryResult(error="ATK/VGN Output 写入失败")
            for _ in range(5):
                r = dev.read_input(timeout_ms=180, length=max(17, iface.input_len))
                if not r:
                    continue
                parsed = parse_atk_vgn_report(r)
                if parsed:
                    return BatteryResult(
                        level=parsed["level"],
                        charging=parsed["charging"],
                        voltage_mv=parsed["voltage_mv"],
                        connection="2.4G",
                        source="ATK/VGN Vendor (0x08 0x04)",
                        raw_hex=_fmt_hex(r[:17]),
                        detail=f"电量 {parsed['level']}%",
                    )
    except OSError as e:
        return BatteryResult(error=f"打开 ATK/VGN 接口失败: {e}")
    return BatteryResult(asleep=True, error="ATK/VGN 未回复（鼠标可能休眠）")


# ==============================================================================
# 5. 罗技 Logitech HID++ 2.0 (046D:*)
# ==============================================================================

def parse_logitech_hidpp_battery(feat_id_lo: int, bat_r: bytes) -> Optional[dict[str, Any]]:
    """解析 Logitech HID++ 2.0 BatteryStatus (0x1000) / UnifiedBattery (0x1004) 回复。"""
    if not bat_r or len(bat_r) < 7 or bat_r[2] == 0xFF:
        return None
    lvl = bat_r[4]
    if not (0 <= lvl <= 100):
        return None
    status_code = bat_r[6]
    is_full = status_code == 3
    charging = (status_code in (1, 2)) and (not is_full)
    feat_name = "UnifiedBattery" if feat_id_lo == 0x04 else "BatteryStatus"
    return {
        "level": lvl,
        "charging": charging,
        "full": is_full,
        "status_code": status_code,
        "feat_name": feat_name,
    }


def strategy_logitech_hidpp(iface: HidInterface) -> BatteryResult:
    if iface.usage_page != 0xFF00 or iface.input_len < 7:
        return BatteryResult(error="非罗技 HID++ 接口")

    def hidpp_call(dev: HidDevice, dev_idx: int, feat_idx: int, func: int, params: bytes = b"") -> Optional[bytes]:
        buf = bytearray(7)
        buf[0] = 0x10
        buf[1] = dev_idx
        buf[2] = feat_idx
        buf[3] = ((func & 0x0F) << 4) | 0x0A
        buf[4 : 4 + len(params[:3])] = params[:3]
        if not _try_write_output(dev, bytes(buf), prefer_hidd=False):
            return None
        for _ in range(5):
            r = dev.read_input(timeout_ms=150, length=max(20, iface.input_len))
            if r and len(r) >= 7 and r[0] in (0x10, 0x11) and r[1] == dev_idx:
                if r[2] == 0xFF:
                    return None
                if r[2] == feat_idx:
                    return r
        return None

    try:
        with HidDevice(iface.path, write=True) as dev:
            dev.drain()
            for dev_idx in (0xFF, 0x01, 0x02):
                for feat_id_hi, feat_id_lo, name in ((0x10, 0x04, "UnifiedBattery"), (0x10, 0x00, "BatteryStatus")):
                    idx_r = hidpp_call(dev, dev_idx, 0x00, 0x00, bytes([feat_id_hi, feat_id_lo]))
                    if not idx_r or idx_r[4] == 0:
                        continue
                    f_idx = idx_r[4]
                    bat_r = hidpp_call(dev, dev_idx, f_idx, 0x01 if feat_id_lo == 0x04 else 0x00)
                    parsed = parse_logitech_hidpp_battery(feat_id_lo, bat_r or b"")
                    if parsed and bat_r is not None:
                        return BatteryResult(
                            level=parsed["level"],
                            charging=parsed["charging"],
                            full=parsed["full"],
                            connection="2.4G / Bolt / Unifying",
                            source=f"Logitech HID++ 2.0 ({name})",
                            raw_hex=_fmt_hex(bat_r[:12]),
                            detail=f"dev_idx=0x{dev_idx:02X}, {name} {parsed['level']}%",
                        )
    except OSError as e:
        return BatteryResult(error=f"打开罗技接口失败: {e}")

    return BatteryResult(error="未检测到活跃罗技 HID++ 无线设备")


# ==============================================================================
# 6. DARKRIM / 杰理 Jieli RCSP 耳机 (3654:6901, 35BB:*)
# ==============================================================================

def strategy_darkrim(iface: HidInterface) -> BatteryResult:
    if iface.usage_page != 0xFF02:
        return BatteryResult(error="非 Jieli / DARKRIM 控制接口")

    if iface.vid == 0x3654:
        lvl, err = darkrim_proto.read_battery_only(iface=iface, timeout=0.45)
        if lvl is not None:
            return BatteryResult(
                level=lvl,
                connection="2.4G",
                source="DARKRIM 0xFE RCSP",
                detail=f"DARKRIM 电量 {lvl}%",
            )
        return BatteryResult(error=err or "DARKRIM 耳机未回应")

    rid_candidates = (0x02, 0x00, 0x01)
    pkt_size = max(16, iface.output_len or iface.input_len or 64)
    try:
        with HidDevice(iface.path, write=True) as dev:
            for rid in rid_candidates:
                for ch, sub in ((darkrim_proto.CH_LOCAL, 0x02), (darkrim_proto.CH_HEADSET, 0x02)):
                    base = darkrim_proto.build_frame(
                        0, bytes([darkrim_proto.TLV_BATTERY, 0x00]), ch=ch, sub=sub
                    )
                    req = bytearray(pkt_size)
                    req[: min(len(base), pkt_size)] = base[: min(len(base), pkt_size)]
                    req[0] = rid
                    dev.drain()
                    if not _try_write_output(dev, bytes(req), prefer_hidd=False):
                        continue
                    for _ in range(3):
                        r = dev.read_input(timeout_ms=140, length=max(64, iface.input_len))
                        if not r:
                            continue
                        frame = darkrim_proto.parse_frame(r)
                        if frame and frame.crc_ok:
                            for t, v in frame.tlvs():
                                if t == darkrim_proto.TLV_BATTERY and v and v[0] <= 100:
                                    return BatteryResult(
                                        level=v[0],
                                        connection="2.4G",
                                        source=f"Jieli RCSP (rid=0x{rid:02X})",
                                        raw_hex=_fmt_hex(r[:20]),
                                        detail=f"电量 {v[0]}%",
                                    )
    except OSError as e:
        return BatteryResult(error=f"打开耳机接口失败: {e}")

    return BatteryResult(
        error="HECATE 2.4G 接收器未回应标准 JL RCSP 查询（多数杰理 2.4G 接收器不透传量化电量）"
    )


# ==============================================================================
# 7. Sony DualSense / DualShock 4 (054C:* 公开 HID 报告协议)
# ==============================================================================

def parse_sony_controller_report(raw: bytes, pid: int = 0x0CE6) -> Optional[dict[str, Any]]:
    """解析 Sony DualSense (0x0CE6/0x0DF2) 与 DualShock 4 (0x05C4/0x09CC) USB/BT 电池字段。"""
    if not raw or len(raw) < 31:
        return None
    rid = raw[0]
    is_dualsense = pid in (0x0CE6, 0x0DF2)
    if is_dualsense:
        if rid == 0x01 and len(raw) >= 54:
            bat_byte = raw[53]
            conn = "有线 USB"
        elif rid == 0x31 and len(raw) >= 55:
            bat_byte = raw[54]
            conn = "蓝牙"
        else:
            return None
    else:
        if rid == 0x01 and len(raw) >= 31:
            bat_byte = raw[30]
            conn = "有线 USB"
        elif rid == 0x11 and len(raw) >= 33:
            bat_byte = raw[32]
            conn = "蓝牙"
        else:
            return None

    raw_step = bat_byte & 0x0F
    state = (bat_byte >> 4) & 0x0F
    if state not in (0x0, 0x1, 0x2, 0x3, 0xA, 0xB):
        return None
    level = 100 if raw_step >= 10 else min(100, raw_step * 10 + 5)
    if state == 0x2:
        full = True
        charging = False
        level = 100
    elif state == 0x1:
        charging = True
        full = False
    else:
        charging = False
        full = False
    return {
        "level": level,
        "charging": charging,
        "full": full,
        "connection": conn,
        "raw_step": raw_step,
        "state": state,
        "bat_byte": bat_byte,
    }


def strategy_sony_dualsense(iface: HidInterface) -> BatteryResult:
    if iface.vid != 0x054C or iface.input_len < 31:
        return BatteryResult(error="非 Sony 手柄 HID 输入接口")
    try:
        with HidDevice(iface.path, write=False) as dev:
            for _ in range(4):
                r = dev.read_input(timeout_ms=200, length=max(64, iface.input_len))
                if not r:
                    continue
                parsed = parse_sony_controller_report(r, iface.pid)
                if parsed:
                    return BatteryResult(
                        level=parsed["level"],
                        charging=parsed["charging"],
                        full=parsed["full"],
                        connection=parsed["connection"],
                        source="Sony DualSense/DS4 HID (公开协议)",
                        raw_hex=_fmt_hex(r[:24]),
                        detail=f"DualSense/DS4 · {parsed['connection']} · 电量 {parsed['level']}% (bat=0x{parsed['bat_byte']:02X})",
                    )
    except OSError as e:
        return BatteryResult(error=f"打开 Sony 手柄接口失败: {e}")
    return BatteryResult(error="未收到 Sony 手柄输入报告（请按下手柄按键唤醒）")


# ==============================================================================
# 8. Razer 雷蛇 90B 公开协议 (1532:*, Feature Report + XOR 校验)
# ==============================================================================

def build_razer_cmd(cmd_class: int, cmd_id: int, data_size: int = 0x02, tx_id: int = 0x1F) -> bytes:
    """构建 OpenRazer 91 字节 Feature Report（首字节 Report ID 0x00 + 90 字节标准帧）。"""
    frame = bytearray(90)
    frame[0] = 0x00  # status
    frame[1] = tx_id & 0xFF
    frame[2] = 0x00
    frame[3] = 0x00
    frame[4] = 0x00
    frame[5] = data_size & 0xFF
    frame[6] = cmd_class & 0xFF
    frame[7] = cmd_id & 0xFF
    crc = 0
    for b in frame[2:88]:
        crc ^= b
    frame[88] = crc & 0xFF
    frame[89] = 0x00
    return bytes([0x00]) + bytes(frame)


def parse_razer_report(raw: bytes) -> Optional[dict[str, Any]]:
    """解析 OpenRazer 90B/91B 电池或充电状态回复（status=0x02 成功）。"""
    if not raw:
        return None
    pkt = raw[1:91] if (len(raw) >= 91 and raw[0] == 0x00) else raw[:90]
    if len(pkt) < 90:
        return None
    crc = 0
    for b in pkt[2:88]:
        crc ^= b
    if crc != pkt[88] or pkt[0] != 0x02:
        return None
    cmd_class = pkt[6]
    cmd_id = pkt[7]
    if cmd_class != 0x07:
        return None
    if cmd_id == 0x80:
        raw_bat = pkt[9]
        lvl = max(0, min(100, round((raw_bat * 100.0) / 255.0)))
        return {"kind": "level", "level": lvl, "raw_bat": raw_bat}
    if cmd_id == 0x84:
        return {"kind": "charging", "charging": bool(pkt[9])}
    return None


def strategy_razer(iface: HidInterface) -> BatteryResult:
    if iface.vid != 0x1532 or iface.feature_len < 90:
        return BatteryResult(error="非 Razer 90B Feature 接口")
    try:
        with HidDevice(iface.path, write=True) as dev:
            _try_set_feature(dev, build_razer_cmd(0x07, 0x80))
            time.sleep(0.05)
            r_lvl = _try_get_feature(dev, 0x00, max(91, iface.feature_len))
            parsed_lvl = parse_razer_report(r_lvl or b"")
            if not parsed_lvl or parsed_lvl.get("kind") != "level":
                return BatteryResult(error="Razer 设备未回复有效电量帧")
            charging: Optional[bool] = None
            _try_set_feature(dev, build_razer_cmd(0x07, 0x84))
            time.sleep(0.04)
            r_chg = _try_get_feature(dev, 0x00, max(91, iface.feature_len))
            parsed_chg = parse_razer_report(r_chg or b"")
            if parsed_chg and parsed_chg.get("kind") == "charging":
                charging = bool(parsed_chg["charging"])
            lvl = int(parsed_lvl["level"])
            return BatteryResult(
                level=lvl,
                charging=charging,
                full=bool(charging and lvl >= 100),
                connection="2.4G / USB",
                source="Razer 90B Feature (OpenRazer 公开协议)",
                raw_hex=_fmt_hex((r_lvl or b"")[:20]),
                detail=f"Razer 电量 {lvl}%",
            )
    except OSError as e:
        return BatteryResult(error=f"打开 Razer 接口失败: {e}")


# ==============================================================================
# 9. SteelSeries 赛睿 无线协议 (1038:*, HeadsetControl 公开协议)
# ==============================================================================

def parse_steelseries_report(raw: bytes, pid: int = 0) -> Optional[dict[str, Any]]:
    """解析 SteelSeries Arctis / Aerox 公开电量回复帧。"""
    if not raw or len(raw) < 5:
        return None
    # Arctis Nova 7 / 7P / 7X: 0x06 0xB0 [status] [level] [charging]
    if raw[0] == 0x06 and raw[1] == 0xB0 and len(raw) >= 4:
        lvl = raw[3]
        if 0 <= lvl <= 100:
            chg = bool(raw[4] == 1) if len(raw) >= 5 else False
            return {"level": lvl, "charging": chg, "online": raw[2] != 0}
    # Arctis 7+ / Pro / legacy: 0x06 0x18 [level] [charging]
    if raw[0] == 0x06 and raw[1] == 0x18:
        lvl = raw[2]
        if 0 <= lvl <= 100:
            chg = bool(raw[3] == 1) if len(raw) >= 4 else False
            return {"level": lvl, "charging": chg, "online": True}
    # SteelSeries Aerox/Rival 无线鼠标 0xD2 回复: 0xD2 [level] [charging]
    if raw[0] == 0xD2 and len(raw) >= 2:
        lvl = raw[1]
        if 0 <= lvl <= 100:
            chg = bool(raw[2] == 1) if len(raw) >= 3 else False
            return {"level": lvl, "charging": chg, "online": True}
    return None


def strategy_steelseries(iface: HidInterface) -> BatteryResult:
    if iface.vid != 0x1038 or iface.usage_page < 0xFF00 or iface.output_len < 16 or iface.input_len < 16:
        return BatteryResult(error="非 SteelSeries 私有通信接口")
    pkt_out = max(64, iface.output_len)
    pkt_in = max(64, iface.input_len)
    probes = (bytes([0x06, 0xB0]), bytes([0x06, 0x18]), bytes([0xD2]))
    try:
        with HidDevice(iface.path, write=True) as dev:
            for head in probes:
                buf = bytearray(pkt_out)
                buf[: len(head)] = head
                dev.drain()
                if not _try_write_output(dev, bytes(buf), prefer_hidd=True):
                    continue
                for _ in range(3):
                    r = dev.read_input(timeout_ms=160, length=pkt_in)
                    if not r:
                        continue
                    parsed = parse_steelseries_report(r, iface.pid)
                    if parsed:
                        if not parsed["online"]:
                            return BatteryResult(asleep=True, error="SteelSeries 耳机未连接或休眠")
                        return BatteryResult(
                            level=parsed["level"],
                            charging=parsed["charging"],
                            full=bool(parsed["charging"] and parsed["level"] >= 100),
                            connection="2.4G",
                            source="SteelSeries HID (HeadsetControl 公开协议)",
                            raw_hex=_fmt_hex(r[:16]),
                            detail=f"SteelSeries 电量 {parsed['level']}%",
                        )
    except OSError as e:
        return BatteryResult(error=f"打开 SteelSeries 接口失败: {e}")
    return BatteryResult(error="未收到 SteelSeries 电量回复")


# ==============================================================================
# 10. 标准 HID 电量 & 自定义协议
# ==============================================================================

def strategy_standard_battery_usage(iface: HidInterface) -> BatteryResult:
    battery_pairs = {(up, u): desc for up, u, desc in BATTERY_USAGES}
    try:
        with HidDevice(iface.path, write=False) as dev:
            for rt, rt_name in ((HidP_Feature, "Feature"), (HidP_Input, "Input")):
                caps = dev.value_caps(rt)
                matched = [
                    c for c in caps
                    if not c.IsRange and (c.UsagePage, c.u.NotRange.Usage) in battery_pairs
                ]
                if not matched:
                    continue
                for cap in matched:
                    if rt == HidP_Feature:
                        raw = _try_get_feature(dev, cap.ReportID, iface.feature_len or 64)
                    else:
                        raw = dev.read_input(timeout_ms=150, length=iface.input_len or 64)
                    if not raw:
                        continue
                    val = dev.usage_value(rt, raw, cap)
                    if val is None:
                        continue
                    max_v = cap.LogicalMax if cap.LogicalMax and cap.LogicalMax > 0 else 100
                    lvl = scale_level(val, max_v)
                    desc = battery_pairs[(cap.UsagePage, cap.u.NotRange.Usage)]
                    return BatteryResult(
                        level=lvl,
                        source=f"标准 HID {desc} ({rt_name})",
                        raw_hex=_fmt_hex(raw[:24]),
                        detail=f"Usage=0x{cap.UsagePage:04X}/0x{cap.u.NotRange.Usage:04X} val={val}/{max_v} -> {lvl}%",
                    )
    except OSError:
        pass
    return BatteryResult(error="未包含标准 HID 电量字段")


def strategy_vendor_protocol(iface: HidInterface, spec: ProtocolSpec) -> BatteryResult:
    try:
        with HidDevice(iface.path, write=True) as dev:
            raw: Optional[bytes] = None
            if spec.method == "feature":
                if spec.command_hex.strip():
                    cmd = bytes.fromhex(spec.command_hex)
                    _try_set_feature(dev, cmd.ljust(max(iface.feature_len, len(cmd)), b"\x00"))
                    time.sleep(0.06)
                raw = _try_get_feature(dev, spec.report_id, iface.feature_len or spec.report_len or 64)
            elif spec.method == "output_then_input":
                cmd = bytes.fromhex(spec.command_hex) if spec.command_hex.strip() else bytes([spec.report_id])
                if spec.report_id and cmd[0] != spec.report_id:
                    cmd = bytes([spec.report_id]) + cmd
                pkt = cmd.ljust(iface.output_len or spec.report_len or len(cmd), b"\x00")
                dev.drain()
                if _try_write_output(dev, pkt, prefer_hidd=spec.use_hidd_output):
                    expect = bytes.fromhex(spec.expect_prefix_hex) if spec.expect_prefix_hex.strip() else b""
                    for _ in range(5):
                        r = dev.read_input(timeout_ms=160, length=iface.input_len or spec.report_len or 64)
                        if r and (not expect or r.startswith(expect)):
                            raw = r
                            break
            elif spec.method == "passive_input":
                raw = dev.read_input(timeout_ms=250, length=iface.input_len or spec.report_len or 64)

            if not raw or spec.level_offset >= len(raw):
                return BatteryResult(error=f"未读到 {spec.name} 有效回复")

            lvl = scale_level(raw[spec.level_offset], spec.level_max)
            charging = None
            if spec.charging_offset is not None and spec.charging_offset < len(raw):
                charging = bool(raw[spec.charging_offset] & spec.charging_mask)
            mv = None
            if spec.voltage_offset is not None and spec.voltage_offset + 1 < len(raw):
                hi, lo = raw[spec.voltage_offset], raw[spec.voltage_offset + 1]
                mv = (hi << 8) | lo if spec.voltage_endian == "big" else hi | (lo << 8)
            return BatteryResult(
                level=lvl,
                charging=charging,
                voltage_mv=mv,
                source=f"自定义协议: {spec.name}",
                raw_hex=_fmt_hex(raw[:24]),
                detail=f"{spec.name} {lvl}%",
            )
    except Exception as e:
        return BatteryResult(error=f"自定义协议错误: {e}")


# ==============================================================================
# 统一调度：按接口 / 按物理设备读取电量
# ==============================================================================

def _pick_strategies(iface: HidInterface) -> list[Callable[[HidInterface], BatteryResult]]:
    out: list[Callable[[HidInterface], BatteryResult]] = []
    if iface.vid in (0x3837, 0x5253) and iface.usage_page in (0xFF01, 0xFFA0) and iface.feature_len >= 13:
        out.append(strategy_mchose_feature)
    if iface.vid == 0x41E4:
        if iface.usage_page == 0xFF02 and iface.input_len >= 20 and iface.output_len >= 20:
            out.append(strategy_sinowealth_20b)
        if iface.usage_page in (0xFF01, 0xFFA0) and iface.feature_len >= 13:
            out.append(strategy_mchose_feature)
    if iface.usage_page == 0xFF02 and iface.input_len == 20 and iface.output_len == 20:
        if strategy_sinowealth_20b not in out:
            out.append(strategy_sinowealth_20b)
    if iface.vid == 0x37D7 and iface.usage_page == 0xFFA0 and iface.input_len >= 32:
        out.append(strategy_flydigi)
    if iface.vid in (0x373B, 0x3554) and iface.usage_page == 0xFF02 and 16 <= iface.output_len <= 32:
        out.append(strategy_atk_vgn)
    if iface.vid == 0x046D and iface.usage_page == 0xFF00:
        out.append(strategy_logitech_hidpp)
    if iface.vid in (0x3654, 0x35BB) and iface.usage_page == 0xFF02:
        out.append(strategy_darkrim)
    if iface.vid == 0x054C and iface.input_len >= 31:
        out.append(strategy_sony_dualsense)
    if iface.vid == 0x1532 and iface.feature_len >= 90:
        out.append(strategy_razer)
    if iface.vid == 0x1038 and iface.usage_page >= 0xFF00 and iface.output_len >= 16 and iface.input_len >= 16:
        out.append(strategy_steelseries)
    return out


def read_battery(iface: HidInterface, user_specs: Optional[list[ProtocolSpec]] = None) -> BatteryResult:
    """对单个 HID 接口尝试内置/自定义/标准协议读取电量。"""
    specs = load_user_protocols() if user_specs is None else user_specs
    errors: list[str] = []

    for sp in specs:
        if sp.vid is not None and sp.vid != iface.vid:
            continue
        if sp.pid is not None and sp.pid != iface.pid:
            continue
        if sp.usage_page is not None and sp.usage_page != iface.usage_page:
            continue
        if sp.usage is not None and sp.usage != iface.usage:
            continue
        res = strategy_vendor_protocol(iface, sp)
        if res.level is not None:
            tray_common.log_event(
                "INFO",
                "battery",
                f"命中自定义协议 [{sp.name}] {iface.vid:04X}:{iface.pid:04X} -> {res.level}%",
            )
            return res
        if res.error:
            errors.append(res.error)

    for fn in _pick_strategies(iface):
        res = fn(iface)
        if res.level is not None or res.asleep:
            tray_common.log_event(
                "DEBUG" if res.asleep else "INFO",
                "battery",
                f"命中策略 {fn.__name__} {iface.vid:04X}:{iface.pid:04X} -> "
                f"{'休眠' if res.asleep else f'{res.level}%'}",
            )
            return res
        if res.error:
            errors.append(res.error)

    std = strategy_standard_battery_usage(iface)
    if std.level is not None:
        tray_common.log_event(
            "INFO",
            "battery",
            f"命中标准 HID Battery {iface.vid:04X}:{iface.pid:04X} -> {std.level}%",
        )
        return std

    return BatteryResult(error=errors[0] if errors else (std.error or "该接口暂未匹配电量协议"))


def _iface_priority(iface: HidInterface) -> int:
    if _pick_strategies(iface):
        return 0
    if iface.usage_page >= 0xFF00:
        return 1
    if iface.feature_len > 0:
        return 2
    return 3


def read_device_battery(
    ifaces: list[HidInterface], user_specs: Optional[list[ProtocolSpec]] = None
) -> BatteryResult:
    """对同一物理外设的一组 HID 接口依次读取，优先返回有效电量，其次休眠提示。"""
    if not ifaces:
        return BatteryResult(error="未发现可用 HID 接口")
    specs = load_user_protocols() if user_specs is None else user_specs
    ordered = sorted(ifaces, key=_iface_priority)

    asleep_res: Optional[BatteryResult] = None
    first_err: Optional[BatteryResult] = None

    for iface in ordered:
        has_vendor = bool(_pick_strategies(iface)) or any(
            (s.vid is None or s.vid == iface.vid)
            and (s.pid is None or s.pid == iface.pid)
            and (s.usage_page is None or s.usage_page == iface.usage_page)
            for s in specs
        )
        if not has_vendor and iface.feature_len == 0 and iface.num_input_value_caps == 0:
            continue
        res = read_battery(iface, specs)
        if res.level is not None:
            return res
        if res.asleep and asleep_res is None:
            asleep_res = res
        elif first_err is None and res.error:
            first_err = res

    if asleep_res is not None:
        return asleep_res
    if first_err is not None:
        return first_err
    return BatteryResult(error="未找到支持电量读取的接口（可在「调试与标定」中探测）")


# ==============================================================================
# 调试辅助（供 app.py 「原始探测」页使用）
# ==============================================================================

def dump_feature_reports(iface: HidInterface) -> list[tuple[int, bytes]]:
    found: list[tuple[int, bytes]] = []
    if iface.feature_len <= 0:
        return found
    with HidDevice(iface.path, write=False) as dev:
        for rid in range(256):
            r = _try_get_feature(dev, rid, iface.feature_len)
            if r:
                found.append((rid, r))
    return found


def probe_report_ids(iface: HidInterface, timeout_ms: int = 80) -> list[tuple[int, bytes]]:
    found: list[tuple[int, bytes]] = []
    with HidDevice(iface.path, write=True) as dev:
        for rid in range(1, 32):
            if iface.feature_len > 0:
                r = _try_get_feature(dev, rid, iface.feature_len)
                if r:
                    found.append((rid, r))
                    continue
            if iface.output_len > 0:
                pkt = bytes([rid]) + bytes(iface.output_len - 1)
                dev.drain()
                if _try_write_output(dev, pkt, prefer_hidd=False):
                    r2 = dev.read_input(timeout_ms=timeout_ms, length=max(16, iface.input_len))
                    if r2:
                        found.append((rid, r2))
    return found


def send_command(
    iface: HidInterface, report_id: int, cmd_bytes: bytes, timeout_ms: int = 250
) -> list[bytes]:
    out: list[bytes] = []
    length = max(iface.output_len, len(cmd_bytes) + 1)
    pkt = bytearray(length)
    pkt[0] = report_id & 0xFF
    pkt[1 : 1 + len(cmd_bytes)] = cmd_bytes[: length - 1]
    with HidDevice(iface.path, write=True) as dev:
        dev.drain()
        if not _try_write_output(dev, bytes(pkt), prefer_hidd=False):
            return out
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline and len(out) < 6:
            r = dev.read_input(timeout_ms=80, length=max(16, iface.input_len))
            if r:
                out.append(r)
    return out


def monitor_input_reports(
    iface: HidInterface, stop_event, callback: Callable[[bytes], None], timeout_ms: int = 200
) -> None:
    try:
        with HidDevice(iface.path, write=False) as dev:
            while not stop_event.is_set():
                r = dev.read_input(timeout_ms=timeout_ms, length=max(16, iface.input_len))
                if r:
                    callback(r)
    except OSError:
        pass


def group_hid_devices(ifaces: Optional[list[HidInterface]] = None) -> dict[tuple[int, int, str], list[HidInterface]]:
    """按物理设备合并 HID 接口组。

    同一设备常拆出 MI_00/MI_01 等无 serial 的子集合与带 serial 的厂商控制集合；
    当同 (vid, pid) 仅存在单一非空 serial 时，将空 serial 子集合一并归入，避免卡片重复。
    """
    if ifaces is None:
        ifaces = enumerate_hid()
    serials_by_vp: dict[tuple[int, int], set[str]] = {}
    for d in ifaces:
        s = (d.serial or "").strip()
        if s:
            serials_by_vp.setdefault((d.vid, d.pid), set()).add(s)

    groups: dict[tuple[int, int, str], list[HidInterface]] = {}
    for d in ifaces:
        s = (d.serial or "").strip()
        if not s:
            vp_serials = serials_by_vp.get((d.vid, d.pid), set())
            if len(vp_serials) == 1:
                s = next(iter(vp_serials))
        groups.setdefault((d.vid, d.pid, s), []).append(d)
    return groups
