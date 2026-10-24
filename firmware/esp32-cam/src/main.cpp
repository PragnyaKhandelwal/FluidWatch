#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"

#include "camera_pins.h"
#include "config.h"
#include "wifi_manager.h"

WiFiManager wifiManager;
unsigned long lastCaptureMs = 0;

namespace {

// STATUS_LED_GPIO_NUM (GPIO33) is inverted: LOW = on, HIGH = off.
void setStatusLed(bool on) {
    digitalWrite(STATUS_LED_GPIO_NUM, on ? LOW : HIGH);
}

// Brief flash on FLASH_LED_GPIO_NUM (GPIO4) so a successful capture is
// visible without a serial monitor attached. Short enough not to show
// up as a lit bag/bottle in the photo itself (called after fb_get()
// returns, so it doesn't affect the frame that was just captured).
void flashCaptureIndicator() {
    digitalWrite(FLASH_LED_GPIO_NUM, HIGH);
    delay(120);
    digitalWrite(FLASH_LED_GPIO_NUM, LOW);
}

// Server-side query params only need spaces encoded in practice (bed
// labels, patient names) - full percent-encoding would need more code
// than this project has time for. Stick to plain text without other
// special characters in config.h's labels.
String urlEncodeSpaces(const char *s) {
    String out;
    for (const char *p = s; *p; ++p) {
        out += (*p == ' ') ? "%20" : String(*p);
    }
    return out;
}

bool initCamera() {
    camera_config_t config;
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
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;

    if (psramFound()) {
        config.frame_size = CAPTURE_FRAME_SIZE;
        config.jpeg_quality = CAPTURE_JPEG_QUALITY;
        config.fb_count = 2;
        config.fb_location = CAMERA_FB_IN_PSRAM;
        config.grab_mode = CAMERA_GRAB_LATEST;
    } else {
        // No PSRAM detected - fall back to a small frame in DRAM rather
        // than failing outright. Bigger frames will run out of DRAM.
        Serial.println("[Camera] WARNING: no PSRAM found, falling back to low-res/DRAM");
        config.frame_size = FRAMESIZE_SVGA;
        config.jpeg_quality = 15;
        config.fb_count = 1;
        config.fb_location = CAMERA_FB_IN_DRAM;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("[Camera] init failed: 0x%x\n", err);
        return false;
    }

    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor != nullptr) {
        sensor->set_brightness(sensor, CAPTURE_BRIGHTNESS);
        sensor->set_contrast(sensor, CAPTURE_CONTRAST);
        sensor->set_saturation(sensor, CAPTURE_SATURATION);
    }
    return true;
}

bool uploadFrame(camera_fb_t *fb) {
    String url = String(SERVER_URL) + "?device_id=" + DEVICE_ID +
                 "&bed_label=" + urlEncodeSpaces(BED_LABEL) +
                 "&patient_name=" + urlEncodeSpaces(PATIENT_NAME) +
                 "&fluid_label=" + urlEncodeSpaces(FLUID_LABEL);

    HTTPClient http;
    http.begin(url);
    http.setTimeout(HTTP_TIMEOUT_MS);

    const String boundary = "FluidWatchBoundary7MA4YWxk";
    http.addHeader("Content-Type", "multipart/form-data; boundary=" + boundary);
    if (strlen(API_AUTH_TOKEN) > 0) {
        http.addHeader("Authorization", String("Bearer ") + API_AUTH_TOKEN);
    }

    String head = "--" + boundary + "\r\n" +
                  "Content-Disposition: form-data; name=\"file\"; filename=\"frame.jpg\"\r\n" +
                  "Content-Type: image/jpeg\r\n\r\n";
    String tail = "\r\n--" + boundary + "--\r\n";

    size_t totalLen = head.length() + fb->len + tail.length();
    uint8_t *buf = (uint8_t *)malloc(totalLen);
    if (buf == nullptr) {
        Serial.println("[Upload] out of memory building multipart body");
        http.end();
        return false;
    }

    size_t pos = 0;
    memcpy(buf + pos, head.c_str(), head.length());
    pos += head.length();
    memcpy(buf + pos, fb->buf, fb->len);
    pos += fb->len;
    memcpy(buf + pos, tail.c_str(), tail.length());
    pos += tail.length();

    int statusCode = http.POST(buf, totalLen);
    bool ok = statusCode >= 200 && statusCode < 300;

    if (ok) {
        Serial.printf("[Upload] OK (%d): %s\n", statusCode, http.getString().c_str());
    } else {
        Serial.printf("[Upload] failed, HTTP %d: %s\n", statusCode, http.getString().c_str());
    }

    free(buf);
    http.end();
    return ok;
}

} // namespace

void setup() {
    Serial.begin(115200);
    delay(300);
    Serial.println("\n[FluidWatch ESP32-CAM] booting...");

    pinMode(STATUS_LED_GPIO_NUM, OUTPUT);
    pinMode(FLASH_LED_GPIO_NUM, OUTPUT);
    setStatusLed(false);
    digitalWrite(FLASH_LED_GPIO_NUM, LOW);

    if (!initCamera()) {
        Serial.println("[main] camera init failed - check the ribbon cable and that PSRAM is enabled, halting");
        while (true) {
            delay(1000);
        }
    }
    Serial.println("[Camera] ready");

    wifiManager.begin(WIFI_SSID, WIFI_PASSWORD);
    setStatusLed(wifiManager.isConnected());
}

void loop() {
    wifiManager.ensureConnected();
    setStatusLed(wifiManager.isConnected());

    unsigned long now = millis();
    if (now - lastCaptureMs < SEND_INTERVAL_MS) {
        return;
    }
    lastCaptureMs = now;

    if (!wifiManager.isConnected()) {
        Serial.println("[main] WiFi not connected, skipping this cycle");
        return;
    }

    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == nullptr) {
        Serial.println("[main] camera capture failed");
        return;
    }
    flashCaptureIndicator();

    uploadFrame(fb);
    esp_camera_fb_return(fb);
}
