import { readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";

import { canConfigureStore } from "../lib/shell-navigation";
import { parsePublicStorefront } from "../lib/public-storefront";
import {
  MIN_ACCENT_CONTRAST,
  accentRejectionMessage,
  contrastRatio,
  isAccentReadable,
  normalizeAccentColor,
  storeAccent,
  storeInitials
} from "../lib/store-brand";
import { parseStoreSettings } from "../lib/store-settings";
import nextConfig from "../next.config";

vi.mock("server-only", () => ({}));

// Configurações da loja (#352): a régua de contraste, o parser da vitrine com
// os campos novos (e sem eles), o contrato das configurações, quem vê o menu,
// o redirect de /payouts e a porta da rota de gravação.

describe("cor de destaque", () => {
  it("aceita só #rrggbb minúsculo, e normaliza o que o formulário manda", () => {
    expect(normalizeAccentColor("#1A6B52")).toBe("#1a6b52");
    expect(normalizeAccentColor(" 1a6b52 ")).toBe("#1a6b52");
    expect(normalizeAccentColor("")).toBeNull();
    expect(normalizeAccentColor("#12345")).toBeNull();
    expect(normalizeAccentColor("verde")).toBeNull();
  });

  it("mede o contraste contra branco como a WCAG", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrastRatio("#ffffff", "#ffffff")).toBeCloseTo(1, 3);
    // Simétrica: a ordem dos argumentos não muda a conta.
    expect(contrastRatio("#1a6b52", "#ffffff")).toBeCloseTo(contrastRatio("#ffffff", "#1a6b52"), 6);
  });

  it("recusa a cor que deixaria o botão ilegível, e aceita a que passa", () => {
    expect(isAccentReadable("#1a6b52")).toBe(true);
    expect(isAccentReadable("#1e3a8a")).toBe(true);
    expect(isAccentReadable("#ffcc00")).toBe(false);
    expect(isAccentReadable("#ffffff")).toBe(false);
    expect(isAccentReadable("#767676")).toBe(true);
    expect(isAccentReadable("#777777")).toBe(false);
    expect(accentRejectionMessage("#ffcc00")).toContain(`${MIN_ACCENT_CONTRAST}:1`);
  });

  it("na renderização, cor ruim vira nada — o tom da Autonom.ia vale", () => {
    expect(storeAccent("#1a6b52")).toBe("#1a6b52");
    expect(storeAccent("#ffcc00")).toBeNull();
    expect(storeAccent("#FFCC00")).toBeNull();
    expect(storeAccent(undefined)).toBeNull();
    expect(storeAccent(42)).toBeNull();
  });

  it("gera as iniciais como a vitrine sempre fez", () => {
    expect(storeInitials("Hub2you")).toBe("HU");
    expect(storeInitials("Studio Marina")).toBe("SM");
    expect(storeInitials("   ")).toBe("—");
  });
});

describe("vitrine com marca", () => {
  const base = {
    schema_version: 1 as const,
    seller: { id: "8d7d1a3a-2d24-4a7f-9b0e-2b1a5b9f0c11", slug: "hub2you", name: "Hub2you" },
    products: [{
      id: "1ce240ca-df55-4945-8ff9-b059f810e9b4",
      slug: "ia-na-pratica",
      title: "IA na prática",
      description: "Playbooks.",
      publication_id: "61831a73-f4ce-5b05-be58-f61890d3cc8a",
      publication_version: 1,
      access_mode: "paid" as const,
      price: { unit_amount_minor: 4700, currency: "BRL", billing_period: "one_time" as const }
    }]
  };
  const image = { url: "https://cdn.test/tenants/x/logo/1", media_type: "image/png", width: 300, height: 300, expires_in_seconds: 60 };

  it("continua aceitando a loja sem marca — o contrato do web antigo", () => {
    expect(parsePublicStorefront(base)).toEqual(base);
  });

  it("aceita marca, capa, promessa e o resumo do conteúdo", () => {
    const branded = {
      ...base,
      seller: { ...base.seller, tagline: "IA para corretores", about: "Doze anos.", accent_color: "#1a6b52", logo: image, banner: { ...image, width: 1600, height: 500 } },
      products: [{
        ...base.products[0],
        headline: "Feche mais apólices",
        cover: image,
        curriculum: { lesson_count: 5, material_count: 2, lessons: ["A", "B"] }
      }]
    };
    expect(parsePublicStorefront(branded)).toEqual(branded);
  });

  it("recusa chave de objeto no lugar de URL, cor fora do formato e chave desconhecida", () => {
    expect(parsePublicStorefront({ ...base, seller: { ...base.seller, logo: { ...image, url: "tenants/x/logo/1" } } })).toBeNull();
    expect(parsePublicStorefront({ ...base, seller: { ...base.seller, logo: { object_key: "k", storage_version_id: "v", media_type: "image/png", width: 1, height: 1 } } })).toBeNull();
    expect(parsePublicStorefront({ ...base, seller: { ...base.seller, banner: { ...image, url: "tenants/x/banner/1" } } })).toBeNull();
    expect(parsePublicStorefront({ ...base, seller: { ...base.seller, accent_color: "#1A6B52" } })).toBeNull();
    expect(parsePublicStorefront({ ...base, seller: { ...base.seller, storage_version_id: "v" } })).toBeNull();
    expect(parsePublicStorefront({ ...base, products: [{ ...base.products[0], curriculum: { lesson_count: 0, material_count: 0, lessons: [] } }] })).toBeNull();
    expect(parsePublicStorefront({ ...base, products: [{ ...base.products[0], curriculum: { lesson_count: 1, material_count: 0, lessons: ["A", "B"] } }] })).toBeNull();
  });
});

describe("contrato das configurações", () => {
  const settings = {
    id: "8d7d1a3a-2d24-4a7f-9b0e-2b1a5b9f0c11",
    slug: "hub2you",
    display_name: "Hub2you",
    status: "active",
    tagline: null,
    about: null,
    accent_color: "#1a6b52",
    logo: { url: "https://cdn.test/l", media_type: "image/png", width: 300, height: 300 },
    banner: { url: "https://cdn.test/b", media_type: "image/jpeg", width: 1600, height: 500 }
  };

  it("aceita a forma que o BFF devolve, com e sem logo ou banner", () => {
    expect(parseStoreSettings(settings)).toEqual(settings);
    expect(parseStoreSettings({ ...settings, logo: null })?.logo).toBeNull();
    expect(parseStoreSettings({ ...settings, banner: null })?.banner).toBeNull();
    const semBanner = { ...settings, banner: undefined };
    expect(parseStoreSettings(semBanner)?.banner).toBeNull();
  });

  it("recusa logo ou banner que não seja uma URL https, e nome fora da régua", () => {
    expect(parseStoreSettings({ ...settings, logo: { ...settings.logo, url: "http://x" } })).toBeNull();
    expect(parseStoreSettings({ ...settings, banner: { ...settings.banner, url: "http://x" } })).toBeNull();
    expect(parseStoreSettings({ ...settings, display_name: "x" })).toBeNull();
    expect(parseStoreSettings({ ...settings, accent_color: "verde" })).toBeNull();
  });
});

describe("quem vê Configurações", () => {
  it("dono e admin da primeira loja; editor, suporte e sem loja, não", () => {
    expect(canConfigureStore([{ role: "seller_owner" }])).toBe(true);
    expect(canConfigureStore([{ role: "seller_admin" }])).toBe(true);
    expect(canConfigureStore([{ role: "seller_editor" }])).toBe(false);
    expect(canConfigureStore([{ role: "seller_support" }])).toBe(false);
    expect(canConfigureStore([{ role: "platform_superadmin" }])).toBe(false);
    expect(canConfigureStore([])).toBe(false);
    expect(canConfigureStore(null)).toBe(false);
  });
});

describe("/payouts", () => {
  it("redireciona de forma permanente para /settings/payouts", async () => {
    const redirects = await nextConfig.redirects!();
    expect(redirects).toContainEqual({ source: "/payouts", destination: "/settings/payouts", permanent: true });
  });
});

describe("a porta da rota PATCH /api/sellers/[id]/settings", () => {
  const ORIGIN = "https://sell.test";
  const CSRF_KEY = Buffer.alloc(32, 7).toString("base64");
  const SELLER = "8d7d1a3a-2d24-4a7f-9b0e-2b1a5b9f0c11";
  const SESSION = { subject: "sub-1", tokenRevision: 1, expiresAt: 0, identity: { email: "a@b.test", emailVerified: true } };

  interface Scenario { updates: Array<Record<string, unknown>>; domainError?: { status: number; code: string } }

  async function call(fields: Record<string, string>, options: { csrf?: boolean; scenario?: Scenario } = {}) {
    vi.resetModules();
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("APPSELL_PUBLIC_ORIGIN", ORIGIN);
    vi.stubEnv("APPSELL_CSRF_HMAC_KEY_B64", CSRF_KEY);
    const scenario: Scenario = options.scenario ?? { updates: [] };
    vi.doMock("@/lib/oauth-service.server", () => ({
      oauthService: () => ({ getSession: async (id?: string) => (id === "sid" ? SESSION : null) })
    }));
    vi.doMock("@/lib/internal-api-client.server", () => {
      class DomainApiError extends Error {
        constructor(readonly status: number, readonly code: string) { super(code); }
      }
      return {
        DomainApiError,
        readDomainSellerSettings: async () => { throw new Error("not used"); },
        updateDomainSellerSettings: async (_s: string, _v: unknown, _id: string, input: Record<string, unknown>) => {
          if (scenario.domainError) throw new DomainApiError(scenario.domainError.status, scenario.domainError.code);
          scenario.updates.push(input);
          return {
            id: SELLER, slug: "hub2you", display_name: input.displayName, status: "active",
            tagline: input.tagline, about: input.about, accent_color: input.accentColor, logo: null, changed: true
          };
        }
      };
    });
    try {
      const { csrfTokenForSession } = await import("@/lib/mutation-protection.server");
      const { PATCH } = await import("../app/api/sellers/[sellerId]/settings/route");
      const { NextRequest } = await import("next/server");
      const body = new URLSearchParams({
        ...(options.csrf === false ? {} : { _csrf: csrfTokenForSession("sid", "PATCH:/api/sellers/settings") }),
        ...fields
      });
      const request = new NextRequest(`${ORIGIN}/api/sellers/${SELLER}/settings`, {
        method: "PATCH",
        headers: { cookie: "__Host-appsell_session=sid", origin: ORIGIN, "content-type": "application/x-www-form-urlencoded" },
        body
      });
      const response = await PATCH(request, { params: Promise.resolve({ sellerId: SELLER }) });
      return { status: response.status, body: await response.json().catch(() => null), scenario };
    } finally {
      vi.unstubAllEnvs();
      vi.doUnmock("@/lib/oauth-service.server");
      vi.doUnmock("@/lib/internal-api-client.server");
    }
  }

  it("grava com nome, frase, sobre e cor normalizada", async () => {
    const result = await call({ display_name: " Hub2you ", tagline: "IA para corretores", about: "", accent_color: "#1A6B52" });
    expect(result.status).toBe(200);
    expect(result.scenario.updates).toEqual([{ displayName: "Hub2you", tagline: "IA para corretores", about: null, accentColor: "#1a6b52" }]);
    expect(result.body.settings.accent_color).toBe("#1a6b52");
    expect(result.body.changed).toBe(true);
  });

  it("recusa a cor ilegível antes de falar com a API, e explica", async () => {
    const result = await call({ display_name: "Hub2you", tagline: "", about: "", accent_color: "#ffcc00" });
    expect(result.status).toBe(422);
    expect(result.body.error).toBe("accent_contrast");
    expect(result.body.message).toContain("4.5:1");
    expect(result.scenario.updates).toEqual([]);
  });

  it("recusa formato de cor errado, campo a mais (slug) e token ausente", async () => {
    expect((await call({ display_name: "Hub2you", tagline: "", about: "", accent_color: "azul" })).status).toBe(422);
    expect((await call({ display_name: "Hub2you", tagline: "", about: "", accent_color: "", slug: "novo" })).status).toBe(422);
    expect((await call({ display_name: "Hub2you", tagline: "", about: "", accent_color: "" }, { csrf: false })).status).toBe(422);
  });

  it("repassa a recusa de papel da API como 403", async () => {
    const result = await call(
      { display_name: "Hub2you", tagline: "", about: "", accent_color: "" },
      { scenario: { updates: [], domainError: { status: 403, code: "forbidden" } } }
    );
    expect(result.status).toBe(403);
    expect(result.body.error).toBe("forbidden");
  });
});

describe("a cabeça fixada", () => {
  const root = resolve(__dirname, "..");

  it("a 0052 é a cabeça, está no schema_ready e em todos os pontos que a fixam", () => {
    const migration = readFileSync(
      resolve(root, "apps/api/migrations/versions/0052_configuracoes_da_loja.py"),
      "utf8"
    );
    const revision = migration.match(/^revision = "([^"]+)"/m)?.[1];
    expect(revision).toBe("0052_configuracoes_da_loja");
    expect(migration).toContain(`'${revision}'))`);
    expect(migration).toContain("alter function {signature} owner to appsell_owner");
    // A cabeça fixada é a última migration do diretório: a 0053 (dono das
    // funções de recebimento) veio logo depois e este teste não pode envelhecer.
    const head = readdirSync(resolve(root, "apps/api/migrations/versions"))
      .filter((name) => /^\d{4}_.*\.py$/.test(name))
      .sort()
      .at(-1)!
      .replace(/\.py$/, "");
    const reset = readFileSync(resolve(root, "apps/api/src/appsell_api/reset_demo_tenant.py"), "utf8");
    expect(reset).toContain(`EXPECTED_MIGRATION = "${head}"`);
    const script = readFileSync(resolve(root, "scripts/test-postgres-foundation.sh"), "utf8");
    expect(script).toContain("test_postgres_configuracoes_da_loja.py");
    expect(script).toContain("test_api_seller_settings.py");
  });

  it("nenhum link interno aponta mais para /payouts", () => {
    const shell = readFileSync(resolve(root, "app/components/app-shell.tsx"), "utf8");
    const editor = readFileSync(resolve(root, "app/components/product-editor-workspace.tsx"), "utf8");
    expect(shell).not.toContain('"/payouts"');
    expect(editor).not.toContain('href="/payouts"');
  });
});
