# -*- coding: utf-8 -*-
"""扩展功能与底层模块单元测试（覆盖 #1-#14 全部需求、协议解析、看门狗、在线更新、告警报表、底层 win_link/win_shell/hid_win/darkrim_proto）。"""

from __future__ import annotations

import csv
import functools
import hashlib
import http.server
import json
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

_EXT_TEST_DIR = tempfile.mkdtemp(prefix="pbv_ext_test_")
os.environ["PBV_DATA_DIR"] = _EXT_TEST_DIR

import battery
import battery_history
import bt_battery
import darkrim_proto
import devices
import hid_win
import tray_common
import tray_service
import watchdog
import win_link
import win_shell

tray_common.set_data_dir(_EXT_TEST_DIR)


class _MockHidCaps:
    def __init__(self, in_len=64, out_len=64, feat_len=64):
        self.InputReportByteLength = in_len
        self.OutputReportByteLength = out_len
        self.FeatureReportByteLength = feat_len


class _MockHidDevice:
    def __init__(self, *, in_queue=None, feature_map=None, caps=None, write_ok=True):
        self._in = list(in_queue or [])
        self._feat = dict(feature_map or {})
        self.caps = caps or _MockHidCaps()
        self.write_ok = write_ok
        self.written = []
        self.feature_set = []
        self.drained = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def drain(self):
        self.drained = True

    def write_output(self, data, use_hidd=False):
        if not self.write_ok:
            raise hid_win.HidError("write fail")
        self.written.append(bytes(data))

    def read_input(self, timeout_ms=100, length=None):
        if self._in:
            return self._in.pop(0)
        return b""

    def set_feature_report(self, data):
        self.feature_set.append(bytes(data))

    def get_feature_report(self, rid, length=64):
        if rid in self._feat:
            return self._feat[rid]
        raise hid_win.HidError(f"missing rid {rid}")


class TestCleanupsAndProtocols(unittest.TestCase):
    def test_code_consistency_cleanups(self):
        self.assertEqual(tray_common.APP_NAME, "PeripheralBatteryViewer")
        self.assertEqual(tray_common.APP_DISPLAY_NAME, "外设电量查看器")
        self.assertIn(0x0BDA, battery.MCHOSE_PAYLOAD_VIDS)
        self.assertIs(battery.MCHOSE_ACCEPT_VIDS, battery.MCHOSE_PAYLOAD_VIDS)
        # 确保 battery._clean_hex_str / validate_protocol_dict 无 Any 未定义问题
        self.assertEqual(battery._clean_hex_str("5a-a5-01"), "5A A5 01")
        sp = battery.validate_protocol_dict({"name": "AnyOk", "vid": "0x41E4", "pid": "0x2001", "usage_page": "0xFF02"})
        self.assertEqual(sp.vid, 0x41E4)

        # 0x41E4 显式策略分发覆盖
        if_20b = hid_win.HidInterface(
            path="p1", vid=0x41E4, pid=0x2001, version=0,
            usage_page=0xFF02, usage=0x0001, input_len=20, output_len=20, feature_len=0,
            product="", manufacturer="", serial="",
        )
        self.assertIn(battery.strategy_sinowealth_20b, battery._pick_strategies(if_20b))

        # DualSense 不应再被 NON_PERIPH_KEYWORDS 误杀，且新厂商关键字生效
        self.assertNotIn("dualsense", devices.NON_PERIPH_KEYWORDS)
        self.assertIn(0x054C, devices.KNOWN_PROTOCOL_VIDS)
        self.assertIn(0x1532, devices.KNOWN_PROTOCOL_VIDS)
        self.assertIn(0x1038, devices.KNOWN_PROTOCOL_VIDS)
        self.assertIn(0x045E, devices.KNOWN_PROTOCOL_VIDS)

    def test_sony_dualsense_and_ds4_parsers(self):
        # DualSense USB report 0x01, bat byte @ offset 53
        ds_usb = bytearray(64)
        ds_usb[0] = 0x01
        ds_usb[53] = 0x16  # charging (1) + step 6 -> 65%
        p1 = battery.parse_sony_controller_report(bytes(ds_usb), 0x0CE6)
        self.assertEqual(p1["connection"], "有线 USB")
        self.assertEqual(p1["level"], 65)
        self.assertTrue(p1["charging"])
        self.assertFalse(p1["full"])

        # DualSense BT report 0x31, bat byte @ offset 54
        ds_bt = bytearray(78)
        ds_bt[0] = 0x31
        ds_bt[54] = 0x2A  # full (2)
        p2 = battery.parse_sony_controller_report(bytes(ds_bt), 0x0CE6)
        self.assertEqual(p2["connection"], "蓝牙")
        self.assertEqual(p2["level"], 100)
        self.assertTrue(p2["full"])

        # DualShock 4 BT report 0x11, bat byte @ offset 32
        ds4_bt = bytearray(78)
        ds4_bt[0] = 0x11
        ds4_bt[32] = 0x04  # discharging step 4 -> 45%
        p3 = battery.parse_sony_controller_report(bytes(ds4_bt), 0x09CC)
        self.assertEqual(p3["connection"], "蓝牙")
        self.assertEqual(p3["level"], 45)
        self.assertFalse(p3["charging"])

    def test_razer_90b_protocol_and_checksum(self):
        cmd = battery.build_razer_cmd(0x07, 0x80)
        self.assertEqual(len(cmd), 91)
        self.assertEqual(cmd[0], 0x00)
        # 构建 status=0x02 电量回复
        resp = bytearray(91)
        resp[1] = 0x02  # status success
        resp[2] = 0x1F
        resp[7] = 0x07
        resp[8] = 0x80
        resp[10] = 204  # ~80%
        crc = 0
        for b in resp[3:89]:
            crc ^= b
        resp[89] = crc
        parsed = battery.parse_razer_report(bytes(resp))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["kind"], "level")
        self.assertEqual(parsed["level"], 80)

        # 坏 CRC 拦截
        bad = bytearray(resp)
        bad[89] ^= 0xFF
        self.assertIsNone(battery.parse_razer_report(bytes(bad)))

        # strategy_razer with mock HidDevice
        resp_chg = bytearray(91)
        resp_chg[1] = 0x02
        resp_chg[2] = 0x1F
        resp_chg[7] = 0x07
        resp_chg[8] = 0x84
        resp_chg[10] = 0x01
        crc_c = 0
        for b in resp_chg[3:89]:
            crc_c ^= b
        resp_chg[89] = crc_c

        calls = {"n": 0}
        class _RazerDev(_MockHidDevice):
            def get_feature_report(self, rid, length=91):
                calls["n"] += 1
                return bytes(resp) if calls["n"] == 1 else bytes(resp_chg)

        iface = hid_win.HidInterface(
            path="razer0", vid=0x1532, pid=0x00A6, version=0,
            usage_page=0xFF00, usage=0x0001, input_len=91, output_len=91, feature_len=91,
            product="", manufacturer="", serial="",
        )
        with mock.patch.object(battery, "HidDevice", return_value=_RazerDev()):
            res = battery.strategy_razer(iface)
        self.assertEqual(res.level, 80)
        self.assertTrue(res.charging)

    def test_steelseries_atk_and_logitech_strategies_mocked(self):
        # SteelSeries parser & strategy
        nova_rx = bytes([0x06, 0xB0, 0x03, 72, 1]) + b"\x00" * 59
        self.assertEqual(battery.parse_steelseries_report(nova_rx), {"level": 72, "charging": True, "online": True})
        ss_iface = hid_win.HidInterface(
            path="ss0", vid=0x1038, pid=0x12D5, version=0,
            usage_page=0xFFC0, usage=0x0001, input_len=64, output_len=64, feature_len=0,
            product="", manufacturer="", serial="",
        )
        with mock.patch.object(battery, "HidDevice", return_value=_MockHidDevice(in_queue=[nova_rx])):
            ss_res = battery.strategy_steelseries(ss_iface)
        self.assertEqual(ss_res.level, 72)
        self.assertTrue(ss_res.charging)

        # ATK/VGN parser & strategy (mock HidDevice)
        atk_rx = bytes([0x08, 0x04, 0, 0, 0, 0, 64, 1, 0x0F, 0xA0]) + b"\x00" * 7  # 64%, chg=1, 4000mV
        self.assertEqual(battery.parse_atk_vgn_report(atk_rx), {"level": 64, "charging": True, "voltage_mv": 4000})
        atk_iface = hid_win.HidInterface(
            path="atk0", vid=0x373B, pid=0x1216, version=0,
            usage_page=0xFF02, usage=0x0002, input_len=17, output_len=17, feature_len=0,
            product="", manufacturer="", serial="",
        )
        with mock.patch.object(battery, "HidDevice", return_value=_MockHidDevice(in_queue=[atk_rx])):
            atk_res = battery.strategy_atk_vgn(atk_iface)
        self.assertEqual(atk_res.level, 64)
        self.assertEqual(atk_res.voltage_mv, 4000)
        self.assertTrue(atk_res.charging)

        # Logitech HID++ 2.0 parser & strategy (mock HidDevice)
        logi_idx_resp = bytes([0x11, 0xFF, 0x00, 0x0A, 0x05, 0x00, 0x00]) + b"\x00" * 13
        logi_bat_resp = bytes([0x11, 0xFF, 0x05, 0x1A, 88, 0x00, 0x00]) + b"\x00" * 13
        logi_iface = hid_win.HidInterface(
            path="logi0", vid=0x046D, pid=0xC547, version=0,
            usage_page=0xFF00, usage=0x0001, input_len=20, output_len=20, feature_len=0,
            product="", manufacturer="", serial="",
        )
        with mock.patch.object(battery, "HidDevice", return_value=_MockHidDevice(in_queue=[logi_idx_resp, logi_bat_resp])):
            logi_res = battery.strategy_logitech_hidpp(logi_iface)
        self.assertEqual(logi_res.level, 88)
        self.assertFalse(logi_res.charging)
        self.assertIn("UnifiedBattery", logi_res.source)

    def test_darkrim_highlevel_reassembly_and_bad_crc(self):
        # 多分片重组：frag=0 (more=1) + frag=1 (more=0) + 一条坏 CRC 干扰帧
        f1 = darkrim_proto.build_frame(seq=7, payload=b"\x02\x07DARK", frag=0, more=1)
        f2 = darkrim_proto.build_frame(seq=7, payload=b"RIM\x01\x01\x52", frag=1, more=0)  # + battery 82%
        bad = bytearray(darkrim_proto.build_frame(seq=7, payload=b"\x01\x01\x05", frag=2, more=0))
        bad[13] ^= 0xFF  # 破坏 CRC

        mock_dev = _MockHidDevice(
            in_queue=[bytes(bad), f1, f2],
            caps=_MockHidCaps(in_len=65, out_len=65, feat_len=0),
        )
        out = darkrim_proto.query(mock_dev, seq=7, tlv_type=darkrim_proto.TLV_NAME, timeout=0.5)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].crc_ok)
        self.assertEqual(out[0].tlvs(), [(0x02, b"DARKRIM"), (0x01, b"\x52")])

        # read_battery_only + read_info high-level path
        bat_f = darkrim_proto.build_frame(seq=0, payload=bytes([darkrim_proto.TLV_BATTERY, 1, 79]))
        fake_iface = hid_win.HidInterface(
            path="dk0", vid=darkrim_proto.VID, pid=0x0001, version=0,
            usage_page=darkrim_proto.USAGE_PAGE, usage=0x0002, input_len=65, output_len=65, feature_len=0,
            product="", manufacturer="", serial="",
        )
        with mock.patch.object(darkrim_proto, "HidDevice", return_value=_MockHidDevice(in_queue=[bat_f], caps=_MockHidCaps(65, 65, 0))):
            lvl, err = darkrim_proto.read_battery_only(iface=fake_iface, timeout=0.2)
        self.assertEqual(lvl, 79)
        self.assertEqual(err, "")


class TestLowLevelWinModules(unittest.TestCase):
    def test_win_link_directory_file_and_safety(self):
        with tempfile.TemporaryDirectory() as td:
            src_dir = os.path.join(td, "target_dir")
            os.makedirs(src_dir)
            src_file = os.path.join(src_dir, "hello.txt")
            with open(src_file, "w", encoding="utf-8") as f:
                f.write("pbv-link-test")

            dir_lnk = os.path.join(td, "dir_link")
            kind = win_link.create_dir_link(src_dir, dir_lnk)
            self.assertIn(kind, ("symlink", "junction"))
            self.assertEqual(win_link.create_dir_link(src_dir, dir_lnk), kind)  # 幂等
            self.assertTrue(os.path.exists(os.path.join(dir_lnk, "hello.txt")))
            self.assertTrue(win_link.remove_link(dir_lnk))
            self.assertTrue(os.path.isdir(src_dir))

            file_lnk = os.path.join(td, "file_link.txt")
            fkind = win_link.create_file_link(src_file, file_lnk)
            self.assertIn(fkind, ("symlink", "hardlink"))
            with open(file_lnk, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "pbv-link-test")
            self.assertTrue(win_link.remove_link(file_lnk))
            self.assertTrue(os.path.isfile(src_file))

            # 安全防护：拒绝覆盖真实非链接文件
            real_blocker = os.path.join(td, "blocker.txt")
            with open(real_blocker, "w", encoding="utf-8") as f:
                f.write("keep")
            with self.assertRaises(OSError):
                win_link.create_file_link(src_file, real_blocker)

    def test_win_shell_shortcut_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            lnk = os.path.join(td, "test_app.lnk")
            ok = win_shell.create_shortcut(
                lnk_path=lnk,
                target=sys.executable,
                arguments="--watchdog --minimized",
                workdir=td,
                description="Unit test shortcut",
            )
            self.assertTrue(ok)
            self.assertTrue(win_shell.shortcut_exists(lnk))
            info = win_shell.read_shortcut(lnk)
            self.assertEqual(os.path.normcase(info["target"]), os.path.normcase(os.path.abspath(sys.executable)))
            self.assertIn("--watchdog --minimized", info["arguments"])
            self.assertTrue(win_shell.remove_shortcut(lnk))
            self.assertFalse(win_shell.shortcut_exists(lnk))

    def test_hid_win_mocked_ctypes_paths(self):
        # 测试 parse_vid_pid & enumerate_hid/HidDevice 错误与正常路径
        with mock.patch.object(hid_win.setupapi, "SetupDiGetClassDevsW", return_value=hid_win.INVALID_HANDLE_VALUE):
            with self.assertRaises(OSError):
                hid_win.enumerate_hid()

        with mock.patch.object(hid_win.kernel32, "CreateFileW", return_value=hid_win.INVALID_HANDLE_VALUE):
            with self.assertRaises(hid_win.HidError):
                hid_win.HidDevice("\\\\?\\hid#vid_1234&pid_5678")

        with mock.patch.object(hid_win.kernel32, "CreateFileW", return_value=1234), \
             mock.patch.object(hid_win.kernel32, "CloseHandle", return_value=1), \
             mock.patch.object(hid_win.hid, "HidD_GetPreparsedData", return_value=0), \
             mock.patch.object(hid_win.hid, "HidD_GetFeature", return_value=0):
            dev = hid_win.HidDevice("\\\\?\\hid#vid_1234&pid_5678", write=True)
            with self.assertRaises(hid_win.HidError):
                dev.get_feature_report(0x01, 16)
            dev.close()


class TestUpdaterWatchdogAppDataAndAlerts(unittest.TestCase):
    def test_online_updater_custom_github_sha256_and_script(self):
        payload_bytes = b"FAKE_PBV_EXE_BINARY_DATA_v9.9.9" * 64
        good_sha = hashlib.sha256(payload_bytes).hexdigest()

        class _Handler(http.server.BaseHTTPRequestHandler):
            manifest = {}
            def do_GET(self):
                if self.path == "/manifest.json":
                    body = json.dumps(self.manifest).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/gh.json":
                    port = self.server.server_address[1]
                    gh = {
                        "tag_name": "v9.9.9",
                        "body": "新增雷蛇与索尼协议",
                        "html_url": f"http://127.0.0.1:{port}/releases",
                        "assets": [{
                            "name": "外设电量查看器.exe",
                            "browser_download_url": f"http://127.0.0.1:{port}/app.exe",
                            "digest": f"sha256:{good_sha}",
                        }],
                    }
                    body = json.dumps(gh).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/app.exe":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload_bytes)))
                    self.end_headers()
                    self.wfile.write(payload_bytes)
                else:
                    self.send_response(404)
                    self.end_headers()
            def log_message(self, *args):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        _Handler.manifest = {
            "version": "9.9.9",
            "download_url": f"http://127.0.0.1:{port}/app.exe",
            "sha256": good_sha,
            "notes": "Test release notes",
        }
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            st = tray_common.check_update_status(update_url=f"http://127.0.0.1:{port}/manifest.json")
            self.assertTrue(st["ok"])
            self.assertTrue(st["update_available"])
            self.assertEqual(st["sha256"], good_sha)

            st_gh = tray_common.check_update_status(update_url=f"http://127.0.0.1:{port}/gh.json")
            self.assertTrue(st_gh["ok"])
            self.assertEqual(st_gh["latest_version"], "9.9.9")
            self.assertEqual(st_gh["sha256"], good_sha)
            self.assertEqual(st_gh["notes"], "新增雷蛇与索尼协议")

            with tempfile.TemporaryDirectory() as td:
                dst_ok = os.path.join(td, "new_ok.exe")
                dl_res = tray_common.download_and_verify_update(
                    st_gh["download_url"], good_sha, dst_ok, progress_cb=lambda d, t: None
                )
                self.assertTrue(dl_res["ok"])
                self.assertTrue(os.path.isfile(dst_ok))

                # 篡改 SHA-256 必须拦截并删掉临时文件
                dst_bad = os.path.join(td, "new_bad.exe")
                dl_bad = tray_common.download_and_verify_update(st_gh["download_url"], "0" * 64, dst_bad)
                self.assertFalse(dl_bad["ok"])
                self.assertFalse(os.path.exists(dst_bad))

                # dry_run 更新脚本包含备份回滚逻辑
                cur_fake = os.path.join(td, "cur.exe")
                with open(cur_fake, "wb") as f:
                    f.write(b"old")
                ok_up, bat_p = tray_common.prepare_and_launch_updater(dst_ok, current_exe=cur_fake, wait_pid=999999, dry_run=True)
                self.assertTrue(ok_up)
                self.assertTrue(os.path.isfile(bat_p))
                with open(bat_p, "r", encoding="utf-8") as bf:
                    content = bf.read()
                self.assertIn(".old", content)
                os.remove(bat_p)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_watchdog_crash_freeze_backoff_and_clean_exit(self):
        # 1) 用户正常退出 exit=0 -> 看门狗不重启并退出 0
        r_clean = watchdog.WatchdogRunner(
            launch_fn=lambda: "p0",
            is_alive_fn=lambda p: False,
            poll_code_fn=lambda p: 0,
            terminate_fn=lambda p: None,
            heartbeat_age_fn=lambda: 0.0,
            has_quit_fn=lambda: False,
            clear_quit_fn=lambda: None,
            sleep_fn=lambda _s: None,
        )
        self.assertEqual(r_clean.run(), 0)
        self.assertEqual(r_clean.stop_reason, "clean_exit")
        self.assertEqual(r_clean.restart_count, 0)

        # 2) 心跳超时 + 连续崩溃达上限 (max_restarts=2) -> 杀卡死进程并按限流停机
        t_cur = {"v": 0.0}
        killed = []
        launches = []
        def _fake_sleep(sec):
            t_cur["v"] += sec
        r_freeze = watchdog.WatchdogRunner(
            launch_fn=lambda: (launches.append(len(launches)) or f"p{len(launches)}"),
            is_alive_fn=lambda p: p == "p1",  # p1 卡死，p2/p3 直接非零崩溃
            poll_code_fn=lambda p: 1,
            terminate_fn=lambda p: killed.append(p),
            heartbeat_age_fn=lambda: 120.0,
            has_quit_fn=lambda: False,
            clear_quit_fn=lambda: None,
            sleep_fn=_fake_sleep,
            clock_fn=lambda: t_cur["v"],
            heartbeat_timeout_s=90.0,
            startup_grace_s=10.0,
            check_interval_s=12.0,
            max_restarts=2,
            window_sec=300.0,
        )
        rc = r_freeze.run()
        self.assertEqual(rc, 1)
        self.assertEqual(killed, ["p1"])
        self.assertEqual(r_freeze.restart_count, 2)
        self.assertEqual(r_freeze.stop_reason, "restart_limit")

    def test_full_charge_per_device_threshold_alert_history_retention_and_power(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as td:
            old_dir = tray_common.get_data_dir()
            try:
                tray_common.set_data_dir(td)
                cfg = tray_common.load_config()
                cfg["notify_threshold"] = 20
                cfg["device_thresholds"] = {"bt:headset": 10, "hid:mouse": 30}
                cfg["notify_full_charge"] = True
                tray_common.save_config(cfg)

                mon = tray_service.LowBatteryMonitor()
                r_hs = devices.PeripheralReading(key="bt:headset", role="headset", name="耳机", product="HS", level=15, charging=False)
                r_ms = devices.PeripheralReading(key="hid:mouse", role="mouse", name="鼠标", product="MS", level=25, charging=True)

                # 首轮：耳机 15% 不应低于专属阈值 10%；鼠标 25% 充电中，记录 initial charging 态不误报充满
                alerts1 = mon.check_with_tiers([r_hs, r_ms], cfg, now=now)
                self.assertEqual(alerts1, [])
                self.assertEqual(mon.check_full_charges([r_hs, r_ms], cfg, now=now), [])

                # 耳机掉到 9% (<10%) 触发低电；鼠标充满 (full=True, 100%) 触发充满电通知
                r_hs.level = 9
                r_ms.level = 100
                r_ms.charging = False
                r_ms.full = True
                alerts2 = mon.check_with_tiers([r_hs, r_ms], cfg, now=now + 60)
                self.assertEqual([r.key for r, _ in alerts2], ["bt:headset"])
                full2 = mon.check_full_charges([r_hs, r_ms], cfg, now=now + 60)
                self.assertEqual([r.key for r in full2], ["hid:mouse"])
                # 同状态第二轮不重复提醒
                self.assertEqual(mon.check_full_charges([r_hs, r_ms], cfg, now=now + 120), [])

                # 告警历史记录、自动闭合与统计报表
                battery_history.record_alert_event(
                    key="bt:headset", name="耳机", kind="low", tier="urgent", level=9, threshold=10, now=now - 1800
                )
                r_hs.level = 20
                battery_history.resolve_open_low_alerts([r_hs], now=now, cfg=cfg)
                doc = battery_history.load_alert_history(now=now)
                self.assertEqual(len(doc["events"]), 1)
                self.assertAlmostEqual(doc["events"][0]["duration_min"], 30.0, places=1)
                ast = battery_history.summarize_alert_stats(doc, window_days=7, now=now)
                self.assertEqual(ast["low_count"], 1)
                self.assertAlmostEqual(ast["total_low_minutes"], 30.0, places=1)
                csv_p = os.path.join(td, "alerts.csv")
                self.assertEqual(battery_history.export_alert_history_csv(doc["events"], csv_p), 1)

                # 可配置保留天数 30 天（保留 20 天前的数据）与 1 天（清理 20 天前的数据），外加导入合并/清空
                hist_p = os.path.join(td, "battery_history.json")
                h_mix = {
                    "version": 1,
                    "devices": {
                        "devA": [
                            {"t": now - 20 * 86400, "lvl": 95, "chg": False, "off": False},
                            {"t": now - 7200, "lvl": 80, "chg": False, "off": False},
                            {"t": now - 3600, "lvl": 70, "chg": False, "off": False},
                            {"t": now - 1800, "lvl": 95, "chg": True, "off": False},
                        ]
                    },
                }
                saved_30 = battery_history.save_history(h_mix, path=hist_p, now=now, retention_days=30)
                self.assertEqual(len(saved_30["devices"]["devA"]), 4)
                saved_1 = battery_history.prune_history(h_mix, now=now, retention_days=1)
                self.assertEqual(len(saved_1["devices"]["devA"]), 3)

                imp_p = os.path.join(td, "import.json")
                with open(imp_p, "w", encoding="utf-8") as f:
                    json.dump({"version": 1, "devices": {"devA": [{"t": now - 1000, "lvl": 92, "chg": False, "off": False}]}}, f)
                imp_res = battery_history.import_history_json(imp_p, path=hist_p, now=now, retention_days=30)
                self.assertEqual(imp_res["added"], 1)
                self.assertTrue(battery_history.clear_device_history("devA", path=hist_p))
                self.assertNotIn("devA", battery_history.load_history(path=hist_p, now=now)["devices"])

                # 功耗 (mA/mW/Wh) 与等效充电循环推算
                pwr = battery_history.estimate_power_and_health(h_mix["devices"]["devA"][1:], capacity_mah=500, now=now)
                self.assertEqual(pwr["capacity_mah"], 500)
                self.assertGreater(pwr["total_discharged_mah"], 0)
                self.assertAlmostEqual(pwr["equivalent_cycles"], 0.25, places=2)
                self.assertIsNotNone(pwr["est_current_ma"])

                # 数据目录迁移至指定目录（模拟 %APPDATA% 迁移）
                dst_mig = os.path.join(td, "appdata_sim")
                mig_res = tray_common.migrate_data_dir(dst_mig, write_redirect=False)
                self.assertTrue(mig_res["ok"])
                self.assertTrue(os.path.isfile(os.path.join(dst_mig, "tray_config.json")))
                self.assertTrue(os.path.isfile(os.path.join(dst_mig, "alert_history.json")))
            finally:
                tray_common.set_data_dir(old_dir)

    def test_ui_filter_grouping_native_tip_and_guide_notice(self):
        import tkinter as tk
        import my_devices

        with tempfile.TemporaryDirectory() as td:
            old_dir = tray_common.get_data_dir()
            root = tk.Tk()
            root.withdraw()
            try:
                tray_common.set_data_dir(td)
                cfg = tray_common.load_config()
                cfg["tray_guide_shown"] = False
                cfg["minimize_on_close"] = True
                tray_common.save_config(cfg)

                r1 = devices.PeripheralReading(key="k1", role="mouse", name="迈从鼠标", product="MCHOSE", vid=0x3837, pid=0x100C, level=15)
                r2 = devices.PeripheralReading(key="k2", role="keyboard", name="机械键盘", product="G98", vid=0x258A, pid=0x01BB, level=80, charging=True)
                r3 = devices.PeripheralReading(key="k3", role="headset", name="游戏耳机", product="G2", offline=True, level=None)

                self.assertEqual([r.key for r in my_devices.filter_readings([r1, r2, r3], query="G98")], ["k2"])
                self.assertEqual([r.key for r in my_devices.filter_readings([r1, r2, r3], status_filter="low", cfg=cfg)], ["k1"])
                self.assertEqual([r.key for r in my_devices.filter_readings([r1, r2, r3], status_filter="charging")], ["k2"])
                self.assertEqual([r.key for r in my_devices.filter_readings([r1, r2, r3], status_filter="offline")], ["k3"])

                # TrayHostThread: grid vs native NIF_TIP + NIN_BALLOONUSERCLICK open_device
                q: "queue.SimpleQueue[tuple[str, object]]" = queue.SimpleQueue()
                th = tray_service.TrayHostThread(q)
                th.update_readings([r1, r2], threshold=20, hover_mode="native")
                self.assertEqual(th._hover_mode, "native")
                th.show_notification("低电", "鼠标低电", warning=True, device_key="k1")
                th._wndproc(0, tray_service.WM_TRAY, 0, tray_service.NIN_BALLOONUSERCLICK)
                self.assertEqual(q.get_nowait(), ("open_device", "k1"))

                panel = my_devices.BatteryPanelApp(root, disable_tray_host=True, auto_scan=False)
                panel.apply_readings_snapshot([r1, r2, r3], {"version": 1, "devices": {}}, cfg)
                root.update_idletasks()
                panel._toggle_group_by_role()
                self.assertTrue(panel.cfg["group_by_role"])
                self.assertEqual(len(panel._group_headers), 3)

                # 首次最小化气泡触发并写回 tray_guide_shown=True
                panel._disable_tray_host = False  # 允许触发引导标志逻辑而不依赖真实 Win32 托盘句柄
                panel.hide_to_tray(from_user=True)
                panel._disable_tray_host = True
                self.assertTrue(tray_common.load_config()["tray_guide_shown"])

                panel._closing = True
                panel._on_destroy()
            finally:
                tray_common.set_data_dir(old_dir)
                try:
                    root.destroy()
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
