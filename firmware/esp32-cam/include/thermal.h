#pragma once

#include <Arduino.h>

// Over-temperature protection from the ESP32's internal die sensor.
//
//   normal      everything runs
//   cooling     reached temp_cutoff_c: camera off, no photos, CPU at 80 MHz,
//               until the chip is back below cutoff - kResumeHysteresisC
//   critical    reached cutoff + kCriticalMarginC: the caller shuts down
//               (deep sleep) to protect the board
//   unavailable no real reading for a minute (the sensor only answers while
//               the Wi-Fi radio is awake; otherwise it returns 53.33 C) -
//               no protection until readings come back
//
// The sensor is uncalibrated (it can be off by about 10 C) and reads the
// silicon die, not the camera, so the cutoff has a generous margin below the
// chip's 125 C limit.
namespace thermal {

constexpr float kResumeHysteresisC = 10.0f;
constexpr float kCriticalMarginC = 10.0f;

enum class State { Normal, Cooling, Critical, Unavailable };

// Samples (at most every 2 s), smooths and updates the state. Call often.
// Returns true when the state changed on this call.
bool update(int32_t cutoffC);

float celsius();    // smoothed reading; NAN until the first sample or if unavailable
float rawCelsius(); // last unsmoothed reading, for diagnostics
State state();
const char *stateName(); // "normal", "cooling", "critical", "unavailable"
bool captureAllowed();   // normal, or no sensor to go by

} // namespace thermal
