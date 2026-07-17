import { NextRequest, NextResponse } from "next/server";

import { sessionCookieName } from "@/lib/cookies";
import {
  createDomainProductDraft,
  DomainApiError,
  listDomainProductDrafts
} from "@/lib/internal-api-client.server";
import { PayloadTooLargeError, readLimitedForm } from "@/lib/limited-form.server";
import {
  CREATE_PRODUCT_DRAFT_CSRF_ACTION,
  verifyMutation
} from "@/lib/mutation-protection.server";
import { oauthService } from "@/lib/oauth-service.server";
import { isOAuthStoreUnavailable } from "@/lib/redis-oauth-service";
import { configuredPublicOrigin, hasConfiguredOrigin } from "@/lib/public-origin";
import {
  capacityProductCreateTelemetryEnabled,
  emitCapacityProductCreateTrace,
  traceLateCompletion,
  validCapacityProductCreateTrace
} from "@/lib/capacity-product-create-telemetry";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const CONTROL = /[\p{Cc}\p{Cs}]/u;

interface RouteContext {
  params: Promise<{ sellerId: string }>;
}

export async function GET(request: NextRequest, context: RouteContext) {
  const sellerId = await sellerIdFrom(context);
  if (!sellerId) return invalidInput("seller");
  const cursor = request.nextUrl.searchParams.get("cursor");
  if (cursor !== null && !UUID.test(cursor)) return invalidInput("cursor");
  try {
    const authenticated = await authenticatedSession(request);
    if (!authenticated) return unauthorized();
    const payload = await listDomainProductDrafts(
      authenticated.sessionId,
      authenticated.session,
      sellerId,
      cursor
    );
    return NextResponse.json(payload, { headers: { "cache-control": "no-store" } });
  } catch (error) {
    return mappedError(error);
  }
}

export async function POST(request: NextRequest, context: RouteContext) {
  const telemetryEnabled = capacityProductCreateTelemetryEnabled();
  const requestedTrace = request.headers.get("x-appsell-capacity-trace");
  const trace = telemetryEnabled && validCapacityProductCreateTrace(requestedTrace)
    ? requestedTrace
    : null;
  const started = performance.now();
  let stageStarted = started;
  let activeStage: "session_auth" | "form_csrf" | "domain_fetch" | "response" =
    "session_auth";
  const durations = { session_auth: 0, form_csrf: 0, domain_fetch: 0, response: 0 };
  let traceStatus = 500;
  let traceOutcome: "success" | "http_error" | "exception" = "exception";
  const mark = (next: typeof activeStage) => {
    const now = performance.now();
    durations[activeStage] += now - stageStarted;
    activeStage = next;
    stageStarted = now;
  };
  const finish = (response: NextResponse) => {
    const now = performance.now();
    durations[activeStage] += now - stageStarted;
    stageStarted = now;
    traceStatus = response.status;
    traceOutcome = response.status < 400 ? "success" : "http_error";
    return response;
  };
  try {
    const sellerId = await sellerIdFrom(context);
    const sessionId = request.cookies.get(sessionCookieName())?.value;
    if (!sellerId || !sessionId || !hasConfiguredOrigin(request, configuredPublicOrigin())) {
      return finish(forbiddenMutation());
    }
    let authenticated: Awaited<ReturnType<typeof authenticatedSession>>;
    try {
      authenticated = await authenticatedSession(request);
    } catch (error) {
      return finish(mappedError(error));
    }
    if (!authenticated) return finish(unauthorized());
    mark("form_csrf");

    let form: FormData | null;
    try {
      form = await readLimitedForm(request);
    } catch (error) {
      if (error instanceof PayloadTooLargeError) {
        return finish(NextResponse.json({ error: "payload_too_large" }, {
          status: 413,
          headers: { "cache-control": "no-store" }
        }));
      }
      form = null;
    }
    const csrf = field(form, "_csrf");
    if (!verifyMutation(request, sessionId, CREATE_PRODUCT_DRAFT_CSRF_ACTION, csrf)) {
      return finish(forbiddenMutation());
    }

    const idempotencyKey = field(form, "idempotency_key");
    const slug = field(form, "slug");
    const productTitle = field(form, "product_title");
    const productDescription = field(form, "product_description");
    const courseTitle = field(form, "course_title");
    if (!idempotencyKey || !UUID.test(idempotencyKey)) return finish(invalidInput("form"));
    if (!slug || Buffer.byteLength(slug) < 3 || Buffer.byteLength(slug) > 63 || !SLUG.test(slug)) {
      return finish(invalidInput("slug"));
    }
    if (!validTrimmedText(productTitle, 2, 160)) return finish(invalidInput("product_title"));
    if (productDescription === null || Buffer.byteLength(productDescription) > 2_000 || CONTROL.test(productDescription)) {
      return finish(invalidInput("product_description"));
    }
    if (!validTrimmedText(courseTitle, 2, 160)) return finish(invalidInput("course_title"));
    mark("domain_fetch");

    try {
      const payload = await createDomainProductDraft(
        authenticated.sessionId,
        authenticated.session,
        sellerId,
        { idempotencyKey, slug, productTitle, productDescription, courseTitle, capacityTrace: trace }
      );
      const created = isRecord(payload) && payload.created === true;
      mark("response");
      return finish(NextResponse.json(payload, {
        status: created ? 201 : 200,
        headers: { "cache-control": "no-store" }
      }));
    } catch (error) {
      mark("response");
      return finish(mappedError(error));
    }
  } finally {
    if (trace) {
      const concluded = performance.now();
      if (concluded > stageStarted) {
        durations[activeStage] += concluded - stageStarted;
      }
      const total = Math.round((concluded - started) * 100) / 100;
      emitCapacityProductCreateTrace({
        schema_version: 1,
        event: "appsell_capacity_product_create_trace",
        layer: "bff",
        trace,
        status: traceStatus,
        outcome: traceOutcome,
        total_ms: total,
        late_completion: traceLateCompletion(total),
        durations_ms: Object.fromEntries(
          Object.entries(durations).map(([name, value]) => [name, Math.round(value * 100) / 100])
        )
      });
    }
  }
}

async function sellerIdFrom(context: RouteContext) {
  const { sellerId } = await context.params;
  return UUID.test(sellerId) ? sellerId : null;
}

async function authenticatedSession(request: NextRequest) {
  const sessionId = request.cookies.get(sessionCookieName())?.value;
  if (!sessionId) return null;
  const session = await oauthService().getSession(sessionId);
  return session ? { sessionId, session } : null;
}

function field(form: FormData | null, name: string) {
  const value = form?.get(name);
  return typeof value === "string" ? value : null;
}

function validTrimmedText(value: string | null, minimum: number, maximum: number): value is string {
  if (value === null || value !== value.trim() || CONTROL.test(value)) return false;
  const bytes = Buffer.byteLength(value);
  return bytes >= minimum && bytes <= maximum;
}

function unauthorized() {
  return NextResponse.json({ error: "unauthorized" }, {
    status: 401,
    headers: { "cache-control": "no-store" }
  });
}

function invalidInput(fieldName: "seller" | "cursor" | "form" | "slug" | "product_title" | "product_description" | "course_title") {
  return NextResponse.json({ error: "invalid_product_input", field: fieldName }, {
    status: 422,
    headers: { "cache-control": "no-store" }
  });
}

function forbiddenMutation() {
  return NextResponse.json({ error: "forbidden_mutation" }, {
    status: 403,
    headers: { "cache-control": "no-store" }
  });
}

function mappedError(error: unknown) {
  if (isOAuthStoreUnavailable(error)) {
    return NextResponse.json({ error: "oauth_store_unavailable" }, {
      status: 503,
      headers: { "cache-control": "no-store" }
    });
  }
  if (error instanceof DomainApiError) {
    return NextResponse.json({ error: error.code }, {
      status: error.status,
      headers: { "cache-control": "no-store" }
    });
  }
  return NextResponse.json({ error: "product_api_unavailable" }, {
    status: 503,
    headers: { "cache-control": "no-store" }
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
