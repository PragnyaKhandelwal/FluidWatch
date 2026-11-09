#include "capture_config.h"
#include "config.h"

// {key, min, max, default, help}. Order must match enum Param. Defaults are
// tuned for an IV bottle/bag photographed from ~15-30 cm under ward lighting;
// fleet.py reads this table (keep each entry on one line).
const ParamSpec kParams[P_COUNT] = {
    {"interval_s", 5, 3600, 10, "Seconds between scheduled photos"},
    {"cpu_mhz", 80, 240, 160, "CPU clock: 80, 160 or 240 MHz. Lower = cooler and less power; 160 is the balance"},
    {"camera_sleep", 0, 1, 1, "1 = power the camera off between photos (cooler, safer); 0 = keep it running"},
    {"xclk_mhz", 10, 20, 20, "Clock fed to the sensor. Lower runs the sensor cooler but slower; 20 is standard"},
    {"warmup_frames", 0, 30, 5, "Frames thrown away after power-up so exposure and white balance settle"},
    {"settle_ms", 0, 5000, 100, "Extra wait after power-up before the warm-up frames"},
    {"flash", 0, 1, 0, "1 = white LED on while capturing (even lighting, but can glare on glossy plastic)"},
    {"temp_cutoff_c", 50, 100, 80, "Chip temperature that pauses the camera until it cools 10 C; 10 C above it the unit shuts down"},
    {"frame_size", 0, 21, 8, "Resolution: QVGA=5 (320x240), VGA=8 (640x480), SVGA=9, XGA=10, HD=11, UXGA=13, QSXGA=21"},
    {"jpeg_quality", 4, 63, 10, "JPEG quality, lower = better and bigger. Below ~8 can overflow at large sizes"},
    {"brightness", -2, 2, 0, "Overall brightness offset"},
    {"contrast", -2, 2, 0, "Contrast. +1 makes the liquid line stand out more"},
    {"saturation", -2, 2, 0, "Colour saturation. Clear fluids need little; coloured fluids may like +1"},
    {"sharpness", -2, 2, 0, "Edge sharpening (not every driver version supports it)"},
    {"denoise", 0, 8, 0, "Noise reduction strength (not every driver version supports it)"},
    {"special_effect", 0, 6, 0, "0 none, 1 negative, 2 grayscale, 3 red, 4 green, 5 blue, 6 sepia"},
    {"awb", 0, 1, 1, "Auto white balance"},
    {"awb_gain", 0, 1, 1, "Apply the auto white balance gains"},
    {"wb_mode", 0, 4, 0, "White balance preset when awb_gain is on: 0 auto, 1 sunny, 2 cloudy, 3 office, 4 home"},
    {"aec", 0, 1, 1, "Auto exposure. 0 = use aec_value (fixed exposure, most consistent frames)"},
    {"aec2", 0, 1, 0, "DSP night mode: longer exposure in low light (slower, may blur drips)"},
    {"ae_level", -2, 2, 0, "Auto exposure target: negative = darker, positive = brighter"},
    {"aec_value", 0, 1200, 300, "Manual exposure time, used when aec = 0"},
    {"agc", 0, 1, 1, "Auto gain. 0 = use agc_gain"},
    {"agc_gain", 0, 30, 0, "Manual gain when agc = 0. Higher = brighter but noisier"},
    {"gainceiling", 0, 6, 2, "Max auto gain: 0=2x 1=4x 2=8x 3=16x 4=32x 5=64x 6=128x. Lower = less noise"},
    {"bpc", 0, 1, 0, "Black pixel correction"},
    {"wpc", 0, 1, 1, "White pixel correction (hides hot pixels)"},
    {"raw_gma", 0, 1, 1, "Gamma correction"},
    {"lenc", 0, 1, 1, "Lens shading correction (evens out darker corners)"},
    {"dcw", 0, 1, 1, "Downsize via the sensor (keep 1 for smaller frame sizes)"},
    {"hmirror", 0, 1, 0, "Mirror the image left-right"},
    {"vflip", 0, 1, 0, "Flip the image upside down (camera mounted inverted)"},
    {"colorbar", 0, 1, 0, "Test pattern instead of the image (diagnostics only)"},
};

namespace {

// framesize_t names, indexed by value (esp32-camera's sensor.h).
const char *const kFrameSizeNames[] = {"96X96", "QQVGA", "QCIF", "HQVGA", "240X240", "QVGA", "CIF", "HVGA",
                                       "VGA",   "SVGA",  "XGA",  "HD",    "SXGA",    "UXGA", "FHD", "P_HD",
                                       "P_3MP", "QXGA",  "QHD",  "WQXGA", "P_FHD",   "QSXGA"};
constexpr size_t kFrameSizeCount = sizeof(kFrameSizeNames) / sizeof(kFrameSizeNames[0]);
constexpr size_t kMaxLabelLen = 40;

int findParam(const char *key) {
    for (int i = 0; i < P_COUNT; ++i) {
        if (!strcmp(kParams[i].key, key)) {
            return i;
        }
    }
    return -1;
}

void addError(String &errors, const char *key, const char *what) {
    errors += key;
    errors += ": ";
    errors += what;
    errors += "; ";
}

bool applyLabel(String &target, JsonVariantConst value, const char *key, String &errors) {
    if (!value.is<const char *>() || strlen(value.as<const char *>()) > kMaxLabelLen) {
        addError(errors, key, "must be text of at most 40 characters");
        return false;
    }
    target = value.as<const char *>();
    return true;
}

} // namespace

const char *frameSizeName(int32_t frameSize) {
    return (frameSize >= 0 && (size_t)frameSize < kFrameSizeCount) ? kFrameSizeNames[frameSize] : nullptr;
}

CaptureConfig::CaptureConfig() : bedLabel(BED_LABEL), patientName(PATIENT_NAME), fluidLabel(FLUID_LABEL) {
    for (int i = 0; i < P_COUNT; ++i) {
        v[i] = kParams[i].def;
    }
}

int CaptureConfig::apply(JsonObjectConst obj, String &errors) {
    int applied = 0;
    for (JsonPairConst kv : obj) {
        const char *key = kv.key().c_str();
        JsonVariantConst value = kv.value();

        if (!strcmp(key, "bed_label")) {
            applied += applyLabel(bedLabel, value, key, errors);
            continue;
        }
        if (!strcmp(key, "patient_name")) {
            applied += applyLabel(patientName, value, key, errors);
            continue;
        }
        if (!strcmp(key, "fluid_label")) {
            applied += applyLabel(fluidLabel, value, key, errors);
            continue;
        }

        int idx = findParam(key);
        if (idx < 0) {
            addError(errors, key, "unknown setting");
            continue;
        }

        int32_t n;
        if (value.is<bool>()) {
            n = value.as<bool>() ? 1 : 0;
        } else if (value.is<int32_t>()) {
            n = value.as<int32_t>();
        } else if (idx == P_FRAME_SIZE && value.is<const char *>()) {
            n = -1;
            for (size_t i = 0; i < kFrameSizeCount; ++i) {
                if (!strcasecmp(kFrameSizeNames[i], value.as<const char *>())) {
                    n = i;
                }
            }
            if (n < 0) {
                addError(errors, key, "unknown frame size name");
                continue;
            }
        } else {
            addError(errors, key, "must be a number");
            continue;
        }

        if (n < kParams[idx].min || n > kParams[idx].max) {
            addError(errors, key, "out of range");
            continue;
        }
        if (idx == P_CPU_MHZ && n != 80 && n != 160 && n != 240) {
            addError(errors, key, "must be 80, 160 or 240");
            continue;
        }
        v[idx] = n;
        ++applied;
    }
    return applied;
}

void CaptureConfig::toJson(JsonObject obj) const {
    for (int i = 0; i < P_COUNT; ++i) {
        obj[kParams[i].key] = v[i];
    }
    obj["bed_label"] = bedLabel;
    obj["patient_name"] = patientName;
    obj["fluid_label"] = fluidLabel;
}

String CaptureConfig::toJsonString() const {
    JsonDocument doc;
    toJson(doc.to<JsonObject>());
    String out;
    serializeJson(doc, out);
    return out;
}
