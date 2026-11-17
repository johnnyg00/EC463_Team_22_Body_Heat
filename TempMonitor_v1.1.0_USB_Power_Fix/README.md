# TempMonitor v1.1.0 — teammate quick start

**USB power-only update (November 17):** if you uploaded an earlier version, upload this package’s sketch once before using a computer only as a power source. The firmware now skips USB diagnostic output when it cannot be sent immediately. The BLE receiver and protocol are unchanged. See [USB_POWER_FIX.md](USB_POWER_FIX.md).

**After uploading this version, the device is designed to run whenever it has USB power.** The separate-computer power-only retest is still pending. It keeps its firmware after unplugging; no upload is needed to use another laptop. It starts the sensors, onboard alert logic, and Bluetooth automatically. A laptop is needed to receive/display the data. Trend history starts over after a power cycle.

This package is for the XIAO ESP32S3 + TMP117 + SHT45. The absolute alert is currently **TMP117 >29°C for the demo**. Hub integration and physical vibration actuation are separate work.

## Start receiving on your computer

1. Extract `TempMonitor_v1.1.0_USB_Power_Fix.zip`, or clone the team repository. Keep the board powered and Bluetooth enabled. Stop the receiver on any other computer before connecting.
2. Install [Python](https://www.python.org/downloads/) **3.10 or newer** if needed. Supported Bleak platforms are macOS 10.15+, Windows 11 build 22000+, or Linux with BlueZ 5.55+. The live setup so far is a Mac; Windows/Linux hardware operation still needs checking.
3. Open Terminal or PowerShell **inside the extracted folder containing `receiver.py` and `requirements.txt`**. Run the commands for your platform:

**Mac / Linux**

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python receiver.py
```

**Windows PowerShell**

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe receiver.py
```

If Python reports a version below 3.10, install a newer version before continuing. If Windows does not recognize `py`, use `python` instead after confirming its version. These commands do not require activating the environment or changing PowerShell execution policy.

On Mac, allow your terminal application's Bluetooth access. If denied, enable it under **System Settings → Privacy & Security → Bluetooth** and restart the terminal.

The receiver finds **TempMonitor** and connects directly over BLE; manual pairing in Bluetooth Settings is not required. Allow roughly 8 seconds for scanning. Press **Ctrl+C** to stop. For later sessions, open this folder and run only the final command above.

## Confirm operation

- Temperatures/humidity arrive about once per second: `TMP117_C=23.29  SHT45_C=23.60  RH_pct=23.2`.
- Alert updates normally arrive every 10 seconds: `[ALERT] ABS=OFF  TREND=WARMUP ...`.
- Gently warm the TMP117 above 29°C and hold it there for about 20 seconds; `ABS=ON` should appear. Let it cool to 29°C or below to clear it.
- Trend detection needs 31 accepted samples spanning five minutes. `WARMUP` is expected initially.
- `WAIT`, `ERR`, `FAULT`, or `STALE` means data/decisions may be unavailable; these are not zero temperatures or an all-clear.

USB may supply power while data travels over Bluetooth. For a wireless demonstration, a suitable USB power supply can power the board independently of the receiving laptop.

## If it does not connect

Check power, Bluetooth, permissions, and whether another receiver/scanner is connected. For multiple boards, add `--list` to the receiver command, then run with `--device "ID_FROM_LIST"`. Use the ID printed on **that computer**; Mac IDs are UUIDs. A hub and this test receiver should not compete for the same board. The receiver retries after disconnects.

## Files for the team

| File | Purpose |
|---|---|
| [BLE_PROTOCOL.md](BLE_PROTOCOL.md) | Exact UUIDs, packet layouts, units, flags, timing, and decoder examples |
| [HUB_INTEGRATION.md](HUB_INTEGRATION.md) | How to connect the hub, map data, handle faults/reconnects, and test the integration |
| [receiver.py](receiver.py) + [requirements.txt](requirements.txt) | Working receiver and reusable Python packet decoders |
| [FIRMWARE_SETUP.md](FIRMWARE_SETUP.md) | Wiring, Arduino installation/upload, configuration, and reset behavior |
| [USB_POWER_FIX.md](USB_POWER_FIX.md) | One-time firmware update and power-only regression check |
| [XIAO_Temperature_BLE/](XIAO_Temperature_BLE/) | Complete firmware source; keep the sketch and all headers together |
| [VALIDATION.md](VALIDATION.md) + [tests/](tests/) | Build results, decoder/alert tests, and remaining hardware checks |

**Software teammate:** start with `HUB_INTEGRATION.md`, then implement against `BLE_PROTOCOL.md`. The current receiver prints to Terminal; it does not automatically forward data to the team's hub.

Platform/permission references: [Bleak support](https://bleak.readthedocs.io/en/latest/#operating-system-support), [macOS Bluetooth permissions](https://bleak.readthedocs.io/en/latest/backends/macos.html).

This is ready for source review and hub-adapter development. The power-only hardware retest and the actual hub API integration are still outstanding. The 29°C demo setting and remaining physical vibration work are documented in the integration guides.
