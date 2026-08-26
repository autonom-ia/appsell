"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, MouseEvent, useCallback, useEffect, useRef, useState } from "react";
import { AccessibleDialog } from "./accessible-dialog";
import { AccessLinksPanel } from "./access-links-panel";
import { CurriculumWorkspace } from "./curriculum-workspace";
import { ProductCoverPanel, type ProductCover } from "./product-cover-panel";
import { allowUnsavedNavigation, useUnsavedChanges } from "./use-unsaved-changes";
import {
  formatOfferPrice,
  isOfferBillingPeriod,
  OFFER_BILLING_PERIODS,
  parsePriceInputToMinor,
  priceInputFromMinor
} from "@/lib/offer-price";
import { keepPriceCharacters } from "@/lib/numeric-field";
import { publishedOnLabel, stepSummaries } from "@/lib/editor-steps";

type EditorSection = "produto" | "curriculo" | "oferta" | "publicar";
type LoadState = "loading" | "ready" | "unauthorized" | "forbidden" | "not_found" | "unavailable" | "error";
type SaveState = "idle" | "saving" | "saved" | "saved_with_changes" | "conflict" | "rebasing" | "rebased" | "invalid" | "forbidden" | "unavailable" | "error";
type PreviewState = "idle" | "loading" | "ready" | "unavailable" | "error";
type PublishState = "idle" | "publishing" | "published" | "conflict" | "forbidden" | "unavailable" | "error";
type OfferStatusState = "idle" | "transitioning" | "reloading" | "done" | "conflict" | "blocked" | "forbidden" | "unavailable" | "error";

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
  unit_amount_minor?: number;
  currency?: string;
  billing_period?: string;
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
  seller_slug?: string;
  slug: string;
  title: string;
  description: string;
  status: "draft" | "published" | "unpublished" | "archived";
  draft_version: number;
  course: CourseDetail;
  courses: CourseDetail[];
  curriculum: CurriculumSummary;
  offer: OfferDetail;
  cover?: ProductCover | null;
  publication?: { id?: string; version: number; published_at?: string } | null;
  has_unpublished_changes?: boolean;
  readiness?: { can_publish: boolean; has_unpublished_changes?: boolean; items: ReadinessItem[] } | null;
}

interface CurriculumSummary {
  modules: number;
  lessons: number;
}

type WorkflowStepState = "complete" | "pending";

interface EditorPayload {
  product?: ProductDetail;
  aggregate?: ProductDetail;
  course?: CourseDetail;
  offer?: OfferDetail | null;
  publication?: ProductDetail["publication"];
  latest_publication?: ProductDetail["publication"];
  csrf?: { update?: string; publish?: string; offer_status?: string; access_link_create?: string; access_link_revoke?: string; access_link_rotate?: string; cover_intent?: string; cover_confirm?: string; cover_clear?: string };
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
  offer_price: string;
  offer_billing_period: string;
}

interface PrivatePreview {
  seller?: { id?: string; display_name?: string; slug?: string };
  product?: { id?: string; title?: string; slug?: string; description?: string; status?: string };
  course?: { title?: string };
  courses?: PreviewCourse[];
  offer?: { title?: string; summary?: string; status?: string } | null;
  draft_version?: number;
  version?: number;
}

interface PreviewAsset {
  id?: string;
  title?: string;
  media_type?: string;
  byte_size?: number;
  content_role?: string;
  download_allowed?: boolean;
}

interface PreviewLesson {
  id?: string;
  title?: string;
  content?: string;
  assets?: PreviewAsset[];
}

interface PreviewModule {
  id?: string;
  title?: string;
  lessons?: PreviewLesson[];
}

interface PreviewCourse {
  id?: string;
  title?: string;
  description?: string;
  modules?: PreviewModule[];
}

interface PrivatePreviewPayload extends PrivatePreview {
  preview?: PrivatePreview | boolean;
  snapshot?: { product?: PrivatePreview["product"]; offer?: PrivatePreview["offer"]; course?: PrivatePreview["course"]; courses?: PreviewCourse[] };
  versions?: { product?: number; course?: number; offer?: number };
}

// Áreas, não etapas. Não há ordem obrigatória e nenhuma delas bloqueia a outra:
// o que impede publicar é a validação do servidor, mostrada à parte.
const AUTOSAVE_IDLE_MS = 2_500;

/**
 * Se a emenda pendente deve ser enviada quando a página vai embora.
 *
 * O autosave existe para o vendedor não perder trabalho, mas descartar também é
 * uma decisão dele. Enviar o que ele acabou de mandar jogar fora seria pior do
 * que não ter autosave nenhum, porque ele já viu a tela dizer que descartou.
 */
export function shouldFlushPendingDraft(state: { dirty: boolean; discarded: boolean }): boolean {
  return state.dirty && !state.discarded;
}

const editorSections: Array<{ id: EditorSection; label: string; hint: string }> = [
  { id: "produto", label: "Produto", hint: "Nome, endereço e descrição" },
  { id: "curriculo", label: "Conteúdo", hint: "Módulos e aulas" },
  { id: "oferta", label: "Preço", hint: "Quanto custa e se está à venda" },
  // "Publicar" virou "Distribuição": um produto no ar não tem o que publicar,
  // tem o que compartilhar. O identificador da área continua `publicar` porque
  // endereço em circulação é contrato — `?section=publicar` precisa abrir.
  { id: "publicar", label: "Distribuição", hint: "Quem entra e como" }
];

/**
 * Se cada área já tem conteúdo. É pista de navegação, não portão: o que libera a
 * publicação é `detail.readiness`, calculado pelo servidor, e essa é a única
 * validação que a coluna de prontidão mostra.
 */
export function workflowState(
  detail: ProductDetail,
  curriculum: CurriculumSummary
): Record<EditorSection, WorkflowStepState> {
  const productComplete = Boolean(
    detail.title.trim() && detail.slug.trim() && detail.course.title.trim()
  );
  const curriculumComplete = curriculum.modules > 0 && curriculum.lessons > 0;
  const offerComplete = Boolean(detail.offer.id && detail.offer.title.trim());
  const publishComplete = Boolean(detail.publication && !(detail.has_unpublished_changes ?? detail.readiness?.has_unpublished_changes));
  return {
    produto: productComplete ? "complete" : "pending",
    curriculo: curriculumComplete ? "complete" : "pending",
    oferta: offerComplete ? "complete" : "pending",
    publicar: publishComplete ? "complete" : "pending"
  };
}

export function ProductEditorWorkspace({ sellerId, productId }: { sellerId: string; productId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedSection = searchParams.get("section");
  const section: EditorSection = editorSections.some((item) => item.id === requestedSection) ? requestedSection as EditorSection : "produto";
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [csrf, setCsrf] = useState<{ update: string; publish: string; offerStatus: string; accessLinkCreate: string; accessLinkRevoke: string; accessLinkRotate: string; coverIntent: string; coverConfirm: string; coverClear: string }>({ update: "", publish: "", offerStatus: "", accessLinkCreate: "", accessLinkRevoke: "", accessLinkRotate: "", coverIntent: "", coverConfirm: "", coverClear: "" });
  const [fields, setFields] = useState<EditorFields>(emptyFields);
  const fieldsRef = useRef(fields);
  const dirtyRef = useRef(false);
  // Descartar é uma intenção, e o flush da desmontagem precisa saber dela: sem
  // isto ele reenviaria justamente a edição que o vendedor mandou jogar fora.
  const discardedRef = useRef(false);
  const [savedFields, setSavedFields] = useState<EditorFields>(emptyFields);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [invalidField, setInvalidField] = useState("");
  const [previewState, setPreviewState] = useState<PreviewState>("idle");
  const [preview, setPreview] = useState<PrivatePreview | null>(null);
  const [previewRetry, setPreviewRetry] = useState(0);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [publishState, setPublishState] = useState<PublishState>("idle");
  const [offerStatusState, setOfferStatusState] = useState<OfferStatusState>("idle");
  const [publishDialogOpen, setPublishDialogOpen] = useState(false);
  const [leaveDialogOpen, setLeaveDialogOpen] = useState(false);
  const [pendingSection, setPendingSection] = useState<EditorSection | null>(null);
  const [archiveDialogOpen, setArchiveDialogOpen] = useState(false);
  const [curriculumDirty, setCurriculumDirty] = useState(false);
  const [curriculumBusy, setCurriculumBusy] = useState(false);
  const [coverBusy, setCoverBusy] = useState(false);
  const [curriculumSummary, setCurriculumSummary] = useState<CurriculumSummary>({ modules: 0, lessons: 0 });
  const [assetNavigationWarning, setAssetNavigationWarning] = useState(false);
  const updateIdempotencyKey = useRef<string | null>(null);
  const publishIdempotencyKey = useRef<string | null>(null);
  const [publishAccessMode, setPublishAccessMode] = useState<"free" | "paid">("free");
  const [payoutState, setPayoutState] = useState<"unknown" | "checking" | "ready" | "missing">("unknown");
  const offerStatusIdempotencyKey = useRef<{ transition: string; key: string } | null>(null);
  const offerId = useRef<string | null>(null);
  const formElement = useRef<HTMLFormElement>(null);
  const publishCancelButton = useRef<HTMLButtonElement>(null);
  const sectionKeepButton = useRef<HTMLButtonElement>(null);
  const previewTriggerButton = useRef<HTMLButtonElement>(null);
  const previewCloseButton = useRef<HTMLButtonElement>(null);
  const leaveCancelButton = useRef<HTMLButtonElement>(null);
  const publishTriggerButton = useRef<HTMLButtonElement>(null);
  const archiveTriggerButton = useRef<HTMLButtonElement>(null);
  const archiveCancelButton = useRef<HTMLButtonElement>(null);
  const publishStatus = useRef<HTMLDivElement>(null);
  const saveErrorSummary = useRef<HTMLDivElement>(null);
  const listLink = useRef<HTMLAnchorElement>(null);
  const dirty = !sameFields(fields, savedFields);
  const editorDirty = dirty || curriculumDirty;
  // Edição editorial (produto/curso) e edição comercial (oferta) têm contratos
  // separados. Só a aba Oferta depende do ciclo de vida da oferta.
  const offerNotDraft = detail !== null && detail.offer.status !== "draft";
  const offerStatusLocked = offerStatusState === "transitioning" || offerStatusState === "reloading" || offerStatusState === "conflict";
  const offerLocked = offerNotDraft || offerStatusLocked;
  // Rótulo do preço na moeda armazenada da oferta: "R$" para BRL, o código ISO
  // (ex.: USD) caso contrário — o operador nunca edita um valor USD achando ser BRL.
  const offerCurrencyLabel = /^[A-Z]{3}$/.test(detail?.offer.currency ?? "")
    ? (detail!.offer.currency === "BRL" ? "R$" : detail!.offer.currency!)
    : "R$";
  const savedPriceInput = priceInputFromMinor(detail?.offer.unit_amount_minor);
  const localPriceMinor = parsePriceInputToMinor(fields.offer_price);
  const localPriceIsPositive = localPriceMinor !== null && localPriceMinor > 0;
  const priceDirty = detail !== null && fields.offer_price !== savedPriceInput;
  // Preço só é julgado depois que a pessoa sai do campo. Julgado a cada tecla,
  // "49," fica vermelho no caminho para "49,90" — o campo acusaria um erro que
  // ainda está sendo digitado. Depois do primeiro blur com erro, volta a
  // acompanhar a digitação para o vermelho sumir assim que for corrigido.
  const [priceTouched, setPriceTouched] = useState(false);
  const priceLocallyInvalid = priceTouched && localPriceMinor === null;

  useUnsavedChanges(editorDirty);

  // Autosave (A3.1/A3.7).
  //
  // A pausa existe para o salvamento não acompanhar a digitação: só depois de
  // AUTOSAVE_IDLE_MS parado é que a emenda vai. `persistDraft` já usa a trava de
  // versão e já sabe lidar com campos que mudaram durante o envio, então o
  // autosave não precisa de caminho próprio.
  //
  // Preço pela metade não é erro do vendedor, é digitação em curso: enquanto o
  // valor não faz sentido, o autosave espera em vez de acusar. O salvamento
  // manual continua acusando na hora.
  const persistDraftRef = useRef(persistDraft);
  // Sincronizados depois do render: escrever numa ref durante o render deixa o
  // valor inconsistente se o React descartar a renderização.
  useEffect(() => {
    persistDraftRef.current = persistDraft;
    dirtyRef.current = dirty;
    // Uma edição nova depois do descarte volta a valer, e volta a ser salva.
    if (dirty) discardedRef.current = false;
  });
  const autosaveBlocked =
    !dirty
    || saveState === "saving"
    || saveState === "conflict"
    || saveState === "rebasing"
    || detail?.status === "archived"
    || offerLocked
    || (section === "oferta" && parsePriceInputToMinor(fields.offer_price) === null);

  useEffect(() => {
    if (autosaveBlocked) return;
    const scheduledSection = section;
    const timer = window.setTimeout(
      () => { void persistDraftRef.current(scheduledSection); },
      AUTOSAVE_IDLE_MS
    );
    return () => window.clearTimeout(timer);
  }, [autosaveBlocked, fields, section]);

  // A última emenda não pode morrer com a página. Ao sair, o que estiver
  // pendente é enviado sem esperar a pausa.
  useEffect(() => () => {
    if (!shouldFlushPendingDraft({ dirty: dirtyRef.current, discarded: discardedRef.current })) return;
    void persistDraftRef.current();
  }, []);
  useEffect(() => {
    if (publishState !== "publishing") return;
    requestAnimationFrame(() => publishStatus.current?.focus());
  }, [publishState]);
  useEffect(() => {
    if (saveState !== "invalid") return;
    requestAnimationFrame(() => saveErrorSummary.current?.focus());
  }, [saveState]);
  const handleCurriculumDirty = useCallback((nextDirty: boolean) => setCurriculumDirty(nextDirty), []);
  const handleCurriculumBusy = useCallback((nextBusy: boolean) => {
    setCurriculumBusy(nextBusy);
    if (!nextBusy) setAssetNavigationWarning(false);
  }, []);
  const handleCurriculumSummary = useCallback((summary: CurriculumSummary) => {
    setCurriculumSummary(summary);
    setDetail((current) => current ? { ...current, curriculum: summary } : current);
  }, []);

  /**
   * Relê o produto.
   *
   * `keepScreenOnFailure` existe porque esta função serve a dois momentos
   * diferentes. No carregamento inicial, falhar significa que não há nada para
   * mostrar, e trocar a tela por um erro é a resposta certa. Depois de uma
   * mutação que já teve sucesso no servidor, falhar significa só que a tela
   * ficou velha — e apagar o editor inteiro faria o vendedor achar que perdeu o
   * que acabou de salvar.
   */
  const loadDetail = useCallback(async (keepScreenOnFailure = false) => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    const fail = (next: LoadState) => {
      if (!keepScreenOnFailure) setLoadState(next);
      return false;
    };
    try {
      const response = await fetch(`/api/sellers/${sellerId}/products/${productId}`, { cache: "no-store", signal: controller.signal });
      if (response.status === 401) return fail("unauthorized");
      if (response.status === 403) return fail("forbidden");
      if (response.status === 404) return fail("not_found");
      if (response.status === 503) return fail("unavailable");
      if (!response.ok) return fail("error");
      const payload = await response.json() as EditorPayload & ProductDetail;
      const nextDetail = normalizeEditorPayload(payload);
      if (!nextDetail) return fail("error");
      const nextFields = fieldsFromDetail(nextDetail);
      offerId.current = nextDetail.offer.id ?? crypto.randomUUID();
      setDetail({ ...nextDetail, readiness: payload.readiness ?? nextDetail.readiness });
      setCurriculumSummary(nextDetail.curriculum);
      setCsrf({ update: payload.csrf?.update ?? readActionCsrf("update"), publish: payload.csrf?.publish ?? readActionCsrf("publish"), offerStatus: payload.csrf?.offer_status ?? "", accessLinkCreate: payload.csrf?.access_link_create ?? "", accessLinkRevoke: payload.csrf?.access_link_revoke ?? "", accessLinkRotate: payload.csrf?.access_link_rotate ?? "", coverIntent: payload.csrf?.cover_intent ?? "", coverConfirm: payload.csrf?.cover_confirm ?? "", coverClear: payload.csrf?.cover_clear ?? "" });
      setFields(nextFields);
      setSavedFields(nextFields);
      setLoadState("ready");
      return true;
    } catch {
      return fail("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }, [productId, sellerId]);
  const handleCurriculumCommitted = useCallback(() => { void loadDetail(); }, [loadDetail]);
  // A capa muda a versão do produto e o endereço assinado da imagem. Recarregar
  // é o que mantém o painel falando do estado real: o número devolvido pela
  // mutação sozinho não traz a URL nova. Falhar aqui não desfaz nada, então a
  // tela do editor fica de pé e quem avisa é o próprio painel.
  const handleCoverCommitted = useCallback(() => loadDetail(true), [loadDetail]);
  const handleCoverBusy = useCallback((busy: boolean) => setCoverBusy(busy), []);

  useEffect(() => {
    const start = window.setTimeout(() => void loadDetail(), 0);
    return () => window.clearTimeout(start);
  }, [loadDetail]);

  useEffect(() => {
    if (!previewOpen || loadState !== "ready" || dirty) return;
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
  }, [dirty, loadState, previewOpen, previewRetry, productId, sellerId]);

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
    await persistDraft();
  }

  async function persistDraft(scheduledSection: EditorSection = section): Promise<boolean> {
    if (!detail || saveState === "saving") return false;
    // A área vem de quem chamou. O autosave passa a que estava aberta quando ele
    // foi agendado, para nunca salvar um grupo de campos achando que é outro.
    const savingOffer = scheduledSection === "oferta";
    // Guarda de defesa: o form já é desabilitado por offerLocked, mas o submit
    // pode chegar por Enter durante uma transição em voo — não salvar nesse caso.
    if (savingOffer && (detail.offer.status !== "draft" || offerStatusState === "transitioning")) return false;
    const submitted = fields;
    const submittedUnitAmountMinor = savingOffer ? parsePriceInputToMinor(submitted.offer_price) : null;
    if (savingOffer && submittedUnitAmountMinor === null) {
      setInvalidField("offer_price");
      setSaveState("invalid");
      const input = formElement.current?.elements.namedItem("offer_price");
      if (input instanceof HTMLElement) input.focus();
      return false;
    }
    const submittedVersions = versionsFromDetail(detail);
    const key = updateIdempotencyKey.current ?? crypto.randomUUID();
    updateIdempotencyKey.current = key;
    const data = new FormData();
    data.set("_csrf", csrf.update);
    data.set("idempotency_key", key);
    data.set("expected_product_version", String(submittedVersions.product));
    data.set("expected_course_version", String(submittedVersions.course));
    data.set("course_id", detail.course.id);
    data.set("slug", submitted.slug);
    data.set("product_title", submitted.product_title);
    data.set("product_description", submitted.product_description);
    data.set("course_title", submitted.course_title);
    if (savingOffer) {
      data.set("expected_offer_version", String(submittedVersions.offer));
      data.set("offer_id", offerId.current ?? crypto.randomUUID());
      data.set("offer_title", submitted.offer_title);
      data.set("offer_summary", submitted.offer_summary);
      data.set("offer_unit_amount_minor", String(submittedUnitAmountMinor));
      // Preserva a moeda armazenada da oferta (não há troca de moeda na UI);
      // hardcodar BRL converteria silenciosamente uma oferta em outra moeda.
      data.set("offer_currency", /^[A-Z]{3}$/.test(detail.offer.currency ?? "") ? detail.offer.currency! : "BRL");
      data.set("offer_billing_period", isOfferBillingPeriod(submitted.offer_billing_period) ? submitted.offer_billing_period : "one_time");
    }
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
        return false;
      }
      const refreshResponse = await fetch(`/api/sellers/${sellerId}/products/${productId}`, { cache: "no-store", signal: controller.signal });
      if (!refreshResponse.ok) {
        setSaveState("unavailable");
        return false;
      }
      const payload = await refreshResponse.json() as EditorPayload & ProductDetail;
      const updated = normalizeEditorPayload(payload);
      if (!updated) {
        setSaveState("error");
        return false;
      }
      updateIdempotencyKey.current = null;
      setDetail({ ...updated, readiness: payload.readiness ?? updated.readiness });
      setCurriculumSummary(updated.curriculum);
      setCsrf((current) => ({ ...current, update: payload.csrf?.update ?? current.update, publish: payload.csrf?.publish ?? current.publish, offerStatus: payload.csrf?.offer_status ?? current.offerStatus, accessLinkCreate: payload.csrf?.access_link_create ?? current.accessLinkCreate, accessLinkRevoke: payload.csrf?.access_link_revoke ?? current.accessLinkRevoke, accessLinkRotate: payload.csrf?.access_link_rotate ?? current.accessLinkRotate, coverIntent: payload.csrf?.cover_intent ?? current.coverIntent, coverConfirm: payload.csrf?.cover_confirm ?? current.coverConfirm, coverClear: payload.csrf?.cover_clear ?? current.coverClear }));
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
      return true;
    } catch {
      setSaveState("unavailable");
      return false;
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
      setCurriculumSummary(latest.curriculum);
      setSavedFields(fieldsFromDetail(latest));
      setCsrf((current) => ({ ...current, update: payload.csrf?.update ?? current.update, publish: payload.csrf?.publish ?? current.publish, offerStatus: payload.csrf?.offer_status ?? current.offerStatus, accessLinkCreate: payload.csrf?.access_link_create ?? current.accessLinkCreate, accessLinkRevoke: payload.csrf?.access_link_revoke ?? current.accessLinkRevoke, accessLinkRotate: payload.csrf?.access_link_rotate ?? current.accessLinkRotate, coverIntent: payload.csrf?.cover_intent ?? current.coverIntent, coverConfirm: payload.csrf?.cover_confirm ?? current.coverConfirm, coverClear: payload.csrf?.cover_clear ?? current.coverClear }));
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

  // Asked every time the seller opens the access decision, because connecting a
  // payout account happens elsewhere on the page: a cached answer would keep
  // showing "configure seus recebimentos" after it was already configured.
  useEffect(() => {
    if (!publishDialogOpen) return;
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    // Deferred so the render that opened the dialog is never interrupted.
    void Promise.resolve()
      .then(() => {
        if (active) setPayoutState("checking");
      });
    void fetch(`/api/sellers/${encodeURIComponent(sellerId)}/payout-account`, {
      cache: "no-store",
      signal: controller.signal
    })
      .then(async (response) => {
        if (!active) return;
        // A transient failure is not the same as "not configured", and telling
        // the seller to go configure what is already configured wastes their time.
        if (!response.ok) return setPayoutState(response.status === 404 ? "missing" : "unknown");
        const payload = await response.json() as {
          payout_account?: {
            charges_enabled?: boolean;
            payouts_enabled?: boolean;
            disabled_reason?: string | null;
          } | null;
        };
        if (!active) return;
        const account = payload.payout_account;
        const usable = Boolean(
          account?.charges_enabled && account?.payouts_enabled && !account?.disabled_reason
        );
        setPayoutState(usable ? "ready" : "missing");
      })
      .catch(() => {
        if (active) setPayoutState("unknown");
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      active = false;
      controller.abort();
      clearTimeout(timeout);
    };
  }, [publishDialogOpen, sellerId]);

  async function publish(accessMode: "free" | "paid") {
    const hasChanges = detail?.has_unpublished_changes ?? detail?.readiness?.has_unpublished_changes;
    if (!detail || editorDirty || publishState === "publishing" || detail.readiness?.can_publish !== true || (detail.publication && hasChanges !== true)) return;
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
    // Publishing carries the access decision, so the catalog and the sale can
    // never disagree: either both land or neither does.
    data.set("access_mode", accessMode);
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
        setCurriculumSummary(updated.curriculum);
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

  async function transitionOffer(nextStatus: "draft" | "active" | "archived") {
    const currentOffer = detail?.offer;
    if (!currentOffer?.id || offerStatusLocked || editorDirty) return;
    const expectedStatus = currentOffer.status;
    if (expectedStatus === nextStatus) return;
    setOfferStatusState("transitioning");
    // Reusa a chave enquanto for a MESMA transição (status+versão): retry após
    // resposta perdida vira replay idempotente em vez de novo comando.
    const transition = `${expectedStatus}->${nextStatus}@${currentOffer.draft_version}`;
    const key = offerStatusIdempotencyKey.current?.transition === transition
      ? offerStatusIdempotencyKey.current.key
      : crypto.randomUUID();
    offerStatusIdempotencyKey.current = { transition, key };
    const data = new FormData();
    data.set("_csrf", csrf.offerStatus);
    data.set("idempotency_key", key);
    data.set("expected_status", expectedStatus);
    data.set("status", nextStatus);
    data.set("expected_offer_version", String(currentOffer.draft_version));
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    try {
      const response = await fetch(
        `/api/sellers/${sellerId}/products/${productId}/offers/${currentOffer.id}/status`,
        { method: "PATCH", body: data, signal: controller.signal }
      );
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) setOfferStatusState("forbidden");
        else if (response.status === 409) setOfferStatusState("conflict");
        else if (response.status === 422) setOfferStatusState("blocked");
        else if (response.status === 503) setOfferStatusState("unavailable");
        else setOfferStatusState("error");
        return;
      }
      // Só descarta a chave APÓS reconciliar: se o PATCH aplicou mas o GET
      // falhar, preservar a chave faz o retry virar replay idempotente em vez
      // de um novo comando (que bateria em 409 na versão já mudada).
      const refreshed = await loadDetail();
      if (refreshed) {
        offerStatusIdempotencyKey.current = null;
        setOfferStatusState("done");
      } else {
        setOfferStatusState("unavailable");
      }
    } catch {
      setOfferStatusState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  async function reloadOfferAfterConflict() {
    if (offerStatusState !== "conflict") return;
    setOfferStatusState("reloading");
    // O estado local ficou obsoleto; uma nova tentativa só pode nascer depois
    // de reconciliar a oferta e receber sua versão atual.
    offerStatusIdempotencyKey.current = null;
    const refreshed = await loadDetail();
    setOfferStatusState(refreshed ? "idle" : "unavailable");
  }

  function requestListNavigation(event: MouseEvent<HTMLAnchorElement>) {
    if (curriculumBusy || coverBusy) {
      event.preventDefault();
      setAssetNavigationWarning(true);
      return;
    }
    if (!editorDirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    setLeaveDialogOpen(true);
  }

  function requestSectionNavigation(event: MouseEvent<HTMLAnchorElement>, target: EditorSection) {
    if (target !== section && (curriculumBusy || coverBusy)) {
      event.preventDefault();
      setAssetNavigationWarning(true);
      return;
    }
    if (target === section || !editorDirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    // Leaving with unsaved work used to be a browser confirm whose only outcomes
    // were losing the edits or staying put. Keeping them is the outcome a seller
    // actually wants, so it has to be on offer.
    event.preventDefault();
    setPendingSection(target);
  }

  function goToPendingSection() {
    const target = pendingSection;
    setPendingSection(null);
    if (!target) return;
    setCurriculumDirty(false);
    router.replace(`?section=${target}`, { scroll: false });
  }

  function discardLocalEdits() {
    if (dirtyRef.current) {
      setFields(savedFields);
      fieldsRef.current = savedFields;
      setSaveState("idle");
      setInvalidField("");
    }
    discardedRef.current = true;
    dirtyRef.current = false;
  }

  function discardAndGo() {
    discardLocalEdits();
    goToPendingSection();
  }

  async function saveAndGo() {
    const saved = await persistDraft();
    if (!saved) return;
    goToPendingSection();
  }

  function closePublishDialog() {
    setPublishDialogOpen(false);
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
  const canPublish = readiness?.can_publish === true && hasUnpublishedChanges && !editorDirty && publishState !== "publishing";
  const publicationVersion = detail.publication?.version;
  const publicProductPath = detail.seller_slug
    ? `/s/${detail.seller_slug}/p/${detail.slug}${detail.offer.status === "draft" ? "/access" : ""}`
    : null;
  const canActivateSales = detail.offer.status === "draft" &&
    Boolean(detail.publication) &&
    !hasUnpublishedChanges &&
    (detail.offer.unit_amount_minor ?? 0) > 0 &&
    !editorDirty &&
    !offerStatusLocked;
  const paidAvailable = payoutState === "ready" && (detail.offer.unit_amount_minor ?? 0) > 0;
  const workflow = workflowState(detail, curriculumSummary);
  const summaries = stepSummaries({
    title: detail.title,
    slug: detail.slug,
    modules: curriculumSummary.modules,
    lessons: curriculumSummary.lessons,
    offerStatus: detail.offer.status,
    unitAmountMinor: detail.offer.unit_amount_minor,
    currency: detail.offer.currency,
    published: Boolean(detail.publication),
    publishedAt: detail.publication?.published_at,
    hasUnpublishedChanges: Boolean(detail.has_unpublished_changes ?? detail.readiness?.has_unpublished_changes)
  });
  const liveSince = detail.publication ? publishedOnLabel(detail.publication.published_at) : null;

  return (
    <section className="product-editor" aria-labelledby="product-editor-title">
      <header className="editor-header">
        <div className="editor-breadcrumb"><Link ref={listLink} href={`/products?seller=${encodeURIComponent(sellerId)}`} onClick={requestListNavigation}>Produtos</Link><span aria-hidden="true">/</span><span aria-current="page">{detail.title}</span></div>
        <div className="editor-title-row">
          <div>
            {/* Nem "Espaço selecionado" nem os oito primeiros caracteres do
                UUID. Quem vende reconhece a própria loja pelo endereço dela,
                não por um identificador do nosso banco. */}
            <p className="eyebrow">{detail.seller_slug ? `Loja @${detail.seller_slug}` : "Sua loja"}</p>
            <h1 id="product-editor-title">{detail.title}</h1>
            {/* Decisão travada do Pacote A: a interface esconde o conceito de
                curso enquanto houver um só. Com dois ou mais, ele volta a ser
                informação que o vendedor precisa. */}
            <p>@{detail.slug}{detail.courses.length > 1 ? ` · ${detail.course.title}` : ""}{liveSince ? ` · no ar desde ${liveSince}` : ""}</p>
          </div>
          <span className={`status status-${detail.status}`}>{detail.status === "published" ? `Publicado${publicationVersion ? ` v${publicationVersion}` : ""}` : detail.status === "draft" ? "Rascunho privado" : detail.status === "unpublished" ? "Despublicado" : "Arquivado"}</span>
        </div>
        <div className={`save-indicator save-${saveState}`}>
          <span role="status" aria-live="polite" aria-atomic="true">{saveMessage(saveState, dirty)}</span>
          <button ref={previewTriggerButton} className="secondary-button" type="button" onClick={() => setPreviewOpen(true)}>Ver como aluno</button>
          {saveState === "conflict" || saveState === "rebasing" ? <button className="secondary-button" type="button" disabled={saveState === "rebasing"} onClick={() => void rebaseFromServer()}>{saveState === "rebasing" ? "Recarregando…" : "Recarregar versão do servidor"}</button> : null}
        </div>
        {saveState === "invalid" ? <div ref={saveErrorSummary} className="form-error-summary" role="alert" tabIndex={-1}><strong>Não foi salvo.</strong><span>Corrija o campo destacado{invalidField && invalidField !== "form" ? ` (${editorFieldLabel(invalidField)})` : ""} e tente novamente.</span></div> : null}
      </header>

      <nav className="editor-section-nav" aria-label="Áreas do produto">
        {editorSections.map((item) => {
          const active = section === item.id;
          const filled = workflow[item.id] === "complete";
          // O resumo troca a dica fixa: em vez de "Módulos e aulas" para
          // sempre, a etapa diz o que ela tem agora — "2 módulos, 5 aulas" — ou
          // o que falta nela.
          const summary = summaries[item.id] ?? item.hint;
          return <Link key={item.id} href={`?section=${item.id}`} replace scroll={false} className={`${active ? "active " : ""}area-${filled ? "filled" : "empty"}`} aria-current={active ? "page" : undefined} onClick={(event) => requestSectionNavigation(event, item.id)}><span className="editor-step-heading"><strong>{item.label}</strong><span className="editor-step-mark" aria-hidden="true">{filled ? "●" : "○"}</span></span><span className="editor-step-summary">{summary}</span><span className="sr-only">{active ? "área aberta" : ""}</span></Link>;
        })}
      </nav>
      {assetNavigationWarning ? <p className="form-note asset-navigation-warning" role="alert">{coverBusy ? "A capa ainda está sendo enviada. Aguarde o envio terminar antes de sair desta área." : "O arquivo ainda está sendo enviado. Aguarde ou cancele o envio antes de sair do conteúdo."}</p> : null}

      {/* A mesma validação que o servidor usa para liberar a publicação, visível
          de qualquer área. Nada aqui é calculado no cliente. */}
      <aside className="editor-readiness" aria-labelledby="editor-readiness-title">
        <h2 id="editor-readiness-title">O que falta para publicar</h2>
        {readiness
          ? readiness.items.length > 0
            ? <ul className="readiness-list">{readiness.items.map((item, index) => <li key={item.key ?? `${item.label}-${index}`} className={item.ready ? "ready" : "blocked"}><span aria-hidden="true">{item.ready ? "✓" : "!"}</span><div><strong>{item.label}</strong>{item.detail ? <p>{item.detail}</p> : null}</div><span className="sr-only">{item.ready ? "Pronto" : "Pendente"}</span></li>)}</ul>
            : <p className="form-note" role="status">O servidor não apontou nenhuma pendência.</p>
          : <p className="form-note" role="status">A validação de publicação ainda não chegou. Nada será publicado até ela chegar.</p>}
        {editorDirty ? <p className="form-note" role="status">Há alterações não salvas.</p> : null}
      </aside>

      {/* Fora do formulário de propósito: os campos da capa não fazem parte
          daquele envio, e Enter aqui não pode salvar o rascunho. Fica antes
          dele, e não depois, porque a capa é identidade — e um painel solto no
          fim da página, atrás do rodapé de salvar, parecia coisa esquecida. */}
      {section === "produto" ? (
        <ProductCoverPanel
          sellerId={sellerId}
          productId={productId}
          productTitle={detail.title}
          cover={detail.cover ?? null}
          productVersion={detail.draft_version}
          archived={detail.status === "archived"}
          csrf={{ intent: csrf.coverIntent, confirm: csrf.coverConfirm, clear: csrf.coverClear }}
          onBusyChange={handleCoverBusy}
          onCommitted={handleCoverCommitted}
        />
      ) : null}

      {section === "curriculo" ? <CurriculumWorkspace sellerId={sellerId} productId={productId} courseId={detail.course.id} courseTitle={detail.course.title} archived={detail.status === "archived"} onDirtyChange={handleCurriculumDirty} onBusyChange={handleCurriculumBusy} onSummaryChange={handleCurriculumSummary} onCommitted={handleCurriculumCommitted}/> : (
      <form ref={formElement} className="editor-form" onSubmit={saveDraft}>
        {section === "produto" ? (
          <fieldset disabled={detail.status === "archived"}>
            <legend>Identidade do produto</legend>
            <p className="section-intro">Edite o produto sem precisar configurar vendas. Uma publicação existente permanece estável até você publicar outra versão.</p>
            <EditorField label="Nome do produto" name="product_title" value={fields.product_title} onChange={(value) => updateField("product_title", value)} minLength={2} maxLength={160} required invalid={invalidField === "product_title"}/>
            <EditorField label="Endereço do produto" name="slug" value={fields.slug} onChange={(value) => updateField("slug", value)} minLength={3} maxLength={63} required invalid={invalidField === "slug"} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" help="Somente letras minúsculas, números e hífens."/>
            <EditorField label="Descrição curta" name="product_description" value={fields.product_description} onChange={(value) => updateField("product_description", value)} maxLength={2000} multiline invalid={invalidField === "product_description"}/>
            <EditorField label="Nome do conteúdo" name="course_title" value={fields.course_title} onChange={(value) => updateField("course_title", value)} minLength={2} maxLength={160} required invalid={invalidField === "course_title"}/>
          </fieldset>
        ) : null}

        {section === "oferta" ? (
          <>
            <fieldset disabled={detail.status === "archived" || offerLocked}>
              <legend>Preço e cobrança</legend>
              {detail.offer.status === "draft"
                ? <div className="billing-boundary" role="note"><strong>{!localPriceIsPositive ? "Defina um preço maior que zero" : priceDirty ? "Salve o preço antes de ativar vendas" : dirty ? "Salve as alterações antes de ativar vendas" : detail.publication ? "Preço salvo; pronto para vender" : "Preço salvo; catálogo pronto para publicar"}</strong><p>{!localPriceIsPositive ? `Informe o valor em ${offerCurrencyLabel}. Sem preço maior que zero, as vendas permanecem desativadas.` : priceDirty ? `O valor em ${offerCurrencyLabel} só será considerado no checkout depois que o rascunho for salvo.` : dirty ? "Salve as alterações pendentes antes de ativar vendas." : detail.publication ? `O preço salvo em ${offerCurrencyLabel} será cobrado quando as vendas estiverem ativas.` : "Publique o catálogo antes de ativar vendas; esta etapa não cria checkout."}</p></div>
                : <div className="billing-boundary" role="note"><strong>{detail.offer.status === "active" ? "Vendas ativas" : "Vendas encerradas"}</strong><p>{detail.offer.status === "active" ? "Para editar termos ou preço, desative as vendas primeiro." : "Encerrar vendas é definitivo, e o produto não volta para a vitrine."}</p></div>}
              <EditorField label="Nome no checkout" name="offer_title" value={fields.offer_title} onChange={(value) => updateField("offer_title", value)} minLength={2} maxLength={120} required invalid={invalidField === "offer_title"}/>
              <EditorField label="Descrição no checkout" name="offer_summary" value={fields.offer_summary} onChange={(value) => updateField("offer_summary", value)} maxLength={500} multiline invalid={invalidField === "offer_summary"}/>
              <EditorField label={`Preço (${offerCurrencyLabel})`} name="offer_price" value={fields.offer_price} onChange={(value) => updateField("offer_price", value)} onBlur={() => setPriceTouched(true)} maxLength={12} inputMode="decimal" pattern="[0-9]{1,9}([.,][0-9]{1,2})?" sanitize={keepPriceCharacters} invalid={invalidField === "offer_price" || invalidField === "offer_unit_amount_minor" || priceLocallyInvalid} help="Use vírgula para centavos, ex.: 49,90. Deixe vazio para manter sem preço."/>
              <label className="editor-field">Cobrança
                <select id="offer_billing_period" name="offer_billing_period" value={fields.offer_billing_period} onChange={(event) => updateField("offer_billing_period", event.target.value)} aria-invalid={invalidField === "offer_billing_period"}>
                  {OFFER_BILLING_PERIODS.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
                </select>
              </label>
            </fieldset>
            <section className="offer-status-panel" aria-labelledby="offer-status-title">
              <p className="eyebrow">Disponibilidade na vitrine</p>
              <h2 id="offer-status-title">Vendas {offerStatusLabel(detail.offer.status)}</h2>
              {detail.offer.status === "draft" ? <p>{!detail.publication ? "Publique o catálogo antes de escolher o acesso. A ativação das vendas fica em Publicar." : "A decisão entre acesso gratuito e vendas com checkout fica em Publicar."}</p> : null}
              {detail.offer.status === "active" ? <p>Vendas ativas por {detail.offer.unit_amount_minor ? formatOfferPrice(detail.offer.unit_amount_minor, detail.offer.currency) : "preço não informado"}. Para editar termos ou preço, volte a rascunho em Publicar.</p> : null}
              {detail.offer.status === "archived" ? <p>Vendas encerradas. Crie um novo produto para vender novamente.</p> : null}
              <p className="form-note" role="status">A alteração do acesso é feita na etapa Publicar, depois que o rascunho estiver salvo.</p>
            </section>
          </>
        ) : null}


        {section === "publicar" ? (
          <section className="publish-panel" aria-labelledby="publish-panel-title">
            <p className="eyebrow">Validação de segurança</p><h2 id="publish-panel-title">Publicar catálogo</h2>
            <p>A publicação cria uma versão fixa do catálogo com produto, preço informativo, módulos, aulas e PDFs já verificados. Depois, escolha aqui se o acesso será gratuito ou passará pelo checkout.</p>
            {readiness ? (
              <ul className="readiness-list">{readiness.items.map((item, index) => <li key={item.key ?? `${item.label}-${index}`} className={item.ready ? "ready" : "blocked"}><span aria-hidden="true">{item.ready ? "✓" : "!"}</span><div><strong>{item.label}</strong>{item.detail ? <p>{item.detail}</p> : null}</div><span className="sr-only">{item.ready ? "Pronto" : "Pendente"}</span></li>)}</ul>
            ) : <p className="form-note" role="status">A API ainda não retornou a validação de publicação. Nada será publicado.</p>}
            {editorDirty ? <p className="form-note" role="status">Salve as alterações e aguarde os materiais em verificação antes de publicar.</p> : null}
            {!dirty && detail.publication && !hasUnpublishedChanges ? <p className="form-note" role="status">Nenhuma mudança desde a versão publicada. Edite e salve o rascunho antes de criar outra versão.</p> : null}
            <div className="publish-action"><button ref={publishTriggerButton} className="primary-button" type="button" disabled={!canPublish} onClick={() => setPublishDialogOpen(true)}><span>{publishState === "publishing" ? "Publicando catálogo…" : `Publicar catálogo${publicationVersion ? ` nova versão após v${publicationVersion}` : " primeira versão"}`}</span></button></div>
            <section className="offer-status-panel" aria-labelledby="access-mode-title">
              <p className="eyebrow">Acesso do produto</p>
              <h2 id="access-mode-title">{detail.offer.status === "active" ? "Vendas ativas" : "Acesso gratuito"}</h2>
              {detail.offer.status === "active"
                ? <p>O link público encaminha o comprador para o checkout antes de liberar o conteúdo.</p>
                : <p>O link público encaminha a pessoa para cadastro ou login e libera o conteúdo sem cobrança. Para vender, salve um preço maior que zero e ative as vendas aqui.</p>}
              {detail.offer.status === "draft" ? <>
                {!detail.publication ? <p className="form-note">Publique o catálogo primeiro. Depois desta publicação, o botão de ativação ficará disponível aqui.</p> : null}
                {detail.publication && hasUnpublishedChanges && !editorDirty ? <p className="form-note">Publique a versão atual antes de ativar vendas. O checkout só deve acompanhar um catálogo publicado.</p> : null}
                {detail.publication && !editorDirty && (detail.offer.unit_amount_minor ?? 0) <= 0 ? <p className="form-note">Para ativar vendas, informe e salve um preço maior que zero na etapa Preço.</p> : null}
                {editorDirty ? <p className="form-note">Salve as alterações antes de escolher o modo de acesso.</p> : null}
                <div className="offer-status-actions"><button className="primary-button" type="button" disabled={!canActivateSales} onClick={() => void transitionOffer("active")}>{offerStatusState === "transitioning" ? "Ativando vendas…" : "Ativar vendas e usar checkout"}</button></div>
              </> : null}
              {/* O ramo do rascunho já explica cada bloqueio; o da oferta ativa
                  desligava os dois botões calado. */}
              {detail.offer.status === "active" && editorDirty ? <p className="form-note">Salve as alterações antes de desativar ou encerrar as vendas.</p> : null}
              {detail.offer.status === "active" ? <div className="offer-status-actions">
                <button className="secondary-button" type="button" disabled={editorDirty || offerStatusLocked} onClick={() => void transitionOffer("draft")}>{offerStatusState === "transitioning" ? "Aplicando…" : "Desativar vendas e voltar ao gratuito"}</button>
                <button ref={archiveTriggerButton} className="secondary-button" type="button" disabled={editorDirty || offerStatusLocked} onClick={() => setArchiveDialogOpen(true)}>Encerrar vendas</button>
              </div> : null}
              <div className="form-feedback" aria-live="polite" aria-atomic="true">
                {offerStatusState === "done" ? <p className="form-success">Modo de acesso atualizado.</p> : null}
                {offerStatusState === "conflict" ? <><p className="form-error">O preço mudou em outra sessão. Atualize os dados antes de tentar novamente.</p><button className="secondary-button" type="button" onClick={() => void reloadOfferAfterConflict()}>Atualizar dados do preço</button></> : null}
                {offerStatusState === "reloading" ? <p className="form-note">Atualizando os dados do preço…</p> : null}
                {offerStatusState === "blocked" ? <p className="form-error">A mudança foi bloqueada. Publique o catálogo e confira se há preço salvo maior que zero.</p> : null}
                {offerStatusState === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente.</p> : null}
                {offerStatusState === "unavailable" ? <p className="form-note">A API não respondeu. Nenhuma mudança foi confirmada; tente novamente.</p> : null}
                {offerStatusState === "error" ? <p className="form-error">Não foi possível mudar o modo de acesso.</p> : null}
              </div>
              {publicProductPath && detail.publication ? <p className="form-note">Link público: <a href={publicProductPath} target="_blank" rel="noreferrer">{publicProductPath}</a></p> : null}
            </section>
            <AccessLinksPanel sellerId={sellerId} productId={productId} published={Boolean(detail.publication)} csrf={{ create: csrf.accessLinkCreate, revoke: csrf.accessLinkRevoke, rotate: csrf.accessLinkRotate }}/>
            <div ref={publishStatus} className="form-feedback" aria-live="polite" aria-atomic="true" tabIndex={-1}>
              {publishState === "publishing" ? <p className="form-note">Publicando uma versão fixa do catálogo…</p> : null}
              {publishState === "published" ? <p className="form-success">Catálogo publicado com sucesso. Agora escolha acima entre acesso gratuito e vendas com checkout.</p> : null}
              {publishState === "conflict" ? <p className="form-error">O produto mudou ou não está pronto. Recarregue os dados antes de publicar.</p> : null}
              {publishState === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente.</p> : null}
              {publishState === "unavailable" ? <p className="form-note">A API não respondeu. A chave desta publicação foi preservada para uma tentativa segura.</p> : null}
              {publishState === "error" ? <p className="form-error">Não foi possível publicar. Revise a validação e tente novamente.</p> : null}
            </div>
          </section>
        ) : null}

        {(section === "produto" || section === "oferta") ? (
          <div className="editor-save-bar">
            <span aria-live="polite">{dirty ? "Alterações não salvas" : "Tudo salvo"}</span>
            <button className="primary-button" type="submit" disabled={!dirty || saveState === "saving" || detail.status === "archived" || (section === "oferta" && offerLocked)}>{saveState === "saving" ? "Salvando…" : "Salvar rascunho"}</button>
          </div>
        ) : null}
      </form>
      )}


      {publishDialogOpen ? (
        <AccessibleDialog titleId="publish-dialog-title" descriptionId="publish-dialog-description" initialFocusRef={publishCancelButton} returnFocusRef={publishTriggerButton} onClose={closePublishDialog}>
            <p className="eyebrow">Colocar no ar</p><h2 id="publish-dialog-title">Colocar {detail.title} no ar?</h2>
            <p id="publish-dialog-description">Conteúdo, materiais verificados e o preço ficam congelados nesta versão. Escolha agora como as pessoas entram.</p>
            <div className="access-mode-choice" role="radiogroup" aria-label="Modo de acesso">
              <label className={publishAccessMode === "free" ? "access-mode-option selected" : "access-mode-option"}>
                <input type="radio" name="access_mode" value="free" checked={publishAccessMode === "free"} onChange={() => setPublishAccessMode("free")}/>
                <span><strong>Acesso gratuito</strong><span>Você distribui por link. Cada pessoa entra com o próprio cadastro.</span></span>
              </label>
              <label className={`${publishAccessMode === "paid" ? "access-mode-option selected" : "access-mode-option"}${paidAvailable ? "" : " disabled"}`}>
                <input type="radio" name="access_mode" value="paid" checked={publishAccessMode === "paid"} disabled={!paidAvailable} onChange={() => setPublishAccessMode("paid")}/>
                <span>
                  <strong>Vender com checkout</strong>
                  <span>{payoutState === "checking"
                    ? "Verificando seus recebimentos…"
                    : payoutState === "unknown"
                      ? "Não conseguimos verificar seus recebimentos agora. Feche e abra de novo."
                      : payoutState === "missing"
                        ? "Indisponível: configure seus recebimentos para poder cobrar."
                        : (detail.offer.unit_amount_minor ?? 0) <= 0
                          ? "Indisponível: salve um preço maior que zero na etapa Preço."
                          : `O link leva ao pagamento de ${formatOfferPrice(detail.offer.unit_amount_minor ?? 0, detail.offer.currency)} antes de liberar o conteúdo.`}</span>
                </span>
              </label>
              {payoutState === "missing" ? <Link className="access-mode-fix" href="/payouts">Configurar recebimentos</Link> : null}
            </div>
            <div className="dialog-actions"><button ref={publishCancelButton} className="secondary-button" type="button" onClick={closePublishDialog}>Revisar novamente</button><button className="primary-button commercial-button" type="button" onClick={() => void publish(publishAccessMode)}>{publishAccessMode === "paid" ? "Colocar no ar e vender" : "Colocar no ar"}</button></div>
        </AccessibleDialog>
      ) : null}

      {previewOpen ? (
        <AccessibleDialog titleId="preview-dialog-title" descriptionId="preview-dialog-description" initialFocusRef={previewCloseButton} returnFocusRef={previewTriggerButton} onClose={() => setPreviewOpen(false)}>
          <p className="eyebrow">Revisão privada</p>
          <h2 id="preview-dialog-title">Ver como aluno</h2>
          <p id="preview-dialog-description">É o que o aluno veria com o rascunho salvo agora. Nada aqui está publicado.</p>
          <PrivatePreviewPanel state={previewState} preview={preview} detail={detail} dirty={dirty} onReload={() => { setPreviewState("idle"); setPreview(null); setPreviewRetry((value) => value + 1); }}/>
          <div className="inline-actions">
            <button ref={previewCloseButton} className="secondary-button" type="button" onClick={() => setPreviewOpen(false)}>Fechar</button>
          </div>
        </AccessibleDialog>
      ) : null}

      {archiveDialogOpen ? (
        <AccessibleDialog titleId="archive-dialog-title" descriptionId="archive-dialog-description" initialFocusRef={archiveCancelButton} returnFocusRef={archiveTriggerButton} onClose={() => setArchiveDialogOpen(false)}>
            <p className="eyebrow">Ação irreversível</p><h2 id="archive-dialog-title">Encerrar as vendas deste produto?</h2>
            <p id="archive-dialog-description">Encerrar é definitivo: o produto sai da vitrine e não volta a rascunho nem pode ser vendido de novo. Para apenas editar, use &quot;Despublicar para editar&quot;.</p>
            <div className="dialog-actions"><button ref={archiveCancelButton} className="secondary-button" type="button" onClick={() => setArchiveDialogOpen(false)}>Cancelar</button><button className="danger-button" type="button" onClick={() => { setArchiveDialogOpen(false); void transitionOffer("archived"); }}>Arquivar definitivamente</button></div>
        </AccessibleDialog>
      ) : null}

      {pendingSection ? (
        <AccessibleDialog
          titleId="section-leave-title"
          descriptionId="section-leave-description"
          initialFocusRef={sectionKeepButton}
          onClose={() => setPendingSection(null)}
        >
          <p className="eyebrow">Alterações não salvas</p>
          <h2 id="section-leave-title">Guardar antes de sair desta etapa?</h2>
          <p id="section-leave-description">
            Você editou algo aqui e ainda não salvou. Pode guardar agora e seguir, ou descartar
            o que mudou nesta etapa.
          </p>
          <div className="dialog-actions">
            <button ref={sectionKeepButton} type="button" className="secondary-button" onClick={() => setPendingSection(null)}>
              Continuar editando
            </button>
            <button type="button" className="primary-button" disabled={saveState === "saving"} onClick={() => void saveAndGo()}>
              {saveState === "saving" ? "Salvando…" : "Salvar e continuar"}
            </button>
            <button type="button" className="danger-button" onClick={discardAndGo}>
              Descartar
            </button>
          </div>
        </AccessibleDialog>
      ) : null}

      {leaveDialogOpen ? (
        <AccessibleDialog titleId="editor-leave-title" descriptionId="editor-leave-description" initialFocusRef={leaveCancelButton} returnFocusRef={listLink} onClose={closeLeaveDialog}>
            <p className="eyebrow">Alterações não salvas</p><h2 id="editor-leave-title">Voltar para produtos?</h2><p id="editor-leave-description">As alterações feitas neste dispositivo serão descartadas.</p>
            <div className="dialog-actions"><button ref={leaveCancelButton} className="secondary-button" type="button" onClick={closeLeaveDialog}>Continuar editando</button><button className="danger-button" type="button" onClick={() => { discardLocalEdits(); allowUnsavedNavigation(); router.push(`/products?seller=${encodeURIComponent(sellerId)}`); }}>Descartar e voltar</button></div>
        </AccessibleDialog>
      ) : null}
    </section>
  );
}

function EditorLoadState({ state, sellerId, onRetry }: { state: LoadState; sellerId: string; onRetry: () => void }) {
  const messages: Record<Exclude<LoadState, "ready">, string> = {
    loading: "Carregando produto…",
    unauthorized: "Sua sessão expirou. Entre novamente.",
    forbidden: "Você não possui permissão ativa nesta loja.",
    not_found: "Produto não encontrado nesta loja.",
    unavailable: "A API está indisponível. Nenhum dado demonstrativo substituiu o produto real.",
    error: "Não foi possível carregar o editor."
  };
  return <section className="editor-load-state" aria-live="polite"><p className="eyebrow">Editor protegido</p><h1>{messages[state === "ready" ? "error" : state]}</h1><div className="error-actions"><Link className="secondary-button" href={`/products?seller=${encodeURIComponent(sellerId)}`}>Voltar aos produtos</Link>{state === "unavailable" || state === "error" ? <button className="primary-button" type="button" onClick={onRetry}>Tentar novamente</button> : null}{state === "unauthorized" ? <a className="primary-button" href="/auth/login">Entrar novamente</a> : null}</div></section>;
}

// `sanitize` é o que impede um campo numérico de aceitar texto: ele filtra a
// digitação em vez de deixar o valor entrar e recusá-lo no envio. Fica fora do
// spread de propriedades porque não é atributo de DOM.
function EditorField({ label, name, value, onChange, onBlur, multiline = false, invalid = false, help, sanitize, ...inputProps }: { label: string; name: keyof EditorFields; value: string; onChange: (value: string) => void; onBlur?: () => void; multiline?: boolean; invalid?: boolean; help?: string; sanitize?: (raw: string) => string; minLength?: number; maxLength?: number; required?: boolean; pattern?: string; inputMode?: "decimal" | "numeric" }) {
  const helpId = `${name}-help`;
  const errorId = `${name}-error`;
  const describedBy = [help ? helpId : "", invalid ? errorId : ""].filter(Boolean).join(" ") || undefined;
  const accept = (raw: string) => onChange(sanitize ? sanitize(raw) : raw);
  return <label className="editor-field">{label}{multiline ? <textarea id={name} name={name} value={value} onChange={(event) => accept(event.target.value)} onBlur={onBlur} rows={5} autoComplete="off" aria-invalid={invalid} aria-describedby={describedBy} {...inputProps}/> : <input id={name} name={name} value={value} onChange={(event) => accept(event.target.value)} onBlur={onBlur} autoComplete="off" spellCheck={name === "slug" ? false : undefined} autoCapitalize={name === "slug" ? "none" : undefined} aria-invalid={invalid} aria-describedby={describedBy} {...inputProps}/>} {help ? <span id={helpId}>{help}</span> : null}{invalid ? <span id={errorId} className="form-error" role="alert">{editorFieldError(name)}</span> : null}</label>;
}

function editorFieldError(field: string) {
  if (field === "slug") return "Use apenas letras minúsculas, números e hífens.";
  if (field === "offer_price" || field === "offer_unit_amount_minor") return "Informe um preço válido, maior que zero para ativar vendas.";
  if (field === "offer_title") return "Informe um nome de checkout com pelo menos 2 caracteres.";
  return "Corrija este campo e tente novamente.";
}

function editorFieldLabel(field: string) {
  const labels: Record<string, string> = {
    product_title: "nome do produto",
    slug: "endereço do produto",
    product_description: "descrição curta",
    course_title: "nome do conteúdo",
    offer_title: "nome no checkout",
    offer_summary: "descrição no checkout",
    offer_price: "preço",
    offer_unit_amount_minor: "preço",
    offer_billing_period: "tipo de cobrança"
  };
  return labels[field] ?? "formulário";
}

function PrivatePreviewPanel({ state, preview, detail, dirty, onReload }: { state: PreviewState; preview: PrivatePreview | null; detail: ProductDetail; dirty: boolean; onReload: () => void }) {
  if (dirty) return <section className="private-preview preview-blocked"><span className="private-badge">Prévia privada</span><h2>Salve para atualizar a prévia</h2><p>Esta tela nunca mistura alterações locais ainda não confirmadas com a versão salva.</p></section>;
  if (state === "loading" || state === "idle") return <p className="empty-state" role="status">Montando prévia privada…</p>;
  if (state === "unavailable" || state === "error") return <section className="private-preview preview-blocked"><span className="private-badge">Prévia privada</span><h2>Prévia indisponível</h2><p>A versão salva permanece protegida. Tente carregar novamente.</p><button className="secondary-button" type="button" onClick={onReload}>Tentar novamente</button></section>;
  const product = preview?.product;
  const offer = preview?.offer;
  const courses = preview?.courses?.length
    ? preview.courses
    : preview?.course
      ? [{ title: preview.course.title, modules: [] }]
      : [];
  const modules = courses.flatMap((course) => course.modules ?? []);
  const lessons = modules.flatMap((module) => module.lessons ?? []);
  const materials = lessons.flatMap((lesson) => lesson.assets ?? []);
  return <section className="private-preview" aria-labelledby="private-preview-title"><div className="preview-context"><span className="private-badge">Prévia privada</span><span>{preview?.seller?.display_name ?? "Sua loja"}</span><span>Versão {preview?.draft_version ?? preview?.version ?? detail.draft_version}</span></div><div className="preview-hero"><p className="eyebrow">Ainda não publicado</p><h2 id="private-preview-title">{product?.title ?? detail.title}</h2><p>{product?.description || detail.description || "Descrição ainda não informada."}</p></div>{courses.length > 1 ? <div className="preview-course"><span>Curso incluído</span><strong>{preview?.course?.title ?? courses[0]?.title ?? detail.course.title}</strong></div> : null}<section className="preview-curriculum" aria-labelledby="preview-curriculum-title"><div><span>Conteúdo incluído</span><strong id="preview-curriculum-title">{modules.length} {modules.length === 1 ? "módulo" : "módulos"} · {lessons.length} {lessons.length === 1 ? "aula" : "aulas"} · {materials.length} {materials.length === 1 ? "material" : "materiais"}</strong></div>{modules.length ? <ul>{modules.map((module, moduleIndex) => <li key={module.id ?? `${module.title}-${moduleIndex}`}><strong>{module.title || `Módulo ${moduleIndex + 1}`}</strong><span>{(module.lessons ?? []).length} {(module.lessons ?? []).length === 1 ? "aula" : "aulas"}</span>{(module.lessons ?? []).flatMap((lesson, lessonIndex) => (lesson.assets ?? []).map((asset, assetIndex) => <small key={asset.id ?? `${lesson.title}-${lessonIndex}-${assetIndex}`}>{asset.title || "Material da aula"}</small>))}</li>)}</ul> : <p>O conteúdo ainda não tem módulos e aulas salvos.</p>}</section><div className="preview-offer"><span>Preço informativo</span><strong>{offer?.title || detail.offer.title || "Preço ainda não configurado"}</strong><p>{offer?.summary || detail.offer.summary || "Informe e salve o preço antes de publicar."}</p><button type="button" disabled>Compra indisponível nesta prévia</button></div></section>;
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
      courses: payload.snapshot.courses,
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
    seller_slug: value.seller_slug,
    slug: value.slug,
    title: value.title,
    description: value.description ?? "",
    status: value.status ?? "draft",
    draft_version: productVersion,
    course: { id: course.id, title: course.title ?? "", status: course.status ?? "draft", draft_version: courseVersion },
    courses: courses.map((item) => ({ id: item.id, title: item.title ?? "", status: item.status ?? "draft", draft_version: item.draft_version ?? 1 })),
    curriculum: {
      modules: Math.max(0, Number(value.curriculum?.modules ?? 0)),
      lessons: Math.max(0, Number(value.curriculum?.lessons ?? 0))
    },
    offer: { id: offer.id, title: offer.title ?? "", summary: offer.summary ?? "", status: offer.status ?? "draft", draft_version: offerVersion, unit_amount_minor: offer.unit_amount_minor, currency: offer.currency, billing_period: offer.billing_period },
    cover: normalizeCover(value.cover),
    publication: value.publication ?? null,
    has_unpublished_changes: value.has_unpublished_changes ?? value.readiness?.has_unpublished_changes,
    readiness: normalizeReadinessValue(value.readiness)
  };
}

/**
 * Uma capa mal formada vira ausência de capa, e não um `img` quebrado.
 *
 * A origem do endereço já foi conferida no servidor; o que sobra aqui é a
 * forma. Se faltar largura ou altura, a moldura colapsa e a lista pula de
 * tamanho enquanto a imagem carrega — a arte gerada é melhor do que isso.
 */
function normalizeCover(value: ProductCover | null | undefined): ProductCover | null {
  if (!value || typeof value.url !== "string" || !value.url) return null;
  const width = Number(value.width);
  const height = Number(value.height);
  if (!Number.isSafeInteger(width) || width < 1) return null;
  if (!Number.isSafeInteger(height) || height < 1) return null;
  return { url: value.url, media_type: String(value.media_type ?? ""), width, height };
}

function fieldsFromDetail(detail: ProductDetail): EditorFields {
  return {
    slug: detail.slug,
    product_title: detail.title,
    product_description: detail.description,
    course_title: detail.course.title,
    offer_title: detail.offer.title,
    offer_summary: detail.offer.summary,
    offer_price: priceInputFromMinor(detail.offer.unit_amount_minor),
    offer_billing_period: isOfferBillingPeriod(detail.offer.billing_period) ? detail.offer.billing_period : "one_time"
  };
}

function versionsFromDetail(detail: ProductDetail) {
  return { product: detail.draft_version, course: detail.course.draft_version, offer: detail.offer.draft_version };
}

function sameFields(left: EditorFields, right: EditorFields) {
  return left.slug === right.slug && left.product_title === right.product_title && left.product_description === right.product_description && left.course_title === right.course_title && left.offer_title === right.offer_title && left.offer_summary === right.offer_summary && left.offer_price === right.offer_price && left.offer_billing_period === right.offer_billing_period;
}

function saveMessage(state: SaveState, dirty: boolean) {
  if (state === "saving") return "Salvando rascunho…";
  if (state === "saved") return "Tudo salvo com segurança.";
  if (state === "saved_with_changes") return "Último envio salvo; há alterações mais recentes neste dispositivo.";
  if (state === "conflict") return "Conflito de versão. Suas alterações foram preservadas; recarregue antes de tentar novamente.";
  if (state === "rebasing") return "Recarregando a versão atual do servidor…";
  if (state === "rebased") return "Versão atualizada; suas alterações locais continuam prontas para novo envio.";
  if (state === "invalid") return "Revise o campo indicado. Os demais campos foram preservados.";
  if (state === "forbidden") return "Salvamento recusado. Sua sessão expirou ou esta conta não tem acesso a esta loja; entre novamente e confira qual loja está aberta.";
  if (state === "unavailable") return "A API não confirmou o estado final. Seus campos foram preservados; recarregue antes de outra tentativa.";
  if (state === "error") return "Não foi possível salvar. Seus campos continuam neste dispositivo.";
  return dirty ? "Alterações não salvas." : "Tudo salvo.";
}

function offerStatusLabel(status: string) {
  if (status === "active") return "ativas";
  if (status === "archived") return "encerradas";
  return "ainda não ativadas";
}

function readActionCsrf(action: "update" | "publish") {
  const prefix = `${process.env.NODE_ENV === "production" ? `__Host-appsell_product_${action}_csrf` : `appsell_product_${action}_csrf` }=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

const emptyFields: EditorFields = { slug: "", product_title: "", product_description: "", course_title: "", offer_title: "", offer_summary: "", offer_price: "", offer_billing_period: "one_time" };
