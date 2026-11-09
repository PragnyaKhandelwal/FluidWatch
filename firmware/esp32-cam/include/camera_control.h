#pragma once

#include <ArduinoJson.h>
#include "capture_config.h"
#include "esp_camera.h"

// The OV5640 is the hottest part of the board while it streams, so by default
// it is only powered for the second or two a photo takes: powerUp() ->
// capture() -> powerDown().
namespace camera {

// Initialises the sensor for `cfg` (frame size, JPEG quality, XCLK) and
// applies every image setting. False if the sensor didn't respond.
bool powerUp(const CaptureConfig &cfg);

// Releases the driver and holds the sensor in hardware power-down (PWDN).
void powerDown();

bool isOn();

// Pushes the image settings to a running sensor. Returns how many the driver
// rejected; their names are appended to `unsupported`.
int applySettings(const CaptureConfig &cfg, String &unsupported);

// Lets exposure settle (settle_ms + warmup_frames, flash LED if enabled) and
// returns one frame. The caller hands it back with esp_camera_fb_return().
camera_fb_t *capture(const CaptureConfig &cfg);

// Sensor model plus every value the driver reports it is currently using.
void report(JsonObject out);

} // namespace camera
