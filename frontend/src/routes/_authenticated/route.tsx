import { Outlet, createFileRoute, redirect } from "@tanstack/react-router";
import { supabase } from "@/integrations/supabase/client";
import { LiveBedsProvider } from "@/lib/liveBeds";

export const Route = createFileRoute("/_authenticated")({
  ssr: false,
  beforeLoad: async ({ location }) => {
    const { data } = await supabase.auth.getSession();
    if (!data.session) {
      throw redirect({ to: "/auth", search: { redirect: location.href } });
    }
  },
  component: () => (
    <LiveBedsProvider>
      <Outlet />
    </LiveBedsProvider>
  ),
});
