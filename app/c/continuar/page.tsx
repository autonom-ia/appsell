import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { AccessLinkRedeemWorkspace } from "@/app/components/access-link-redeem-workspace";
import { ACCESS_LINK_CONTINUE_PATH } from "@/app/components/access-link-handoff-shared";
import { sessionCookieName } from "@/lib/cookies";
import {
  REDEEM_ACCESS_LINK_CSRF_ACTION,
  csrfTokenForSession
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";

export const dynamic = "force-dynamic";

/**
 * Where an invitation resumes after login.
 *
 * This path carries no secret, which is the whole point: the token waits in the
 * tab's session storage and is read by the client, so nothing about the
 * invitation reaches a server log or a cookie.
 */
export default async function AccessLinkContinuePage() {
  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = sessionId ? await oauthService().getSession(sessionId) : null;
  if (!sessionId || !session) {
    redirect(`/auth/login?next=${encodeURIComponent(ACCESS_LINK_CONTINUE_PATH)}`);
  }

  return (
    <AccessLinkRedeemWorkspace
      csrf={csrfTokenForSession(sessionId, REDEEM_ACCESS_LINK_CSRF_ACTION)}
      token={null}
    />
  );
}
