import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Battery,
  BatteryLow,
  Download,
  Droplet,
  Gauge,
  Radio,
  Wifi,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AppShell } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { useLiveBeds } from "@/lib/liveBeds";
import { fetchDevices, fetchReadings, type DeviceStatus, type ReadingRow } from "@/lib/fluidwatch";

export const Route = createFileRoute("/_authenticated/analytics")({
  head: () => ({
    meta: [
      { title: "Analytics — FluidWatch Ward Trends" },
      {
        name: "description",
        content:
          "Historical infusion trends: fluid level and flow rate over time, status distribution and hardware health for every device on the ward.",
      },
      { property: "og:title", content: "Analytics — FluidWatch" },
      {
        property: "og:description",
        content: "Historical trends and hardware health across the ward's IV stands.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: AnalyticsPage,
});

const REFRESH_MS = 15000;

const STATUS_ORDER = ["NORMAL", "LOW_LEVEL", "EMPTY", "OCCLUSION", "ERROR"] as const;
const STATUS_TONE: Record<string, string> = {
  NORMAL: "var(--success)",
  LOW_LEVEL: "var(--warn)",
  EMPTY: "var(--critical)",
  OCCLUSION: "var(--critical)",
  ERROR: "var(--critical)",
};

function StatCard({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: typeof Gauge;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <article className="glass-card rounded-2xl p-5">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold tracking-widest text-muted-foreground">
          {label.toUpperCase()}
        </p>
        <Icon className="size-4 text-foreground" />
      </div>
      <p className="mt-3 text-3xl font-bold tracking-tight">{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </article>
  );
}

/** Averages readings into fixed time buckets - used for the "all devices"
 * view where raw per-device series would overlap into noise. */
function bucketAverage(readings: ReadingRow[], bucketMs = 60_000) {
  const buckets = new Map<number, { levelSum: number; flowSum: number; count: number }>();
  for (const r of readings) {
    const t = Math.floor(new Date(r.received_at).getTime() / bucketMs) * bucketMs;
    const b = buckets.get(t) ?? { levelSum: 0, flowSum: 0, count: 0 };
    b.levelSum += r.fluid_level_percent;
    b.flowSum += r.flow_rate_ml_per_hr;
    b.count += 1;
    buckets.set(t, b);
  }
  return Array.from(buckets.entries())
    .sort(([a], [b]) => a - b)
    .map(([t, b]) => ({
      time: new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      level: Math.round((b.levelSum / b.count) * 10) / 10,
      flow: Math.round((b.flowSum / b.count) * 10) / 10,
    }));
}

function AnalyticsPage() {
  const { httpOrigin, settingsLoading } = useLiveBeds();
  const [deviceId, setDeviceId] = useState<string>("all");
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [readings, setReadings] = useState<ReadingRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!httpOrigin) return;
    let cancelled = false;
    const load = () => {
      Promise.all([
        fetchDevices(httpOrigin),
        fetchReadings(httpOrigin, deviceId === "all" ? undefined : deviceId, 500),
      ])
        .then(([d, r]) => {
          if (cancelled) return;
          setDevices(d);
          setReadings(r);
          setError(null);
        })
        .catch(
          (err) => !cancelled && setError(err instanceof Error ? err.message : "Failed to load"),
        )
        .finally(() => !cancelled && setLoaded(true));
    };
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [httpOrigin, deviceId]);

  const chartData = useMemo(() => {
    const ascending = [...readings].reverse();
    if (deviceId === "all") return bucketAverage(ascending);
    return ascending.map((r) => ({
      time: new Date(r.received_at).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      }),
      level: Math.round(r.fluid_level_percent * 10) / 10,
      flow: Math.round(r.flow_rate_ml_per_hr * 10) / 10,
    }));
  }, [readings, deviceId]);

  const statusCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const r of readings) counts.set(r.status, (counts.get(r.status) ?? 0) + 1);
    return STATUS_ORDER.filter((s) => counts.has(s)).map((s) => ({
      status: s,
      count: counts.get(s) ?? 0,
    }));
  }, [readings]);

  const stats = useMemo(() => {
    const total = readings.length;
    const avgLevel = total ? readings.reduce((s, r) => s + r.fluid_level_percent, 0) / total : 0;
    const avgFlow = total ? readings.reduce((s, r) => s + r.flow_rate_ml_per_hr, 0) / total : 0;
    const alerts = readings.filter((r) => r.status !== "NORMAL").length;
    const online = devices.filter((d) => d.online).length;
    return { total, avgLevel, avgFlow, alerts, online, fleet: devices.length };
  }, [readings, devices]);

  const exportHref = (kind: "csv" | "xlsx") => {
    if (!httpOrigin) return "#";
    const params = deviceId === "all" ? "" : `?device_id=${encodeURIComponent(deviceId)}`;
    return `${httpOrigin}/api/v1/readings/export.${kind}${params}`;
  };

  const noData = loaded && !error && devices.length === 0;

  return (
    <AppShell>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Analytics</h1>
          <p className="mt-1 text-muted-foreground">
            Historical fluid-level and flow trends, and hardware health, from real backend readings.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Select value={deviceId} onValueChange={setDeviceId}>
            <SelectTrigger className="w-48">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All devices</SelectItem>
              {devices.map((d) => (
                <SelectItem key={d.device_id} value={d.device_id}>
                  {d.bed_label || d.device_id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" asChild>
            <a href={exportHref("csv")}>
              <Download className="size-4" /> CSV
            </a>
          </Button>
          <Button variant="outline" asChild>
            <a href={exportHref("xlsx")}>
              <Download className="size-4" /> Excel
            </a>
          </Button>
        </div>
      </div>

      {!httpOrigin && !settingsLoading && (
        <p className="glass-card mt-6 rounded-2xl p-5 text-sm text-muted-foreground">
          No ward data server is configured yet — set one from the Admin panel, then reload this
          page.
        </p>
      )}

      {error && (
        <p className="glass-card mt-6 rounded-2xl p-5 text-sm text-critical">
          Couldn't reach the ward server at {httpOrigin}: {error}
        </p>
      )}

      {noData && !error && (
        <p className="glass-card mt-6 rounded-2xl p-5 text-sm text-muted-foreground">
          No historical readings yet. Connect the ward server from the Dashboard, or upload a test
          frame via the backend's <code className="rounded bg-secondary px-1 py-0.5">/docs</code>{" "}
          page (<code className="rounded bg-secondary px-1 py-0.5">POST /api/v1/frames</code>) to
          see data here.
        </p>
      )}

      {devices.length > 0 && (
        <>
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
            <StatCard
              icon={Activity}
              label="Readings"
              value={String(stats.total)}
              hint="In current view"
            />
            <StatCard
              icon={Droplet}
              label="Avg fluid level"
              value={`${Math.round(stats.avgLevel)}%`}
            />
            <StatCard icon={Gauge} label="Avg flow" value={`${Math.round(stats.avgFlow)} ml/hr`} />
            <StatCard
              icon={AlertTriangle}
              label="Non-normal readings"
              value={String(stats.alerts)}
            />
            <StatCard
              icon={Radio}
              label="Fleet online"
              value={`${stats.online}/${stats.fleet}`}
              hint="Devices reporting in the last 90s"
            />
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-2">
            <section className="glass-card rounded-2xl p-5">
              <h2 className="text-sm font-semibold tracking-widest text-muted-foreground">
                FLUID LEVEL OVER TIME
              </h2>
              <div className="mt-4 h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis
                      dataKey="time"
                      tick={{ fontSize: 11 }}
                      stroke="var(--muted-foreground)"
                    />
                    <YAxis
                      domain={[0, 100]}
                      tick={{ fontSize: 11 }}
                      stroke="var(--muted-foreground)"
                    />
                    <Tooltip
                      contentStyle={{
                        background: "var(--popover)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="level"
                      stroke="var(--chart-2)"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="glass-card rounded-2xl p-5">
              <h2 className="text-sm font-semibold tracking-widest text-muted-foreground">
                FLOW RATE OVER TIME
              </h2>
              <div className="mt-4 h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis
                      dataKey="time"
                      tick={{ fontSize: 11 }}
                      stroke="var(--muted-foreground)"
                    />
                    <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" />
                    <Tooltip
                      contentStyle={{
                        background: "var(--popover)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                      }}
                    />
                    <Line
                      type="monotone"
                      dataKey="flow"
                      stroke="var(--chart-1)"
                      strokeWidth={2}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="glass-card rounded-2xl p-5">
              <h2 className="text-sm font-semibold tracking-widest text-muted-foreground">
                STATUS DISTRIBUTION
              </h2>
              <div className="mt-4 h-56 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={statusCounts}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis
                      dataKey="status"
                      tick={{ fontSize: 11 }}
                      stroke="var(--muted-foreground)"
                    />
                    <YAxis
                      allowDecimals={false}
                      tick={{ fontSize: 11 }}
                      stroke="var(--muted-foreground)"
                    />
                    <Tooltip
                      contentStyle={{
                        background: "var(--popover)",
                        border: "1px solid var(--border)",
                        borderRadius: 12,
                      }}
                    />
                    <Bar dataKey="count" radius={[6, 6, 0, 0]}>
                      {statusCounts.map((s) => (
                        <Cell key={s.status} fill={STATUS_TONE[s.status]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </section>

            <section className="glass-card rounded-2xl p-5">
              <h2 className="text-sm font-semibold tracking-widest text-muted-foreground">
                HARDWARE FLEET
              </h2>
              <div className="mt-3">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Device</TableHead>
                      <TableHead>Battery</TableHead>
                      <TableHead>Signal</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {devices.map((d) => (
                      <TableRow key={d.device_id}>
                        <TableCell>
                          <p className="font-medium">{d.bed_label || d.device_id}</p>
                          <p className="text-xs text-muted-foreground">
                            {d.patient_name || "Unassigned"}
                          </p>
                        </TableCell>
                        <TableCell>
                          <span className="flex items-center gap-1.5 text-sm">
                            {d.battery_percent <= 20 ? (
                              <BatteryLow className="size-4 text-warn" />
                            ) : (
                              <Battery className="size-4 text-muted-foreground" />
                            )}
                            {d.battery_percent}%
                          </span>
                        </TableCell>
                        <TableCell>
                          <span className="flex items-center gap-1.5 text-sm">
                            <Wifi
                              className={cn(
                                "size-4",
                                d.wifi_rssi > -60
                                  ? "text-success"
                                  : d.wifi_rssi > -75
                                    ? "text-warn"
                                    : "text-critical",
                              )}
                            />
                            {d.wifi_rssi} dBm
                          </span>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={
                              d.online
                                ? "border-success text-success"
                                : "border-critical text-critical"
                            }
                          >
                            {d.online ? "Online" : "Offline"}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </section>
          </div>
        </>
      )}
    </AppShell>
  );
}
