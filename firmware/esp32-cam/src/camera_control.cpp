#include "camera_control.h"
#include "camera_pins.h"

namespace camera {

namespace {

bool cameraOn = false;

void track(int result, const char *name, int &rejected, String &unsupported) {
    if (result != 0) {
        ++rejected;
        unsupported += name;
        unsupported += ' ';
    }
}

} // namespace

bool powerUp(const CaptureConfig &cfg) {
    if (cameraOn) {
        return true;
    }

    camera_config_t config = {};
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = cfg[P_XCLK_MHZ] * 1000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size = (framesize_t)cfg[P_FRAME_SIZE];
    config.jpeg_quality = cfg[P_JPEG_QUALITY];

    if (psramFound()) {
        config.fb_count = 2;
        config.fb_location = CAMERA_FB_IN_PSRAM;
        config.grab_mode = CAMERA_GRAB_LATEST;
    } else {
        // No PSRAM: only small frames fit in internal RAM.
        Serial.println("[Camera] WARNING: no PSRAM found, limiting to SVGA in DRAM");
        if (config.frame_size > FRAMESIZE_SVGA) {
            config.frame_size = FRAMESIZE_SVGA;
        }
        config.fb_count = 1;
        config.fb_location = CAMERA_FB_IN_DRAM;
        config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("[Camera] init failed: 0x%x\n", err);
        powerDown();
        return false;
    }
    cameraOn = true;

    String unsupported;
    if (applySettings(cfg, unsupported) > 0) {
        Serial.printf("[Camera] sensor rejected: %s\n", unsupported.c_str());
    }
    return true;
}

void powerDown() {
    if (cameraOn) {
        esp_camera_deinit();
        cameraOn = false;
    }
    // PWDN is active high on the AI-Thinker board: the sensor stops drawing
    // its ~100 mA streaming current and cools down.
    pinMode(PWDN_GPIO_NUM, OUTPUT);
    digitalWrite(PWDN_GPIO_NUM, HIGH);
}

bool isOn() {
    return cameraOn;
}

int applySettings(const CaptureConfig &cfg, String &unsupported) {
    sensor_t *s = esp_camera_sensor_get();
    if (s == nullptr) {
        unsupported += "(no sensor)";
        return 1;
    }

    // Only settings that were set explicitly are written. The OV5640 boots with
    // its own tuned exposure, gamma, lens and pixel correction; overwriting
    // all of them with nominal values gave dark frames with dotted columns.
    int rejected = 0;
#define APPLY(param, call)                                                     \
    if (cfg.isSet(param)) {                                                    \
        track(call, kParams[param].key, rejected, unsupported);                \
    }
    APPLY(P_JPEG_QUALITY, s->set_quality(s, cfg[P_JPEG_QUALITY]));
    APPLY(P_BRIGHTNESS, s->set_brightness(s, cfg[P_BRIGHTNESS]));
    APPLY(P_CONTRAST, s->set_contrast(s, cfg[P_CONTRAST]));
    APPLY(P_SATURATION, s->set_saturation(s, cfg[P_SATURATION]));
    APPLY(P_SHARPNESS, s->set_sharpness(s, cfg[P_SHARPNESS]));
    APPLY(P_DENOISE, s->set_denoise(s, cfg[P_DENOISE]));
    APPLY(P_SPECIAL_EFFECT, s->set_special_effect(s, cfg[P_SPECIAL_EFFECT]));
    APPLY(P_AWB, s->set_whitebal(s, cfg[P_AWB]));
    APPLY(P_AWB_GAIN, s->set_awb_gain(s, cfg[P_AWB_GAIN]));
    APPLY(P_WB_MODE, s->set_wb_mode(s, cfg[P_WB_MODE]));
    // Writing a manual exposure/gain value can switch the OV5640 out of auto
    // mode (frames stuck dark), so the manual values are only sent when auto
    // is off, and the auto switches are written last so they always win.
    if (!cfg[P_AEC] && (cfg.isSet(P_AEC) || cfg.isSet(P_AEC_VALUE))) {
        track(s->set_aec_value(s, cfg[P_AEC_VALUE]), "aec_value", rejected, unsupported);
    }
    if (!cfg[P_AGC] && (cfg.isSet(P_AGC) || cfg.isSet(P_AGC_GAIN))) {
        track(s->set_agc_gain(s, cfg[P_AGC_GAIN]), "agc_gain", rejected, unsupported);
    }
    APPLY(P_GAINCEILING, s->set_gainceiling(s, (gainceiling_t)cfg[P_GAINCEILING]));
    APPLY(P_AEC2, s->set_aec2(s, cfg[P_AEC2]));
    APPLY(P_AE_LEVEL, s->set_ae_level(s, cfg[P_AE_LEVEL]));
    APPLY(P_AEC, s->set_exposure_ctrl(s, cfg[P_AEC]));
    APPLY(P_AGC, s->set_gain_ctrl(s, cfg[P_AGC]));
    APPLY(P_BPC, s->set_bpc(s, cfg[P_BPC]));
    APPLY(P_WPC, s->set_wpc(s, cfg[P_WPC]));
    APPLY(P_RAW_GMA, s->set_raw_gma(s, cfg[P_RAW_GMA]));
    APPLY(P_LENC, s->set_lenc(s, cfg[P_LENC]));
    APPLY(P_DCW, s->set_dcw(s, cfg[P_DCW]));
    APPLY(P_HMIRROR, s->set_hmirror(s, cfg[P_HMIRROR]));
    APPLY(P_VFLIP, s->set_vflip(s, cfg[P_VFLIP]));
    APPLY(P_COLORBAR, s->set_colorbar(s, cfg[P_COLORBAR]));
#undef APPLY
    return rejected;
}

camera_fb_t *capture(const CaptureConfig &cfg) {
    if (!cameraOn) {
        return nullptr;
    }
    if (cfg[P_FLASH]) {
        digitalWrite(FLASH_LED_GPIO_NUM, HIGH); // on before warm-up, so exposure adapts to it
    }
    delay(cfg[P_SETTLE_MS]);
    for (int i = 0; i < cfg[P_WARMUP_FRAMES]; ++i) {
        camera_fb_t *discard = esp_camera_fb_get();
        if (discard != nullptr) {
            esp_camera_fb_return(discard);
        }
    }
    camera_fb_t *fb = esp_camera_fb_get();
    digitalWrite(FLASH_LED_GPIO_NUM, LOW);
    return fb;
}

void report(JsonObject out) {
    sensor_t *s = esp_camera_sensor_get();
    if (s == nullptr) {
        out["sensor"] = "not running";
        return;
    }
    char pid[8];
    snprintf(pid, sizeof(pid), "0x%04x", s->id.PID);
    out["sensor_pid"] = pid;
    out["sensor"] = s->id.PID == OV5640_PID ? "OV5640" : (s->id.PID == OV2640_PID ? "OV2640" : "other");
    out["psram"] = psramFound();
    out["xclk_mhz"] = s->xclk_freq_hz / 1000000;

    const camera_status_t &st = s->status;
    JsonObject live = out["live"].to<JsonObject>();
    live["frame_size"] = frameSizeName(st.framesize);
    live["jpeg_quality"] = st.quality;
    live["brightness"] = st.brightness;
    live["contrast"] = st.contrast;
    live["saturation"] = st.saturation;
    live["sharpness"] = st.sharpness;
    live["denoise"] = st.denoise;
    live["special_effect"] = st.special_effect;
    live["awb"] = st.awb;
    live["awb_gain"] = st.awb_gain;
    live["wb_mode"] = st.wb_mode;
    live["aec"] = st.aec;
    live["aec2"] = st.aec2;
    live["ae_level"] = st.ae_level;
    live["aec_value"] = st.aec_value;
    live["agc"] = st.agc;
    live["agc_gain"] = st.agc_gain;
    live["gainceiling"] = st.gainceiling;
    live["bpc"] = st.bpc;
    live["wpc"] = st.wpc;
    live["raw_gma"] = st.raw_gma;
    live["lenc"] = st.lenc;
    live["dcw"] = st.dcw;
    live["hmirror"] = st.hmirror;
    live["vflip"] = st.vflip;
    live["colorbar"] = st.colorbar;
}

} // namespace camera
