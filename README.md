# PeripheralBatteryViewer —— Windows 2.4G 无线外设电量监视器

纯 Python 标准库 + `ctypes` + `tkinter` 实现（**零第三方依赖**，无需安装 `hidapi` / `pystray` / `Pillow`）。

## 启动方式

- **主面板**：双击 `我的设备电量.bat` 或运行 `E:\Python\pythonw.exe my_devices.py`
- **命令行快查**：`E:\Python\python.exe my_devices.py --once`（加 `--json` 输出 JSON）
- **系统托盘驻留**：双击 `启动托盘后台.bat` 或运行 `E:\Python\pythonw.exe tray_app.py`（支持 `--status` / `--refresh` / `--quit` / `--debug`）
- **协议调试与手动标定**：双击 `外设调试与标定.bat` 或运行 `E:\Python\pythonw.exe app.py`（支持 `--list` / `--battery` / `--bt` / `--dump VID:PID`）

## 已支持协议

1. **迈从 MCHOSE 鼠标（`3837:100C` A5 Ultra 等）**：`0xFF01` 65 字节 Feature Report `0x11/0x06`（兼容明文 `0x06` 与 RealTek 按位取反 `0xF9` 响应，带休眠检测）。
2. **SinoWealth / MCHOSE 20B 无线键盘（`258A:01BB`、`3554:FA09` 等）**：`0xFF02` 20 字节 `0x13 0x05/0x4A` 帧与字节求和校验。
3. **飞智 Flydigi 手柄（`37D7:2502` APEX 6 Pro 等）**：`0xFFA0` 33 字节 `5A A5 01` 查询帧，自动识别有线/2.4G/蓝牙连接模式。
4. **通用 2.4G 与蓝牙外设兼容**：ATK / VXE / VGN (`373B:*`/`3554:*`)、罗技 HID++ 2.0 (`046D:*`)、DARKRIM / 杰理 RCSP (`3654:6901` 等)、标准 HID 电量用法 (`0x0006/0x0020`、`0x0085`)、Windows 蓝牙 HFP PnP 节点，以及通过 `protocols_user.json` 手动标定的任意 HID 设备。
