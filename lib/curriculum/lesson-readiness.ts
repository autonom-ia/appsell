/**
 * Se uma aula está pronta, e o que falta nela (#649).
 *
 * A lista "Seu curso" mostra o estado de cada aula sem ninguém precisar abrir
 * nada. As regras moram aqui, longe da tela, para que a lista e a aula aberta
 * digam a mesma coisa.
 */

export type UploadPhase =
  | "idle"
  | "loading"
  | "hashing"
  | "creating"
  | "uploading"
  | "confirming"
  | "verifying"
  | "delayed"
  | "ready"
  | "failed"
  | "cancelled";

/** O envio que está acontecendo nesta aba, que o servidor ainda não conhece. */
export interface LiveUpload {
  phase: UploadPhase;
  progress: number;
}

export interface LessonAssetSummary {
  assets: ReadonlyArray<{ status: string; content_role: string; media_type?: string }>;
  uploads: ReadonlyArray<{ status: string }>;
}

export type ReadinessState =
  | "unknown"
  | "missing"
  | "text_only"
  | "ready"
  | "uploading"
  | "checking"
  | "interrupted";

export interface LessonReadiness {
  state: ReadinessState;
  /** Conta como aula pronta no andamento do curso. */
  done: boolean;
  progress: number;
}

const TRANSPORT_PHASES: readonly UploadPhase[] = ["hashing", "creating", "uploading", "confirming"];
const CHECKING_PHASES: readonly UploadPhase[] = ["verifying", "delayed"];
const SERVER_CHECKING: readonly string[] = ["uploaded", "verifying"];

export function lessonReadiness({
  content,
  summary,
  live
}: {
  content: string;
  summary: LessonAssetSummary | null;
  live?: LiveUpload | null;
}): LessonReadiness {
  if (live && TRANSPORT_PHASES.includes(live.phase)) {
    return { state: "uploading", done: false, progress: clampProgress(live.progress) };
  }
  if (live && CHECKING_PHASES.includes(live.phase)) {
    return { state: "checking", done: false, progress: 100 };
  }
  if (!summary) return { state: "unknown", done: false, progress: 0 };
  const hasMain = summary.assets.some(
    (asset) => asset.status === "ready" && asset.content_role === "course_content"
  );
  if (hasMain) return { state: "ready", done: true, progress: 100 };
  if (summary.uploads.some((upload) => SERVER_CHECKING.includes(upload.status))) {
    return { state: "checking", done: false, progress: 100 };
  }
  if (summary.uploads.some((upload) => upload.status === "pending")) {
    return { state: "interrupted", done: false, progress: 0 };
  }
  if (content.trim().length > 0) return { state: "text_only", done: true, progress: 0 };
  return { state: "missing", done: false, progress: 0 };
}

export function outlineProgress(
  lessons: ReadonlyArray<{ id: string; title: string; readiness: LessonReadiness }>
): { total: number; done: number; next: { id: string; title: string } | null } {
  const pending = lessons.find((lesson) => !lesson.readiness.done);
  return {
    total: lessons.length,
    done: lessons.filter((lesson) => lesson.readiness.done).length,
    next: pending ? { id: pending.id, title: pending.title } : null
  };
}

function clampProgress(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value)));
}
