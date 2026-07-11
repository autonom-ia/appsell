"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

interface Seller {
  id: string;
  slug: string;
  display_name: string;
  role: string;
}

interface ProductDraft {
  id: string;
  slug: string;
  title: string;
  description: string;
  status: "draft";
  created_at: string;
  course?: { id: string; title: string; status: "draft" } | null;
  course_id?: string;
  course_title?: string;
}

type LoadState = "loading" | "idle" | "unauthorized" | "unavailable" | "error";
type SaveState = "idle" | "saving" | "conflict" | "forbidden" | "invalid" | "unavailable" | "error";

export function ProductDraftWorkspace() {
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [selectedSellerId, setSelectedSellerId] = useState("");
  const [products, setProducts] = useState<ProductDraft[]>([]);
  const [sellerState, setSellerState] = useState<LoadState>("loading");
  const [catalogState, setCatalogState] = useState<LoadState>("idle");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [invalidField, setInvalidField] = useState("");
  const [formDirty, setFormDirty] = useState(false);
  const [pendingSellerId, setPendingSellerId] = useState("");
  const idempotencyKey = useRef<string | null>(null);
  const selectedSellerIdRef = useRef("");
  const catalogRequest = useRef<{ controller: AbortController; version: number } | null>(null);
  const catalogVersion = useRef(0);

  const loadProducts = useCallback(async (sellerId: string) => {
    catalogRequest.current?.controller.abort();
    const version = ++catalogVersion.current;
    if (!sellerId) {
      setProducts([]);
      setCatalogState("idle");
      catalogRequest.current = null;
      return;
    }
    setCatalogState("loading");
    const controller = new AbortController();
    catalogRequest.current = { controller, version };
    const timeout = setTimeout(() => controller.abort(), 5_000);
    try {
      const response = await fetch(`/api/sellers/${sellerId}/products`, {
        cache: "no-store",
        signal: controller.signal
      });
      if (catalogRequest.current?.version !== version) return;
      if (response.status === 401) return setCatalogState("unauthorized");
      if (response.status === 503) return setCatalogState("unavailable");
      if (!response.ok) return setCatalogState("error");
      const payload = await response.json() as { products?: ProductDraft[] };
      setProducts(Array.isArray(payload.products) ? payload.products : []);
      setCatalogState("idle");
    } catch {
      if (catalogRequest.current?.version !== version) return;
      setCatalogState("unavailable");
    } finally {
      clearTimeout(timeout);
      if (catalogRequest.current?.version === version) catalogRequest.current = null;
    }
  }, []);

  useEffect(() => {
    let active = true;
    const controllers = new Set<AbortController>();
    const refresh = () => {
      const controller = new AbortController();
      controllers.add(controller);
      setSellerState("loading");
      const timeout = setTimeout(() => controller.abort(), 5_000);
      void fetch("/api/sellers", { cache: "no-store", signal: controller.signal })
        .then(async (response) => {
          if (response.status === 401) return setSellerState("unauthorized");
          if (response.status === 503) return setSellerState("unavailable");
          if (!response.ok) return setSellerState("error");
          const payload = await response.json() as { sellers?: Seller[] };
          if (!active) return;
          setSellers(Array.isArray(payload.sellers) ? payload.sellers : []);
          setSellerState("idle");
        })
        .catch(() => {
          if (active) setSellerState("unavailable");
        })
        .finally(() => {
          clearTimeout(timeout);
          controllers.delete(controller);
        });
    };
    refresh();
    window.addEventListener("appsell:sellers-changed", refresh);
    return () => {
      active = false;
      window.removeEventListener("appsell:sellers-changed", refresh);
      controllers.forEach((controller) => controller.abort());
      catalogRequest.current?.controller.abort();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedSellerId = selectedSellerIdRef.current;
    if (!submittedSellerId) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const csrfCookie = process.env.NODE_ENV === "production"
      ? "__Host-appsell_product_csrf"
      : "appsell_product_csrf";
    data.set("_csrf", readCookie(csrfCookie));
    idempotencyKey.current ??= crypto.randomUUID();
    data.set("idempotency_key", idempotencyKey.current);
    setInvalidField("");
    setSaveState("saving");
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8_000);
    try {
      const response = await fetch(`/api/sellers/${submittedSellerId}/products`, {
        method: "POST",
        body: data,
        signal: controller.signal
      });
      if (selectedSellerIdRef.current !== submittedSellerId) return;
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) setSaveState("forbidden");
        else if (response.status === 409) setSaveState("conflict");
        else if (response.status === 422) {
          const payload = await response.json().catch(() => null) as { field?: string } | null;
          const field = payload?.field ?? "form";
          setInvalidField(field);
          setSaveState("invalid");
          const input = form.elements.namedItem(field);
          if (input instanceof HTMLElement) input.focus();
        } else if (response.status === 503) setSaveState("unavailable");
        else setSaveState("error");
        return;
      }
      idempotencyKey.current = null;
      form.reset();
      setFormDirty(false);
      setSaveState("idle");
      await loadProducts(submittedSellerId);
    } catch {
      if (selectedSellerIdRef.current !== submittedSellerId) return;
      setSaveState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  const selectedSeller = sellers?.find((seller) => seller.id === selectedSellerId) ?? null;
  const pendingSeller = sellers?.find((seller) => seller.id === pendingSellerId) ?? null;

  function switchSeller(sellerId: string) {
    selectedSellerIdRef.current = sellerId;
    setSelectedSellerId(sellerId);
    setPendingSellerId("");
    setFormDirty(false);
    setSaveState("idle");
    setInvalidField("");
    idempotencyKey.current = null;
    void loadProducts(sellerId);
  }

  function isInvalid(field: string) {
    return saveState === "invalid" && invalidField === field;
  }

  return (
    <section className="catalog-workspace" aria-labelledby="catalog-workspace-title">
      <div className="catalog-workspace-heading">
        <div>
          <p className="eyebrow">Catálogo persistente</p>
          <h1 id="catalog-workspace-title">Produtos</h1>
          <p>Crie o produto e o primeiro curso juntos. Tudo permanece privado até uma publicação futura.</p>
        </div>
        <span className="private-badge">Rascunhos privados</span>
      </div>

      <div className="tenant-picker">
        <label htmlFor="seller-context">Espaço de vendas</label>
        <select
          id="seller-context"
          value={selectedSellerId}
          disabled={sellerState === "loading" || saveState === "saving" || !sellers?.length}
          onChange={(event) => {
            const sellerId = event.target.value;
            if (formDirty && sellerId !== selectedSellerId) {
              setPendingSellerId(sellerId);
              return;
            }
            switchSeller(sellerId);
          }}
        >
          <option value="">Selecione o tenant para continuar</option>
          {sellers?.map((seller) => (
            <option key={seller.id} value={seller.id}>{seller.display_name} · @{seller.slug}</option>
          ))}
        </select>
        <div aria-live="polite">
          {sellerState === "loading" ? <p className="form-note">Carregando seus espaços…</p> : null}
          {sellerState === "unavailable" ? <p className="form-note">A API local está indisponível. O catálogo demonstrativo não substitui dados reais.</p> : null}
          {sellerState === "unauthorized" ? <p className="form-error">Sua sessão expirou. <a href="/auth/login">Entre novamente</a>.</p> : null}
          {sellerState === "error" ? <p className="form-error">Não foi possível carregar seus espaços.</p> : null}
          {sellerState === "idle" && sellers?.length === 0 ? <p className="form-note">Ative um espaço de vendas acima antes de criar produtos.</p> : null}
        </div>
        {pendingSeller ? (
          <div className="tenant-switch-warning" role="alert">
            <div><strong>Você tem alterações não salvas.</strong><p>Para trocar para {pendingSeller.display_name}, descarte primeiro os campos deste rascunho.</p></div>
            <div><button type="button" className="secondary-button" onClick={() => setPendingSellerId("")}>Continuar editando</button><button type="button" className="danger-button" onClick={() => switchSeller(pendingSeller.id)}>Descartar e trocar</button></div>
          </div>
        ) : null}
      </div>

      {selectedSeller ? (
        <div className="catalog-workspace-grid">
          <form key={selectedSeller.id} className="product-draft-form" action={`/api/sellers/${selectedSeller.id}/products`} method="post" onInput={() => setFormDirty(true)} onSubmit={submit}>
            <div className="form-heading">
              <p className="eyebrow">Novo rascunho</p>
              <h2>Estruture sua próxima oferta</h2>
              <p>Tenant: <strong>{selectedSeller.display_name}</strong>. Nenhuma cobrança ou publicação acontece nesta etapa.</p>
            </div>
            <input type="hidden" name="_csrf" defaultValue="" />
            <input type="hidden" name="idempotency_key" defaultValue="" />
            <label>Nome do produto<input name="product_title" minLength={2} maxLength={160} required autoComplete="off" placeholder="Ex.: Creator OS" aria-invalid={isInvalid("product_title")} aria-describedby={isInvalid("product_title") ? "product-title-error" : undefined} />{isInvalid("product_title") ? <span id="product-title-error" className="form-error">Informe um nome entre 2 e 160 bytes, sem espaços nas pontas.</span> : null}</label>
            <label>Endereço do produto<input name="slug" minLength={3} maxLength={63} required autoComplete="off" autoCapitalize="none" spellCheck={false} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="creator-os" aria-invalid={isInvalid("slug")} aria-describedby={isInvalid("slug") ? "product-slug-help product-slug-error" : "product-slug-help"} /><span id="product-slug-help">Somente letras minúsculas, números e hífens.</span>{isInvalid("slug") ? <span id="product-slug-error" className="form-error">Use de 3 a 63 caracteres válidos.</span> : null}</label>
            <label>Descrição curta<textarea name="product_description" maxLength={2000} rows={4} placeholder="O que o aluno vai transformar com este produto?" aria-invalid={isInvalid("product_description")} aria-describedby={isInvalid("product_description") ? "product-description-error" : undefined} />{isInvalid("product_description") ? <span id="product-description-error" className="form-error">A descrição deve ter até 2.000 bytes e não pode conter controles.</span> : null}</label>
            <label>Nome do curso inicial<input name="course_title" minLength={2} maxLength={160} required autoComplete="off" placeholder="Ex.: Fundamentos do Creator OS" aria-invalid={isInvalid("course_title")} aria-describedby={isInvalid("course_title") ? "course-title-error" : undefined} />{isInvalid("course_title") ? <span id="course-title-error" className="form-error">Informe um nome entre 2 e 160 bytes, sem espaços nas pontas.</span> : null}</label>
            <button className="primary-button" type="submit" disabled={saveState === "saving"}>{saveState === "saving" ? "Salvando rascunho…" : "Criar produto e curso"}</button>
            <div className="form-feedback" aria-live="polite" aria-atomic="true">
              {saveState === "invalid" ? <p className="form-error">Revise o campo {fieldLabel(invalidField)}.</p> : null}
              {saveState === "conflict" ? <p className="form-error">Este endereço já existe neste tenant. Escolha outro slug.</p> : null}
              {saveState === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente antes de tentar.</p> : null}
              {saveState === "unavailable" ? <p className="form-note">A API está indisponível. Seus campos e a chave desta tentativa foram preservados; tente novamente.</p> : null}
              {saveState === "error" ? <p className="form-error">Não foi possível salvar. Tente novamente sem alterar os dados.</p> : null}
            </div>
          </form>

          <div className="draft-list" aria-live="polite">
            <div className="draft-list-heading"><div><p className="eyebrow">Neste tenant</p><h2>Rascunhos</h2></div><strong>{products.length}</strong></div>
            {catalogState === "loading" ? <p className="empty-state">Carregando rascunhos…</p> : null}
            {catalogState === "unavailable" ? <p className="empty-state">A API local está indisponível. Tente selecionar o tenant novamente.</p> : null}
            {catalogState === "unauthorized" ? <p className="empty-state">Sua sessão expirou.</p> : null}
            {catalogState === "error" ? <p className="empty-state">Não foi possível carregar este catálogo.</p> : null}
            {catalogState === "idle" && products.length === 0 ? <p className="empty-state">Nenhum rascunho ainda. O primeiro aparecerá aqui sem ser publicado.</p> : null}
            {catalogState === "idle" ? products.map((product) => (
              <article className="draft-card" key={product.id}>
                <div><span className="status status-draft">Rascunho</span><span>@{product.slug}</span></div>
                <h3>{product.title}</h3>
                <p>{product.description || "Descrição ainda não informada."}</p>
                <footer><span>Curso inicial</span><strong>{product.course?.title ?? product.course_title ?? "Curso em rascunho"}</strong></footer>
              </article>
            )) : null}
          </div>
        </div>
      ) : (
        <div className="tenant-required"><strong>Escolha o contexto antes de editar.</strong><p>O tenant nunca é inferido silenciosamente da sua conta.</p></div>
      )}
    </section>
  );
}

function readCookie(name: string) {
  const prefix = `${name}=`;
  const value = document.cookie.split("; ").find((part) => part.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

function fieldLabel(field: string) {
  const labels: Record<string, string> = {
    slug: "endereço do produto",
    product_title: "nome do produto",
    product_description: "descrição curta",
    course_title: "nome do curso"
  };
  return labels[field] ?? "indicado";
}
