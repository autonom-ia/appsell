"use client";

import Link from "next/link";
import { FormEvent, useRef, useState } from "react";

import { exceedsBytes, type CurriculumLimits, type CurriculumModule } from "@/lib/curriculum/curriculum-api.client";
import { JOURNEY_COPY } from "@/lib/curriculum/journey-copy";
import { outlineProgress, type LessonReadiness } from "@/lib/curriculum/lesson-readiness";

/**
 * A lista "Seu curso" (#649): quantas aulas estão prontas, o próximo passo, e
 * o estado de cada aula. Nada recolhido: criar, renomear, ordenar e excluir
 * estão à vista de quem precisa deles.
 */

const MIN_TITLE = 2;
const MAX_TITLE = 160;

export interface CourseOutlineProps {
  modules: CurriculumModule[];
  limits: CurriculumLimits;
  archived: boolean;
  busy: boolean;
  selectedId: string | null;
  readinessOf: (lesson: CurriculumModule["lessons"][number]) => LessonReadiness;
  notice: string;
  onOpenLesson: (lessonId: string) => void;
  onCreateModule: (title: string) => Promise<string | null>;
  onRenameModule: (module: CurriculumModule, title: string) => Promise<boolean>;
  onDeleteModule: (module: CurriculumModule) => void;
  onMoveModule: (index: number, direction: -1 | 1) => Promise<void>;
  onCreateLesson: (module: CurriculumModule, title: string) => Promise<boolean>;
  onMoveLesson: (module: CurriculumModule, index: number, direction: -1 | 1) => Promise<void>;
  onReload: () => void;
}

export function CourseOutline(props: CourseOutlineProps) {
  const { modules, limits, archived, busy, selectedId, readinessOf, notice } = props;
  const [addingModule, setAddingModule] = useState(false);
  const [addingLessonTo, setAddingLessonTo] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);

  const lessons = modules.flatMap((module) => module.lessons.map((lesson) => ({
    id: lesson.id,
    title: lesson.title,
    readiness: readinessOf(lesson)
  })));
  const progress = outlineProgress(lessons);
  const totalLessons = lessons.length;
  const modulesFull = modules.length >= limits.modules_per_course;
  const lessonsFull = totalLessons >= limits.lessons_per_course;

  const createModule = async (title: string) => {
    const moduleId = await props.onCreateModule(title);
    if (!moduleId) return false;
    setAddingModule(false);
    setAddingLessonTo(moduleId);
    focusSoon(`#new-lesson-${moduleId}`);
    return true;
  };

  return <div className="course-outline">
    <header className="course-outline-head">
      <p className="eyebrow">{JOURNEY_COPY.courseEyebrow}</p>
      <h2 id="curriculum-title" tabIndex={-1}>{JOURNEY_COPY.courseTitle}</h2>
    </header>

    {archived ? <p className="form-note" role="status">{JOURNEY_COPY.archivedProduct}</p> : null}
    <p className="course-notice" role="status" aria-live="polite" aria-atomic="true" data-testid="curriculum-live">
      {notice}
      {notice === JOURNEY_COPY.changedElsewhere ? <button className="secondary-button" type="button" onClick={props.onReload}>{JOURNEY_COPY.reload}</button> : null}
    </p>

    {modules.length === 0 ? <div className="course-empty" data-testid="curriculum-empty">
      <h3>{JOURNEY_COPY.emptyTitle}</h3>
      <p>{JOURNEY_COPY.emptyHelp}</p>
      {!archived ? <NameForm
        maxBytes={limits.title_bytes}
        id="new-module-title"
        label={JOURNEY_COPY.moduleName}
        placeholder={JOURNEY_COPY.modulePlaceholder}
        submitLabel={JOURNEY_COPY.createModule}
        busy={busy}
        onSubmit={createModule}
      /> : null}
    </div> : <>
      <div className="course-progress" role="group" aria-label="Andamento do curso">
        <strong>{progress.done} de {progress.total} {progress.total === 1 ? "aula pronta" : "aulas prontas"}</strong>
        <div className="course-progress-bar" aria-hidden="true">
          <span style={{ width: `${progress.total ? Math.round((progress.done / progress.total) * 100) : 0}%` }}/>
        </div>
        {progress.next ? <div className="course-next">
          <p>Próximo passo: coloque o vídeo, o PDF ou o texto de “{progress.next.title}”.</p>
          <button className="primary-button" type="button" onClick={() => props.onOpenLesson(progress.next!.id)}>{JOURNEY_COPY.continue}</button>
        </div> : progress.total > 0 ? <div className="course-next">
          <p>{JOURNEY_COPY.allReady}</p>
          <Link className="primary-button" href="?section=oferta" replace scroll={false}>{JOURNEY_COPY.goToPrice}</Link>
        </div> : <p className="course-next-hint">Crie a primeira aula em um dos módulos abaixo.</p>}
      </div>

      <ol className="course-modules">
        {modules.map((module, moduleIndex) => <li key={module.id} data-testid={`module-${module.id}`}>
          <article className="course-module" aria-labelledby={`module-title-${module.id}`}>
            <div className="course-module-head">
              <div className="course-module-name">
                <small>Módulo {moduleIndex + 1}</small>
                {renaming === module.id
                  ? <h3 id={`module-title-${module.id}`} className="sr-only">{module.title}</h3>
                  : <h3 id={`module-title-${module.id}`}>{module.title}</h3>}
              </div>
              {!archived ? <div className="course-actions">
                {renaming !== module.id ? <button className="icon-button" type="button" aria-label={`Mudar nome do módulo ${module.title}`} onClick={() => { setRenaming(module.id); focusSoon(`#rename-module-${module.id}`); }}>
                  <span aria-hidden="true">✎</span>
                </button> : null}
                <button className="icon-button" type="button" data-move={`module-up-${module.id}`} aria-label={`Subir módulo ${module.title}`} disabled={busy || moduleIndex === 0} onClick={() => void props.onMoveModule(moduleIndex, -1)}><span aria-hidden="true">↑</span></button>
                <button className="icon-button" type="button" data-move={`module-down-${module.id}`} aria-label={`Descer módulo ${module.title}`} disabled={busy || moduleIndex === modules.length - 1} onClick={() => void props.onMoveModule(moduleIndex, 1)}><span aria-hidden="true">↓</span></button>
              </div> : null}
            </div>

            {renaming === module.id ? <div className="course-rename">
              <NameForm
                maxBytes={limits.title_bytes}
                id={`rename-module-${module.id}`}
                label={JOURNEY_COPY.moduleName}
                initial={module.title}
                submitLabel="Salvar nome"
                busy={busy}
                onCancel={() => setRenaming(null)}
                onSubmit={async (title) => {
                  const ok = await props.onRenameModule(module, title);
                  if (ok) setRenaming(null);
                  return ok;
                }}
              />
              <button className="danger-button" type="button" aria-label={`Excluir módulo ${module.title}`} onClick={() => { setRenaming(null); props.onDeleteModule(module); }}>
                {module.lessons.length === 0 ? "Excluir módulo" : `Excluir módulo e ${module.lessons.length === 1 ? "a aula dele" : `as ${module.lessons.length} aulas dele`}`}
              </button>
            </div> : null}

            {module.lessons.length === 0 ? <p className="course-lesson-empty">Nenhuma aula neste módulo ainda.</p> : <ol className="course-lessons">
              {module.lessons.map((lesson, lessonIndex) => {
                const readiness = readinessOf(lesson);
                return <li key={lesson.id} className="course-lesson" aria-current={selectedId === lesson.id ? "true" : undefined} data-testid={`lesson-${lesson.id}`}>
                  <a
                    className="course-lesson-open"
                    href={`?section=curriculo&aula=${lesson.id}`}
                    data-lesson-link={lesson.id}
                    onClick={(event) => { event.preventDefault(); props.onOpenLesson(lesson.id); }}
                  >
                    <span className="course-lesson-title">{lessonIndex + 1}. {lesson.title}</span>
                    <StatusChip readiness={readiness}/>
                  </a>
                  {!archived ? <div className="course-actions">
                    <button className="icon-button" type="button" data-move={`lesson-up-${lesson.id}`} aria-label={`Subir aula ${lesson.title}`} disabled={busy || lessonIndex === 0} onClick={() => void props.onMoveLesson(module, lessonIndex, -1)}><span aria-hidden="true">↑</span></button>
                    <button className="icon-button" type="button" data-move={`lesson-down-${lesson.id}`} aria-label={`Descer aula ${lesson.title}`} disabled={busy || lessonIndex === module.lessons.length - 1} onClick={() => void props.onMoveLesson(module, lessonIndex, 1)}><span aria-hidden="true">↓</span></button>
                  </div> : null}
                </li>;
              })}
            </ol>}

            {!archived ? addingLessonTo === module.id ? <NameForm
              maxBytes={limits.title_bytes}
              id={`new-lesson-${module.id}`}
              label={JOURNEY_COPY.lessonName}
              placeholder={JOURNEY_COPY.lessonPlaceholder}
              submitLabel={JOURNEY_COPY.createLesson}
              busy={busy}
              onCancel={() => setAddingLessonTo(null)}
              onSubmit={async (title) => {
                // Falhou? O formulário fica, com o nome digitado, e o aviso
                // no topo diz o que fazer.
                const ok = await props.onCreateLesson(module, title);
                if (ok) setAddingLessonTo(null);
                return ok;
              }}
            /> : <>
              <button
                className="secondary-button course-add"
                type="button"
                aria-label={`Nova aula em ${module.title}`}
                disabled={lessonsFull || module.lessons.length >= limits.lessons_per_module}
                aria-describedby={lessonsFull ? "course-lessons-full" : undefined}
                onClick={() => { setAddingLessonTo(module.id); focusSoon(`#new-lesson-${module.id}`); }}
              >{JOURNEY_COPY.newLesson}</button>
            </> : null}
          </article>
        </li>)}
      </ol>

      {lessonsFull ? <p className="form-note" id="course-lessons-full">{JOURNEY_COPY.limitLessons}</p> : null}
      {!archived ? addingModule ? <div className="course-module course-module-new">
        <NameForm
          maxBytes={limits.title_bytes}
          id="new-module-title"
          label={JOURNEY_COPY.moduleName}
          placeholder={JOURNEY_COPY.modulePlaceholder}
          submitLabel={JOURNEY_COPY.createModule}
          busy={busy}
          onCancel={() => setAddingModule(false)}
          onSubmit={createModule}
        />
      </div> : <>
        <button className="secondary-button course-add" type="button" disabled={modulesFull} aria-describedby={modulesFull ? "course-modules-full" : undefined} onClick={() => { setAddingModule(true); focusSoon("#new-module-title"); }}>{JOURNEY_COPY.newModule}</button>
        {modulesFull ? <p className="form-note" id="course-modules-full">{JOURNEY_COPY.limitModules}</p> : null}
      </> : null}
    </>}
  </div>;
}

function StatusChip({ readiness }: { readiness: LessonReadiness }) {
  const labels: Record<LessonReadiness["state"], { text: string; tone: string }> = {
    unknown: { text: "…", tone: "quiet" },
    missing: { text: "Falta o vídeo, PDF ou texto", tone: "todo" },
    text_only: { text: "✓ Pronta (só texto)", tone: "ok" },
    ready: { text: "✓ Pronta", tone: "ok" },
    uploading: { text: `Enviando ${readiness.progress}%`, tone: "run" },
    checking: { text: "Quase lá", tone: "run" },
    interrupted: { text: "O envio parou: escolha o arquivo de novo", tone: "bad" }
  };
  const label = labels[readiness.state];
  return <span className={`course-chip course-chip-${label.tone}`}>
    {readiness.state === "unknown" ? <span className="sr-only">Carregando o estado da aula</span> : null}
    <span aria-hidden={readiness.state === "unknown" ? "true" : undefined}>{label.text}</span>
  </span>;
}

function NameForm({
  id,
  maxBytes,
  label,
  placeholder,
  initial = "",
  submitLabel,
  busy,
  onSubmit,
  onCancel
}: {
  id: string;
  /** O limite do servidor, em bytes: acento conta dois. */
  maxBytes: number;
  label: string;
  placeholder?: string;
  initial?: string;
  submitLabel: string;
  busy: boolean;
  onSubmit: (title: string) => Promise<boolean>;
  onCancel?: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = value.trim();
    if (title.length < MIN_TITLE) {
      setError(JOURNEY_COPY.nameTooShort);
      input.current?.focus();
      return;
    }
    if (exceedsBytes(title, maxBytes)) {
      setError(JOURNEY_COPY.nameTooLong);
      input.current?.focus();
      return;
    }
    setError("");
    await onSubmit(title);
  }

  return <form className="course-name-form" onSubmit={(event) => void submit(event)} noValidate>
    <label htmlFor={id}>{label}</label>
    <input
      ref={input}
      id={id}
      name="title"
      value={value}
      placeholder={placeholder}
      maxLength={MAX_TITLE}
      autoComplete="off"
      aria-invalid={error ? "true" : undefined}
      aria-describedby={error ? `${id}-error` : undefined}
      onChange={(event) => { setValue(event.target.value); if (error) setError(""); }}
      onKeyDown={(event) => { if (event.key === "Escape" && onCancel) onCancel(); }}
    />
    {error ? <span id={`${id}-error`} className="form-error">{error}</span> : null}
    <div className="course-name-actions">
      <button className="primary-button" type="submit" disabled={busy}>{busy ? JOURNEY_COPY.saving : submitLabel}</button>
      {onCancel ? <button className="secondary-button" type="button" onClick={onCancel}>{JOURNEY_COPY.cancel}</button> : null}
    </div>
  </form>;
}

function focusSoon(selector: string) {
  window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
    document.querySelector<HTMLElement>(selector)?.focus();
  }));
}
