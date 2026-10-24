import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Loader2, Save, Shield, ShieldOff, Users } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { AppShell } from "@/components/AppShell";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/lib/auth";
import { useLiveBeds } from "@/lib/liveBeds";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_authenticated/admin")({
  head: () => ({
    meta: [
      { title: "Admin Panel — FluidWatch Ward Administration" },
      {
        name: "description",
        content:
          "Administer FluidWatch: manage staff accounts and roles, set infusion alert thresholds and point the ward at its live data server.",
      },
      { property: "og:title", content: "Admin Panel — FluidWatch" },
      {
        property: "og:description",
        content: "Staff roles, alert thresholds and ward data source configuration.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: AdminPage,
});

interface StaffRow {
  id: string;
  full_name: string;
  ward: string;
  job_title: string;
  created_at: string;
  role: "admin" | "nurse";
}

interface Settings {
  watch_level: number;
  critical_level: number;
  min_flow: number;
  ws_url: string;
  sound_alerts: boolean;
}

function AdminPage() {
  const { isAdmin, loading, user } = useAuth();
  const { refreshWardSettings } = useLiveBeds();
  const [staff, setStaff] = useState<StaffRow[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);

  const load = async () => {
    const [{ data: profiles, error: pErr }, { data: roles }, { data: cfg }] = await Promise.all([
      supabase
        .from("profiles")
        .select("id, full_name, ward, job_title, created_at")
        .order("created_at"),
      supabase.from("user_roles").select("user_id, role"),
      supabase
        .from("ward_settings")
        .select("watch_level, critical_level, min_flow, ws_url, sound_alerts")
        .maybeSingle(),
    ]);
    if (pErr) toast.error(pErr.message);
    const roleFor = new Map((roles ?? []).map((r) => [r.user_id, r.role as "admin" | "nurse"]));
    setStaff(
      (profiles ?? []).map((p) => ({
        ...(p as Omit<StaffRow, "role">),
        role: roleFor.get(p.id) ?? "nurse",
      })),
    );
    if (cfg) setSettings(cfg as Settings);
    setReady(true);
  };

  useEffect(() => {
    if (!loading && isAdmin) void load();
  }, [loading, isAdmin]);

  if (loading) {
    return (
      <AppShell>
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </AppShell>
    );
  }

  if (!isAdmin) {
    return (
      <AppShell>
        <div className="glass-card mx-auto max-w-md rounded-2xl p-8 text-center">
          <ShieldOff className="mx-auto size-8 text-critical" />
          <h1 className="mt-4 text-xl font-semibold">Administrators only</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            Your account doesn't have admin access to the ward control panel.
          </p>
          <Button asChild className="mt-6">
            <Link to="/dashboard">Back to dashboard</Link>
          </Button>
        </div>
      </AppShell>
    );
  }

  const setRole = async (row: StaffRow, role: "admin" | "nurse") => {
    if (row.id === user?.id && role === "nurse") {
      toast.error("You can't remove your own admin access.");
      return;
    }
    setBusy(true);
    const { error: delErr } = await supabase.from("user_roles").delete().eq("user_id", row.id);
    const { error } = delErr
      ? { error: delErr }
      : await supabase.from("user_roles").insert({ user_id: row.id, role });
    setBusy(false);
    if (error) {
      toast.error(error.message);
      return;
    }
    toast.success(`${row.full_name || "Staff member"} is now ${role}`);
    void load();
  };

  const saveSettings = async () => {
    if (!settings) return;
    setBusy(true);
    const { error } = await supabase
      .from("ward_settings")
      .update({ ...settings, updated_at: new Date().toISOString() })
      .eq("id", true);
    setBusy(false);
    if (error) {
      toast.error(error.message);
      return;
    }
    // The live connection (LiveBedsProvider, mounted once for the whole
    // session) fetched ward_settings on login and won't see this update
    // on its own - refresh it now so a new ws_url takes effect in this
    // tab immediately instead of requiring a reload.
    void refreshWardSettings();
    toast.success("Ward settings saved");
  };

  return (
    <AppShell>
      <div className="flex items-center gap-3">
        <Shield className="size-6 text-primary" />
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Admin panel</h1>
          <p className="text-muted-foreground">
            Staff access, alert thresholds and ward data source.
          </p>
        </div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {[
          { label: "Staff accounts", value: staff.length },
          { label: "Administrators", value: staff.filter((s) => s.role === "admin").length },
          { label: "Nurses", value: staff.filter((s) => s.role === "nurse").length },
        ].map((s) => (
          <article key={s.label} className="glass-card rounded-2xl p-5">
            <p className="text-xs font-semibold tracking-widest text-muted-foreground">
              {s.label.toUpperCase()}
            </p>
            <p className="mt-2 text-3xl font-bold tracking-tight">{s.value}</p>
          </article>
        ))}
      </div>

      <section className="mt-8">
        <h2 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
          <Users className="size-5" /> Staff directory
        </h2>
        <div className="glass-card mt-4 overflow-hidden rounded-2xl">
          {!ready && <p className="px-5 py-4 text-sm text-muted-foreground">Loading staff…</p>}
          {ready && staff.length === 0 && (
            <p className="px-5 py-4 text-sm text-muted-foreground">No staff accounts yet.</p>
          )}
          {staff.map((s) => (
            <div
              key={s.id}
              className="flex flex-wrap items-center justify-between gap-4 border-b border-border px-5 py-4 last:border-0"
            >
              <div>
                <p className="font-semibold">{s.full_name || "Unnamed staff"}</p>
                <p className="text-sm text-muted-foreground">
                  {s.job_title} · {s.ward} · joined {new Date(s.created_at).toLocaleDateString()}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "rounded-md px-2 py-1 text-xs font-semibold capitalize",
                    s.role === "admin"
                      ? "bg-success-soft text-success"
                      : "bg-secondary text-secondary-foreground",
                  )}
                >
                  {s.role}
                </span>
                <Button
                  size="sm"
                  variant={s.role === "admin" ? "outline" : "default"}
                  disabled={busy}
                  onClick={() => setRole(s, s.role === "admin" ? "nurse" : "admin")}
                >
                  {s.role === "admin" ? "Demote to nurse" : "Make admin"}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="mt-8 max-w-2xl">
        <h2 className="text-xl font-semibold tracking-tight">Ward configuration</h2>
        {settings ? (
          <div className="glass-card mt-4 space-y-5 rounded-2xl p-6">
            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="watch">Watch level (%)</Label>
                <Input
                  id="watch"
                  type="number"
                  value={settings.watch_level}
                  onChange={(e) =>
                    setSettings({ ...settings, watch_level: Number(e.target.value) })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="crit">Critical level (%)</Label>
                <Input
                  id="crit"
                  type="number"
                  value={settings.critical_level}
                  onChange={(e) =>
                    setSettings({ ...settings, critical_level: Number(e.target.value) })
                  }
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="flow">Min flow (ml/hr)</Label>
                <Input
                  id="flow"
                  type="number"
                  value={settings.min_flow}
                  onChange={(e) => setSettings({ ...settings, min_flow: Number(e.target.value) })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="ws">Ward data server (WebSocket URL)</Label>
              <Input
                id="ws"
                value={settings.ws_url}
                onChange={(e) => setSettings({ ...settings, ws_url: e.target.value })}
              />
            </div>
            <label className="flex items-center justify-between">
              <span className="font-medium">Sound alerts enabled ward-wide</span>
              <Switch
                checked={settings.sound_alerts}
                onCheckedChange={(v) => setSettings({ ...settings, sound_alerts: v })}
              />
            </label>
            <Button onClick={saveSettings} disabled={busy}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
              Save settings
            </Button>
          </div>
        ) : (
          <p className="mt-4 text-sm text-muted-foreground">Loading configuration…</p>
        )}
      </section>
    </AppShell>
  );
}
