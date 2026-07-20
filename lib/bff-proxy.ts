import { configuredPublicOrigin, hasConfiguredOrigin } from "./public-origin";

export interface BffProxyConfig {
  targetBaseUrl: string;
  pathPrefix: string;
  accessTokenCookieName?: string;
  identityTokenCookieName?: string;
}

export type BffRouteContext = {
  params: Promise<{ path?: string[] }>;
};

const FETCH_TIMEOUT_MS = 15000;
const MAX_BODY_BYTES = 2_000_000;

export function createBffProxy(config: BffProxyConfig) {
  const handler = async (request: Request, context: BffRouteContext) => {
    if (!config.targetBaseUrl) {
      return json({ error: { code: "SERVER_CONFIG_ERROR", message: "Backend não configurado." } }, 500);
    }

    const cookieHeader = request.headers.get("cookie") ?? "";
    const accessToken = readCookie(cookieHeader, config.accessTokenCookieName ?? "autonomia_access_token");
    if (!accessToken) {
      return json({ error: { code: "UNAUTHORIZED", message: "Sessão inválida ou expirada." } }, 401);
    }

    // CSRF: state-changing methods carry ambient cookie auth, so require a same-origin request.
    const method = request.method.toUpperCase();
    const isStateChanging = method !== "GET" && method !== "HEAD" && method !== "OPTIONS";
    if (isStateChanging && !isSameOrigin(request)) {
      return json({ error: { code: "FORBIDDEN", message: "Origem inválida." } }, 403);
    }

    const params = await context.params;
    const segments = params.path ?? [];
    // Reject dot-segments/empty: encodeURIComponent leaves "." and ".." intact and
    // new URL() would normalize them, letting a path escape the configured prefix.
    if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) {
      return json({ error: { code: "BAD_REQUEST", message: "Caminho inválido." } }, 400);
    }
    const prefix = normalizePrefix(config.pathPrefix);
    const path = segments.map(encodeURIComponent).join("/");
    const requestUrl = new URL(request.url);
    const targetUrl = new URL(`${trimSlash(config.targetBaseUrl)}${prefix}${path ? `/${path}` : ""}`);
    // Defense in depth: the resolved path must stay within the configured prefix.
    if (targetUrl.pathname !== prefix && !targetUrl.pathname.startsWith(`${prefix}/`)) {
      return json({ error: { code: "BAD_REQUEST", message: "Caminho inválido." } }, 400);
    }
    targetUrl.search = requestUrl.search;

    const headers = forwardHeaders(request.headers);
    headers.set("authorization", `Bearer ${accessToken}`);

    // BFF owns x-identity-token: drop any client-supplied value, set only from cookie.
    headers.delete("x-identity-token");
    const identityToken = readCookie(cookieHeader, config.identityTokenCookieName ?? "autonomia_id_token");
    if (identityToken) headers.set("x-identity-token", identityToken);

    const init: RequestInit = {
      method: request.method,
      headers,
      redirect: "manual",
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS)
    };
    if (method !== "GET" && method !== "HEAD") {
      if (exceedsDeclaredBodyLimit(request)) {
        return json({ error: { code: "PAYLOAD_TOO_LARGE", message: "Corpo muito grande." } }, 413);
      }
      const body = await request.arrayBuffer();
      if (body.byteLength > MAX_BODY_BYTES) {
        return json({ error: { code: "PAYLOAD_TOO_LARGE", message: "Corpo muito grande." } }, 413);
      }
      init.body = body;
    }

    let response: Response;
    try {
      response = await fetch(targetUrl, init);
    } catch {
      return json({ error: { code: "BACKEND_UNAVAILABLE", message: "Backend indisponível." } }, 502);
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders(response.headers)
    });
  };

  return {
    GET: handler,
    POST: handler,
    PUT: handler,
    PATCH: handler,
    DELETE: handler,
    OPTIONS: handler
  };
}

function isSameOrigin(request: Request) {
  try {
    return hasConfiguredOrigin(request, configuredPublicOrigin());
  } catch {
    // Fail closed: if the public origin is not configured, reject state-changing requests.
    return false;
  }
}

function exceedsDeclaredBodyLimit(request: Request) {
  const declared = Number(request.headers.get("content-length") ?? "");
  return Number.isFinite(declared) && declared > MAX_BODY_BYTES;
}

function forwardHeaders(input: Headers) {
  const headers = new Headers(input);
  for (const name of ["host", "cookie", "connection", "content-length"]) {
    headers.delete(name);
  }
  return headers;
}

function responseHeaders(input: Headers) {
  const headers = new Headers(input);
  for (const name of ["content-encoding", "content-length", "transfer-encoding", "set-cookie"]) {
    headers.delete(name);
  }
  // Authenticated proxy fronts account/payment data: never cache unless backend opts in.
  if (!headers.has("cache-control")) {
    headers.set("cache-control", "private, no-store");
  }
  return headers;
}

function readCookie(header: string, name: string) {
  const item = header.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${name}=`));
  if (!item) return undefined;
  return decodeURIComponent(item.slice(name.length + 1));
}

function json(body: unknown, status: number) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" }
  });
}

function trimSlash(value: string) {
  return value.replace(/\/+$/g, "");
}

function normalizePrefix(value: string) {
  return `/${value.replace(/^\/+|\/+$/g, "")}`;
}
