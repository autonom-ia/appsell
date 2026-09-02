import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, test, vi } from "vitest";

vi.mock("server-only", () => ({}));

// Minha conta (#351). As rotas rodam de verdade, com a sessão e a API de
// domínio substituídas: o assunto é a porta — origem, sessão, token, corpo só
// com o token, taxa — e a forma da resposta que o navegador recebe.

const root = resolve(__dirname, "..");
const ORIGIN = "https://sell.test";
const CSRF_KEY = Buffer.alloc(32, 7).toString("base64");
const SESSION_COOKIE = "__Host-appsell_session=sid";
const NOW = "2026-09-02T12:00:00.000Z";
const EXPORT = {
  schema_version: 1,
  exported_at: NOW,
  identity: { email: "ana@example.test", email_verified: true, display_name: "Ana", created_at: NOW },
  accesses: [{ product: "Curso", store: "Loja", status: "active" }]
};
const SESSION = {
  subject: "sub-1",
  tokenRevision: 1,
  expiresAt: 0,
  identity: { email: "ana@example.test", emailVerified: true }
};

interface Scenario {
  session?: boolean;
  allowed?: boolean;
  status?: unknown;
  exportData?: unknown;
  deletion?: unknown;
  domainError?: { status: number; code: string };
}

// A última chamada ao limitador: a chave tem de ser a pessoa, não a sessão.
let lastRateLimit: { action: string; clientKey: string } | null = null;

function mockModules(scenario: Scenario) {
  vi.doMock("@/lib/oauth-service.server", () => ({
    oauthService: () => ({
      getSession: async (id?: string) => (scenario.session !== false && id === "sid" ? SESSION : null),
      consumeRateLimit: async (action: string, clientKey: string) => {
        lastRateLimit = { action, clientKey };
        return scenario.allowed !== false;
      }
    })
  }));
  vi.doMock("@/lib/internal-api-client.server", () => {
    class DomainApiError extends Error {
      constructor(readonly status: number, readonly code: string) {
        super(code);
      }
    }
    const gate = () => {
      if (scenario.domainError) {
        throw new DomainApiError(scenario.domainError.status, scenario.domainError.code);
      }
    };
    return {
      DomainApiError,
      readDomainAccountStatus: async () => {
        gate();
        return scenario.status ?? { deletion_requested_at: null };
      },
      exportDomainAccountData: async () => {
        gate();
        return "exportData" in scenario ? scenario.exportData : EXPORT;
      },
      requestDomainAccountDeletion: async () => {
        gate();
        return scenario.deletion ?? { requested_at: NOW, already_requested: false };
      }
    };
  });
}

type Route = "" | "/export" | "/deletion-request";
const routes: Record<Route, () => Promise<{ GET?: unknown; POST?: unknown }>> = {
  "": () => import("../app/api/me/account/route"),
  "/export": () => import("../app/api/me/account/export/route"),
  "/deletion-request": () => import("../app/api/me/account/deletion-request/route")
};

interface CallOptions {
  method?: "GET" | "POST";
  cookie?: boolean;
  origin?: string | null;
  fields?: Record<string, string> | null;
  json?: unknown;
  csrf?: boolean;
  action?: string;
  scenario?: Scenario;
}

async function call(route: Route, options: CallOptions = {}) {
  vi.resetModules();
  vi.stubEnv("NODE_ENV", "production");
  vi.stubEnv("APPSELL_PUBLIC_ORIGIN", ORIGIN);
  vi.stubEnv("APPSELL_CSRF_HMAC_KEY_B64", CSRF_KEY);
  mockModules(options.scenario ?? {});
  try {
    const { csrfTokenForSession } = await import("@/lib/mutation-protection.server");
    const handlers = await routes[route]();
    const { NextRequest } = await import("next/server");
    const method = options.method ?? "POST";
    const headers: Record<string, string> = {};
    if (options.cookie !== false) headers.cookie = SESSION_COOKIE;
    if (options.origin !== null) headers.origin = options.origin ?? ORIGIN;
    let body: string | undefined;
    if (method === "POST") {
      if (options.json !== undefined) {
        headers["content-type"] = "application/json";
        body = JSON.stringify(options.json);
      } else {
        headers["content-type"] = "application/x-www-form-urlencoded";
        const fields = new URLSearchParams(options.fields ?? {});
        if (options.csrf !== false) {
          fields.set("_csrf", csrfTokenForSession("sid", options.action ?? `POST:/api/me/account${route}`));
        }
        body = fields.toString();
      }
    }
    const handler = (method === "GET" ? handlers.GET : handlers.POST) as (request: unknown) => Promise<Response>;
    return await handler(new NextRequest(`${ORIGIN}/api/me/account${route}`, { method, headers, body }));
  } finally {
    vi.unstubAllEnvs();
    vi.doUnmock("@/lib/oauth-service.server");
    vi.doUnmock("@/lib/internal-api-client.server");
  }
}

describe("a porta das rotas da conta", () => {
  test("o status exige sessão e nunca é cacheado", async () => {
    const anonymous = await call("", { method: "GET", cookie: false });
    expect(anonymous.status).toBe(401);
    const ok = await call("", { method: "GET" });
    expect(ok.status).toBe(200);
    expect(await ok.json()).toEqual({ deletion_requested_at: null });
    expect(ok.headers.get("cache-control")).toContain("no-store");
  });

  test("sem a origem configurada a mutação não passa, mesmo com sessão e token", async () => {
    expect((await call("/export", { origin: null })).status).toBe(403);
    expect((await call("/export", { origin: "https://evil.test" })).status).toBe(403);
    expect((await call("/deletion-request", { origin: "null" })).status).toBe(403);
  });

  test("sem sessão viva é 401; sem o token da ação certa é 403", async () => {
    expect((await call("/export", { scenario: { session: false } })).status).toBe(401);
    // Corpo sem o token é corpo malformado (422); token de outra ação é
    // recusa da assinatura (403). São defeitos diferentes, e a resposta diz qual.
    expect((await call("/export", { csrf: false })).status).toBe(422);
    expect((await call("/export", { fields: { _csrf: "forjado" } , csrf: false })).status).toBe(403);
    // O token do resgate de link não serve para exportar: cada ação tem o seu.
    const wrongAction = await call("/export", { action: "POST:/api/library/access-links/redeem" });
    expect(wrongAction.status).toBe(403);
    const crossed = await call("/deletion-request", { action: "POST:/api/me/account/export" });
    expect(crossed.status).toBe(403);
  });

  test("um campo a mais no corpo é pedido malformado, não algo a ignorar", async () => {
    // O modo de falha desta onda: alguém manda o id de outra pessoa.
    const forged = await call("/export", { fields: { identity_user_id: "00000000-0000-4000-8000-000000000001" } });
    expect(forged.status).toBe(422);
    expect(await forged.json()).toEqual({ error: "invalid_request_body" });
    const asJson = await call("/deletion-request", { json: { _csrf: "x" } });
    expect(asJson.status).toBe(422);
  });

  test("a taxa fecha a porta com 429 antes de falar com a API", async () => {
    const limited = await call("/export", { scenario: { allowed: false, domainError: { status: 500, code: "should_not_be_called" } } });
    expect(limited.status).toBe(429);
    expect(await limited.json()).toEqual({ error: "rate_limited" });
  });

  test("a cota é da pessoa, não da sessão", async () => {
    // Abrir dez sessões não pode comprar dez cotas: a chave é o sujeito.
    await call("/export");
    expect(lastRateLimit).toEqual({ action: "account-export", clientKey: SESSION.subject });
    await call("/deletion-request");
    expect(lastRateLimit).toEqual({ action: "account-deletion-request", clientKey: SESSION.subject });
  });
});

describe("a exportação como arquivo", () => {
  test("chega como anexo JSON com nome datado e sem cache", async () => {
    const response = await call("/export");
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/json; charset=utf-8");
    expect(response.headers.get("content-disposition")).toMatch(
      /^attachment; filename="meus-dados-autonomia-sell-\d{4}-\d{2}-\d{2}\.json"$/
    );
    expect(response.headers.get("cache-control")).toContain("no-store");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(await response.json()).toEqual(EXPORT);
  });

  test("um documento que não é a exportação vira 503, não um arquivo vazio", async () => {
    for (const garbage of [null, [], {}, { schema_version: 2, identity: {}, exported_at: NOW }, { error: "x" }]) {
      const response = await call("/export", { scenario: { exportData: garbage } });
      expect(response.status).toBe(503);
      expect(await response.json()).toEqual({ error: "invalid_account_response" });
    }
  });

  test("o erro da API de domínio passa com o código dela — inclusive o teto de tamanho", async () => {
    const response = await call("/export", { scenario: { domainError: { status: 403, code: "forbidden" } } });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "forbidden" });
    // Acima de 8 MiB o banco recusa; a tela precisa distinguir isso de "fora do ar".
    const tooLarge = await call("/export", { scenario: { domainError: { status: 413, code: "account_export_too_large" } } });
    expect(tooLarge.status).toBe(413);
    expect(await tooLarge.json()).toEqual({ error: "account_export_too_large" });
  });
});

describe("o pedido de exclusão", () => {
  test("registra com 201 e devolve o mesmo com 200 quando já existia", async () => {
    const first = await call("/deletion-request");
    expect(first.status).toBe(201);
    expect(await first.json()).toEqual({ requested_at: NOW, already_requested: false });
    const again = await call("/deletion-request", { scenario: { deletion: { requested_at: NOW, already_requested: true } } });
    expect(again.status).toBe(200);
    expect(await again.json()).toEqual({ requested_at: NOW, already_requested: true });
  });

  test("uma resposta fora do contrato não vira pedido registrado", async () => {
    const response = await call("/deletion-request", { scenario: { deletion: { requested_at: "ontem", already_requested: false } } });
    expect(response.status).toBe(503);
  });
});

describe("a conta no produto", () => {
  const shell = readFileSync(resolve(root, "app/components/app-shell.tsx"), "utf8");
  const between = (from: string, to: string) => shell.slice(shell.indexOf(from), shell.indexOf(to));

  test("toda identidade real vê Minha conta; a persona demo de aluno não", () => {
    expect(between("const sellerNavigation", "const buyerNavigation")).toContain("accountNavigation,");
    expect(between("const buyerNavigation", "const demoBuyerNavigation")).toContain("accountNavigation,");
    expect(between("const withoutStoreNavigation", "const recoveryNavigation")).toContain("accountNavigation,");
    expect(between("const demoBuyerNavigation", "const withoutStoreNavigation")).not.toContain("accountNavigation");
    expect(shell).toContain('audience === "demo-buyer"\n          ? demoBuyerNavigation');
  });

  test("o proxy cobre a rota e a matriz de autorização a descreve", () => {
    const proxy = readFileSync(resolve(root, "proxy.ts"), "utf8");
    expect(proxy).toContain('"/settings/:path*"');
    const matrix = readFileSync(resolve(root, "docs/architecture/AUTHORIZATION_MATRIX.md"), "utf8");
    for (const row of ["Exportar os próprios dados da conta", "Pedir exclusão da própria conta", "Ler status da própria conta"]) {
      expect(matrix).toContain(row);
    }
  });

  test("a exportação manual espelha a função: mesmas seções, um único sujeito, só leitura", () => {
    // O caminho manual contorna as guardas do banco; o que o protege é a
    // forma do script. Cada seção filtra pela MESMA variável, e o script não
    // escreve nada.
    const script = readFileSync(resolve(root, "scripts/ops/exporta-conta.sql"), "utf8");
    const migration = readFileSync(
      resolve(root, "apps/api/migrations/versions/0051_minha_conta_do_comprador.py"),
      "utf8"
    );
    expect(script).toContain("begin read only;");
    expect(script).not.toMatch(/\b(insert|update|delete|truncate|alter|drop)\b/i);
    const sections = ["memberships", "accesses", "purchases", "learning", "pdf_bookmarks", "pdf_annotations", "emails_sent", "activity"];
    for (const section of sections) {
      expect(script).toContain(`'${section}',coalesce((`);
      expect(migration).toContain(`'${section}',coalesce((`);
    }
    // Toda cláusula que filtra por pessoa usa a variável do psql — nenhum
    // UUID literal, nenhum e-mail.
    const subjectFilters = script.match(/(identity_user_id|buyer_identity_user_id|recipient_identity_user_id|actor_identity_user_id|subject\.id|id)=:'sujeito'::uuid/g) ?? [];
    expect(subjectFilters.length).toBeGreaterThanOrEqual(10);
    expect(script).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/);
    expect(script).not.toMatch(/email_normalized=/);
    for (const forbidden of ["identity_sub", "identity_issuer", "sha256", "metadata"]) {
      // Aparecem só no comentário que diz o que NÃO sai, nunca numa seleção.
      const inSelect = script.split("\n").filter((line) => !line.trimStart().startsWith("--") && line.includes(forbidden));
      expect(inSelect).toEqual([]);
    }
  });

  test("a migration da conta está na cabeça fixada e no schema_ready", () => {
    const migration = readFileSync(
      resolve(root, "apps/api/migrations/versions/0051_minha_conta_do_comprador.py"),
      "utf8"
    );
    const revision = migration.match(/^revision = "([^"]+)"/m)?.[1];
    expect(revision).toBe("0051_minha_conta_do_comprador");
    expect(migration).toContain(`'${revision}'))`);
    const reset = readFileSync(resolve(root, "apps/api/src/appsell_api/reset_demo_tenant.py"), "utf8");
    expect(reset).toContain(`EXPECTED_MIGRATION = "${revision}"`);
    const script = readFileSync(resolve(root, "scripts/test-postgres-foundation.sh"), "utf8");
    expect(script).toContain("test_postgres_minha_conta.py");
    expect(script).toContain("test_api_minha_conta.py");
  });
});
