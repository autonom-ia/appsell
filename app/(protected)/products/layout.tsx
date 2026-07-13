import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { sessionCookieName } from "@/lib/cookies";
import { demoProfileForSubject } from "@/lib/demo-config";
import { oauthService } from "@/lib/oauth-service.server";

export default async function SellerWorkspaceLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = await oauthService().getSession(sessionId);
  if (demoProfileForSubject(session?.subject)?.persona === "buyer") redirect("/library");
  return children;
}
