import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Activity, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { supabase } from "@/integrations/supabase/client";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/auth")({
  validateSearch: z.object({ redirect: z.string().optional() }),
  head: () => ({
    meta: [
      { title: "Staff Sign In — FluidWatch Infusion Monitoring" },
      {
        name: "description",
        content:
          "Sign in to FluidWatch to monitor live infusion flow rates, fluid levels and ward alerts for every bed.",
      },
      { property: "og:title", content: "Staff Sign In — FluidWatch" },
      {
        property: "og:description",
        content: "Secure staff access to the FluidWatch ward monitoring console.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const navigate = useNavigate();
  const { session, loading } = useAuth();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);

  useEffect(() => {
    if (!loading && session) navigate({ to: "/dashboard", replace: true });
  }, [loading, session, navigate]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: window.location.origin,
            data: { full_name: fullName },
          },
        });
        if (error) throw error;
        if (!data.session) {
          setSent(true);
          toast.success("Account created — check your email to confirm.");
          return;
        }
        navigate({ to: "/dashboard", replace: true });
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
        navigate({ to: "/dashboard", replace: true });
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  };

  const google = async () => {
    setBusy(true);
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/auth` },
    });
    if (error) {
      toast.error("Google sign-in failed");
      setBusy(false);
    }
    // On success the browser navigates away to Google immediately; the
    // effect above sends us to /dashboard once we're back with a session.
  };

  return (
    <div className="flex min-h-screen items-center justify-center gradient-mesh px-4 py-12">
      <div className="w-full max-w-md">
        <div className="flex items-center gap-3">
          <span className="flex size-11 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <Activity className="size-6" />
          </span>
          <div>
            <p className="text-xl font-bold tracking-tight">FluidWatch</p>
            <p className="text-sm text-muted-foreground">Ward staff console</p>
          </div>
        </div>

        <div className="glass-card mt-6 rounded-2xl p-6">
          <div className="glass-panel flex rounded-xl p-1">
            {(["signin", "signup"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => {
                  setMode(m);
                  setSent(false);
                }}
                className={cn(
                  "flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
                  mode === m ? "bg-secondary text-foreground" : "text-muted-foreground",
                )}
              >
                {m === "signin" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>

          {sent ? (
            <p className="mt-6 text-sm text-muted-foreground">
              We sent a confirmation link to{" "}
              <span className="font-medium text-foreground">{email}</span>. Confirm your email, then
              sign in.
            </p>
          ) : (
            <form className="mt-6 space-y-4" onSubmit={submit}>
              {mode === "signup" && (
                <div className="space-y-2">
                  <Label htmlFor="name">Full name</Label>
                  <Input
                    id="name"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    required
                  />
                </div>
              )}
              <div className="space-y-2">
                <Label htmlFor="email">Work email</Label>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  type="password"
                  autoComplete={mode === "signin" ? "current-password" : "new-password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  minLength={6}
                  required
                />
              </div>
              <Button type="submit" className="w-full" disabled={busy}>
                {busy && <Loader2 className="size-4 animate-spin" />}
                {mode === "signin" ? "Sign in" : "Create account"}
              </Button>
            </form>
          )}

          <div className="my-5 flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" /> or <span className="h-px flex-1 bg-border" />
          </div>
          <Button variant="outline" className="w-full" onClick={google} disabled={busy}>
            Continue with Google
          </Button>
          <p className="mt-4 text-xs text-muted-foreground">
            The first account to register becomes the ward administrator.
          </p>
        </div>
      </div>
    </div>
  );
}
