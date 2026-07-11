import { AppShell } from "../components/app-shell";
import { cookies } from "next/headers";
import { sessionCookieName } from "@/lib/cookies";
import { demoProfileForSubject } from "@/lib/demo-config";
import { oauthService } from "@/lib/oauth-service.server";

export default async function ProtectedLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const cookieStore = await cookies();
  const session = await oauthService().getSession(cookieStore.get(sessionCookieName())?.value);
  return <AppShell demoProfile={demoProfileForSubject(session?.subject)}>{children}</AppShell>;
}
