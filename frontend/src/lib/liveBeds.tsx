import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { toast } from "sonner";
import { useWardSettings } from "@/lib/wardSettings";
import { INITIAL_BEDS, applyLiveUpdate, tick, type Bed } from "@/lib/fluidwatch";

export interface AlertLogEntry {
  id: string;
  text: string;
  at: number;
}

interface LiveBedsValue {
  beds: Bed[];
  connected: boolean;
  wantLive: boolean;
  setWantLive: Dispatch<SetStateAction<boolean>>;
  toggleMute: (id: string) => void;
  log: AlertLogEntry[];
  sound: boolean;
  setSound: Dispatch<SetStateAction<boolean>>;
  autoRefresh: boolean;
  setAutoRefresh: Dispatch<SetStateAction<boolean>>;
  wsUrl: string;
  httpOrigin: string | null;
  settingsLoading: boolean;
  /** Re-pulls ward_settings from Supabase - call after Admin saves a
   * change so this tab's connection picks it up immediately instead of
   * waiting for a reload. See wardSettings.ts for why this exists. */
  refreshWardSettings: () => Promise<void>;
}

const LiveBedsContext = createContext<LiveBedsValue | null>(null);

function beep() {
  try {
    const Ctx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.value = 0.06;
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.18);
  } catch {
    /* audio unavailable */
  }
}

/**
 * Owns the ward WebSocket connection, the live bed list, and critical-alert
 * notifications - mounted once in `_authenticated/route.tsx` (above
 * Dashboard/Analytics/Admin) instead of inside DashboardPage. It used to
 * live entirely in DashboardPage's local state, which meant navigating to
 * Analytics unmounted the component, closed the socket, and reset
 * `wantLive` to false - "connect" didn't survive a page change. Living
 * here, the connection (and critical alerts) persist across every
 * authenticated page.
 */
export function LiveBedsProvider({ children }: { children: ReactNode }) {
  const {
    wsUrl,
    httpOrigin,
    soundAlerts,
    thresholds,
    loading: settingsLoading,
    refresh: refreshWardSettings,
  } = useWardSettings();
  const [beds, setBeds] = useState<Bed[]>(INITIAL_BEDS);
  const [sound, setSound] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [connected, setConnected] = useState(false);
  const [wantLive, setWantLive] = useState(false);
  const [log, setLog] = useState<AlertLogEntry[]>([]);
  const socketRef = useRef<WebSocket | null>(null);
  const prevStatus = useRef<Record<string, Bed["status"]>>({});

  // Ward-wide default, configured by administrators - synced once the
  // settings load, but still locally toggleable from the dashboard.
  useEffect(() => setSound(soundAlerts), [soundAlerts]);

  const applyPayload = useCallback(
    (payload: unknown) => {
      setBeds((current) => applyLiveUpdate(current, payload, thresholds));
    },
    [thresholds],
  );

  useEffect(() => {
    if (!wantLive) {
      socketRef.current?.close();
      socketRef.current = null;
      setConnected(false);
      return;
    }
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl);
    } catch {
      toast.error("Invalid WebSocket URL");
      setWantLive(false);
      return;
    }
    socketRef.current = ws;
    ws.onopen = () => {
      setConnected(true);
      toast.success(`Connected to ${wsUrl}`);
    };
    ws.onmessage = (event) => {
      try {
        applyPayload(JSON.parse(event.data));
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onerror = () => toast.error("WebSocket error — falling back to simulated stream");
    ws.onclose = () => setConnected(false);
    return () => ws.close();
  }, [wantLive, wsUrl, applyPayload]);

  useEffect(() => {
    if (!autoRefresh || connected) return;
    const t = setInterval(() => setBeds((b) => tick(b, thresholds)), 2000);
    return () => clearInterval(t);
  }, [autoRefresh, connected, thresholds]);

  useEffect(() => {
    beds.forEach((b) => {
      const before = prevStatus.current[b.id];
      if (before && before !== b.status && b.status === "CRITICAL") {
        setLog((l) =>
          [
            {
              id: `${b.id}-${Date.now()}`,
              text: `${b.bed} · ${b.patient} went CRITICAL`,
              at: Date.now(),
            },
            ...l,
          ].slice(0, 30),
        );
        toast.error(`${b.bed} critical`, { description: `${b.patient} · ${b.fluid}` });
        if (sound && !b.muted) beep();
      }
      prevStatus.current[b.id] = b.status;
    });
  }, [beds, sound]);

  const toggleMute = useCallback((id: string) => {
    setBeds((bs) => bs.map((b) => (b.id === id ? { ...b, muted: !b.muted } : b)));
  }, []);

  const value = useMemo<LiveBedsValue>(
    () => ({
      beds,
      connected,
      wantLive,
      setWantLive,
      toggleMute,
      log,
      sound,
      setSound,
      autoRefresh,
      setAutoRefresh,
      wsUrl,
      httpOrigin,
      settingsLoading,
      refreshWardSettings,
    }),
    [
      beds,
      connected,
      wantLive,
      toggleMute,
      log,
      sound,
      autoRefresh,
      wsUrl,
      httpOrigin,
      settingsLoading,
      refreshWardSettings,
    ],
  );

  return <LiveBedsContext.Provider value={value}>{children}</LiveBedsContext.Provider>;
}

export function useLiveBeds() {
  const ctx = useContext(LiveBedsContext);
  if (!ctx) throw new Error("useLiveBeds must be used inside LiveBedsProvider");
  return ctx;
}
