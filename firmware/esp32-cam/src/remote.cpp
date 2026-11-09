#include "remote.h"

#include <PubSubClient.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

#include "config.h"
#include "root_ca.h"

namespace remote {

namespace {

constexpr unsigned long kMinBackoffMs = 5000UL;
constexpr unsigned long kMaxBackoffMs = 60000UL;
constexpr size_t kPhotoChunk = 1024;

WiFiClientSecure tlsClient;
PubSubClient mqtt(tlsClient);
MessageHandler messageHandler = nullptr;
unsigned long lastAttemptMs = 0;
unsigned long backoffMs = 0;
const String base = String("fluidwatch/") + DEVICE_ID + "/";

void onMessage(char *topic, byte *payload, unsigned int len) {
    if (messageHandler == nullptr) {
        return;
    }
    String t(topic);
    if (t.endsWith("/config")) {
        messageHandler("config", (const char *)payload, len);
    } else if (t.endsWith("/cmd")) {
        messageHandler("cmd", (const char *)payload, len);
    }
}

void connect() {
    String statusTopic = base + "status";
    Serial.printf("[MQTT] connecting to %s:%d...\n", MQTT_HOST, MQTT_PORT);
    bool ok = mqtt.connect(DEVICE_ID, MQTT_USERNAME, MQTT_PASSWORD, statusTopic.c_str(), 1, true,
                           "{\"online\":false}");
    if (!ok) {
        Serial.printf("[MQTT] connect failed, state=%d\n", mqtt.state());
        backoffMs = backoffMs == 0 ? kMinBackoffMs : min(backoffMs * 2, kMaxBackoffMs);
        return;
    }
    backoffMs = 0;
    mqtt.subscribe((base + "config").c_str(), 1);
    mqtt.subscribe((base + "cmd").c_str(), 1);
    Serial.println("[MQTT] connected");
}

} // namespace

void begin(MessageHandler handler) {
    messageHandler = handler;
    if (!enabled()) {
        Serial.println("[MQTT] MQTT_HOST not set in secrets.h - remote control disabled");
        return;
    }
    tlsClient.setCACert(kMqttRootCa);
    mqtt.setServer(MQTT_HOST, MQTT_PORT);
    mqtt.setCallback(onMessage);
    mqtt.setBufferSize(4096); // retained config JSON and OTA commands with signed URLs
    mqtt.setKeepAlive(60);
    mqtt.setSocketTimeout(10);
}

void loop() {
    if (!enabled() || WiFi.status() != WL_CONNECTED) {
        return;
    }
    if (mqtt.connected()) {
        mqtt.loop();
        return;
    }
    unsigned long now = millis();
    if (lastAttemptMs == 0 || now - lastAttemptMs >= backoffMs) {
        lastAttemptMs = now;
        connect();
    }
}

bool enabled() {
    return strlen(MQTT_HOST) > 0;
}

bool connected() {
    return enabled() && mqtt.connected();
}

bool publishJson(const char *subtopic, const JsonDocument &doc, bool retain) {
    if (!connected()) {
        return false;
    }
    String payload;
    serializeJson(doc, payload);
    return mqtt.publish((base + subtopic).c_str(), payload.c_str(), retain);
}

bool publishPhoto(const char *tag, const uint8_t *jpeg, size_t len) {
    if (!connected()) {
        return false;
    }
    // Streamed so a 100+ KB JPEG never has to fit in the MQTT buffer.
    if (!mqtt.beginPublish((base + "photo/" + tag).c_str(), len, false)) {
        return false;
    }
    for (size_t pos = 0; pos < len; pos += kPhotoChunk) {
        size_t n = min(kPhotoChunk, len - pos);
        if (mqtt.write(jpeg + pos, n) != n) {
            return false;
        }
    }
    return mqtt.endPublish();
}

} // namespace remote
