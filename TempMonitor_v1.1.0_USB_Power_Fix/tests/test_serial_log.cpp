#include "../XIAO_Temperature_BLE/serial_log.hpp"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

namespace {
unsigned checks = 0;
void require(bool condition, const char *message) {
  ++checks;
  if (!condition) {
    std::cerr << "FAIL: " << message << '\n';
    std::exit(1);
  }
}

// This port makes hidden waits/retries observable. A stalled host advertises
// no room; a race after admission can return a short write without draining.
struct FakePort {
  bool connected = true;
  int room = 256;
  std::size_t accept_limit = std::numeric_limits<std::size_t>::max();
  bool disconnect_after_capacity_check = false;
  unsigned connection_checks = 0, capacity_checks = 0, writes = 0, flushes = 0;
  std::size_t requested = 0;
  std::string received;

  explicit operator bool() {
    ++connection_checks;
    return connected;
  }
  int availableForWrite() {
    ++capacity_checks;
    const int result = room;
    if (disconnect_after_capacity_check) connected = false;
    return result;
  }
  std::size_t write(const std::uint8_t *data, std::size_t size) {
    ++writes;
    requested = size;
    require(writes == 1, "one log attempt must never retry a write");
    require(room >= 0 && size <= static_cast<std::size_t>(room),
            "never submit a log larger than available capacity");
    if (!connected) return 0;
    const std::size_t count = std::min(size, accept_limit);
    received.append(reinterpret_cast<const char *>(data), count);
    room -= static_cast<int>(count);
    return count;
  }
  void flush() {
    ++flushes;
    require(false, "best-effort logging must never flush or wait for the host");
  }
  void nextAttempt() {
    connection_checks = capacity_checks = writes = flushes = 0;
    requested = 0;
  }
};

const char line[] = "TMP117_C=29.12  BLE=connected\n";
constexpr std::size_t length = sizeof(line) - 1;

bool send(FakePort &port) {
  return temp_serial::tryWriteLogLine(port, line, length);
}
} // namespace

int main() {
  {
    FakePort port;
    port.connected = false;
    require(!send(port), "absent monitor drops log");
    require(port.capacity_checks == 0 && port.writes == 0,
            "absent monitor never touches TX capacity or write");
    require(port.received.empty(), "absent monitor stores no backlog");
  }
  for (const int room : {-1, 0, static_cast<int>(length) - 1}) {
    FakePort port;
    port.room = room;
    require(!send(port), "busy or insufficient capacity drops whole log");
    require(port.writes == 0 && port.flushes == 0,
            "full TX buffer causes neither a write nor a flush");
    require(port.received.empty(), "dropped line leaves no partial prefix");
  }
  {
    FakePort port;
    port.room = static_cast<int>(length);
    require(send(port), "exact-fit capacity permits the full line");
    require(port.writes == 1 && port.requested == length, "healthy log uses one write");
    require(port.received == line && port.room == 0, "line including newline is preserved");
  }
  for (const std::size_t accepted : {std::size_t(0), std::size_t(3)}) {
    FakePort port;
    port.accept_limit = accepted;
    require(!send(port), "short write reports a dropped/incomplete log");
    require(port.writes == 1 && port.flushes == 0, "short write is never retried or flushed");
    require(port.received == std::string(line, accepted), "only accepted bytes are retained");
  }
  {
    FakePort port;
    port.disconnect_after_capacity_check = true;
    require(!send(port), "disconnect during admission is tolerated");
    require(port.writes == 1 && port.received.empty(), "disconnect does not create a retry loop");
  }
  {
    FakePort port;
    port.room = 0;
    require(!send(port), "stopped host drops current line");
    port.nextAttempt();
    port.room = 256;
    require(send(port), "new logs resume after the host drains its buffer");
    require(port.received == line, "recovery does not replay the discarded line");
    port.nextAttempt();
    port.connected = false;
    require(!send(port), "closing monitor suppresses later writes");
    port.nextAttempt();
    port.connected = true;
    require(send(port), "reopening monitor permits future logs");
    require(port.received == std::string(line) + line, "no hidden retry queue across monitor changes");
  }
  std::cout << "Serial logging: " << checks << " checks passed\n";
}
