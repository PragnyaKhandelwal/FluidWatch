#include "thermal.h"

#include <esp_wifi.h>
#include <math.h>

namespace thermal {

namespace {

constexpr unsigned long kSampleIntervalMs = 2000UL;
constexpr float kSmoothing = 0.3f; // weight of each new sample
// temperatureRead() returns exactly this (raw 128 F) when the sensor has no
// fresh conversion - which on the classic ESP32 is whenever the Wi-Fi radio
// (which powers the sensor's ADC) is asleep. Such samples are skipped, not
// averaged in. A real 53.33 C reading is lost too, which costs nothing.
constexpr float kNoReading = 53.33f;
// No real reading for this long means this chip's sensor doesn't work.
constexpr unsigned long kUnavailableAfterMs = 60000UL;

float smoothed = NAN;
float lastRaw = NAN;
State current = State::Normal;
unsigned long lastSampleMs = 0;
unsigned long lastValidMs = 0;
bool sampled = false;

bool isNoReading(float t) {
    return fabsf(t - kNoReading) < 0.05f;
}

// One reading, with the radio kept awake for a moment if modem sleep left
// the sensor unpowered.
float readSensor() {
    float t = temperatureRead();
    wifi_ps_type_t ps;
    if (!isNoReading(t) || esp_wifi_get_ps(&ps) != ESP_OK || ps == WIFI_PS_NONE) {
        return t;
    }
    esp_wifi_set_ps(WIFI_PS_NONE);
    for (int i = 0; i < 5 && isNoReading(t); ++i) {
        delay(10);
        t = temperatureRead();
    }
    esp_wifi_set_ps(ps);
    return t;
}

} // namespace

bool update(int32_t cutoffC) {
    unsigned long now = millis();
    if (sampled && now - lastSampleMs < kSampleIntervalMs) {
        return false;
    }
    sampled = true;
    lastSampleMs = now;

    float reading = readSensor();
    lastRaw = reading;
    State next = current;

    if (isNoReading(reading) || reading < -40.0f || reading > 150.0f) {
        if (now - lastValidMs >= kUnavailableAfterMs) {
            next = State::Unavailable;
            smoothed = NAN;
        }
    } else {
        lastValidMs = now;
        smoothed = isnan(smoothed) ? reading : smoothed + kSmoothing * (reading - smoothed);
        if (smoothed >= cutoffC + kCriticalMarginC) {
            next = State::Critical;
        } else if (smoothed >= cutoffC) {
            next = current == State::Critical ? State::Critical : State::Cooling;
        } else if (smoothed <= cutoffC - kResumeHysteresisC || current == State::Unavailable) {
            next = State::Normal;
        }
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
