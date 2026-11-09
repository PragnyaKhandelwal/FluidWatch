#include "thermal.h"

#include <math.h>

namespace thermal {

namespace {

constexpr unsigned long kSampleIntervalMs = 2000UL;
constexpr float kSmoothing = 0.3f;          // weight of each new sample
constexpr float kNoSensorReading = 53.33f;  // what temperatureRead() returns without a sensor
constexpr int kSamplesToJudgeSensor = 10;

float smoothed = NAN;
State current = State::Normal;
unsigned long lastSampleMs = 0;
int samples = 0;
int noSensorSamples = 0;

} // namespace

bool update(int32_t cutoffC) {
    unsigned long now = millis();
    if (samples > 0 && now - lastSampleMs < kSampleIntervalMs) {
        return false;
    }
    lastSampleMs = now;

    float reading = temperatureRead();
    ++samples;
    if (fabsf(reading - kNoSensorReading) < 0.05f) {
        ++noSensorSamples;
    }
    if (samples >= kSamplesToJudgeSensor && noSensorSamples == samples) {
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
    } else if (smoothed <= cutoffC - kResumeHysteresisC) {
        next = State::Normal;
    } else if (current == State::Unavailable) {
        next = State::Normal;
    }
    bool changed = next != current;
    current = next;
    return changed;
}

float celsius() {
    return current == State::Unavailable ? NAN : smoothed;
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
