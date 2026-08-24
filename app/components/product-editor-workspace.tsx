"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, MouseEvent, useCallback, useEffect, useRef, useState } from "react";
import { AccessibleDialog } from "./accessible-dialog";
import { AccessLinksPanel } from "./access-links-panel";
import { CurriculumWorkspace } from "./curriculum-workspace";
import { allowUnsavedNavigation, useUnsavedChanges } from "./use-unsaved-changes";
import {
  formatOfferPrice,
  isOfferBillingPeriod,
  OFFER_BILLING_PERIODS,
  parsePriceInputToMinor,
  priceInputFromMinor
} from "@/lib/offer-price";

type EditorSection = "produto" | "curriculo" | "oferta" | "preview" | "publicar";
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
  publication?: { id?: string; version: number; published_at?: string } | null;
  has_unpublished_changes?: boolean;
  readiness?: { can_publish: boolean; has_unpublished_changes?: boolean; items: ReadinessItem[] } | null;
}

interface CurriculumSummary {
  modules: number;
  lessons: number;
}

type WorkflowStepState = "complete" | "current" | "pending";

interface EditorPayload {
  product?: ProductDetail;
  aggregate?: ProductDetail;
  course?: CourseDetail;
  offer?: OfferDetail | null;
  publication?: ProductDetail["publication"];
  latest_publication?: ProductDetail["publication"];
  csrf?: { update?: string; publish?: string; offer_status?: string; access_link_create?: string; access_link_revoke?: string; access_link_rotate?: string };
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

const editorSections: Array<{ id: EditorSection; label: string; hint: string }> = [
  { id: "produto", label: "Produto", hint: "Identidade e curso" },
  { id: "curriculo", label: "Currículo", hint: "Módulos e aulas" },
  { id: "oferta", label: "Oferta", hint: "Preço e disponibilidade" },
  { id: "preview", label: "Preview", hint: "Revisão privada" },
  { id: "publicar", label: "Publicar", hint: "Snapshot versionado" }
];

export function workflowState(
  detail: ProductDetail,
  curriculum: CurriculumSummary,
  editorDirty: boolean,
  previewState: PreviewState
): Record<EditorSection, WorkflowStepState> {
  const productComplete = Boolean(
    detail.title.trim() && detail.slug.trim() && detail.course.title.trim()
  );
  const curriculumComplete = curriculum.modules > 0 && curriculum.lessons > 0;
  const offerComplete = Boolean(detail.offer.id && detail.offer.title.trim());
  const hasPublishedSnapshot = Boolean(
    detail.publication && !(detail.has_unpublished_changes ?? detail.readiness?.has_unpublished_changes)
  );
  const previewComplete = !editorDirty && (previewState === "ready" || hasPublishedSnapshot);
  const publishComplete = Boolean(detail.publication && !(detail.has_unpublished_changes ?? detail.readiness?.has_unpublished_changes));
  return {
    produto: productComplete ? "complete" : "pending",
    curriculo: curriculumComplete ? "complete" : "pending",
    oferta: offerComplete ? "complete" : "pending",
    preview: previewComplete ? "complete" : "pending",
    publicar: publishComplete ? "complete" : "pending"
  };
}

function nextWorkflowStep(
  section: EditorSection,
  workflow: Record<EditorSection, WorkflowStepState>
) {
  const firstPending = editorSections.find((item) => workflow[item.id] !== "complete");
  if (!firstPending) return null;
  const currentPending = workflow[section] !== "complete";
  const target = currentPending ? section : firstPending.id;
  const details: Record<EditorSection, string> = {
    produto: "Confirme o nome, endereço, descrição e nome do curso; o salvamento do produto é independente da oferta.",
    curriculo: "Adicione pelo menos um módulo e uma aula para o curso ter conteúdo revisável.",
    oferta: "Configure e salve o nome da oferta. O preço prepara vendas, mas não bloqueia o salvamento editorial.",
    preview: "Salve o que mudou e abra a prévia para conferir o catálogo e o conteúdo incluído.",
    publicar: "Publique o catálogo e, nesta mesma etapa, escolha entre acesso gratuito ou vendas com checkout."
  };
  return { id: target, label: editorSections.find((item) => item.id === target)?.label ?? target, detail: details[target] };
}

export function ProductEditorWorkspace({ sellerId, productId }: { sellerId: string; productId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedSection = searchParams.get("section");
  const section: EditorSection = editorSections.some((item) => item.id === requestedSection) ? requestedSection as EditorSection : "produto";
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [detail, setDetail] = useState<ProductDetail | null>(null);
  const [csrf, setCsrf] = useState<{ update: string; publish: string; offerStatus: string; accessLinkCreate: string; accessLinkRevoke: string; accessLinkRotate: string }>({ update: "", publish: "", offerStatus: "", accessLinkCreate: "", accessLinkRevoke: "", accessLinkRotate: "" });
  const [fields, setFields] = useState<EditorFields>(emptyFields);
  const fieldsRef = useRef(fields);
  const [savedFields, setSavedFields] = useState<EditorFields>(emptyFields);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [invalidField, setInvalidField] = useState("");
  const [previewState, setPreviewState] = useState<PreviewState>("idle");
  const [preview, setPreview] = useState<PrivatePreview | null>(null);
  const [previewRetry, setPreviewRetry] = useState(0);
  const [publishState, setPublishState] = useState<PublishState>("idle");
  const [offerStatusState, setOfferStatusState] = useState<OfferStatusState>("idle");
  const [publishDialogOpen, setPublishDialogOpen] = useState(false);
  const [leaveDialogOpen, setLeaveDialogOpen] = useState(false);
  const [archiveDialogOpen, setArchiveDialogOpen] = useState(false);
  const [curriculumDirty, setCurriculumDirty] = useState(false);
  const [curriculumBusy, setCurriculumBusy] = useState(false);
  const [curriculumSummary, setCurriculumSummary] = useState<CurriculumSummary>({ modules: 0, lessons: 0 });
  const [assetNavigationWarning, setAssetNavigationWarning] = useState(false);
  const updateIdempotencyKey = useRef<string | null>(null);
  const publishIdempotencyKey = useRef<string | null>(null);
  const [publishAccessMode, setPublishAccessMode] = useState<"free" | "paid">("free");
  const [payoutReady, setPayoutReady] = useState<boolean | null>(null);
  const offerStatusIdempotencyKey = useRef<{ transition: string; key: string } | null>(null);
  const offerId = useRef<string | null>(null);
  const formElement = useRef<HTMLFormElement>(null);
  const publishCancelButton = useRef<HTMLButtonElement>(null);
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

  useUnsavedChanges(editorDirty);
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
      setCurriculumSummary(nextDetail.curriculum);
      setCsrf({ update: payload.csrf?.update ?? readActionCsrf("update"), publish: payload.csrf?.publish ?? readActionCsrf("publish"), offerStatus: payload.csrf?.offer_status ?? "", accessLinkCreate: payload.csrf?.access_link_create ?? "", accessLinkRevoke: payload.csrf?.access_link_revoke ?? "", accessLinkRotate: payload.csrf?.access_link_rotate ?? "" });
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
    const savingOffer = section === "oferta";
    // Guarda de defesa: o form já é desabilitado por offerLocked, mas o submit
    // pode chegar por Enter durante uma transição em voo — não salvar nesse caso.
    if (savingOffer && (detail.offer.status !== "draft" || offerStatusState === "transitioning")) return;
    const submitted = fields;
    const submittedUnitAmountMinor = savingOffer ? parsePriceInputToMinor(submitted.offer_price) : null;
    if (savingOffer && submittedUnitAmountMinor === null) {
      setInvalidField("offer_price");
      setSaveState("invalid");
      const input = formElement.current?.elements.namedItem("offer_price");
      if (input instanceof HTMLElement) input.focus();
      return;
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
        return;
      }
      const refreshResponse = await fetch(`/api/sellers/${sellerId}/products/${productId}`, { cache: "no-store", signal: controller.signal });
      if (!refreshResponse.ok) return setSaveState("unavailable");
      const payload = await refreshResponse.json() as EditorPayload & ProductDetail;
      const updated = normalizeEditorPayload(payload);
      if (!updated) return setSaveState("error");
      updateIdempotencyKey.current = null;
      setDetail({ ...updated, readiness: payload.readiness ?? updated.readiness });
      setCurriculumSummary(updated.curriculum);
      setCsrf((current) => ({ ...current, update: payload.csrf?.update ?? current.update, publish: payload.csrf?.publish ?? current.publish, offerStatus: payload.csrf?.offer_status ?? current.offerStatus, accessLinkCreate: payload.csrf?.access_link_create ?? current.accessLinkCreate, accessLinkRevoke: payload.csrf?.access_link_revoke ?? current.accessLinkRevoke, accessLinkRotate: payload.csrf?.access_link_rotate ?? current.accessLinkRotate }));
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
      setCurriculumSummary(latest.curriculum);
      setSavedFields(fieldsFromDetail(latest));
      setCsrf((current) => ({ ...current, update: payload.csrf?.update ?? current.update, publish: payload.csrf?.publish ?? current.publish, offerStatus: payload.csrf?.offer_status ?? current.offerStatus, accessLinkCreate: payload.csrf?.access_link_create ?? current.accessLinkCreate, accessLinkRevoke: payload.csrf?.access_link_revoke ?? current.accessLinkRevoke, accessLinkRotate: payload.csrf?.access_link_rotate ?? current.accessLinkRotate }));
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

  // Asked only when the seller opens the access decision. Nothing else on the
  // page depends on it, so there is no reason to spend a request on mount.
  useEffect(() => {
    if (!publishDialogOpen || payoutReady !== null) return;
    let active = true;
    void fetch(`/api/sellers/${encodeURIComponent(sellerId)}/payout-account`, { cache: "no-store" })
      .then(async (response) => {
        if (!active) return;
        if (!response.ok) return setPayoutReady(false);
        const payload = await response.json() as {
          payout_account?: {
            charges_enabled?: boolean;
            payouts_enabled?: boolean;
            disabled_reason?: string | null;
          } | null;
        };
        const account = payload.payout_account;
        setPayoutReady(Boolean(
          account?.charges_enabled && account?.payouts_enabled && !account?.disabled_reason
        ));
      })
      .catch(() => {
        if (active) setPayoutReady(false);
      });
    return () => {
      active = false;
    };
  }, [payoutReady, publishDialogOpen, sellerId]);

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
    if (curriculumBusy) {
      event.preventDefault();
      setAssetNavigationWarning(true);
      return;
    }
    if (!editorDirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    setLeaveDialogOpen(true);
  }

  function requestSectionNavigation(event: MouseEvent<HTMLAnchorElement>, target: EditorSection) {
    if (target !== section && curriculumBusy) {
      event.preventDefault();
      setAssetNavigationWarning(true);
      return;
    }
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
  const paidAvailable = payoutReady === true && (detail.offer.unit_amount_minor ?? 0) > 0;
  const workflow = workflowState(detail, curriculumSummary, editorDirty, previewState);
  const nextStep = nextWorkflowStep(section, workflow);

  return (
    <section className="product-editor" aria-labelledby="product-editor-title">
      <header className="editor-header">
        <div className="editor-breadcrumb"><Link ref={listLink} href={`/products?seller=${encodeURIComponent(sellerId)}`} onClick={requestListNavigation}>Produtos</Link><span aria-hidden="true">/</span><span aria-current="page">{detail.title}</span></div>
        <div className="editor-title-row">
          <div><p className="eyebrow">Espaço selecionado · {shortId(sellerId)}</p><h1 id="product-editor-title">{detail.title}</h1><p>@{detail.slug} · curso {detail.course.title}</p></div>
          <span className={`status status-${detail.status}`}>{detail.status === "published" ? `Publicado${publicationVersion ? ` v${publicationVersion}` : ""}` : detail.status === "draft" ? "Rascunho privado" : detail.status === "unpublished" ? "Despublicado" : "Arquivado"}</span>
        </div>
        <div className={`save-indicator save-${saveState}`}>
          <span role="status" aria-live="polite" aria-atomic="true">{saveMessage(saveState, dirty)}</span>
          {saveState === "conflict" || saveState === "rebasing" ? <button className="secondary-button" type="button" disabled={saveState === "rebasing"} onClick={() => void rebaseFromServer()}>{saveState === "rebasing" ? "Recarregando…" : "Recarregar versão do servidor"}</button> : null}
        </div>
        {saveState === "invalid" ? <div ref={saveErrorSummary} className="form-error-summary" role="alert" tabIndex={-1}><strong>Não foi salvo.</strong><span>Corrija o campo destacado{invalidField && invalidField !== "form" ? ` (${editorFieldLabel(invalidField)})` : ""} e tente novamente.</span></div> : null}
      </header>

      <nav className="editor-section-nav" aria-label="Etapas do produto">
        {editorSections.map((item) => {
          const active = section === item.id;
          const state = workflow[item.id];
          const stateLabel = active && state === "complete" ? "Concluída; etapa atual" : state === "complete" ? "Concluída" : active ? "Etapa atual; pendente" : "Pendente";
          return <Link key={item.id} href={`?section=${item.id}`} replace scroll={false} className={`${active ? "active " : ""}step-${state}`} aria-current={active ? "step" : undefined} onClick={(event) => requestSectionNavigation(event, item.id)}><span className="editor-step-heading"><strong>{item.label}</strong><span className="editor-step-mark" aria-hidden="true">{state === "complete" ? "✓" : ""}</span></span><span>{item.hint}</span><span className="sr-only">{stateLabel}</span></Link>;
        })}
      </nav>
      {nextStep ? <div className="editor-next-step" role="status"><div><strong>{nextStep.id === section ? "Próximo requisito desta etapa" : `Próximo passo: ${nextStep.label}`}</strong><p>{nextStep.detail}</p></div>{nextStep.id !== section ? <Link className="secondary-button" href={`?section=${nextStep.id}`} replace scroll={false} onClick={(event) => requestSectionNavigation(event, nextStep.id)}>Ir para {nextStep.label}</Link> : null}</div> : null}
      {assetNavigationWarning ? <p className="form-note asset-navigation-warning" role="alert">O arquivo ainda está sendo enviado. Aguarde ou cancele o envio antes de sair do currículo.</p> : null}

      {section === "curriculo" ? <CurriculumWorkspace sellerId={sellerId} productId={productId} courseId={detail.course.id} courseTitle={detail.course.title} archived={detail.status === "archived"} onDirtyChange={handleCurriculumDirty} onBusyChange={handleCurriculumBusy} onSummaryChange={handleCurriculumSummary} onCommitted={handleCurriculumCommitted}/> : (
      <form ref={formElement} className="editor-form" onSubmit={saveDraft}>
        {section === "produto" ? (
          <fieldset disabled={detail.status === "archived"}>
            <legend>Identidade do produto</legend>
            <p className="section-intro">Edite o produto e o curso sem precisar configurar vendas. Uma publicação existente permanece estável até você publicar outra versão.</p>
            <EditorField label="Nome do produto" name="product_title" value={fields.product_title} onChange={(value) => updateField("product_title", value)} minLength={2} maxLength={160} required invalid={invalidField === "product_title"}/>
            <EditorField label="Endereço do produto" name="slug" value={fields.slug} onChange={(value) => updateField("slug", value)} minLength={3} maxLength={63} required invalid={invalidField === "slug"} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" help="Somente letras minúsculas, números e hífens."/>
            <EditorField label="Descrição curta" name="product_description" value={fields.product_description} onChange={(value) => updateField("product_description", value)} maxLength={2000} multiline invalid={invalidField === "product_description"}/>
            <EditorField label="Nome do curso inicial" name="course_title" value={fields.course_title} onChange={(value) => updateField("course_title", value)} minLength={2} maxLength={160} required invalid={invalidField === "course_title"}/>
          </fieldset>
        ) : null}

        {section === "oferta" ? (
          <>
            <fieldset disabled={detail.status === "archived" || offerLocked}>
              <legend>Oferta e preço</legend>
              {detail.offer.status === "draft"
                ? <div className="billing-boundary" role="note"><strong>{!localPriceIsPositive ? "Defina um preço maior que zero" : priceDirty ? "Salve o preço antes de ativar vendas" : dirty ? "Salve as alterações antes de ativar vendas" : detail.publication ? "Preço salvo; oferta pronta para vender" : "Preço salvo; catálogo pronto para publicar"}</strong><p>{!localPriceIsPositive ? `Informe o valor em ${offerCurrencyLabel}. Sem preço maior que zero, as vendas permanecem desativadas.` : priceDirty ? `O valor em ${offerCurrencyLabel} só será considerado no checkout depois que o rascunho for salvo.` : dirty ? "Salve as alterações pendentes antes de ativar vendas." : detail.publication ? `O preço salvo em ${offerCurrencyLabel} será cobrado quando as vendas estiverem ativas.` : "Publique o catálogo antes de ativar vendas; esta etapa não cria checkout."}</p></div>
                : <div className="billing-boundary" role="note"><strong>{detail.offer.status === "active" ? "Oferta ativa" : "Oferta arquivada"}</strong><p>{detail.offer.status === "active" ? "Para editar termos ou preço, despublique a oferta primeiro." : "Uma oferta arquivada é definitiva e não aparece na vitrine."}</p></div>}
              <EditorField label="Nome da oferta" name="offer_title" value={fields.offer_title} onChange={(value) => updateField("offer_title", value)} minLength={2} maxLength={120} required invalid={invalidField === "offer_title"}/>
              <EditorField label="Resumo da oferta" name="offer_summary" value={fields.offer_summary} onChange={(value) => updateField("offer_summary", value)} maxLength={500} multiline invalid={invalidField === "offer_summary"}/>
              <EditorField label={`Preço (${offerCurrencyLabel})`} name="offer_price" value={fields.offer_price} onChange={(value) => updateField("offer_price", value)} maxLength={12} invalid={invalidField === "offer_price" || invalidField === "offer_unit_amount_minor"} help="Use vírgula para centavos, ex.: 49,90. Deixe vazio para manter sem preço."/>
              <label className="editor-field">Cobrança
                <select id="offer_billing_period" name="offer_billing_period" value={fields.offer_billing_period} onChange={(event) => updateField("offer_billing_period", event.target.value)} aria-invalid={invalidField === "offer_billing_period"}>
                  {OFFER_BILLING_PERIODS.map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
                </select>
              </label>
            </fieldset>
            <section className="offer-status-panel" aria-labelledby="offer-status-title">
              <p className="eyebrow">Disponibilidade na vitrine</p>
              <h2 id="offer-status-title">Status da oferta: {offerStatusLabel(detail.offer.status)}</h2>
              {detail.offer.status === "draft" ? <p>{!detail.publication ? "Publique o catálogo antes de escolher o acesso. A ativação das vendas fica em Publicar." : "A decisão entre acesso gratuito e vendas com checkout fica em Publicar."}</p> : null}
              {detail.offer.status === "active" ? <p>Oferta ativa por {detail.offer.unit_amount_minor ? formatOfferPrice(detail.offer.unit_amount_minor, detail.offer.currency) : "preço não informado"}. Para editar termos ou preço, volte a rascunho em Publicar.</p> : null}
              {detail.offer.status === "archived" ? <p>Oferta arquivada. Crie um novo produto para vender novamente.</p> : null}
              <p className="form-note" role="status">A alteração do acesso é feita na etapa Publicar, depois que o rascunho estiver salvo.</p>
            </section>
          </>
        ) : null}

        {section === "preview" ? <PrivatePreviewPanel state={previewState} preview={preview} detail={detail} dirty={dirty} onReload={() => { setPreviewState("idle"); setPreview(null); setPreviewRetry((value) => value + 1); }}/> : null}

        {section === "publicar" ? (
          <section className="publish-panel" aria-labelledby="publish-panel-title">
            <p className="eyebrow">Validação de segurança</p><h2 id="publish-panel-title">Publicar catálogo</h2>
            <p>A publicação cria uma versão fixa do catálogo com produto, oferta informativa, módulos, aulas e PDFs já verificados. Depois, escolha aqui se o acesso será gratuito ou passará pelo checkout.</p>
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
                {detail.publication && !editorDirty && (detail.offer.unit_amount_minor ?? 0) <= 0 ? <p className="form-note">Para ativar vendas, informe e salve um preço maior que zero na etapa Oferta.</p> : null}
                {editorDirty ? <p className="form-note">Salve as alterações antes de escolher o modo de acesso.</p> : null}
                <div className="offer-status-actions"><button className="primary-button" type="button" disabled={!canActivateSales} onClick={() => void transitionOffer("active")}>{offerStatusState === "transitioning" ? "Ativando vendas…" : "Ativar oferta e usar checkout"}</button></div>
              </> : null}
              {detail.offer.status === "active" ? <div className="offer-status-actions">
                <button className="secondary-button" type="button" disabled={editorDirty || offerStatusLocked} onClick={() => void transitionOffer("draft")}>{offerStatusState === "transitioning" ? "Aplicando…" : "Desativar vendas e voltar ao gratuito"}</button>
                <button ref={archiveTriggerButton} className="secondary-button" type="button" disabled={editorDirty || offerStatusLocked} onClick={() => setArchiveDialogOpen(true)}>Arquivar oferta</button>
              </div> : null}
              <div className="form-feedback" aria-live="polite" aria-atomic="true">
                {offerStatusState === "done" ? <p className="form-success">Modo de acesso atualizado.</p> : null}
                {offerStatusState === "conflict" ? <><p className="form-error">A oferta mudou em outra sessão. Atualize os dados antes de tentar novamente.</p><button className="secondary-button" type="button" onClick={() => void reloadOfferAfterConflict()}>Atualizar dados da oferta</button></> : null}
                {offerStatusState === "reloading" ? <p className="form-note">Atualizando os dados da oferta…</p> : null}
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
            <span aria-live="polite">{dirty ? "Alterações não salvas" : "Rascunho salvo"}</span>
            <button className="primary-button" type="submit" disabled={!dirty || saveState === "saving" || detail.status === "archived" || (section === "oferta" && offerLocked)}>{saveState === "saving" ? "Salvando…" : "Salvar rascunho"}</button>
          </div>
        ) : null}
      </form>
      )}

      {publishDialogOpen ? (
        <AccessibleDialog titleId="publish-dialog-title" descriptionId="publish-dialog-description" initialFocusRef={publishCancelButton} returnFocusRef={publishTriggerButton} onClose={closePublishDialog}>
            <p className="eyebrow">Colocar no ar</p><h2 id="publish-dialog-title">Colocar {detail.title} no ar?</h2>
            <p id="publish-dialog-description">Conteúdo, materiais verificados e a oferta ficam congelados nesta versão. Escolha agora como as pessoas entram.</p>
            <div className="access-mode-choice" role="radiogroup" aria-label="Modo de acesso">
              <label className={publishAccessMode === "free" ? "access-mode-option selected" : "access-mode-option"}>
                <input type="radio" name="access_mode" value="free" checked={publishAccessMode === "free"} onChange={() => setPublishAccessMode("free")}/>
                <span><strong>Acesso gratuito</strong><span>Você distribui por link. Cada pessoa entra com o próprio cadastro.</span></span>
              </label>
              <label className={`${publishAccessMode === "paid" ? "access-mode-option selected" : "access-mode-option"}${paidAvailable ? "" : " disabled"}`}>
                <input type="radio" name="access_mode" value="paid" checked={publishAccessMode === "paid"} disabled={!paidAvailable} onChange={() => setPublishAccessMode("paid")}/>
                <span>
                  <strong>Vender com checkout</strong>
                  <span>{payoutReady === false
                    ? "Indisponível: configure seus recebimentos para poder cobrar."
                    : (detail.offer.unit_amount_minor ?? 0) <= 0
                      ? "Indisponível: salve um preço maior que zero na etapa Oferta."
                      : `O link leva ao pagamento de ${formatOfferPrice(detail.offer.unit_amount_minor ?? 0, detail.offer.currency)} antes de liberar o conteúdo.`}</span>
                  {payoutReady === false ? <Link className="access-mode-fix" href="/payouts">Configurar recebimentos</Link> : null}
                </span>
              </label>
            </div>
            <div className="dialog-actions"><button ref={publishCancelButton} className="secondary-button" type="button" onClick={closePublishDialog}>Revisar novamente</button><button className="primary-button commercial-button" type="button" onClick={() => void publish(publishAccessMode)}>{publishAccessMode === "paid" ? "Colocar no ar e vender" : "Colocar no ar"}</button></div>
        </AccessibleDialog>
      ) : null}

      {archiveDialogOpen ? (
        <AccessibleDialog titleId="archive-dialog-title" descriptionId="archive-dialog-description" initialFocusRef={archiveCancelButton} returnFocusRef={archiveTriggerButton} onClose={() => setArchiveDialogOpen(false)}>
            <p className="eyebrow">Ação irreversível</p><h2 id="archive-dialog-title">Arquivar esta oferta?</h2>
            <p id="archive-dialog-description">Arquivar é definitivo: a oferta sai da vitrine e não pode voltar a rascunho nem ser ativada de novo. Para apenas editar, use &quot;Despublicar para editar&quot;.</p>
            <div className="dialog-actions"><button ref={archiveCancelButton} className="secondary-button" type="button" onClick={() => setArchiveDialogOpen(false)}>Cancelar</button><button className="danger-button" type="button" onClick={() => { setArchiveDialogOpen(false); void transitionOffer("archived"); }}>Arquivar definitivamente</button></div>
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
    forbidden: "Você não possui permissão ativa neste espaço.",
    not_found: "Produto não encontrado neste espaço.",
    unavailable: "A API está indisponível. Nenhum dado demonstrativo substituiu o produto real.",
    error: "Não foi possível carregar o editor."
  };
  return <section className="editor-load-state" aria-live="polite"><p className="eyebrow">Editor protegido</p><h1>{messages[state === "ready" ? "error" : state]}</h1><div className="error-actions"><Link className="secondary-button" href={`/products?seller=${encodeURIComponent(sellerId)}`}>Voltar aos produtos</Link>{state === "unavailable" || state === "error" ? <button className="primary-button" type="button" onClick={onRetry}>Tentar novamente</button> : null}{state === "unauthorized" ? <a className="primary-button" href="/auth/login">Entrar novamente</a> : null}</div></section>;
}

function EditorField({ label, name, value, onChange, multiline = false, invalid = false, help, ...inputProps }: { label: string; name: keyof EditorFields; value: string; onChange: (value: string) => void; multiline?: boolean; invalid?: boolean; help?: string; minLength?: number; maxLength?: number; required?: boolean; pattern?: string }) {
  const helpId = `${name}-help`;
  const errorId = `${name}-error`;
  const describedBy = [help ? helpId : "", invalid ? errorId : ""].filter(Boolean).join(" ") || undefined;
  return <label className="editor-field">{label}{multiline ? <textarea id={name} name={name} value={value} onChange={(event) => onChange(event.target.value)} rows={5} autoComplete="off" aria-invalid={invalid} aria-describedby={describedBy} {...inputProps}/> : <input id={name} name={name} value={value} onChange={(event) => onChange(event.target.value)} autoComplete="off" spellCheck={name === "slug" ? false : undefined} autoCapitalize={name === "slug" ? "none" : undefined} aria-invalid={invalid} aria-describedby={describedBy} {...inputProps}/>} {help ? <span id={helpId}>{help}</span> : null}{invalid ? <span id={errorId} className="form-error" role="alert">{editorFieldError(name)}</span> : null}</label>;
}

function editorFieldError(field: string) {
  if (field === "slug") return "Use apenas letras minúsculas, números e hífens, sem espaços.";
  if (field === "offer_price" || field === "offer_unit_amount_minor") return "Informe um preço válido, maior que zero para ativar vendas.";
  if (field === "offer_title") return "Informe um nome de oferta com pelo menos 2 caracteres.";
  return "Corrija este campo e tente novamente.";
}

function editorFieldLabel(field: string) {
  const labels: Record<string, string> = {
    product_title: "nome do produto",
    slug: "endereço do produto",
    product_description: "descrição curta",
    course_title: "nome do curso",
    offer_title: "nome da oferta",
    offer_summary: "resumo da oferta",
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
  return <section className="private-preview" aria-labelledby="private-preview-title"><div className="preview-context"><span className="private-badge">Prévia privada</span><span>Espaço {preview?.seller?.display_name ?? shortId(detail.seller_organization_id ?? "")}</span><span>Versão {preview?.draft_version ?? preview?.version ?? detail.draft_version}</span></div><div className="preview-hero"><p className="eyebrow">Ainda não publicado</p><h2 id="private-preview-title">{product?.title ?? detail.title}</h2><p>{product?.description || detail.description || "Descrição ainda não informada."}</p></div><div className="preview-course"><span>Curso incluído</span><strong>{preview?.course?.title ?? courses[0]?.title ?? detail.course.title}</strong></div><section className="preview-curriculum" aria-labelledby="preview-curriculum-title"><div><span>Conteúdo incluído</span><strong id="preview-curriculum-title">{modules.length} {modules.length === 1 ? "módulo" : "módulos"} · {lessons.length} {lessons.length === 1 ? "aula" : "aulas"} · {materials.length} {materials.length === 1 ? "material" : "materiais"}</strong></div>{modules.length ? <ul>{modules.map((module, moduleIndex) => <li key={module.id ?? `${module.title}-${moduleIndex}`}><strong>{module.title || `Módulo ${moduleIndex + 1}`}</strong><span>{(module.lessons ?? []).length} {(module.lessons ?? []).length === 1 ? "aula" : "aulas"}</span>{(module.lessons ?? []).flatMap((lesson, lessonIndex) => (lesson.assets ?? []).map((asset, assetIndex) => <small key={asset.id ?? `${lesson.title}-${lessonIndex}-${assetIndex}`}>{asset.title || "Material da aula"}</small>))}</li>)}</ul> : <p>O currículo ainda não tem módulos e aulas salvos.</p>}</section><div className="preview-offer"><span>Oferta informativa</span><strong>{offer?.title || detail.offer.title || "Oferta ainda não configurada"}</strong><p>{offer?.summary || detail.offer.summary || "Configure e salve a oferta antes de publicar."}</p><button type="button" disabled>Compra indisponível nesta prévia</button></div></section>;
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
    publication: value.publication ?? null,
    has_unpublished_changes: value.has_unpublished_changes ?? value.readiness?.has_unpublished_changes,
    readiness: normalizeReadinessValue(value.readiness)
  };
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
  if (state === "saved") return "Rascunho salvo com segurança.";
  if (state === "saved_with_changes") return "Último envio salvo; há alterações mais recentes neste dispositivo.";
  if (state === "conflict") return "Conflito de versão. Suas alterações foram preservadas; recarregue antes de tentar novamente.";
  if (state === "rebasing") return "Recarregando a versão atual do servidor…";
  if (state === "rebased") return "Versão atualizada; suas alterações locais continuam prontas para novo envio.";
  if (state === "invalid") return "Revise o campo indicado. Os demais campos foram preservados.";
  if (state === "forbidden") return "Salvamento recusado. Sua sessão expirou ou esta conta não tem acesso a este espaço; entre novamente e confira o tenant.";
  if (state === "unavailable") return "A API não confirmou o estado final. Seus campos foram preservados; recarregue antes de outra tentativa.";
  if (state === "error") return "Não foi possível salvar. Seus campos continuam neste dispositivo.";
  return dirty ? "Alterações não salvas." : "Rascunho sincronizado.";
}

function shortId(value: string) {
  return value ? `${value.slice(0, 8)}…` : "não informado";
}

function offerStatusLabel(status: string) {
  if (status === "active") return "ativa";
  if (status === "archived") return "arquivada";
  return "rascunho";
}

function readActionCsrf(action: "update" | "publish") {
  const prefix = `${process.env.NODE_ENV === "production" ? `__Host-appsell_product_${action}_csrf` : `appsell_product_${action}_csrf` }=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

const emptyFields: EditorFields = { slug: "", product_title: "", product_description: "", course_title: "", offer_title: "", offer_summary: "", offer_price: "", offer_billing_period: "one_time" };
