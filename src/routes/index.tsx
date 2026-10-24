import { createFileRoute, Link } from "@tanstack/react-router";
import {
  Activity,
  ArrowRight,
  BarChart3,
  BellRing,
  Camera,
  Cpu,
  Gauge,
  LayoutDashboard,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "FluidWatch — Live Infusion Monitoring for Wards" },
      {
        name: "description",
        content:
          "FluidWatch streams infusion flow rate and fluid level for every ward bed, alerts nurses on critical drips, and gives admins full control of thresholds and staff access.",
      },
      { property: "og:title", content: "FluidWatch — Live Infusion Monitoring for Wards" },
      {
        property: "og:description",
        content:
          "Real-time bed-by-bed infusion monitoring with instant critical alerts and an admin control panel.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: Landing,
});

const STATS = [
  { value: "24/7", label: "Continuous monitoring" },
  { value: "<10s", label: "Frame-to-alert latency" },
  { value: "2", label: "Role-scoped access tiers" },
  { value: "100%", label: "Camera-verified readings" },
];

const FEATURES = [
  {
    icon: Gauge,
    title: "Live telemetry",
    body: "Flow rate and fluid level for every bed, streamed over WebSocket the instant a reading lands.",
  },
  {
    icon: BellRing,
    title: "Critical alerts",
    body: "Audible + on-screen alarms the moment a drip goes critical.",
  },
  {
    icon: Camera,
    title: "Camera verification",
    body: "See the actual photo the model read the level from.",
  },
  {
    icon: BarChart3,
    title: "Analytics & trends",
    body: "Historical charts, status breakdowns, CSV/Excel export.",
  },
  {
    icon: Users,
    title: "Role-based access",
    body: "Nurses monitor; only admins manage staff and thresholds.",
  },
  {
    icon: ShieldCheck,
    title: "Admin control",
    body: "Tune thresholds and the live data source from one panel.",
  },
];

const PIPELINE = [
  {
    icon: Camera,
    title: "ESP32-CAM hardware",
    body: "An AI-Thinker ESP32-CAM on the IV stand photographs the bag on a timer and uploads it over Wi-Fi.",
    tag: "Hardware",
  },
  {
    icon: Cpu,
    title: "FastAPI + CV model",
    body: "The backend runs a trained MobileNetV3 model on each frame, derives fill level and flow rate, and stores it.",
    tag: "Backend",
  },
  {
    icon: LayoutDashboard,
    title: "Live dashboard",
    body: "Every reading broadcasts over WebSocket straight to the ward dashboard — a new bed card in seconds.",
    tag: "Software",
  },
];

const MOCK_BEDS = [
  { bed: "Bed 01", level: 78, flow: 124, status: "STABLE" as const },
  { bed: "Bed 03", level: 9, flow: 12, status: "CRITICAL" as const },
  { bed: "Bed 05", level: 31, flow: 45, status: "WATCH" as const },
  { bed: "Bed 04", level: 91, flow: 110, status: "STABLE" as const },
];

const MOCK_TONE: Record<string, string> = {
  STABLE: "bg-success",
  WATCH: "bg-warn",
  CRITICAL: "bg-critical",
};

const MOCK_BADGE: Record<string, string> = {
  STABLE: "bg-success-soft text-success",
  WATCH: "bg-warn-soft text-warn",
  CRITICAL: "bg-critical-soft text-critical",
};

function DashboardMock() {
  return (
    <div className="relative mx-auto w-full max-w-md lg:mx-0">
      <div
        className="blob top-1/2 left-1/2 h-72 w-72 -translate-x-1/2 -translate-y-1/2 bg-primary/30"
        aria-hidden="true"
      />
      <div
        className="glass-card animate-float relative rounded-3xl p-3"
        style={{ ["--tilt" as string]: "-2deg", transform: "rotate(-2deg)" }}
      >
        <div className="flex items-center gap-1.5 px-2 py-1">
          <span className="size-2.5 rounded-full bg-critical/70" />
          <span className="size-2.5 rounded-full bg-warn/70" />
          <span className="size-2.5 rounded-full bg-success/70" />
          <span className="ml-3 text-xs font-medium text-muted-foreground">
            FluidWatch — Ward A
          </span>
          <span className="ml-auto flex items-center gap-1 text-[10px] font-semibold text-success">
            <span className="size-1.5 animate-pulse rounded-full bg-success" /> LIVE
          </span>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-3 p-2">
          {MOCK_BEDS.map((b) => (
            <div key={b.bed} className="glass-panel rounded-xl p-3">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold">{b.bed}</p>
                <span
                  className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${MOCK_BADGE[b.status]}`}
                >
                  {b.status}
                </span>
              </div>
              <p className="mt-2 text-xl font-bold tracking-tight">
                {b.flow} <span className="text-xs font-medium text-muted-foreground">ml/hr</span>
              </p>
              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div
                  className={`h-full rounded-full ${MOCK_TONE[b.status]}`}
                  style={{ width: `${b.level}%` }}
                />
              </div>
              <p className="mt-1 text-[10px] text-muted-foreground">{b.level}% remaining</p>
            </div>
          ))}
        </div>
      </div>
      <div
        className="glass-panel absolute -bottom-6 -right-4 hidden items-center gap-2 rounded-2xl px-4 py-3 sm:flex"
        style={{ animationDelay: "1s" }}
      >
        <span className="flex size-8 items-center justify-center rounded-lg bg-critical-soft text-critical">
          <BellRing className="size-4" />
        </span>
        <div>
          <p className="text-xs font-semibold">Bed 03 critical</p>
          <p className="text-[10px] text-muted-foreground">Nurse notified in 4s</p>
        </div>
      </div>
    </div>
  );
}

function Landing() {
  return (
    <div className="min-h-screen gradient-mesh overflow-x-clip">
      <header className="glass-nav sticky top-0 z-20">
        <div className="mx-auto flex max-w-6xl items-center gap-3 px-6 py-4">
          <span className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Activity className="size-5" />
          </span>
          <span className="text-lg font-bold tracking-tight">FluidWatch</span>
          <nav className="ml-8 hidden items-center gap-6 text-sm font-medium text-muted-foreground md:flex">
            <a href="#pipeline" className="transition-colors hover:text-foreground">
              How it works
            </a>
            <a href="#features" className="transition-colors hover:text-foreground">
              Features
            </a>
          </nav>
          <div className="ml-auto">
            <Button asChild>
              <Link to="/auth">Staff sign in</Link>
            </Button>
          </div>
        </div>
      </header>

      <section className="relative overflow-hidden">
        <div
          className="blob left-[-8%] top-[-10%] h-96 w-96 bg-[var(--chart-2)]/25"
          aria-hidden="true"
        />
        <div
          className="blob right-[-10%] top-[15%] h-[26rem] w-[26rem] bg-primary/20"
          style={{ animationDelay: "4s" }}
          aria-hidden="true"
        />

        <div className="relative mx-auto grid max-w-6xl items-center gap-16 px-6 py-20 lg:grid-cols-2 lg:py-28">
          <div>
            <span className="glass-panel inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold text-muted-foreground">
              <Sparkles className="size-3.5 text-primary" /> Camera-verified, real-time ward
              monitoring
            </span>
            <h1 className="mt-6 text-5xl font-bold leading-[1.05] tracking-tight sm:text-6xl">
              Never miss an <span className="text-gradient">empty drip</span> again
            </h1>
            <p className="mt-5 max-w-lg text-lg text-muted-foreground">
              FluidWatch watches every infusion pump on the ward and calls out the ones that need a
              nurse — before the line runs dry.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Button asChild size="lg" className="shadow-[0_8px_30px_-8px_var(--primary)]">
                <Link to="/auth">
                  Open the console <ArrowRight className="size-4" />
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className="glass-panel border-0">
                <a href="#pipeline">See how it works</a>
              </Button>
            </div>
            <dl className="mt-14 grid max-w-lg grid-cols-2 gap-x-6 gap-y-8 sm:grid-cols-4">
              {STATS.map((s) => (
                <div key={s.label}>
                  <dt className="sr-only">{s.label}</dt>
                  <dd className="text-2xl font-bold tracking-tight text-gradient">{s.value}</dd>
                  <dd className="mt-1 text-xs text-muted-foreground">{s.label}</dd>
                </div>
              ))}
            </dl>
          </div>

          <DashboardMock />
        </div>
      </section>

      <section id="pipeline" className="mx-auto max-w-5xl px-6 py-24">
        <h2 className="text-center text-sm font-semibold tracking-widest text-primary">
          HARDWARE → BACKEND → SOFTWARE
        </h2>
        <p className="mt-2 text-center text-3xl font-bold tracking-tight">
          One continuous pipeline, zero manual steps
        </p>
        <div className="mt-14 grid gap-6 md:grid-cols-3">
          {PIPELINE.map(({ icon: Icon, title, body, tag }, i) => (
            <div key={title} className="glass-card relative rounded-3xl p-7">
              <span className="absolute -top-4 left-7 flex size-8 items-center justify-center rounded-full bg-primary text-xs font-bold text-primary-foreground shadow-md">
                {i + 1}
              </span>
              <span className="flex size-12 items-center justify-center rounded-2xl bg-primary/10">
                <Icon className="size-6 text-primary" />
              </span>
              <span className="mt-5 block text-xs font-semibold uppercase tracking-widest text-primary">
                {tag}
              </span>
              <h3 className="mt-1 text-lg font-semibold">{title}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{body}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="features" className="mx-auto max-w-5xl px-6 pb-24">
        <p className="text-center text-3xl font-bold tracking-tight">
          Everything a ward needs, nothing it doesn't
        </p>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, body }) => (
            <article
              key={title}
              className="glass-card rounded-3xl p-7 transition-all hover:-translate-y-0.5 hover:shadow-lg"
            >
              <span className="flex size-11 items-center justify-center rounded-2xl bg-primary/10">
                <Icon className="size-5 text-primary" />
              </span>
              <h3 className="mt-4 text-lg font-semibold">{title}</h3>
              <p className="mt-2 text-sm text-muted-foreground">{body}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-24">
        <div className="glass-card relative overflow-hidden rounded-3xl px-8 py-14 text-center">
          <div
            className="blob left-1/2 top-1/2 h-80 w-80 -translate-x-1/2 -translate-y-1/2 bg-primary/15"
            aria-hidden="true"
          />
          <div className="relative">
            <p className="text-3xl font-bold tracking-tight">Ready to watch your ward live?</p>
            <p className="mx-auto mt-3 max-w-md text-muted-foreground">
              Sign in to open the console — the first account to register becomes the ward
              administrator.
            </p>
            <Button asChild size="lg" className="mt-7 shadow-[0_8px_30px_-8px_var(--primary)]">
              <Link to="/auth">
                Staff sign in <ArrowRight className="size-4" />
              </Link>
            </Button>
          </div>
        </div>
      </section>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-8 text-sm text-muted-foreground">
          <span className="flex items-center gap-2 font-semibold text-foreground">
            <Activity className="size-4" /> FluidWatch
          </span>
          <nav className="flex flex-wrap gap-5">
            <Link to="/dashboard" className="hover:text-foreground">
              Dashboard
            </Link>
            <Link to="/analytics" className="hover:text-foreground">
              Analytics
            </Link>
            <Link to="/admin" className="hover:text-foreground">
              Admin
            </Link>
            <Link to="/auth" className="hover:text-foreground">
              Sign in
            </Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
