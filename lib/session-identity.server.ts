import "server-only";

import {
  isDomainApiConfigured,
  listDomainSellers,
  readDomainIdentity
} from "@/lib/internal-api-client.server";
import { PLATFORM_ROLES, SELLER_ROLES } from "@/lib/rbac-roles";

// ServerSession não é exportado pelo cliente interno; derivar o tipo do próprio
// consumidor evita duplicar a forma da sessão em dois lugares que divergiriam.
type DomainSession = Parameters<typeof readDomainIdentity>[1];

// O papel de quem está logado, resolvido no servidor a partir da fonte de
// verdade — nunca do browser. O shell precisa dele para montar a navegação;
// antes desta rota ele não tinha como saber, e mostrava o menu do vendedor
// para todo mundo (#305).
//
// A resolução falha FECHADA: quando a API de domínio não responde, não
// afirmamos que a pessoa é vendedora nem que não é. Devolvemos null e o
// chamador trata como sessão não validada, porque exibir o menu do vendedor
// sem confirmar membership é exatamente o defeito que esta issue corrige.
export interface SessionIdentity {
  readonly email: string;
  readonly emailVerified: boolean;
  readonly platformRoles: readonly string[];
  readonly sellers: readonly SessionIdentitySeller[];
}

export interface SessionIdentitySeller {
  readonly id: string;
  readonly slug: string;
  readonly displayName: string;
  readonly role: string;
}

export async function resolveSessionIdentity(
  sessionId: string,
  session: DomainSession
): Promise<SessionIdentity | null> {
  if (!isDomainApiConfigured()) return null;
  const [identity, sellers] = await Promise.all([
    readDomainIdentity(sessionId, session),
    listDomainSellers(sessionId, session)
  ]);
  const parsedIdentity = parseIdentity(identity);
  const parsedSellers = parseSellers(sellers);
  if (!parsedIdentity || !parsedSellers) return null;
  return { ...parsedIdentity, sellers: parsedSellers };
}

function parseIdentity(payload: unknown) {
  if (!isRecord(payload)) return null;
  const email = payload.email;
  if (typeof email !== "string" || email.length === 0) return null;
  const roles = payload.platform_roles;
  if (!Array.isArray(roles) || roles.some((role) => typeof role !== "string")) return null;
  return {
    email,
    emailVerified: payload.email_verified === true,
    // Papel que a borda não reconhece não vira permissão: deny-by-default vale
    // também para o que a própria API devolve.
    platformRoles: (roles as string[]).filter((role) => PLATFORM_ROLES.has(role))
  };
}

function parseSellers(payload: unknown): SessionIdentitySeller[] | null {
  if (!isRecord(payload)) return null;
  const sellers = payload.sellers;
  if (!Array.isArray(sellers)) return null;
  const parsed: SessionIdentitySeller[] = [];
  for (const seller of sellers) {
    if (!isRecord(seller)) return null;
    const { id, slug, display_name: displayName, role } = seller;
    if (typeof id !== "string" || id.length === 0 ||
        typeof slug !== "string" || slug.length === 0 ||
        typeof displayName !== "string" || displayName.length === 0 ||
        typeof role !== "string" || !SELLER_ROLES.has(role)) {
      return null;
    }
    parsed.push({ id, slug, displayName, role });
  }
  return parsed;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
