#include "ota.h"

#include <HTTPClient.h>
#include <Preferences.h>
#include <Update.h>
#include <WiFiClientSecure.h>
#include <esp_ota_ops.h>
#include <mbedtls/sha256.h>

namespace ota {

namespace {

constexpr unsigned long kConfirmWindowMs = 5UL * 60UL * 1000UL;
constexpr uint8_t kMaxPendingBoots = 3;
constexpr unsigned long kStallTimeoutMs = 20000UL;

Preferences prefs;
bool pending = false;
unsigned long confirmDeadlineMs = 0;

void rollBack(const char *reason) {
    String version = prefs.getString("ver", "?");
    prefs.putBool("pending", false);
    prefs.putString("result", "rolled back from " + version + ": " + reason);
    Serial.printf("[OTA] rolling back from %s: %s\n", version.c_str(), reason);

    const esp_partition_t *previous = esp_ota_get_next_update_partition(nullptr);
    if (previous != nullptr && esp_ota_set_boot_partition(previous) == ESP_OK) {
        delay(200);
        ESP.restart();
    }
    Serial.println("[OTA] no previous image to roll back to - staying on this one");
}

String toHex(const uint8_t *bytes, size_t len) {
    static const char digits[] = "0123456789abcdef";
    String out;
    out.reserve(len * 2);
    for (size_t i = 0; i < len; ++i) {
        out += digits[bytes[i] >> 4];
        out += digits[bytes[i] & 0x0f];
    }
    return out;
}

} // namespace

bool install(const char *url, const char *sha256Hex, size_t expectedSize, const char *version,
             ProgressFn progress, String &error) {
    if (strlen(sha256Hex) != 64) {
        error = "sha256 must be 64 hex characters";
        return false;
    }

    // Transport integrity isn't needed here: the SHA-256 check below rejects
    // anything that isn't exactly the released image.
    WiFiClientSecure client;
    client.setInsecure();
    HTTPClient http;
    http.setTimeout(kStallTimeoutMs);
    if (!http.begin(client, url)) {
        error = "bad URL";
        return false;
    }
    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        error = "download failed, HTTP " + String(code);
        http.end();
        return false;
    }
    int total = http.getSize();
    if (total <= 0 || (expectedSize > 0 && (size_t)total != expectedSize)) {
        error = "unexpected size " + String(total);
        http.end();
        return false;
    }
    if (!Update.begin(total)) {
        error = String("no room for image: ") + Update.errorString();
        http.end();
        return false;
    }

    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts_ret(&sha, 0);

    static uint8_t buf[2048];
    WiFiClient *stream = http.getStreamPtr();
    size_t written = 0;
    int lastReported = -10;
    unsigned long lastDataMs = millis();
    while (written < (size_t)total) {
        size_t avail = stream->available();
        if (avail == 0) {
            if (millis() - lastDataMs > kStallTimeoutMs) {
                error = "download stalled";
                break;
            }
            delay(2);
            continue;
        }
        int n = stream->readBytes(buf, min(avail, sizeof(buf)));
        if (n <= 0) {
            continue;
        }
        mbedtls_sha256_update_ret(&sha, buf, n);
        if (Update.write(buf, n) != (size_t)n) {
            error = String("flash write failed: ") + Update.errorString();
            break;
        }
        written += n;
        lastDataMs = millis();
        int percent = written * 100 / total;
        if (progress != nullptr && percent >= lastReported + 10) {
            lastReported = percent;
            progress(percent);
        }
    }
    http.end();

    uint8_t digest[32];
    mbedtls_sha256_finish_ret(&sha, digest);
    mbedtls_sha256_free(&sha);

    if (error.length() == 0 && !toHex(digest, sizeof(digest)).equalsIgnoreCase(sha256Hex)) {
        error = "SHA-256 mismatch - image rejected";
    }
    if (error.length() > 0) {
        Update.abort();
        return false;
    }
    if (!Update.end(true)) {
        error = String("finalise failed: ") + Update.errorString();
        return false;
    }

    prefs.putBool("pending", true);
    prefs.putUChar("boots", 0);
    prefs.putString("ver", version);
    return true;
}

void bootCheck() {
    prefs.begin("ota", false);
    pending = prefs.getBool("pending", false);
    if (!pending) {
        return;
    }
    uint8_t boots = prefs.getUChar("boots", 0) + 1;
    prefs.putUChar("boots", boots);
    Serial.printf("[OTA] new image %s pending confirmation (boot %u)\n", prefs.getString("ver", "?").c_str(), boots);
    if (boots > kMaxPendingBoots) {
        rollBack("kept crashing");
    }
    confirmDeadlineMs = millis() + kConfirmWindowMs;
}

void confirm() {
    if (!pending) {
        return;
    }
    pending = false;
    prefs.putBool("pending", false);
    prefs.putString("result", "updated to " + prefs.getString("ver", "?"));
    Serial.println("[OTA] new image confirmed");
}

void loop() {
    if (pending && (long)(millis() - confirmDeadlineMs) > 0) {
        rollBack("never reached the broker");
    }
}

String lastResult() {
    return prefs.getString("result", "");
}

} // namespace ota
