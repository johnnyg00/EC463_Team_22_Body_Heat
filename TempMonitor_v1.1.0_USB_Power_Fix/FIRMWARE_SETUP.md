# Firmware and hardware reference

For a board running an earlier package, upload the USB power-only fix described in [USB_POWER_FIX.md](USB_POWER_FIX.md) once. The assembled board otherwise retains its installed firmware. These instructions are for rebuilding, changing configuration, or setting up another board. Receiving data on another computer only needs the [README](README.md) steps.

## Hardware and wiring

- Seeed Studio XIAO ESP32S3 with its attached Molex 146153 antenna.
- Adafruit TMP117 contact-temperature breakout, I2C address `0x48`.
- Adafruit SHT45 temperature/humidity breakout, I2C address `0x44`.
- Both breakouts share the same I2C bus through their STEMMA QT connectors.

| STEMMA QT lead | XIAO connection | Function |
|---|---|---|
| Red | 3V3 | Breakout VIN power |
| Black | GND | Common ground |
| Blue | D4 / GPIO5 | SDA |
| Yellow | D5 / GPIO6 | SCL |

Disconnect power before changing wiring. The Molex part is the antenna; the ESP32S3 provides the BLE radio. This firmware uses BLE GATT, not Bluetooth Classic serial/SPP.

## Rebuild and upload

1. Install Arduino IDE. Follow [Seeed's XIAO ESP32S3 setup](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/) to install the **esp32 by Espressif Systems** board package.
2. Install **Adafruit SHT4x Library** and its **Adafruit BusIO** / **Adafruit Unified Sensor** dependencies through Library Manager. TMP117 access is implemented directly through `Wire`; this sketch does not require the Adafruit TMP117 library. BLE is included in the ESP32 core; no separate ArduinoBLE library is required.
3. Extract the ZIP and open `XIAO_Temperature_BLE/XIAO_Temperature_BLE.ino`. Keep all accompanying `.hpp` files in the same sketch folder.
4. Connect the board with a USB data cable. Select board **XIAO_ESP32S3** and the matching USB port. Set **USB CDC On Boot: Enabled** and use **Hardware CDC and JTAG** USB mode where shown.
5. Verify, then Upload. Open Serial Monitor at **115200 baud** for startup/sensor messages. `Hard resetting via RTS pin...` is normal upload output; ongoing sensor output means the sketch is already running.
6. Start the laptop receiver from `README.md` and verify both sensor and alert messages.

Validated build: Espressif core **3.3.13**, SHT4x **1.0.5**, BusIO **1.17.4**, Unified Sensor **1.1.15**; FQBN `esp32:esp32:XIAO_ESP32S3`. Full results are in [VALIDATION.md](VALIDATION.md).

If the IDE says no board was found, select **XIAO_ESP32S3** manually from the installed Espressif package and choose the USB port separately. A connected USB cable alone does not identify/install the board package. The startup scan should find `0x44` and `0x48`; missing addresses indicate a power/wiring/sensor issue.

## Alert configuration and timing

Both alerts use TMP117 readings. SHT45 supplies ambient context only.

| Behavior | Current configuration |
|---|---|
| Absolute ON | TMP117 strictly greater than 29.0°C |
| Absolute OFF | TMP117 at or below 29.0°C |
| Decision cadence | First valid sample, then normally every 10 seconds |
| Trend ON | Least-squares slope greater than 0.06°C/min over 300 seconds, subject to the core's numerical tolerance |
| Trend warmup | 31 accepted samples at 10-second intervals |
| Reset trend | Send `r` or `R` through USB Serial Monitor |

The flags are independent and not latched. An absolute change may take up to the next 10-second evaluation. Warming a room-temperature sensor creates a rising trend; it is not a core-body temperature measurement.

Change `DEMO_ABSOLUTE_LIMIT_C` in the `.ino` to change the threshold, then rebuild/upload. Its value is **29.0** now; **38.0** restores the original module's absolute default. The copied `alert_module.hpp` itself is unchanged. The device transmits its configured absolute threshold in every alert packet. There is no BLE command to change thresholds or reset history. The startup banner and transmitted threshold both follow that constant.

TMP117 is polled every 250 ms. A successful value may be reused for up to 1.5 seconds while the next conversion is pending. A hard read error invalidates it immediately. Stale/failed input invalidates alert decisions; missed scheduled samples restart trend history when sampling resumes. Slope output is rounded for transport; the onboard decision uses the original calculation.

## Power and persistence

Firmware remains installed when unplugged. With USB power from a laptop or suitable USB supply, the sketch starts automatically after its brief startup sequence; Serial Monitor is not required by the updated sketch. USB diagnostic lines are skipped if the port is unavailable or the transmit buffer cannot accept a whole line immediately; missed debug lines are not replayed. Startup messages may be missed when Serial Monitor opens later; periodic readings will still appear when the port can accept them. A power cycle clears RAM, including counters and trend history. Sending `r` clears alert history/cache without reinstalling firmware; the next fresh reading may activate the absolute alert again.

Disconnecting the laptop's Bluetooth does not stop onboard sensing or alert calculations. Reconnection resumes current values. The board does not store a historical log or replay missed readings. The receiver displays values; persistent logging must be supplied by the hub or another application.

The current code reports alert flags through USB/BLE. It does **not** drive a vibration motor, implement the team's hub API, or configure BLE pairing/encryption/application authentication.

References: [Seeed board setup](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/), [Espressif USB CDC](https://docs.espressif.com/projects/arduino-esp32/en/latest/tutorials/cdc_dfu_flash.html), [Espressif BLE](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/ble.html).
