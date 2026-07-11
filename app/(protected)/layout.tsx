import { AppShell } from "../components/app-shell";
import { headers } from "next/headers";
import { demoProfileForPersona } from "@/lib/demo-config";

export default async function ProtectedLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const requestHeaders = await headers();
  const demoProfile = demoProfileForPersona(requestHeaders.get("x-appsell-demo-persona"));
  return <AppShell demoProfile={demoProfile}>{children}</AppShell>;
}
