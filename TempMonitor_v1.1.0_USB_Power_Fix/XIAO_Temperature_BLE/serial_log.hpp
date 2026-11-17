#ifndef TEMP_SERIAL_LOG_HPP
#define TEMP_SERIAL_LOG_HPP

#include <cstddef>
#include <cstdint>

namespace temp_serial {
// The caller must configure the USB CDC port's TX timeout to zero first.
// Diagnostic logs are disposable: never wait for a host or retry a short write.
template <typename Port>
bool tryWriteLogLine(Port &port, const char *bytes, std::size_t length) {
  if (bytes == nullptr || length == 0 || !static_cast<bool>(port)) return false;
  const int freeBytes = port.availableForWrite();
  if (freeBytes < 0 || static_cast<std::size_t>(freeBytes) < length) return false;
  return port.write(reinterpret_cast<const std::uint8_t *>(bytes), length) == length;
}
}  // namespace temp_serial

#endif
