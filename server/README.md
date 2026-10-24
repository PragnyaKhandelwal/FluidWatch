# Smart IV Drip System - Backend

FastAPI service that ingests readings from the ESP32 firmware, stores them,
pushes live updates to dashboards over WebSocket, and exports logs for
EMR/records purposes. Replaces the earlier `mock_server.py` stand-in.

## Layout

- `app/models.py` / `app/schemas.py` - the `Reading` shape. Field names
  originally mirrored an earlier ESP32-S3 prototype's `IVDripReading`
  struct. The current ESP32-CAM firmware (`firmware/esp32-cam/`) uploads
  images to `/api/v1/frames` instead, and the server builds the reading
  itself - `POST /api/v1/readings` stays for any device that reports
  numbers directly.
- `app/routers/readings.py` - ingest (`POST /api/v1/readings`), list, latest,
  and CSV/Excel export.
- `app/routers/ws.py` - two WebSocket feeds, both broadcast the instant a
  reading is ingested: `/ws/readings` (full `IVDripReading` shape) and
  `/ws/bedfeed` (reshaped for the `frontend/` dashboard - see below).
- `app/bedfeed.py` - translates a `Reading` into the `{id, bed, patient,
  fluid, flow, level, status}` shape `frontend/src/lib/fluidwatch.ts`
  expects, mapping our richer `status` enum down to its `STABLE/WATCH/CRITICAL`.
- `app/routers/frames.py` - the camera-frame path (`POST /api/v1/frames`):
  takes an uploaded image, runs it through `active_processor`
  (`app/cv/pipeline.py`) to get a fill %, then `build_reading()`
  (`app/cv/reading_builder.py`) turns that into a full reading - same DB
  row and WebSocket broadcast as the ESP32 JSON path. Runs a real trained
  model (`app/cv/real_frame_processor.py`, MobileNetV3-Small) as of
  2026-08-21 - see **`CV_INTEGRATION.md`** for what it is and how to
  update it. Test it with any image, no camera/device needed, via the
  file-upload widget at `/docs`.
- `app/db.py` - SQLAlchemy engine/session. SQLite by default; swap
  `DATABASE_URL` to Postgres later without touching app code.

## Setup

```
cd frontend/server   # from the project root; this repo's root is frontend/
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

No `.env` needed to get started — defaults match what's already in the
firmware's `secrets.h` (`SERVER_URL` pointing at this machine's LAN IP,
port 8000). Copy `.env.example` to `.env` if you want to set
`DATABASE_URL` or restrict `CORS_ORIGINS`.

**Auth is on by default.** If `API_AUTH_TOKEN` isn't set, `app/config.py`
generates a random one on first startup and writes it to `.env` (check the
startup log). Every `POST /api/v1/readings` after that requires
`Authorization: Bearer <token>` - copy the printed value into the ESP32
firmware's `secrets.h` (`API_AUTH_TOKEN`) so it can still authenticate. The
token is stable across restarts once written; delete it from `.env` to have
a new one generated.

## Endpoints

- `POST /api/v1/readings` - what the ESP32 sends every `SEND_INTERVAL_MS`.
- `GET /api/v1/readings?device_id=&limit=` - recent readings.
- `GET /api/v1/readings/latest?device_id=` - most recent reading.
- `GET /api/v1/readings/export.csv` / `.xlsx` - full log export, for
  hospital record-keeping / EMR hand-off.
- `WS /ws/readings` - live feed, full reading shape.
- `WS /ws/bedfeed` - live feed, shaped for `frontend/`'s dashboard.
- `POST /api/v1/frames` - camera-frame uploads (what the ESP32-CAM sends
  every `SEND_INTERVAL_MS`); runs the real fill-level model.
- `GET /api/v1/frames/latest?device_id=` - the most recent photo received
  from that device (what its last reading was computed from) - no auth,
  same as the other GET endpoints, since browsers can't attach a bearer
  token to an `<img src>` load. The dashboard's bed cards use this.
- `GET /health` - liveness check.
- `GET /docs` - interactive Swagger UI (FastAPI auto-generated).

## Verifying against the firmware and dashboard

1. Run `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000` here.
2. Flash the firmware (see `firmware/esp32-cam/README.md`); it uploads a
   photo to `SERVER_URL` from `secrets.h` on its capture timer.
3. Watch readings arrive at `GET http://localhost:8000/api/v1/readings/latest`
   or in `/docs`.
4. Run the dashboard (see `frontend/README.md`) and hit "Connect ward
   server" - it points at `ws://localhost:8000/ws/bedfeed` by default and a
   new bed card appears for the ESP32's `DEVICE_ID` as readings arrive.

## Tests

```
pip install -r requirements-dev.txt
pytest
```

`tests/` covers every endpoint (auth, validation, ordering, exports), both
WebSocket feeds, the bedfeed translation, `reading_builder`'s
status/flow derivation, frame storage path sanitizing, and smoke tests
that the real model checkpoint loads and returns sane output. Tests use a
throwaway SQLite DB and frames dir - they never touch `data/` or `.env`.

`e2e/smoke_test.py` is different: it drives an already-running server
from the outside (CI runs it against the Docker image).

## Docker

```
docker build -t iv-drip-backend .
docker run -p 8000:8000 -e API_AUTH_TOKEN=<token> -v iv-data:/app/data iv-drip-backend
```

or `API_AUTH_TOKEN=<token> docker compose up --build` from the repo root.
Always pass `API_AUTH_TOKEN` to a container - the auto-generate-into-.env
fallback would otherwise mint a new token each time the container is
recreated and lock the ESP32 out.

## Deploying (Render free tier)

Render's free Web Services run a real long-lived process (unlike serverless
functions), which is what this app needs - `ws_manager.py` holds active
WebSocket connections in memory. Trade-off: a free service spins down after
15 minutes idle and takes ~30-60s to wake back up on the next request/socket
connect. `render.yaml` here is a Render Blueprint that captures the service
config; you can also skip it and fill the same values into the dashboard by
hand.

This folder lives inside the `frontend/` git repo (`server/` is a subfolder,
alongside `src/`) rather than its own repo, so Render deploys it from the
same repo as the dashboard - just pointed at a different subfolder via
**Root Directory**.

### Steps

1. Render Dashboard → **New → Web Service** → connect the
   `PragnyaKhandelwal/SIH26` repo (same one the dashboard deploys from).
   - **Root Directory**: `server`.
   - **Runtime**: Python 3.
   - **Build Command**: `pip install -r requirements.txt`.
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
     - note `$PORT`, not a hardcoded `8000` - Render assigns the port.
   - **Health Check Path**: `/health`.
   - **Instance Type**: Free.
2. **Environment** tab - add:
   - `CORS_ORIGINS` = your Vercel frontend's origin, e.g.
     `https://your-app.vercel.app` (comma-separate multiple origins).
   - `API_AUTH_TOKEN` = generate one yourself and set it explicitly here
     (e.g. `python -c "import secrets; print(secrets.token_urlsafe(24))"`).
     You *can* leave it unset and let `app/config.py` self-generate one on
     first boot instead, but Render's disk is ephemeral - a fresh deploy
     wipes it and generates a new one, breaking any firmware already
     configured with the old value. Setting it explicitly keeps it stable.
   - Leave `DATABASE_URL` unset to use SQLite - fine for a demo, but note
     it resets on every deploy (Render's free tier has no persistent disk).
     For real persistence, add a Render Postgres instance and set
     `DATABASE_URL` to its connection string.
3. Deploy. Render assigns a `https://<name>.onrender.com` URL.
4. Copy the same `API_AUTH_TOKEN` value into the ESP32's `secrets.h`.
5. Point the frontend at this service: set `ward_settings.ws_url` (via
   `/admin`, or update `DEFAULT_WS_URL` in
   `frontend/src/lib/fluidwatch.ts` and rebuild) to
   `wss://<name>.onrender.com/ws/bedfeed` - **`wss://`, not `ws://`**;
   browsers block a plaintext WebSocket from an `https:` page.
6. Update the ESP32's `secrets.h` `SERVER_URL` to
   `https://<name>.onrender.com/api/v1/readings`.

## Note on the existing Supabase project

`frontend/` also talks to a pre-existing Supabase project for staff
login/roles and the `ward_settings` table (which stores the dashboard's
WebSocket URL, editable from its Admin panel). That's orthogonal to this
service - Supabase handles auth/config, this FastAPI service handles the
actual device telemetry. Nothing here touches Supabase.
