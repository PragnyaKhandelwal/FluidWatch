import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Battery,
  BatteryLow,
  Bell,
  BellOff,
  Camera,
  CameraOff,
  CheckCircle2,
  Clock,
  Expand,
  Wifi,
} from "lucide-react";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { formatFlow, frameImageUrl, UNITS, type Bed, type UnitKey } from "@/lib/fluidwatch";

const statusStyles: Record<Bed["status"], string> = {
  STABLE: "bg-success-soft text-success",
  WATCH: "bg-warn-soft text-warn",
  CRITICAL: "bg-critical-soft text-critical",
};

const barStyles: Record<Bed["status"], string> = {
  STABLE: "bg-success",
  WATCH: "bg-warn",
  CRITICAL: "bg-critical",
};

const sparklineStroke: Record<Bed["status"], string> = {
  STABLE: "var(--success)",
  WATCH: "var(--warn)",
  CRITICAL: "var(--critical)",
};

const gaugeStroke: Record<Bed["status"], string> = {
  STABLE: "var(--success)",
  WATCH: "var(--warn)",
  CRITICAL: "var(--critical)",
};

const gaugeText: Record<Bed["status"], string> = {
  STABLE: "text-foreground",
  WATCH: "text-warn",
  CRITICAL: "text-critical",
};

const accentBar: Record<Bed["status"], string> = {
  STABLE: "bg-success",
  WATCH: "bg-warn",
  CRITICAL: "bg-critical",
};

/** Circular fluid-level gauge - replaces a plain icon+number with a ring
 * that visually reads as "how full" at a glance, the same information
 * the linear bar below already carries but legible from across a room. */
function RadialGauge({ level, status }: { level: number; status: Bed["status"] }) {
  const radius = 26;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, level));
  const offset = circumference - (clamped / 100) * circumference;

  return (
    <div className="relative flex size-16 shrink-0 items-center justify-center">
      <svg width="64" height="64" viewBox="0 0 64 64" className="-rotate-90">
        <circle cx="32" cy="32" r={radius} fill="none" stroke="var(--border)" strokeWidth="6" />
        <circle
          cx="32"
          cy="32"
          r={radius}
          fill="none"
          stroke={gaugeStroke[status]}
          strokeWidth="6"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: "stroke-dashoffset 500ms ease" }}
        />
      </svg>
      <span className={cn("absolute text-sm font-bold tabular-nums", gaugeText[status])}>
        {Math.round(clamped)}%
      </span>
    </div>
  );
}

const StatusIcon = ({ status }: { status: Bed["status"] }) =>
  status === "STABLE" ? (
    <CheckCircle2 className="size-3.5" />
  ) : status === "WATCH" ? (
    <Clock className="size-3.5" />
  ) : (
    <AlertTriangle className="size-3.5" />
  );

/** "just now" / "12s ago" / "4m ago", ticking every second. Deliberately
 * its own component, not a hook called inside BedCard - a hook's state
 * lives in whichever component calls it, so ticking every second would
 * re-render the *entire* card (image, sparkline chart, everything) once
 * a second. Isolated here, only this small text node re-renders.
 *
 * The label and title are computed inside an effect, not during render -
 * "now" differs between the server's render time and the client's
 * hydration time (and INITIAL_BEDS/demo data bakes in a fixed `updatedAt`
 * at module-load time on top of that), so computing "seconds ago" during
 * render produces different text on the server vs. the client's first
 * paint and React discards/rebuilds the tree. Rendering a stable blank
 * until the post-hydration effect fills it in avoids the mismatch. */
function FreshnessLabel({ updatedAt, className }: { updatedAt: number; className?: string }) {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => {
      const seconds = Math.max(0, Math.round((Date.now() - updatedAt) / 1000));
      setLabel(
        seconds < 3
          ? "just now"
          : seconds < 60
            ? `${seconds}s ago`
            : `${Math.round(seconds / 60)}m ago`,
      );
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [updatedAt]);

  return (
    <span
      className={cn("mt-0.5 shrink-0 text-xs text-muted-foreground", className)}
      title={label ? new Date(updatedAt).toLocaleTimeString() : undefined}
    >
      {label ?? " "}
    </span>
  );
}

/** The hardware-acquired photo, dressed the way live-camera feed cards
 * usually are (Ring/Nest-style): a bottom scrim holding a "verified" chip
 * and capture time directly on the image, click-to-expand, and a fixed-
 * height placeholder (rather than nothing) when this device has no camera
 * frame yet - keeps the grid's row heights aligned instead of jagged. */
function CameraFrame({ bed, httpOrigin }: { bed: Bed; httpOrigin: string | null }) {
  const imageUrl = frameImageUrl(httpOrigin, bed.id, bed.updatedAt);
  // Tracks the specific URL that failed, not just a boolean - so a demo
  // bed (no photo, ever) shows the placeholder without a broken-image
  // flash, but a real device's transient failure doesn't hide it forever
  // once the next reading's URL (a new cache-busted timestamp) comes in.
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const showImage = imageUrl !== null && imageUrl !== failedUrl;

  const frame = (
    <div className="group relative mt-3 aspect-video w-full overflow-hidden rounded-lg bg-muted">
      {showImage ? (
        <img
          key={imageUrl}
          src={imageUrl}
          onError={() => setFailedUrl(imageUrl)}
          alt={`Latest camera frame for ${bed.bed}`}
          className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-105"
        />
      ) : (
        <div className="flex h-full flex-col items-center justify-center gap-1.5 text-muted-foreground">
          <CameraOff className="size-5" />
          <span className="text-xs">No camera frame yet</span>
        </div>
      )}
      {showImage && (
        <div className="absolute inset-x-0 bottom-0 flex items-center gap-1.5 bg-gradient-to-t from-black/70 via-black/25 to-transparent px-2.5 py-2">
          <Camera className="size-3.5 text-white" />
          <span className="text-xs font-medium text-white">Camera-verified</span>
          <FreshnessLabel updatedAt={bed.updatedAt} className="ml-auto text-white/85" />
          <Expand className="size-3.5 text-white opacity-0 transition-opacity group-hover:opacity-100" />
        </div>
      )}
    </div>
  );

  if (!showImage) return frame;

  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type="button"
          className="block w-full text-left"
          aria-label={`Expand camera frame for ${bed.bed}`}
        >
          {frame}
        </button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {bed.bed} · {bed.patient}
          </DialogTitle>
          <DialogDescription>
            {bed.fluid} — captured {new Date(bed.updatedAt).toLocaleString()}
          </DialogDescription>
        </DialogHeader>
        <img
          src={imageUrl ?? undefined}
          alt={`Full-size camera frame for ${bed.bed}`}
          className="w-full rounded-lg"
        />
      </DialogContent>
    </Dialog>
  );
}

export function BedCard({
  bed,
  unit,
  httpOrigin,
  onToggleMute,
}: {
  bed: Bed;
  unit: UnitKey;
  httpOrigin: string | null;
  onToggleMute: (id: string) => void;
}) {
  const critical = bed.status === "CRITICAL";
  const sparklineData = bed.history.map((level, i) => ({ i, level }));

  return (
    <article
      className={cn(
        "glass-card relative overflow-hidden rounded-2xl p-6 transition-all hover:-translate-y-0.5 hover:shadow-lg",
        critical && "card-critical",
      )}
      aria-label={`${bed.bed} ${bed.patient} ${bed.status}`}
    >
      <span
        className={cn("absolute inset-x-0 top-0 h-1", accentBar[bed.status])}
        aria-hidden="true"
      />
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold tracking-tight">
            {bed.bed} · {bed.patient}
          </h3>
          <p className="mt-1 text-sm text-muted-foreground">{bed.fluid}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <FreshnessLabel updatedAt={bed.updatedAt} />
          {(bed.battery !== null || bed.wifi !== null) && (
            <div
              className="flex items-center gap-2 text-muted-foreground"
              title="Live hardware telemetry"
            >
              {bed.battery !== null && (
                <span className="flex items-center gap-0.5 text-xs">
                  {bed.battery <= 20 ? (
                    <BatteryLow className="size-3.5 text-warn" />
                  ) : (
                    <Battery className="size-3.5" />
                  )}
                  {bed.battery}%
                </span>
              )}
              {bed.wifi !== null && (
                <span className="flex items-center gap-0.5 text-xs">
                  <Wifi className="size-3.5" />
                  {bed.wifi} dBm
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      <CameraFrame bed={bed} httpOrigin={httpOrigin} />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-semibold tracking-wide",
            statusStyles[bed.status],
          )}
        >
          <StatusIcon status={bed.status} />
          {bed.status}
        </span>
        {bed.auxClass && (
          <span
            className="inline-flex items-center rounded-md bg-secondary px-2 py-1 text-xs font-medium text-secondary-foreground"
            title="Model's auxiliary classification, from the same image"
          >
            AI read: {bed.auxClass}
          </span>
        )}
      </div>

      <p className="mt-5 text-xs font-medium tracking-widest text-muted-foreground">CURRENT FLOW</p>
      <div className="flex items-end justify-between gap-4">
        <p className={cn("text-5xl font-bold tracking-tight", critical && "text-critical")}>
          {formatFlow(bed.flow, unit)}
          <span className="text-xl font-semibold">{UNITS[unit].suffix.split("/")[0]}</span>
          <span className="text-xl font-medium text-muted-foreground">
            /{UNITS[unit].suffix.split("/")[1]}
          </span>
        </p>
        <RadialGauge level={bed.level} status={bed.status} />
      </div>

      <p className="mt-1 text-sm text-muted-foreground">Fluid Level</p>
      <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            "h-full rounded-full transition-[width] duration-500",
            barStyles[bed.status],
          )}
          style={{ width: `${Math.max(1, Math.min(100, bed.level))}%` }}
        />
      </div>

      {sparklineData.length > 1 && (
        <div className="mt-3 h-10 w-full" aria-hidden="true">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={sparklineData}>
              <YAxis domain={[0, 100]} hide />
              <Line
                type="monotone"
                dataKey="level"
                stroke={sparklineStroke[bed.status]}
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {critical && (
        <Button
          variant={bed.muted ? "outline" : "default"}
          className="mt-4 w-full"
          onClick={() => onToggleMute(bed.id)}
        >
          {bed.muted ? <Bell className="size-4" /> : <BellOff className="size-4" />}
          {bed.muted ? "Unmute alarm" : "Mute alarm"}
        </Button>
      )}
    </article>
  );
}
