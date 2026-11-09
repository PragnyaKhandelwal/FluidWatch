#include <Arduino.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <WiFi.h>

#include "camera_control.h"
#include "camera_pins.h"
#include "capture_config.h"
#include "config.h"
#include "ota.h"
#include "remote.h"
#include "thermal.h"
#include "wifi_manager.h"

#include <esp_sleep.h>
#include <math.h>

namespace {

WiFiManager wifiManager;
CaptureConfig config;
Preferences settingsStore;
unsigned long lastCaptureMs = 0;
unsigned long lastStatusMs = 0;

// Filled by the MQTT callback, handled in loop() - a capture or an OTA must
// never run inside the callback itself.
String pendingConfig;
String pendingCommand;

struct LastCapture {
    unsigned long atMs = 0;
    int httpStatus = 0;
    size_t bytes = 0;
    uint32_t tookMs = 0;
    float fillPercent = -1;
} lastCapture;

// STATUS_LED_GPIO_NUM (GPIO33) is inverted: LOW = on, HIGH = off.
void setStatusLed(bool on) {
    digitalWrite(STATUS_LED_GPIO_NUM, on ? LOW : HIGH);
}

// Server-side query params only need spaces encoded in practice (bed
// labels, patient names) - full percent-encoding would need more code
// than this project has time for. Stick to plain text without other
// special characters in the labels.
String urlEncodeSpaces(const String &s) {
    String out;
    for (char c : s) {
        out += (c == ' ') ? "%20" : String(c);
    }
    return out;
}

void loadSettings() {
    settingsStore.begin("fw", false);
    String saved = settingsStore.getString("cfg", "");
    if (saved.length() == 0) {
        return;
    }
    JsonDocument doc;
    if (deserializeJson(doc, saved) == DeserializationError::Ok) {
        String errors;
        config.apply(doc.as<JsonObjectConst>(), errors);
    }
}

void saveSettings() {
    settingsStore.putString("cfg", config.toJsonString());
}

void applyCpuClock() {
    setCpuFrequencyMhz(config[P_CPU_MHZ]);
}

// Read-only health metric, attached to status and every capture event.
void addTemperature(JsonDocument &doc) {
    float t = thermal::celsius();
    if (!isnan(t)) {
        doc["chip_temp_c"] = roundf(t * 10) / 10;
    }
    doc["thermal"] = thermal::stateName();
}

void publishEvent(const char *type, JsonDocument &doc) {
    doc["type"] = type;
    doc["uptime_s"] = millis() / 1000;
    remote::publishJson("event", doc);
}

void publishError(const char *type, const String &message) {
    Serial.printf("[main] %s: %s\n", type, message.c_str());
    JsonDocument doc;
    doc["error"] = message;
    publishEvent(type, doc);
}

void publishStatus() {
    JsonDocument doc;
    doc["online"] = true;
    doc["fw"] = FW_VERSION;
    doc["ip"] = WiFi.localIP().toString();
    doc["rssi"] = WiFi.RSSI();
    doc["uptime_s"] = millis() / 1000;
    doc["cpu_mhz"] = getCpuFrequencyMhz();
    addTemperature(doc);
    doc["temp_cutoff_c"] = config[P_TEMP_CUTOFF_C];
    doc["heap_free"] = ESP.getFreeHeap();
    doc["psram_free"] = ESP.getFreePsram();
    doc["camera_on"] = camera::isOn();
    doc["interval_s"] = config[P_INTERVAL_S];
    String otaResult = ota::lastResult();
    if (otaResult.length() > 0) {
        doc["ota"] = otaResult;
    }
    if (lastCapture.atMs > 0) {
        JsonObject last = doc["last_capture"].to<JsonObject>();
        last["ago_s"] = (millis() - lastCapture.atMs) / 1000;
        last["http"] = lastCapture.httpStatus;
        last["bytes"] = lastCapture.bytes;
        last["took_ms"] = lastCapture.tookMs;
        if (lastCapture.fillPercent >= 0) {
            last["fill_percent"] = lastCapture.fillPercent;
        }
    }
    remote::publishJson("status", doc, true);
    lastStatusMs = millis();
}

// POSTs the frame to SERVER_URL. Returns the HTTP status (negative for
// connection errors) and, when the backend answers, its fill estimate.
int uploadFrame(camera_fb_t *fb, float &fillPercent) {
    String url = String(SERVER_URL) + "?device_id=" + DEVICE_ID + "&bed_label=" + urlEncodeSpaces(config.bedLabel) +
                 "&patient_name=" + urlEncodeSpaces(config.patientName) +
                 "&fluid_label=" + urlEncodeSpaces(config.fluidLabel);

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
        return -100;
    }

    size_t pos = 0;
    memcpy(buf + pos, head.c_str(), head.length());
    pos += head.length();
    memcpy(buf + pos, fb->buf, fb->len);
    pos += fb->len;
    memcpy(buf + pos, tail.c_str(), tail.length());

    int statusCode = http.POST(buf, totalLen);
    String body = http.getString();
    if (statusCode >= 200 && statusCode < 300) {
        Serial.printf("[Upload] OK (%d), chip %.1f C\n", statusCode, thermal::celsius());
        JsonDocument reading;
        if (deserializeJson(reading, body) == DeserializationError::Ok && reading["fluid_level_percent"].is<float>()) {
            fillPercent = reading["fluid_level_percent"].as<float>();
        }
    } else {
        Serial.printf("[Upload] failed, HTTP %d: %s\n", statusCode, body.c_str());
    }

    free(buf);
    http.end();
    return statusCode;
}

// One photo with `cfg`: power the camera up (unless it is kept running),
// let exposure settle, capture, then deliver to the backend or as an MQTT
// photo, and power down again.
void runCapture(const CaptureConfig &cfg, bool toServer, const char *tag, bool oneOff) {
    if (!thermal::captureAllowed()) {
        Serial.printf("[main] chip at %.1f C - camera paused until it cools\n", thermal::celsius());
        if (oneOff || strcmp(tag, "scheduled") != 0) {
            publishError("capture_skipped", "cooling down: chip at " + String(thermal::celsius(), 1) + " C");
        }
        return;
    }
    unsigned long startMs = millis();

    // A one-off capture with different settings needs a fresh init (frame
    // size and XCLK are fixed at init), so restart a kept-on camera for it.
    if (oneOff && camera::isOn()) {
        camera::powerDown();
    }
    if (!camera::powerUp(cfg)) {
        publishError("capture_failed", "camera init failed");
        return;
    }
    camera_fb_t *fb = camera::capture(cfg);
    if (fb == nullptr) {
        camera::powerDown();
        publishError("capture_failed", "no frame from sensor");
        return;
    }

    JsonDocument doc;
    doc["tag"] = tag;
    doc["width"] = fb->width;
    doc["height"] = fb->height;
    doc["bytes"] = fb->len;

    if (toServer) {
        float fill = -1;
        int status = uploadFrame(fb, fill);
        lastCapture.atMs = millis();
        lastCapture.httpStatus = status;
        lastCapture.bytes = fb->len;
        lastCapture.tookMs = millis() - startMs;
        lastCapture.fillPercent = fill;
        doc["http"] = status;
        if (fill >= 0) {
            doc["fill_percent"] = fill;
        }
    } else {
        doc["delivered"] = remote::publishPhoto(tag, fb->buf, fb->len);
    }
    esp_camera_fb_return(fb);

    if (cfg[P_CAMERA_SLEEP] || oneOff) {
        camera::powerDown();
    }
    if (oneOff && !config[P_CAMERA_SLEEP]) {
        camera::powerUp(config); // back to the kept-on camera with the saved settings
    }

    doc["took_ms"] = millis() - startMs;
    addTemperature(doc);
    publishEvent("capture", doc);
}

// Thermal state changes: pause the camera and slow the CPU while cooling,
// resume afterwards, and shut down completely if it keeps heating.
void handleThermal() {
    if (!thermal::update(config[P_TEMP_CUTOFF_C])) {
        return;
    }
    JsonDocument doc;
    addTemperature(doc);
    doc["temp_cutoff_c"] = config[P_TEMP_CUTOFF_C];
    publishEvent("thermal", doc);
    Serial.printf("[Thermal] %s (chip %.1f C, cutoff %d C)\n", thermal::stateName(), thermal::celsius(),
                  (int)config[P_TEMP_CUTOFF_C]);

    switch (thermal::state()) {
    case thermal::State::Cooling:
        camera::powerDown();
        setCpuFrequencyMhz(80);
        break;
    case thermal::State::Critical: {
        // Last resort: everything off for 10 minutes, then a fresh boot.
        publishStatus();
        delay(500);
        camera::powerDown();
        digitalWrite(FLASH_LED_GPIO_NUM, LOW);
        WiFi.disconnect(true);
        WiFi.mode(WIFI_OFF);
        Serial.println("[Thermal] critical - deep sleep for 10 minutes");
        Serial.flush();
        esp_sleep_enable_timer_wakeup(10ULL * 60ULL * 1000000ULL);
        esp_deep_sleep_start();
        break;
    }
    default: // back to normal, or no sensor
        applyCpuClock();
        if (!config[P_CAMERA_SLEEP]) {
            camera::powerUp(config);
        }
        break;
    }
}

void handleConfig(const String &payload) {
    JsonDocument doc;
    if (deserializeJson(doc, payload) != DeserializationError::Ok || !doc.is<JsonObject>()) {
        publishError("config_rejected", "not a JSON object");
        return;
    }

    CaptureConfig previous = config;
    String errors;
    int applied = config.apply(doc.as<JsonObjectConst>(), errors);
    saveSettings();
    bool cooling = !thermal::captureAllowed();
    if (!cooling) {
        applyCpuClock(); // while cooling the CPU stays at 80 MHz
    }

    bool needsInit = config[P_FRAME_SIZE] != previous[P_FRAME_SIZE] || config[P_XCLK_MHZ] != previous[P_XCLK_MHZ];
    String unsupported;
    if (config[P_CAMERA_SLEEP] || cooling) {
        camera::powerDown();
    } else if (!camera::isOn() || needsInit) {
        camera::powerDown();
        camera::powerUp(config);
    } else {
        camera::applySettings(config, unsupported);
    }

    JsonDocument result;
    result["applied"] = applied;
    if (errors.length() > 0) {
        result["errors"] = errors;
    }
    if (unsupported.length() > 0) {
        result["unsupported"] = unsupported;
    }
    config.toJson(result["config"].to<JsonObject>());
    publishEvent("config_applied", result);
    Serial.printf("[main] config applied (%d settings)%s%s\n", applied, errors.length() ? ", errors: " : "",
                  errors.c_str());
}

void onOtaProgress(int percent) {
    remote::loop(); // keep the broker connection alive during a long download
    JsonDocument doc;
    doc["percent"] = percent;
    publishEvent("ota_progress", doc);
}

void handleCommand(const String &payload) {
    JsonDocument doc;
    if (deserializeJson(doc, payload) != DeserializationError::Ok) {
        publishError("cmd_rejected", "not JSON");
        return;
    }
    String cmd = doc["cmd"] | "";

    if (cmd == "capture") {
        // Optional one-off overrides (for tuning) that don't touch the saved settings.
        CaptureConfig shot = config;
        String errors;
        bool oneOff = doc["overrides"].is<JsonObject>();
        if (oneOff) {
            shot.apply(doc["overrides"].as<JsonObjectConst>(), errors);
        }
        if (errors.length() > 0) {
            publishError("cmd_rejected", errors);
            return;
        }
        bool toServer = strcmp(doc["deliver"] | "server", "mqtt") != 0;
        runCapture(shot, toServer, doc["tag"] | "manual", oneOff);
    } else if (cmd == "status") {
        publishStatus();
    } else if (cmd == "report") {
        bool wasOn = camera::isOn();
        JsonDocument out;
        if (camera::powerUp(config)) {
            camera::report(out["camera"].to<JsonObject>());
        } else {
            out["camera"] = "init failed";
        }
        if (!wasOn) {
            camera::powerDown();
        }
        config.toJson(out["config"].to<JsonObject>());
        publishEvent("report", out);
    } else if (cmd == "reboot") {
        JsonDocument out;
        publishEvent("rebooting", out);
        delay(500);
        ESP.restart();
    } else if (cmd == "ota") {
        const char *url = doc["url"] | "";
        const char *sha = doc["sha256"] | "";
        const char *version = doc["version"] | "?";
        JsonDocument start;
        start["version"] = version;
        publishEvent("ota_start", start);

        camera::powerDown(); // free PSRAM/heap and keep the board cool during the download
        String error;
        if (ota::install(url, sha, doc["size"] | 0, version, onOtaProgress, error)) {
            JsonDocument done;
            done["version"] = version;
            publishEvent("ota_installed", done);
            delay(1000);
            ESP.restart();
        }
        publishError("ota_failed", error);
        if (!config[P_CAMERA_SLEEP]) {
            camera::powerUp(config);
        }
    } else {
        publishError("cmd_rejected", "unknown cmd '" + cmd + "'");
    }
}

void onRemoteMessage(const char *kind, const char *payload, size_t len) {
    String text;
    text.reserve(len);
    for (size_t i = 0; i < len; ++i) {
        text += payload[i];
    }
    if (!strcmp(kind, "config")) {
        pendingConfig = text;
    } else {
        pendingCommand = text;
    }
}

void printSensorReport() {
    JsonDocument doc;
    camera::report(doc.to<JsonObject>());
    Serial.println("[Camera] sensor report:");
    serializeJsonPretty(doc, Serial);
    Serial.println();
}

} // namespace

void setup() {
    Serial.begin(115200);
    delay(300);
    Serial.printf("\n[FluidWatch ESP32-CAM] firmware %s booting...\n", FW_VERSION);

    pinMode(STATUS_LED_GPIO_NUM, OUTPUT);
    pinMode(FLASH_LED_GPIO_NUM, OUTPUT);
    setStatusLed(false);
    digitalWrite(FLASH_LED_GPIO_NUM, LOW);
    camera::powerDown(); // sensor off until the first photo

    ota::bootCheck();
    loadSettings();
    applyCpuClock();
    Serial.printf("[main] settings: %s\n", config.toJsonString().c_str());

    // Check the sensor once at boot. A failure is reported but doesn't halt:
    // the unit stays reachable over MQTT, so it can still be fixed remotely.
    if (camera::powerUp(config)) {
        printSensorReport();
        if (config[P_CAMERA_SLEEP]) {
            camera::powerDown();
        }
    } else {
        Serial.println("[main] camera init failed - check the ribbon cable and power; staying online for remote fixes");
    }

    wifiManager.begin(WIFI_SSID, WIFI_PASSWORD);
    WiFi.setSleep(true); // modem sleep between packets: noticeably less heat and power
    setStatusLed(wifiManager.isConnected());
    remote::begin(onRemoteMessage);
}

void loop() {
    wifiManager.ensureConnected();
    setStatusLed(wifiManager.isConnected());

    bool wasConnected = remote::connected();
    remote::loop();
    if (remote::connected()) {
        ota::confirm();
        if (!wasConnected) {
            publishStatus();
        }
    }
    ota::loop();
    handleThermal();

    if (pendingConfig.length() > 0) {
        String payload = pendingConfig;
        pendingConfig = "";
        handleConfig(payload);
    }
    if (pendingCommand.length() > 0) {
        String payload = pendingCommand;
        pendingCommand = "";
        handleCommand(payload);
    }

    unsigned long now = millis();
    if (wifiManager.isConnected() && (lastCaptureMs == 0 || now - lastCaptureMs >= config[P_INTERVAL_S] * 1000UL)) {
        lastCaptureMs = now;
        runCapture(config, true, "scheduled", false);
    }
    if (remote::connected() && now - lastStatusMs >= STATUS_INTERVAL_MS) {
        publishStatus();
    }

    // Let the idle task run: a busy loop keeps a core at 100% and warms the board.
    delay(20);
}
