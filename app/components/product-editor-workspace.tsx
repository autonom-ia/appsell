"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, MouseEvent, useCallback, useEffect, useRef, useState } from "react";
import { AccessibleDialog } from "./accessible-dialog";
import { CurriculumWorkspace } from "./curriculum-workspace";
import { allowUnsavedNavigation, useUnsavedChanges } from "./use-unsaved-changes";

type EditorSection = "produto" | "curriculo" | "oferta" | "preview" | "publicar";
type LoadState = "loading" | "ready" | "unauthorized" | "forbidden" | "not_found" | "unavailable" | "error";
type SaveState = "idle" | "saving" | "saved" | "saved_with_changes" | "conflict" | "rebasing" | "rebased" | "invalid" | "forbidden" | "unavailable" | "error";
type PreviewState = "idle" | "loading" | "ready" | "unavailable" | "error";
type PublishState = "idle" | "publishing" | "published" | "conflict" | "forbidden" | "unavailable" | "error";

interface CourseDetail {
  id: string;
  title: string;
  status: string;
  draft_version: number;
}

interface OfferDetail {
  id?: string | null;
  title: string;
  summary: string;
  status: string;
  draft_version: number;
}

interface ReadinessItem {
  key?: string;
  label: string;
  ready: boolean;
  detail?: string;
}

interface ProductDetail {
  id: string;
  seller_organization_id?: string;
  slug: string;
  title: string;
  description: string;
  status: "draft" | "published" | "unpublished" | "archived";
  draft_version: number;
  course: CourseDetail;
  courses: CourseDetail[];
  offer: OfferDetail;
  publication?: { id?: string; version: number; published_at?: string } | null;
  has_unpublished_changes?: boolean;
  readiness?: { can_publish: boolean; has_unpublished_changes?: boolean; items: ReadinessItem[] } | null;
}

interface EditorPayload {
  product?: ProductDetail;
  aggregate?: ProductDetail;
  course?: CourseDetail;
  offer?: OfferDetail | null;
  publication?: ProductDetail["publication"];
  latest_publication?: ProductDetail["publication"];
  csrf?: { update?: string; publish?: string };
  readiness?: { can_publish: boolean; has_unpublished_changes?: boolean; items: Array<ReadinessItem | string> };
  can_publish?: boolean;
  readiness_violations?: Array<string | { key?: string; label?: string; detail?: string }>;
}

interface EditorFields {
  slug: string;
  product_title: string;
  product_description: string;
  course_title: string;
  offer_title: string;
  offer_summary: string;
}

interface PrivatePreview {
  seller?: { id?: string; display_name?: string; slug?: string };
  product?: { id?: string; title?: string; slug?: string; description?: string; status?: string };
  course?: { title?: string };
  offer?: { title?: string; summary?: string; status?: string } | null;
  draft_version?: number;
  version?: number;
}

interface PrivatePreviewPayload extends PrivatePreview {
  preview?: PrivatePreview | boolean;
  snapshot?: { product?: PrivatePreview["product"]; offer?: PrivatePreview["offer"]; course?: PrivatePreview["course"]; courses?: Array<NonNullable<PrivatePreview["course"]>> };
  versions?: { product?: number; course?: number; offer?: number };
}

const editorSections: Array<{ id: EditorSection; label: string; hint: string }> = [
  { id: "produto", label: "Produto", hint: "Identidade e curso" },
  { id: "curriculo", label: "Currículo", hint: "Módulos e aulas" },
  { id: "oferta", label: "Oferta", hint: "Termos informativos" },
  { id: "preview", label: "Preview", hint: "Revisão privada" },
  { id: "publicar", label: "Publicar", hint: "Snapshot versionado" }
];

export function ProductEditorWorkspace({ sellerId, productId }: { sellerId: string; productId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedSection = searchParams.get("section");
  const section: EditorSection = editorSections.some((item) => item.id === requestedSection) ? requestedSection as EditorSection : "produto";
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [csrf, setCsrf] = useState<{ update: string; publish: string }>({ update: "", publish: "" });
  const [fields, setFields] = useState<EditorFields>(emptyFields);
  const fieldsRef = useRef(fields);
  const [savedFields, setSavedFields] = useState<EditorFields>(emptyFields);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [invalidField, setInvalidField] = useState("");
  const [previewState, setPreviewState] = useState<PreviewState>("idle");
  const [preview, setPreview] = useState<PrivatePreview | null>(null);
  const [previewRetry, setPreviewRetry] = useState(0);
  const [publishState, setPublishState] = useState<PublishState>("idle");
  const [publishDialogOpen, setPublishDialogOpen] = useState(false);
  const [leaveDialogOpen, setLeaveDialogOpen] = useState(false);
  const [curriculumDirty, setCurriculumDirty] = useState(false);
  const updateIdempotencyKey = useRef<string | null>(null);
  const publishIdempotencyKey = useRef<string | null>(null);
  const offerId = useRef<string | null>(null);
  const formElement = useRef<HTMLFormElement>(null);
  const publishCancelButton = useRef<HTMLButtonElement>(null);
  const leaveCancelButton = useRef<HTMLButtonElement>(null);
  const publishTriggerButton = useRef<HTMLButtonElement>(null);
  const listLink = useRef<HTMLAnchorElement>(null);
  const dirty = !sameFields(fields, savedFields);
  const editorDirty = dirty || curriculumDirty;

  useUnsavedChanges(editorDirty);
  const handleCurriculumDirty = useCallback((nextDirty: boolean) => setCurriculumDirty(nextDirty), []);

  const loadDetail = useCallback(async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(`/api/sellers/${sellerId}/products/${productId}`, { cache: "no-store", signal: controller.signal });
      if (response.status === 401) { setLoadState("unauthorized"); return false; }
      if (response.status === 403) { setLoadState("forbidden"); return false; }
      if (response.status === 404) { setLoadState("not_found"); return false; }
      if (response.status === 503) { setLoadState("unavailable"); return false; }
      if (!response.ok) { setLoadState("error"); return false; }
      const payload = await response.json() as EditorPayload & ProductDetail;
      const nextDetail = normalizeEditorPayload(payload);
      if (!nextDetail) { setLoadState("error"); return false; }
      const nextFields = fieldsFromDetail(nextDetail);
      offerId.current = nextDetail.offer.id ?? crypto.randomUUID();
      setDetail({ ...nextDetail, readiness: payload.readiness ?? nextDetail.readiness });
      setCsrf({ update: payload.csrf?.update ?? readActionCsrf("update"), publish: payload.csrf?.publish ?? readActionCsrf("publish") });
      setFields(nextFields);
      setSavedFields(nextFields);
      setLoadState("ready");
      return true;
    } catch {
      setLoadState("unavailable");
      return false;
    } finally {
      clearTimeout(timeout);
    }
  }, [productId, sellerId]);
  const handleCurriculumCommitted = useCallback(() => { void loadDetail(); }, [loadDetail]);

  useEffect(() => {
    const start = window.setTimeout(() => void loadDetail(), 0);
    return () => window.clearTimeout(start);
  }, [loadDetail]);

  useEffect(() => {
    if (section !== "preview" || loadState !== "ready" || dirty) return;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    void fetch(`/api/sellers/${sellerId}/products/${productId}/preview`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (response.status === 503) return setPreviewState("unavailable");
        if (!response.ok) return setPreviewState("error");
        const payload = await response.json() as PrivatePreviewPayload;
        const normalized = normalizePrivatePreview(payload);
        if (!normalized) return setPreviewState("error");
        setPreview(normalized);
        setPreviewState("ready");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setPreviewState("unavailable");
      })
      .finally(() => clearTimeout(timeout));
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [dirty, loadState, previewRetry, productId, section, sellerId]);

  function updateField<K extends keyof EditorFields>(field: K, value: EditorFields[K]) {
    updateIdempotencyKey.current = null;
    publishIdempotencyKey.current = null;
    setSaveState("idle");
    setPublishState("idle");
    setInvalidField("");
    setFields((current) => {
      const next = { ...current, [field]: value };
      fieldsRef.current = next;
      return next;
    });
  }

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail || saveState === "saving") return;
    const submitted = fields;
    const submittedVersions = versionsFromDetail(detail);
    const key = updateIdempotencyKey.current ?? crypto.randomUUID();
    updateIdempotencyKey.current = key;
    const data = new FormData();
    data.set("_csrf", csrf.update);
    data.set("idempotency_key", key);
    data.set("expected_product_version", String(submittedVersions.product));
    data.set("expected_course_version", String(submittedVersions.course));
    data.set("expected_offer_version", String(submittedVersions.offer));
    data.set("course_id", detail.course.id);
    data.set("offer_id", offerId.current ?? crypto.randomUUID());
    for (const [name, value] of Object.entries(submitted)) data.set(name, value);
    setSaveState("saving");
    setInvalidField("");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await fetch(`/api/sellers/${sellerId}/products/${productId}`, { method: "PATCH", body: data, signal: controller.signal });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) setSaveState("forbidden");
        else if (response.status === 409) setSaveState("conflict");
        else if (response.status === 422) {
          const payload = await response.json().catch(() => null) as { field?: string } | null;
          const field = payload?.field ?? "form";
          setInvalidField(field);
          setSaveState("invalid");
          const input = formElement.current?.elements.namedItem(field);
          if (input instanceof HTMLElement) input.focus();
        } else if (response.status === 503) setSaveState("unavailable");
        else setSaveState("error");
        return;
      }
      const refreshResponse = await fetch(`/api/sellers/${sellerId}/products/${productId}`, { cache: "no-store", signal: controller.signal });
      if (!refreshResponse.ok) return setSaveState("unavailable");
      const payload = await refreshResponse.json() as EditorPayload & ProductDetail;
      const updated = normalizeEditorPayload(payload);
      if (!updated) return setSaveState("error");
      updateIdempotencyKey.current = null;
      setDetail({ ...updated, readiness: payload.readiness ?? updated.readiness });
      setCsrf((current) => ({ update: payload.csrf?.update ?? current.update, publish: payload.csrf?.publish ?? current.publish }));
      setSavedFields(fieldsFromDetail(updated));
      if (sameFields(fieldsRef.current, submitted)) {
        const serverFields = fieldsFromDetail(updated);
        setFields(serverFields);
        setSavedFields(serverFields);
        setSaveState("saved");
      } else {
        setSaveState("saved_with_changes");
      }
      setPreview(null);
      setPreviewState("idle");
    } catch {
      setSaveState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  async function rebaseFromServer() {
    if (saveState === "rebasing") return;
    setSaveState("rebasing");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(`/api/sellers/${sellerId}/products/${productId}`, { cache: "no-store", signal: controller.signal });
      if (!response.ok) {
        setSaveState("unavailable");
        return;
      }
      const payload = await response.json() as EditorPayload & ProductDetail;
      const latest = normalizeEditorPayload(payload);
      if (!latest) {
        setSaveState("error");
        return;
      }
      setDetail(latest);
      setSavedFields(fieldsFromDetail(latest));
      setCsrf((current) => ({ update: payload.csrf?.update ?? current.update, publish: payload.csrf?.publish ?? current.publish }));
      offerId.current = latest.offer.id ?? offerId.current ?? crypto.randomUUID();
      updateIdempotencyKey.current = null;
      setInvalidField("");
      setSaveState("rebased");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") setSaveState("unavailable");
      else setSaveState("error");
    } finally {
      clearTimeout(timeout);
    }
  }

  async function publish() {
    const hasChanges = detail?.has_unpublished_changes ?? detail?.readiness?.has_unpublished_changes;
    if (!detail || dirty || publishState === "publishing" || detail.readiness?.can_publish !== true || (detail.publication && hasChanges !== true)) return;
    setPublishDialogOpen(false);
    setPublishState("publishing");
    const versions = versionsFromDetail(detail);
    const key = publishIdempotencyKey.current ?? crypto.randomUUID();
    publishIdempotencyKey.current = key;
    const data = new FormData();
    data.set("_csrf", csrf.publish);
    data.set("idempotency_key", key);
    data.set("expected_product_version", String(versions.product));
    data.set("expected_offer_version", String(versions.offer));
    data.set("offer_id", offerId.current ?? crypto.randomUUID());
    data.set("course_versions", JSON.stringify(detail.courses.map((course) => ({ id: course.id, version: course.draft_version }))));
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 12_000);
    try {
      const response = await fetch(`/api/sellers/${sellerId}/products/${productId}/publications`, { method: "POST", body: data, signal: controller.signal });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) setPublishState("forbidden");
        else if (response.status === 409 || response.status === 422) {
          publishIdempotencyKey.current = null;
          setPublishState("conflict");
        }
        else if (response.status === 503) setPublishState("unavailable");
        else setPublishState("error");
        return;
      }
      const payload = await response.json() as EditorPayload & ProductDetail;
      const updated = normalizeEditorPayload(payload);
      if (updated) {
        setDetail({ ...updated, readiness: payload.readiness ?? updated.readiness });
        const serverFields = fieldsFromDetail(updated);
        setFields(serverFields);
        setSavedFields(serverFields);
      } else {
        const refreshed = await loadDetail();
        if (!refreshed) {
          setPublishState("unavailable");
          return;
        }
      }
      publishIdempotencyKey.current = null;
      setPublishState("published");
    } catch {
      setPublishState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  function requestListNavigation(event: MouseEvent<HTMLAnchorElement>) {
    if (!editorDirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    setLeaveDialogOpen(true);
  }

  function requestSectionNavigation(event: MouseEvent<HTMLAnchorElement>, target: EditorSection) {
    if (target === section || !editorDirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!window.confirm("Há alterações não salvas nesta etapa. Descartar e continuar?")) {
      event.preventDefault();
      return;
    }
    if (dirty) {
      setFields(savedFields);
      fieldsRef.current = savedFields;
      setSaveState("idle");
      setInvalidField("");
    }
    setCurriculumDirty(false);
  }

  function closePublishDialog() {
    setPublishDialogOpen(false);
    queueMicrotask(() => publishTriggerButton.current?.focus());
  }

  function closeLeaveDialog() {
    setLeaveDialogOpen(false);
    queueMicrotask(() => listLink.current?.focus());
  }

  if (loadState !== "ready" || !detail) {
    return <EditorLoadState state={loadState} sellerId={sellerId} onRetry={() => { setLoadState("loading"); void loadDetail(); }}/>;
  }

  const readiness = detail.readiness;
  const hasUnpublishedChanges = detail.has_unpublished_changes ?? readiness?.has_unpublished_changes ?? !detail.publication;
  const canPublish = readiness?.can_publish === true && hasUnpublishedChanges && !dirty && publishState !== "publishing";
  const publicationVersion = detail.publication?.version;

  return (
    <section className="product-editor" aria-labelledby="product-editor-title">
      <header className="editor-header">
        <div className="editor-breadcrumb"><Link ref={listLink} href={`/products?seller=${encodeURIComponent(sellerId)}`} onClick={requestListNavigation}>Produtos</Link><span aria-hidden="true">/</span><span aria-current="page">{detail.title}</span></div>
        <div className="editor-title-row">
          <div><p className="eyebrow">Tenant explícito · {shortId(sellerId)}</p><h1 id="product-editor-title">{detail.title}</h1><p>@{detail.slug} · curso {detail.course.title}</p></div>
          <span className={`status status-${detail.status}`}>{detail.status === "published" ? `Publicado${publicationVersion ? ` v${publicationVersion}` : ""}` : detail.status === "draft" ? "Rascunho privado" : detail.status === "unpublished" ? "Despublicado" : "Arquivado"}</span>
        </div>
        <div className={`save-indicator save-${saveState}`}>
          <span role="status" aria-live="polite" aria-atomic="true">{saveMessage(saveState, dirty)}</span>
          {saveState === "conflict" || saveState === "rebasing" ? <button className="secondary-button" type="button" disabled={saveState === "rebasing"} onClick={() => void rebaseFromServer()}>{saveState === "rebasing" ? "Recarregando…" : "Recarregar versão do servidor"}</button> : null}
        </div>
      </header>

      <nav className="editor-section-nav" aria-label="Etapas do produto">
        {editorSections.map((item) => {
          const active = section === item.id;
          return <Link key={item.id} href={`?section=${item.id}`} replace scroll={false} className={active ? "active" : ""} aria-current={active ? "step" : undefined} onClick={(event) => requestSectionNavigation(event, item.id)}><strong>{item.label}</strong><span>{item.hint}</span></Link>;
        })}
      </nav>

      {section === "curriculo" ? <CurriculumWorkspace sellerId={sellerId} productId={productId} courseId={detail.course.id} courseTitle={detail.course.title} archived={detail.status === "archived"} onDirtyChange={handleCurriculumDirty} onCommitted={handleCurriculumCommitted}/> : (
      <form ref={formElement} className="editor-form" onSubmit={saveDraft}>
        {section === "produto" ? (
          <fieldset disabled={detail.status === "archived"}>
            <legend>Identidade do produto</legend>
            <p className="section-intro">Edite o rascunho deste tenant. Uma publicação existente permanece estável até você publicar outra versão.</p>
            <EditorField label="Nome do produto" name="product_title" value={fields.product_title} onChange={(value) => updateField("product_title", value)} minLength={2} maxLength={160} required invalid={invalidField === "product_title"}/>
            <EditorField label="Endereço do produto" name="slug" value={fields.slug} onChange={(value) => updateField("slug", value)} minLength={3} maxLength={63} required invalid={invalidField === "slug"} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" help="Somente letras minúsculas, números e hífens."/>
            <EditorField label="Descrição curta" name="product_description" value={fields.product_description} onChange={(value) => updateField("product_description", value)} maxLength={2000} multiline invalid={invalidField === "product_description"}/>
            <EditorField label="Nome do curso inicial" name="course_title" value={fields.course_title} onChange={(value) => updateField("course_title", value)} minLength={2} maxLength={160} required invalid={invalidField === "course_title"}/>
          </fieldset>
        ) : null}

        {section === "oferta" ? (
          <fieldset disabled={detail.status === "archived"}>
            <legend>Oferta informativa</legend>
            <div className="billing-boundary" role="note"><strong>Cobrança não conectada</strong><p>Estes termos ajudam a apresentar o produto. Publicar não cria checkout, preço cobrado ou acesso pago.</p></div>
            <EditorField label="Nome da oferta" name="offer_title" value={fields.offer_title} onChange={(value) => updateField("offer_title", value)} minLength={2} maxLength={120} required invalid={invalidField === "offer_title"}/>
            <EditorField label="Resumo da oferta" name="offer_summary" value={fields.offer_summary} onChange={(value) => updateField("offer_summary", value)} maxLength={500} multiline invalid={invalidField === "offer_summary"} help="Não informe preço ou promessa de cobrança nesta fase."/>
          </fieldset>
        ) : null}

        {section === "preview" ? <PrivatePreviewPanel state={previewState} preview={preview} detail={detail} dirty={dirty} onReload={() => { setPreviewState("idle"); setPreview(null); setPreviewRetry((value) => value + 1); }}/> : null}

        {section === "publicar" ? (
          <section className="publish-panel" aria-labelledby="publish-panel-title">
            <p className="eyebrow">Validação server-side</p><h2 id="publish-panel-title">Preparar publicação</h2>
            <p>A publicação cria um snapshot versionado com produto, oferta, módulos e aulas de texto. Cobrança e checkout continuam desativados.</p>
            {readiness ? (
              <ul className="readiness-list">{readiness.items.map((item, index) => <li key={item.key ?? `${item.label}-${index}`} className={item.ready ? "ready" : "blocked"}><span aria-hidden="true">{item.ready ? "✓" : "!"}</span><div><strong>{item.label}</strong>{item.detail ? <p>{item.detail}</p> : null}</div><span className="sr-only">{item.ready ? "Pronto" : "Pendente"}</span></li>)}</ul>
            ) : <p className="form-note" role="status">A API ainda não retornou a validação de publicação. Nada será publicado.</p>}
            {dirty ? <p className="form-note" role="status">Salve as alterações antes de publicar.</p> : null}
            {!dirty && detail.publication && !hasUnpublishedChanges ? <p className="form-note" role="status">Nenhuma mudança desde o snapshot publicado. Edite e salve o rascunho antes de criar outra versão.</p> : null}
            <div className="publish-action"><button ref={publishTriggerButton} className="primary-button" type="button" disabled={!canPublish} onClick={() => setPublishDialogOpen(true)}><span>{publishState === "publishing" ? "Publicando…" : `Publicar${publicationVersion ? ` nova versão após v${publicationVersion}` : " primeira versão"}`}</span></button></div>
            <div className="form-feedback" aria-live="polite" aria-atomic="true">
              {publishState === "published" ? <p className="form-success">Versão publicada com sucesso. O rascunho futuro continuará separado.</p> : null}
              {publishState === "conflict" ? <p className="form-error">O produto mudou ou não está pronto. Recarregue os dados antes de publicar.</p> : null}
              {publishState === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente.</p> : null}
              {publishState === "unavailable" ? <p className="form-note">A API não respondeu. A chave desta publicação foi preservada para uma tentativa segura.</p> : null}
              {publishState === "error" ? <p className="form-error">Não foi possível publicar. Revise a validação e tente novamente.</p> : null}
            </div>
          </section>
        ) : null}

        {(section === "produto" || section === "oferta") ? (
          <div className="editor-save-bar">
            <span aria-live="polite">{dirty ? "Alterações não salvas" : "Rascunho salvo"}</span>
            <button className="primary-button" type="submit" disabled={!dirty || saveState === "saving" || detail.status === "archived"}>{saveState === "saving" ? "Salvando…" : "Salvar rascunho"}</button>
          </div>
        ) : null}
      </form>
      )}

      {publishDialogOpen ? (
        <AccessibleDialog titleId="publish-dialog-title" descriptionId="publish-dialog-description" initialFocusRef={publishCancelButton} returnFocusRef={publishTriggerButton} onClose={closePublishDialog}>
            <p className="eyebrow">Confirmação de publicação</p><h2 id="publish-dialog-title">Publicar este snapshot?</h2>
            <p id="publish-dialog-description">Produto, oferta, módulos e aulas de texto ficarão disponíveis na versão publicada. Nenhuma cobrança, checkout ou entitlement será criado.</p>
            <div className="dialog-actions"><button ref={publishCancelButton} className="secondary-button" type="button" onClick={closePublishDialog}>Revisar novamente</button><button className="primary-button" type="button" onClick={() => void publish()}>Publicar snapshot</button></div>
        </AccessibleDialog>
      ) : null}

      {leaveDialogOpen ? (
        <AccessibleDialog titleId="editor-leave-title" descriptionId="editor-leave-description" initialFocusRef={leaveCancelButton} returnFocusRef={listLink} onClose={closeLeaveDialog}>
            <p className="eyebrow">Alterações não salvas</p><h2 id="editor-leave-title">Voltar para produtos?</h2><p id="editor-leave-description">As alterações feitas neste dispositivo serão descartadas.</p>
            <div className="dialog-actions"><button ref={leaveCancelButton} className="secondary-button" type="button" onClick={closeLeaveDialog}>Continuar editando</button><button className="danger-button" type="button" onClick={() => { allowUnsavedNavigation(); router.push(`/products?seller=${encodeURIComponent(sellerId)}`); }}>Descartar e voltar</button></div>
        </AccessibleDialog>
      ) : null}
    </section>
  );
}

function EditorLoadState({ state, sellerId, onRetry }: { state: LoadState; sellerId: string; onRetry: () => void }) {
  const messages: Record<Exclude<LoadState, "ready">, string> = {
    loading: "Carregando produto…",
    unauthorized: "Sua sessão expirou. Entre novamente.",
    forbidden: "Você não possui permissão ativa neste tenant.",
    not_found: "Produto não encontrado neste tenant.",
    unavailable: "A API está indisponível. Nenhum dado demonstrativo substituiu o produto real.",
    error: "Não foi possível carregar o editor."
  };
  return <section className="editor-load-state" aria-live="polite"><p className="eyebrow">Editor protegido</p><h1>{messages[state === "ready" ? "error" : state]}</h1><div className="error-actions"><Link className="secondary-button" href={`/products?seller=${encodeURIComponent(sellerId)}`}>Voltar aos produtos</Link>{state === "unavailable" || state === "error" ? <button className="primary-button" type="button" onClick={onRetry}>Tentar novamente</button> : null}{state === "unauthorized" ? <a className="primary-button" href="/auth/login">Entrar novamente</a> : null}</div></section>;
}

function EditorField({ label, name, value, onChange, multiline = false, invalid = false, help, ...inputProps }: { label: string; name: keyof EditorFields; value: string; onChange: (value: string) => void; multiline?: boolean; invalid?: boolean; help?: string; minLength?: number; maxLength?: number; required?: boolean; pattern?: string }) {
  const helpId = `${name}-help`;
  const errorId = `${name}-error`;
  const describedBy = [help ? helpId : "", invalid ? errorId : ""].filter(Boolean).join(" ") || undefined;
  return <label className="editor-field">{label}{multiline ? <textarea name={name} value={value} onChange={(event) => onChange(event.target.value)} rows={5} autoComplete="off" aria-invalid={invalid} aria-describedby={describedBy} {...inputProps}/> : <input name={name} value={value} onChange={(event) => onChange(event.target.value)} autoComplete="off" spellCheck={name === "slug" ? false : undefined} autoCapitalize={name === "slug" ? "none" : undefined} aria-invalid={invalid} aria-describedby={describedBy} {...inputProps}/>} {help ? <span id={helpId}>{help}</span> : null}{invalid ? <span id={errorId} className="form-error" role="alert">Revise este campo e tente novamente.</span> : null}</label>;
}

function PrivatePreviewPanel({ state, preview, detail, dirty, onReload }: { state: PreviewState; preview: PrivatePreview | null; detail: ProductDetail; dirty: boolean; onReload: () => void }) {
  if (dirty) return <section className="private-preview preview-blocked"><span className="private-badge">Prévia privada</span><h2>Salve para atualizar a prévia</h2><p>Esta tela nunca mistura alterações locais ainda não confirmadas com o draft persistido.</p></section>;
  if (state === "loading" || state === "idle") return <p className="empty-state" role="status">Montando prévia privada…</p>;
  if (state === "unavailable" || state === "error") return <section className="private-preview preview-blocked"><span className="private-badge">Prévia privada</span><h2>Prévia indisponível</h2><p>O draft permanece protegido. Tente carregar novamente.</p><button className="secondary-button" type="button" onClick={onReload}>Tentar novamente</button></section>;
  const product = preview?.product;
  const offer = preview?.offer;
  return <section className="private-preview" aria-labelledby="private-preview-title"><div className="preview-context"><span className="private-badge">Prévia privada</span><span>Tenant {preview?.seller?.display_name ?? shortId(detail.seller_organization_id ?? "")}</span><span>Draft v{preview?.draft_version ?? preview?.version ?? detail.draft_version}</span></div><div className="preview-hero"><p className="eyebrow">Ainda não publicado</p><h2 id="private-preview-title">{product?.title ?? detail.title}</h2><p>{product?.description || detail.description || "Descrição ainda não informada."}</p></div><div className="preview-course"><span>Curso incluído</span><strong>{preview?.course?.title ?? detail.course.title}</strong></div><div className="preview-offer"><span>Oferta informativa</span><strong>{offer?.title || detail.offer.title || "Oferta ainda não configurada"}</strong><p>{offer?.summary || detail.offer.summary || "Configure e salve a oferta antes de publicar."}</p><button type="button" disabled>Compra indisponível nesta prévia</button></div></section>;
}

function normalizeEditorPayload(payload: EditorPayload & Partial<ProductDetail>): ProductDetail | null {
  const raw = (payload.aggregate ?? payload.product ?? payload) as Partial<ProductDetail>;
  const baseReadiness = normalizeReadinessValue(raw.readiness ?? payload.readiness) ?? normalizeReadiness(payload.can_publish, payload.readiness_violations);
  const hasUnpublishedChanges = raw.has_unpublished_changes ?? baseReadiness?.has_unpublished_changes;
  const readiness = baseReadiness ? { ...baseReadiness, has_unpublished_changes: hasUnpublishedChanges } : null;
  return normalizeDetail({
    ...raw,
    course: raw.course ?? raw.courses?.[0] ?? payload.course,
    offer: raw.offer ?? payload.offer ?? undefined,
    publication: raw.publication ?? payload.publication ?? payload.latest_publication ?? null,
    readiness
  });
}

function normalizeReadinessValue(value: EditorPayload["readiness"] | ProductDetail["readiness"] | null | undefined): ProductDetail["readiness"] {
  if (!value || typeof value.can_publish !== "boolean") return null;
  const items = (value.items ?? []).map((item, index) => typeof item === "string"
    ? { key: `readiness-${index}`, label: item, ready: false }
    : { key: item.key ?? `readiness-${index}`, label: item.label || "Pendência de publicação", ready: item.ready === true, detail: item.detail });
  if (!items.length) items.push({ key: "server-readiness", label: value.can_publish ? "Validação do servidor concluída" : "Publicação bloqueada pelo servidor", ready: value.can_publish });
  return { can_publish: value.can_publish, has_unpublished_changes: value.has_unpublished_changes, items };
}

function normalizeReadiness(canPublish?: boolean, violations?: EditorPayload["readiness_violations"]): ProductDetail["readiness"] {
  if (typeof canPublish !== "boolean") return null;
  const items = (violations ?? []).map((violation, index) => typeof violation === "string"
    ? { key: `violation-${index}`, label: violation, ready: false }
    : { key: violation.key ?? `violation-${index}`, label: violation.label ?? "Pendência de publicação", detail: violation.detail, ready: false });
  if (!items.length) items.push({ key: "server-readiness", label: canPublish ? "Validação do servidor concluída" : "Publicação bloqueada pelo servidor", ready: canPublish });
  return { can_publish: canPublish, items };
}

function normalizePrivatePreview(payload: PrivatePreviewPayload): PrivatePreview | null {
  if (payload.snapshot) {
    return {
      seller: payload.seller,
      product: payload.snapshot.product,
      course: payload.snapshot.course ?? payload.snapshot.courses?.[0],
      offer: payload.snapshot.offer,
      draft_version: payload.versions?.product ?? payload.draft_version,
      version: payload.version
    };
  }
  if (payload.preview && typeof payload.preview === "object") return payload.preview;
  if (payload.product) return payload;
  return null;
}

function normalizeDetail(value: Partial<ProductDetail> | null | undefined): ProductDetail | null {
  if (!value?.id || !value.slug || !value.title || !value.course) return null;
  const course = value.course;
  const courses = value.courses?.length ? value.courses : [course];
  const offer = value.offer ?? { id: null, title: "", summary: "", status: "draft", draft_version: 0 };
  const productVersion = (value as Partial<ProductDetail> & { version?: number }).draft_version ?? (value as Partial<ProductDetail> & { version?: number }).version ?? 1;
  const courseVersion = course.draft_version ?? (course as CourseDetail & { version?: number }).version ?? 1;
  const offerVersion = offer.draft_version ?? (offer as OfferDetail & { version?: number }).version ?? 0;
  return {
    id: value.id,
    seller_organization_id: value.seller_organization_id,
    slug: value.slug,
    title: value.title,
    description: value.description ?? "",
    status: value.status ?? "draft",
    draft_version: productVersion,
    course: { id: course.id, title: course.title ?? "", status: course.status ?? "draft", draft_version: courseVersion },
    courses: courses.map((item) => ({ id: item.id, title: item.title ?? "", status: item.status ?? "draft", draft_version: item.draft_version ?? 1 })),
    offer: { id: offer.id, title: offer.title ?? "", summary: offer.summary ?? "", status: offer.status ?? "draft", draft_version: offerVersion },
    publication: value.publication ?? null,
    has_unpublished_changes: value.has_unpublished_changes ?? value.readiness?.has_unpublished_changes,
    readiness: normalizeReadinessValue(value.readiness)
  };
}

function fieldsFromDetail(detail: ProductDetail): EditorFields {
  return { slug: detail.slug, product_title: detail.title, product_description: detail.description, course_title: detail.course.title, offer_title: detail.offer.title, offer_summary: detail.offer.summary };
}

function versionsFromDetail(detail: ProductDetail) {
  return { product: detail.draft_version, course: detail.course.draft_version, offer: detail.offer.draft_version };
}

function sameFields(left: EditorFields, right: EditorFields) {
  return left.slug === right.slug && left.product_title === right.product_title && left.product_description === right.product_description && left.course_title === right.course_title && left.offer_title === right.offer_title && left.offer_summary === right.offer_summary;
}

function saveMessage(state: SaveState, dirty: boolean) {
  if (state === "saving") return "Salvando rascunho…";
  if (state === "saved") return "Rascunho salvo com segurança.";
  if (state === "saved_with_changes") return "Último envio salvo; há alterações mais recentes neste dispositivo.";
  if (state === "conflict") return "Conflito de versão. Suas alterações foram preservadas; recarregue antes de tentar novamente.";
  if (state === "rebasing") return "Recarregando a versão atual do servidor…";
  if (state === "rebased") return "Versão atualizada; suas alterações locais continuam prontas para novo envio.";
  if (state === "invalid") return "Revise o campo indicado. Os demais campos foram preservados.";
  if (state === "forbidden") return "Sua sessão ou permissão mudou. Entre novamente.";
  if (state === "unavailable") return "A API não confirmou o estado final. Seus campos foram preservados; recarregue antes de outra tentativa.";
  if (state === "error") return "Não foi possível salvar. Seus campos continuam neste dispositivo.";
  return dirty ? "Alterações não salvas." : "Rascunho sincronizado.";
}

function shortId(value: string) {
  return value ? `${value.slice(0, 8)}…` : "não informado";
}

function readActionCsrf(action: "update" | "publish") {
  const prefix = `${process.env.NODE_ENV === "production" ? `__Host-appsell_product_${action}_csrf` : `appsell_product_${action}_csrf` }=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

const emptyFields: EditorFields = { slug: "", product_title: "", product_description: "", course_title: "", offer_title: "", offer_summary: "" };
