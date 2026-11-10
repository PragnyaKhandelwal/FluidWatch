#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

// Every runtime-tunable setting: capture schedule, power/thermal, and the
// OV5640's image controls. kParams (capture_config.cpp) is the single source
// of truth for names, ranges, defaults and help text - firmware/tools/fleet.py
// reads that same table, so keep this enum and the table in the same order.
enum Param : uint8_t {
    // schedule, power and heat
    P_INTERVAL_S,
    P_CPU_MHZ,
    P_CAMERA_SLEEP,
    P_XCLK_MHZ,
    P_WARMUP_FRAMES,
    P_SETTLE_MS,
    P_FLASH,
    P_TEMP_CUTOFF_C,
    // image format
    P_FRAME_SIZE,
    P_JPEG_QUALITY,
    // image tuning
    P_BRIGHTNESS,
    P_CONTRAST,
    P_SATURATION,
    P_SHARPNESS,
    P_DENOISE,
    P_SPECIAL_EFFECT,
    P_AWB,
    P_AWB_GAIN,
    P_WB_MODE,
    P_AEC,
    P_AEC2,
    P_AE_LEVEL,
    P_AEC_VALUE,
    P_AGC,
    P_AGC_GAIN,
    P_GAINCEILING,
    P_BPC,
    P_WPC,
    P_RAW_GMA,
    P_LENC,
    P_DCW,
    P_HMIRROR,
    P_VFLIP,
    P_COLORBAR,
    P_COUNT
};

struct ParamSpec {
    const char *key;
    int32_t min;
    int32_t max;
    int32_t def;
    const char *help;
};

extern const ParamSpec kParams[P_COUNT];

// Name of a framesize_t value ("VGA", "SVGA", ...), or nullptr if out of range.
const char *frameSizeName(int32_t frameSize);

struct CaptureConfig {
    int32_t v[P_COUNT];
    // Which settings were set explicitly (config, NVS, the tuning page). Only
    // those are written to the sensor; everything else keeps the OV5640's own
    // factory tuning, which is what gives clean, well-exposed frames.
    uint64_t explicitMask = 0;
    // Bed card metadata sent with every upload (see config.h for defaults).
    String bedLabel;
    String patientName;
    String fluidLabel;

    CaptureConfig(); // every value at its default, nothing explicit

    int32_t operator[](Param p) const { return v[p]; }
    bool isSet(Param p) const { return explicitMask & (1ULL << p); }

    // Applies the keys present in `obj` and marks them explicit. Unknown keys
    // and out-of-range values are skipped and described in `errors`. Returns
    // how many were applied.
    int apply(JsonObjectConst obj, String &errors);

    // explicitOnly: just the settings that were set explicitly (plus the
    // labels) - what gets persisted, so defaults stay "sensor's own".
    void toJson(JsonObject obj, bool explicitOnly = false) const;
    String toJsonString(bool explicitOnly = false) const;
};
