/**
 * Tipos e chamadas do currículo vistos pelo editor (#649).
 *
 * As rotas BFF não mudaram: os mesmos formulários com `_csrf`,
 * `idempotency_key` e `expected_course_version`. O que mudou é quem chama, e
 * daqui em diante uma chamada só por vez (`serialize`): a versão do curso é
 * uma só, e duas mudanças ao mesmo tempo levavam a segunda a um 409.
 */

export interface CurriculumLesson {
  id: string;
  title: string;
  kind: "text";
  content: string;
  status: string;
  position: number;
}

export interface CurriculumModule {
  id: string;
  title: string;
  status: string;
  position: number;
  lessons: CurriculumLesson[];
}

export interface CurriculumCsrf {
  create: string;
  update: string;
  reorder: string;
  archive: string;
}

export interface CurriculumLimits {
  modules_per_course: number;
  lessons_per_module: number;
  lessons_per_course: number;
  title_bytes: number;
  lesson_content_bytes: number;
}

export interface CurriculumPayload {
  course: { id: string; draft_version: number };
  modules: CurriculumModule[];
  csrf: CurriculumCsrf;
  limits?: CurriculumLimits;
  usage?: { modules_total: number; lessons_total: number; lessons_by_module: Record<string, number> };
}

/**
 * O servidor mede nome e texto em bytes, não em letras: "ç" e "ã" ocupam dois.
 * Contar letras deixava passar um texto acentuado que o servidor recusa.
 */
export function exceedsBytes(value: string, maxBytes: number): boolean {
  return new TextEncoder().encode(value).length > maxBytes;
}

export const DEFAULT_LIMITS: CurriculumLimits = {
  modules_per_course: 50,
  lessons_per_module: 100,
  lessons_per_course: 500,
  title_bytes: 160,
  lesson_content_bytes: 8_192
};

export type MutationFailure = "offline" | "invalid" | "conflict" | "forbidden" | "error";

export type MutationResult =
  | { ok: true; payload: Record<string, unknown>; courseVersion: number }
  | { ok: false; failure: MutationFailure; field?: string };

const MUTATION_TIMEOUT_MS = 12_000;

export function validCurriculum(payload: unknown, expectedCourseId: string): payload is CurriculumPayload {
  if (!payload || typeof payload !== "object") return false;
  const value = payload as Partial<CurriculumPayload>;
  return value.course?.id === expectedCourseId
    && Number.isSafeInteger(value.course.draft_version)
    && Array.isArray(value.modules)
    && Boolean(value.csrf)
    && typeof value.csrf?.create === "string";
}

export async function sendCurriculumMutation({
  url,
  method,
  csrf,
  idempotencyKey,
  courseVersion,
  fields,
  keepalive = false
}: {
  url: string;
  method: "POST" | "PUT" | "PATCH";
  csrf: string;
  idempotencyKey: string;
  courseVersion: number;
  fields: Record<string, string>;
  keepalive?: boolean;
}): Promise<MutationResult> {
  const form = new FormData();
  form.set("_csrf", csrf);
  form.set("idempotency_key", idempotencyKey);
  form.set("expected_course_version", String(courseVersion));
  for (const [name, value] of Object.entries(fields)) form.set(name, value);
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), MUTATION_TIMEOUT_MS);
  try {
    const response = await fetch(url, { method, body: form, signal: controller.signal, keepalive });
    if (!response.ok) return { ok: false, ...(await failureOf(response)) };
    const payload = await response.json() as Record<string, unknown>;
    if (typeof payload.course_version !== "number") return { ok: false, failure: "error" };
    return { ok: true, payload, courseVersion: payload.course_version };
  } catch {
    return { ok: false, failure: "offline" };
  } finally {
    window.clearTimeout(timeout);
  }
}

async function failureOf(response: Response): Promise<{ failure: MutationFailure; field?: string }> {
  if (response.status === 401 || response.status === 403) return { failure: "forbidden" };
  if (response.status === 409) return { failure: "conflict" };
  if (response.status === 422) {
    const payload = await response.json().catch(() => null) as { field?: unknown } | null;
    return { failure: "invalid", field: typeof payload?.field === "string" ? payload.field : undefined };
  }
  if (response.status === 503) return { failure: "offline" };
  return { failure: "error" };
}

/** Uma chamada por vez, na ordem em que foram pedidas. */
export function createSerializer() {
  let tail: Promise<unknown> = Promise.resolve();
  return function serialize<T>(task: () => Promise<T>): Promise<T> {
    const run = tail.then(task, task);
    tail = run.catch(() => undefined);
    return run;
  };
}
