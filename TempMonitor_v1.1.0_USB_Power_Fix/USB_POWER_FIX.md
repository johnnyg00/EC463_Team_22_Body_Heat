# USB power-only firmware fix — November 17, 2026

## What happened

Moving the board's USB cable to a second computer supplying power produced intermittent BLE discovery, a stored alert snapshot, and no continuous sensor notifications. The receiver disconnected after its normal 15-second timeout. Only one BLE receiver was active.

The earlier sketch printed diagnostics over USB unconditionally. USB transmit backpressure can block those writes and delay the same loop that samples sensors, publishes notifications, and restarts advertising. This is a strong explanation for the observed log; the exact physical cause still needs confirmation with the retest below.

## What changed

- USB transmit timeout is zero; the sketch no longer waits for a Serial Monitor at startup.
- Each diagnostic line is formatted in a bounded buffer and attempted only when the port is connected and has enough room for the whole line.
- A blocked/unavailable USB destination drops diagnostic lines. There is one write attempt and no flush, wait-for-space, or short-write retry.
- USB command processing is limited per loop, and only positive available-byte counts are processed. `r`/`R` still resets alert history.
- BLE characteristics, packet formats, receiver, sensor configuration, and the 29°C demo alert threshold are unchanged.

## Install once, then test

1. Stop the Bluetooth receiver with Ctrl+C. Connect the board to the computer used for uploading.
2. Extract this updated ZIP. Open `XIAO_Temperature_BLE/XIAO_Temperature_BLE.ino`, keeping **all five accompanying `.hpp` files**, including the new `serial_log.hpp`, in that folder.
3. Select XIAO_ESP32S3, its USB port, and USB CDC On Boot enabled. Verify and upload, as in [FIRMWARE_SETUP.md](FIRMWARE_SETUP.md).
4. Close Serial Monitor. Move USB power back to your friend's computer, with no Arduino software or serial reader running there.
5. Start the existing receiver on your laptop. No new Python dependencies or receiver changes are required.
6. Watch for continuously advancing BLE sequence/uptime values and roughly one sensor report per second for at least two minutes. Valid alert decisions should arrive about every ten seconds. Warm TMP117 above 29°C, then cool it, to confirm the absolute flag still activates and clears.
7. Stop/restart the receiver and confirm it reconnects to the same board. A full five-minute continuous run checks that trend history can become ready. Repeated history restarts would indicate another interruption to investigate.

If the same symptom persists after this upload, capture both the receiver log and USB serial output from a separate diagnostic run. A successful build and host tests do not prove the power-only hardware test passed.

The change is confined to optional USB diagnostics/command handling; no change to the teammate's BLE decoder or hub mapping is needed.
