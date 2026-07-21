import { NextRequest } from "next/server";

import { FinancialInternalConfigError, financialInternalConfig, trimTrailingSlash } from "@/lib/financial-internal-config.server";

const FETCH_TIMEOUT_MS = 15000;
const MAX_BODY_BYTES = 65_536;

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
  let config: { apiUrl: string; apiKey: string };
  try {
    config = financialInternalConfig();
  } catch (error) {
    if (error instanceof FinancialInternalConfigError) {
      return json({ error: { code: "SERVER_CONFIG_ERROR", message: "Checkout de registro não configurado." } }, 500);
    }
    throw error;
  }

  const params = await context.params;
  const sourcePath = `/${(params.path ?? []).join("/")}`;
  const targetPath = mapRegistrationCheckoutPath(sourcePath);
  if (!targetPath) {
    return json({ error: { code: "NOT_FOUND", message: "Rota de checkout não encontrada." } }, 404);
  }

  const requestUrl = new URL(request.url);
  const targetUrl = new URL(`${trimTrailingSlash(config.apiUrl)}${targetPath}`);
  targetUrl.search = requestUrl.search;

  const headers = new Headers({
    accept: "application/json",
    "x-api-key": config.apiKey
  });
  if (request.method !== "GET" && request.method !== "HEAD") {
    headers.set("content-type", request.headers.get("content-type") ?? "application/json");
  }

  let body: ArrayBuffer | undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const declared = Number(request.headers.get("content-length") ?? "");
    if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
      return json({ error: { code: "PAYLOAD_TOO_LARGE", message: "Corpo muito grande." } }, 413);
    }
    body = await request.arrayBuffer();
    if (body.byteLength > MAX_BODY_BYTES) {
      return json({ error: { code: "PAYLOAD_TOO_LARGE", message: "Corpo muito grande." } }, 413);
    }
  }

  let response: Response;
  try {
    response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS)
    });
  } catch {
    return json({ error: { code: "BACKEND_UNAVAILABLE", message: "Backend indisponível." } }, 502);
  }

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
  if (checkoutMatch?.[1]) {
    const checkoutId = checkoutMatch[1];
    // Reject dot-segments: new URL() would normalize them and escape the internal prefix.
    if (checkoutId === "." || checkoutId === "..") return null;
    return `/financial/internal/registration-checkout/${encodeURIComponent(checkoutId)}`;
  }

  return null;
}

function responseHeaders(input: Headers) {
  const headers = new Headers(input);
  for (const name of ["content-encoding", "content-length", "transfer-encoding", "set-cookie"]) {
    headers.delete(name);
  }
  headers.set("cache-control", "private, no-cache, no-store, max-age=0, must-revalidate");
  return headers;
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
