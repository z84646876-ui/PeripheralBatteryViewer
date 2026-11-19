# -*- coding: utf-8 -*-
"""单元测试：协议构建、真实硬件报文解析、休眠检测、CRC 与图标渲染。"""

import atexit
import csv
import json
import os
import queue
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="pbv_test_data_")
os.environ["PBV_DATA_DIR"] = _TEST_DATA_DIR
atexit.register(shutil.rmtree, _TEST_DATA_DIR, True)

import battery
import battery_history
import bt_battery
import darkrim_proto
import devices
import neu_ui
import tray_common
import tray_service
import win_icon
from hid_win import HidInterface

tray_common.set_data_dir(_TEST_DATA_DIR)


class TestMchoseFeatureProtocol(unittest.TestCase):
    def test_live_plaintext_0x12_sample(self):
        # 实机 3837:100C GetFeature(0x12) 采样
        raw = bytes.fromhex(
            "11 06 da 0b 62 47 00 02 0a 03 09 4f 00 50 00 00 00 00 00 00"
        ) + b"\x00" * 45
        p = battery.parse_mchose_feature_payload(raw)
        self.assertIsNotNone(p)
        self.assertFalse(p["asleep"])
        self.assertEqual(p["donor_vid"], 0x0BDA)
        self.assertEqual(p["donor_pid"], 0x4762)
        self.assertEqual(p["flags"], 0x09)
        self.assertEqual(p["level"], 79)
        self.assertFalse(p["charging"])

    def test_live_inverted_0x11_sample(self):
        # 实机 3837:100C GetFeature(0x11) 取反响应采样
        raw = bytes.fromhex(
            "11 f9 25 f4 9d b8 ff fd f5 fc f6 b0 ff af ff ff ff ff ff ff"
        ) + b"\xff" * 45
        p = battery.parse_mchose_feature_payload(raw)
        self.assertIsNotNone(p)
        self.assertFalse(p["asleep"])
        self.assertEqual(p["donor_vid"], 0x0BDA)
        self.assertEqual(p["donor_pid"], 0x4762)
        self.assertEqual(p["level"], 79)
        self.assertFalse(p["charging"])

    def test_asleep_zero_payload(self):
        # 设备休眠时 flags/level/charging 全 0，应标记 asleep 而不是返回 0%
        raw = bytes([0x11, 0x06, 0xDA, 0x0B, 0x62, 0x47, 0x00, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00]) + b"\x00" * 52
        p = battery.parse_mchose_feature_payload(raw)
        self.assertIsNotNone(p)
        self.assertTrue(p["asleep"])

    def test_rejects_bad_header_or_vid_or_level(self):
        bad_hdr = bytes([0x10, 0x06, 0xDA, 0x0B]) + b"\x00" * 61
        self.assertIsNone(battery.parse_mchose_feature_payload(bad_hdr))

        bad_vid = bytes([0x11, 0x06, 0x99, 0x99, 0x00, 0x00, 0, 0, 0, 0, 9, 50, 0]) + b"\x00" * 52
        self.assertIsNone(battery.parse_mchose_feature_payload(bad_vid))

        bad_lvl = bytes([0x11, 0x06, 0x37, 0x38, 0x0C, 0x10, 0, 0, 0, 0, 9, 105, 0]) + b"\x00" * 52
        self.assertIsNone(battery.parse_mchose_feature_payload(bad_lvl))


class TestSinoWealth20BProtocol(unittest.TestCase):
    def test_build_commands_checksum(self):
        c_model = battery.build_sinowealth_cmd(0x05, 0x01, 20)
        self.assertEqual(len(c_model), 20)
        self.assertEqual(c_model[:3], b"\x13\x05\x01")
        self.assertEqual(c_model[19], 0x19)

        c_bat = battery.build_sinowealth_cmd(0x4A, 0x01, 20)
        self.assertEqual(c_bat[:3], b"\x13\x4A\x01")
        self.assertEqual(c_bat[19], 0x5E)

    def test_live_258a_sample(self):
        # 实机 258A:01BB RX 采样
        raw = bytes.fromhex(
            "13 4a 01 00 02 56 00 00 00 00 00 00 00 00 00 00 00 00 00 b6"
        )
        p = battery.parse_sinowealth_battery(raw)
        self.assertIsNotNone(p)
        self.assertEqual(p["level"], 86)
        self.assertFalse(p["charging"])
        self.assertFalse(p["full"])

    def test_charging_and_bad_checksum(self):
        pkt = bytearray(20)
        pkt[0:7] = bytes([0x13, 0x4A, 0x01, 0x00, 0x02, 42, 0x10])
        pkt[19] = sum(pkt[:19]) & 0xFF
        p = battery.parse_sinowealth_battery(bytes(pkt))
        self.assertIsNotNone(p)
        self.assertEqual(p["level"], 42)
        self.assertTrue(p["charging"])
        self.assertFalse(p["full"])

        pkt[19] ^= 0x01
        self.assertIsNone(battery.parse_sinowealth_battery(bytes(pkt)))


class TestFlydigiProtocol(unittest.TestCase):
    def test_build_33b_command(self):
        cmd = battery.build_flydigi_cmd(33)
        self.assertEqual(len(cmd), 33)
        self.assertEqual(cmd[:5], b"\x00\x5A\xA5\x01\x02")
        self.assertEqual(cmd[32], sum(cmd[3:32]) & 0xFF)

    def test_live_apex6_pro_sample(self):
        # 实机 37D7:2502 RX 采样（2.4G 接收器连接，conn_code=0x01）
        raw = bytes.fromhex(
            "00 5a a5 01 01 00 96 01 00 00 00 00 06 00 00 00 00 00 00 00"
        ) + b"\x00" * 13
        p = battery.parse_flydigi_report(raw)
        self.assertIsNotNone(p)
        self.assertEqual(p["model"], "Flydigi APEX 6 Pro")
        self.assertEqual(p["connection"], "2.4G")
        self.assertEqual(p["level"], 60)
        self.assertFalse(p["charging"])

        # 2026-11-20 实机 2.4G 未充电采样：不得再被误判为“有线 USB”
        raw_24g = bytes.fromhex(
            "00 5a a5 01 01 00 96 01 00 00 00 00 04 45 01 00 90 68 05 09 01 11 00 00 01 43 00 00 10 63 9f 00 51"
        )
        p2 = battery.parse_flydigi_report(raw_24g)
        self.assertEqual(p2["connection"], "2.4G")
        self.assertEqual(p2["level"], 80)
        self.assertFalse(p2["charging"])
        self.assertNotIn("有线", p2["connection"])

        # 2026-11-20 实机有线 USB 插线充电采样：conn_code=0x00, bat_byte=0x13 -> 有线 USB + charging=True + 60%
        raw_wired = bytes.fromhex(
            "00 5a a5 01 01 00 96 00 00 00 00 00 13 45 01 00 90 68 00 00 01 11 00 00 01 43 00 00 10 63 9f 00 51"
        )
        p3 = battery.parse_flydigi_report(raw_wired)
        self.assertEqual(p3["connection"], "有线 USB")
        self.assertEqual(p3["level"], 60)
        self.assertTrue(p3["charging"])
        self.assertFalse(p3["full"])

    def test_charging_24g(self):
        buf = bytearray(32)
        buf[0:3] = b"\x5A\xA5\x01"
        buf[5] = 150
        buf[6] = 2  # 2.4G
        buf[11] = 0x14  # charging + raw=4 -> 80%
        p = battery.parse_flydigi_report(bytes(buf))
        self.assertEqual(p["connection"], "2.4G")
        self.assertTrue(p["charging"])
        self.assertFalse(p["full"])
        self.assertEqual(p["level"], 80)

    def test_charged_full_and_wired_idle_states(self):
        # high nibble = 2 表示已充满，应自动结束充电态并置 full=True
        buf_full = bytearray(32)
        buf_full[0:3] = b"\x5A\xA5\x01"
        buf_full[5] = 150
        buf_full[6] = 1  # 2.4G
        buf_full[11] = 0x25  # full + 100%
        p_full = battery.parse_flydigi_report(bytes(buf_full))
        self.assertEqual(p_full["connection"], "2.4G")
        self.assertEqual(p_full["level"], 100)
        self.assertFalse(p_full["charging"])
        self.assertTrue(p_full["full"])

        # high nibble = 0：放电中（未充电）
        buf_idle = bytearray(32)
        buf_idle[0:3] = b"\x5A\xA5\x01"
        buf_idle[5] = 150
        buf_idle[6] = 1
        buf_idle[11] = 0x04  # 80% not charging
        p_idle = battery.parse_flydigi_report(bytes(buf_idle))
        self.assertEqual(p_idle["connection"], "2.4G")
        self.assertEqual(p_idle["level"], 80)
        self.assertFalse(p_idle["charging"])
        self.assertFalse(p_idle["full"])


class TestDarkrimAndHelpers(unittest.TestCase):
    def test_darkrim_packet_roundtrip(self):
        frame = darkrim_proto.build_frame(seq=3, payload=bytes([darkrim_proto.TLV_BATTERY, 0x01, 75]))
        self.assertEqual(len(frame), darkrim_proto.REPORT_LEN)
        self.assertEqual(frame[0], 0x00)
        self.assertEqual(frame[1], 0xFE)
        parsed = darkrim_proto.parse_frame(frame)
        self.assertIsNotNone(parsed)
        self.assertTrue(parsed.crc_ok)
        self.assertEqual(parsed.seq, 3)
        self.assertEqual(parsed.tlvs(), [(darkrim_proto.TLV_BATTERY, b"\x4b")])

    def test_scale_and_role_and_filtering(self):
        self.assertEqual(battery.scale_level(3, 5), 60)
        self.assertEqual(battery.scale_level(120, 100), 100)
        self.assertEqual(battery.infer_role(0x3837, 0x100C, "A5 ultra"), "mouse")
        self.assertEqual(battery.infer_role(0x258A, 0x01BB, "2.4G Wireless Receiver"), "keyboard")
        self.assertEqual(battery.infer_role(0x35BB, 0xA209, "HECATE G2无线版"), "headset")

        rgb = HidInterface("p1", 0x0B05, 0x19AF, 1, 0xFF72, 0x00A1, 64, 64, 0, "AURA LED Controller", "Asus", "", 0, 0, 0)
        self.assertFalse(devices._is_candidate_hid_group(0x0B05, 0x19AF, [rgb], []))

        mouse2 = HidInterface("p2", 0x1234, 0x5678, 1, 0xFF00, 0x0001, 32, 32, 0, "2.4G Mouse Dongle", "Vendor", "", 0, 0, 0)
        self.assertTrue(devices._is_candidate_hid_group(0x1234, 0x5678, [mouse2], []))

        # 同 VID:PID 下一个集合带 serial、另一个集合不带 serial 时应合并为同一设备
        g1 = HidInterface("p3", 0x37D7, 0x2502, 1, 0x0001, 0x0005, 16, 16, 0, "Flydigi APEX 6", "Flydigi", "", 0, 0, 0)
        g2 = HidInterface("p4", 0x37D7, 0x2502, 1, 0xFFA0, 0x0001, 33, 33, 0, "Flydigi APEX 6", "Flydigi", "Flydigi APEX 6", 0, 0, 0)
        grouped = battery.group_hid_devices([g1, g2])
        self.assertEqual(len(grouped), 1)

    def test_ico_generation(self):
        imgs = [(s, win_icon.render_rgba("86", s, (93, 220, 138), role="keyboard")) for s in (16, 24)]
        ico = win_icon.build_ico(imgs)
        reserved, kind, count = struct.unpack("<HHH", ico[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 2))

    def test_launch_args_script_and_frozen(self):
        orig_frozen = tray_common.IS_FROZEN
        try:
            tray_common.IS_FROZEN = True
            self.assertEqual(tray_common.build_launch_args("panel"), [sys.executable])
            self.assertEqual(tray_common.build_launch_args("tray", ["--debug"]), [sys.executable, "--tray", "--debug"])
            self.assertEqual(tray_common.build_launch_args("debug", ["--select", "3837:100C"]), [sys.executable, "--debug-ui", "--select", "3837:100C"])
            self.assertTrue(tray_common._is_tray_daemon_cmd("外设电量查看器.exe --tray"))
            self.assertFalse(tray_common._is_tray_daemon_cmd("外设电量查看器.exe --tray --status"))
            self.assertFalse(tray_common._is_tray_daemon_cmd("外设电量查看器.exe --tray --quit"))
        finally:
            tray_common.IS_FROZEN = orig_frozen


class TestBatteryColorsHistoryAndTray(unittest.TestCase):
    def test_battery_color_hex_updated_thresholds(self):
        self.assertEqual(devices.battery_color_hex(19), "#C07A80")
        self.assertEqual(devices.battery_color_hex(20), "#D8C68A")
        self.assertEqual(devices.battery_color_hex(39), "#D8C68A")
        self.assertEqual(devices.battery_color_hex(40), "#7FAFC7")
        self.assertEqual(devices.battery_color_hex(100), "#7FAFC7")
        # 动态低电阈值联动校验（#9/#10/#11）
        self.assertEqual(devices.battery_color_hex(25, low_threshold=30), "#C07A80")
        self.assertEqual(devices.battery_color_hex(35, low_threshold=30), "#D8C68A")
        self.assertEqual(devices.battery_color_hex(60, charging=True), "#D4B28C")
        self.assertEqual(devices.battery_color_hex(100, charging=True, full=True), "#7FAFC7")
        self.assertEqual(devices.battery_color_hex(None, offline=True), "#8a93a2")
        kind, hint = devices.classify_device_issue("read timeout occurred", "")
        self.assertEqual(kind, "timeout")
        self.assertIn("超时", hint)

    def test_reading_wired_and_offline_status_labels(self):
        r_wired_idle = devices.PeripheralReading(
            key="k1", role="controller", name="APEX 6 Pro", product="APEX 6",
            level=80, charging=False, full=False, connection="有线 USB",
        )
        self.assertEqual(r_wired_idle.power_state, "wired_power")
        self.assertEqual(r_wired_idle.status_text, "有线供电（未充电）")

        r_charging = devices.PeripheralReading(
            key="k1", role="controller", name="APEX 6 Pro", product="APEX 6",
            level=80, charging=True, full=False, connection="有线 USB",
        )
        self.assertEqual(r_charging.power_state, "charging")
        self.assertIn("充电中", r_charging.status_text)

        r_full = devices.PeripheralReading(
            key="k1", role="controller", name="APEX 6 Pro", product="APEX 6",
            level=100, charging=False, full=True, connection="有线 USB",
        )
        self.assertEqual(r_full.power_state, "full")
        self.assertIn("已充满", r_full.status_text)

        r_off = devices.PeripheralReading(
            key="k1", role="mouse", name="A5 Ultra", product="A5",
            connected=False, offline=True, level=None,
        )
        self.assertEqual(r_off.power_state, "offline")
        self.assertEqual(r_off.status_text, "已离线")

    def test_history_auto_prune_and_estimate(self):
        now = 1_800_000_000.0
        with tempfile.TemporaryDirectory() as tmp:
            hp = os.path.join(tmp, "hist.json")
            raw = {
                "version": 1,
                "devices": {
                    "dev1": [
                        {"t": now - 8 * 86400, "lvl": 95, "chg": False, "off": False},
                        {"t": now - 2 * 3600, "lvl": 90, "chg": False, "off": False},
                        {"t": now - 1 * 3600, "lvl": 85, "chg": False, "off": False},
                    ]
                },
            }
            saved = battery_history.save_history(raw, path=hp, now=now)
            pts = saved["devices"]["dev1"]
            self.assertEqual(len(pts), 2)  # 超过 7 天的记录被自动清理
            est = battery_history.estimate_remaining_hours(pts, current_level=80, now=now)
            self.assertIsNotNone(est)
            self.assertAlmostEqual(est, 16.0, delta=0.5)
            self.assertEqual(battery_history.format_remaining_text(est), "预估剩余 16h")

            # 无足够历史时严格显示“暂无预估数据”
            self.assertIsNone(battery_history.estimate_remaining_hours([], current_level=78, now=now))
            self.assertEqual(battery_history.format_remaining_text(None), "暂无预估数据")
            self.assertEqual(battery_history.format_remaining_text(14.0, offline=True), "设备已离线")

    def test_low_battery_monitor_and_controller_icon(self):
        mon = tray_service.LowBatteryMonitor(cooldown_sec=1800)
        cfg = {"notify_enabled": True, "notify_threshold": 20}
        low = devices.PeripheralReading(key="m1", role="mouse", name="M", product="M", level=18)
        chg = devices.PeripheralReading(key="k1", role="keyboard", name="K", product="K", level=15, charging=True)
        off = devices.PeripheralReading(key="h1", role="headset", name="H", product="H", offline=True, level=None)
        ok_dev = devices.PeripheralReading(key="c1", role="controller", name="C", product="C", level=20)

        hits = mon.check([low, chg, off, ok_dev], cfg, now=1000.0)
        self.assertEqual([r.key for r in hits], ["m1"])
        # 冷却期内不重复打扰
        self.assertEqual(mon.check([low], cfg, now=1100.0), [])

        imgs = [(s, win_icon.render_controller_logo_rgba(s, alert=(s == 32))) for s in (16, 32)]
        ico = win_icon.build_ico(imgs)
        self.assertEqual(struct.unpack("<HHH", ico[:6]), (0, 1, 2))

    def test_neumorphic_surfaces_modal_sparkline_and_widgets(self):
        import tkinter as tk

        orig_theme = neu_ui.get_theme_mode()
        root = tk.Tk()
        root.withdraw()
        try:
            raised = neu_ui.make_surface_photo(120, 80, radius=24, off=7, box_r=4, pad=19, inset=False)
            self.assertEqual((raised.width(), raised.height()), (158, 118))
            self.assertEqual(raised.get(0, 0), neu_ui.BASE_RGB)
            self.assertEqual(raised.get(79, 59), neu_ui.BASE_RGB)
            self.assertGreater(raised.get(79, 17)[0], neu_ui.BASE_RGB[0])
            self.assertLess(raised.get(79, 100)[0], neu_ui.BASE_RGB[0])

            recessed = neu_ui.make_surface_photo(120, 32, radius=16, off=4, box_r=3, pad=0, inset=True)
            self.assertEqual((recessed.width(), recessed.height()), (120, 32))
            self.assertLess(recessed.get(60, 1)[0], neu_ui.BASE_RGB[0])
            self.assertGreater(recessed.get(60, 30)[0], neu_ui.BASE_RGB[0])

            var = tk.BooleanVar(value=True)
            tb = neu_ui.NeuTitleBar(root, title="测试", subtitle="副标题")
            ib = neu_ui.NeuIconButton(root, "gear", lambda: None, text="")
            sp = neu_ui.NeuSparkline(root)
            now = 1_800_000_000.0
            sp.update_series([{"t": now - 3600, "lvl": 85}, {"t": now, "lvl": 78}], "#7FAFC7")
            modal = neu_ui.NeuModalOverlay(root, title="设置", min_height_dp=400)
            self.assertIsInstance(modal, tk.Canvas)
            self.assertEqual(len(root.winfo_children()) >= 1, True)
            modal.close()
            btn = neu_ui.NeuButton(root, text="测试按钮", accent=True)
            tgl = neu_ui.NeuToggle(root, text="开关", variable=var)
            # 点击开关不应绘制任何焦点矩形框
            tgl._toggle(None)
            self.assertFalse(var.get())
            self.assertEqual(
                [tgl._cv.type(item) for item in tgl._cv.find_all()].count("rectangle"),
                0,
            )
            bar = neu_ui.NeuProgressBar(root)
            bar.set_value(78, "#7FAFC7")

            # 低电量弹窗默认为 60% 不透明度与 8 秒自动关闭（带底部悬停可冻结进度条）
            import inspect
            sig = inspect.signature(tray_service.LowBatteryToastPopup.show)
            self.assertEqual(sig.parameters["auto_close_ms"].default, 8000)
            self.assertAlmostEqual(sig.parameters["alpha"].default, 0.60, places=2)
            toast = tray_service.LowBatteryToastPopup(root, on_open_main=lambda: None)
            toast.show(
                devices.PeripheralReading(key="m_low", role="mouse", name="LowMouse", product="M", level=15),
                auto_close_ms=0,
            )
            self.assertAlmostEqual(float(toast._win.attributes("-alpha")), 0.60, places=2)
            toast.dismiss()

            # 紧凑托盘悬停 Tooltip 不应包含进度条组件
            tip = tray_service.TrayHoverTooltip(
                root,
                get_icon_rect=lambda: (1000, 1000, 1032, 1040),
                on_open_main=lambda: None,
            )
            tip._ensure_window()
            self.assertTrue(all("bar" not in slot for slot in tip._cell_slots))
            tip.hide()

            # 内嵌调试工作区组件可随模态容器一同安全销毁
            import app as debug_app
            import my_devices

            ws_modal = neu_ui.NeuModalOverlay(root, title="调试与标定", max_width_dp=840, min_height_dp=540)
            ws = debug_app.DebugWorkspaceFrame(ws_modal.body, compact=True, show_header=False)
            ws.pack(fill="both", expand=True)
            root.update_idletasks()
            self.assertTrue(ws.winfo_exists())
            ws_modal.close()
            root.update_idletasks()
            self.assertFalse(ws.winfo_exists())

            # 设置 -> 调试与标定 -> 返回，应回到设置界面；再返回才回主界面
            panel_root = tk.Toplevel(root)
            panel_root.withdraw()
            panel = my_devices.BatteryPanelApp(panel_root, disable_tray_host=True)
            panel.open_settings_modal()
            self.assertIsNotNone(panel._settings_modal)
            panel.open_debug_tool(None)
            self.assertIsNone(panel._settings_modal)
            self.assertIsNotNone(panel._debug_modal)
            panel._debug_modal.close()
            self.assertIsNone(panel._debug_modal)
            self.assertIsNotNone(panel._settings_modal)
            panel._settings_modal.close()
            self.assertIsNone(panel._settings_modal)
            panel._closing = True
            for aid in list(panel._after_ids):
                try:
                    panel_root.after_cancel(aid)
                except Exception:
                    pass
            panel_root.destroy()

            root.update_idletasks()
            for w in (tb, ib, sp, btn, tgl, bar):
                self.assertTrue(w.winfo_exists())
        finally:
            neu_ui.apply_theme(orig_theme)
            root.destroy()

    def test_frontend_polish_tokens_diff_skeleton_toast_and_errors(self):
        import tkinter as tk
        import types
        import my_devices

        # 1) 色彩对比度 (WCAG AA >= 4.5:1) 与最小字阶 (>= 9)
        def rel_lum(hx: str) -> float:
            r, g, b = [int(hx.lstrip("#")[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
            lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

        bg_l = rel_lum(neu_ui.BASE)
        for tok in (neu_ui.TEXT_DIM, neu_ui.TEXT_MUTED, neu_ui.RED_DANGER, neu_ui.SUCCESS, neu_ui.INFO):
            tl = rel_lum(tok)
            ratio = (max(bg_l, tl) + 0.05) / (min(bg_l, tl) + 0.05)
            self.assertGreaterEqual(ratio, 4.49, f"token {tok} contrast={ratio:.2f}")
        self.assertGreaterEqual(neu_ui.FONT_CAPTION, 9)

        orig_theme = neu_ui.get_theme_mode()
        root = tk.Tk()
        root.withdraw()
        try:
            # 2) 按钮加载态与全局异常回调拦截
            btn = neu_ui.NeuButton(root, text="立即刷新")
            btn.set_loading(True, "读取中")
            self.assertTrue(btn._loading)
            self.assertEqual(btn._state, "disabled")
            btn.set_loading(False, "立即刷新")
            self.assertFalse(btn._loading)
            self.assertEqual(btn._state, "normal")

            captured_err = []
            neu_ui.set_command_error_handler(lambda src, exc: captured_err.append((src, str(exc))))
            try:
                neu_ui._safe_invoke(lambda: (_ for _ in ()).throw(RuntimeError("boom")), "test_btn")
            finally:
                neu_ui.set_command_error_handler(None)
            self.assertEqual(captured_err, [("test_btn", "boom")])

            # 3) 多设备 Toast + 悬停暂停 + 超过 4 台的 Tooltip 提示
            toast = tray_service.LowBatteryToastPopup(root, on_open_main=lambda: None)
            alerts = [
                devices.PeripheralReading(key="m1", role="mouse", name="Mouse1", product="M1", level=15),
                devices.PeripheralReading(key="k1", role="keyboard", name="Key2", product="K2", level=18),
            ]
            toast.show(alerts, auto_close_ms=5000, animate=False)
            self.assertIsNotNone(toast._timer_id)
            toast._on_enter()
            self.assertIsNone(toast._timer_id)
            toast.dismiss()

            tip = tray_service.TrayHoverTooltip(
                root,
                get_icon_rect=lambda: (1000, 1000, 1032, 1040),
                on_open_main=lambda: None,
            )
            five = [
                devices.PeripheralReading(key=f"d{i}", role="mouse", name=f"Dev{i}", product=f"D{i}", level=70 + i)
                for i in range(5)
            ]
            tip.update_data(five)
            tip._ensure_window()
            tip._populate()
            self.assertIn("1 台更多", tip._lbl_more.cget("text"))
            tip.hide()

            # 4) 骨架屏 -> 差量原地刷新 -> 调试弹窗拦滚轮 & 触控板累积 -> 错误重试入口
            panel_root = tk.Toplevel(root)
            panel_root.withdraw()
            panel = my_devices.BatteryPanelApp(panel_root, disable_tray_host=True)
            skels = [w for w in panel.cards_frame.winfo_children() if isinstance(w, neu_ui.NeuSkeletonCard)]
            self.assertGreaterEqual(len(skels), 2)

            r1 = devices.PeripheralReading(key="k_a", role="mouse", name="MouseA", product="A", level=80)
            r2 = devices.PeripheralReading(key="k_b", role="keyboard", name="KeyB", product="B", level=60)
            panel.apply_readings_snapshot([r1, r2], history_data={"version": 1, "devices": {}})
            first_card_obj = panel._card_widgets[0]

            r1_new = devices.PeripheralReading(key="k_a", role="mouse", name="MouseA", product="A", level=72)
            panel.apply_readings_snapshot([r1_new, r2], history_data={"version": 1, "devices": {}})
            self.assertIs(panel._card_widgets[0], first_card_obj)  # 差量更新未重建对象
            self.assertEqual(panel._card_widgets[0]._lbl_num.cget("text"), "72%")

            # 触控板细粒度 delta 累加
            scroll_calls = []
            panel.canvas.yview_scroll = lambda n, what: scroll_calls.append(n)  # type: ignore[method-assign]
            panel._on_mousewheel(types.SimpleNamespace(delta=20))  # -0.5 < 1 -> 累积中
            self.assertEqual(scroll_calls, [])
            panel._on_mousewheel(types.SimpleNamespace(delta=30))  # -1.25 -> -1
            self.assertEqual(scroll_calls, [-1])

            # 调试弹窗打开时屏蔽底层滚轮穿透
            panel.open_debug_tool(None)
            scroll_calls.clear()
            panel._on_mousewheel(types.SimpleNamespace(delta=-240))
            self.assertEqual(scroll_calls, [])
            panel._debug_modal.close()

            # 扫码错误展示友好中文 + 红色文案 + 内联重试按钮
            panel.ui_queue.put(("error", "Access is denied"))
            panel._pump_queues()
            self.assertIn("读取失败", panel.lbl_summary.cget("text"))
            self.assertEqual(panel.lbl_summary.cget("fg"), neu_ui.RED_DANGER)
            self.assertEqual(panel._btn_retry.winfo_manager(), "pack")

            panel._closing = True
            for aid in list(panel._after_ids):
                try:
                    panel_root.after_cancel(aid)
                except Exception:
                    pass
            panel_root.destroy()
        finally:
            neu_ui.apply_theme(orig_theme)
            neu_ui.set_command_error_handler(None)
            root.destroy()

    def test_ui_optimizations_all_components(self):
        import tkinter as tk
        import app as debug_app
        import my_devices

        orig_theme = neu_ui.get_theme_mode()
        root = tk.Tk()
        root.withdraw()
        try:
            # 1) NeuStatusPill、NeuSegmentedSwitch、NeuButton 加载锁宽与 NeuToggle 焦点属性
            pill = neu_ui.NeuStatusPill(root, text="无线已连接", bg_color="#dde8ec", fg_color=neu_ui.INFO)
            self.assertEqual(pill.cget("text"), "无线已连接")
            w_before_pill = pill._pill_w
            pill.set_pill("充电中 · 已满电等待同步", bg_color="#efe2d4", fg_color=neu_ui.AMBER_CHARGE)
            self.assertEqual(pill.cget("text"), "充电中 · 已满电等待同步")
            self.assertGreater(pill._pill_w, w_before_pill)

            switched = []
            seg = neu_ui.NeuSegmentedSwitch(
                root, [(24, "24h"), (168, "7天")], initial_value=24, on_change=lambda v: switched.append(v)
            )
            seg._on_key_toggle()
            self.assertEqual(switched, [168])
            self.assertEqual(seg._value, 168)

            lbtn = neu_ui.NeuButton(root, text="确认保存", accent=True)
            root.update_idletasks()
            base_w = lbtn._w
            lbtn.set_loading(True, "加载中…")
            self.assertGreaterEqual(lbtn._w, base_w)
            self.assertIsNotNone(lbtn._loading_lock_w)
            lbtn.set_loading(False)
            self.assertIsNone(lbtn._loading_lock_w)

            tvar = tk.BooleanVar(value=True)
            tgl = neu_ui.NeuToggle(root, text="开关", variable=tvar)
            self.assertEqual(int(str(tgl._cv.cget("takefocus"))), 1)

            # 2) NeuModalOverlay Escape 精准解绑（不误删主窗口既有绑定）+ 统一电量语义色
            keep_id = root.bind("<Escape>", lambda _e: None, add="+")
            before_bind = str(root.bind("<Escape>"))
            self.assertIn(keep_id, before_bind)
            m = neu_ui.NeuModalOverlay(root, title="测试浮层", min_height_dp=240, animate=False)
            modal_esc_id = m._esc_bind_id
            self.assertIsNotNone(modal_esc_id)
            self.assertIn(modal_esc_id, str(root.bind("<Escape>")))
            m.close()
            root.update_idletasks()
            self.assertIsNone(m._esc_bind_id)
            after_bind = str(root.bind("<Escape>"))
            self.assertIn(keep_id, after_bind)
            self.assertNotIn(modal_esc_id, after_bind)
            self.assertEqual(after_bind.strip(), before_bind.strip())
            root.unbind("<Escape>", keep_id)

            self.assertEqual(neu_ui.battery_accent_color(18, threshold=25), neu_ui.RED_DANGER)
            self.assertEqual(neu_ui.battery_accent_color(18, threshold=15), neu_ui.TEXT)
            self.assertEqual(neu_ui.battery_accent_color(18, charging=True), neu_ui.AMBER_CHARGE)
            self.assertEqual(neu_ui.battery_accent_color(None, offline=True), neu_ui.TEXT_DISABLED)

            # 3) 托盘四宫格低电优先排序 + 隐藏空槽 + 点击胶囊定位回调
            r_ok = devices.PeripheralReading(key="ok", role="mouse", name="NormalMouse", product="M", level=82)
            r_off = devices.PeripheralReading(key="off", role="keyboard", name="OffKey", product="K", offline=True, level=None)
            r_low = devices.PeripheralReading(key="low", role="headset", name="LowHeadset", product="H", level=14)
            ordered = tray_service._order_for_quad_grid([r_ok, r_off, r_low], threshold=20)
            self.assertEqual([x.key if x else None for x in ordered], ["low", "ok", "off", None])

            focused = []
            tip = tray_service.TrayHoverTooltip(
                root,
                get_icon_rect=lambda: (1000, 1000, 1032, 1040),
                on_open_main=lambda: focused.append("MAIN"),
                on_open_device=lambda k: focused.append(k),
            )
            tip.update_data([r_ok, r_low], threshold=25)
            tip._ensure_window()
            tip._populate()
            root.update_idletasks()
            self.assertEqual(tip._cell_slots[0]["key"], "low")
            self.assertEqual(tip._cell_slots[1]["key"], "ok")
            self.assertEqual(tip._cell_slots[2]["canvas"].winfo_manager(), "")
            tip._handle_click(tip._cell_slots[0]["key"])
            self.assertEqual(focused, ["low"])
            tip.hide()

            # 4) 主面板单卡居中、空状态双 CTA、离线灰底同步与日志弹窗
            panel_root = tk.Toplevel(root)
            panel_root.withdraw()
            panel = my_devices.BatteryPanelApp(panel_root, disable_tray_host=True)
            panel.cfg["notify_threshold"] = 25
            panel.apply_readings_snapshot([], history_data={"version": 1, "devices": {}})
            root.update_idletasks()
            self.assertEqual(len(panel._card_widgets), 0)

            def _iter_descendants(parent_w):
                for ch in parent_w.winfo_children():
                    yield ch
                    yield from _iter_descendants(ch)

            empty_btns = [
                w for w in _iter_descendants(panel.cards_frame)
                if isinstance(w, neu_ui.NeuButton) and ("立即重新扫描" in w._text or "打开调试与标定" in w._text)
            ]
            self.assertEqual(len(empty_btns), 2)

            panel.apply_readings_snapshot([r_low], history_data={"version": 1, "devices": {}})
            root.update_idletasks()
            self.assertEqual(len(panel._card_widgets), 1)
            self.assertEqual(panel._card_widgets[0]._lbl_num.cget("fg"), neu_ui.RED_DANGER)

            panel.apply_readings_snapshot([r_off], history_data={"version": 1, "devices": {}})
            root.update_idletasks()
            self.assertEqual(panel._card_widgets[0].content.cget("bg"), neu_ui.MUTED_CARD_BG)
            self.assertEqual(panel._card_widgets[0]._lbl_num.cget("bg"), neu_ui.MUTED_CARD_BG)
            self.assertEqual(panel._card_widgets[0]._pill.cget("text"), "已离线")

            panel.ui_queue.put(("error", "Access is denied"))
            panel._pump_queues()
            panel.open_error_log_modal()
            self.assertIsNotNone(panel._log_modal)
            panel._log_modal.close()
            self.assertIsNone(panel._log_modal)

            panel._closing = True
            for aid in list(panel._after_ids):
                try:
                    panel_root.after_cancel(aid)
                except Exception:
                    pass
            panel_root.destroy()

            # 5) 调试工作区颜色统一 + 后台异常传播 + 标定校验 + DebugApp 独立窗口无边框初始化
            ws_root = tk.Toplevel(root)
            ws_root.withdraw()
            ws = debug_app.DebugWorkspaceFrame(ws_root, compact=True, show_header=False)
            ws.pack(fill="both", expand=True)
            self.assertEqual(debug_app._battery_fg(15, threshold=20), neu_ui.RED_DANGER)
            self.assertEqual(debug_app._battery_fg(60, charging=True), neu_ui.AMBER_CHARGE)
            ws._set_scan_loading(True)
            ws.ui_queue.put(("scan_error", "模拟扫描失败"))
            ws._pump()
            self.assertFalse(ws._scanning)
            self.assertFalse(ws._btn_scan_all._loading)

            dummy_iface = HidInterface(
                r"\\.\hid#test", 0x1234, 0x5678, 1, 0xFF00, 0x0001, 32, 32, 32,
                "TestDev", "TestMfg", "SN1", 0, 0, 0
            )
            iid = ws.tree.insert("", "end", text="TestDev")
            ws.by_iid[iid] = dummy_iface
            ws.tree.selection_set(iid)
            ws.calib_vars["rid"].set("ZZ")
            dialogs = []
            orig_dlg = neu_ui.ask_message_dialog
            neu_ui.ask_message_dialog = lambda parent, title, msg, **kw: dialogs.append((title, kw.get("kind")))  # type: ignore[assignment]
            try:
                ws.cmd_save_calib()
            finally:
                neu_ui.ask_message_dialog = orig_dlg  # type: ignore[assignment]
            self.assertFalse(ws.saved_protocol)
            self.assertEqual(dialogs, [("参数校验失败", "error")])
            ws.cleanup()
            ws_root.destroy()

            dbg_root = tk.Toplevel(root)
            dbg_root.withdraw()
            dbg = debug_app.DebugApp(dbg_root)
            dbg_root.update_idletasks()
            self.assertTrue(dbg.workspace.winfo_exists())
            dbg.workspace.cleanup()
            dbg_root.destroy()

            # 6) 悬停模块四周均匀发光 vs 静默态不亮 + 无边框窗口 Win11 圆角外框
            glow_img = neu_ui.make_surface_photo(
                160, 100, radius=24, off=3, box_r=4, pad=14,
                inset=False, shadow_strength=0.14, light_strength=1.0, glow_strength=1.0,
            )
            idle_img = neu_ui.make_surface_photo(
                160, 100, radius=24, off=6, box_r=4, pad=14,
                inset=False, shadow_strength=0.72, light_strength=0.16, glow_strength=0.0,
            )
            # 悬停图上下左右外缘均应明显高于基底亮度，且底/右侧明显亮于静默态
            self.assertGreater(glow_img.get(94, 11)[0], neu_ui.BASE_RGB[0] + 10)   # 上缘
            self.assertGreater(glow_img.get(94, 116)[0], neu_ui.BASE_RGB[0] + 10)  # 下缘
            self.assertGreater(glow_img.get(11, 64)[0], neu_ui.BASE_RGB[0] + 10)   # 左缘
            self.assertGreater(glow_img.get(176, 64)[0], neu_ui.BASE_RGB[0] + 10)  # 右缘
            self.assertGreater(glow_img.get(94, 116)[0], idle_img.get(94, 116)[0] + 20)
            self.assertLessEqual(idle_img.get(94, 11)[0], neu_ui.BASE_RGB[0] + 6)

            hover_root = tk.Toplevel(root)
            hover_root.withdraw()
            hp = my_devices.BatteryPanelApp(hover_root, disable_tray_host=True)
            hp.apply_readings_snapshot([r_ok, r_low], history_data={"version": 1, "devices": {}})
            root.update_idletasks()
            self.assertEqual(len(hp._card_widgets), 2)
            c0, c1 = hp._card_widgets[0], hp._card_widgets[1]
            self.assertTrue(c0._hoverable and c1._hoverable)
            c0._on_enter()
            self.assertTrue(c0._hovered)
            c1._on_enter()
            self.assertFalse(c0._hovered)
            self.assertTrue(c1._hovered)

            # Win11 DWM 圆角属性生效检查
            import ctypes
            from ctypes import wintypes

            self.assertTrue(neu_ui.apply_window_rounded_corners(hover_root, rounded=True))
            hwnd = neu_ui._root_hwnd(hover_root)
            self.assertTrue(hwnd)
            if int(sys.getwindowsversion().build) >= 22000:
                val = ctypes.c_int(0)
                ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(
                    wintypes.HWND(hwnd), 33, ctypes.byref(val), ctypes.sizeof(val)
                )
                self.assertEqual(val.value, 2)
                neu_ui.apply_window_rounded_corners(hover_root, rounded=False)
                ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(
                    wintypes.HWND(hwnd), 33, ctypes.byref(val), ctypes.sizeof(val)
                )
                self.assertEqual(val.value, 1)

            hp._closing = True
            for aid in list(hp._after_ids):
                try:
                    hover_root.after_cancel(aid)
                except Exception:
                    pass
            hover_root.destroy()

            # 7) 开关/胶囊/分段条/进度条 4x4 超采样抗锯齿 + 模态浮层延后同步显形过渡（浅色基准校验）
            neu_ui.apply_theme("light")
            aa_tgl_var = tk.BooleanVar(value=True)
            aa_tgl = neu_ui.NeuToggle(root, text="抗锯齿", variable=aa_tgl_var)
            aa_pill = neu_ui.NeuStatusPill(root, text="已连接", bg_color="#dde8ec", fg_color=neu_ui.INFO)
            aa_seg = neu_ui.NeuSegmentedSwitch(root, [(24, "24h"), (168, "7天")], initial_value=24)
            aa_bar = neu_ui.NeuProgressBar(root)
            aa_bar.set_value(75, neu_ui.BAR_GREEN)
            root.update_idletasks()
            self.assertEqual([aa_tgl._cv.type(i) for i in aa_tgl._cv.find_all()], ["image"])
            self.assertEqual([aa_pill.type(i) for i in aa_pill.find_all()].count("oval"), 0)
            self.assertEqual([aa_seg.type(i) for i in aa_seg.find_all()].count("oval"), 0)
            self.assertEqual([aa_bar.type(i) for i in aa_bar.find_all()].count("oval"), 0)
            # 验证旋钮边缘存在连续抗锯齿中间像素（非纯 0/1 二值阶梯）
            timg = aa_tgl._frame_photo
            mid_blues = set()
            for py in range(timg.height()):
                for px in range(timg.width()):
                    r_v, g_v, b_v = timg.get(px, py)
                    if 130 < r_v < 200 and 120 < g_v < 200 and b_v > 235:
                        mid_blues.add((r_v, g_v, b_v))
            self.assertGreaterEqual(len(mid_blues), 4)

            anim_modal = neu_ui.NeuModalOverlay(root, title="动态过渡", min_height_dp=240, animate=True)
            self.assertFalse(anim_modal._presented)
            root.update()
            self.assertTrue(anim_modal._presented)
            anim_modal.close()

            # 8) 模态背景浅雾磨砂化（绝非黑底）+ 真实窗口截图多阶生成无异常
            self.assertGreaterEqual(min(neu_ui.hex_to_rgb(neu_ui.OVERLAY_BACKDROP_BG)), 180)
            cap_win = tk.Toplevel(root)
            cap_win.overrideredirect(True)
            cap_win.configure(bg=neu_ui.BASE)
            cap_win.attributes("-alpha", 0.0)
            cap_win.geometry("180x140+80+80")
            cap_win.update()
            stages = neu_ui._capture_dimmed_window_stages(cap_win)
            self.assertEqual(len(stages), 3)
            self.assertGreaterEqual(min(stages[-1].get(20, 20)), 165)
            cap_win.destroy()
        finally:
            neu_ui.apply_theme(orig_theme)
            neu_ui.set_command_error_handler(None)
            root.destroy()

    def test_frontend_79_items_comprehensive_behaviors(self):
        """覆盖双主题对比度、字号联动、矢量状态胶囊、焦点环/模态抓焦、3/4列响应、托盘分页与崩溃恢复等关键优化项。"""
        import queue
        import tkinter as tk
        import app as debug_app
        import my_devices
        import tray_service
        import win_icon

        def rel_lum(hx: str) -> float:
            r, g, b = neu_ui.hex_to_rgb(hx)
            def _lin(c: float) -> float:
                v = c / 255.0
                return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
            return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)

        def contrast_ratio(c1: str, c2: str) -> float:
            l1, l2 = rel_lum(c1), rel_lum(c2)
            hi, lo = max(l1, l2), min(l1, l2)
            return (hi + 0.05) / (lo + 0.05)

        orig_theme = neu_ui.get_theme_mode()
        orig_scale = neu_ui.get_font_scale_mode()
        root = tk.Tk()
        root.withdraw()
        try:
            # 1) 双主题 tokens + 深色模式 WCAG >= 4.5 对比度 + 隐藏胶囊非红 + 离线描边 + 语意拆分
            neu_ui.apply_theme("dark")
            self.assertTrue(neu_ui.is_dark_theme())
            for tok in (neu_ui.TEXT, neu_ui.TEXT_MUTED, neu_ui.TEXT_DIM, neu_ui.BLURPLE_LINK, neu_ui.SUCCESS):
                self.assertGreaterEqual(contrast_ratio(neu_ui.BASE, tok), 4.5, f"dark contrast fail: {tok}")
            hid_bg, hid_fg, _ = neu_ui.get_status_pill_style("hidden")
            self.assertNotEqual(hid_fg.lower(), neu_ui.RED_DANGER.lower())
            off_bg, off_fg, off_bd = neu_ui.get_status_pill_style("offline")
            self.assertIsNotNone(off_bd)
            self.assertNotEqual(off_bg.lower(), neu_ui.MUTED_CARD_BG.lower())

            neu_ui.apply_theme("light")
            self.assertFalse(neu_ui.is_dark_theme())
            self.assertEqual(neu_ui.BAR_RED.lower(), "#c07a80")

            # 2) 字号档位与纯数字 Display / 中文 UI 族分离 + 折线联动高度
            neu_ui.set_font_scale_mode("standard")
            h_std = neu_ui.NeuSparkline(root)._canvas.winfo_reqheight()
            neu_ui.set_font_scale_mode("large")
            self.assertEqual(neu_ui.scaled_font_size(neu_ui.FONT_CAPTION), neu_ui.FONT_CAPTION + 2)
            self.assertIn("YaHei", neu_ui.pick_font_family(root))
            h_lrg = neu_ui.NeuSparkline(root)._canvas.winfo_reqheight()
            self.assertGreater(h_lrg, h_std)
            neu_ui.set_font_scale_mode("standard")

            # 3) 矢量充电/对勾胶囊 + 最小宽对称 + 禁用按钮与焦点保留 + Select 无 emoji + 标题栏横向钳制
            pill_chg = neu_ui.NeuStatusPill(root, text="充电中", icon_kind="charge", min_width_dp=64)
            self.assertGreaterEqual(pill_chg._pill_w, neu_ui.sx(64, root))
            self.assertEqual(len(pill_chg.find_all()), 3)  # image + polygon + text

            btn = neu_ui.NeuButton(root, text="操作按钮")
            btn.set_disabled(True)
            self.assertEqual(btn._state, "disabled")
            btn.set_disabled(False)
            self.assertEqual(btn._state, "normal")

            tb = neu_ui.NeuTitleBar(root, title="拖拽钳制", subtitle="副标题")
            root.geometry("800x600+100+100")
            root.update_idletasks()
            tb._drag_origin = (0, 0, -5000, 100)
            tb._on_drag(type("Ev", (), {"x_root": 0, "y_root": 100})())
            root.update_idletasks()
            self.assertGreater(root.winfo_x(), -800)

            # 4) Modal 抓焦闭环 + 关闭清理子下拉 + 重绘尺寸变更抛弃旧截图
            modal = neu_ui.NeuModalOverlay(root, title="抓焦测试", min_height_dp=240, animate=False)
            sv = tk.StringVar(value="A")
            sel = neu_ui.NeuSelect(modal.body, variable=sv, values=["A", "B"])
            sel.pack()
            sel._toggle_popup()
            self.assertIsNotNone(sel._popup)
            modal._captured_size = (800, 600)
            modal._relayout()
            self.assertIn("panel_bg", modal.find_withtag("panel_bg") and ["panel_bg"] or ["panel_bg"])
            modal.close()
            root.update_idletasks()
            self.assertIsNone(sel._popup)

            # 5) 16px 托盘图标无微字噪点 + 托盘单击打开/双击最近设备 + 整台不截断 + 超过4台分页 + 悬停冻结倒计时
            rgba16 = win_icon.render_rgba("50", 16, (127, 175, 199))
            self.assertEqual(len(rgba16), 16 * 16 * 4)

            q: "queue.SimpleQueue[tuple[str, object]]" = queue.SimpleQueue()
            host = tray_service.TrayHostThread(q)
            r_list = [
                devices.PeripheralReading(key=f"d{i}", role="mouse", name=f"VeryLongPeripheralModelName-{i}", product="P", level=50 + i)
                for i in range(6)
            ]
            tip_str = host._build_tip_text(r_list, 20)
            self.assertIn("等 ", tip_str)
            self.assertRegex(tip_str, r"等 \d+ 台$")
            host._wndproc(0, tray_service.WM_TRAY, 0, tray_service.WM_LBUTTONUP)
            self.assertEqual(q.get_nowait()[0], "open_main")
            host._wndproc(0, tray_service.WM_TRAY, 0, tray_service.WM_LBUTTONDBLCLK)
            self.assertEqual(q.get_nowait()[0], "open_last_device")

            tip = tray_service.TrayHoverTooltip(root, lambda: (1600, 1000, 1632, 1040), on_open_main=lambda: None)
            tip.update_data(r_list, threshold=20)
            tip._ensure_window()
            tip._populate()
            self.assertEqual(tip._total_pages, 2)
            self.assertIn("第 1/2 页", tip._lbl_more.cget("text"))
            tip._step_page(1)
            self.assertIn("第 2/2 页", tip._lbl_more.cget("text"))
            tip.hide()

            toast = tray_service.LowBatteryToastPopup(root, on_open_main=lambda: None)
            toast.show(r_list[0], auto_close_ms=8000, animate=False)
            toast._on_enter()
            self.assertTrue(toast._hovered)
            self.assertIsNone(toast._timer_id)
            toast.dismiss()

            # 6) 主面板：3/4 列断点、卡片挂起同步/报错过期态、设置独立信息区、重构界面、调试工具窄列折叠
            pr = tk.Toplevel(root)
            pr.withdraw()
            app = my_devices.BatteryPanelApp(pr, disable_tray_host=True)
            app._on_canvas_configure(type("E", (), {"width": neu_ui.sx(1200, pr)})())
            self.assertEqual(app._grid_cols, 3)
            app._on_canvas_configure(type("E", (), {"width": neu_ui.sx(1600, pr)})())
            self.assertEqual(app._grid_cols, 4)

            app.apply_readings_snapshot(r_list[:2], history_data={"version": 1, "devices": {}})
            pr.update_idletasks()
            c0 = app._card_widgets[0]
            c0.set_pending_sync(True, "重命名后鼠标")
            self.assertIn("待刷新同步", c0._pill.cget("text"))
            c0.set_stale_error(True, "12:00")
            self.assertIn("旧值", c0._pill.cget("text"))

            app.open_settings_modal()
            pr.update_idletasks()
            self.assertIn("开机自启状态", app._lbl_modal_info.cget("text"))
            app._set_modal_note("测试反馈", neu_ui.INFO)
            self.assertIn("开机自启状态", app._lbl_modal_info.cget("text"))
            self.assertEqual(app._lbl_modal_msg.cget("text"), "测试反馈")
            app._rebuild_ui(reopen_settings=False)
            pr.update_idletasks()
            self.assertEqual(len(app._card_widgets), 2)

            ws = debug_app.DebugWorkspaceFrame(pr, compact=True, show_header=False)
            self.assertEqual(ws._btn_read_sel._state, "disabled")
            ws._on_tree_configure(type("E", (), {"width": neu_ui.sx(200, pr)})())
            self.assertEqual(tuple(ws.tree.cget("displaycolumns")), ("level",))
            ws.cleanup()

            app._closing = True
            for aid in list(app._after_ids):
                try:
                    pr.after_cancel(aid)
                except Exception:
                    pass
            app._stop_scan_progress_timer()
            pr.destroy()
        finally:
            neu_ui.apply_theme(orig_theme)
            neu_ui.set_font_scale_mode(orig_scale)
            neu_ui.set_command_error_handler(None)
            root.destroy()


class TestFeatureGaps22Items(unittest.TestCase):
    """覆盖 功能缺口.txt 全部 22 项新增功能、UI 联动、后台策略、原生通知与诊断能力。"""

    def test_config_validation_and_rotating_4level_logger(self):
        import tray_common

        with tempfile.TemporaryDirectory() as td:
            cfg_p = os.path.join(td, "tray_config.json")
            log_p = os.path.join(td, "app_error.log")
            old_cfg = tray_common.CONFIG_FILE
            old_log = tray_common.LOG_FILE
            old_lvl = tray_common.get_log_level()
            try:
                tray_common.CONFIG_FILE = cfg_p
                tray_common.LOG_FILE = log_p
                with open(cfg_p, "w", encoding="utf-8") as f:
                    json.dump(
                        {
                            "notify_threshold": 999,
                            "notify_cooldown_min": -2,
                            "bg_interval": 1,
                            "sort_mode": "bad_mode",
                            "tray_icon_style": "unknown",
                            "tray_visibility": "bogus",
                            "log_level": "WARNING",
                            "manual_devices": ["046d:c547", "VID_3554&PID_F510", "invalid"],
                            "device_roles": {"k1": "gamepad", "k2": "invalid_role"},
                        },
                        f,
                    )
                loaded = tray_common.load_config()
                self.assertEqual(loaded["notify_threshold"], 50)
                self.assertEqual(loaded["notify_cooldown_min"], 1)
                self.assertEqual(loaded["bg_interval"], 30)
                self.assertEqual(loaded["sort_mode"], "default")
                self.assertEqual(loaded["tray_icon_style"], "digit")
                self.assertEqual(loaded["tray_visibility"], "always")
                self.assertEqual(loaded["log_level"], "WARN")
                self.assertEqual(loaded["manual_devices"], ["046D:C547", "3554:F510"])
                self.assertEqual(loaded["device_roles"], {"k1": "controller"})

                # 验证 4 级日志过滤与 max_bytes × 3 轮转 (#45)
                tray_common.set_log_level("WARN")
                self.assertFalse(tray_common.log_event("DEBUG", "t", "hidden debug", log_file=log_p))
                self.assertFalse(tray_common.log_event("INFO", "t", "hidden info", log_file=log_p))
                self.assertTrue(tray_common.log_event("WARN", "t", "visible warn", log_file=log_p, max_bytes=120, backup_count=3))
                for idx in range(10):
                    tray_common.log_event("ERROR", "t", f"err-entry-{idx:02d}-padding-payload", log_file=log_p, max_bytes=120, backup_count=3)
                self.assertTrue(os.path.isfile(log_p))
                self.assertTrue(os.path.isfile(f"{log_p}.1"))
                self.assertTrue(os.path.isfile(f"{log_p}.2"))
                self.assertTrue(os.path.isfile(f"{log_p}.3"))
                self.assertFalse(os.path.exists(f"{log_p}.4"))

                tray_common.set_log_level("DEBUG")
                tray_common.log_event("INFO", "src", "hello info", log_file=log_p, max_bytes=4096)
                only_err = tray_common.read_log_entries("ERROR", log_file=log_p)
                self.assertTrue(all("[ERROR]" in ln for ln in only_err))
                tray_common.clear_log_files(log_file=log_p, backup_count=3)
                self.assertFalse(os.path.exists(log_p))
            finally:
                tray_common.CONFIG_FILE = old_cfg
                tray_common.LOG_FILE = old_log
                tray_common.set_log_level(old_lvl)

    def test_low_battery_monitor_tiers_cooldown_and_snooze(self):
        import tray_service

        mon = tray_service.LowBatteryMonitor()
        cfg = {"notify_enabled": True, "notify_threshold": 20, "notify_cooldown_min": 60}
        now = 100000.0
        r_m = devices.PeripheralReading(key="k_mouse", role="mouse", name="Mouse", product="M", level=18)

        # #3/#1: 18% < 20% 触发 normal；10 分钟后仍处于 60 分钟冷却内不重复，60 分钟后再次触发
        res1 = mon.check_with_tiers([r_m], cfg, now=now)
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0][1], "normal")
        self.assertEqual(mon.check_with_tiers([r_m], cfg, now=now + 600), [])
        self.assertEqual(len(mon.check_with_tiers([r_m], cfg, now=now + 3600)), 1)

        # #3: 电量跨越至 <=10% (urgent) 时立即突破冷却升级提醒
        r_m.level = 10
        res_urg = mon.check_with_tiers([r_m], cfg, now=now + 3660)
        self.assertEqual([(r.key, t) for r, t in res_urg], [("k_mouse", "urgent")])

        # #3: 掉落至 <=5% (critical) 时不仅立即突破，且后续轮询周期持续触发提醒
        r_m.level = 4
        res_crit1 = mon.check_with_tiers([r_m], cfg, now=now + 3700)
        res_crit2 = mon.check_with_tiers([r_m], cfg, now=now + 3820)
        self.assertEqual(res_crit1[0][1], "critical")
        self.assertEqual(res_crit2[0][1], "critical")

        # #9: snooze 暂停该设备或全局提醒 30 分钟，过期后自动恢复
        mon.snooze("k_mouse", minutes=30, now=now + 3900)
        self.assertEqual(mon.check_with_tiers([r_m], cfg, now=now + 4200), [])
        self.assertEqual(len(mon.check_with_tiers([r_m], cfg, now=now + 5800)), 1)

    def test_voltage_outlier_filter_charge_and_discharge_eta(self):
        # #46 / #47 / #48
        t0 = 1700000000.0
        samples_with_glitch = [
            {"t": t0, "lvl": 80, "chg": False, "off": False, "mv": 4020},
            {"t": t0 + 1800, "lvl": 70, "chg": False, "off": False, "mv": 3950},
            # 电压瞬间毛刺跳降至 2600mV 且百分比乱跳至 15%，应被电压校验剔除
            {"t": t0 + 2700, "lvl": 15, "chg": False, "off": False, "mv": 2600},
            {"t": t0 + 3600, "lvl": 60, "chg": False, "off": False, "mv": 3880},
        ]
        cleaned = battery_history.filter_voltage_outliers(samples_with_glitch)
        self.assertEqual(len(cleaned), 3)
        est_h, rate_h, dep_ts = battery_history.estimate_discharge_details(
            samples_with_glitch, current_level=60, now=t0 + 3600
        )
        self.assertIsNotNone(est_h)
        self.assertAlmostEqual(rate_h, 20.0, places=1)
        self.assertIsNotNone(dep_ts)
        txt = battery_history.format_remaining_text(
            est_h, discharge_rate_per_h=rate_h, deplete_ts=dep_ts
        )
        self.assertIn("%/h", txt)
        self.assertIn("耗尽", txt)

        # #47 充电斜率与预计充满时间估算
        chg_samples = [
            {"t": t0, "lvl": 40, "chg": True, "off": False},
            {"t": t0 + 1800, "lvl": 70, "chg": True, "off": False},
        ]
        chg_mins, chg_rate, _ = battery_history.estimate_charge_details(
            chg_samples, current_level=70, charging=True, now=t0 + 1800
        )
        self.assertIsNotNone(chg_mins)
        self.assertAlmostEqual(chg_mins, 30.0, places=1)
        self.assertIn("充满", battery_history.format_charge_eta_text(chg_mins))
        self.assertIn("估算中", battery_history.format_charge_eta_text(None))

    def test_scan_manual_ignored_30d_prune_role_skipped_and_diag_wizard(self):
        from hid_win import HidInterface

        with tempfile.TemporaryDirectory() as td:
            cfg_p = os.path.join(td, "tray_config.json")
            log_p = os.path.join(td, "app_error.log")
            hist_p = os.path.join(td, "battery_history.json")
            proto_p = os.path.join(td, "protocols_user.json")
            old_paths = (
                tray_common.CONFIG_FILE,
                tray_common.LOG_FILE,
                battery_history.HISTORY_FILE,
                battery.USER_PROTO_FILE,
            )
            orig_enum = devices.enumerate_hid
            orig_bt = devices.bt_battery.find_battery_devices
            orig_read = devices.battery.read_device_battery
            orig_bat_enum = devices.battery.enumerate_hid
            now = time.time()
            try:
                tray_common.CONFIG_FILE = cfg_p
                tray_common.LOG_FILE = log_p
                battery_history.HISTORY_FILE = hist_p
                battery.USER_PROTO_FILE = proto_p

                cfg = tray_common.load_config()
                cfg["manual_devices"] = ["9999:0001"]
                cfg["ignored_devices"] = ["hid:258A:01BB"]
                cfg["pinned"] = ["hid:3837:100C", "hid:OLD0:PIN1"]
                cfg["device_roles"] = {"hid:3837:100C": "headset"}  # #21 角色覆写
                cfg["known_devices"] = {
                    "hid:OLD0:DROP": {"name": "ExpiredOld", "had_battery": True, "last_seen": now - 35 * 86400},
                    "hid:OLD0:PIN1": {"name": "ExpiredPinned", "role": "keyboard", "had_battery": True, "last_seen": now - 35 * 86400},
                }
                tray_common.save_config(cfg)

                fake_ifaces = [
                    HidInterface(r"\\.\hid#m", 0x3837, 0x100C, 1, 0xFF01, 0x0001, 65, 65, 65, "MCHOSE A5", "MCHOSE", "", 0, 0, 0),
                    HidInterface(r"\\.\hid#k", 0x258A, 0x01BB, 2, 0xFF02, 0x0001, 20, 20, 20, "IgnoredKbd", "MCHOSE", "", 0, 0, 0),
                    HidInterface(r"\\.\hid#man", 0x9999, 0x0001, 3, 0x0001, 0x0002, 8, 0, 0, "CustomDongle", "Other", "", 0, 0, 0),
                    # 未匹配任何关键字/usage 的物理设备 -> 应计入 skipped_count (#42)
                    HidInterface(r"\\.\hid#skip", 0x1234, 0xABCD, 4, 0xFF99, 0x0001, 32, 32, 32, "MysteryPad", "Vendor", "", 0, 0, 0),
                    # 系统灯控 IGNORED_VIDS -> 不应计入 skipped_count 误报用户
                    HidInterface(r"\\.\hid#aura", 0x0B05, 0x19AF, 5, 0xFF72, 0x00A1, 64, 64, 64, "AURA LED", "ASUS", "", 0, 0, 0),
                ]
                devices.enumerate_hid = lambda: fake_ifaces  # type: ignore[assignment]
                devices.battery.enumerate_hid = lambda: fake_ifaces  # type: ignore[assignment]
                off_bt = devices.bt_battery.BluetoothDevice(
                    name="OfflineBT", instance_id="BT\\1", battery=None, reason="off"
                )
                devices.bt_battery.find_battery_devices = lambda: [off_bt]  # type: ignore[assignment]

                def _fake_read(ifaces, specs=None):
                    if ifaces[0].vid == 0x3837:
                        return battery.BatteryResult(level=75, voltage_mv=3820, connection="2.4G", source="MCHOSE")
                    return battery.BatteryResult(error="该接口暂未匹配电量协议")

                devices.battery.read_device_battery = _fake_read  # type: ignore[assignment]

                # 前台扫描验证 #19/#20/#21/#42/#44/#46
                readings, stats = devices.scan_peripherals_with_stats(cfg, save_cache=True)
                keys = [r.key for r in readings]
                self.assertIn("hid:3837:100C", keys)
                self.assertNotIn("hid:258A:01BB", keys)  # ignored_devices 过滤
                self.assertIn("hid:9999:0001", keys)     # manual_devices 强纳候选 (#20)
                self.assertNotIn("hid:OLD0:DROP", keys)  # >30天未置顶已清理 (#19)
                self.assertIn("hid:OLD0:PIN1", keys)     # >30天但置顶保留 (#19)

                m_r = next(r for r in readings if r.key == "hid:3837:100C")
                self.assertEqual(m_r.role, "headset")    # 角色覆写 (#21)
                self.assertEqual(m_r.voltage_mv, 3820)   # 电压透传 (#46)
                self.assertEqual(m_r.voltage_text, "3.82V")

                man_r = next(r for r in readings if r.key == "hid:9999:0001")
                self.assertTrue(man_r.manual)
                self.assertIn("可去标定", man_r.detail)

                # skipped_count 只包含 MysteryPad 而非 ASUS AURA (#42)
                self.assertEqual(stats.skipped_count, 1)
                self.assertEqual(stats.skipped_devices[0]["vidpid"], "1234:ABCD")
                self.assertGreaterEqual(stats.hid_iface_count, 5)

                # 后台模式验证 #13：跳过纯离线 BT 枚举
                _, bg_stats = devices.scan_peripherals_with_stats(cfg, save_cache=False, background=True)
                self.assertTrue(bg_stats.skipped_offline_enum)

                # #41 三步排障向导验证
                steps = devices.diagnose_device_steps(0x1234, 0xABCD)
                self.assertEqual(len(steps), 3)
                self.assertTrue(steps[0]["ok"])
                self.assertTrue(steps[1]["ok"])
                self.assertFalse(steps[2]["ok"])
                self.assertEqual(steps[2]["action_type"], "open_debug")
            finally:
                tray_common.CONFIG_FILE, tray_common.LOG_FILE, battery_history.HISTORY_FILE, battery.USER_PROTO_FILE = old_paths
                devices.enumerate_hid = orig_enum  # type: ignore[assignment]
                devices.bt_battery.find_battery_devices = orig_bt  # type: ignore[assignment]
                devices.battery.read_device_battery = orig_read  # type: ignore[assignment]
                devices.battery.enumerate_hid = orig_bat_enum  # type: ignore[assignment]

    def test_protocol_export_import_validation_and_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            proto_p = os.path.join(td, "protocols_user.json")
            exp_p = os.path.join(td, "exported.json")
            bad_p = os.path.join(td, "bad.json")
            sp1 = battery.ProtocolSpec(name="ProtoOne", vid=0x1111, pid=0x2222, usage_page=0xFF00, level_offset=4)
            battery.save_user_protocol(sp1, proto_file=proto_p)
            cnt = battery.export_user_protocols(exp_p, proto_file=proto_p)
            self.assertEqual(cnt, 1)

            # 冲突覆盖与跳过测试 (#43)
            with open(exp_p, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["protocols"][0]["name"] = "ProtoOneUpdated"
            data["protocols"].append({"name": "ProtoTwo", "vid": "0x3333", "pid": "0x4444", "usage_page": "0xFF01", "level_offset": 5})
            with open(exp_p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)

            res_skip = battery.import_user_protocols(exp_p, overwrite=False, proto_file=proto_p)
            self.assertEqual(res_skip["imported"], 1)
            self.assertEqual(res_skip["skipped"], 1)
            self.assertEqual(len(res_skip["conflicts"]), 1)

            res_over = battery.import_user_protocols(exp_p, overwrite=True, proto_file=proto_p)
            self.assertEqual(res_over["imported"], 2)
            loaded = battery.load_user_protocols(proto_p)
            self.assertEqual(next(p.name for p in loaded if p.vid == 0x1111), "ProtoOneUpdated")

            # 非法 hex / 偏移拦截校验
            with open(bad_p, "w", encoding="utf-8") as f:
                json.dump([{"name": "BadHex", "command_hex": "ZZZ"}], f)
            parsed_bad, errs = battery.parse_protocols_file(bad_p)
            self.assertEqual(parsed_bad, [])
            self.assertEqual(len(errs), 1)

    def test_tray_styles_visibility_device_change_debounce_and_ui_modes(self):
        import queue
        import tkinter as tk
        import my_devices
        import tray_service
        import win_icon

        # #37 4种托盘样式渲染 + Live Preview PPM
        for st in ("digit", "ring", "dot", "lowest_device"):
            rgba = win_icon.render_style_rgba(st, 24, 65, (127, 175, 199), alert=False, role="mouse")
            self.assertEqual(len(rgba), 24 * 24 * 4)
            ppm = win_icon.render_style_preview_ppm(st, 65, (127, 175, 199), size=28)
            self.assertTrue(ppm.startswith(b"P6\n28 28\n255\n"))

        # #10 DeviceChangeDebouncer 1.5s 延迟 + 3s 合并防抖
        fake_clock = [10.0]
        fired = []
        deb = tray_service.DeviceChangeDebouncer(
            delay_sec=1.5, debounce_sec=3.0, on_trigger=lambda ev: fired.append((fake_clock[0], ev)),
            time_fn=lambda: fake_clock[0],
        )
        w1 = deb.notify(tray_service.DBT_DEVICEARRIVAL, async_timer=False)
        self.assertAlmostEqual(w1, 1.5)
        fake_clock[0] += 1.6
        self.assertTrue(deb.flush_due())
        self.assertEqual(len(fired), 1)
        # 紧接着 0.5 秒后再发消息，应合并推迟到 >= 3.0s 窗口后
        fake_clock[0] += 0.5
        w2 = deb.notify(tray_service.DBT_DEVNODES_CHANGED, async_timer=False)
        self.assertGreaterEqual(w2, 2.4)
        self.assertFalse(deb.flush_due())

        # #5/#40 TrayHostThread alert_only 模式 & NIF_INFO 结构构造测试
        q: "queue.SimpleQueue[tuple[str, object]]" = queue.SimpleQueue()
        host = tray_service.TrayHostThread(q)
        host._visibility = "alert_only"
        self.assertEqual(host._build_tip_text([], 20)[:4], tray_common.UI_TITLE[:4])
        r_hi = devices.PeripheralReading(key="k1", role="mouse", name="High", product="M", level=80, voltage_mv=3900)
        r_lo = devices.PeripheralReading(key="k2", role="keyboard", name="Low", product="K", level=8, error="ReadTimeout", last_read_error="ReadTimeout")
        host.update_readings([r_hi], threshold=20, style="ring", visibility="alert_only")
        self.assertFalse(host._alert)
        host.update_readings([r_hi, r_lo], threshold=20, style="ring", visibility="alert_only")
        self.assertTrue(host._alert)

        # #18 置顶 + 4 种排序模式测试
        r_a = devices.PeripheralReading(key="a", role="keyboard", name="Beta", product="P", level=50, updated_at=100.0)
        r_b = devices.PeripheralReading(key="b", role="mouse", name="Alpha", product="P", level=15, updated_at=50.0)
        r_c = devices.PeripheralReading(key="c", role="controller", name="Gamma", product="P", level=90, updated_at=200.0)
        devices.sort_readings([r_a, r_b, r_c], "low_first", {"c"})
        # note sort_readings sorts in-place
        arr = [r_a, r_b, r_c]
        devices.sort_readings(arr, "low_first", {"c"})
        self.assertEqual([x.key for x in arr], ["c", "b", "a"])  # 置顶 c 第一，随后低电 b(15) < a(50)
        devices.sort_readings(arr, "name", set())
        self.assertEqual([x.key for x in arr], ["b", "a", "c"])  # Alpha, Beta, Gamma
        devices.sort_readings(arr, "recent", set())
        self.assertEqual([x.key for x in arr], ["c", "a", "b"])  # 200, 100, 50

        # UI 组件：#22 紧凑卡片视图、#46 电压展示、#13 后台调度间隔、#1/#3/#37 设置预览与 #41 三步向导
        with tempfile.TemporaryDirectory() as td:
            cfg_p = os.path.join(td, "tray_config.json")
            old_cfg = tray_common.CONFIG_FILE
            orig_theme = neu_ui.get_theme_mode()
            root = tk.Tk()
            root.withdraw()
            try:
                tray_common.CONFIG_FILE = cfg_p
                cfg = tray_common.load_config()
                cfg["theme_mode"] = "light"
                cfg["interval"] = 120
                cfg["bg_interval"] = 300
                cfg["compact_view"] = True
                tray_common.save_config(cfg)

                panel = my_devices.BatteryPanelApp(root, disable_tray_host=True, auto_scan=False)
                panel.apply_readings_snapshot([r_hi, r_lo], history_data={"version": 1, "devices": {}})
                root.update_idletasks()
                self.assertEqual(len(panel._card_widgets), 2)
                self.assertTrue(panel._card_widgets[0]._compact)
                self.assertIsNone(panel._card_widgets[0].spark)
                self.assertIn("3.90V", panel._card_widgets[0]._lbl_stat.cget("text"))
                self.assertIn("ReadTimeout", panel._card_widgets[1]._foot_tip._text)

                panel._schedule_auto_refresh(hidden_override=True)
                self.assertEqual(panel._last_scheduled_interval, 300)
                panel._schedule_auto_refresh(hidden_override=False)
                self.assertEqual(panel._last_scheduled_interval, 120)

                # #15 失败设备短间隔重试 (5s / 15s 最多 2 次) + single_retry 更新
                r_fail = devices.PeripheralReading(
                    key="hid:1234:5678", role="mouse", name="FlakyMouse", product="M",
                    vid=0x1234, pid=0x5678, level=None, offline=False, asleep=False,
                    transport="HID", error="TransientBusy", last_read_error="TransientBusy",
                )
                after_delays = []
                orig_after = panel.root.after
                panel.root.after = lambda ms, cb=None, *a: (after_delays.append(ms) or orig_after(ms, cb, *a))  # type: ignore[assignment]
                try:
                    panel._retry_attempts.clear()
                    panel._retry_cycle_id = 77
                    panel._schedule_failed_retries([r_fail], 77)
                    panel._schedule_failed_retries([r_fail], 77)
                    panel._schedule_failed_retries([r_fail], 77)  # 第 3 次超上限不再入队
                    self.assertEqual(after_delays, [5000, 15000])
                finally:
                    panel.root.after = orig_after  # type: ignore[assignment]
                    panel._clear_retry_timers()

                panel.apply_readings_snapshot([r_fail], history_data={"version": 1, "devices": {}})
                r_fixed = devices.PeripheralReading(
                    key="hid:1234:5678", role="mouse", name="FlakyMouse", product="M",
                    vid=0x1234, pid=0x5678, level=88, offline=False, transport="HID",
                )
                panel.ui_queue.put(("single_retry", 77, 1, r_fixed, {"version": 1, "devices": {}}, tray_common.load_config()))
                panel._pump_queues()
                self.assertEqual(panel.readings[0].level, 88)

                panel.open_settings_modal()
                root.update_idletasks()
                self.assertIsNotNone(panel._settings_modal)
                self.assertTrue(hasattr(panel, "_tray_preview_photo"))
                panel._var_tray_style.set("环形")
                panel._on_tray_style_change()
                self.assertEqual(tray_common.load_config()["tray_icon_style"], "ring")
                panel.close_settings_modal()

                panel.open_why_unread_wizard(r_lo)
                self.assertIsNotNone(panel._wizard_modal)
                panel._wizard_modal.close()
                self.assertIsNone(panel._wizard_modal)

                panel._closing = True
                for aid in list(panel._after_ids):
                    try:
                        root.after_cancel(aid)
                    except Exception:
                        pass
                panel._clear_retry_timers()
            finally:
                tray_common.CONFIG_FILE = old_cfg
                neu_ui.apply_theme(orig_theme)
                root.destroy()

    def test_select_popup_click_away_modal_wheel_and_no_click_focus_ring(self):
        import tkinter as tk
        import my_devices

        with tempfile.TemporaryDirectory() as td:
            cfg_p = os.path.join(td, "tray_config.json")
            old_cfg = tray_common.CONFIG_FILE
            orig_theme = neu_ui.get_theme_mode()
            root = tk.Tk()
            root.withdraw()
            try:
                tray_common.CONFIG_FILE = cfg_p
                cfg = tray_common.load_config()
                cfg["theme_mode"] = "light"
                tray_common.save_config(cfg)

                panel = my_devices.BatteryPanelApp(root, disable_tray_host=True, auto_scan=False)
                root.geometry("820x560+100+100")
                root.deiconify()
                root.update()

                # 1) 主页面鼠标点击按钮不显示紫框，关闭设置后也不在齿轮按钮留聚焦环；仅键盘 Tab 导航展示聚焦环
                neu_ui._mark_keyboard_focus()
                panel.btn_settings._set_focus(True)
                self.assertTrue(panel.btn_settings._focused)
                panel.btn_settings._on_press()
                self.assertFalse(panel.btn_settings._focused)
                panel.btn_settings._on_release(type("E", (), {"x": 10, "y": 10})())
                for _ in range(12):
                    root.update()
                    time.sleep(0.01)
                self.assertIsNotNone(panel._settings_modal)
                modal = panel._settings_modal
                self.assertFalse(panel.btn_settings._focused)

                # 2) 设置页滚轮在嵌套 Label / NeuCard / Scrollbar 上也能顺畅滚动，且可拖拽右侧滚动条
                self.assertTrue(bool(modal._body_sb.winfo_manager()))
                first_label = None
                first_sel = None
                stack = [modal.body]
                while stack:
                    w = stack.pop(0)
                    if isinstance(w, tk.Label) and first_label is None:
                        first_label = w
                    if isinstance(w, neu_ui.NeuSelect) and first_sel is None:
                        first_sel = w
                    stack.extend(w.winfo_children())
                self.assertIsNotNone(first_label)
                self.assertIsNotNone(first_sel)
                before_y = float(modal._body_canvas.yview()[0])
                first_label.event_generate("<MouseWheel>", delta=-240)
                root.update_idletasks()
                self.assertGreater(float(modal._body_canvas.yview()[0]), before_y)

                # 3) NeuSelect 单画布选项清晰呈现，点击菜单外部任意坐标立即关闭退出
                first_sel._toggle_popup()
                root.update()
                pop = first_sel._popup
                self.assertIsNotNone(pop)
                pop_cv = pop.winfo_children()[0]
                self.assertIsInstance(pop_cv, tk.Canvas)
                texts = [pop_cv.itemcget(tid, "text") for tid in pop_cv.find_withtag("menu_item") if pop_cv.type(tid) == "text"]
                self.assertEqual(texts, first_sel.values)
                # 在浮层外任意位置点击应关闭浮层
                pop_cv.event_generate("<ButtonPress-1>", x=-50, y=-50, rootx=10, rooty=10)
                root.update_idletasks()
                self.assertIsNone(first_sel._popup)

                # 关闭设置弹窗后，主界面齿轮按钮依然不带聚焦边框
                modal.close()
                root.update()
                self.assertFalse(panel.btn_settings._focused)

                # 4) 验证 pop.grab_set() 将应用内点击发往 Toplevel(pop) 本身时同样立即退出选择
                panel._sel_toolbar_sort._toggle_popup()
                root.update()
                pop2 = panel._sel_toolbar_sort._popup
                self.assertIsNotNone(pop2)
                pop2.event_generate("<ButtonPress-1>", x=-10, y=-10, rootx=120, rooty=120)
                root.update_idletasks()
                self.assertIsNone(panel._sel_toolbar_sort._popup)
            finally:
                try:
                    panel._closing = True
                    for aid in list(panel._after_ids):
                        try:
                            root.after_cancel(aid)
                        except Exception:
                            pass
                    panel._clear_retry_timers()
                except Exception:
                    pass
                tray_common.CONFIG_FILE = old_cfg
                neu_ui.apply_theme(orig_theme)
                root.destroy()

    def test_more_menu_ui_same_option_no_refresh_tray_right_click_and_topmost_hover(self):
        import queue
        import tkinter as tk
        import my_devices
        import tray_service

        with tempfile.TemporaryDirectory() as td:
            cfg_p = os.path.join(td, "tray_config.json")
            old_cfg = tray_common.CONFIG_FILE
            orig_theme = neu_ui.get_theme_mode()
            root = tk.Tk()
            root.withdraw()
            try:
                tray_common.CONFIG_FILE = cfg_p
                cfg = tray_common.load_config()
                cfg["theme_mode"] = "light"
                cfg["font_scale_mode"] = "standard"
                cfg["sort_mode"] = "default"
                tray_common.save_config(cfg)

                r_m = devices.PeripheralReading(key="hid:3837:100C", role="mouse", name="TestMouse", product="M", level=62)
                r_k = devices.PeripheralReading(key="hid:258A:01BB", role="keyboard", name="TestKbd", product="K", level=78)
                panel = my_devices.BatteryPanelApp(root, disable_tray_host=True, auto_scan=False)
                root.geometry("920x620+80+80")
                root.deiconify()
                root.update()
                panel.apply_readings_snapshot([r_m, r_k], history_data={"version": 1, "devices": {}})
                root.update()

                # 1) 省略号按钮改名为“更多”，点开后使用统一新拟物 NeuPopupMenu 且支持子菜单与点外即关
                c0 = panel._card_widgets[0]
                self.assertEqual(c0._btn_more._text, "更多")
                c0._open_context_menu_from_btn()
                root.update()
                menu = getattr(c0, "_card_menu", None)
                self.assertIsNotNone(menu)
                self.assertIsInstance(menu, neu_ui.NeuPopupMenu)
                self.assertTrue(menu.is_open())
                menu_texts = [menu._canvas.itemcget(tid, "text") for tid in menu._canvas.find_withtag("menu_item") if menu._canvas.type(tid) == "text"]
                self.assertTrue(any("修改设备类型" in t for t in menu_texts))
                self.assertTrue(any("不再显示此设备" in t for t in menu_texts))
                sub_idx = next(i for i, rm in enumerate(menu._row_meta) if rm["kind"] == "submenu")
                menu._open_submenu_for_row(sub_idx)
                root.update()
                self.assertIsNotNone(menu._submenu)
                self.assertTrue(menu._submenu.is_open())
                # 通过子菜单真实 Toplevel/Canvas 事件点击“键盘”可触发切换角色并关闭整个菜单
                sub = menu._submenu
                kbd_rm = next(rm for rm in sub._row_meta if rm["item"].get("label") == "键盘")
                srx0, sry0, _, _ = sub._live_card_rect()
                sub_cy = int((kbd_rm["y0"] + kbd_rm["y1"]) / 2.0 - sub._pad)
                sub._canvas.event_generate(
                    "<ButtonPress-1>",
                    x=int(sub._box_w // 2),
                    y=sub_cy,
                    rootx=int(srx0 + sub._box_w // 2),
                    rooty=int(sry0 + sub_cy),
                )
                root.update_idletasks()
                self.assertFalse(menu.is_open())
                self.assertEqual(panel.readings[0].role, "keyboard")
                panel._set_device_role(r_m, "mouse")
                root.update()

                # 再次打开并展开子菜单，点击菜单及子菜单外部任意位置，整体立即退出
                c0 = panel._card_widgets[0]
                c0._open_context_menu_from_btn()
                root.update()
                menu = getattr(c0, "_card_menu", None)
                sub_idx = next(i for i, rm in enumerate(menu._row_meta) if rm["kind"] == "submenu")
                menu._open_submenu_for_row(sub_idx)
                root.update()
                menu._handle_press_screen(10, 10)
                root.update_idletasks()
                self.assertFalse(menu.is_open())
                self.assertIsNone(getattr(c0, "_card_menu", None))

                # 2) 选择与当前完全相同的主题/字号/排序/设备类型时，不触发 UI 重建或卡片刷新
                rebuild_cnt = []
                render_cnt = []
                orig_rebuild = panel._rebuild_ui
                orig_render = panel._render_cards
                panel._rebuild_ui = lambda *a, **k: rebuild_cnt.append(1)  # type: ignore[method-assign]
                panel._render_cards = lambda *a, **k: render_cnt.append(1)  # type: ignore[method-assign]
                try:
                    panel.open_settings_modal()
                    root.update()
                    panel._on_theme_change()
                    panel._on_font_scale_change()
                    panel._apply_sort_mode("default")
                    panel._set_device_role(r_m, "mouse")
                    # 直接通过 NeuSelect._choose 选择同值也不触发 on_change；切为不同值才触发
                    changed_hits = []
                    panel._sel_toolbar_sort._on_change = lambda: changed_hits.append(1)
                    panel._sel_toolbar_sort._choose("默认顺序", via_pointer=True)
                    self.assertEqual(len(rebuild_cnt), 0)
                    self.assertEqual(len(render_cnt), 0)
                    self.assertEqual(len(changed_hits), 0)
                    panel._sel_toolbar_sort._choose("名称排序", via_pointer=True)
                    self.assertEqual(len(changed_hits), 1)
                    panel.close_settings_modal()
                finally:
                    panel._rebuild_ui = orig_rebuild  # type: ignore[method-assign]
                    panel._render_cards = orig_render  # type: ignore[method-assign]

                # 3) 托盘悬停浮层：已删除“X/Y 台在线 · 点击胶囊定位”且严格置于最顶层 (-topmost)
                tip = tray_service.TrayHoverTooltip(
                    root, lambda: (1600, 1000, 1632, 1040), on_open_main=panel.show_main_window, on_open_device=panel.focus_device
                )
                tip.update_data([r_m, r_k], threshold=20)
                tip.show_near_rect((1600, 1000, 1632, 1040), pin_for_preview=True)
                root.update()
                self.assertIsNone(tip._summary_cv)
                self.assertIsNone(tip._lbl_summary)
                self.assertTrue(bool(tip._win.attributes("-topmost")))
                tip.hide()

                # 4) 托盘右键事件流 + 防重入去抖 + 统一风格快捷菜单弹出与点外退出
                q: "queue.SimpleQueue[tuple[str, object]]" = queue.SimpleQueue()
                host = tray_service.TrayHostThread(q)
                host._wndproc(0, tray_service.WM_TRAY, 0, tray_service.WM_RBUTTONUP)
                # 紧随的 WM_CONTEXTMENU 应被去抖忽略
                host._wndproc(0, tray_service.WM_TRAY, 0, tray_service.WM_CONTEXTMENU)
                evs = []
                while not q.empty():
                    evs.append(q.get_nowait()[0])
                self.assertEqual(evs, ["hide_tooltip", "tray_menu"])

                panel.tray_events.put(("tray_menu", (500, 500)))
                panel._pump_queues()
                root.update()
                tmenu = getattr(panel, "_tray_menu", None)
                self.assertIsNotNone(tmenu)
                self.assertTrue(tmenu.is_open())
                self.assertTrue(bool(tmenu._popup.attributes("-topmost")))
                t_texts = [tmenu._canvas.itemcget(tid, "text") for tid in tmenu._canvas.find_withtag("menu_item") if tmenu._canvas.type(tid) == "text"]
                self.assertIn("打开主界面", t_texts)
                self.assertIn("立即刷新电量", t_texts)
                self.assertIn("退出软件", t_texts)
                tmenu._handle_press_screen(10, 10)
                root.update_idletasks()
                self.assertFalse(tmenu.is_open())

                # 5) 从“更多 -> 重命名设备…”打开自定义名称弹窗：输入框立刻激活并支持点边框聚焦、修改与保存/取消
                rename_state: dict[str, object] = {}

                def _inspect_rename_and_save() -> None:
                    dlgs = [
                        w for w in root.winfo_children()
                        if isinstance(w, tk.Toplevel)
                        and w.winfo_exists()
                        and str(w.state()) == "normal"
                        and w is not getattr(tip, "_win", None)
                        and w is not getattr(panel.hover_tooltip, "_win", None)
                    ]
                    if not dlgs:
                        return
                    d = dlgs[-1]
                    try:
                        d.update()
                        eb = None
                        btn_save = None
                        stack = [d]
                        while stack:
                            w = stack.pop(0)
                            if isinstance(w, neu_ui.NeuEntry):
                                eb = w
                            if isinstance(w, neu_ui.NeuButton) and w._text == "保存名称":
                                btn_save = w
                            stack.extend(w.winfo_children())
                        rename_state["has_widgets"] = (eb is not None and btn_save is not None)
                        if eb is not None and btn_save is not None:
                            rename_state["focused_entry"] = (root.focus_get() is eb.entry)
                            rename_state["selected"] = eb.entry.selection_present()
                            eb._bg_label.event_generate("<ButtonPress-1>", x=2, y=2)
                            root.update()
                            rename_state["pad_click_focused"] = (root.focus_get() is eb.entry)
                            eb.entry.delete(0, "end")
                            eb.entry.insert(0, "已改名鼠标")
                            btn_save._on_press()
                            btn_save._on_release(type("E", (), {"x": 12, "y": 12})())
                    except Exception as exc:
                        rename_state["error"] = str(exc)
                        try:
                            d.destroy()
                        except Exception:
                            pass

                c0 = panel._card_widgets[0]
                c0._open_context_menu_from_btn()
                root.update()
                rmenu = c0._card_menu
                ren_idx = next(i for i, rm in enumerate(rmenu._row_meta) if "重命名" in rm["item"].get("label", ""))
                panel.scanning = True  # 就地同步展示新名称而无需阻塞实体硬件扫描
                root.after(180, _inspect_rename_and_save)
                rmenu._activate_row(ren_idx)
                panel.scanning = False
                self.assertNotIn("error", rename_state)
                self.assertTrue(rename_state.get("has_widgets"))
                self.assertTrue(rename_state.get("focused_entry"))
                self.assertTrue(rename_state.get("selected"))
                self.assertTrue(rename_state.get("pad_click_focused"))
                self.assertEqual(tray_common.load_config()["device_names"].get(r_m.key), "已改名鼠标")
                self.assertEqual(panel.readings[0].name, "已改名鼠标")

                # 6) 首次最小化到托盘弹出一次引导提示并持久化 tray_guide_shown=True，二次起不再弹出
                panel._disable_tray_host = False
                panel.hide_to_tray()
                self.assertIsNotNone(panel.low_toast._win)
                self.assertTrue(tray_common.load_config().get("tray_guide_shown"))
                panel.low_toast.dismiss()
                self.assertIsNone(panel.low_toast._win)
                panel.hide_to_tray()
                self.assertIsNone(panel.low_toast._win)
            finally:
                try:
                    panel._closing = True
                    for aid in list(panel._after_ids):
                        try:
                            root.after_cancel(aid)
                        except Exception:
                            pass
                    panel._clear_retry_timers()
                except Exception:
                    pass
                tray_common.CONFIG_FILE = old_cfg
                neu_ui.apply_theme(orig_theme)
                root.destroy()


class TestGap11Items(unittest.TestCase):
    def test_gap_11_items_stability_backup_hooks_history_export_version_and_ble_gatt(self):
        import tkinter as tk
        import my_devices
        from tools import make_version_info

        tray_common.set_data_dir(_TEST_DATA_DIR)
        while tray_common.pop_io_warning() is not None:
            pass

        # #05: Runtime files isolated inside temporary _TEST_DATA_DIR (never E:\电量\源码)
        abs_tmp = os.path.abspath(_TEST_DATA_DIR)
        self.assertEqual(os.path.abspath(tray_common.get_data_dir()), abs_tmp)
        for p in (
            tray_common.CONFIG_FILE,
            tray_common.STATE_FILE,
            tray_common.LOG_FILE,
            battery_history.HISTORY_FILE,
        ):
            self.assertEqual(os.path.dirname(os.path.abspath(p)), abs_tmp)

        # #12: tray_max_icons removed from DEFAULT_CONFIG & stripped on load/save
        self.assertNotIn("tray_max_icons", tray_common.DEFAULT_CONFIG)
        with open(tray_common.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"tray_max_icons": 4, "interval": 90}, f)
        loaded_cfg = tray_common.load_config()
        self.assertNotIn("tray_max_icons", loaded_cfg)
        self.assertEqual(loaded_cfg["interval"], 90)
        self.assertTrue(tray_common.save_config(loaded_cfg))
        with open(tray_common.CONFIG_FILE, "r", encoding="utf-8") as f:
            self.assertNotIn("tray_max_icons", json.load(f))

        # #06: Backup (.bak) + corruption recovery + I/O warning queue for config & history
        loaded_cfg["interval"] = 180
        self.assertTrue(tray_common.save_config(loaded_cfg))
        self.assertTrue(os.path.exists(tray_common.CONFIG_FILE + ".bak"))
        with open(tray_common.CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("{corrupted_json!!")
        rec_cfg = tray_common.load_config()
        self.assertEqual(rec_cfg["interval"], 90)
        cfg_warn = tray_common.pop_io_warning()
        self.assertIsNotNone(cfg_warn)
        self.assertTrue(cfg_warn["recovered"])

        now = time.time()
        h1 = {"version": 1, "devices": {"hid:1111:2222": [{"t": now - 1200, "lvl": 80, "mv": 4050}]}}
        battery_history.save_history(h1, now=now)
        h2 = {"version": 1, "devices": {"hid:1111:2222": [{"t": now - 600, "lvl": 72, "mv": 3990}]}}
        battery_history.save_history(h2, now=now)
        self.assertTrue(os.path.exists(battery_history.HISTORY_FILE + ".bak"))
        with open(battery_history.HISTORY_FILE, "w", encoding="utf-8") as f:
            f.write("not json")
        rec_hist = battery_history.load_history(now=now)
        self.assertEqual(rec_hist["devices"]["hid:1111:2222"][0]["lvl"], 80)
        hist_warn = tray_common.pop_io_warning()
        self.assertIsNotNone(hist_warn)
        self.assertTrue(hist_warn["recovered"])

        # #19: summarize_series_stats, export_history_csv, export_history_json
        series = [
            {"t": now - 7200, "lvl": 90, "mv": 4120, "chg": False, "off": False},
            {"t": now - 3600, "lvl": 70, "mv": 3950, "chg": False, "off": False},
            {"t": now - 1800, "off": True},
            {"t": now - 600, "lvl": 82, "mv": 4160, "chg": True, "off": False},
        ]
        st = battery_history.summarize_series_stats(series, now=now)
        self.assertEqual(st["online_count"], 3)
        self.assertEqual(st["offline_count"], 1)
        self.assertEqual(st["charging_count"], 1)
        self.assertEqual(st["min_level"], 70)
        self.assertEqual(st["max_level"], 90)
        self.assertAlmostEqual(st["avg_level"], 80.7, places=1)
        self.assertEqual(st["min_mv"], 3950)
        self.assertEqual(st["max_mv"], 4160)

        csv_out = os.path.join(_TEST_DATA_DIR, "export.csv")
        json_out = os.path.join(_TEST_DATA_DIR, "export.json")
        self.assertEqual(battery_history.export_history_csv(series, csv_out, key="dev1", device_name="测试鼠"), 4)
        self.assertEqual(battery_history.export_history_json(series, json_out, key="dev1", device_name="测试鼠"), 4)
        with open(csv_out, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0][0], "device_key")
        with open(json_out, "r", encoding="utf-8") as f:
            jdoc = json.load(f)
        self.assertEqual(jdoc["stats"]["online_count"], 3)
        self.assertEqual(len(jdoc["samples"]), 4)

        # #23: Version metadata, update check, make_version_info, CLI --version
        self.assertEqual(tray_common.__version__, "2.4.0")
        upd = tray_common.check_update_status()
        self.assertTrue(upd["ok"])
        self.assertEqual(upd["status"], "local_only")
        self.assertFalse(upd["update_available"])
        vi_txt = make_version_info.render_version_info()
        self.assertIn("VSVersionInfo", vi_txt)
        self.assertIn("2.4.0", vi_txt)
        proc = subprocess.run(
            [sys.executable, os.path.join(_SRC_DIR, "my_devices.py"), "--version", "--json"],
            capture_output=True,
            text=True,
            env={**os.environ, "PBV_DATA_DIR": _TEST_DATA_DIR},
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        ver_json = json.loads(proc.stdout.strip())
        self.assertEqual(ver_json["version"], "2.4.0")

        # #25: BLE GATT grouping, multi-part battery, and HFP+BLE deduplication
        ble_list = bt_battery._build_ble_gatt_devices([
            ("TWS Earbuds", "BTHLE\\DEV_AABBCCDDEEFF&1", [{"label": "左耳", "level": 82}, {"label": "右耳", "level": 76}]),
            ("TWS Earbuds", "BTHLE\\DEV_AABBCCDDEEFF&2", [{"label": "充电盒", "level": 90}]),
            ("BLE Mouse", "BTHLE\\DEV_112233445566", [{"label": "", "level": 64}]),
        ])
        self.assertEqual(len(ble_list), 2)
        tws = next(d for d in ble_list if d.name == "TWS Earbuds")
        self.assertEqual(tws.battery, 76)
        self.assertEqual(len(tws.parts), 3)
        self.assertIn("BLE GATT", tws.source)
        hfp_list = [bt_battery.BluetoothDevice(name="TWS Earbuds", instance_id="BTHENUM\\DEV_AABBCCDDEEFF", battery=80, has_property=True, reason="HFP")]
        merged = bt_battery._merge_bt_sources(hfp_list, ble_list)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len(merged[0].parts), 3)

        # #01 / #02 / #03 / #08 / #09: App pump survival, idle backoff, hooks, toast alpha, modals
        orig_theme = neu_ui.get_theme_mode()
        orig_cfg_file = tray_common.CONFIG_FILE
        before_sys_hook = sys.excepthook
        root = tk.Tk()
        root.withdraw()
        panel = None
        try:
            panel = my_devices.BatteryPanelApp(root, start_minimized=True, auto_scan=False, disable_tray_host=True)
            self.assertIsNotNone(panel._pump_aid)
            self.assertIsNot(sys.excepthook, before_sys_hook)

            # Poisoned queue item + unhandled_error banner + pump timer survival
            panel.ui_queue.put(None)
            panel.ui_queue.put(("unhandled_error", {"where": "test", "summary": "ZeroDivisionError: boom"}))
            panel._pump_queues()
            self.assertIsNotNone(panel._pump_aid)
            self.assertTrue(panel._warn_banner_visible)
            self.assertIn("boom", panel._warn_banner_lbl.cget("text"))
            panel._dismiss_warning_banner()
            self.assertFalse(panel._warn_banner_visible)

            # IO warning surfaced on pump
            tray_common.record_io_warning("test_io", "测试磁盘警告", recovered=False)
            panel._pump_queues()
            self.assertTrue(panel._warn_banner_visible)
            self.assertIn("测试磁盘警告", panel._warn_banner_lbl.cget("text"))
            panel._dismiss_warning_banner()

            # Adaptive backoff + TrayHostThread on_wake + request_pump reset
            delays: list[int] = []
            orig_sched = panel._schedule_pump
            def _capture_sched(delay_ms: int = 100) -> None:
                delays.append(delay_ms)
                orig_sched(delay_ms)
            panel._schedule_pump = _capture_sched
            panel.root.deiconify()
            root.update_idletasks()
            for _ in range(5):
                panel._pump_queues()
            self.assertEqual(delays[-1], 600)
            panel.root.withdraw()
            root.update_idletasks()
            panel._pump_queues()
            self.assertEqual(delays[-1], 800)

            woken: list[bool] = []
            th = tray_service.TrayHostThread(queue.SimpleQueue(), on_wake=lambda: woken.append(True))
            th._put_event("refresh", None)
            self.assertEqual(woken, [True])
            panel.request_pump()
            self.assertEqual(panel._pump_idle_ticks, 0)

            # #03: Global exception hooks capture & restore
            self.assertIsNotNone(panel._restore_exc_hooks)
            try:
                1 / 0
            except ZeroDivisionError as exc:
                threading.excepthook(types.SimpleNamespace(exc_type=ZeroDivisionError, exc_value=exc, exc_traceback=exc.__traceback__, thread=threading.current_thread()))
            q_items = []
            while True:
                try:
                    q_items.append(panel.ui_queue.get_nowait())
                except queue.Empty:
                    break
            self.assertTrue(any(it[0] == "unhandled_error" and "ZeroDivisionError" in it[1].get("summary", "") for it in q_items))
            panel._restore_exc_hooks()
            panel._restore_exc_hooks = None
            self.assertIs(sys.excepthook, before_sys_hook)

            # #09: _on_toast_alpha_change guard & live update + LowBatteryToastPopup trigger in snapshot
            panel._on_toast_alpha_change()  # must safely no-op before settings modal
            panel._var_toast_alpha = tk.StringVar(value="80%")
            panel._on_toast_alpha_change()
            self.assertEqual(tray_common.load_config()["notify_alpha"], 80)

            low_r = devices.PeripheralReading(
                key="hid:9999:0001",
                role="headset",
                name="低电耳机",
                product="Low Headset",
                vid=0x9999,
                pid=0x0001,
                connected=True,
                offline=False,
                level=12,
                transport="BLE",
                connection="BLE GATT",
                parts=[{"label": "左耳", "level": 12}, {"label": "右耳", "level": 25}],
            )
            panel.apply_readings_snapshot([low_r], {"version": 1, "devices": {"hid:9999:0001": series}}, tray_common.load_config())
            root.update_idletasks()
            self.assertIsNotNone(panel.low_toast._win)
            panel.low_toast.dismiss()

            # #19 & #23 UI modals smoke
            panel.open_history_modal(low_r)
            root.update_idletasks()
            self.assertIsNotNone(panel._history_modal)
            panel._history_modal.close()
            self.assertIsNone(panel._history_modal)

            panel.open_about_modal()
            root.update_idletasks()
            self.assertIsNotNone(panel._about_modal)
            panel._about_modal.close()
            self.assertIsNone(panel._about_modal)

            # minimize_frameless_window regression guard (no AttributeError)
            self.assertTrue(callable(getattr(neu_ui, "minimize_frameless_window", None)))
            panel._minimize_window()
        finally:
            if panel is not None:
                try:
                    panel._closing = True
                    panel._on_destroy()
                except Exception:
                    pass
            tray_common.CONFIG_FILE = orig_cfg_file
            neu_ui.apply_theme(orig_theme)
            try:
                root.destroy()
            except Exception:
                pass

    def test_dual_mode_bluetooth_and_24g_merge_and_stale_history_skip(self):
        self.assertEqual(
            devices._strip_bt_profile_suffix("HECATE G2无线版 Hands-Free AG"),
            "HECATE G2无线版",
        )
        fake_iface = HidInterface(
            path=r"\\?\hid#vid_35bb&pid_a209&mi_03&col02",
            vid=0x35BB,
            pid=0xA209,
            version=0,
            usage_page=0xFF02,
            usage=0x0002,
            product="HECATE G2无线版",
            manufacturer="Jieli Technology",
            serial="4250315A35313707",
            input_len=64,
            output_len=64,
            feature_len=0,
        )
        bt_key = "bt:BTHENUM\\{0000111E}\\DEV_D7379D950769"
        hid_key = "hid:35BB:A209:4250315A35313707"

        # Case 1: BT online + 2.4G dongle plugged in (no 2.4G battery) -> merged single online card
        bt_on = devices.PeripheralReading(
            key=bt_key, role="headset", name="HECATE G2无线版 Hands-Free AG",
            product="HECATE G2无线版 Hands-Free AG", connected=True, offline=False,
            level=90, connection="蓝牙", transport="BT",
        )
        hid_off = devices.PeripheralReading(
            key=hid_key, role="headset", name="HECATE G2 无线耳机",
            product="HECATE G2无线版", vid=0x35BB, pid=0xA209,
            connected=False, offline=True, level=None, connection="2.4G",
            transport="HID", ifaces=[fake_iface],
        )
        self.assertTrue(devices._is_dual_mode_pair(hid_off, bt_on))
        stats1 = devices.ScanStats(online_count=1, offline_count=1)
        known1 = {bt_key: {"name": "HECATE G2无线版 Hands-Free AG", "had_battery": True, "last_level": 90}}
        merged1, dirty1 = devices._merge_dual_mode_readings(
            [hid_off, bt_on], known_map=known1, names_map={},
            bt_cached_levels={bt_key: 90}, hist={"version": 1, "devices": {}},
            stats=stats1, now=1000.0,
        )
        self.assertTrue(dirty1)
        self.assertEqual(len(merged1), 1)
        self.assertEqual(merged1[0].key, bt_key)
        self.assertEqual(merged1[0].name, "HECATE G2无线版")
        self.assertEqual(merged1[0].connection, "蓝牙 + 2.4G")
        self.assertEqual(merged1[0].level, 90)
        self.assertFalse(merged1[0].offline)
        self.assertFalse(merged1[0].stale)
        self.assertEqual(stats1.online_count, 1)
        self.assertEqual(stats1.offline_count, 0)

        # Case 2: BT disconnected + 2.4G dongle plugged in -> merged single 2.4G card reusing cached BT level
        bt_off = devices.PeripheralReading(
            key=bt_key, role="headset", name="HECATE G2无线版",
            product="HECATE G2无线版 Hands-Free AG", connected=False, offline=True,
            level=None, connection="蓝牙", transport="BT",
        )
        hid_off2 = devices.PeripheralReading(
            key=hid_key, role="headset", name="HECATE G2 无线耳机",
            product="HECATE G2无线版", vid=0x35BB, pid=0xA209,
            connected=False, offline=True, level=None, connection="2.4G",
            transport="HID", ifaces=[fake_iface],
        )
        stats2 = devices.ScanStats(online_count=0, offline_count=2)
        merged2, _ = devices._merge_dual_mode_readings(
            [hid_off2, bt_off], known_map=known1, names_map={},
            bt_cached_levels={bt_key: 80}, hist={"version": 1, "devices": {}},
            stats=stats2, now=2000.0,
        )
        self.assertEqual(len(merged2), 1)
        self.assertFalse(merged2[0].offline)
        self.assertTrue(merged2[0].stale)
        self.assertEqual(merged2[0].level, 80)
        self.assertEqual(merged2[0].status_text, "2.4G · 沿用蓝牙电量")
        self.assertEqual(stats2.online_count, 1)
        self.assertEqual(stats2.offline_count, 0)

        # Stale merged reading must not pollute real discharge history
        with tempfile.TemporaryDirectory() as td:
            hp = os.path.join(td, "hist.json")
            h = battery_history.record_readings(merged2, path=hp, now=2000.0)
            self.assertNotIn(bt_key, h.get("devices", {}))

    def test_bt_quantized_smooth_interpolation_learning_calibration_and_guards(self):
        base_t = 1_795_000_000.0

        def _make_online_segment(start_t: float, hours: float, lvl: int, step_sec: float = 300.0) -> list[dict]:
            pts = []
            cur = start_t
            end_t = start_t + hours * 3600.0
            while cur <= end_t + 1e-6:
                pts.append({"t": cur, "lvl": lvl, "chg": False, "off": False})
                cur += step_sec
            return pts

        # 1) 平台初期（0.2h，默认 2.0%/h -> drop=0）：保持原值 90%，不标估算
        s_short = _make_online_segment(base_t, 0.2, 90)
        r_short = battery_history.interpolate_quantized_bt_level(s_short, 90, now=base_t + 0.2 * 3600.0)
        self.assertTrue(r_short.quantized)
        self.assertFalse(r_short.interpolated)
        self.assertEqual(r_short.level, 90)
        self.assertEqual(r_short.raw_level, 90)

        # 2) 默认速率（2.0%/h）活跃 1.5h -> 推算 ~87%；中间插入 8h 关机/休眠间隙不应计入耗电
        s_morning = _make_online_segment(base_t, 1.0, 90)
        t_after_sleep = base_t + 9.0 * 3600.0  # 中间 8h 离线休眠
        s_afternoon = _make_online_segment(t_after_sleep, 0.5, 90)
        s_sleep = s_morning + [{"t": base_t + 1.2 * 3600.0, "lvl": None, "chg": False, "off": True}] + s_afternoon
        r_sleep = battery_history.interpolate_quantized_bt_level(s_sleep, 90, now=t_after_sleep + 0.5 * 3600.0)
        self.assertTrue(r_sleep.interpolated)
        self.assertEqual(r_sleep.rate_source, "default")
        self.assertAlmostEqual(r_sleep.elapsed_online_h, 1.5, places=2)
        self.assertEqual(r_sleep.level, 87)  # 90 - floor(1.5 * 2.0) = 87

        # 3) 真实 10% 掉档学习（含 6h 夜间休眠间隙）：每 2.5h 活跃掉 10% -> 学习速率约 3.9%/h
        t0 = base_t
        seg_100 = _make_online_segment(t0, 0.5, 100)
        t_drop90 = t0 + 0.5 * 3600.0 + 300.0
        # 90% 平台：活跃 1.5h + 休眠 6h + 活跃 1.0h = 总活跃 2.5h
        seg_90_a = _make_online_segment(t_drop90, 1.5, 90)
        t_90_b = t_drop90 + 7.5 * 3600.0
        seg_90_b = _make_online_segment(t_90_b, 1.0, 90)
        t_drop80 = t_90_b + 1.0 * 3600.0 + 300.0
        # 80% 平台：连续活跃 2.5h 后掉 70%（300s 步进含 1 格边界）
        seg_80 = _make_online_segment(t_drop80, 2.5, 80)
        t_drop70 = t_drop80 + 2.5 * 3600.0 + 300.0
        # 70% 平台：连续活跃 1.0h
        seg_70 = _make_online_segment(t_drop70, 1.0, 70)
        s_learned = seg_100 + seg_90_a + seg_90_b + seg_80 + seg_70
        r_learned = battery_history.interpolate_quantized_bt_level(s_learned, 70, now=t_drop70 + 1.0 * 3600.0)
        self.assertTrue(r_learned.interpolated)
        self.assertEqual(r_learned.rate_source, "learned")
        self.assertGreaterEqual(r_learned.rate_per_h, 3.5)
        self.assertLessEqual(r_learned.rate_per_h, 4.2)
        self.assertIn(r_learned.level, (66, 67))  # 70 - floor(1.0 * ~3.87) = 67

        # 4) 钳位保护：在 70% 平台活跃 10h 也绝不越过 61%（raw - 9）
        seg_70_long = _make_online_segment(t_drop70, 10.0, 70)
        r_clamped = battery_history.interpolate_quantized_bt_level(
            seg_100 + seg_90_a + seg_90_b + seg_80 + seg_70_long, 70, now=t_drop70 + 10.0 * 3600.0
        )
        self.assertTrue(r_clamped.interpolated)
        self.assertEqual(r_clamped.level, 61)

        # 5) 硬件真实掉档到 60%：立刻校准归零为精确 60%
        t_drop60 = t_drop70 + 10.0 * 3600.0 + 300.0
        s_cal = seg_100 + seg_90_a + seg_90_b + seg_80 + seg_70_long + [{"t": t_drop60, "lvl": 60, "chg": False, "off": False}]
        r_cal = battery_history.interpolate_quantized_bt_level(s_cal, 60, now=t_drop60)
        self.assertFalse(r_cal.interpolated)
        self.assertEqual(r_cal.level, 60)
        self.assertEqual(r_cal.raw_level, 60)

        # 5b) 跨离线/2.4G 间隙掉档（如切到 2.4G 玩 4 小时后切回蓝牙直接变 70%）：不得污染学习速率
        s_cross_gap = (
            _make_online_segment(base_t, 0.5, 90)
            + [{"t": base_t + 0.6 * 3600.0, "lvl": None, "chg": False, "off": True}]
            + _make_online_segment(base_t + 4.6 * 3600.0, 0.5, 70)
        )
        r_cross = battery_history.interpolate_quantized_bt_level(s_cross_gap, 70, now=base_t + 5.1 * 3600.0)
        self.assertEqual(r_cross.rate_source, "default")
        self.assertEqual(r_cross.level, 69)  # 仅按 70% 平台 0.5h * 默认 2.0%/h = 1% 推算

        # 6) 非 10% 步进设备（历史出现过 87%）：绝不插值
        s_fine = _make_online_segment(base_t, 2.0, 90) + [{"t": base_t - 600.0, "lvl": 87, "chg": False, "off": False}]
        r_fine = battery_history.interpolate_quantized_bt_level(s_fine, 90, now=base_t + 2.0 * 3600.0)
        self.assertFalse(r_fine.quantized)
        self.assertFalse(r_fine.interpolated)
        self.assertEqual(r_fine.level, 90)

        # 7) 端到端链路：record_readings 仅记录真实硬件 90%，_populate_history_metrics 输出平滑 ~87%，HID 1% 设备不受影响
        bt_key = "bt:HECATE_G2_TEST"
        hid_key = "hid:3837:100C"
        now_t = base_t + 1.5 * 3600.0
        hist = {
            "version": 1,
            "devices": {
                bt_key: _make_online_segment(base_t, 1.5, 90),
                hid_key: _make_online_segment(base_t, 1.5, 80),
            },
        }
        r_bt = devices.PeripheralReading(
            key=bt_key, role="headset", name="HECATE G2无线版", product="HECATE G2无线版",
            connected=True, offline=False, level=90, connection="蓝牙 + 2.4G", transport="BT",
        )
        r_hid = devices.PeripheralReading(
            key=hid_key, role="mouse", name="MCHOSE A5 Ultra", product="A5 Ultra",
            connected=True, offline=False, level=80, connection="2.4G", transport="HID",
        )
        with tempfile.TemporaryDirectory() as td:
            hp = os.path.join(td, "hist.json")
            battery_history.save_history(hist, path=hp)
            hist_saved = battery_history.record_readings([r_bt, r_hid], path=hp, now=now_t + 240.0)
            # 历史文件里必须仍存原始硬件 90，不含插值数
            self.assertTrue(all(pt["lvl"] == 90 for pt in hist_saved["devices"][bt_key]))
            devices._populate_history_metrics([r_bt, r_hid], hist_saved, now_t + 240.0)

        self.assertTrue(r_bt.interpolated)
        self.assertEqual(r_bt.raw_level, 90)
        self.assertEqual(r_bt.level, 87)  # (1.5h + 240s) * 2.0%/h = 3.13% -> drop 3 -> 87%
        self.assertEqual(r_bt.level_text, "~87%")
        self.assertEqual(r_bt.status_text, "蓝牙 + 2.4G · 平滑估算")
        self.assertIn("硬件上报 90%", r_bt.detail)

        # HID 鼠标（即使当前正好 80%）绝不触发蓝牙插值
        self.assertFalse(r_hid.interpolated)
        self.assertEqual(r_hid.level, 80)
        self.assertEqual(r_hid.level_text, "80%")


if __name__ == "__main__":
    unittest.main(verbosity=2)
