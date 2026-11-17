# TempMonitor BLE protocol — software hub handoff

This document describes the firmware in this package, dated 2026-11-17. The
source of truth is `XIAO_Temperature_BLE/*.hpp` and `.ino`; `receiver.py` contains
working Python decoders and a connection example. Packet version is **1** for
both characteristics. The absolute threshold in this demo is **29.00 °C**.

## Connection contract

| Item | Value |
| --- | --- |
| Peripheral name | `TempMonitor` |
| Service UUID | `71f10000-7a42-4d32-9ef4-01352c8f9a10` |
| Sensor characteristic UUID | `71f10001-7a42-4d32-9ef4-01352c8f9a10` |
| Alert characteristic UUID | `71f10002-7a42-4d32-9ef4-01352c8f9a10` |
| Both characteristic properties | READ and NOTIFY |
| Transport | Bluetooth Low Energy GATT; binary values |
| Incoming BLE commands | None; characteristics are not writable |
| Pairing/security | No pairing or encryption configured in this bench demo |

The ESP32 is the peripheral/server; the laptop or hub is the central/client.
Scan for the **service UUID**, connect, discover both characteristics, then
enable notifications on each. The service UUID is in the advertisement and the
name is in the scan response. Do not require the name to identify a candidate.
Both packets fit in the default 20-byte characteristic notification payload;
no custom MTU negotiation, byte-stream assembly, or newline parsing is needed.
Each notification is one complete packet for that characteristic.

Use one active central at a time. Stop the demonstration receiver before the
hub connects. Firmware restarts advertising approximately 500 ms after a
disconnect; client scan/connect time adds to recovery time. Sensor acquisition
and alert calculations continue when no central is connected.

If several boards advertise this service, require explicit device selection.
`receiver.py --list` prints candidates, and `--device ID` selects one. macOS IDs
are CoreBluetooth UUIDs, not Bluetooth MAC addresses. Do not assume an ID copied
from one computer identifies the board on another computer. There is no unique
device ID, user ID, boot ID, firmware revision, or battery field in these packets;
the hub must maintain its own device association and connection session ID.

## Sensor packet: 14 bytes

All multibyte fields are **little-endian**, with no padding. Python format:
`<BBHIhhH`. Signed fields use two's complement.

| Offset | Bytes | Type | Field | Meaning |
| --- | --- | --- | --- | --- |
| 0 | 1 | uint8 | version | Must be `1` |
| 1 | 1 | uint8 | flags | Sensor status bits below |
| 2 | 2 | uint16 | sequence | Sensor stream sequence, wrapping modulo 65536 |
| 4 | 4 | uint32 | uptime_ms | Board uptime in milliseconds, modulo 2³² |
| 8 | 2 | int16 | tmp117_centi_c | Divide by 100 for TMP117 °C |
| 10 | 2 | int16 | sht45_centi_c | Divide by 100 for SHT45 °C |
| 12 | 2 | uint16 | rh_centi_pct | Divide by 100 for relative humidity percent |

| Bit | Mask | Meaning when set |
| --- | --- | --- |
| 0 | `0x01` | TMP117 temperature is valid |
| 1 | `0x02` | Both SHT45 temperature and humidity are valid |
| 2 | `0x04` | TMP117 is awaiting a conversion and has no usable recent value |
| 3–7 | `0xF8` | Reserved; reject if any are set for version 1 |

Temperature sentinel: **−32768** (`00 80`). Humidity sentinel: **65535** (`ff ff`).
Check status before scaling; invalid data must become `null`/unavailable, never
zero or a physical measurement. TMP117 cannot be valid and WAIT simultaneously.
TMP117 with neither valid nor WAIT means error/unavailable. SHT45 has one shared
validity bit for temperature and humidity.

The reference decoder rejects a packet unless:

- Its length is exactly 14, version is 1, and reserved bits are clear.
- A valid TMP117 value is in −55.00…150.00 °C; an invalid value has its sentinel.
- Valid SHT45 values are in −45.00…130.00 °C and 0.00…100.00% RH; when invalid,
  both fields have their sentinels.
- TMP117 valid and WAIT are not both set.

Sensor notifications are produced approximately once per second. TMP117 is
polled every 250 ms and uses continuous one-second conversions. A successful
reading can be reused for up to 1500 ms; a transient conversion-pending response
does not discard that recent value. A hard read error discards it immediately.
Consequently, TMP117 valid means a checked recent reading, not necessarily a new
conversion exactly at notification time. Its precise acquisition time/age is
not included in the BLE packet. SHT45 is read for each sensor report.

## Alert packet: 19 bytes

Little-endian, no padding; Python format: `<BBHIIhiB`.

| Offset | Bytes | Type | Field | Meaning |
| --- | --- | --- | --- | --- |
| 0 | 1 | uint8 | version | Must be `1` |
| 1 | 1 | uint8 | flags | Alert state bits below |
| 2 | 2 | uint16 | sequence | Independent alert event sequence, modulo 65536 |
| 4 | 4 | uint32 | report_uptime_ms | Time the board generated this event, modulo 2³² |
| 8 | 4 | uint32 | sample_uptime_ms | Software snapshot time of the accepted alert input |
| 12 | 2 | int16 | threshold_centi_c | Absolute threshold ×100; demo value `2900` |
| 14 | 4 | int32 | slope_milli_c_per_min | Fitted slope ×1000; divide by 1000 for °C/min |
| 18 | 1 | uint8 | sample_count | Accepted samples in the current trend window |

| Bit | Mask | Meaning when set |
| --- | --- | --- |
| 0 | `0x01` | Absolute alert active |
| 1 | `0x02` | Trend alert active |
| 2 | `0x04` | Trend window ready |
| 3 | `0x08` | Alert decision valid |
| 4 | `0x10` | Sensor/input fault; decision unavailable |
| 5 | `0x20` | Trend history restarted because the accepted cadence was interrupted |
| 6–7 | `0xC0` | Reserved; reject if set for version 1 |

Alert interpretation must start with the **valid** bit:

| State | Hub display/behavior |
| --- | --- |
| Valid=0, fault=0 | Awaiting a sample, such as startup or manual history reset |
| Valid=0, fault=1 | Decision unavailable due to a fault; do not display “all clear” |
| Valid=1, ready=0 | Absolute decision is usable; trend is warming up |
| Valid=1, ready=1 | Both absolute and trend decisions are usable |
| No recent live event | Previous decision is stale; do not present it as current |

For valid decisions, sample count is 1…31 and ready is true exactly when count
is 31. Fault must be false. Trend cannot be active before ready. Slope must be
available exactly when ready. The slope sentinel is **−2147483648**
(`00 00 00 80`). Threshold sentinel −32768 is rejected by the reference decoder.

For invalid decisions, absolute, trend, ready, and restarted must be false;
count must be zero; sample time must be `0xFFFFFFFF`; and slope must have its
sentinel. Fault may be either true or false. **A valid decision may legitimately
have sample time `0xFFFFFFFF` near clock wrap**: distinguish this using valid,
not the timestamp alone. The reference decoder also rejects a valid sample
that is later than its report using unsigned modular subtraction:
`((report - sample) & 0xFFFFFFFF) >= 0x80000000`.

### Alert algorithm and cadence

- **Input:** TMP117 only. SHT45 temperature and humidity are telemetry and do
  not affect either alert in this firmware.
- **Absolute:** ON when accepted TMP117 temperature is strictly **greater than
  29.0 °C**; OFF at or below 29.0 °C. No five-minute warmup is needed. The original
  core default was 38.0 °C; this sketch explicitly overrides it to 29.0 °C.
- **Trend:** ON when the least-squares temperature slope over 300 seconds is
  greater than 0.06 °C/min. A numerical 1e−9 °C/min tolerance handles equality;
  it is not physical hysteresis. A full window requires **31 samples spaced
  10 seconds apart**. Subsequent accepted samples maintain a rolling window.
- **Evaluation:** The first fresh input is accepted as soon as available;
  subsequent input snapshots are normally accepted every 10 whole seconds.
  Allow roughly 10 seconds for a temperature change to affect an alert.
- **Freshness/faults:** The accepted source reading must be at most 1500 ms old.
  Hard errors or expired data invalidate the published decision immediately
  when the firmware observes them. A fault event is sent once on entry; a
  persistent fault does not generate repeated alert notifications.
- **Missing samples:** There is no invented catch-up data. When the next
  accepted input has a cadence gap, trend history restarts and count returns
  to 1; the absolute check still runs on that input. A brief fault between
  scheduled samples does not necessarily restart the trend window.
- **Reset:** Sending `r` or `R` over USB Serial clears alert history and waits for
  a fresh input. This is not a BLE command. Absolute can become ON again on the
  next accepted reading if the sensor is still above threshold.
- **No latching:** Alerts reflect the latest valid accepted input/window. They
  do not remain ON until acknowledged, and this firmware drives no vibration
  motor GPIO.

The alert value is updated and notified for each accepted sample, fault
transition, and manual reset. Sensor and alert sequences are independent.
`report_uptime_ms` and `sample_uptime_ms` are equal for current normal accepted
events. Sample time is the software snapshot time, not the exact sensor
conversion time. Source age is printed on USB Serial but is not sent over BLE.

Use the transmitted alert flags as the device decision. The displayed BLE
temperature is rounded to 0.01 °C and slope to 0.001 °C/min; the device makes its
decision using greater precision. Recomputing a threshold from rounded values
can disagree near a boundary. In particular, a displayed slope of 0.060 can
coexist with trend ON when the unrounded slope is just above the threshold.

## Reads, notifications, timestamps, and loss

A READ returns the **stored latest value**, including an initial unavailable
state before acquisition. It does not trigger a measurement and does not prove
the device is producing new data. On connection, the reference receiver reads
the alert characteristic **before subscribing** and labels it a stored snapshot;
later notifications establish live updates. Reading before subscribing also
avoids CoreBluetooth read/notification callback ambiguity. There is no backlog
replay after reconnect, and an initial read/subscribe transition can race with a
new event; use subsequent live updates as authoritative.

Track receipt time using a host monotonic clock for timeout decisions, and a
host UTC wall-clock timestamp for logs. Board uptime is not a calendar time.
The transmitted 32-bit millisecond clock wraps about every 49.7 days; sequences
wrap every 65536 generated reports/events. Internal alert scheduling uses a
64-bit clock. Sequences and uptime restart on reboot, but BLE disconnect alone
does not reset them or the trend window.

Use `(sequence, uptime_ms)` to ignore an immediately repeated sensor packet and
`(sequence, report_uptime_ms)` for alert duplicates, as in `receiver.py`. A gap
can indicate missed notifications or time while disconnected; sequence numbers
advance even without a client. Do not assume sequence starts at zero when you
connect. Create a new host connection session on reconnect and do not infer
an unambiguous reboot from one wrapped value; the protocol has no boot ID.

The reference receiver uses these application policies, which the hub can adopt:

- No new well-formed, nonduplicate sensor packet for 5 s: show stale data.
- No such sensor packet for 15 s: disconnect and reconnect to the same board.
- No new alert event for 15 s after a valid decision: show stale alert state,
  even if sensor packets continue arriving.
- Invalid/fault alert states remain unavailable until a new valid event; a
  persistent fault does not need repeated events to stay a fault.

Transport liveness and measurement validity are separate. A correctly formed
sensor packet containing ERR/WAIT still establishes transport activity; it
does not establish valid temperature data. Keep separate last-received and
last-valid-measurement timestamps, and never let temperature traffic refresh
the age of an old alert decision.

## Verified decoder examples

These are synthetic protocol fixtures, not captured measurements. Each was
decoded successfully with the supplied `receiver.py`.

**Sensor, both valid:** sequence 42, uptime 123456 ms, TMP117 29.34 °C,
SHT45 23.61 °C, RH 23.20%.

```text
01 03 2a 00 40 e2 01 00 76 0b 39 09 10 09
```

**Sensor, TMP117 WAIT:** sequence 43, uptime 124456 ms, TMP117 unavailable,
SHT45 23.61 °C, RH 23.20%.

```text
01 06 2b 00 28 e6 01 00 00 80 39 09 10 09
```

**Alert, absolute ON during trend warmup:** sequence 7, report/sample 123500 ms,
threshold 29.00 °C, count 4, slope unavailable, valid=true.

```text
01 09 07 00 6c e2 01 00 6c e2 01 00 54 0b 00 00 00 80 04
```

**Alert, both ON with full trend window:** sequence 31, report/sample 300000 ms,
threshold 29.00 °C, slope 0.080 °C/min, count 31, valid=true.

```text
01 0f 1f 00 e0 93 04 00 e0 93 04 00 54 0b 50 00 00 00 1f
```

**Alert, fault:** sequence 32, report 301500 ms, threshold 29.00 °C;
sample/slope unavailable, count 0, valid=false, fault=true.

```text
01 10 20 00 bc 99 04 00 ff ff ff ff 54 0b 00 00 00 80 00
```

**Alert, manual reset/awaiting input:** sequence 33, report 302000 ms,
threshold 29.00 °C; sample/slope unavailable, count 0, valid=false, fault=false.

```text
01 00 21 00 b0 9b 04 00 ff ff ff ff 54 0b 00 00 00 80 00
```

For Python hub code, import `decode_packet` and `decode_alert_packet` from
`receiver.py` and pass the notification bytes into the appropriate decoder.
They return typed dataclasses and raise `ValueError` for malformed input.
Do not scrape `format_reading()` or the human-readable console log; those
strings are a display layer, not the wire protocol. Decoder tests are in
`tests/test_receiver.py`; firmware-side packet checks are in
`tests/test_packet.cpp` and `tests/test_alert_packet.cpp`.

## Remaining hub integration decisions

The existing hub's language, ingest API, storage format, and device-association
scheme have not been supplied. This package provides the device contract and
a working BLE client; it does not claim compatibility with an unknown hub API.
The software teammate should map decoded readings and alert states into that
hub, preserve unavailable/stale states, select the correct physical device,
and agree on the final absolute threshold before replacing the 29 °C demo value.
