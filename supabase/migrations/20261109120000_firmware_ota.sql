-- Remote management of ESP32-CAM units (firmware/tools/fleet.py):
--   * firmware images live in the private `firmware` bucket, one row per
--     release in `firmware_releases` (the SHA-256 the device verifies);
--   * each unit's settings live in `device_config`, and are pushed to the
--     unit as a retained MQTT message.
-- fleet.py writes with the service-role key (bypasses RLS); signed-in admins
-- can read both tables from the dashboard or the Table Editor.

CREATE TABLE public.firmware_releases (
  version TEXT PRIMARY KEY CHECK (version ~ '^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$'),
  sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
  storage_path TEXT NOT NULL UNIQUE,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.firmware_releases ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Admins can read firmware releases" ON public.firmware_releases
  FOR SELECT TO authenticated USING (public.has_role(auth.uid(), 'admin'));

-- Settings JSON as accepted by the firmware (keys from
-- firmware/esp32-cam/src/capture_config.cpp, plus bed_label/patient_name/
-- fluid_label). Only the keys that differ from the firmware defaults.
CREATE TABLE public.device_config (
  device_id TEXT PRIMARY KEY,
  config JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(config) = 'object'),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE public.device_config ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Admins can read device config" ON public.device_config
  FOR SELECT TO authenticated USING (public.has_role(auth.uid(), 'admin'));

-- Private bucket for firmware images. An app slot is 1.9 MB (min_spiffs.csv);
-- devices download through short-lived signed URLs only.
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('firmware', 'firmware', FALSE, 2097152, ARRAY['application/octet-stream'])
ON CONFLICT (id) DO NOTHING;

CREATE POLICY "Admins can read firmware images" ON storage.objects
  FOR SELECT TO authenticated
  USING (bucket_id = 'firmware' AND public.has_role(auth.uid(), 'admin'));
