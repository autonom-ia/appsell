"use client";

import { useCallback, useEffect, useRef } from "react";

import type { AutosaveFailure, AutosaveState } from "@/lib/curriculum/autosave";
import type { CurriculumLesson, CurriculumModule } from "@/lib/curriculum/curriculum-api.client";
import { JOURNEY_COPY } from "@/lib/curriculum/journey-copy";
import type { LessonReadiness, LiveUpload } from "@/lib/curriculum/lesson-readiness";

import type { AssetActivity } from "./curriculum-workspace";
import { LessonAssetPanel } from "./lesson-asset-panel";
import { LessonMaterialPanel } from "./lesson-material-panel";
import { useAutosave } from "./use-autosave";

/**
 * A aula aberta (#649): nome, vídeo ou PDF, texto e materiais, tudo à vista.
 *
 * Fica montada, escondida, enquanto um arquivo dela ainda sobe: voltar para o
 * curso não pode interromper o envio.
 */

type SaveResult = { ok: true } | { ok: false; reason: AutosaveFailure };

export function LessonEditor({
  hidden,
  sellerId,
  productId,
  courseId,
  module,
  lesson,
  courseVersion,
  archived,
  readiness,
  transportBusy,
  onBack,
  onDelete,
  onSave,
  onActivityChange,
  onLiveChange,
  onCommitted
}: {
  hidden: boolean;
  sellerId: string;
  productId: string;
  courseId: string;
  module: CurriculumModule;
  lesson: CurriculumLesson;
  courseVersion: number;
  archived: boolean;
  readiness: LessonReadiness;
  transportBusy: boolean;
  onBack: () => void;
  onDelete: () => void;
  onSave: (change: { title?: string; content?: string }, options?: { keepalive?: boolean }) => Promise<SaveResult>;
  onActivityChange: (panelKey: string, value: AssetActivity) => void;
  onLiveChange: (lessonId: string, value: LiveUpload) => void;
  onCommitted: (lessonId: string) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const saveTitle = useCallback((value: string, options?: { keepalive?: boolean }) => value.trim().length < 2
    ? Promise.resolve<SaveResult>({ ok: false, reason: "invalid" })
    : onSave({ title: value.trim() }, options), [onSave]);
  const saveContent = useCallback((value: string, options?: { keepalive?: boolean }) => onSave({ content: value }, options), [onSave]);
  const title = useAutosave({ serverValue: lesson.title, save: saveTitle, disabled: archived });
  const content = useAutosave({ serverValue: lesson.content, save: saveContent, disabled: archived });

  useEffect(() => {
    if (!hidden) heading.current?.focus({ preventScroll: false });
  }, [hidden, lesson.id]);

  const committed = useCallback(() => onCommitted(lesson.id), [lesson.id, onCommitted]);
  const live = useCallback((value: LiveUpload) => onLiveChange(lesson.id, value), [lesson.id, onLiveChange]);
  const done = readiness.done;
  const deleteHelp = `lesson-delete-help-${lesson.id}`;

  return <section className="lesson-editor" hidden={hidden} aria-labelledby={`lesson-heading-${lesson.id}`}>
    <div className="lesson-editor-top">
      <button className="secondary-button lesson-back" type="button" onClick={onBack}>
        <span aria-hidden="true">←</span> {JOURNEY_COPY.back}
      </button>
      {!archived ? <button
        className="danger-button"
        type="button"
        aria-disabled={transportBusy ? "true" : undefined}
        aria-describedby={transportBusy ? deleteHelp : undefined}
        onClick={() => { if (!transportBusy) onDelete(); }}
      >{JOURNEY_COPY.deleteLesson}</button> : null}
    </div>
    {transportBusy ? <p className="form-note" id={deleteHelp}>{JOURNEY_COPY.deleteLessonBlocked}</p> : null}

    <h2 id={`lesson-heading-${lesson.id}`} ref={heading} tabIndex={-1} className="lesson-editor-heading">
      <span className="eyebrow">{module.title}</span>
      <span className="sr-only">Aula: </span>{title.state.value || lesson.title}
    </h2>

    <div className="lesson-field">
      <label htmlFor={`lesson-title-${lesson.id}`}>Nome da aula</label>
      <input
        id={`lesson-title-${lesson.id}`}
        value={title.state.value}
        maxLength={160}
        disabled={archived}
        autoComplete="off"
        aria-invalid={title.state.reason === "invalid" ? "true" : undefined}
        aria-describedby={`lesson-title-state-${lesson.id}`}
        onChange={(event) => title.onChange(event.target.value)}
        onBlur={() => void title.flush()}
      />
      <SaveState id={`lesson-title-state-${lesson.id}`} state={title.state} onRetry={title.retry}/>
    </div>

    <section className="lesson-block" aria-labelledby={`lesson-main-${lesson.id}`}>
      <div className="lesson-block-head">
        <h3 id={`lesson-main-${lesson.id}`}>{JOURNEY_COPY.mainTitle}</h3>
        <span className="lesson-block-hint">{JOURNEY_COPY.mainHint}</span>
      </div>
      <LessonAssetPanel
        contentRole="course_content"
        sellerId={sellerId}
        productId={productId}
        courseId={courseId}
        moduleId={module.id}
        lessonId={lesson.id}
        lessonTitle={lesson.title}
        courseVersion={courseVersion}
        archived={archived}
        onActivityChange={onActivityChange}
        onLiveChange={live}
        onCommitted={committed}
      />
    </section>

    <section className="lesson-block" aria-labelledby={`lesson-text-${lesson.id}`}>
      <div className="lesson-block-head">
        <h3 id={`lesson-text-${lesson.id}`}>{JOURNEY_COPY.textTitle}</h3>
        <span className="lesson-block-hint">{JOURNEY_COPY.optional}</span>
      </div>
      <label className="sr-only" htmlFor={`lesson-content-${lesson.id}`}>{JOURNEY_COPY.textTitle}</label>
      <textarea
        id={`lesson-content-${lesson.id}`}
        className="lesson-text"
        value={content.state.value}
        maxLength={8192}
        disabled={archived}
        placeholder={JOURNEY_COPY.textPlaceholder}
        aria-describedby={`lesson-content-help-${lesson.id} lesson-content-state-${lesson.id}`}
        onChange={(event) => content.onChange(event.target.value)}
        onBlur={() => void content.flush()}
      />
      <p className="field-help" id={`lesson-content-help-${lesson.id}`}>{JOURNEY_COPY.textHelp}</p>
      <SaveState id={`lesson-content-state-${lesson.id}`} state={content.state} onRetry={content.retry}/>
    </section>

    <section className="lesson-block" aria-labelledby={`lesson-materials-${lesson.id}`}>
      <div className="lesson-block-head">
        <h3 id={`lesson-materials-${lesson.id}`}>{JOURNEY_COPY.materialsTitle}</h3>
        <span className="lesson-block-hint">{JOURNEY_COPY.optional}</span>
      </div>
      <LessonAssetPanel
        contentRole="support_material"
        sellerId={sellerId}
        productId={productId}
        courseId={courseId}
        moduleId={module.id}
        lessonId={lesson.id}
        lessonTitle={lesson.title}
        courseVersion={courseVersion}
        archived={archived}
        onActivityChange={onActivityChange}
        onCommitted={committed}
      >
        <LessonMaterialPanel
          sellerId={sellerId}
          productId={productId}
          courseId={courseId}
          moduleId={module.id}
          lessonId={lesson.id}
          lessonTitle={lesson.title}
          courseVersion={courseVersion}
          archived={archived}
          onCommitted={committed}
        />
      </LessonAssetPanel>
    </section>

    {done ? <div className="lesson-editor-foot">
      <button className="primary-button" type="button" onClick={onBack}>{JOURNEY_COPY.backReady}</button>
    </div> : null}
  </section>;
}

function SaveState({ id, state, onRetry }: { id: string; state: AutosaveState; onRetry: () => void }) {
  if (state.status === "failed") {
    const messages: Record<AutosaveFailure, string> = {
      invalid: JOURNEY_COPY.nameTooShort,
      offline: JOURNEY_COPY.offline,
      forbidden: JOURNEY_COPY.noPermission,
      conflict: JOURNEY_COPY.changedElsewhere,
      error: JOURNEY_COPY.saveFailed
    };
    const message = messages[state.reason ?? "error"];
    return <p className="save-state save-state-failed" id={id} role="alert">
      <span>{message}</span>
      {state.reason !== "invalid" ? <button className="secondary-button" type="button" onClick={onRetry}>{JOURNEY_COPY.notSaved}</button> : null}
    </p>;
  }
  const text = state.status === "saving" ? JOURNEY_COPY.saving : state.status === "saved" ? JOURNEY_COPY.saved : "";
  return <p className={`save-state save-state-${state.status}`} id={id} role="status" aria-live="polite">{text}</p>;
}
