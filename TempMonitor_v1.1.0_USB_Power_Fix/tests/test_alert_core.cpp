#include "alert_module.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>

namespace {

int checks = 0;
int failures = 0;

void check(bool condition, const char* description, int line) {
    ++checks;
    if (!condition) {
        ++failures;
        std::cerr << "FAIL line " << line << ": " << description << '\n';
    }
}

#define CHECK(condition) check((condition), #condition, __LINE__)

bool near(double actual, double expected, double tolerance = 1e-10) {
    return std::isfinite(actual) && std::fabs(actual - expected) <= tolerance;
}

body_heat::Decision ramp(body_heat::AlertModule& module, double start,
                         double rate_c_per_min, std::uint64_t origin = 0) {
    body_heat::Decision result;
    for (std::uint64_t t = 0; t <= 300; t += 10) {
        result = module.update(start + rate_c_per_min * static_cast<double>(t) / 60.0,
                               origin + t);
    }
    return result;
}

void planned_cases() {
    body_heat::AlertModule module;
    CHECK(module.configuration_valid());
    CHECK(module.config().window_seconds == 300);
    CHECK(module.config().sample_interval_seconds == 10);

    for (int run = 0; run < 3; ++run) {
        module.reset();
        for (std::uint64_t t = 0; t <= 300; t += 10) {
            const body_heat::Decision d = module.update(37.0, t);
            CHECK(d.input_accepted && !d.history_restarted);
            CHECK(!d.trend_alert && !d.absolute_alert);
            CHECK(d.trend_ready == (t == 300));
            CHECK(d.sample_count == t / 10 + 1);
            if (t < 300) CHECK(std::isnan(d.slope_c_per_min));
            else CHECK(near(d.slope_c_per_min, 0.0));
        }

        module.reset();
        for (std::uint64_t t = 0; t <= 300; t += 10) {
            const body_heat::Decision d = module.update(36.0 + 0.5 * t / 300.0, t);
            CHECK(d.input_accepted);
            CHECK(d.trend_ready == (t == 300));
            CHECK(d.trend_alert == (t == 300));
            CHECK(!d.absolute_alert);
            if (t == 300) CHECK(near(d.slope_c_per_min, 0.1));
        }

        module.reset();
        for (std::uint64_t t = 0; t <= 300; t += 10) {
            const body_heat::Decision d = module.update(36.0 + 0.3 * t / 300.0, t);
            CHECK(d.input_accepted && !d.trend_alert && !d.absolute_alert);
            CHECK(d.trend_ready == (t == 300));
            if (t == 300) CHECK(near(d.slope_c_per_min, 0.06));
        }

        module.reset();
        body_heat::Decision d = module.update(38.1, 0);
        CHECK(d.input_accepted && d.absolute_alert);
        CHECK(!d.trend_ready && !d.trend_alert && d.sample_count == 1);
        module.reset();
        d = module.update(38.0, 0);
        CHECK(d.input_accepted && !d.absolute_alert && !d.trend_alert);
    }
}

void boundaries_and_reset() {
    body_heat::AlertModule module;
    CHECK(!ramp(module, 36.0, 0.06 - 1e-7).trend_alert);
    module.reset();
    CHECK(ramp(module, 36.0, 0.06 + 1e-7).trend_alert);
    module.reset();
    body_heat::Decision d = ramp(module, 37.0, -0.1);
    CHECK(d.trend_ready && !d.trend_alert && !d.absolute_alert);
    CHECK(near(d.slope_c_per_min, -0.1));

    module.reset();
    CHECK(!module.update(37.9999, 0).absolute_alert);
    CHECK(!module.update(38.0, 10).absolute_alert);
    CHECK(module.update(38.0001, 20).absolute_alert);
    CHECK(!module.update(37.0, 30).absolute_alert);  // The flags do not latch.

    module.reset();
    d = ramp(module, 38.0, 0.1);
    CHECK(d.trend_alert && d.absolute_alert);
    module.reset();
    d = module.update(37.0, 0);
    CHECK(d.input_accepted && !d.history_restarted && d.sample_count == 1);
    CHECK(!d.trend_ready && !d.trend_alert && !d.absolute_alert);
    CHECK(std::isnan(d.slope_c_per_min));
}

void rolling_window_and_large_clock() {
    body_heat::AlertModule module;
    CHECK(ramp(module, 36.0, 0.1).trend_alert);
    body_heat::Decision d;
    for (std::uint64_t t = 310; t <= 1500; t += 10) {
        d = module.update(36.5, t);
        CHECK(d.input_accepted && !d.history_restarted && d.trend_ready);
        CHECK(d.sample_count == 31 && !d.absolute_alert);
        if (t >= 600) {
            CHECK(!d.trend_alert);
            CHECK(near(d.slope_c_per_min, 0.0));
        }
    }
    module.reset();
    // Integer subtraction must happen before double conversion, including at
    // origins beyond the range of exact integer representation in a double.
    const std::uint64_t origin = (std::uint64_t(1) << 60);
    d = ramp(module, 36.0, 0.1, origin);
    CHECK(d.trend_ready && d.trend_alert);
    CHECK(near(d.slope_c_per_min, 0.1));
    module.reset();
    d = ramp(module, 36.0, 0.06, std::numeric_limits<std::uint64_t>::max() - 300);
    CHECK(d.trend_ready && !d.trend_alert);
    CHECK(near(d.slope_c_per_min, 0.06));
}

void invalid_inputs_and_cadence() {
    body_heat::AlertModule module;
    CHECK(module.update(37.0, 0).input_accepted);
    CHECK(module.update(37.0, 10).input_accepted);
    const double bad_values[] = {
        std::numeric_limits<double>::quiet_NaN(),
        std::numeric_limits<double>::infinity(),
        -std::numeric_limits<double>::infinity()
    };
    for (std::size_t i = 0; i < 3; ++i) {
        const body_heat::Decision d = module.update(bad_values[i], 20);
        CHECK(!d.input_accepted && !d.history_restarted && d.sample_count == 2);
    }
    CHECK(!module.update(99.0, 10).input_accepted);
    CHECK(!module.update(99.0, 9).input_accepted);
    body_heat::Decision d = module.update(37.0, 20);
    CHECK(d.input_accepted && !d.history_restarted && d.sample_count == 3);
    CHECK(!d.absolute_alert);

    d = module.update(38.1, 40);  // Missing expected sample at30s.
    CHECK(d.input_accepted && d.history_restarted && d.sample_count == 1);
    CHECK(d.absolute_alert && !d.trend_ready && !d.trend_alert);
    CHECK(std::isnan(d.slope_c_per_min));
    for (std::uint64_t t = 50; t <= 340; t += 10) {
        d = module.update(37.0, t);
        CHECK(d.input_accepted && !d.history_restarted);
        CHECK(d.trend_ready == (t == 340));
        CHECK(!d.trend_alert && !d.absolute_alert);
    }
    d = module.update(37.0, 345);  // Early sample also restarts the window.
    CHECK(d.input_accepted && d.history_restarted && d.sample_count == 1);
    CHECK(!d.trend_ready);
    d = module.update(37.0, 355);
    CHECK(d.input_accepted && !d.history_restarted && d.sample_count == 2);
}

void invalid_configuration() {
    body_heat::Config c;
    c.window_seconds = 0;
    CHECK(!body_heat::AlertModule(c).configuration_valid());
    c = body_heat::Config(); c.sample_interval_seconds = 0;
    CHECK(!body_heat::AlertModule(c).configuration_valid());
    c = body_heat::Config(); c.window_seconds = 301;
    CHECK(!body_heat::AlertModule(c).configuration_valid());
    c = body_heat::Config(); c.window_seconds = 640;
    body_heat::AlertModule too_many(c);
    CHECK(!too_many.configuration_valid());
    CHECK(!too_many.update(38.1, 0).input_accepted);
    c = body_heat::Config(); c.slope_limit_c_per_min = 0;
    CHECK(!body_heat::AlertModule(c).configuration_valid());
    c.slope_limit_c_per_min = -0.01;
    CHECK(!body_heat::AlertModule(c).configuration_valid());
    c.slope_limit_c_per_min = std::numeric_limits<double>::quiet_NaN();
    CHECK(!body_heat::AlertModule(c).configuration_valid());
    c.slope_limit_c_per_min = std::numeric_limits<double>::infinity();
    CHECK(!body_heat::AlertModule(c).configuration_valid());
    c = body_heat::Config(); c.absolute_limit_c = std::numeric_limits<double>::infinity();
    CHECK(!body_heat::AlertModule(c).configuration_valid());

    c = body_heat::Config(); c.window_seconds = 630;
    body_heat::AlertModule full_capacity(c);
    CHECK(full_capacity.configuration_valid());
    for (std::uint64_t t = 0; t <= 1500; t += 10) {
        const body_heat::Decision d = full_capacity.update(37.0, t);
        CHECK(d.input_accepted && d.sample_count <= 64);
        CHECK(d.trend_ready == (t >= 630));
        CHECK(!d.trend_alert && !d.absolute_alert);
    }
    c = body_heat::Config(); c.window_seconds = 10;
    body_heat::AlertModule shortest(c);
    CHECK(shortest.configuration_valid());
    CHECK(!shortest.update(36.0, 0).trend_ready);
    const body_heat::Decision d = shortest.update(36.1, 10);
    CHECK(d.trend_ready && d.trend_alert && near(d.slope_c_per_min, 0.6));
}

}  // namespace

int main() {
    planned_cases();
    boundaries_and_reset();
    rolling_window_and_large_clock();
    invalid_inputs_and_cadence();
    invalid_configuration();
    std::cout << (failures == 0 ? "PASS: " : "FAIL: ") << checks
              << " checks, " << failures << " failures.\n";
    return failures == 0 ? 0 : 1;
}
