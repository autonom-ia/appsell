"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

interface LibraryAsset {
  id: string;
  lesson_id: string;
  seed_key: string;
  title: string;
  media_type: "application/pdf";
  byte_size: number;
  sha256: string;
  position: number;
}

interface LibraryLesson {
  id: string;
  title: string;
  content: string;
  position: number;
}

interface LibraryItem {
  seller: { id: string; slug: string; name: string };
  product: {
    id: string;
    slug: string;
    title: string;
    description: string;
    publication_id: string;
    publication_version: number;
  };
  progress_percent: number;
  snapshot: {
    courses: Array<{
      id: string;
      title: string;
      modules: Array<{ id: string; title: string; lessons: LibraryLesson[] }>;
    }>;
  };
  assets: LibraryAsset[];
}

type State = "loading" | "ready" | "unauthorized" | "forbidden" | "unavailable" | "error";

export function LibraryWorkspace() {
  const [state, setState] = useState<State>("loading");
  const [items, setItems] = useState<LibraryItem[]>([]);

  const load = useCallback(async () => {
    setState("loading");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch("/api/library", { cache: "no-store", signal: controller.signal });
      if (response.status === 401) return setState("unauthorized");
      if (response.status === 403) return setState("forbidden");
      if (response.status === 503) return setState("unavailable");
      if (!response.ok) return setState("error");
      const payload: unknown = await response.json();
      if (!isLibraryPayload(payload)) return setState("error");
      setItems(payload.items);
      setState("ready");
    } catch {
      setState("unavailable");
    } finally {
      window.clearTimeout(timeout);
    }
  }, []);

  useEffect(() => {
    const start = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(start);
  }, [load]);

  if (state !== "ready") {
    const messages: Record<Exclude<State, "ready">, string> = {
      loading: "Carregando sua biblioteca…",
      unauthorized: "Sua sessão expirou. Entre novamente.",
      forbidden: "Esta conta não possui acesso à biblioteca solicitada.",
      unavailable: "A biblioteca não respondeu. Tente novamente.",
      error: "Não foi possível validar os dados da biblioteca."
    };
    return <section className="library-state" aria-live="polite">
      <p>{messages[state]}</p>
      {state === "unauthorized" || state === "forbidden"
        ? <Link className="primary-button" href="/">Voltar para a entrada</Link>
        : state === "unavailable" || state === "error"
          ? <button type="button" className="primary-button" onClick={() => void load()}>Tentar novamente</button>
          : null}
    </section>;
  }

  if (!items.length) {
    return <p className="empty-state">Nenhum produto ativo está disponível para esta conta.</p>;
  }

  return <div className="learner-library">{items.map((item) => {
    const assets = new Map(item.assets.map((asset) => [asset.lesson_id, asset]));
    return <article key={`${item.seller.id}:${item.product.id}`} className="learner-course" aria-labelledby={`course-${item.product.id}`}>
      <header>
        <div><p className="eyebrow">{item.seller.name} · acesso autorizado</p><h2 id={`course-${item.product.id}`}>{item.product.title}</h2><p>{item.product.description}</p></div>
        <span className="publication-badge">Versão publicada {item.product.publication_version}</span>
      </header>
      <div className="course-progress">
        <label htmlFor={`progress-${item.product.id}`}>Progresso geral: {item.progress_percent}%</label>
        <progress id={`progress-${item.product.id}`} max={100} value={item.progress_percent}>{item.progress_percent}%</progress>
      </div>
      {item.snapshot.courses.map((course) => <section className="learner-course-block" key={course.id}>
        <h3>{course.title}</h3>
        {course.modules.map((module) => <section className="learner-module" key={module.id}>
          <h4>{module.title}</h4>
          <ol className="learner-lessons">
            {module.lessons.map((lesson, index) => {
              const asset = assets.get(lesson.id);
              return <li key={lesson.id}>
                <div><span>Aula {index + 1}</span><h5>{lesson.title}</h5><p>{lesson.content}</p></div>
                {asset ? <Link className="secondary-button" href={`/api/library/assets/${asset.seed_key}`} target="_blank" rel="noopener">Abrir material em PDF<span className="sr-only"> da aula {lesson.title} em nova aba</span></Link> : <span className="asset-unavailable">PDF indisponível</span>}
              </li>;
            })}
          </ol>
        </section>)}
      </section>)}
    </article>;
  })}</div>;
}

export function isLibraryPayload(value: unknown): value is { items: LibraryItem[] } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const items = (value as Record<string, unknown>).items;
  if (!Array.isArray(items)) return false;
  return items.every(isLibraryItem);
}

function isLibraryItem(value: unknown): value is LibraryItem {
  if (!isRecord(value)) return false;
  return isStringRecord(value.seller, ["id", "slug", "name"]) &&
    isProduct(value.product) && Number.isInteger(value.progress_percent) &&
    typeof value.progress_percent === "number" && value.progress_percent >= 0 &&
    value.progress_percent <= 100 && Array.isArray(value.assets) &&
    value.assets.every(isAsset) && isRecord(value.snapshot) &&
    Array.isArray(value.snapshot.courses) && value.snapshot.courses.every(isCourse);
}

function isProduct(value: unknown) {
  return isStringRecord(value, ["id", "slug", "title", "description", "publication_id"]) &&
    Number.isInteger(value.publication_version) && (value.publication_version as number) > 0;
}

function isAsset(value: unknown): value is LibraryAsset {
  return isStringRecord(value, ["id", "lesson_id", "seed_key", "title", "sha256"]) &&
    value.media_type === "application/pdf" && Number.isInteger(value.byte_size) &&
    (value.byte_size as number) > 0 && Number.isInteger(value.position) &&
    (value.position as number) >= 0;
}

function isCourse(value: unknown) {
  return isStringRecord(value, ["id", "title"]) && Array.isArray(value.modules) &&
    value.modules.every((module) => isStringRecord(module, ["id", "title"]) &&
      Array.isArray(module.lessons) && module.lessons.every((lesson) =>
        isStringRecord(lesson, ["id", "title", "content"]) &&
        Number.isInteger(lesson.position) && (lesson.position as number) >= 0));
}

function isStringRecord(value: unknown, keys: string[]): value is Record<string, unknown> {
  return isRecord(value) && keys.every((key) => typeof value[key] === "string" && value[key] !== "");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
