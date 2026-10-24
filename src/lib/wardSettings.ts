import { useCallback, useEffect, useMemo, useState } from "react";
import { supabase } from "@/integrations/supabase/client";
import {
  DEFAULT_THRESHOLDS,
  DEFAULT_WS_URL,
  deriveHttpOrigin,
  type Thresholds,
} from "@/lib/fluidwatch";

interface WardSettings {
  wsUrl: string;
  httpOrigin: string | null;
  soundAlerts: boolean;
  thresholds: Thresholds;
}

const DEFAULTS: WardSettings = {
  wsUrl: DEFAULT_WS_URL,
  httpOrigin: deriveHttpOrigin(DEFAULT_WS_URL),
  soundAlerts: true,
  thresholds: DEFAULT_THRESHOLDS,
};

/** The ward-wide data-source config admins set from /admin, persisted to
 * Supabase's `ward_settings` row. Previously fetched only inline in
 * dashboard.tsx; pulled out so AppShell (fleet-status pill) and
 * analytics.tsx can point at the same backend without duplicating the
 * Supabase query.
 *
 * Exposes `refresh()` because this now lives inside LiveBedsProvider,
 * mounted once for the whole authenticated session rather than
 * remounting per page - a mount-only fetch would mean Admin saving a new
 * ws_url never reaches the already-running WebSocket connection until a
 * full reload. admin.tsx calls refresh() right after a successful save
 * so the change takes effect (and the live socket reconnects to the new
 * URL) immediately, in this tab, without waiting on a reload. */
export function useWardSettings() {
  const [settings, setSettings] = useState<WardSettings>(DEFAULTS);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const { data } = await supabase
      .from("ward_settings")
      .select("ws_url, sound_alerts, watch_level, critical_level, min_flow")
      .maybeSingle();
    if (data) {
      const wsUrl = data.ws_url ?? DEFAULT_WS_URL;
      setSettings({
        wsUrl,
        httpOrigin: deriveHttpOrigin(wsUrl),
        soundAlerts: Boolean(data.sound_alerts),
        thresholds: {
          watchLevel: data.watch_level ?? DEFAULT_THRESHOLDS.watchLevel,
          criticalLevel: data.critical_level ?? DEFAULT_THRESHOLDS.criticalLevel,
          minFlow: data.min_flow ?? DEFAULT_THRESHOLDS.minFlow,
        },
      });
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return useMemo(() => ({ ...settings, loading, refresh }), [settings, loading, refresh]);
}
