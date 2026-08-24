import { cookies } from "next/headers";
import { notFound } from "next/navigation";

import { AccessLinkHandoff } from "@/app/components/access-link-handoff";
import { AccessLinkRedeemWorkspace } from "@/app/components/access-link-redeem-workspace";
import { sessionCookieName } from "@/lib/cookies";
import { isAccessLinkToken } from "@/lib/access-link-token.server";
import {
  REDEEM_ACCESS_LINK_CSRF_ACTION,
  csrfTokenForSession
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";

export const dynamic = "force-dynamic";

/**
 * The invitation landing page.
 *
 * The token in the address IS the credential, so this page does two things and
 * nothing else: it refuses anything that is not shaped like a token, and it
 * requires a signed-in person before the seat can be claimed. Claiming is a
 * mutation, so it never happens on a GET. A visitor who is not signed in is
 * handed off through session storage rather than through the post-login cookie,
 * so the credential never travels the network a second time.
 */
export default async function AccessLinkPage(
  { params }: { params: Promise<{ token: string }> }
) {
  const { token } = await params;
  if (!isAccessLinkToken(token)) notFound();

  const sessionId = (await cookies()).get(sessionCookieName())?.value;
  const session = sessionId ? await oauthService().getSession(sessionId) : null;
  if (!sessionId || !session) return <AccessLinkHandoff token={token}/>;

  return (
    <AccessLinkRedeemWorkspace
      csrf={csrfTokenForSession(sessionId, REDEEM_ACCESS_LINK_CSRF_ACTION)}
      token={token}
    />
  );
}
