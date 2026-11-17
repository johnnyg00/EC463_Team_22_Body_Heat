# Software hub integration handoff

This package provides the device firmware, a working Python BLE receiver, and the wire protocol. The existing hub's language, API, and deployment environment were not available, so this guide identifies where its adapter belongs. No compatibility with that hub has been verified yet.

Start with [README.md](README.md) to run the receiver. Use [BLE_PROTOCOL.md](BLE_PROTOCOL.md) for exact bytes and [FIRMWARE_SETUP.md](FIRMWARE_SETUP.md) only when changing or uploading firmware. [VALIDATION.md](VALIDATION.md) records build and host-test coverage.

## Connection model

The XIAO is a BLE peripheral/GATT server. The computer running the hub or a local collector is the BLE central/client. The board advertises as `TempMonitor`; its Molex antenna is part of the radio connection, not a separate receiver.

If the hub runs remotely, run a collector on a nearby Bluetooth-capable computer and forward decoded events through the hub's existing API. This firmware does not send directly to an Internet/cloud server. Choose the forwarding transport, authentication, and destination from the actual hub requirements; none are configured in this package.

| Interface | UUID | Use |
|---|---|---|
| Advertised service | `71f10000-7a42-4d32-9ef4-01352c8f9a10` | Discovery filter |
| Sensor characteristic | `71f10001-7a42-4d32-9ef4-01352c8f9a10` | Read/Notify; 14-byte sensor packet |
| Alert characteristic | `71f10002-7a42-4d32-9ef4-01352c8f9a10` | Read/Notify; 19-byte alert packet |

There is no writable characteristic, remote threshold setting, or BLE reset command. Pairing, BLE encryption, and application authentication are not configured in this bench demo. The radio identity and received measurements therefore are not authenticated. Physical vibration actuation is also not implemented; the current outputs are data and alert decisions.

## Implement the adapter

1. Scan for the **service UUID**, not only the display name. The name is in the scan response and can be absent from an initial advertisement.
2. Select the intended board explicitly if several match. `receiver.py --list` prints identifiers; `--device ID` selects one. On macOS the identifier is a CoreBluetooth UUID, not a MAC address. Store a user-assigned project device ID and the local Bluetooth identifier separately: the firmware has no unique project device ID in its payload, and identifiers are not guaranteed to match across computers.
3. Connect, discover the service, and require both characteristics for the integrated sensor-and-alert application. The supplied receiver tolerates an older sensor-only board, but a hub that needs alerts should show **unsupported firmware / alerts unavailable** when the alert characteristic is absent.
4. Subscribe to notifications on **both** characteristics. Connection alone does not start client delivery. With Bleak, use `await client.start_notify(characteristic, callback)`; it handles notification subscription. Keep one central connected for this demo, closing the standalone receiver before connecting the hub.
5. Decode and validate each complete notification using the matching characteristic's format. These are binary packets, not UTF-8, CSV, or the text printed in Serial Monitor. Both fit in the default ATT notification payload; no application fragmentation is used.
6. Normalize decoded values into the hub's data model, timestamp receipt, and enqueue them for the application. Keep callbacks short; perform database writes, HTTP requests, and other potentially blocking work outside the callback.
7. On disconnect, mark both streams unavailable/stale, clear connection-specific freshness tracking, and reconnect to the selected board. Resubscribe after every connection. Do not silently select another nearby `TempMonitor`.

An optional initial alert **read** can populate a clearly labeled stored snapshot. In the supplied receiver this read happens before subscribing because CoreBluetooth can deliver reads through the same callback path once notifications are enabled. A read is not a newly acquired measurement: wait for a live event before presenting it as a current decision.

## Reuse the Python decoder

`receiver.py` is import-safe: importing it does not start scanning. It supplies:

```python
from receiver import (
    SERVICE_UUID, DATA_UUID, ALERT_UUID,
    decode_packet, decode_alert_packet,
    StreamWatch, AlertWatch,
)

# Called with the notification payload from the corresponding characteristic:
sensor = decode_packet(sensor_payload)       # returns Reading
alert = decode_alert_packet(alert_payload)   # returns AlertReading
```

The decoders raise `ValueError` for malformed or unsupported frames. Reject and count those errors rather than inserting their fields into the hub. The `Reading` object contains `sequence`, `uptime_ms`, `skin_c`, `ambient_c`, `humidity_pct`, and `skin_wait`. The `AlertReading` fields are visible in the dataclass at the top of `receiver.py` and match the alert semantics in the protocol document.

Adapt the existing `on_packet` and `on_alert` callbacks inside `run()` to publish structured objects to the hub instead of calling `log(format_reading(...))` and `log(format_alert(...))`. Do not parse those human-readable log strings as the integration interface. `StreamWatch` and `AlertWatch` provide duplicate/freshness checks, but the surrounding hub still needs to update its own UI/state on timeout and disconnect; these helpers do not do that automatically.

## Suggested hub data model

The following is a **proposed internal hub schema**, not another format transmitted by the board. Use the hub's existing names if it already has a schema.

| Field/group | Suggested representation |
|---|---|
| Identity | Project `device_id`, collector ID, local BLE identifier, collector-generated connection/session ID |
| Receipt time | UTC `received_at` timestamp plus a local monotonic arrival time for timeout checks |
| Sensor event | `sequence`, `device_uptime_ms`, nullable `skin_c`, `ambient_c`, `humidity_pct`, and separate sensor status |
| Sensor status | TMP117: `valid`, `waiting`, or `error`; SHT45: `valid` or `error`; stream status separately `live`, `stale`, or `disconnected` |
| Alert event | Independent `sequence`, `report_uptime_ms`, nullable `sample_uptime_ms`, `threshold_c`, nullable slope, sample count, history-restarted flag |
| Alert status | `valid`, `awaiting_sample`, `fault`, `stale`, `disconnected`, or `unsupported` |
| Absolute decision | `true`/`false` only for a current valid decision; otherwise `null` |
| Trend decision | `true`/`false` only for a current valid, trend-ready decision; otherwise `null` with `warming_up` or unavailable status |

For example, a valid alert during warmup can normalize to:

```json
{
  "event_type": "temperature_alert",
  "device_id": "team-device-01",
  "status": "valid",
  "absolute": true,
  "trend": null,
  "trend_status": "warming_up",
  "threshold_c": 29.0,
  "slope_c_per_min": null,
  "sample_count": 1
}
```

Add receipt/identity/timing fields from the table in the real adapter. Keep sensor events and alert events independent; they have different sequences and cadences. A combined dashboard can display their latest states with separate ages, but should not claim that the latest one-second temperature was necessarily the value used by the latest ten-second alert decision.

## Validity, freshness, and timing rules

- **Do not substitute zero for missing data.** `decode_packet` returns `None` for an invalid sensor reading. Zero degrees and zero percent humidity can be valid values. TMP117 `WAIT` is distinct from a read/init error; SHT45 validity is independent of TMP117 validity.
- **A well-formed packet is not proof of healthy sensors.** Packets can continue arriving with invalid sensor fields. Transport freshness and sensor validity must be checked separately.
- **An invalid alert is not OFF.** When `alert.valid` is false, the wire's decision bits are cleared. Convert these to unavailable/null, using `fault` to distinguish a fault from startup/reset awaiting a sample. Do not present them as a safe current decision.
- **Warmup is not a negative trend decision.** While `trend_ready` is false, show warming up and retain the independent absolute decision if it is valid.
- **Match the receiver's freshness policy initially:** warn after 5 seconds without a new accepted sensor packet; reconnect after 15 seconds. A previously valid alert becomes stale after 15 seconds without a new alert event, even if temperature packets continue. Use host monotonic time, not wall-clock changes, for these timers. Duplicate `(sequence, uptime)` values do not refresh a stream.
- **Startup/no-alert and fault states need explicit UI states.** `AlertWatch` warns about stale *previously valid* decisions; it does not itself report that no alert has ever arrived. Maintain `awaiting_sample` until live valid data arrives, and retain `fault` until recovery. Persistent fault states need not emit a new alert packet every ten seconds.
- **Board times are uptime, not UTC.** Record a host receive timestamp for logging. Both uint32 millisecond fields wrap after about 49.7 days; sequence fields also wrap. Power cycling resets firmware counters/history. A lower counter alone does not prove a reboot. There is no boot UUID, so begin a new collector connection/session on reconnect and avoid claiming certain boot identity where it cannot be established. Do not compare raw sequence numbers with simple greater-than tests.
- The sensor packet timestamp is the packet time; a valid TMP117 value may come from its bounded cache, at most 1.5 seconds old when selected. The alert sample time is the software snapshot time; it is not a wall-clock or exact hardware conversion timestamp.
- Notifications are live updates. There is no history-download or missed-packet replay interface. A GATT read returns the characteristic's latest stored value only. Do not backfill a disconnect gap with repeated last values.

## Alert behavior to preserve

The device computes the decisions; consume its flags as the firmware result rather than recalculating from rounded display values. Both use TMP117, with SHT45 supplied only as ambient context.

| Decision | Current behavior |
|---|---|
| Absolute | ON strictly above **29.0 C**, OFF at/below 29.0 C; demonstration setting |
| Trend | ON for least-squares rise greater than 0.06 C/min over 300 seconds; the original numerical comparison tolerance remains in place |
| Cadence | First usable sample evaluated at startup; subsequent decisions normally every 10 seconds |
| Warmup | 31 accepted samples spanning five minutes before a trend decision is ready |
| Reset/recovery | USB `r`/`R` clears history; a missed ten-second slot restarts the trend window on recovery; no synthetic samples are inserted |
| Latching | Neither flag is latched; absolute and trend can be ON independently |

The transmitted slope is rounded to 0.001 C/min, so it need not reproduce the full-precision threshold comparison at the boundary. The threshold travels in the alert packet: display it from the packet rather than hard-coding 29 in the hub. Change the demo threshold in firmware as described in [FIRMWARE_SETUP.md](FIRMWARE_SETUP.md) when the team is ready for its intended project setting. These are measured contact temperatures; the firmware does not estimate core-body temperature.

## USB power-only firmware update

The package includes [USB_POWER_FIX.md](USB_POWER_FIX.md). Upload that firmware update once on boards running the earlier sketch. It prevents optional USB diagnostics from waiting for a host reader; the BLE protocol and receiver are unchanged. Include a test powered by a separate computer with no Serial Monitor open.

## Integration acceptance checks

Run the supplied host tests before changing the decoder, then repeat them after adapter changes. See [VALIDATION.md](VALIDATION.md) for coverage and environment limits. Use the golden packets and malformed-frame cases in `tests/test_receiver.py` to test the hub parser offline.

| Check | Expected hub behavior |
|---|---|
| Connect to the powered board | Correct selected device; sensor updates about once per second and independent alert events normally every ten seconds |
| Warm TMP117 above 29 C, hold about 20 seconds | Absolute ON once a qualifying alert sample is evaluated; ambient data remains separate |
| Cool TMP117 to at/below 29 C | Absolute OFF on a subsequent valid evaluation; allow normal sampling delay |
| Trend warmup and full-window event | Show warmup until ready; then show slope and independent trend ON/OFF; use a fixture for deterministic parser tests |
| Inject sensor WAIT/error and alert fault fixtures | Missing values stay null; valid other-sensor data survives; invalid alerts never become a reassuring OFF |
| Stop sensor notifications / stop only alert notifications | Independent stale indicators at the documented timeouts; continuing sensor data does not refresh an old alert |
| Power from a second computer with no Serial Monitor | Continuous sensor and alert notifications on the receiver computer; no dependency on a USB reader |
| Disconnect or power cycle, then reconnect | Unavailable state during gap, same selected board, subscriptions restored, new live events; no invented historical readings |
| Duplicate, malformed, future version, and wrap fixtures | No duplicate freshness refresh; rejected bad frames; legitimate zero/negative values and wrap cases decode correctly |
| Two advertising boards | Explicit selection; no silent device switch |

Fixture tests validate software handling; they do not substitute for the live radio/sensor tests. The final hub integration check should use the actual hub deployment computer and its Bluetooth permissions/adapter.
