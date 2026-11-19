# -*- coding: utf-8 -*-
"""bt_battery.py —— 读取蓝牙耳机/音箱的电量（Windows 未公开 PnP 属性）

原理
----
Windows 的蓝牙协议栈收到耳机通过 HFP（免提协议）上报的电量后，会把它写进
设备 **Hands-Free / 免提** 节点上的一个未公开 PnP 属性：

    PROPERTYKEY {104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2   (DEVPROP_TYPE_BYTE)
    值 = 电量百分比（0-100）

注意两点：
  1. 这个属性挂在 **Hands-Free AG 节点** 上，不在设备主节点上——
     所以很多"查不到电量"的教程都是因为在主节点上找。
  2. 该属性由驱动在收到耳机上报时写入，**耳机没连上/不支持 HFP 电量上报时为 0 或不存在**。

同一个机制对蓝牙鼠标、手柄也有效（微软的 Xbox 手柄就是走这个属性）。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Optional

import hid_win
from hid_win import GUID, setupapi, kernel32

# 蓝牙电量属性：{104EA319-6EE2-4701-BD47-8DDBF425BBE5}, 2
FMTID_BLUETOOTH = GUID(
    0x104EA319, 0x6EE2, 0x4701,
    (ctypes.c_ubyte * 8)(0xBD, 0x47, 0x8D, 0xDB, 0xF4, 0x25, 0xBB, 0xE5),
)
PID_BATTERY_LEVEL = 2

DIGCF_ALLCLASSES = 0x04
DIGCF_PRESENT_ = 0x02
SPDRP_FRIENDLYNAME = 0x0C
SPDRP_DEVICEDESC = 0x00
ERROR_NOT_FOUND = 1168

# 蓝牙类 {e0cbf06c-cd8b-4647-bb8a-263b43f0f974}
CLASS_BLUETOOTH = GUID(
    0xE0CBF06C, 0xCD8B, 0x4647,
    (ctypes.c_ubyte * 8)(0xBB, 0x8A, 0x26, 0x3B, 0x43, 0xF0, 0xF9, 0x74),
)
# 系统类 {4d36e97d-e325-11ce-bfc1-08002be10318}（HFP 免提节点在这里）
CLASS_SYSTEM = GUID(
    0x4D36E97D, 0xE325, 0x11CE,
    (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18),
)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


setupapi.SetupDiEnumDeviceInfo.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInfo.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiGetDeviceRegistryPropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
setupapi.SetupDiGetDevicePropertyW.restype = wintypes.BOOL
setupapi.SetupDiGetDevicePropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.POINTER(PROPERTYKEY),
    ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
]
setupapi.SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]


@dataclass
class BluetoothDevice:
    name: str
    instance_id: str
    battery: Optional[int] = None
    has_property: bool = False
    reason: str = ""
    source: str = "Windows 蓝牙 HFP PnP"
    parts: list[dict[str, Any]] = field(default_factory=list)
    online: bool = True

    @property
    def is_handsfree(self) -> bool:
        low = self.name.lower()
        return "hands-free" in low or "免提" in self.name or "handsfree" in low


def _device_string(hdev, info, prop: int) -> str:
    buf = ctypes.create_string_buffer(1024)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceRegistryPropertyW(
            hdev, ctypes.byref(info), prop, None, buf, 1024, ctypes.byref(need)):
        return ""
    return buf.raw.decode("utf-16-le", errors="ignore").split("\x00")[0]


def _instance_id(hdev, info) -> str:
    buf = ctypes.create_unicode_buffer(512)
    need = wintypes.DWORD(0)
    if not setupapi.SetupDiGetDeviceInstanceIdW(
            hdev, ctypes.byref(info), buf, 512, ctypes.byref(need)):
        return ""
    return buf.value


def _read_battery_property(hdev, info) -> tuple[Optional[int], bool, str]:
    """返回 (电量, 属性是否存在, 说明)。"""
    key = PROPERTYKEY(FMTID_BLUETOOTH, PID_BATTERY_LEVEL)
    ptype = wintypes.ULONG(0)
    value = ctypes.c_ubyte(0)
    need = wintypes.DWORD(0)
    ok = setupapi.SetupDiGetDevicePropertyW(
        hdev, ctypes.byref(info), ctypes.byref(key), ctypes.byref(ptype),
        ctypes.byref(value), 1, ctypes.byref(need), 0,
    )
    if not ok:
        err = ctypes.get_last_error()
        if err == ERROR_NOT_FOUND:
            return None, False, "该设备没有电池属性（耳机可能未连接或不上报电量）"
        return None, False, f"读取失败 err={err}"
    level = int(value.value)
    if level == 0:
        return 0, True, "属性存在但值为 0（通常表示未连接或未上报）"
    return min(100, level), True, "来自蓝牙 HFP 电量上报"


_GATT_BATTERY_SERVICE_UUID = 0x180F
_GATT_BATTERY_LEVEL_CHAR_UUID = 0x2A19
_GATT_CHAR_USER_DESC_UUID = 0x2901
_GATT_CHAR_PRES_FMT_UUID = 0x2904
_BLUETOOTH_GATT_FLAG_NONE = 0x00000000
_BLE_SPDRP_DEVICEDESC = 0
_BLE_SPDRP_FRIENDLYNAME = 12

_BLE_PART_NS_LABELS = {
    0x010D: "左耳",
    0x010E: "右耳",
    0x0104: "主单元",
    0x0105: "副单元",
    0x0106: "充电盒",
}


def _get_ble_dev_reg_str(hdev: Any, dev_info: Any, prop: int) -> str:
    req = ctypes.c_uint32(0)
    setupapi.SetupDiGetDeviceRegistryPropertyW(
        hdev, ctypes.byref(dev_info), prop, None, None, 0, ctypes.byref(req)
    )
    if req.value <= 2 or req.value > 4096:
        return ""
    buf = ctypes.create_string_buffer(req.value)
    if not setupapi.SetupDiGetDeviceRegistryPropertyW(
        hdev, ctypes.byref(dev_info), prop, None, buf, req.value, None
    ):
        return ""
    return ctypes.wstring_at(buf).strip()


class _BTH_LE_UUID_VALUE(ctypes.Union):
    _fields_ = [
        ("ShortUuid", ctypes.c_ushort),
        ("LongUuid", GUID),
    ]


class _BTH_LE_UUID(ctypes.Structure):
    _fields_ = [
        ("IsShortUuid", ctypes.c_ubyte),
        ("Value", _BTH_LE_UUID_VALUE),
    ]


class _BTH_LE_GATT_SERVICE(ctypes.Structure):
    _fields_ = [
        ("ServiceUuid", _BTH_LE_UUID),
        ("AttributeHandle", ctypes.c_ushort),
    ]


class _BTH_LE_GATT_CHARACTERISTIC(ctypes.Structure):
    _fields_ = [
        ("ServiceHandle", ctypes.c_ushort),
        ("CharacteristicUuid", _BTH_LE_UUID),
        ("AttributeHandle", ctypes.c_ushort),
        ("CharacteristicValueHandle", ctypes.c_ushort),
        ("IsBroadcastable", ctypes.c_ubyte),
        ("IsReadable", ctypes.c_ubyte),
        ("IsWritable", ctypes.c_ubyte),
        ("IsWritableWithoutResponse", ctypes.c_ubyte),
        ("IsSignedWritable", ctypes.c_ubyte),
        ("IsNotifiable", ctypes.c_ubyte),
        ("IsIndicatable", ctypes.c_ubyte),
        ("HasExtendedProperties", ctypes.c_ubyte),
    ]


class _BTH_LE_GATT_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("ServiceHandle", ctypes.c_ushort),
        ("CharacteristicHandle", ctypes.c_ushort),
        ("DescriptorType", ctypes.c_int),
        ("DescriptorUuid", _BTH_LE_UUID),
        ("AttributeHandle", ctypes.c_ushort),
    ]


def _make_gatt_battery_service_guid() -> GUID:
    return GUID(
        0x0000180F,
        0x0000,
        0x1000,
        (ctypes.c_ubyte * 8)(0x80, 0x00, 0x00, 0x80, 0x5F, 0x9B, 0x34, 0xFB),
    )


def _uuid_matches_short(u: _BTH_LE_UUID, short_uuid: int) -> bool:
    if u.IsShortUuid:
        return int(u.Value.ShortUuid) == int(short_uuid)
    lg = u.Value.LongUuid
    tail = tuple(lg.Data4[i] for i in range(8))
    return (
        int(lg.Data1) == (0x00000000 | int(short_uuid))
        and int(lg.Data2) == 0x0000
        and int(lg.Data3) == 0x1000
        and tail == (0x80, 0x00, 0x00, 0x80, 0x5F, 0x9B, 0x34, 0xFB)
    )


def _extract_bt_mac(instance_id: str) -> str:
    s = (instance_id or "").upper()
    for token in s.replace("\\", "_").replace("&", "_").split("_"):
        if len(token) == 12 and all(ch in "0123456789ABCDEF" for ch in token):
            return token
    if "DEV_" in s:
        after = s.split("DEV_", 1)[1]
        hex12 = "".join(ch for ch in after if ch in "0123456789ABCDEF")
        if len(hex12) >= 12:
            return hex12[:12]
    return ""


def _read_gatt_value_bytes(bth_dll: Any, handle: int, obj_ptr: Any, is_descriptor: bool = False) -> bytes:
    needed = ctypes.c_ushort(0)
    fn = bth_dll.BluetoothGATTGetDescriptorValue if is_descriptor else bth_dll.BluetoothGATTGetCharacteristicValue
    hr = fn(handle, obj_ptr, 0, None, ctypes.byref(needed), _BLUETOOTH_GATT_FLAG_NONE)
    if needed.value < 8:
        return b""
    buf = (ctypes.c_ubyte * int(needed.value))()
    actual = ctypes.c_ushort(0)
    hr = fn(handle, obj_ptr, needed.value, ctypes.cast(buf, ctypes.c_void_p), ctypes.byref(actual), _BLUETOOTH_GATT_FLAG_NONE)
    if hr != 0:
        return b""
    if is_descriptor:
        # BTH_LE_GATT_DESCRIPTOR_VALUE header varies slightly; CharacteristicUserDescription starts after Type+Uuid+...
        # Use raw trailing data if DataSize fits cleanly, otherwise handle presentation format carefully.
        return bytes(buf[: int(actual.value or needed.value)])
    data_size = int(ctypes.c_ulong.from_buffer(buf, 0).value)
    if data_size <= 0 or (4 + data_size) > len(buf):
        return b""
    return bytes(buf[4 : 4 + data_size])


def _read_ble_gatt_char_label(bth_dll: Any, handle: int, char_obj: _BTH_LE_GATT_CHARACTERISTIC, fallback_idx: int) -> str:
    try:
        d_count = ctypes.c_ushort(0)
        bth_dll.BluetoothGATTGetDescriptors(handle, ctypes.byref(char_obj), 0, None, ctypes.byref(d_count), _BLUETOOTH_GATT_FLAG_NONE)
        if d_count.value <= 0:
            return f"单元{fallback_idx}" if fallback_idx > 1 else ""
        descs = (_BTH_LE_GATT_DESCRIPTOR * int(d_count.value))()
        actual = ctypes.c_ushort(0)
        hr = bth_dll.BluetoothGATTGetDescriptors(
            handle,
            ctypes.byref(char_obj),
            d_count.value,
            descs,
            ctypes.byref(actual),
            _BLUETOOTH_GATT_FLAG_NONE,
        )
        if hr != 0:
            return f"单元{fallback_idx}" if fallback_idx > 1 else ""
        for idx in range(min(int(actual.value), len(descs))):
            desc = descs[idx]
            if _uuid_matches_short(desc.DescriptorUuid, _GATT_CHAR_USER_DESC_UUID):
                raw = _read_gatt_value_bytes(bth_dll, handle, ctypes.byref(desc), is_descriptor=True)
                # DescriptorValue: DescriptorType(4)+DescriptorUuid(20)+union+DataSize(4)+Data[...]
                # Try UTF-8 text from trailing DataSize if plausible.
                if len(raw) > 28:
                    txt = raw[28:].rstrip(b"\x00").decode("utf-8", errors="ignore").strip()
                    if txt and any(ch.isprintable() for ch in txt):
                        return txt
            elif _uuid_matches_short(desc.DescriptorUuid, _GATT_CHAR_PRES_FMT_UUID):
                raw = _read_gatt_value_bytes(bth_dll, handle, ctypes.byref(desc), is_descriptor=True)
                # CharacteristicPresentationFormat: Format(1),Exp(1),Unit(2),Namespace(1),Description(2) starting after DescriptorUuid (~24 bytes)
                if len(raw) >= 31:
                    ns_desc = int.from_bytes(raw[29:31], "little")
                    if ns_desc in _BLE_PART_NS_LABELS:
                        return _BLE_PART_NS_LABELS[ns_desc]
    except Exception:
        pass
    return f"单元{fallback_idx}" if fallback_idx > 1 else ""


def _read_ble_gatt_interface_batteries(device_path: str) -> list[dict[str, Any]]:
    """Open a BLE GATT 0x180F interface handle and read all readable 0x2A19 battery level(s)."""
    try:
        bth_dll = ctypes.windll.LoadLibrary("BluetoothAPIs.dll")
    except OSError:
        return []

    CREATE_FILE = getattr(kernel32, "CreateFileW", None)
    CLOSE_HANDLE = getattr(kernel32, "CloseHandle", None)
    if CREATE_FILE is None or CLOSE_HANDLE is None:
        return []

    INVALID_HANDLE = hid_win.INVALID_HANDLE_VALUE
    handle = CREATE_FILE(device_path, 0xC0000000, 3, None, 3, 0, None)
    if handle in (None, 0, INVALID_HANDLE):
        handle = CREATE_FILE(device_path, 0x80000000, 3, None, 3, 0, None)
    if handle in (None, 0, INVALID_HANDLE):
        return []

    readings: list[dict[str, Any]] = []
    try:
        svc_count = ctypes.c_ushort(0)
        bth_dll.BluetoothGATTGetServices(handle, 0, None, ctypes.byref(svc_count), _BLUETOOTH_GATT_FLAG_NONE)
        if svc_count.value <= 0:
            return []
        svcs = (_BTH_LE_GATT_SERVICE * int(svc_count.value))()
        actual_svcs = ctypes.c_ushort(0)
        hr = bth_dll.BluetoothGATTGetServices(handle, svc_count.value, svcs, ctypes.byref(actual_svcs), _BLUETOOTH_GATT_FLAG_NONE)
        if hr != 0:
            return []
        for s_idx in range(min(int(actual_svcs.value), len(svcs))):
            svc = svcs[s_idx]
            if not _uuid_matches_short(svc.ServiceUuid, _GATT_BATTERY_SERVICE_UUID):
                continue
            c_count = ctypes.c_ushort(0)
            bth_dll.BluetoothGATTGetCharacteristics(handle, ctypes.byref(svc), 0, None, ctypes.byref(c_count), _BLUETOOTH_GATT_FLAG_NONE)
            if c_count.value <= 0:
                continue
            chars = (_BTH_LE_GATT_CHARACTERISTIC * int(c_count.value))()
            actual_chars = ctypes.c_ushort(0)
            hr_c = bth_dll.BluetoothGATTGetCharacteristics(
                handle,
                ctypes.byref(svc),
                c_count.value,
                chars,
                ctypes.byref(actual_chars),
                _BLUETOOTH_GATT_FLAG_NONE,
            )
            if hr_c != 0:
                continue
            for c_idx in range(min(int(actual_chars.value), len(chars))):
                ch = chars[c_idx]
                if not ch.IsReadable or not _uuid_matches_short(ch.CharacteristicUuid, _GATT_BATTERY_LEVEL_CHAR_UUID):
                    continue
                val_bytes = _read_gatt_value_bytes(bth_dll, handle, ctypes.byref(ch), is_descriptor=False)
                if not val_bytes:
                    continue
                lvl = int(val_bytes[0])
                if 0 <= lvl <= 100:
                    label = _read_ble_gatt_char_label(bth_dll, handle, ch, len(readings) + 1)
                    readings.append({"label": label, "level": lvl})
    except Exception:
        pass
    finally:
        try:
            CLOSE_HANDLE(handle)
        except Exception:
            pass
    return readings


def _build_ble_gatt_devices(raw_items: list[tuple[str, str, list[dict[str, Any]]]]) -> list[BluetoothDevice]:
    """Group per-interface BLE GATT battery readings by MAC/name into BluetoothDevice objects (supports multi-part)."""
    grouped: dict[str, dict[str, Any]] = {}
    for name, instance_id, raw_parts in raw_items:
        if not raw_parts:
            continue
        mac = _extract_bt_mac(instance_id)
        clean_name = (name or "").strip() or (f"BLE 设备 {mac}" if mac else "BLE 外设")
        gkey = f"mac:{mac}" if mac else f"id:{instance_id}"
        entry = grouped.setdefault(
            gkey,
            {
                "name": clean_name,
                "instance_id": instance_id,
                "parts": [],
            },
        )
        if clean_name and (not entry["name"] or entry["name"].startswith("BLE 设备")):
            entry["name"] = clean_name
        for pt in raw_parts:
            lvl = int(pt.get("level", -1))
            if not (0 <= lvl <= 100):
                continue
            lbl = str(pt.get("label") or "").strip()
            entry["parts"].append({"label": lbl, "level": lvl})

    out: list[BluetoothDevice] = []
    for entry in grouped.values():
        parts = entry["parts"]
        if not parts:
            continue
        if len(parts) == 1:
            lvl = parts[0]["level"]
            lbl = parts[0]["label"]
            reason = f"BLE GATT 0x180F{f' ({lbl})' if lbl else ''}"
            final_parts = [{"label": lbl or "电量", "level": lvl}] if lbl else []
        else:
            default_labels = ["主单元", "副单元", "充电盒"]
            final_parts = []
            for idx, p in enumerate(parts):
                lbl = p["label"] or (default_labels[idx] if idx < len(default_labels) else f"部件{idx + 1}")
                final_parts.append({"label": lbl, "level": p["level"]})
            lvl = min(p["level"] for p in final_parts)
            reason = " / ".join(f"{p['label']} {p['level']}%" for p in final_parts)
        out.append(
            BluetoothDevice(
                name=entry["name"],
                instance_id=entry["instance_id"],
                battery=lvl,
                has_property=True,
                reason=reason,
                source="Windows BLE GATT (0x180F)",
                parts=final_parts,
            )
        )
    return out


def list_ble_gatt_devices() -> list[BluetoothDevice]:
    """Enumerate present BLE GATT Battery Service (0x180F) device interfaces and read 0x2A19 levels."""
    guid = _make_gatt_battery_service_guid()
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid),
        None,
        None,
        hid_win.DIGCF_PRESENT | hid_win.DIGCF_DEVICEINTERFACE,
    )
    if not hdev or hdev == hid_win.INVALID_HANDLE_VALUE:
        return []

    raw_items: list[tuple[str, str, list[dict[str, Any]]]] = []
    try:
        idx = 0
        while True:
            if_data = hid_win.SP_DEVICE_INTERFACE_DATA()
            if_data.cbSize = ctypes.sizeof(hid_win.SP_DEVICE_INTERFACE_DATA)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                hdev,
                None,
                ctypes.byref(guid),
                idx,
                ctypes.byref(if_data),
            )
            if not ok:
                break
            idx += 1
            dev_info = SP_DEVINFO_DATA()
            dev_info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            req = ctypes.c_uint32(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(if_data), None, 0, ctypes.byref(req), ctypes.byref(dev_info)
            )
            if req.value <= 6:
                continue
            buf = ctypes.create_string_buffer(req.value)
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(if_data), buf, req.value, None, ctypes.byref(dev_info)
            ):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if not path:
                continue
            instance_id = ""
            id_buf = ctypes.create_unicode_buffer(512)
            if setupapi.SetupDiGetDeviceInstanceIdW(
                hdev, ctypes.byref(dev_info), id_buf, len(id_buf), None
            ):
                instance_id = id_buf.value or ""
            friendly = _get_ble_dev_reg_str(hdev, dev_info, _BLE_SPDRP_FRIENDLYNAME) or _get_ble_dev_reg_str(
                hdev, dev_info, _BLE_SPDRP_DEVICEDESC
            )
            parts = _read_ble_gatt_interface_batteries(path)
            if parts:
                raw_items.append((friendly, instance_id or path, parts))
    except Exception:
        pass
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return _build_ble_gatt_devices(raw_items)


def _merge_bt_sources(hfp_devices: list[BluetoothDevice], ble_devices: list[BluetoothDevice]) -> list[BluetoothDevice]:
    """Merge HFP PnP devices with BLE GATT devices, avoiding duplicates by BT MAC or normalized name."""
    merged = list(hfp_devices)
    seen_macs = {_extract_bt_mac(d.instance_id) for d in hfp_devices if _extract_bt_mac(d.instance_id)}
    seen_names = {(d.name or "").strip().lower() for d in hfp_devices if (d.name or "").strip()}
    for ble in ble_devices:
        mac = _extract_bt_mac(ble.instance_id)
        name_l = (ble.name or "").strip().lower()
        matched_idx: Optional[int] = None
        if mac and mac in seen_macs:
            for i, d in enumerate(merged):
                if _extract_bt_mac(d.instance_id) == mac:
                    matched_idx = i
                    break
        elif name_l and name_l in seen_names:
            for i, d in enumerate(merged):
                if (d.name or "").strip().lower() == name_l:
                    matched_idx = i
                    break
        if matched_idx is not None:
            existing = merged[matched_idx]
            if not existing.has_property or existing.battery is None:
                merged[matched_idx] = ble
            elif ble.parts and not existing.parts:
                existing.parts = list(ble.parts)
            continue
        merged.append(ble)
        if mac:
            seen_macs.add(mac)
        if name_l:
            seen_names.add(name_l)
    return merged


def list_bluetooth_devices() -> list[BluetoothDevice]:
    return _merge_bt_sources(_list_hfp_bluetooth_devices(), list_ble_gatt_devices())


class _SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", wintypes.WORD),
        ("wMonth", wintypes.WORD),
        ("wDayOfWeek", wintypes.WORD),
        ("wDay", wintypes.WORD),
        ("wHour", wintypes.WORD),
        ("wMinute", wintypes.WORD),
        ("wSecond", wintypes.WORD),
        ("wMilliseconds", wintypes.WORD),
    ]


class _BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("Address", ctypes.c_uint64),
        ("ulClassofDevice", wintypes.ULONG),
        ("fConnected", wintypes.BOOL),
        ("fRemembered", wintypes.BOOL),
        ("fAuthenticated", wintypes.BOOL),
        ("stLastSeen", _SYSTEMTIME),
        ("stLastUsed", _SYSTEMTIME),
        ("szName", wintypes.WCHAR * 248),
    ]


def _query_bt_connected_state(instance_id: str) -> Optional[bool]:
    """查询经典蓝牙设备是否处于真实已连接状态（避免 Windows 缓存旧电量误判为在线）。"""
    mac = _extract_bt_mac(instance_id)
    if not mac or len(mac) != 12:
        return None
    try:
        addr = int(mac, 16)
    except ValueError:
        return None
    try:
        bth = ctypes.WinDLL("bthprops.cpl")
        info = _BLUETOOTH_DEVICE_INFO()
        info.dwSize = ctypes.sizeof(_BLUETOOTH_DEVICE_INFO)
        info.Address = addr
        res = int(bth.BluetoothGetDeviceInfo(None, ctypes.byref(info)))
        if res == 0:
            return bool(info.fConnected)
    except Exception:
        pass
    return None


def _list_hfp_bluetooth_devices() -> list[BluetoothDevice]:
    """列出所有蓝牙相关设备及其电量（有的话）。

    两条枚举路径都试一遍再合并：有些 BTHENUM 节点只在设备已连接时存在，
    而且枚举器（enumerator）参数与 DIGCF_ALLCLASSES 的表现不完全一致。
    """
    found: dict[str, BluetoothDevice] = {}
    conn_cache: dict[str, Optional[bool]] = {}
    attempts = (
        (None, None, DIGCF_ALLCLASSES),
        ("BTHENUM", None, 0),
        (None, CLASS_SYSTEM, 0),
        (None, CLASS_BLUETOOTH, 0),
    )
    for enumerator, class_guid, flags in attempts:
        hdev = setupapi.SetupDiGetClassDevsW(
            ctypes.byref(class_guid) if class_guid is not None else None,
            enumerator, None, flags | DIGCF_PRESENT_,
        )
        if not hdev or hdev == ctypes.c_void_p(-1).value:
            continue
        try:
            index = 0
            while True:
                info = SP_DEVINFO_DATA()
                info.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
                if not setupapi.SetupDiEnumDeviceInfo(hdev, index, ctypes.byref(info)):
                    break
                index += 1
                iid = _instance_id(hdev, info)
                if not iid.upper().startswith("BTHENUM") or iid in found:
                    continue
                name = _device_string(hdev, info, SPDRP_FRIENDLYNAME)
                if not name:
                    name = _device_string(hdev, info, SPDRP_DEVICEDESC)
                level, has, reason = _read_battery_property(hdev, info)
                mac = _extract_bt_mac(iid)
                if mac:
                    if mac not in conn_cache:
                        conn_cache[mac] = _query_bt_connected_state(iid)
                    is_conn = conn_cache[mac]
                else:
                    is_conn = None
                online = True if is_conn is None else bool(is_conn)
                if not online and has and level is not None:
                    reason = f"蓝牙已断开（系统缓存 {level}%）"
                found[iid] = BluetoothDevice(
                    name=name or "(无名称)", instance_id=iid,
                    battery=level, has_property=has, reason=reason,
                    online=online,
                )
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return list(found.values())


def find_battery_devices() -> list[BluetoothDevice]:
    """只返回真正带电量值的蓝牙设备（优先 Hands-Free 节点）。"""
    devs = [d for d in list_bluetooth_devices()
            if d.has_property and d.battery is not None]
    devs.sort(key=lambda d: (0 if d.is_handsfree else 1, -(d.battery or 0)))
    return devs


def displayable_devices() -> list[BluetoothDevice]:
    """界面用：只保留“可能有电量”的节点，避免把 AVRCP 之类的杂项全列出来。"""
    keys = ("hands-free", "免提", "handsfree", "ag audio")
    out = []
    for d in list_bluetooth_devices():
        low = d.name.lower()
        if d.has_property or any(k in low for k in keys):
            out.append(d)
    return out


def refresh(dev: BluetoothDevice) -> BluetoothDevice:
    """重新读一次指定设备的电量。"""
    for d in list_bluetooth_devices():
        if d.instance_id == dev.instance_id:
            return d
    dev.battery = None
    dev.has_property = False
    dev.reason = "设备已不存在（耳机断开了）"
    return dev


if __name__ == "__main__":
    print("=== 所有蓝牙 HID/音频节点 ===")
    for d in list_bluetooth_devices():
        tag = "🔋" if (d.has_property and d.battery) else "  "
        print(f" {tag} {d.name}   电量={d.battery}  {d.reason}")
        print(f"      {d.instance_id}")
