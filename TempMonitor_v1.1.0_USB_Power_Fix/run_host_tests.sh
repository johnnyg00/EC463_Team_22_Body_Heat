#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
python_bin="${PYTHON:-python3}"
cxx_bin="${CXX:-c++}"
test_dir="$(mktemp -d)"
trap 'rm -rf -- "$test_dir"' EXIT
"$python_bin" -m unittest discover -s tests -p 'test_*.py' -v
for test_source in tests/test_packet.cpp tests/test_alert_core.cpp \
                   tests/test_alert_sampler.cpp tests/test_alert_packet.cpp \
                   tests/test_serial_log.cpp; do
  test_name="$(basename "$test_source" .cpp)"
  "$cxx_bin" -std=c++11 -Wall -Wextra -Wpedantic -Werror \
    -I XIAO_Temperature_BLE "$test_source" -o "$test_dir/$test_name"
  "$test_dir/$test_name"
done
