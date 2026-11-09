#pragma once

#include <Arduino.h>

// Over-the-air updates. The image is downloaded from a short-lived signed
// Supabase Storage URL and only installed if its SHA-256 matches the hash in
// the MQTT command (which itself arrived over authenticated TLS).
//
// The Arduino bootloader has no automatic rollback, so this adds one: a new
// image is "pending" until it reaches the MQTT broker. If it crash-loops or
// can't connect within kConfirmWindowMs, the previous image is booted again -
// a bad update can never strand a unit that has no programmer attached.
namespace ota {

// Called every ~10% with progress; use it to keep MQTT alive.
using ProgressFn = void (*)(int percent);

// Downloads, verifies and installs. On success the caller reboots.
bool install(const char *url, const char *sha256Hex, size_t expectedSize, const char *version,
             ProgressFn progress, String &error);

void bootCheck(); // once in setup(), before anything that might crash
void confirm();   // once the running image has proved it can reach the broker
void loop();      // rolls back if a pending image was never confirmed

// Outcome of the last update ("updated to 1.2.0", "rolled back from ..."),
// or empty. Reported in the status topic.
String lastResult();

} // namespace ota
