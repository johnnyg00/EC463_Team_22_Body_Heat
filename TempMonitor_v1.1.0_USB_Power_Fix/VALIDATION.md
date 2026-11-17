# Validation — USB power-only fix, November 17, 2026

## ESP32 target build

Compilation passed without warnings or errors for `esp32:esp32:XIAO_ESP32S3` with USB CDC enabled and hardware CDC mode.

| Component | Version |
|---|---|
| Arduino CLI | 1.6.0 |
| Espressif ESP32 core | 3.3.13 |
| Adafruit SHT4x | 1.0.5 |
| Adafruit BusIO | 1.17.4 |
| Adafruit Unified Sensor | 1.1.15 |

Flash usage: 605,014 / 3,342,336 bytes (18%). Global variables: 30,272 / 327,680 bytes (9%).

## Host checks

- C++ packet encoder: 49 checks passed, including golden bytes, endianness, negative values, rounding, valid zeros, invalid readings, WAIT, boundaries, wrapping counters, and output-buffer guards.
- Encoder built with C++11, strict warnings as errors, AddressSanitizer, and UndefinedBehaviorSanitizer. LeakSanitizer was disabled because the test environment uses ptrace.
- Original alert core: 2,052 checks passed. The copied `alert_module.hpp` and its original test source are byte-identical to the previously supplied module and tests.
- Alert sampler: 1,441 checks passed, covering the strict 29 C demo boundary, immediate startup evaluation, normal ten-second timing, fractional scheduling jitter, five-minute windows, fresh cached input, WAIT, stale/missing/invalid data, faults, resets, actual delayed timestamps, and skipped-sample recovery.
- Alert packet encoder: 906 checks passed, including exact bytes, flag/sentinel behavior, thresholds, slope representation, and wrapping counters.
- Python receiver: 25 unit tests passed, covering both packet formats, malformed frames, sensor/alert state handling, independent freshness checks, wrapping counters/timestamps, and device selection.
- USB logging helper: 41 checks passed for missing readers, unavailable/insufficient buffers, exact-fit writes, interrupted/short writes without retry, and later recovery without replaying dropped logs.
- Final combined `run_host_tests.sh` passed with 4,489 C++ checks and 25 Python tests, zero failures. The USB logging helper uses a fake port in these tests; real USB driver behavior still requires the hardware retest.
- Bleak 3.1.0 installed successfully in an isolated Python environment. Required scanner, client, notification, and permission-error APIs were imported/checked. Receiver command-line help was checked.

## Hardware status and handoff checks

These checks were performed in a Linux build/test environment without a Bluetooth adapter connected to your XIAO. They do not establish that macOS permissions, radio range, notification delivery, reconnection, or sensor operation while BLE is active have passed.

The earlier USB-only sensor sketch was demonstrated on the assembled device, followed by a user report that the BLE setup was working. Moving USB power to a second computer without a serial reader then caused stopped notifications and repeated receiver timeouts. The revised sketch removes blocking USB diagnostic output as a plausible cause; this exact power-only scenario still needs a physical retest after uploading the fix.

For the receiving teammate, confirm temperature and alert notifications, absolute ON above 29 C and OFF at/below 29 C, a disconnect/reconnect, and a power cycle. Keep both sensors connected. Use the acceptance checklist in `HUB_INTEGRATION.md` to record hub results. Windows/Linux hardware operation, radio range, and a complete hub integration remain unverified here.

This package now implements sensor readout, the original trend/absolute alert module (demo absolute override 29 C), and BLE transmission of readings and decisions. The physical vibration driver and compatibility with the team's hub remain separate integration work.

Original and copied alert-core SHA256: `925700e84695c38cba60d8bd5f56d231cf004e9bfa12774c85adae12734ec7c1`.

## Documentation handoff

The teammate handoff adds a quick start, firmware setup, binary protocol, and hub integration guide. The subsequent USB power-only update changes optional firmware logging and bounds USB command processing. The BLE protocol, sensor configuration, alert module, and Python receiver remain unchanged. The hub API has not been provided; the integration guide identifies the adapter work and proposes a data mapping rather than claiming an existing connection.

## Run the included checks

From the extracted package folder, on macOS/Linux with Python 3.10+ and a C++11 compiler:

```bash
bash run_host_tests.sh
```

For only the Python decoder/receiver tests (no BLE adapter or Bleak installation needed):

```bash
# macOS/Linux, after creating the README's virtual environment
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

```powershell
# Windows PowerShell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'
```

These are offline checks. They do not establish that the actual hub's Bluetooth connection, forwarding API, or display works.

## USB power-only fix verification

The updated sketch compiled for the XIAO ESP32S3 with all warnings enabled and no warnings/errors. USB TX timeout is set to zero before any application log, no startup Serial wait remains, and every diagnostic line uses a checked single write with no retry/flush. USB command handling is bounded per loop. The startup threshold message now follows the configured constant.

See `USB_POWER_FIX.md` for the one-time upload and hardware checks: run with the second computer supplying power but no serial reader, verify continuous sensor/alert traffic, repeat disconnect/reconnect, and allow a five-minute trend window. These checks have not been performed by the build environment.
