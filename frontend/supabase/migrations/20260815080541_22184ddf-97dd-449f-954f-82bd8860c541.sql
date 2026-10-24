CREATE TYPE public.app_role AS ENUM ('admin', 'nurse');

CREATE TABLE public.profiles (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  full_name TEXT NOT NULL DEFAULT '',
  ward TEXT NOT NULL DEFAULT 'Ward A',
  job_title TEXT NOT NULL DEFAULT 'Nurse',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT, UPDATE, DELETE ON public.profiles TO authenticated;
GRANT ALL ON public.profiles TO service_role;
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;

CREATE TABLE public.user_roles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role public.app_role NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, role)
);
GRANT SELECT ON public.user_roles TO authenticated;
GRANT ALL ON public.user_roles TO service_role;
ALTER TABLE public.user_roles ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION public.has_role(_user_id UUID, _role public.app_role)
RETURNS BOOLEAN LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (SELECT 1 FROM public.user_roles WHERE user_id = _user_id AND role = _role)
$$;

CREATE POLICY "Signed-in staff can view profiles" ON public.profiles
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "Users can insert their own profile" ON public.profiles
  FOR INSERT TO authenticated WITH CHECK (id = auth.uid());
CREATE POLICY "Users can update their own profile" ON public.profiles
  FOR UPDATE TO authenticated USING (id = auth.uid()) WITH CHECK (id = auth.uid());
CREATE POLICY "Admins can update any profile" ON public.profiles
  FOR UPDATE TO authenticated USING (public.has_role(auth.uid(), 'admin')) WITH CHECK (public.has_role(auth.uid(), 'admin'));
CREATE POLICY "Admins can delete profiles" ON public.profiles
  FOR DELETE TO authenticated USING (public.has_role(auth.uid(), 'admin'));

CREATE POLICY "Signed-in staff can view roles" ON public.user_roles
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "Admins manage roles" ON public.user_roles
  FOR ALL TO authenticated USING (public.has_role(auth.uid(), 'admin')) WITH CHECK (public.has_role(auth.uid(), 'admin'));

CREATE TABLE public.ward_settings (
  id BOOLEAN PRIMARY KEY DEFAULT true,
  watch_level NUMERIC NOT NULL DEFAULT 35,
  critical_level NUMERIC NOT NULL DEFAULT 10,
  min_flow NUMERIC NOT NULL DEFAULT 15,
  ws_url TEXT NOT NULL DEFAULT 'ws://localhost:8765',
  sound_alerts BOOLEAN NOT NULL DEFAULT true,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ward_settings_singleton CHECK (id)
);
GRANT SELECT, INSERT, UPDATE ON public.ward_settings TO authenticated;
GRANT ALL ON public.ward_settings TO service_role;
ALTER TABLE public.ward_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Signed-in staff can view ward settings" ON public.ward_settings
  FOR SELECT TO authenticated USING (true);
CREATE POLICY "Admins can change ward settings" ON public.ward_settings
  FOR ALL TO authenticated USING (public.has_role(auth.uid(), 'admin')) WITH CHECK (public.has_role(auth.uid(), 'admin'));

INSERT INTO public.ward_settings (id) VALUES (true);

CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE
  is_first BOOLEAN;
BEGIN
  INSERT INTO public.profiles (id, full_name, job_title)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.raw_user_meta_data->>'name', ''),
    COALESCE(NEW.raw_user_meta_data->>'job_title', 'Nurse')
  )
  ON CONFLICT (id) DO NOTHING;

  SELECT NOT EXISTS (SELECT 1 FROM public.user_roles) INTO is_first;
  INSERT INTO public.user_roles (user_id, role)
  VALUES (NEW.id, CASE WHEN is_first THEN 'admin'::public.app_role ELSE 'nurse'::public.app_role END)
  ON CONFLICT DO NOTHING;

  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
AFTER INSERT ON auth.users
FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();