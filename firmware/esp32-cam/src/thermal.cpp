#include "thermal.h"

#include <math.h>

namespace thermal {

namespace {

constexpr unsigned long kSampleIntervalMs = 2000UL;
constexpr float kSmoothing = 0.3f;         // weight of each new sample
constexpr float kNoSensorReading = 53.33f; // what temperatureRead() returns without a sensor
// A real sensor jitters by its 0.56 C step within a few samples; this many
// identical 53.33 readings in a row (~30 s) means there is no sensor. The
// first reading after power-up can differ, so only the latest run counts.
constexpr int kNoSensorRun = 15;

float smoothed = NAN;
float lastRaw = NAN;
State current = State::Normal;
unsigned long lastSampleMs = 0;
bool sampled = false;
int noSensorRun = 0;

} // namespace

bool update(int32_t cutoffC) {
    unsigned long now = millis();
    if (sampled && now - lastSampleMs < kSampleIntervalMs) {
        return false;
    }
    sampled = true;
    lastSampleMs = now;

    float reading = temperatureRead();
    lastRaw = reading;
    noSensorRun = fabsf(reading - kNoSensorReading) < 0.05f ? noSensorRun + 1 : 0;
    if (noSensorRun >= kNoSensorRun) {
        bool changed = current != State::Unavailable;
        current = State::Unavailable;
        smoothed = NAN;
        return changed;
    }
    if (reading < -40.0f || reading > 150.0f) {
        return false; // glitch
    }
    smoothed = isnan(smoothed) ? reading : smoothed + kSmoothing * (reading - smoothed);

    State next = current;
    if (smoothed >= cutoffC + kCriticalMarginC) {
        next = State::Critical;
    } else if (smoothed >= cutoffC) {
        next = current == State::Critical ? State::Critical : State::Cooling;
    } else if (smoothed <= cutoffC - kResumeHysteresisC || current == State::Unavailable) {
        next = State::Normal;
    }
    bool changed = next != current;
    current = next;
    return changed;
}

float celsius() {
    return current == State::Unavailable ? NAN : smoothed;
}

float rawCelsius() {
    return lastRaw;
}

State state() {
    return current;
}

const char *stateName() {
    switch (current) {
    case State::Cooling:
        return "cooling";
    case State::Critical:
        return "critical";
    case State::Unavailable:
        return "unavailable";
    default:
        return "normal";
    }
}

bool captureAllowed() {
    return current == State::Normal || current == State::Unavailable;
}

} // namespace thermal
