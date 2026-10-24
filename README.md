# FluidWatch — Smart IV Drip System

An ESP32-CAM unit mounted on an IV stand photographs the bag on a timer; a
backend scores each photo with a trained fill-level model, derives flow
rate and status, and pushes live updates to a dashboard that shows nursing
staff every bed's status and raises alerts when one goes critical.

```
frontend/             Ward dashboard - TanStack Start + React, Supabase auth.   Deploys to Vercel.
server/               FastAPI backend - ingest, fill-level model, storage,      Deploys to Render.
                      exports, WebSocket feeds.
firmware/esp32-cam/   ESP32-CAM (AI-Thinker, OV5640) firmware - captures a photo
                      on a timer and uploads it to the backend. PlatformIO.
ml-model-training/    Training scripts and model artifacts (provenance for the
                      model the backend ships).
.github/workflows/    CI - tests, builds, firmware compile, Docker end-to-end.
docker-compose.yml    Runs the backend locally in Docker.
```

Each folder's own README has the details.

## How the pieces connect

```
ESP32-CAM  --HTTP POST image-->  FastAPI (server/)  --WebSocket-->  Dashboard (frontend/)
                                   MobileNetV3 fill-level   /ws/bedfeed    live bed cards
                                   model, SQLite storage,   /ws/readings   (+ Supabase for
                                   CSV/Excel export                        auth/roles/settings)
```

- `POST /api/v1/frames` takes an image, runs it through the real trained
  model (`server/app/cv/real_frame_processor.py`, see
  `server/CV_INTEGRATION.md`), stores the reading and broadcasts it.
- `server/app/bedfeed.py` is the seam: it reshapes a reading into the
  `{id, bed, patient, fluid, flow, level, status, ...}` message that
  `frontend/src/lib/fluidwatch.ts`'s `applyLiveUpdate()` expects. A device
  the dashboard hasn't seen yet becomes a new bed card automatically.
- The model has been spot-checked end-to-end but **not validated against
  real IV bag photos at scale** - see CV_INTEGRATION.md's caveats before
  trusting specific numbers in a demo.

## Running locally

1. **Backend** - `cd server`, create a venv, `pip install -r requirements.txt`,
   `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
   (see `server/README.md`). Or with Docker, from the repo root:
   `API_AUTH_TOKEN=<token> docker compose up --build`.
2. **Dashboard** - `cd frontend && npm i && npm run dev`, sign in, then on
   `/dashboard` click "Connect ward server".
3. **Firmware** - `cd firmware/esp32-cam`, copy
   `include/secrets.h.example` to `include/secrets.h`, fill in WiFi, the
   backend URL and the same `API_AUTH_TOKEN`, then `pio run -t upload`
   (wiring and flash-mode steps in its README). Without hardware, upload
   any image to `POST /api/v1/frames` from the backend's `/docs` page.

## Testing & CI

Every push/PR to `main` runs `.github/workflows/ci.yml`:

| Job | What it checks |
| --- | --- |
| Backend | `pytest` in `server/` - API, auth, WebSocket feeds, exports, CV pipeline, real-model smoke tests |
| Dashboard | `lint`, `typecheck`, Vitest unit tests, production build in `frontend/` |
| Firmware | `pio run` compiles `firmware/esp32-cam` (against `secrets.h.example`) |
| End-to-end | Builds the backend Docker image, runs it, and drives it from outside: frame upload → real model → stored reading → `/ws/bedfeed` message → REST + CSV/XLSX exports (`server/e2e/smoke_test.py`) |

Locally:

```sh
cd frontend && npm test && npm run lint && npm run typecheck

cd server
pip install -r requirements-dev.txt
pytest

# from the repo root - full stack in Docker, then the E2E check against it
API_AUTH_TOKEN=dev-token docker compose up --build
python server/e2e/smoke_test.py --token dev-token
```

## Deployment

- **Dashboard** → Vercel, project Root Directory `frontend` (see
  `frontend/README.md`).
- **Backend** → Render, Root Directory `server` (see `server/README.md`).
- **Firmware** → flashed over USB-serial; `secrets.h` points it at the
  deployed backend.
