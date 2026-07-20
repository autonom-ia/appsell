export interface BffProxyConfig {
  targetBaseUrl: string;
  pathPrefix: string;
  accessTokenCookieName?: string;
  identityTokenCookieName?: string;
}

export type BffRouteContext = {
  params: Promise<{ path?: string[] }>;
};

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

    const params = await context.params;
    const path = (params.path ?? []).map(encodeURIComponent).join("/");
    const requestUrl = new URL(request.url);
    const targetUrl = new URL(`${trimSlash(config.targetBaseUrl)}${normalizePrefix(config.pathPrefix)}${path ? `/${path}` : ""}`);
    targetUrl.search = requestUrl.search;

    const headers = forwardHeaders(request.headers);
    headers.set("authorization", `Bearer ${accessToken}`);

    const identityToken = readCookie(cookieHeader, config.identityTokenCookieName ?? "autonomia_id_token");
    if (identityToken) headers.set("x-identity-token", identityToken);

    const init: RequestInit = {
      method: request.method,
      headers,
      redirect: "manual"
    };
    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = await request.arrayBuffer();
    }

    const response = await fetch(targetUrl, init);

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

function forwardHeaders(input: Headers) {
  const headers = new Headers(input);
  for (const name of ["host", "cookie", "connection", "content-length"]) {
    headers.delete(name);
  }
  return headers;
}

function responseHeaders(input: Headers) {
  const headers = new Headers(input);
  for (const name of ["content-encoding", "content-length", "transfer-encoding"]) {
    headers.delete(name);
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
