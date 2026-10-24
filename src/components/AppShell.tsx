import { Link, useNavigate } from "@tanstack/react-router";
import { Activity, BarChart3, LogOut, Moon, Radio, Shield, Sun, LayoutGrid } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { useLiveBeds } from "@/lib/liveBeds";
import { fetchDevices, type DeviceStatus } from "@/lib/fluidwatch";
import { cn } from "@/lib/utils";

function FleetStatusPill() {
  const { httpOrigin } = useLiveBeds();
  const [devices, setDevices] = useState<DeviceStatus[] | null>(null);

  useEffect(() => {
    if (!httpOrigin) return;
    let cancelled = false;
    const load = () => {
      fetchDevices(httpOrigin)
        .then((d) => !cancelled && setDevices(d))
        .catch(() => !cancelled && setDevices(null));
    };
    load();
    const t = setInterval(load, 15000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [httpOrigin]);

  if (!devices || devices.length === 0) return null;
  const online = devices.filter((d) => d.online).length;

  return (
    <span
      className={cn(
        "hidden items-center gap-2 rounded-full border border-border px-3 py-1.5 text-xs font-semibold sm:inline-flex",
        online > 0 ? "text-success" : "text-muted-foreground",
      )}
      title="Hardware devices reporting to the ward server"
    >
      <Radio className="size-3.5" />
      {online}/{devices.length} devices online
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { profile, user, role, isAdmin, signOut } = useAuth();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const { connected } = useLiveBeds();
  const dark = theme === "dark";

  const handleSignOut = async () => {
    await signOut();
    navigate({ to: "/auth", replace: true });
  };

  const initials = (profile?.full_name || user?.email || "?")
    .split(/[\s@.]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase())
    .join("");

  return (
    <div className="min-h-screen gradient-mesh">
      <header className="glass-nav sticky top-0 z-20">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-4 px-6 py-3">
          <Link to="/dashboard" className="flex items-center gap-3">
            <span className="flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Activity className="size-5" />
            </span>
            <span>
              <span className="block text-lg font-bold leading-tight tracking-tight">
                FluidWatch
              </span>
              <span className="block text-xs text-muted-foreground">
                Remote Infusion Monitoring
              </span>
            </span>
          </Link>

          <nav className="flex items-center gap-1">
            <Link
              to="/dashboard"
              className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
              activeProps={{ className: "bg-secondary text-foreground" }}
            >
              <LayoutGrid className="size-4" /> Dashboard
            </Link>
            <Link
              to="/analytics"
              className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
              activeProps={{ className: "bg-secondary text-foreground" }}
            >
              <BarChart3 className="size-4" /> Analytics
            </Link>
            {isAdmin && (
              <Link
                to="/admin"
                className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
                activeProps={{ className: "bg-secondary text-foreground" }}
              >
                <Shield className="size-4" /> Admin
              </Link>
            )}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <FleetStatusPill />
            <span
              className={cn(
                "hidden items-center gap-2 rounded-full border border-border px-3 py-1.5 text-xs font-semibold sm:inline-flex",
                connected ? "text-success" : "text-muted-foreground",
              )}
            >
              <span className={cn("size-2 rounded-full", connected ? "bg-success" : "bg-warn")} />
              {connected ? "Live stream" : "Simulated stream"}
            </span>
            <Button variant="outline" size="icon" onClick={toggle} title="Toggle theme">
              {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
            <div className="flex items-center gap-2 rounded-xl border border-border pl-1 pr-2 py-1">
              <span className="flex size-8 items-center justify-center rounded-lg bg-secondary text-xs font-bold">
                {initials}
              </span>
              <span className="hidden leading-tight sm:block">
                <span className="block text-xs font-semibold">
                  {profile?.full_name || user?.email}
                </span>
                <span className="block text-[11px] capitalize text-muted-foreground">
                  {role ?? "staff"}
                </span>
              </span>
              <Button variant="ghost" size="icon" onClick={handleSignOut} title="Sign out">
                <LogOut className="size-4" />
              </Button>
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
    </div>
  );
}
