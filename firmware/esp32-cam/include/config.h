#pragma once

#include "secrets.h"

// Firmware version, reported in the status topic. `fleet.py release` builds
// with -DFW_VERSION="x.y.z"; a plain `pio run` reports "dev".
#ifndef FW_VERSION
#define FW_VERSION "dev"
#endif

// Unique identifier for this IV stand unit - what the backend/dashboard
// key readings against, and its MQTT topic (fluidwatch/<DEVICE_ID>/...).
// Give each physical device a distinct ID.
#define DEVICE_ID "IV-CAM-01"

// Default bed card metadata sent with every frame. These are only the
// first-boot values: change them remotely with
// `fleet.py config IV-CAM-01 bed_label="Bed 12"` (kept across reboots).
// Avoid characters that need URL-encoding beyond spaces.
#define BED_LABEL "Bed 07"
#define PATIENT_NAME "Unassigned"
#define FLUID_LABEL "Saline 0.9%"

// HTTP request timeout for the upload - higher than a typical JSON POST
// since it's transferring an actual image and waiting on model
// inference server-side.
#define HTTP_TIMEOUT_MS 15000

// WiFi connect timeout before retrying, in milliseconds.
#define WIFI_CONNECT_TIMEOUT_MS 15000UL

// How often the status topic is refreshed (uptime, heap, temperature...).
#define STATUS_INTERVAL_MS 60000UL

// Capture schedule, power/heat and image settings live in
// src/capture_config.cpp (kParams) - every one can be changed remotely, so
// there is nothing to edit here for them.

// MQTT broker for remote control and over-the-air updates - set these in
// secrets.h (see secrets.h.example). Empty host = run without remote control.
#ifndef MQTT_HOST
#define MQTT_HOST ""
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 8883
#endif
#ifndef MQTT_USERNAME
#define MQTT_USERNAME ""
#endif
#ifndef MQTT_PASSWORD
#define MQTT_PASSWORD ""
#endif
