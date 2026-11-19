# -*- coding: utf-8 -*-
"""app.py —— HID / 2.4G 外设协议调试、原始探测与手动标定工具（零第三方依赖）

支持命令行模式与桌面调试界面：
  python app.py --list
  python app.py --battery
  python app.py --bt
  python app.py --dump VID:PID
  pythonw app.py [--select VID:PID]
"""

from __future__ import annotations

import argparse
import ctypes
import queue
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Optional

import battery
import bt_battery
import devices
import neu_ui
import tray_common
from hid_win import HidInterface, enumerate_hid

try:
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.WinDLL("user32").SetProcessDPIAware()
    except Exception:
        pass


def _sync_palette_globals() -> None:
    global BG, FG, MUTED, DIM, BLURPLE, CORAL, HEADING_ACTIVE_BG
    BG = neu_ui.BASE
    FG = neu_ui.TEXT
    MUTED = neu_ui.TEXT_MUTED
    DIM = neu_ui.TEXT_DIM
    BLURPLE = neu_ui.BLURPLE_BRAND
    CORAL = neu_ui.RED_DANGER
    HEADING_ACTIVE_BG = neu_ui.HEADING_ACTIVE_BG


BG = neu_ui.BASE
FG = neu_ui.TEXT
MUTED = neu_ui.TEXT_MUTED
DIM = neu_ui.TEXT_DIM
BLURPLE = neu_ui.BLURPLE_BRAND
CORAL = neu_ui.RED_DANGER
HEADING_ACTIVE_BG = neu_ui.HEADING_ACTIVE_BG


def _low_battery_threshold() -> int:
    try:
        val = int(tray_common.load_config().get("notify_threshold", 20))
    except Exception:
        val = 20
    return max(5, min(50, val))


def _battery_fg(
    level: Optional[int],
    *,
    charging: bool = False,
    asleep: bool = False,
    threshold: Optional[int] = None,
) -> str:
    th = _low_battery_threshold() if threshold is None else threshold
    return neu_ui.battery_accent_color(
        level,
        threshold=th,
        charging=charging,
        offline=level is None and not asleep,
        asleep=asleep,
    )


def _fmt_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def run_cli(args: argparse.Namespace) -> int:
    devs = enumerate_hid()
    if args.list:
        for d in devs:
            print(
                f"{d.vid:04X}:{d.pid:04X} up=0x{d.usage_page:04X} u=0x{d.usage:04X} "
                f"in={d.input_len} out={d.output_len} feat={d.feature_len} "
                f"| {d.product or '-'} | {d.manufacturer or '-'}"
            )
        return 0

    if args.bt:
        for b in bt_battery.list_bluetooth_devices():
            print(f"{b.name} | battery={b.battery} | {b.reason} | {b.instance_id}")
        return 0

    if args.dump:
        parts = args.dump.upper().replace("0X", "").split(":")
        if len(parts) != 2:
            print("--dump 参数格式应为 VID:PID，例如 3837:100C")
            return 2
        tv, tp = int(parts[0], 16), int(parts[1], 16)
        for d in devs:
            if d.vid == tv and d.pid == tp:
                print(f"=== {d.vid:04X}:{d.pid:04X} up=0x{d.usage_page:04X} feat={d.feature_len} ===")
                for rid, raw in battery.dump_feature_reports(d):
                    print(f"  RID 0x{rid:02X} ({len(raw)}B): {_fmt_bytes(raw)}")
        return 0

    if args.battery:
        specs = battery.load_user_protocols()
        for (vid, pid, serial), group in battery.group_hid_devices(devs).items():
            res = battery.read_device_battery(group, specs)
            if res.level is not None or res.asleep or vid in (0x3837, 0x258A, 0x35BB, 0x37D7):
                name = battery.friendly_device_name(vid, pid, group[0].product, group[0].manufacturer)
                lvl = f"{res.level}%" if res.level is not None else "--"
                print(f"{vid:04X}:{pid:04X} ({name}) -> {lvl} | {res.detail or res.error}")
        return 0

    return 0


def configure_debug_treeview_style(widget: tk.Misc, *, compact: bool = False) -> None:
    _sync_palette_globals()
    style = ttk.Style(widget.winfo_toplevel())
    style.theme_use("clam")
    base_font = neu_ui.ui_font(neu_ui.FONT_CAPTION, root=widget)
    head_font = neu_ui.ui_font(neu_ui.FONT_CAPTION, "bold", root=widget)
    style.configure(
        "Treeview",
        background=neu_ui.BASE,
        fieldbackground=neu_ui.BASE,
        foreground=neu_ui.TEXT,
        rowheight=neu_ui.sx(22 if compact else 25, widget),
        borderwidth=0,
        relief="flat",
        font=base_font,
    )
    style.configure(
        "Treeview.Heading",
        background=neu_ui.BASE,
        foreground=neu_ui.TEXT_MUTED,
        font=head_font,
        borderwidth=0,
        relief="flat",
        padding=(3 if compact else 4, 4 if compact else 6),
    )
    style.map(
        "Treeview.Heading",
        background=[("active", neu_ui.HEADING_ACTIVE_BG)],
        foreground=[("active", neu_ui.TEXT)],
    )
    style.map(
        "Treeview",
        background=[("selected", neu_ui.BLURPLE_SELECTION)],
        foreground=[("selected", neu_ui.BLURPLE_LINK)],
    )
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])


class DebugWorkspaceFrame(tk.Frame):
    """可嵌入模态浮层或独立窗口的 HID / 2.4G 调试与协议标定工作区。"""

    def __init__(
        self,
        parent: tk.Misc,
        select_vidpid: str = "",
        *,
        compact: bool = False,
        show_header: bool = False,
        initial_tab: int = 0,
        on_saved: Optional[Callable[[], None]] = None,
    ) -> None:
        _sync_palette_globals()
        super().__init__(parent, bg=neu_ui.BASE, bd=0, highlightthickness=0)
        self.select_vidpid = select_vidpid.upper().strip()
        self._compact = compact
        self._show_header = show_header
        self._initial_tab = max(0, min(3, int(initial_tab)))
        self._on_saved = on_saved
        self.saved_protocol = False
        self.ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self.by_iid: dict[str, HidInterface] = {}
        self.bt_by_iid: dict[str, bt_battery.BluetoothDevice] = {}
        self.monitor_stop: Optional[threading.Event] = None
        self._closing = False
        self._scanning = False
        self._hid_busy = False
        self._after_ids: list[str] = []
        self._hint_reset_id: Optional[str] = None
        self._tree_tip_win: Optional[tk.Toplevel] = None
        self._tree_tip_iid: str = ""
        self._tree_display_mode: str = ""
        self._hint_default = "选中左侧设备或子接口，查看诊断或探测 / 保存自定义协议"
        self.calib_entries: dict[str, tk.Widget] = {}

        configure_debug_treeview_style(self, compact=compact)
        self._build_ui()
        self._sync_action_states()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._after_ids.append(self.after(80, self._pump))
        self._after_ids.append(self.after(120, self.scan_all))

    def _make_recessed_text(self, parent: tk.Widget, wrap: str = "word") -> tk.Text:
        well = neu_ui.NeuCard(
            parent,
            radius=14 if self._compact else 16,
            pad=0,
            inner_pad=(10, 8) if self._compact else (14, 12),
            off=4 if self._compact else 5,
            box_r=3,
            inset=True,
        )
        txt = tk.Text(
            well.content,
            bg=neu_ui.BASE,
            fg=neu_ui.TEXT,
            insertbackground=neu_ui.BLURPLE_BRAND,
            selectbackground=neu_ui.BLURPLE_BRAND,
            selectforeground=neu_ui.TEXT_ON_ACCENT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=neu_ui.mono_font(neu_ui.FONT_CAPTION, root=self),
            wrap=wrap,
        )
        sb = neu_ui.NeuScrollbar(well.content, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y", padx=(4, 0))
        txt.pack(side="left", fill="both", expand=True)
        txt._neu_well = well  # type: ignore[attr-defined]
        return txt

    def _switch_tab(self, idx: int) -> None:
        if 0 <= idx < len(self._tab_frames):
            self._tab_frames[idx].tkraise()

    def _set_hint(self, text: str, *, fg: str = MUTED, auto_reset_ms: int = 0) -> None:
        if self._hint_reset_id is not None:
            try:
                self.after_cancel(self._hint_reset_id)
            except Exception:
                pass
            self._hint_reset_id = None
        try:
            self.lbl_hint.configure(text=text, fg=fg)
        except Exception:
            return
        if auto_reset_ms > 0:
            self._hint_reset_id = self.after(auto_reset_ms, self._restore_hint)

    def _restore_hint(self) -> None:
        self._hint_reset_id = None
        if not self._closing and self.winfo_exists():
            try:
                self.lbl_hint.configure(text=self._hint_default, fg=MUTED)
            except Exception:
                pass

    def _build_ui(self) -> None:
        c = self._compact
        top_padx = 0 if c else 26
        top_pady = (0, neu_ui.sx(8, self)) if c else 12
        top = tk.Frame(self, bg=BG, padx=top_padx, pady=0 if c else 12)
        top.pack(fill="x", pady=(0, top_pady[1]) if c else 0)

        top_btns = tk.Frame(top, bg=BG)
        top_btns.pack(side="right")
        btn_top_fs = neu_ui.FONT_CAPTION if c else neu_ui.FONT_BODY
        self._btn_read_sel = neu_ui.NeuButton(
            top_btns,
            text="读取选中项电量",
            command=self.read_selected,
            accent=True,
            weight="bold",
            radius=14 if c else 16,
            pad=6 if c else 10,
            padx=12 if c else 16,
            pady=4 if c else 6,
            font_size=btn_top_fs,
        )
        self._btn_read_sel.pack(side="left", padx=(0, neu_ui.sx(8, self)))
        self._btn_scan_all = neu_ui.NeuButton(
            top_btns,
            text="扫描全部设备",
            command=self.scan_all,
            radius=14 if c else 16,
            pad=6 if c else 10,
            padx=11 if c else 14,
            pady=4 if c else 6,
            font_size=btn_top_fs,
        )
        self._btn_scan_all.pack(side="left")

        title_col = tk.Frame(top, bg=BG)
        title_col.pack(side="left", fill="x", expand=True, padx=(0, 12 if c else 16))
        if self._show_header:
            tk.Label(
                title_col,
                text="HID / 2.4G 外设调试与标定工具",
                bg=BG,
                fg=FG,
                font=neu_ui.ui_font(neu_ui.FONT_TITLE, "bold", root=self),
                anchor="w",
            ).pack(anchor="w")
        self.lbl_hint = tk.Label(
            title_col,
            text=self._hint_default,
            bg=BG,
            fg=MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self),
            anchor="w",
        )
        self.lbl_hint.pack(anchor="w", pady=(3, 0) if self._show_header else (4, 0))
        init_stats = devices.get_last_scan_stats()
        stats_txt = (
            f"最近扫描诊断：{init_stats.summary_text()}"
            if init_stats.ended_at > 0
            else "最近扫描诊断：等待扫描完成…"
        )
        self.lbl_scan_stats = tk.Label(
            title_col,
            text=stats_txt,
            bg=BG,
            fg=MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self),
            anchor="w",
        )
        self.lbl_scan_stats.pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self, bg=BG, padx=0 if c else 14, pady=0 if c else 4)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=5, minsize=neu_ui.sx(260 if c else 450, self))
        body.columnconfigure(1, weight=6, minsize=neu_ui.sx(290 if c else 440, self))
        body.rowconfigure(0, weight=1)

        card_pad = 8 if c else 16
        left = neu_ui.NeuCard(
            body,
            radius=18 if c else 24,
            pad=card_pad,
            inner_pad=(8, 8) if c else (14, 16),
            off=5 if c else 7,
            box_r=3 if c else 4,
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, neu_ui.sx(8 if c else 12, self)))
        left.content.rowconfigure(0, weight=1)
        left.content.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            left.content,
            columns=("vidpid", "usage", "caps", "level"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="设备 / 接口")
        self.tree.heading("vidpid", text="VID:PID")
        self.tree.heading("usage", text="UP:Usage")
        self.tree.heading("caps", text="I/O/F" if c else "IN/OUT/F")
        self.tree.heading("level", text="电量")
        if c:
            self.tree.column("#0", width=neu_ui.sx(116, self), minwidth=neu_ui.sx(90, self), stretch=True)
            self.tree.column("vidpid", width=neu_ui.sx(58, self), anchor="center", stretch=False)
            self.tree.column("usage", width=neu_ui.sx(58, self), anchor="center", stretch=False)
            self.tree.column("caps", width=neu_ui.sx(38, self), anchor="center", stretch=False)
            self.tree.column("level", width=neu_ui.sx(34, self), anchor="center", stretch=False)
        else:
            self.tree.column("#0", width=neu_ui.sx(196, self), minwidth=neu_ui.sx(130, self), stretch=True)
            self.tree.column("vidpid", width=neu_ui.sx(88, self), anchor="center", stretch=False)
            self.tree.column("usage", width=neu_ui.sx(104, self), anchor="center", stretch=False)
            self.tree.column("caps", width=neu_ui.sx(78, self), anchor="center", stretch=False)
            self.tree.column("level", width=neu_ui.sx(52, self), anchor="center", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = neu_ui.NeuScrollbar(left.content, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Configure>", self._on_tree_configure)
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<Leave>", self._hide_tree_tip)

        right = neu_ui.NeuCard(
            body,
            radius=18 if c else 24,
            pad=card_pad,
            inner_pad=(12, 12) if c else (18, 18),
            off=5 if c else 7,
            box_r=3 if c else 4,
        )
        right.grid(row=0, column=1, sticky="nsew")
        right.content.rowconfigure(1, weight=1)
        right.content.columnconfigure(0, weight=1)

        self.tabs = neu_ui.NeuTabs(
            right.content,
            ["电量与诊断", "集合信息", "原始探测", "标定与保存"],
            on_switch=self._switch_tab,
        )
        self.tabs.grid(row=0, column=0, sticky="w", pady=(0, neu_ui.sx(8 if c else 12, self)))

        stack = tk.Frame(right.content, bg=BG)
        stack.grid(row=1, column=0, sticky="nsew")
        stack.rowconfigure(0, weight=1)
        stack.columnconfigure(0, weight=1)

        # Tab 1: 电量与诊断
        t1 = tk.Frame(stack, bg=BG)
        t1.grid(row=0, column=0, sticky="nsew")
        t1.columnconfigure(0, weight=1)
        t1.rowconfigure(3, weight=1)
        self.lbl_sel = tk.Label(
            t1,
            text="请在左侧选择一个设备或 HID 接口",
            bg=BG,
            fg=FG,
            font=neu_ui.ui_font(neu_ui.FONT_SUBTITLE, "bold", root=self),
            anchor="w",
        )
        self.lbl_sel.grid(row=0, column=0, sticky="w")
        self.lbl_big = tk.Label(
            t1,
            text="--",
            bg=BG,
            fg=DIM,
            font=neu_ui.display_font(22 if c else neu_ui.FONT_NUMERIC, "bold", root=self),
            anchor="w",
        )
        self.lbl_big.grid(row=1, column=0, sticky="w", pady=(4 if c else 6, 2 if c else 4))
        self.lbl_stat = tk.Label(
            t1,
            text="",
            bg=BG,
            fg=MUTED,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=self),
            anchor="w",
        )
        self.lbl_stat.grid(row=2, column=0, sticky="w", pady=(0, 8 if c else 12))
        self.txt_diag = self._make_recessed_text(t1, wrap="word")
        self.txt_diag._neu_well.grid(row=3, column=0, sticky="nsew")  # type: ignore[attr-defined]

        # Tab 2: 全部集合信息
        t2 = tk.Frame(stack, bg=BG)
        t2.grid(row=0, column=0, sticky="nsew")
        t2.rowconfigure(0, weight=1)
        t2.columnconfigure(0, weight=1)
        self.txt_ifaces = self._make_recessed_text(t2, wrap="none")
        self.txt_ifaces._neu_well.grid(row=0, column=0, sticky="nsew")  # type: ignore[attr-defined]

        # Tab 3: 原始探测
        t3 = tk.Frame(stack, bg=BG)
        t3.grid(row=0, column=0, sticky="nsew")
        t3.columnconfigure(0, weight=1)
        t3.rowconfigure(2, weight=1)

        r1 = tk.Frame(t3, bg=BG)
        r1.grid(row=0, column=0, sticky="ew", pady=(0, 6 if c else 8))
        btn_pad = 5 if c else 7
        btn_px = 9 if c else 12
        btn_py = 4 if c else 5
        btn_fs = neu_ui.FONT_CAPTION
        btn_gap = neu_ui.sx(6, self)
        self._btn_dump = neu_ui.NeuButton(
            r1,
            text="Feature 转储" if c else "转储 Feature Reports",
            command=self.cmd_dump_features,
            radius=12 if c else 14,
            pad=btn_pad,
            font_size=btn_fs,
            padx=btn_px,
            pady=btn_py,
        )
        self._btn_dump.pack(side="left", padx=(0, btn_gap))
        self._btn_probe = neu_ui.NeuButton(
            r1,
            text="探测 RID (1..31)" if c else "探测活跃 Report ID (1..31)",
            command=self.cmd_probe_rids,
            radius=12 if c else 14,
            pad=btn_pad,
            font_size=btn_fs,
            padx=btn_px,
            pady=btn_py,
        )
        self._btn_probe.pack(side="left", padx=(0, btn_gap))
        self.btn_mon = neu_ui.NeuButton(
            r1,
            text="监听输入报告",
            command=self.cmd_toggle_monitor,
            dot_color=DIM,
            radius=12 if c else 14,
            pad=btn_pad,
            font_size=btn_fs,
            padx=btn_px,
            pady=btn_py,
        )
        self.btn_mon.pack(side="left")

        r2 = tk.Frame(t3, bg=BG)
        r2.grid(row=1, column=0, sticky="ew", pady=(0, 8 if c else 10))
        self.v_rid = tk.StringVar(value="13")
        self.v_cmd = tk.StringVar(value="4A 01")
        self._btn_send = neu_ui.NeuButton(
            r2,
            text="发送 Output" if c else "试发 Output 指令",
            command=self.cmd_send_custom,
            accent=True,
            radius=12 if c else 14,
            pad=btn_pad,
            font_size=btn_fs,
            padx=btn_px,
            pady=btn_py,
        )
        self._btn_send.pack(side="right", padx=(6 if c else 8, 0))
        tk.Label(r2, text="RID(hex):" if c else "Report ID(hex):", bg=BG, fg=MUTED, font=neu_ui.ui_font(btn_fs, root=self)).pack(side="left")
        neu_ui.NeuEntry(
            r2, textvariable=self.v_rid, width=4 if c else 5, font_tuple=neu_ui.mono_font(btn_fs, root=self)
        ).pack(side="left", padx=(4 if c else 6, 8 if c else 12))
        tk.Label(r2, text="CMD(hex):" if c else "命令字节(hex):", bg=BG, fg=MUTED, font=neu_ui.ui_font(btn_fs, root=self)).pack(side="left")
        neu_ui.NeuEntry(
            r2, textvariable=self.v_cmd, width=12 if c else 18, font_tuple=neu_ui.mono_font(btn_fs, root=self)
        ).pack(side="left", fill="x", expand=True, padx=(4 if c else 6, 0))

        self.txt_raw = self._make_recessed_text(t3, wrap="none")
        self.txt_raw._neu_well.grid(row=2, column=0, sticky="nsew")  # type: ignore[attr-defined]

        # Tab 4: 手动标定并保存
        t4 = tk.Frame(stack, bg=BG, padx=2 if c else 4, pady=2 if c else 4)
        t4.grid(row=0, column=0, sticky="nsew")
        t4.columnconfigure(1, weight=1)
        fields = [
            ("协议名称", "name", "我的自定义无线外设", False),
            ("通信方式", "method", "output_then_input", False),
            ("Report ID (hex)", "rid", "08", True),
            ("命令字节 (hex，可空)", "cmd", "", False),
            ("期望回复前缀 (hex，可空)", "expect", "", False),
            ("电量字节偏移 (从 0 起)", "lvl_off", "6", True),
            ("电量最大值 (通常 100)", "lvl_max", "100", True),
            ("充电标志偏移 (可空)", "chg_off", "", True),
        ]
        self.calib_vars: dict[str, tk.StringVar] = {}
        self.calib_entries = {}
        f_pad = 3 if c else 4
        lbl_fs = neu_ui.FONT_CAPTION
        for row_idx, (label, key, default, is_short) in enumerate(fields):
            tk.Label(
                t4,
                text=label,
                bg=BG,
                fg=MUTED,
                font=neu_ui.ui_font(lbl_fs, root=self),
                anchor="w",
            ).grid(row=row_idx, column=0, sticky="w", pady=f_pad, padx=(0, 10 if c else 14))
            v = tk.StringVar(value=default)
            self.calib_vars[key] = v
            if key == "method":
                sel_w = neu_ui.NeuSelect(
                    t4,
                    variable=v,
                    values=["output_then_input", "feature", "input", "output"],
                    width_px=neu_ui.sx(172 if c else 188, self),
                )
                sel_w.grid(row=row_idx, column=1, sticky="w", pady=f_pad)
                self.calib_entries[key] = sel_w
            elif is_short:
                ent = neu_ui.NeuEntry(
                    t4, textvariable=v, width=7 if c else 8, font_tuple=neu_ui.mono_font(btn_fs, root=self)
                )
                ent.grid(row=row_idx, column=1, sticky="w", pady=f_pad)
                self.calib_entries[key] = ent
            else:
                ent = neu_ui.NeuEntry(t4, textvariable=v, width=20 if c else 24)
                ent.grid(row=row_idx, column=1, sticky="ew", pady=f_pad)
                self.calib_entries[key] = ent
        btn_row4 = tk.Frame(t4, bg=BG)
        btn_row4.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(8 if c else 12, 0))
        self._btn_save = neu_ui.NeuButton(
            btn_row4,
            text="保存到 protocols_user.json",
            command=self.cmd_save_calib,
            accent=True,
            weight="bold",
            radius=14 if c else 16,
            pad=7 if c else 10,
            padx=12 if c else 16,
            pady=5 if c else 6,
            font_size=btn_top_fs,
        )
        self._btn_save.pack(side="left", padx=(0, neu_ui.sx(8, self)))
        self._btn_export_proto = neu_ui.NeuButton(
            btn_row4,
            text="导出协议…",
            command=self.cmd_export_protocols,
            radius=14 if c else 16,
            pad=6 if c else 9,
            padx=10 if c else 12,
            pady=5 if c else 6,
            font_size=btn_top_fs,
        )
        self._btn_export_proto.pack(side="left", padx=(0, neu_ui.sx(6, self)))
        self._btn_import_proto = neu_ui.NeuButton(
            btn_row4,
            text="导入协议…",
            command=self.cmd_import_protocols,
            radius=14 if c else 16,
            pad=6 if c else 9,
            padx=10 if c else 12,
            pady=5 if c else 6,
            font_size=btn_top_fs,
        )
        self._btn_import_proto.pack(side="left")

        self._tab_frames = [t1, t2, t3, t4]
        self._switch_tab(self._initial_tab)

    def _log_raw(self, text: str) -> None:
        self.txt_raw.insert("end", text + "\n")
        self.txt_raw.see("end")

    def _hide_tree_tip(self, _e=None) -> None:
        self._tree_tip_iid = ""
        if self._tree_tip_win is not None:
            try:
                if self._tree_tip_win.winfo_exists():
                    self._tree_tip_win.destroy()
            except Exception:
                pass
            self._tree_tip_win = None

    def _on_tree_configure(self, event=None) -> None:
        w = int(getattr(event, "width", 0) or self.tree.winfo_width() or 0)
        if w <= 20:
            return
        if w < neu_ui.sx(235, self):
            mode = "tiny"
            cols = ("level",)
        elif w < neu_ui.sx(335, self):
            mode = "narrow"
            cols = ("vidpid", "level")
        else:
            mode = "full"
            cols = ("vidpid", "usage", "caps", "level")
        if mode != self._tree_display_mode:
            self._tree_display_mode = mode
            try:
                self.tree.configure(displaycolumns=cols)
            except Exception:
                pass

    def _on_tree_motion(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if not iid:
            self._hide_tree_tip()
            return
        if iid == self._tree_tip_iid and self._tree_tip_win is not None and self._tree_tip_win.winfo_exists():
            return
        self._hide_tree_tip()
        self._tree_tip_iid = iid
        text = self.tree.item(iid, "text")
        vals = self.tree.item(iid, "values") or ()
        detail_parts = [text] + [str(v) for v in vals if str(v).strip()]
        tip_txt = neu_ui.TEXT_SEP.join(detail_parts)
        if not tip_txt:
            return
        tip = tk.Toplevel(self)
        tip.withdraw()
        tip.configure(bg=neu_ui.TOOLTIP_BG)
        neu_ui.enable_frameless_taskbar(tip, no_activate=True)
        tk.Label(
            tip,
            text=tip_txt,
            bg=neu_ui.TOOLTIP_BG,
            fg=neu_ui.TOOLTIP_FG,
            font=neu_ui.ui_font(neu_ui.FONT_CAPTION, root=tip),
            padx=neu_ui.sx(8, tip),
            pady=neu_ui.sx(4, tip),
        ).pack()
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        wl, wt, wr, wb = neu_ui.get_work_area()
        wx = max(wl + 6, min(wr - tw - 6, int(event.x_root) + neu_ui.sx(12, tip)))
        wy = max(wt + 6, min(wb - th - 6, int(event.y_root) + neu_ui.sx(14, tip)))
        tip.geometry(f"{tw}x{th}+{wx}+{wy}")
        tip.deiconify()
        neu_ui.enable_frameless_taskbar(tip, no_activate=True)
        self._tree_tip_win = tip

    def _sync_action_states(self) -> None:
        """统一同步全部操作按钮启用/禁用态（无选中项、扫描中或 HID 忙碌时清晰禁用）。"""
        sel = self.tree.selection()
        has_sel = bool(sel)
        has_hid = self._selected_iface() is not None
        busy = self._hid_busy
        scanning = self._scanning
        active_btn = getattr(self, "_active_busy_btn", None)

        def _set_dis(btn: Optional[neu_ui.NeuButton], dis: bool) -> None:
            if btn is None or btn is active_btn:
                return
            try:
                btn.set_disabled(dis)
            except Exception:
                pass

        _set_dis(self._btn_read_sel, busy or scanning or (not has_sel))
        _set_dis(self._btn_dump, busy or (not has_hid))
        _set_dis(self._btn_probe, busy or (not has_hid))
        _set_dis(self.btn_mon, (busy and self.monitor_stop is None) or (not has_hid and self.monitor_stop is None))
        _set_dis(self._btn_send, busy or (not has_hid))
        _set_dis(self._btn_save, busy or (not has_hid))

    def _set_scan_loading(self, loading: bool) -> None:
        self._scanning = loading
        try:
            self._btn_scan_all.set_loading(loading, "扫描中…" if loading else None)
        except Exception:
            pass
        self._sync_action_states()

    def _begin_hid_busy(self, btn: neu_ui.NeuButton, loading_text: str) -> bool:
        if self._hid_busy:
            self._set_hint("正在执行上一项探测或读取操作，请稍候…", fg=neu_ui.TEXT_MUTED, auto_reset_ms=2200)
            return False
        self._hid_busy = True
        self._active_busy_btn = btn
        try:
            btn.set_loading(True, loading_text)
        except Exception:
            pass
        self._sync_action_states()
        return True

    def _end_hid_busy(self) -> None:
        self._hid_busy = False
        btn = getattr(self, "_active_busy_btn", None)
        self._active_busy_btn = None
        if btn is not None:
            try:
                btn.set_loading(False)
            except Exception:
                pass
        self._sync_action_states()

    def _focus_calib_field(self, key: str) -> None:
        w = self.calib_entries.get(key)
        if w is None:
            return
        try:
            target = getattr(w, "entry", w)
            target.focus_set()
        except Exception:
            pass

    def scan_all(self) -> None:
        if self._scanning:
            return
        self._set_scan_loading(True)
        self._set_hint("正在枚举 USB HID 与蓝牙设备…", fg=MUTED)

        def worker() -> None:
            try:
                hids = enumerate_hid()
                bts = bt_battery.displayable_devices()
                self.ui_queue.put(("scan_done", hids, bts))
            except Exception as e:
                self.ui_queue.put(("scan_error", str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _selected_iface(self) -> Optional[HidInterface]:
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        if iid in self.by_iid:
            return self.by_iid[iid]
        for child in self.tree.get_children(iid):
            if child in self.by_iid:
                return self.by_iid[child]
        return None

    def _on_select(self, _e=None) -> None:
        self._sync_action_states()
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid in self.bt_by_iid:
            b = self.bt_by_iid[iid]
            self.lbl_sel.configure(text=f"[蓝牙] {b.name}")
            self.lbl_big.configure(
                text=f"{b.battery}%" if b.battery is not None else "--",
                fg=_battery_fg(b.battery),
            )
            self.lbl_stat.configure(text=b.reason)
            self.txt_diag.delete("1.0", "end")
            self.txt_diag.insert("end", f"InstanceId: {b.instance_id}\nBattery: {b.battery}\nReason: {b.reason}\n")
            return

        iface = self._selected_iface()
        if not iface:
            return
        fname = battery.friendly_device_name(iface.vid, iface.pid, iface.product, iface.manufacturer)
        self.lbl_sel.configure(text=f"{fname} ({iface.vid:04X}:{iface.pid:04X})")
        try:
            cur_cname = self.calib_vars["name"].get().strip()
            if not cur_cname or cur_cname == "我的自定义无线外设":
                self.calib_vars["name"].set(fname)
        except Exception:
            pass
        self.txt_ifaces.delete("1.0", "end")
        group = [d for d in self.by_iid.values() if (d.vid, d.pid, d.serial) == (iface.vid, iface.pid, iface.serial)]
        for g in group:
            self.txt_ifaces.insert(
                "end",
                f"up=0x{g.usage_page:04X} u=0x{g.usage:04X} IN={g.input_len} OUT={g.output_len} FEAT={g.feature_len}\n  {g.path}\n\n",
            )

    def read_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self._set_hint("请先在左侧列表选择一个设备或 HID 接口", fg=CORAL, auto_reset_ms=3000)
            return
        iid = sel[0]
        if iid in self.bt_by_iid:
            if not self._begin_hid_busy(self._btn_read_sel, "读取中…"):
                return
            bt_dev = self.bt_by_iid[iid]

            def bt_worker() -> None:
                try:
                    b = bt_battery.refresh(bt_dev)
                    self.ui_queue.put(("bt_read_done", iid, b))
                except Exception as e:
                    self.ui_queue.put(("read_error", iid, str(e)))
            threading.Thread(target=bt_worker, daemon=True).start()
            return

        if iid in self.by_iid:
            target = [self.by_iid[iid]]
        else:
            target = [self.by_iid[c] for c in self.tree.get_children(iid) if c in self.by_iid]
        if not target:
            self._set_hint("当前选中项没有可读取的 HID 子接口", fg=CORAL, auto_reset_ms=3000)
            return
        if not self._begin_hid_busy(self._btn_read_sel, "读取中…"):
            return

        def worker() -> None:
            try:
                res = battery.read_device_battery(target) if len(target) > 1 else battery.read_battery(target[0])
                self.ui_queue.put(("read_done", iid, res))
            except Exception as e:
                self.ui_queue.put(("read_error", iid, str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def cmd_dump_features(self) -> None:
        iface = self._selected_iface()
        if not iface:
            self._set_hint("请先在左侧选择要转储 Feature 的 HID 接口", fg=CORAL, auto_reset_ms=3000)
            return
        if not self._begin_hid_busy(self._btn_dump, "转储中…"):
            return
        self._log_raw(f"[Feature Dump] {iface.vid:04X}:{iface.pid:04X} up=0x{iface.usage_page:04X} feat={iface.feature_len}")

        def worker() -> None:
            try:
                rows = battery.dump_feature_reports(iface)
                self.ui_queue.put(("raw_lines", [f"  RID 0x{rid:02X}: {_fmt_bytes(r)}" for rid, r in rows] or ["  (无有效 Feature Report)"]))
            except Exception as e:
                self.ui_queue.put(("raw_lines", [f"  错误: {e}"]))
            finally:
                self.ui_queue.put(("hid_op_done",))
        threading.Thread(target=worker, daemon=True).start()

    def cmd_probe_rids(self) -> None:
        iface = self._selected_iface()
        if not iface:
            self._set_hint("请先在左侧选择要探测 RID 的 HID 接口", fg=CORAL, auto_reset_ms=3000)
            return
        if not self._begin_hid_busy(self._btn_probe, "探测中…"):
            return
        self._log_raw(f"[Probe RID 1..31] {iface.vid:04X}:{iface.pid:04X} up=0x{iface.usage_page:04X}")

        def worker() -> None:
            try:
                rows = battery.probe_report_ids(iface)
                self.ui_queue.put(("raw_lines", [f"  命中 RID 0x{rid:02X}: {_fmt_bytes(r)}" for rid, r in rows] or ["  (无应答)"]))
            except Exception as e:
                self.ui_queue.put(("raw_lines", [f"  错误: {e}"]))
            finally:
                self.ui_queue.put(("hid_op_done",))
        threading.Thread(target=worker, daemon=True).start()

    def cmd_send_custom(self) -> None:
        iface = self._selected_iface()
        if not iface:
            self._set_hint("请先在左侧选择目标 HID 接口后再发送指令", fg=CORAL, auto_reset_ms=3000)
            return
        rid_txt = self.v_rid.get().strip().replace("0x", "").replace("0X", "")
        cmd_txt = self.v_cmd.get().strip()
        try:
            rid = int(rid_txt, 16)
            if not (0 <= rid <= 0xFF):
                raise ValueError("Report ID 须在 00..FF 范围内")
            cmd = bytes.fromhex(cmd_txt)
        except ValueError as e:
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "格式错误", f"请输入合法十六进制：\n{e}", kind="error"
            )
            return
        if not self._begin_hid_busy(self._btn_send, "发送中…"):
            return
        self._log_raw(f"[Send] RID=0x{rid:02X} CMD={_fmt_bytes(cmd)}")

        def worker() -> None:
            try:
                rs = battery.send_command(iface, rid, cmd)
                self.ui_queue.put(("raw_lines", [f"  RX: {_fmt_bytes(r)}" for r in rs] or ["  (无回复)"]))
            except Exception as e:
                self.ui_queue.put(("raw_lines", [f"  错误: {e}"]))
            finally:
                self.ui_queue.put(("hid_op_done",))
        threading.Thread(target=worker, daemon=True).start()

    def cmd_toggle_monitor(self) -> None:
        if self.monitor_stop is not None:
            self.monitor_stop.set()
            self.monitor_stop = None
            self.btn_mon.configure(text="监听输入报告", dot_color=DIM, fg=FG)
            return
        iface = self._selected_iface()
        if not iface:
            self._set_hint("请先在左侧选择要监听输入报告的 HID 接口", fg=CORAL, auto_reset_ms=3000)
            return
        ev = threading.Event()
        self.monitor_stop = ev
        self.btn_mon.configure(text="停止监听", dot_color=CORAL, fg=CORAL)
        self._log_raw(f"[Monitor Start] up=0x{iface.usage_page:04X} u=0x{iface.usage:04X}")
        threading.Thread(
            target=battery.monitor_input_reports,
            args=(iface, ev, lambda r: self.ui_queue.put(("raw_lines", [f"  IN: {_fmt_bytes(r)}"]))),
            daemon=True,
        ).start()

    def cmd_save_calib(self) -> None:
        iface = self._selected_iface()
        if not iface:
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "提示", "请先在左侧选中目标 HID 接口。", kind="warn"
            )
            return

        def _err(field_key: str, msg: str) -> None:
            self._focus_calib_field(field_key)
            neu_ui.ask_message_dialog(self.winfo_toplevel(), "参数校验失败", msg, kind="error")

        name = self.calib_vars["name"].get().strip() or f"{iface.vid:04X}:{iface.pid:04X}"
        method = self.calib_vars["method"].get().strip() or "output_then_input"
        valid_methods = {"output_then_input", "feature", "input", "output"}
        if method not in valid_methods:
            _err("method", f"通信方式无效：{method}\n可选值：{', '.join(sorted(valid_methods))}")
            return

        rid_raw = self.calib_vars["rid"].get().strip().replace("0x", "").replace("0X", "") or "0"
        try:
            rid_val = int(rid_raw, 16)
            if not (0 <= rid_val <= 0xFF):
                raise ValueError("Report ID 须在 00..FF 范围内")
        except ValueError as e:
            _err("rid", f"Report ID 格式无效（需 16 进制 00~FF）：\n{e}")
            return

        cmd_hex = self.calib_vars["cmd"].get().strip()
        if cmd_hex:
            try:
                bytes.fromhex(cmd_hex)
            except ValueError as e:
                _err("cmd", f"命令字节不是合法的十六进制序列：\n{e}")
                return

        expect_hex = self.calib_vars["expect"].get().strip()
        if expect_hex:
            try:
                bytes.fromhex(expect_hex)
            except ValueError as e:
                _err("expect", f"期望回复前缀不是合法的十六进制序列：\n{e}")
                return

        try:
            lvl_off = int(self.calib_vars["lvl_off"].get().strip() or "0")
            if lvl_off < 0:
                raise ValueError("偏移量不能为负数")
        except ValueError as e:
            _err("lvl_off", f"电量字节偏移无效（须为 >= 0 的整数）：\n{e}")
            return

        try:
            lvl_max = int(self.calib_vars["lvl_max"].get().strip() or "100")
            if lvl_max <= 0 or lvl_max > 65535:
                raise ValueError("电量最大值须在 1..65535 之间")
        except ValueError as e:
            _err("lvl_max", f"电量最大值无效：\n{e}")
            return

        chg_raw = self.calib_vars["chg_off"].get().strip()
        chg_off: Optional[int] = None
        if chg_raw:
            try:
                chg_off = int(chg_raw)
                if chg_off < 0:
                    raise ValueError("充电标志偏移不能为负数")
            except ValueError as e:
                _err("chg_off", f"充电标志偏移无效（须为 >= 0 的整数）：\n{e}")
                return

        try:
            spec = battery.ProtocolSpec(
                name=name,
                vid=iface.vid,
                pid=iface.pid,
                usage_page=iface.usage_page,
                usage=iface.usage,
                method=method,
                report_id=rid_val,
                report_len=max(iface.input_len, iface.output_len, iface.feature_len, 16),
                command_hex=cmd_hex,
                expect_prefix_hex=expect_hex,
                level_offset=lvl_off,
                level_max=lvl_max,
                charging_offset=chg_off,
            )
            out = battery.save_user_protocol(spec)
            self.saved_protocol = True
            if self._on_saved:
                try:
                    self._on_saved()
                except Exception:
                    pass
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "保存成功", f"已写入自定义协议：\n{out}", kind="success"
            )
        except Exception as e:
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "保存失败", f"未能保存自定义协议：{e}", kind="error"
            )

    def cmd_export_protocols(self) -> None:
        all_specs = battery.load_user_protocols()
        if not all_specs:
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "暂无可导出协议", "protocols_user.json 当前为空，请先保存一条自定义协议。", kind="info"
            )
            return
        iface = self._selected_iface()
        to_export = all_specs
        if iface is not None:
            matched = [p for p in all_specs if p.vid == iface.vid and p.pid == iface.pid]
            if matched and len(matched) < len(all_specs):
                only_sel = neu_ui.ask_confirm_dialog(
                    self.winfo_toplevel(),
                    "选择导出范围",
                    f"检测到选中设备 ({iface.vid:04X}:{iface.pid:04X}) 已有 {len(matched)} 条自定义协议。\n\n"
                    f"点击「确定」仅导出选中设备协议；点击「取消」改为导出全部 {len(all_specs)} 条协议。",
                    ok_text="仅导出此设备",
                    cancel_text="导出全部协议",
                )
                if only_sel:
                    to_export = matched
        default_name = (
            f"protocol_{to_export[0].vid or 0:04X}_{to_export[0].pid or 0:04X}.json"
            if len(to_export) == 1
            else "protocols_export.json"
        )
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title="导出自定义协议",
            defaultextension=".json",
            initialfile=default_name,
            filetypes=[("JSON 协议文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            count = battery.export_user_protocols(path, specs=to_export)
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "导出成功", f"已导出 {count} 条协议到：\n{path}", kind="success"
            )
        except Exception as e:
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "导出失败", f"写入协议文件时出错：{e}", kind="error"
            )

    def cmd_import_protocols(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="导入自定义协议 JSON",
            filetypes=[("JSON 协议文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            incoming, errors = battery.parse_protocols_file(path)
        except Exception as e:
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "解析失败", f"所选协议文件无法解析：\n{e}", kind="error"
            )
            return
        if not incoming:
            detail = "\n".join(errors[:5]) if errors else "未在文件中找到合法协议条目。"
            neu_ui.ask_message_dialog(
                self.winfo_toplevel(), "无有效协议", f"未能导入任何协议：\n{detail}", kind="error"
            )
            return

        existing_keys = {battery.protocol_conflict_key(p) for p in battery.load_user_protocols()}
        conflicts = [sp for sp in incoming if battery.protocol_conflict_key(sp) in existing_keys]
        overwrite = True
        if conflicts:
            names_preview = "、".join(
                f"{sp.name}({sp.vid or 0:04X}:{sp.pid or 0:04X})" for sp in conflicts[:4]
            )
            overwrite = neu_ui.ask_confirm_dialog(
                self.winfo_toplevel(),
                "检测到同名设备协议冲突",
                f"本地已存在相同 VID/PID/UsagePage 的 {len(conflicts)} 条协议：\n{names_preview}\n\n"
                "是否用导入文件覆盖现有条目？（取消则保留本地旧协议、仅追加新协议）",
                ok_text="覆盖冲突协议",
                cancel_text="跳过冲突保留旧版",
            )

        res = battery.import_user_protocols(path, overwrite=overwrite)
        self.saved_protocol = self.saved_protocol or (res["imported"] > 0)
        if res["imported"] > 0 and self._on_saved:
            try:
                self._on_saved()
            except Exception:
                pass
        msg = f"成功导入 {res['imported']} 条协议（跳过冲突 {res['skipped']} 条）。"
        if res["errors"]:
            msg += f"\n\n另有 {len(res['errors'])} 条校验失败：\n" + "\n".join(res["errors"][:3])
        neu_ui.ask_message_dialog(
            self.winfo_toplevel(), "导入完成", msg, kind="success" if res["imported"] > 0 else "info"
        )

    def cleanup(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._hide_tree_tip()
        if self.monitor_stop is not None:
            self.monitor_stop.set()
            self.monitor_stop = None
        if self._hint_reset_id is not None:
            try:
                self.after_cancel(self._hint_reset_id)
            except Exception:
                pass
            self._hint_reset_id = None
        for aid in self._after_ids:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._after_ids.clear()

    def _on_destroy(self, event=None) -> None:
        if event is not None and event.widget is not self:
            return
        self.cleanup()

    def _pump(self) -> None:
        if self._closing or not self.winfo_exists():
            return
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]
                if kind == "scan_done":
                    self._set_scan_loading(False)
                    _, hids, bts = item
                    self.tree.delete(*self.tree.get_children())
                    self.by_iid.clear()
                    self.bt_by_iid.clear()
                    target_iid = None
                    groups = battery.group_hid_devices(hids)
                    for (vid, pid, serial), group in sorted(groups.items()):
                        vp = f"{vid:04X}:{pid:04X}"
                        title = battery.friendly_device_name(vid, pid, group[0].product, group[0].manufacturer)
                        parent_iid = self.tree.insert("", "end", text=title, values=(vp, f"{len(group)} 个集合", "", ""), open=False)
                        if self.select_vidpid and self.select_vidpid == vp and target_iid is None:
                            target_iid = parent_iid
                        for g in group:
                            ciid = self.tree.insert(
                                parent_iid,
                                "end",
                                text=g.product or "HID 接口",
                                values=(vp, f"{g.usage_page:04X}:{g.usage:04X}", f"{g.input_len}/{g.output_len}/{g.feature_len}", ""),
                            )
                            self.by_iid[ciid] = g
                    for b in bts:
                        biid = self.tree.insert(
                            "", "end",
                            text=f"[蓝牙] {b.name}",
                            values=("Bluetooth", "-", "-", f"{b.battery}%" if b.battery is not None else "--"),
                        )
                        self.bt_by_iid[biid] = b
                    if target_iid:
                        self.tree.item(target_iid, open=True)
                        self.tree.selection_set(target_iid)
                        self.tree.see(target_iid)
                        self._on_select()
                    last_st = devices.get_last_scan_stats()
                    if hasattr(self, "lbl_scan_stats"):
                        if last_st.ended_at > 0:
                            self.lbl_scan_stats.configure(text=f"最近扫描诊断：{last_st.summary_text()}")
                        else:
                            self.lbl_scan_stats.configure(
                                text=f"调试枚举：HID接口 {len(hids)} · HID分组 {len(groups)} · 蓝牙节点 {len(bts)}"
                            )
                    if not groups and not bts:
                        self._set_hint("未检测到 HID 或蓝牙设备，请检查连接或接收器状态后重新扫描", fg=MUTED)
                    else:
                        self._set_hint(f"已扫描 {len(groups)} 组 HID 设备、{len(bts)} 个蓝牙节点 · {self._hint_default}", fg=MUTED)
                elif kind == "scan_error":
                    self._set_scan_loading(False)
                    self._set_hint(f"设备扫描失败：{item[1]}", fg=CORAL, auto_reset_ms=5000)
                elif kind == "bt_read_done":
                    self._end_hid_busy()
                    _, iid, b = item
                    if iid in self.bt_by_iid:
                        self.bt_by_iid[iid] = b
                    lvl = f"{b.battery}%" if b.battery is not None else "--"
                    if self.tree.exists(iid):
                        self.tree.set(iid, "level", lvl)
                    self.lbl_big.configure(text=lvl, fg=_battery_fg(b.battery))
                    self.lbl_stat.configure(text=b.reason)
                elif kind == "read_done":
                    self._end_hid_busy()
                    _, iid, res = item
                    lvl = f"{res.level}%" if res.level is not None else "--"
                    if self.tree.exists(iid):
                        self.tree.set(iid, "level", lvl)
                    num_col = _battery_fg(res.level, charging=res.charging, asleep=res.asleep)
                    self.lbl_big.configure(text=lvl, fg=num_col)
                    self.lbl_stat.configure(text=res.detail or res.error)
                    self.txt_diag.delete("1.0", "end")
                    self.txt_diag.insert(
                        "end",
                        f"来源: {res.source or '-'}\n连接: {res.connection or '-'}\n电量: {lvl}\n"
                        f"充电: {res.charging}\n休眠: {res.asleep}\n原始报文: {res.raw_hex or '-'}\n说明: {res.detail or res.error}\n",
                    )
                elif kind == "read_error":
                    self._end_hid_busy()
                    _, _iid, err = item
                    self.lbl_stat.configure(text=f"读取异常：{err}")
                    self._set_hint(f"读取选中项失败：{err}", fg=CORAL, auto_reset_ms=4000)
                elif kind == "hid_op_done":
                    self._end_hid_busy()
                elif kind == "raw_lines":
                    for ln in item[1]:
                        self._log_raw(ln)
        except queue.Empty:
            pass
        if not self._closing and self.winfo_exists():
            self._after_ids.append(self.after(100, self._pump))


class DebugApp:
    def __init__(self, root: tk.Tk, select_vidpid: str = "") -> None:
        cfg = tray_common.load_config()
        neu_ui.apply_theme(str(cfg.get("theme_mode", "light")))
        neu_ui.set_font_scale_mode(str(cfg.get("font_scale_mode", "standard")))
        _sync_palette_globals()
        self.root = root
        self.root.title("HID / 2.4G 外设调试与协议标定工具")
        self.root.configure(bg=neu_ui.BASE)
        neu_ui.fit_geometry(self.root, 1140, 770, 920, 600)
        ico = tray_common.app_icon_path()
        if ico:
            try:
                self.root.iconbitmap(ico)
            except Exception:
                pass
        neu_ui.enable_frameless_taskbar(self.root, round_corners=True)

        self.titlebar = neu_ui.NeuTitleBar(
            self.root,
            title="HID / 2.4G 外设调试与协议标定工具",
            subtitle="接口遍历 · 原始探测 · 协议标定",
            allow_maximize=True,
            icon_role="other",
        )
        self.titlebar.pack(fill="x")
        neu_ui.apply_window_rounded_corners(self.root, rounded=not self.titlebar.is_maximized())

        self.workspace = DebugWorkspaceFrame(
            self.root,
            select_vidpid=select_vidpid,
            compact=False,
            show_header=True,
        )
        self.workspace.pack(fill="both", expand=True)
        neu_ui.enable_frameless_resize(
            self.root,
            min_w=neu_ui.sx(920, self.root),
            min_h=neu_ui.sx(600, self.root),
            border_px=5,
            is_maximized=self.titlebar.is_maximized,
        )


def main() -> int:
    ap = argparse.ArgumentParser(description="HID / 2.4G 外设协议调试与标定工具")
    ap.add_argument("--list", action="store_true", help="列出全部 HID 接口")
    ap.add_argument("--battery", action="store_true", help="尝试读取并输出所有支持设备的电量")
    ap.add_argument("--bt", action="store_true", help="列出蓝牙 HID/免提节点的电量属性")
    ap.add_argument("--dump", type=str, default="", help="转储指定 VID:PID 的全部 Feature Reports")
    ap.add_argument("--select", type=str, default="", help="启动 GUI 并定位到指定 VID:PID")
    args = ap.parse_args()

    if args.list or args.battery or args.bt or args.dump:
        return run_cli(args)

    root = tk.Tk()
    root.withdraw()
    DebugApp(root, select_vidpid=args.select)
    try:
        root.update_idletasks()
        root.attributes("-alpha", 0.0)
        root.deiconify()
        neu_ui.enable_frameless_taskbar(root, round_corners=True)
        neu_ui.fade_in_window(root, steps=7, step_ms=14)
    except Exception:
        root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
