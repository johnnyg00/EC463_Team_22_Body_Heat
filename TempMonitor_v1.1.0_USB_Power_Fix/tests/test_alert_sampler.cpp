#include "../XIAO_Temperature_BLE/alert_sampler.hpp"

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>

namespace {
unsigned checks = 0;
void require(bool condition, const char *message, int line) {
  ++checks;
  if (!condition) {
    std::cerr << "FAIL line " << line << ": " << message << '\n';
    std::exit(1);
  }
}
#define CHECK(expression) require((expression), #expression, __LINE__)

bool near(double a, double b) {
  return std::isfinite(a) && std::fabs(a - b) < 1e-9;
}

temp_alert::Event fresh(temp_alert::AlertSampler &sampler,
                        std::uint64_t nowMs, double c) {
  sampler.observe(1, c, nowMs);
  return sampler.poll(nowMs);
}

void unavailable(const temp_alert::Event &event) {
  CHECK(!event.valid);
  CHECK(event.fault);
  CHECK(!event.decision.input_accepted);
  CHECK(!event.decision.absolute_alert);
  CHECK(!event.decision.trend_alert);
  CHECK(!event.decision.trend_ready);
  CHECK(event.decision.sample_count == 0);
  CHECK(std::isnan(event.decision.slope_c_per_min));
}

void configured_threshold_and_cadence() {
  // The test-only device setting must not modify the portable core default.
  body_heat::AlertModule original;
  CHECK(original.config().absolute_limit_c == 38.0);
  CHECK(!original.update(38.0, 0).absolute_alert);
  CHECK(original.update(38.01, 10).absolute_alert);

  temp_alert::AlertSampler sampler;
  CHECK(sampler.absolute_limit_c() == 29.0);
  auto event = fresh(sampler, 1250, 29.0);
  CHECK(event.emitted && event.valid && !event.fault);
  CHECK(event.decision.input_accepted);
  CHECK(!event.decision.absolute_alert);
  CHECK(event.sample_ms == 1250 && event.report_ms == 1250);
  CHECK(event.source_age_ms == 0);
  CHECK(event.decision.sample_count == 1);

  CHECK(!sampler.poll(1251).emitted);
  sampler.observe(1, 29.01, 10999);
  CHECK(!sampler.poll(10999).emitted);
  CHECK(sampler.state().decision.sample_count == 1);
  event = sampler.poll(11000);
  CHECK(event.emitted && event.valid);
  CHECK(event.decision.absolute_alert);
  CHECK(event.source_age_ms == 1);
  CHECK(event.decision.sample_count == 2);
  CHECK(!event.decision.history_restarted);
  CHECK(!sampler.poll(11001).emitted);
  event = fresh(sampler, 21500, 28.99);
  CHECK(event.valid && !event.decision.absolute_alert);
  CHECK(event.decision.sample_count == 3);
  CHECK(!event.decision.history_restarted);

  temp_alert::AlertSampler normalThreshold(38.0);
  CHECK(normalThreshold.absolute_limit_c() == 38.0);
  CHECK(!fresh(normalThreshold, 1000, 29.01).decision.absolute_alert);
}

void full_window_and_jitter() {
  for (double rate : {0.0, 0.06, 0.0600001, 0.1, -0.1}) {
    temp_alert::AlertSampler sampler;
    temp_alert::Event event;
    for (std::uint64_t n = 0; n <= 30; ++n) {
      // Different fractional delays do not accumulate into the 10 s cadence.
      const std::uint64_t jitter[] = {250, 3, 799, 470, 999};
      const std::uint64_t now = 1000 + n * 10000 + jitter[n % 5];
      event = fresh(sampler, now, 27.0 + rate * double(n * 10) / 60.0);
      CHECK(event.emitted && event.valid && !event.fault);
      CHECK(event.sample_ms == now);
      CHECK(event.decision.sample_count == n + 1);
      CHECK(!event.decision.history_restarted);
      CHECK(event.decision.trend_ready == (n == 30));
      CHECK(!event.decision.absolute_alert);
      if (n < 30) {
        CHECK(!event.decision.trend_alert);
        CHECK(std::isnan(event.decision.slope_c_per_min));
      }
    }
    CHECK(near(event.decision.slope_c_per_min, rate));
    CHECK(event.decision.trend_alert == (rate > 0.06));
    event = fresh(sampler, 311002, 27.0 + rate * 310.0 / 60.0);
    CHECK(event.valid && event.decision.trend_ready);
    CHECK(event.decision.sample_count == 31);
    CHECK(!event.decision.history_restarted);
    CHECK(near(event.decision.slope_c_per_min, rate));
  }
}

void wait_cache_and_age_boundary() {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  temp_alert::AlertSampler sampler;
  CHECK(fresh(sampler, 1000, 28.0).valid);
  sampler.observe(1, 30.0, 10500);
  sampler.observe(0, nan, 11000); // WAIT must not overwrite a successful read.
  auto event = sampler.poll(11000);
  CHECK(event.valid && event.decision.absolute_alert);
  CHECK(event.source_age_ms == 500);
  CHECK(event.decision.sample_count == 2);

  temp_alert::AlertSampler boundary;
  boundary.observe(1, 30.0, 1000);
  event = boundary.poll(2500);
  CHECK(event.valid && event.source_age_ms == 1500);
  CHECK(event.decision.absolute_alert);
  event = boundary.poll(2501);
  CHECK(event.emitted);
  unavailable(event);
  CHECK(!boundary.poll(2502).emitted); // No repeated fault notifications.

  temp_alert::AlertSampler neverRead;
  neverRead.observe(0, 30.0, 1000);
  event = neverRead.poll(1000);
  CHECK(!event.emitted);
  CHECK(!neverRead.state().valid && !neverRead.state().fault);
  CHECK(!neverRead.state().decision.input_accepted);
}

void invalid_sources() {
  const double nan = std::numeric_limits<double>::quiet_NaN();
  const double inf = std::numeric_limits<double>::infinity();
  for (double c : {nan, inf, -inf, -55.001, 150.001}) {
    temp_alert::AlertSampler sampler;
    sampler.observe(1, c, 1000);
    auto event = sampler.poll(1000);
    CHECK(event.emitted);
    unavailable(event);
  }
  for (double c : {-55.0, 150.0}) {
    temp_alert::AlertSampler sampler;
    CHECK(fresh(sampler, 1000, c).valid);
  }
  temp_alert::AlertSampler future;
  future.observe(1, 30.0, 1001);
  auto event = future.poll(1000);
  CHECK(event.emitted);
  unavailable(event);

  temp_alert::AlertSampler missing;
  event = missing.poll(1000);
  CHECK(!event.emitted);
  CHECK(!missing.state().valid && !missing.state().fault);
  CHECK(!missing.state().decision.input_accepted);

  temp_alert::AlertSampler hardFault;
  CHECK(fresh(hardFault, 1000, 30.0).decision.absolute_alert);
  hardFault.observe(-1, 30.0, 1200);
  event = hardFault.poll(1200);
  CHECK(event.emitted);
  unavailable(event); // Old active flags are unavailable, not fresh safe flags.
  CHECK(!hardFault.poll(1201).emitted);
  hardFault.observe(0, 30.0, 1300);
  CHECK(!hardFault.poll(1300).emitted);
  unavailable(hardFault.state()); // WAIT cannot undo a hard fault.
}

void missed_slots_and_recovery() {
  temp_alert::AlertSampler sampler;
  CHECK(fresh(sampler, 1000, 30.0).valid);
  CHECK(fresh(sampler, 11000, 30.0).decision.sample_count == 2);
  auto event = sampler.poll(21000); // No valid source for this due slot.
  CHECK(event.emitted);
  unavailable(event);
  sampler.observe(1, 30.0, 22000);
  CHECK(!sampler.poll(22000).emitted);
  CHECK(!sampler.state().valid);
  event = fresh(sampler, 31000, 30.0);
  CHECK(event.emitted && event.valid && !event.fault);
  CHECK(event.decision.history_restarted);
  CHECK(event.decision.sample_count == 1);
  CHECK(event.decision.absolute_alert && !event.decision.trend_ready);

  // A fault between sampling slots is visible but does not invent a gap.
  temp_alert::AlertSampler transient;
  CHECK(fresh(transient, 1000, 30.0).valid);
  transient.observe(-1, 30.0, 1500);
  unavailable(transient.poll(1500));
  transient.observe(1, 30.0, 2000);
  CHECK(!transient.poll(2000).emitted);
  event = fresh(transient, 11000, 30.0);
  CHECK(event.valid && !event.decision.history_restarted);
  CHECK(event.decision.sample_count == 2);

  // A blocked loop records the real late sample, never backfills old slots.
  temp_alert::AlertSampler late;
  CHECK(fresh(late, 1000, 28.0).valid);
  event = fresh(late, 26500, 30.0);
  CHECK(event.emitted && event.valid);
  CHECK(event.sample_ms == 26500);
  CHECK(event.decision.history_restarted && event.decision.sample_count == 1);
  CHECK(!late.poll(26501).emitted);
  event = fresh(late, 36001, 30.0);
  CHECK(event.valid && !event.decision.history_restarted);
  CHECK(event.decision.sample_count == 2);
}

void reset_and_large_clock() {
  temp_alert::AlertSampler sampler;
  CHECK(fresh(sampler, 1000, 30.0).decision.absolute_alert);
  auto event = sampler.reset(1500);
  CHECK(event.emitted && !event.valid && !event.fault);
  CHECK(event.decision.sample_count == 0);
  CHECK(!event.decision.absolute_alert && !event.decision.trend_alert);
  CHECK(!sampler.poll(1501).emitted); // Reset requires a new successful read.
  CHECK(!sampler.state().valid && !sampler.state().fault);
  event = fresh(sampler, 2000, 29.0);
  CHECK(event.valid && event.decision.sample_count == 1);
  CHECK(!event.decision.history_restarted && !event.decision.absolute_alert);

  const std::uint64_t origin = (std::uint64_t(1) << 32) + 1000;
  temp_alert::AlertSampler longUptime;
  CHECK(fresh(longUptime, origin, 28.0).valid);
  event = fresh(longUptime, origin + 10000, 30.0);
  CHECK(event.valid && event.sample_ms == origin + 10000);
  CHECK(event.decision.sample_count == 2);
  CHECK(!event.decision.history_restarted);

  // Monotonic clock restart must discard history and cached sensor values.
  event = longUptime.poll(5000);
  CHECK(event.emitted);
  unavailable(event);
  event = fresh(longUptime, 6000, 29.0);
  CHECK(event.valid && event.decision.sample_count == 1);
  CHECK(!event.decision.history_restarted);
}
} // namespace

int main() {
  configured_threshold_and_cadence();
  full_window_and_jitter();
  wait_cache_and_age_boundary();
  invalid_sources();
  missed_slots_and_recovery();
  reset_and_large_clock();
  std::cout << "PASS: " << checks << " alert sampler checks\n";
}
