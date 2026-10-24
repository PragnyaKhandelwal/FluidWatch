import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BellRing,
  Droplet,
  Gauge,
  Plug,
  RefreshCw,
  Volume2,
  VolumeX,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { AppShell } from "@/components/AppShell";
import { BedCard } from "@/components/BedCard";
import { useAuth } from "@/lib/auth";
import { useLiveBeds } from "@/lib/liveBeds";
import { UNITS, type UnitKey } from "@/lib/fluidwatch";

export const Route = createFileRoute("/_authenticated/dashboard")({
  head: () => ({
    meta: [
      { title: "Ward Dashboard — FluidWatch Live Bed Monitor" },
      {
        name: "description",
        content:
          "Live infusion dashboard: flow rate, fluid level, ward-wide statistics and critical alerts for every bed.",
      },
      { property: "og:title", content: "Ward Dashboard — FluidWatch" },
      {
        property: "og:description",
        content: "Bed-by-bed infusion telemetry with ward statistics and alerts.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: DashboardPage,
});

type Filter = "all" | "critical" | "watch" | "stable";

function StatCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = "default",
}: {
  icon: typeof Gauge;
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "success" | "warn" | "critical";
}) {
  const toneClass =
    tone === "critical"
      ? "text-critical"
      : tone === "warn"
        ? "text-warn"
        : tone === "success"
          ? "text-success"
          : "text-foreground";
  const chipClass =
    tone === "critical"
      ? "bg-critical-soft text-critical"
      : tone === "warn"
        ? "bg-warn-soft text-warn"
        : tone === "success"
          ? "bg-success-soft text-success"
          : "bg-primary/10 text-primary";
  return (
    <article className="glass-card rounded-2xl p-5 transition-all hover:-translate-y-0.5 hover:shadow-lg">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold tracking-widest text-muted-foreground">
          {label.toUpperCase()}
        </p>
        <span className={cn("flex size-8 items-center justify-center rounded-lg", chipClass)}>
          <Icon className="size-4" />
        </span>
      </div>
      <p className={cn("mt-3 text-3xl font-bold tracking-tight", toneClass)}>{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </article>
  );
}

function DashboardPage() {
  const { profile } = useAuth();
  const {
    beds,
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
  } = useLiveBeds();
  const [filter, setFilter] = useState<Filter>("all");
  const [unit, setUnit] = useState<UnitKey>("ml");

  const stats = useMemo(() => {
    const critical = beds.filter((b) => b.status === "CRITICAL").length;
    const watch = beds.filter((b) => b.status === "WATCH").length;
    const stable = beds.filter((b) => b.status === "STABLE").length;
    const avgLevel = beds.reduce((s, b) => s + b.level, 0) / (beds.length || 1);
    const avgFlow = beds.reduce((s, b) => s + b.flow, 0) / (beds.length || 1);
    return { critical, watch, stable, avgLevel, avgFlow };
  }, [beds]);

  const visible = useMemo(
    () => beds.filter((b) => (filter === "all" ? true : b.status === filter.toUpperCase())),
    [beds, filter],
  );

  return (
    <AppShell>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-success">
            <span className="size-1.5 animate-pulse rounded-full bg-success" /> LIVE MONITOR
          </span>
          <h1 className="mt-1 text-3xl font-bold tracking-tight">
            <span className="text-gradient">{profile?.ward ?? "Ward A"}</span> — Live Bed Monitor
          </h1>
          <p className="mt-1 text-muted-foreground">
            Welcome back{profile?.full_name ? `, ${profile.full_name}` : ""} · {beds.length} beds
            under observation
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div className="glass-panel flex rounded-xl p-1">
            {(["all", "critical", "watch", "stable"] as Filter[]).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-sm font-medium capitalize transition-colors",
                  filter === f ? "bg-secondary text-foreground" : "text-muted-foreground",
                )}
              >
                {f === "all" ? "All beds" : f}
              </button>
            ))}
          </div>
          <label className="glass-panel flex items-center gap-3 rounded-xl px-4 py-2 text-sm font-medium">
            {sound ? <Volume2 className="size-4" /> : <VolumeX className="size-4" />}
            Sound
            <Switch checked={sound} onCheckedChange={setSound} aria-label="Sound alerts" />
          </label>
        </div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard
          icon={Activity}
          label="Beds monitored"
          value={String(beds.length)}
          hint="Active infusion lines"
        />
        <StatCard
          icon={AlertTriangle}
          label="Critical"
          value={String(stats.critical)}
          hint="Needs a nurse now"
          tone={stats.critical ? "critical" : "default"}
        />
        <StatCard
          icon={BellRing}
          label="Watch"
          value={String(stats.watch)}
          hint="Approaching thresholds"
          tone="warn"
        />
        <StatCard
          icon={Droplet}
          label="Avg fluid level"
          value={`${Math.round(stats.avgLevel)}%`}
          tone="success"
        />
        <StatCard
          icon={Gauge}
          label="Avg flow"
          value={`${Math.round(stats.avgFlow)} ml/hr`}
          hint="Ward average"
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="grid gap-6 md:grid-cols-2">
          {visible.map((b) => (
            <BedCard
              key={b.id}
              bed={b}
              unit={unit}
              httpOrigin={httpOrigin}
              onToggleMute={toggleMute}
            />
          ))}
          {visible.length === 0 && (
            <p className="text-muted-foreground">No beds match this filter.</p>
          )}
        </div>

        <aside className="space-y-6">
          <section className="glass-card rounded-2xl p-5">
            <h2 className="text-sm font-semibold tracking-widest text-muted-foreground">
              ALERT FEED
            </h2>
            <div className="mt-4 space-y-3">
              {log.length === 0 && (
                <p className="text-sm text-muted-foreground">No alerts recorded yet.</p>
              )}
              {log.slice(0, 8).map((l) => (
                <div
                  key={l.id}
                  className="rounded-xl border border-critical bg-critical-soft px-3 py-2"
                >
                  <p className="text-sm font-medium text-critical">{l.text}</p>
                  <p className="text-xs text-muted-foreground">
                    {new Date(l.at).toLocaleTimeString()}
                  </p>
                </div>
              ))}
            </div>
          </section>

          <section className="glass-card rounded-2xl p-5">
            <h2 className="text-sm font-semibold tracking-widest text-muted-foreground">
              STREAM CONTROL
            </h2>
            <label className="mt-4 flex items-center justify-between text-sm font-medium">
              <span className="flex items-center gap-2">
                <RefreshCw
                  className={cn("size-4", autoRefresh && "animate-spin [animation-duration:3s]")}
                />
                Auto-refresh
              </span>
              <Switch
                checked={autoRefresh}
                onCheckedChange={setAutoRefresh}
                aria-label="Auto refresh"
              />
            </label>
            <div className="mt-4 flex items-center justify-between gap-3">
              <span className="text-sm text-muted-foreground">Flow units</span>
              <Select value={unit} onValueChange={(v) => setUnit(v as UnitKey)}>
                <SelectTrigger className="w-28">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(UNITS).map(([k, v]) => (
                    <SelectItem key={k} value={k}>
                      {v.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              variant={wantLive ? "default" : "outline"}
              className="mt-4 w-full"
              onClick={() => setWantLive((v) => !v)}
            >
              <Plug className="size-4" />
              {wantLive ? "Disconnect ward server" : "Connect ward server"}
            </Button>
            <p className="mt-2 break-all text-xs text-muted-foreground">{wsUrl}</p>
          </section>
        </aside>
      </div>

      <section className="mt-8">
        <h2 className="text-xl font-semibold tracking-tight">Patients</h2>
        <div className="glass-card mt-4 overflow-hidden rounded-2xl">
          {beds.map((b) => (
            <div
              key={b.id}
              className="flex items-center justify-between gap-4 border-b border-border px-5 py-3 last:border-0"
            >
              <div>
                <p className="font-semibold">{b.patient}</p>
                <p className="text-sm text-muted-foreground">
                  {b.bed} · {b.fluid}
                </p>
              </div>
              <p className="text-sm text-muted-foreground">
                {Math.round(b.flow)} ml/hr · {Math.round(b.level)}%
              </p>
              <span
                className={cn(
                  "rounded-md px-2 py-1 text-xs font-semibold",
                  b.status === "CRITICAL"
                    ? "bg-critical-soft text-critical"
                    : b.status === "WATCH"
                      ? "bg-warn-soft text-warn"
                      : "bg-success-soft text-success",
                )}
              >
                {b.status}
              </span>
            </div>
          ))}
        </div>
      </section>
    </AppShell>
  );
}
