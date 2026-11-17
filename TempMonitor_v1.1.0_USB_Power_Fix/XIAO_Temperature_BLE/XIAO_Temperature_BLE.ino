// XIAO ESP32S3 + SHT45 + TMP117, USB Serial and Bluetooth LE notifications.
// QT: red=3V3, black=GND, blue=D4/GPIO5/SDA, yellow=D5/GPIO6/SCL.
// Install Adafruit SHT4x and dependencies. BLE comes with the ESP32 board core.
// Alert flags are reported over Serial/BLE; no motor GPIO is driven.
// No pairing, encryption, or incoming BLE commands in this bench demo.
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_SHT4x.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
#include <atomic>
#include <math.h>
#include <esp_timer.h>
#include <stdarg.h>
#include <stdio.h>
#include "telemetry_packet.hpp"
#include "alert_sampler.hpp"
#include "alert_packet.hpp"
#include "serial_log.hpp"

#if !ARDUINO_USB_CDC_ON_BOOT
#error "Enable USB CDC On Boot for this XIAO ESP32S3 sketch."
#endif

constexpr int SDA_GPIO = 5, SCL_GPIO = 6;
constexpr uint8_t SHT_ADDR = 0x44, TMP_ADDR = 0x48;
constexpr uint32_t SAMPLE_MS = 1000, RETRY_MS = 5000;
constexpr uint32_t TMP_POLL_MS = 250;
// DEMO ONLY: strict greater-than threshold. Set 38.0 to restore the original.
constexpr double DEMO_ABSOLUTE_LIMIT_C = 29.0;
constexpr char DEVICE_NAME[] = "TempMonitor";
constexpr char SERVICE_UUID[] = "71f10000-7a42-4d32-9ef4-01352c8f9a10";
constexpr char DATA_UUID[] = "71f10001-7a42-4d32-9ef4-01352c8f9a10";
constexpr char ALERT_UUID[] = "71f10002-7a42-4d32-9ef4-01352c8f9a10";
Adafruit_SHT4x sht4;
bool shtReady = false, tmpReady = false;
uint32_t lastSample = 0, lastRetry = 0, tmpFreshAt = 0;
BLEServer *bleServer = nullptr;
BLECharacteristic *dataCharacteristic = nullptr;
BLECharacteristic *alertCharacteristic = nullptr;
temp_alert::AlertSampler alertSampler(DEMO_ABSOLUTE_LIMIT_C);
std::atomic<bool> bleConnected(false), bleRestartRequested(false);
uint16_t packetSequence = 0, alertSequence = 0;
bool restartPending = false, lastConnectionPrinted = false;
uint32_t restartRequestedAt = 0;
uint32_t lastTmpPoll = 0;
bool haveLatestTmp = false;
float latestTmpC = NAN;
int latestTmpStatus = 0;
uint64_t latestTmpSourceMs = 0;

// Format one bounded line, then send only if USB can accept the entire line.
// No host/room means a dropped debug line, never delayed sensing or BLE.
void logLine(const char *format, ...) {
  if (!Serial) return;
  char line[384];
  va_list args;
  va_start(args, format);
  const int length = vsnprintf(line, sizeof(line) - 2, format, args);
  va_end(args);
  if (length < 0 || static_cast<size_t>(length) >= sizeof(line) - 2) return;
  line[length] = '\r';
  line[length + 1] = '\n';
  temp_serial::tryWriteLogLine(Serial, line, static_cast<size_t>(length) + 2);
}

uint64_t monotonicMs() {
  return static_cast<uint64_t>(esp_timer_get_time()) / 1000;
}

class ConnectionCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *) override {
    bleConnected.store(true);
  }
  void onDisconnect(BLEServer *) override {
    bleConnected.store(false);
    bleRestartRequested.store(true);
  }
};

void startBLE() {
  BLEDevice::init(DEVICE_NAME);
  bleServer = BLEDevice::createServer();
  bleServer->setCallbacks(new ConnectionCallbacks());
  BLEService *service = bleServer->createService(SERVICE_UUID);
  dataCharacteristic = service->createCharacteristic(
      DATA_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  alertCharacteristic = service->createCharacteristic(
      ALERT_UUID, BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
#if defined(CONFIG_BLUEDROID_ENABLED)
  dataCharacteristic->addDescriptor(new BLE2902());
  alertCharacteristic->addDescriptor(new BLE2902());
#endif
  uint8_t initial[temp_ble::kPacketSize];
  temp_ble::encodePacket(initial, packetSequence++, millis(), -1, NAN, false, NAN, NAN);
  dataCharacteristic->setValue(initial, sizeof(initial));
  uint8_t initialAlert[temp_ble::kAlertPacketSize];
  temp_ble::encodeAlertPacket(initialAlert, alertSequence++, millis(),
                             alertSampler.state(), DEMO_ABSOLUTE_LIMIT_C);
  alertCharacteristic->setValue(initialAlert, sizeof(initialAlert));
  service->start();

  // Service UUID fits in the advertisement; the name is in the scan response.
  BLEAdvertising *advertising = bleServer->getAdvertising();
  BLEAdvertisementData advertisement, scanResponse;
  advertisement.setFlags(0x06);
  advertisement.setCompleteServices(BLEUUID(SERVICE_UUID));
  scanResponse.setName(DEVICE_NAME);
  advertising->setAdvertisementData(advertisement);
  advertising->setScanResponseData(scanResponse);
  advertising->setScanResponse(true);
  advertising->start();
  logLine("BLE advertising as TempMonitor; start the laptop receiver.");
}

void publishAlertEvent(const temp_alert::Event &event) {
  if (!event.emitted) return;
  uint8_t packet[temp_ble::kAlertPacketSize];
  temp_ble::encodeAlertPacket(packet, alertSequence++,
                             static_cast<uint32_t>(event.report_ms), event,
                             DEMO_ABSOLUTE_LIMIT_C);
  alertCharacteristic->setValue(packet, sizeof(packet));
  if (bleConnected.load()) alertCharacteristic->notify();
  if (!Serial) return; // The BLE event above is independent of USB logging.
  const char *absolute = event.valid ? (event.decision.absolute_alert ? "1" : "0") : "NA";
  const char *trend = !event.valid ? "NA" : !event.decision.trend_ready ? "WARMUP"
                                                 : event.decision.trend_alert ? "1" : "0";
  char age[24] = "NA", sample[112] = "";
  if (event.source_age_ms != temp_alert::kUnknownAge)
    snprintf(age, sizeof(age), "%llu", static_cast<unsigned long long>(event.source_age_ms));
  if (event.valid) {
    if (event.decision.trend_ready) {
      snprintf(sample, sizeof(sample), " sample_ms=%llu history_restarted=%u slope_C_per_min=%.4f",
               static_cast<unsigned long long>(event.sample_ms),
               unsigned(event.decision.history_restarted), event.decision.slope_c_per_min);
    } else {
      snprintf(sample, sizeof(sample), " sample_ms=%llu history_restarted=%u",
               static_cast<unsigned long long>(event.sample_ms),
               unsigned(event.decision.history_restarted));
    }
  }
  logLine("[ALERT] ABS=%s TREND=%s valid=%u fault=%u samples=%u/31 threshold=%.2fC source_age_ms=%s%s",
          absolute, trend, unsigned(event.valid), unsigned(event.fault),
          unsigned(event.valid ? event.decision.sample_count : 0),
          DEMO_ABSOLUTE_LIMIT_C, age, sample);
}

void serviceAlertCommands() {
  // Bound command handling too; an absent port can report -1, not just zero.
  for (unsigned handled = 0; handled < 32 && Serial.available() > 0; ++handled) {
    const char command = static_cast<char>(Serial.read());
    if (command == 'r' || command == 'R') {
      publishAlertEvent(alertSampler.reset(monotonicMs()));
      logLine("Alert history reset; waiting for a fresh TMP117 sample.");
    }
  }
}

void serviceBLE() {
  if (bleRestartRequested.exchange(false)) {
    restartRequestedAt = millis();
    restartPending = true;
  }
  // Allow stack cleanup without blocking sampling or advertising in a callback.
  if (restartPending && uint32_t(millis() - restartRequestedAt) >= 500) {
    if (!bleConnected.load()) bleServer->startAdvertising();
    restartPending = false;
  }
  const bool connected = bleConnected.load();
  if (connected != lastConnectionPrinted) {
    logLine("%s", connected ? "BLE connected; receiver must subscribe to notifications."
                            : "BLE disconnected; advertising will restart.");
    lastConnectionPrinted = connected;
  }
}

bool responds(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

bool tmpRead16(uint8_t reg, uint16_t &value) {
  Wire.beginTransmission(TMP_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(TMP_ADDR, (uint8_t)2) != 2) {
    while (Wire.available()) Wire.read();
    return false;
  }
  uint8_t hi = Wire.read(), lo = Wire.read();
  value = (uint16_t(hi) << 8) | lo;
  return true;
}

bool tmpWrite16(uint8_t reg, uint16_t value) {
  Wire.beginTransmission(TMP_ADDR);
  Wire.write(reg);
  Wire.write(uint8_t(value >> 8));
  Wire.write(uint8_t(value));
  return Wire.endTransmission() == 0;
}

void scanI2C() {
  logLine("I2C scan: expect SHT45=0x44 and TMP117=0x48");
  unsigned found = 0;
  for (uint8_t address = 0x08; address <= 0x77; ++address) {
    if (responds(address)) {
      logLine("  Found 0x%02X", unsigned(address));
      ++found;
    }
  }
  if (!found) logLine("  No devices found; check 3V3/GND/SDA/SCL.");
}

void initMissingSensors() {
  if (!shtReady) {
    shtReady = responds(SHT_ADDR) && sht4.begin(&Wire);
    if (shtReady) {
      sht4.setPrecision(SHT4X_HIGH_PRECISION);
      sht4.setHeater(SHT4X_NO_HEATER);
    }
    logLine("%s", shtReady ? "SHT45 ready (heater OFF)"
                           : "ERROR: SHT45 init failed; retry in 5 s.");
  }
  if (!tmpReady) {
    uint16_t id = 0, config = 0;
    // 0x0220: continuous conversion, 1-second cycle, 8-sample averaging.
    tmpReady = tmpRead16(0x0F, id) && (id & 0x0FFF) == 0x0117
               && tmpWrite16(0x01, 0x0220)
               && tmpRead16(0x01, config) && (config & 0x0FFE) == 0x0220;
    tmpFreshAt = millis();
    logLine("%s", tmpReady ? "TMP117 ready (first conversion pending)"
                           : "ERROR: TMP117 init failed; retry in 5 s.");
  }
}

// Returns 1 for a fresh reading, 0 while waiting, -1 on a read/init error.
int readTMP(float &temperature) {
  if (!tmpReady) return -1;
  uint16_t config = 0, raw = 0;
  if (!tmpRead16(0x01, config)) {
    tmpReady = false;
    return -1;
  }
  if (!(config & 0x2000)) {
    if (uint32_t(millis() - tmpFreshAt) < 3000) return 0;
    tmpReady = false;
    return -1;
  }
  if (!tmpRead16(0x00, raw) || raw == 0x8000) {
    tmpReady = false;
    return -1;
  }
  temperature = int16_t(raw) * 0.0078125f;
  tmpFreshAt = millis();
  return 1;
}

void setup() {
  Serial.begin(115200);
  // HWCDC and TinyUSB CDC both support a zero TX timeout. Set it before
  // any logging: even availableForWrite() uses this timeout for its lock.
  Serial.setTxTimeoutMs(0);
  logLine("XIAO temperature BLE: SDA=GPIO5, SCL=GPIO6");
  Wire.begin(SDA_GPIO, SCL_GPIO, 100000);
  Wire.setTimeOut(50);
  scanI2C();
  initMissingSensors();
  startBLE();
  lastRetry = lastSample = millis();
  lastTmpPoll = millis();
  logLine("ERR=missing/read failure; WAIT=next TMP117 conversion pending");
  logLine("DEMO: ABS if TMP117 >%.1fC; trend >0.06C/min over5min, sampled every10s.",
          DEMO_ABSOLUTE_LIMIT_C);
  logLine("After skin contact settles, send r in Serial Monitor to reset trend history.");
}

void loop() {
  serviceBLE();
  serviceAlertCommands();
  uint32_t now = millis();
  if (uint32_t(now - lastRetry) >= RETRY_MS) {
    lastRetry = now;
    initMissingSensors();
  }
  if (uint32_t(now - lastTmpPoll) >= TMP_POLL_MS) {
    lastTmpPoll = now;
    float reading = NAN;
    latestTmpStatus = readTMP(reading);
    if (latestTmpStatus == 1 && (!isfinite(reading) || reading < -55 || reading > 150)) {
      latestTmpStatus = -1;
      tmpReady = false;
    }
    const uint64_t readAt = monotonicMs();
    alertSampler.observe(latestTmpStatus, reading, readAt);
    if (latestTmpStatus == 1) {
      latestTmpC = reading;
      latestTmpSourceMs = readAt;
      haveLatestTmp = true;
    } else if (latestTmpStatus < 0) {
      haveLatestTmp = false; // Reinitializing the sensor cannot restore old data.
    }
  }
  publishAlertEvent(alertSampler.poll(monotonicMs()));
  if (uint32_t(now - lastSample) < SAMPLE_MS) {
    delay(1);
    return;
  }
  lastSample = now;
  const uint64_t snapshotMs = monotonicMs();
  const bool recentTmp = haveLatestTmp && snapshotMs >= latestTmpSourceMs &&
                        snapshotMs - latestTmpSourceMs <= temp_alert::kMaxSourceAgeMs;
  const float tmpC = recentTmp ? latestTmpC : NAN;
  const int tmpStatus = recentTmp ? 1 : (latestTmpStatus < 0 ? -1 : 0);
  sensors_event_t humidity = {}, shtTemp = {};
  bool shtValid = shtReady && sht4.getEvent(&humidity, &shtTemp)
                  && isfinite(shtTemp.temperature)
                  && shtTemp.temperature >= -45 && shtTemp.temperature <= 130
                  && isfinite(humidity.relative_humidity)
                  && humidity.relative_humidity >= 0
                  && humidity.relative_humidity <= 100;
  if (!shtValid) shtReady = false;

  uint8_t packet[temp_ble::kPacketSize];
  temp_ble::encodePacket(packet, packetSequence++, millis(), tmpStatus, tmpC,
                        shtValid, shtTemp.temperature, humidity.relative_humidity);
  dataCharacteristic->setValue(packet, sizeof(packet));
  // The BLE stack sends notifications only to a subscribed connected client.
  if (bleConnected.load()) dataCharacteristic->notify();

  if (Serial) {
    char skin[24], ambient[24], rh[24];
    if (tmpStatus == 1) snprintf(skin, sizeof(skin), "%.2f", double(tmpC));
    else snprintf(skin, sizeof(skin), "%s", tmpStatus == 0 ? "WAIT" : "ERR");
    if (shtValid) {
      snprintf(ambient, sizeof(ambient), "%.2f", double(shtTemp.temperature));
      snprintf(rh, sizeof(rh), "%.1f", double(humidity.relative_humidity));
    } else {
      snprintf(ambient, sizeof(ambient), "ERR");
      snprintf(rh, sizeof(rh), "ERR");
    }
    logLine("TMP117_C=%s  SHT45_C=%s  RH_pct=%s  BLE=%s", skin, ambient, rh,
            bleConnected.load() ? "connected" : "advertising");
  }
}
