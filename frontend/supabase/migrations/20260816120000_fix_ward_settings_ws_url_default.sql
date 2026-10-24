-- The original default/seed pointed at the old standalone mock_server.py
-- (ws://localhost:8765). The backend now lives at server/ and serves the
-- ward feed at /ws/bedfeed on port 8000 - keep new projects and any row
-- still on the old default in sync with frontend/src/lib/fluidwatch.ts's
-- DEFAULT_WS_URL.

ALTER TABLE public.ward_settings
  ALTER COLUMN ws_url SET DEFAULT 'ws://localhost:8000/ws/bedfeed';

UPDATE public.ward_settings
SET ws_url = 'ws://localhost:8000/ws/bedfeed'
WHERE ws_url = 'ws://localhost:8765';
