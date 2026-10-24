# FluidWatch — Ward Dashboard

The staff-facing dashboard for the Smart IV Drip System project: sign in,
see every bed's live fluid level/flow rate, get alerted when one goes
critical. Lives in `frontend/` of the FluidWatch repo, next to `server/`
(the FastAPI backend it gets live telemetry from) and `firmware/esp32-cam/`.
See the [root README](../README.md) for how the pieces fit together and for
tests/CI.

## Built with

TanStack Start · TypeScript · React · Tailwind CSS · Supabase (auth/roles) ·
a raw WebSocket to `server/`'s `/ws/bedfeed` (live telemetry)

## Local development

Requires Node.js.

```sh
cd frontend
npm i
npm run dev
```

`.env` is already committed with a working Supabase URL + publishable key
(safe to expose - it's an RLS-protected anon key, not a secret) pointing at
the project described in Option A below. Only touch it if you're doing
Option B.

Opens on `http://localhost:8080`. Sign up - **the first account created
becomes the ward admin** (see the `handle_new_user` trigger in
`supabase/migrations/`).

To see live device data instead of the built-in simulator: run `server/`
(`uvicorn app.main:app --port 8000`, see `server/README.md`), sign in here,
go to `/dashboard`, and click **"Connect ward server."** Without a live
connection the dashboard free-runs a client-side random-walk simulator on
six demo beds so the UI is never empty.

## Environment variables

| Variable | Used by | Notes |
|---|---|---|
| `VITE_SUPABASE_URL` | browser bundle | baked in at build time |
| `VITE_SUPABASE_PUBLISHABLE_KEY` | browser bundle | baked in at build time |
| `SUPABASE_URL` | SSR (server-side render) | same value, no `VITE_` prefix |
| `SUPABASE_PUBLISHABLE_KEY` | SSR | same value, no `VITE_` prefix |
| `SUPABASE_PROJECT_ID` | tooling (e.g. `supabase` CLI) | |

Locally, `.env` covers all of these (see `src/integrations/supabase/client.ts`,
which reads `import.meta.env` client-side and falls back to `process.env`
server-side). **On a real deployment these are two different mechanisms** -
see [Deployment](#deployment) below.

There's also a `client.server.ts` with a `supabaseAdmin` service-role client
for server-only admin operations that bypass RLS. Nothing in this app uses
it yet - if you add something that does, it needs `SUPABASE_SERVICE_ROLE_KEY`
set (never expose that key to the browser bundle).

## Configuring Supabase

### Option A - use the existing project (default, recommended)

`.env` already points at a pre-existing Supabase project
(`nkcweoiecmfxpkegytww`) with the schema below applied. If you're working
against that project, there's nothing to set up locally - just `npm run dev`.
Two things worth doing once you're deploying it for real, though:

- **Rename it** - `nkcweoiecmfxpkegytww` is just an auto-generated project
  ref and can't be changed, but the human-readable label shown in the
  dashboard can: **Project Settings → General → Project Name** → e.g.
  "FluidWatch" or "Smart IV Drip System DB". This is cosmetic only - it
  doesn't touch the URL, keys, or anything in `.env`.
- **Add your production URL** once you have a Vercel deployment - see
  "URL Configuration" under Option B below (same steps, same project).

### Option B - stand up your own project

Needed if you're forking this for a deployment that shouldn't share auth/data
with the original project.

1. Create a project at [supabase.com](https://supabase.com/dashboard) (or
   `supabase init` + `supabase start` locally with the CLI).
2. Apply the schema - open the SQL Editor and run each file in
   `supabase/migrations/` **in filename order** (they're timestamped, so
   sorting the folder gives the right order). This creates:
   - `profiles`, `user_roles` (+ `app_role` enum `admin`/`nurse`) - staff
     accounts, RLS'd so only admins can change roles.
   - `ward_settings` - a single-row table (`watch_level`, `critical_level`,
     `min_flow`, `ws_url`, `sound_alerts`) editable from `/admin`, read by
     the dashboard on load.
   - `handle_new_user` trigger - auto-creates a `profiles` row and makes the
     *first* signup an admin, everyone after a nurse.
3. **URL Configuration** (Supabase Dashboard → **Authentication → URL
   Configuration**):
   - **Site URL**: your production URL (e.g. `https://your-app.vercel.app`).
   - **Redirect URLs**: add `http://localhost:8080/**` (local dev),
     `https://your-app.vercel.app/**` (production), and
     `https://*.vercel.app/**` if you want Vercel's per-branch preview
     deployments to be able to sign in too.
   Email confirmation is on by default; turn it off under **Auth →
   Providers → Email** if you want signups usable immediately during
   testing.
4. **Google sign-in** ("Continue with Google" on `/auth`) calls
   `supabase.auth.signInWithOAuth({ provider: "google" })` directly. It
   won't work until you configure a Google OAuth
   client and enable the provider in Supabase:
   1. Supabase Dashboard → **Authentication → Providers → Google** →
      enable it. Copy the **Callback URL (for OAuth)** shown there -
      it looks like `https://<project-ref>.supabase.co/auth/v1/callback`.
   2. [Google Cloud Console](https://console.cloud.google.com/) → create/
      select a project → **APIs & Services → OAuth consent screen** →
      configure it (External, app name, support email - defaults are fine
      for testing).
   3. **APIs & Services → Credentials → Create Credentials → OAuth client
      ID** → Application type **Web application** → under **Authorized
      redirect URIs**, paste the callback URL from step 1.
   4. Copy the generated **Client ID** and **Client Secret** back into
      Supabase's Google provider config from step 1 → **Save**.
   Until this is done, the button will show a "Google sign-in failed"
   toast - email/password sign-in is unaffected either way.
5. `cp .env.example .env` and fill in the new project's URL and publishable
   (`anon`) key from **Settings → API** (both the `VITE_` and non-`VITE_`
   variants - see the table above).
6. Update `ward_settings.ws_url`'s default if your backend won't run on
   `localhost:8000` - either edit the value directly in the SQL Editor, or
   change it after signing in via `/admin`.

## Deployment

Deployed on **Vercel**. Deploying this dashboard
makes it reachable, but it's only useful once `server/` (the WebSocket
source) is *also* running somewhere public over HTTPS - see
`server/README.md`'s Deploying section (Render). Without that, a deployed
dashboard just runs the built-in demo simulator.

`vite.config.ts` hard-pins the build target: `nitro: { preset: "vercel" }`.
`npm run build` produces `.vercel/output/` in Vercel's Build Output API v3
shape directly - Vercel's platform deploys that as-is once it sees it,
skipping its own framework auto-build.

### Deploy via the Vercel dashboard (recommended)

1. [vercel.com](https://vercel.com) → **Add New → Project** → import this
   repo (`PragnyaKhandelwal/FluidWatch`).
2. **Root Directory**: `frontend`.
3. **Build Command**: `npm run build` (auto-detected from `package.json`).
   **Install Command**: `npm install`. Output Directory doesn't matter -
   the Build Output API takes over.
4. **Environment Variables** - add all six from `.env`, for Production *and*
   Preview: `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY`,
   `VITE_SUPABASE_PROJECT_ID`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`,
   `SUPABASE_PROJECT_ID`. Unlike Cloudflare, Vercel uses the same
   dashboard env vars for both build time (`VITE_*`, baked into the
   client bundle) and runtime (the rest, read by the SSR function) - no
   separate secrets step.
5. Deploy. Vercel assigns a `*.vercel.app` domain immediately.
6. Add that domain to Supabase's **Auth → URL Configuration** (see
   Configuring Supabase below) - sign-in will fail until you do.
7. Point `ward_settings.ws_url` (via `/admin`) at the deployed backend's
   `wss://` URL once it's up - `ws://localhost:...` only works locally.

### Deploy via CLI

```sh
npx vercel login    # one-time, opens a browser to authorize your account
npx vercel --prod
```

The Vercel CLI runs its own build (respecting `vite.config.ts`'s `vercel`
preset) and links/creates the project interactively on first run.

## Integration with the Smart IV Drip System

Two separate data sources feed this dashboard:

- **Staff auth, roles, and ward settings** - Supabase (`profiles`,
  `user_roles`, `ward_settings` tables; see Configuring Supabase above).
- **Live device telemetry** - `src/routes/_authenticated/dashboard.tsx`
  opens a raw WebSocket to `ward_settings.ws_url` (admin-editable from
  `/admin`, defaults to `DEFAULT_WS_URL` in `src/lib/fluidwatch.ts`) and
  expects messages shaped `{id, bed, patient, fluid, flow, level, status}`
  (single object or array). `server/app/routers/ws.py`'s `/ws/bedfeed`
  produces exactly this, translating each ESP32 reading via
  `server/app/bedfeed.py`.

`src/lib/fluidwatch.ts`'s `applyLiveUpdate()` merges incoming messages into
the bed list by `id`: an id that matches an existing card (including the 6
demo beds seeded in `INITIAL_BEDS`) updates it in place, and an unrecognized
id (a real device's `DEVICE_ID`) is added as a new card - no manual
provisioning needed on the frontend side when a new IV stand comes online.
Alert thresholds (`WATCH`/`CRITICAL` cutoffs on fluid level and flow) come
from `ward_settings` (admin-editable) rather than being hardcoded - both the
live path and the demo simulator use the same `deriveStatus()`.

To see it live: run `server/` (`uvicorn app.main:app --port 8000`), flash
`firmware/`, run this dashboard, sign in, and hit "Connect ward server" on
`/dashboard`. See `server/README.md` for the full walkthrough. Note the
backend now requires a bearer token by default (auto-generated into
`server/.env` on first run) - the firmware's `secrets.h` needs the same
value in `API_AUTH_TOKEN`.
