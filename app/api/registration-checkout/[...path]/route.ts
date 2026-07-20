import { NextRequest } from "next/server";

const financialApiUrl = process.env.FINANCIAL_API_URL ?? "https://financial.api-autonomia.com";

export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ path?: string[] }>;
};

export async function GET(request: NextRequest, context: RouteContext) {
  return proxyRegistrationCheckout(request, context);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxyRegistrationCheckout(request, context);
}

async function proxyRegistrationCheckout(request: NextRequest, context: RouteContext) {
  const internalApiKey = getInternalApiKey();
  if (!internalApiKey) {
    return json({ error: { code: "SERVER_CONFIG_ERROR", message: "Checkout de registro não configurado." } }, 500);
  }

  const params = await context.params;
  const sourcePath = `/${(params.path ?? []).join("/")}`;
  const targetPath = mapRegistrationCheckoutPath(sourcePath);
  if (!targetPath) {
    return json({ error: { code: "NOT_FOUND", message: "Rota de checkout não encontrada." } }, 404);
  }

  const requestUrl = new URL(request.url);
  const targetUrl = new URL(`${trimSlash(financialApiUrl)}${targetPath}`);
  targetUrl.search = requestUrl.search;

  const headers = new Headers({
    accept: "application/json",
    "x-api-key": internalApiKey
  });
  if (request.method !== "GET" && request.method !== "HEAD") {
    headers.set("content-type", request.headers.get("content-type") ?? "application/json");
  }

  const response = await fetch(targetUrl, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : await request.arrayBuffer(),
    cache: "no-store",
    redirect: "manual"
  });

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders(response.headers)
  });
}

function mapRegistrationCheckoutPath(path: string) {
  if (path === "/financial/me/product-onboarding") return "/financial/internal/product-onboarding";
  if (path === "/financial/me/registration-checkout/quote") return "/financial/internal/registration-checkout/quote";
  if (path === "/financial/me/registration-checkout/coupon") return "/financial/internal/registration-checkout/coupon";
  if (path === "/financial/me/registration-checkout/intent") return "/financial/internal/registration-checkout/intent";

  const checkoutMatch = path.match(/^\/financial\/me\/checkout\/([^/]+)$/);
  if (checkoutMatch?.[1]) return `/financial/internal/registration-checkout/${encodeURIComponent(checkoutMatch[1])}`;

  return null;
}

function responseHeaders(input: Headers) {
  const headers = new Headers(input);
  for (const name of ["content-encoding", "content-length", "transfer-encoding"]) {
    headers.delete(name);
  }
  headers.set("cache-control", "private, no-cache, no-store, max-age=0, must-revalidate");
  return headers;
}

function getInternalApiKey() {
  return process.env.FINANCIAL_INTERNAL_API_KEY ?? process.env.INTERNAL_SERVICE_API_KEY ?? "";
}

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "private, no-cache, no-store, max-age=0, must-revalidate"
    }
  });
}

function trimSlash(value: string) {
  return value.replace(/\/+$/g, "");
}
