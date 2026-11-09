#pragma once

#include <ArduinoJson.h>

// MQTT link to the broker (HiveMQ Cloud over TLS), so a unit can be
// reconfigured, triggered and updated from anywhere without a programmer.
//
// Topics, all under fluidwatch/<DEVICE_ID>/:
//   config     in, retained   JSON settings (see capture_config.cpp)
//   cmd        in             {"cmd": "capture" | "status" | "report" | "reboot" | "ota", ...}
//   status     out, retained  health snapshot; {"online": false} is the last will
//   event      out            results: capture, config_applied, ota_*, errors
//   photo/<t>  out            raw JPEG, only for captures sent with "deliver": "mqtt"
namespace remote {

// kind is "config" or "cmd". Called from inside loop(): copy the payload and
// act on it later - never start a long job (OTA, capture) from the callback.
using MessageHandler = void (*)(const char *kind, const char *payload, size_t len);

void begin(MessageHandler handler);
void loop(); // keeps the connection alive and reconnects with backoff
bool enabled(); // false when MQTT_HOST is empty
bool connected();

bool publishJson(const char *subtopic, const JsonDocument &doc, bool retain = false);
bool publishPhoto(const char *tag, const uint8_t *jpeg, size_t len);

} // namespace remote
