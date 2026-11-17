# Changelog

## v1.1.0 — USB Power Fix (2026-11-17)

First explicitly versioned distribution of the USB power-only fix. Runtime source is identical to the latest previously supplied `XIAO_Bluetooth_Demo.zip`; this release adds versioned packaging and GitHub housekeeping.

### Included fix

- Zero USB TX timeout and bounded, best-effort diagnostic lines.
- No startup wait for Serial Monitor; unavailable/full USB output is skipped.
- Bounded USB command handling while preserving `r`/`R` history reset.
- BLE packets, receiver, sensor settings, and the 29°C demo threshold unchanged.

### Handoff contents

Firmware and all headers, Python receiver/dependency, host tests, quick setup, complete BLE protocol, hub integration guide, firmware setup, validation, and power-only retest instructions.

### Validation status

The included runtime source previously passed the XIAO ESP32S3 target build with no warnings/errors, 25 Python tests, and 4,489 C++ checks. No runtime changes were made for this naming/packaging update. The hardware power-only retest and integration with the team's actual hub remain pending.

### Version identifiers

Package/release version: **1.1.0**. BLE sensor packet version: **1**. BLE alert packet version: **1**. The Bluetooth advertisement remains `TempMonitor`.
