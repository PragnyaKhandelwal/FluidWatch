-- Training-data collection: every ESP32-CAM frame the backend receives is
-- archived to the private `training-frames` Storage bucket, with one
-- `training_frames` row per image. The backend writes with the service-role
-- key (bypasses RLS); signed-in FluidWatch admins can read and label.

-- One row per archived image.
CREATE TABLE public.training_frames (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  storage_path TEXT NOT NULL UNIQUE,
  device_id TEXT NOT NULL,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  bytes INTEGER NOT NULL,
  width INTEGER,
  height INTEGER,
  -- What the deployed model said about this frame (useful for spotting
  -- domain-gap failures, not ground truth).
  predicted_fill_percent REAL,
  predicted_class TEXT,
  -- Ground truth. Stamped from capture_settings at upload time, or filled
  -- in afterwards from the Table Editor.
  label_fill_percent REAL CHECK (label_fill_percent BETWEEN 0 AND 100),
  label_yaw_deg REAL CHECK (label_yaw_deg BETWEEN 0 AND 90),
  session TEXT,
  notes TEXT
);

CREATE INDEX training_frames_device_time_idx ON public.training_frames (device_id, captured_at DESC);
CREATE INDEX training_frames_session_idx ON public.training_frames (session);

ALTER TABLE public.training_frames ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Admins can read training frames" ON public.training_frames
  FOR SELECT TO authenticated USING (public.has_role(auth.uid(), 'admin'));
CREATE POLICY "Admins can label training frames" ON public.training_frames
  FOR UPDATE TO authenticated
  USING (public.has_role(auth.uid(), 'admin')) WITH CHECK (public.has_role(auth.uid(), 'admin'));

-- Single row: what is in front of the camera right now. Set it before a
-- collection run ("bottle at 80%, yaw 30, session run-03") and the backend
-- stamps those labels onto every frame it archives until it's changed.
CREATE TABLE public.capture_settings (
  id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  session TEXT,
  label_fill_percent REAL CHECK (label_fill_percent BETWEEN 0 AND 100),
  label_yaw_deg REAL CHECK (label_yaw_deg BETWEEN 0 AND 90),
  notes TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.capture_settings DEFAULT VALUES;

ALTER TABLE public.capture_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Admins can read capture settings" ON public.capture_settings
  FOR SELECT TO authenticated USING (public.has_role(auth.uid(), 'admin'));
CREATE POLICY "Admins can update capture settings" ON public.capture_settings
  FOR UPDATE TO authenticated
  USING (public.has_role(auth.uid(), 'admin')) WITH CHECK (public.has_role(auth.uid(), 'admin'));

-- Private bucket for the images themselves (5 MB cap per file; a VGA JPEG
-- from the ESP32-CAM is well under 100 KB).
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('training-frames', 'training-frames', FALSE, 5242880, ARRAY['image/jpeg', 'image/png'])
ON CONFLICT (id) DO NOTHING;

CREATE POLICY "Admins can read training images" ON storage.objects
  FOR SELECT TO authenticated
  USING (bucket_id = 'training-frames' AND public.has_role(auth.uid(), 'admin'));
