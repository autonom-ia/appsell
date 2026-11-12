"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { LessonAssetSummary } from "@/lib/curriculum/lesson-readiness";

/**
 * Os arquivos de cada aula, para a lista do curso mostrar o estado sem
 * ninguém abrir nada (#649, decisão D3).
 *
 * Uma chamada por aula, no máximo quatro ao mesmo tempo. O resumo no próprio
 * currículo, com uma chamada só, é o #650. Uma aula com arquivo sendo conferido
 * é perguntada de novo a cada poucos segundos, até ficar pronta ou o tempo
 * acabar: a lista é o lugar para onde a pessoa volta enquanto espera.
 */

const CONCURRENCY = 4;
const CHECK_EVERY_MS = 5_000;
const CHECK_FOR_MS = 3 * 60_000;
const REQUEST_TIMEOUT_MS = 10_000;
const CHECKING: readonly string[] = ["uploaded", "verifying"];

export interface LessonRef {
  moduleId: string;
  lessonId: string;
}

export function useLessonSummaries(courseBase: string, lessons: readonly LessonRef[]) {
  const [summaries, setSummaries] = useState<Record<string, LessonAssetSummary>>({});
  const known = useRef(new Map<string, LessonRef>());
  const checkingSince = useRef(new Map<string, number>());
  const sequence = useRef(new Map<string, number>());

  const fetchOne = useCallback(async (ref: LessonRef) => {
    const seq = (sequence.current.get(ref.lessonId) ?? 0) + 1;
    sequence.current.set(ref.lessonId, seq);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(
        `${courseBase}/modules/${ref.moduleId}/lessons/${ref.lessonId}/assets`,
        { cache: "no-store", signal: controller.signal }
      );
      if (!response.ok) return;
      const payload = await response.json() as Partial<LessonAssetSummary>;
      if (!Array.isArray(payload.assets) || !Array.isArray(payload.uploads)) return;
      if (sequence.current.get(ref.lessonId) !== seq) return;
      const summary: LessonAssetSummary = { assets: payload.assets, uploads: payload.uploads };
      const checking = summary.uploads.some((upload) => CHECKING.includes(upload.status));
      if (checking && !checkingSince.current.has(ref.lessonId)) checkingSince.current.set(ref.lessonId, Date.now());
      if (!checking) checkingSince.current.delete(ref.lessonId);
      setSummaries((current) => ({ ...current, [ref.lessonId]: summary }));
    } catch {
      // A lista mostra "—" para esta aula; abrir a aula carrega de novo.
    } finally {
      window.clearTimeout(timeout);
    }
  }, [courseBase]);

  const fetchMany = useCallback(async (refs: readonly LessonRef[]) => {
    let next = 0;
    const worker = async () => {
      while (next < refs.length) {
        const ref = refs[next];
        next += 1;
        await fetchOne(ref);
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, refs.length) }, worker));
  }, [fetchOne]);

  const lessonsKey = lessons.map((lesson) => `${lesson.moduleId}/${lesson.lessonId}`).join("|");
  useEffect(() => {
    const fresh = lessons.filter((lesson) => known.current.get(lesson.lessonId)?.moduleId !== lesson.moduleId);
    known.current = new Map(lessons.map((lesson) => [lesson.lessonId, lesson]));
    if (fresh.length === 0) return;
    const start = window.setTimeout(() => void fetchMany(fresh), 0);
    return () => window.clearTimeout(start);
    // `lessonsKey` resume a lista; o array muda de identidade a cada render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lessonsKey, fetchMany]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.hidden) return;
      const now = Date.now();
      const due = [...checkingSince.current.entries()]
        .filter(([, since]) => now - since < CHECK_FOR_MS)
        .map(([lessonId]) => known.current.get(lessonId))
        .filter((ref): ref is LessonRef => Boolean(ref));
      if (due.length > 0) void fetchMany(due);
    }, CHECK_EVERY_MS);
    return () => window.clearInterval(timer);
  }, [fetchMany]);

  const refresh = useCallback((lessonId: string) => {
    const ref = known.current.get(lessonId);
    if (ref) void fetchOne(ref);
  }, [fetchOne]);

  return { summaries, refresh };
}
