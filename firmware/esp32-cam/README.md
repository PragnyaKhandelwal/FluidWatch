# ESP32-CAM firmware

Camera unit firmware for an AI-Thinker ESP32-CAM board with an OV5640 5MP
camera module. Captures a JPEG on a timer and uploads it to the backend's
`POST /api/v1/frames` - the server runs the actual fluid-level model on
it and broadcasts the result to the dashboard. This firmware does no
image analysis itself; it's deliberately dumb (connect WiFi, capture,
upload, repeat).

Replaces two earlier, abandoned hardware attempts (ESP32-S3 + OV3660,
Raspberry Pi 3B+ + Camera Module) - neither is in this repo anymore.

## Hardware

- AI-Thinker ESP32-CAM (ESP32-D0WDQ6, 4MB PSRAM, OV5640 5MP camera)
- **No onboard USB** - you need a separate FTDI/USB-TTL adapter to flash it
- 5V/2A+ power supply - the datasheet's own FAQ flags under-2A supplies
  and thin jumper wires as the most common cause of random resets/camera
  init failures

### Wiring for flashing

| FTDI adapter | ESP32-CAM |
|---|---|
| 5V | 5V |
| GND | GND |
| TX | U0R |
| RX | U0T |

Additionally, **tie GPIO0 to GND** before powering on to enter flashing
mode. After a successful upload, remove that jumper and press the reset
button to run the firmware normally - leaving GPIO0 grounded keeps it
stuck in flashing mode.

## Setup

```sh
cd firmware/esp32-cam
cp include/secrets.h.example include/secrets.h
# edit include/secrets.h: WiFi SSID/password, SERVER_URL, API_AUTH_TOKEN
pio run --target upload
pio device monitor
```

`SERVER_URL` needs the backend's LAN IP (check with `ipconfig` on
whatever machine runs `server`) plus the `/api/v1/frames` path,
e.g. `http://192.168.1.34:8000/api/v1/frames`. `API_AUTH_TOKEN` must
match the backend's - see `server/.env` (auto-generated on
first run; check its startup log) or `server/README.md`'s auth
section.

## What it does

1. `setup()`: initializes the OV5640 camera (fails loudly and halts if
   PSRAM isn't detected or init otherwise fails - check the serial
   monitor), then connects WiFi.
2. `loop()`: every `SEND_INTERVAL_MS` (`include/config.h`, default 10s),
   captures a JPEG and POSTs it as `multipart/form-data` to
   `SERVER_URL?device_id=...&bed_label=...&patient_name=...&fluid_label=...`
   with the bearer token, same as the backend's `/docs` Swagger UI would.
3. Logs the HTTP status and response body to serial either way - watch
   `pio device monitor` to confirm frames are actually landing (should
   see `201` and a JSON reading back).

## Tuning capture quality vs. upload speed

`include/config.h`'s `CAPTURE_FRAME_SIZE` / `CAPTURE_JPEG_QUALITY`
control the tradeoff: bigger/higher-quality frames give the model more
detail but take longer to upload over WiFi (and the model resizes
everything to 224×224 internally anyway - see
`server/app/cv/real_frame_processor.py` - so there's a point
past which more resolution stops helping). `FRAMESIZE_VGA` (640×480) is
the starting default; only go higher if testing shows the model actually
needs it.

## Troubleshooting

- **Camera init fails / board resets in a loop**: almost always power -
  use a real 5V/2A+ supply and short, thick wires, not a thin
  jumper-wire chain from a laptop USB port.
- **"no PSRAM found" on serial**: firmware falls back to a small
  DRAM-only frame automatically, but if you're seeing this, PSRAM isn't
  being detected/enabled - double check `platformio.ini`'s
  `BOARD_HAS_PSRAM` build flag actually applied (clean and rebuild if
  you changed it after a previous build).
- **Upload always fails / times out**: confirm `SERVER_URL` is reachable
  from the ESP32's network (same WiFi as the machine running
  `server`, correct LAN IP, backend actually running), and that
  `API_AUTH_TOKEN` matches exactly.
