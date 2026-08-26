"use client";

import Link from "next/link";

import { catalogPrice, productArt, studentCountLabel } from "@/lib/product-art";
import { useRouter } from "next/navigation";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { autoSelectableSpace, SellerSpace, sellerRoleLabel } from "./seller-space";
import { DIRTY_NAVIGATION_REQUEST_EVENT, useUnsavedChanges } from "./use-unsaved-changes";

type Seller = SellerSpace;

interface ProductDraft {
  id: string;
  slug: string;
  title: string;
  description: string;
  status: "draft" | "published" | "unpublished" | "archived";
  created_at: string;
  access_mode?: "free" | "paid";
  cover?: { url: string; media_type: string; width: number; height: number } | null;
  students?: number;
  unit_amount_minor?: number | null;
  currency?: string | null;
  course?: { id: string; title: string; status: "draft" | "published" | "archived" } | null;
  course_id?: string;
  course_title?: string;
}

type LoadState = "loading" | "loading_more" | "page_error" | "idle" | "unauthorized" | "unavailable" | "error";
type SaveState = "idle" | "saving" | "conflict" | "forbidden" | "invalid" | "unavailable" | "error";

export function ProductDraftWorkspace() {
  const router = useRouter();
  const [sellers, setSellers] = useState<Seller[] | null>(null);
  const [selectedSellerId, setSelectedSellerId] = useState("");
  const [products, setProducts] = useState<ProductDraft[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [sellerState, setSellerState] = useState<LoadState>("loading");
  const [catalogState, setCatalogState] = useState<LoadState>("idle");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [invalidField, setInvalidField] = useState("");
  const [formDirty, setFormDirty] = useState(false);
  // Qual produto acabou de ter o endereço copiado. Guardar o id e não um
  // booleano é o que impede o "Endereço copiado" de aparecer em todos os
  // cartões de uma vez.
  const [shared, setShared] = useState<string | null>(null);
  // Fechado por padrão: quem chega em Produtos quer ver os que tem.
  const [creating, setCreating] = useState(false);
  const formDirtyRef = useRef(false);
  const [pendingSellerId, setPendingSellerId] = useState("");
  const idempotencyKey = useRef<string | null>(null);
  const selectedSellerIdRef = useRef("");
  const catalogRequest = useRef<{ controller: AbortController; version: number } | null>(null);
  const catalogVersion = useRef(0);
  const catalogHeading = useRef<HTMLHeadingElement>(null);

  useUnsavedChanges(formDirty);

  const loadProducts = useCallback(async (sellerId: string, cursor?: string) => {
    catalogRequest.current?.controller.abort();
    const version = ++catalogVersion.current;
    if (!sellerId) {
      setProducts([]);
      setNextCursor(null);
      setCatalogState("idle");
      catalogRequest.current = null;
      return;
    }
    setCatalogState(cursor ? "loading_more" : "loading");
    const controller = new AbortController();
    catalogRequest.current = { controller, version };
    const timeout = setTimeout(() => controller.abort(), 5_000);
    try {
      const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
      const response = await fetch(`/api/sellers/${sellerId}/products${query}`, {
        cache: "no-store",
        signal: controller.signal
      });
      if (catalogRequest.current?.version !== version) return;
      if (response.status === 401) return setCatalogState("unauthorized");
      if (response.status === 503) return setCatalogState(cursor ? "page_error" : "unavailable");
      if (!response.ok) return setCatalogState(cursor ? "page_error" : "error");
      const payload = await response.json() as { products?: ProductDraft[]; next_cursor?: string | null };
      const loaded = Array.isArray(payload.products) ? payload.products : [];
      setProducts((current) => cursor ? [...current, ...loaded] : loaded);
      const followingCursor = typeof payload.next_cursor === "string" ? payload.next_cursor : null;
      setNextCursor(followingCursor);
      setCatalogState("idle");
      if (cursor && !followingCursor) queueMicrotask(() => catalogHeading.current?.focus());
    } catch {
      if (catalogRequest.current?.version !== version) return;
      setCatalogState(cursor ? "page_error" : "unavailable");
    } finally {
      clearTimeout(timeout);
      if (catalogRequest.current?.version === version) catalogRequest.current = null;
    }
  }, []);

  const switchSeller = useCallback((sellerId: string, syncUrl = true) => {
    selectedSellerIdRef.current = sellerId;
    setSelectedSellerId(sellerId);
    setPendingSellerId("");
    setFormDirty(false);
    formDirtyRef.current = false;
    setSaveState("idle");
    setInvalidField("");
    idempotencyKey.current = null;
    if (syncUrl) {
      router.replace(sellerId ? `/products?seller=${encodeURIComponent(sellerId)}` : "/products", { scroll: false });
    }
    void loadProducts(sellerId);
  }, [loadProducts, router]);

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
          const nextSellers = Array.isArray(payload.sellers) ? payload.sellers : [];
          setSellers(nextSellers);
          setSellerState("idle");
          if (formDirtyRef.current) return;
          const requestedSellerId = new URLSearchParams(window.location.search).get("seller") ?? "";
          if (requestedSellerId && requestedSellerId !== selectedSellerIdRef.current && nextSellers.some((seller) => seller.id === requestedSellerId)) {
            switchSeller(requestedSellerId, false);
            return;
          }
          const onlySpace = autoSelectableSpace(nextSellers);
          if (onlySpace && onlySpace.id !== selectedSellerIdRef.current) {
            switchSeller(onlySpace.id, false);
          }
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
  }, [switchSeller]);

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
      formDirtyRef.current = false;
      setSaveState("idle");
      // O formulário fecha e o foco volta para a lista, que é onde o produto
      // recém-criado apareceu. Deixá-lo aberto e vazio depois de criar convida
      // a criar de novo sem querer.
      setCreating(false);
      await loadProducts(submittedSellerId);
      queueMicrotask(() => catalogHeading.current?.focus());
    } catch {
      if (selectedSellerIdRef.current !== submittedSellerId) return;
      setSaveState("unavailable");
    } finally {
      clearTimeout(timeout);
    }
  }

  const selectedSeller = sellers?.find((seller) => seller.id === selectedSellerId) ?? null;
  const pendingSeller = sellers?.find((seller) => seller.id === pendingSellerId) ?? null;
  const lockedSpace = autoSelectableSpace(sellers);

  function isInvalid(field: string) {
    return saveState === "invalid" && invalidField === field;
  }

  /**
   * Copia o endereço público do produto.
   *
   * Um produto no ar sem preço entra pela página de acesso; com venda ativa,
   * pela vitrine. Mandar todo mundo para a vitrine faria o link gratuito pedir
   * um pagamento que não existe.
   */
  async function share(product: ProductDraft) {
    if (!selectedSeller) return;
    const path = product.access_mode === "paid"
      ? `/s/${selectedSeller.slug}/p/${product.slug}`
      : `/s/${selectedSeller.slug}/p/${product.slug}/access`;
    try {
      await navigator.clipboard.writeText(new URL(path, window.location.origin).toString());
      setShared(product.id);
    } catch {
      // Sem área de transferência não há o que compartilhar em silêncio: o
      // rótulo continua "Compartilhar" em vez de mentir que copiou.
      setShared(null);
    }
  }

  return (
    <section className="catalog-workspace" aria-labelledby="catalog-workspace-title">
      {/* A tela tinha três títulos de tamanho parecido disputando a atenção:
          "Produtos" (o h1 da página), "Organize seus produtos" e "Produtos
          cadastrados". Sobrou um. O rótulo desta região vira nome acessível,
          não manchete. */}
      <h2 id="catalog-workspace-title" className="sr-only">Catálogo desta loja</h2>

      <div className="tenant-picker">
        {lockedSpace ? (
          <div className="tenant-locked">
            <p className="tenant-locked-label">Sua loja</p>
            <strong>{lockedSpace.display_name}</strong>
            <span>@{lockedSpace.slug} · {sellerRoleLabel(lockedSpace.role)}</span>
          </div>
        ) : (
          <>
            <label htmlFor="seller-context">Sua loja</label>
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
              <option value="">Selecione a loja para continuar</option>
              {sellers?.map((seller) => (
                <option key={seller.id} value={seller.id}>{seller.display_name} · @{seller.slug}</option>
              ))}
            </select>
          </>
        )}
        <div aria-live="polite">
          {sellerState === "loading" ? <p className="form-note">Carregando suas lojas…</p> : null}
          {sellerState === "unavailable" ? <p className="form-note">A API local está indisponível. O catálogo demonstrativo não substitui dados reais.</p> : null}
          {sellerState === "unauthorized" ? <p className="form-error">Sua sessão expirou. <a href="/auth/login">Entre novamente</a>.</p> : null}
          {sellerState === "error" ? <p className="form-error">Não foi possível carregar suas lojas.</p> : null}
          {sellerState === "idle" && sellers?.length === 0 ? <p className="form-note">Ative uma loja acima antes de criar produtos.</p> : null}
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
          <div className="draft-list">
            <div className="draft-list-heading"><div><p className="eyebrow">Nesta loja</p><h2 ref={catalogHeading} tabIndex={-1}>Produtos cadastrados</h2></div><strong aria-label={`${products.length} produtos carregados`}>{products.length}</strong></div>
            <div className="sr-only" aria-live="polite" aria-atomic="true">{catalogState === "idle" ? `${products.length} ${products.length === 1 ? "produto carregado" : "produtos carregados"}.` : ""}</div>
            {catalogState === "loading" ? <p className="empty-state">Carregando produtos…</p> : null}
            {catalogState === "unavailable" ? <p className="empty-state">O catálogo está indisponível. Tente selecionar a loja novamente.</p> : null}
            {catalogState === "unauthorized" ? <p className="empty-state">Sua sessão expirou.</p> : null}
            {catalogState === "error" ? <p className="empty-state">Não foi possível carregar este catálogo.</p> : null}
            {catalogState === "idle" && products.length === 0 ? <p className="empty-state">Nenhum produto ainda. Crie o primeiro como rascunho privado.</p> : null}
            {catalogState === "idle" || catalogState === "loading_more" || catalogState === "page_error" ? products.map((product) => (
              <article className="draft-card" key={product.id}>
                {/* Miniatura quadrada de 104px, não uma faixa 16:7 ocupando um
                    terço da tela. A capa é decoração — quem informa é o título
                    ao lado —, então ela encolhe e o texto cresce. */}
                <div className="draft-card-media">
                  {product.cover
                    // eslint-disable-next-line @next/next/no-img-element
                    ? <img className="draft-card-art draft-card-cover" src={product.cover.url} alt="" width={product.cover.width} height={product.cover.height} loading="lazy" decoding="async" />
                    : (
                      <div className="draft-card-art" aria-hidden="true" style={{ background: productArt(product.title).gradient }}>
                        <span>{productArt(product.title).initials}</span>
                      </div>
                    )}
                </div>
                <div className="draft-card-body">
                  <div className="draft-card-badges">
                    <span className={`status status-${product.status}`}>{productStatusLabel(product.status)}</span>
                    <span className={`access-badge access-${product.access_mode ?? "free"}`}>
                      {product.access_mode === "paid" ? "Venda ativa" : "Acesso gratuito"}
                    </span>
                    {catalogPrice(product.unit_amount_minor, product.currency)
                      ? <strong className="draft-card-price">{catalogPrice(product.unit_amount_minor, product.currency)}</strong>
                      : null}
                  </div>
                  <h3>{product.title}</h3>
                  <p className="draft-card-meta">@{product.slug} · {product.course?.title ?? product.course_title ?? "Conteúdo em rascunho"}</p>
                  {/* O número que mais importa era o menor texto do cartão, em
                      cinza. Agora é o maior, e em cor de texto cheia. */}
                  <p className="draft-card-count">{studentCountLabel(product.students)}</p>
                  <div className="draft-card-actions">
                    <Link className="draft-card-action" href={`/products/${encodeURIComponent(selectedSeller.id)}/${encodeURIComponent(product.id)}`} onClick={(event) => {
                      if (!formDirty || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                      event.preventDefault();
                      window.dispatchEvent(new CustomEvent(DIRTY_NAVIGATION_REQUEST_EVENT, { detail: { href: event.currentTarget.href, trigger: event.currentTarget } }));
                    }}>Abrir</Link>
                    {/* Compartilhar só existe para produto no ar: um rascunho
                        não tem endereço público para copiar, e um botão que
                        copia um link quebrado é pior do que botão nenhum. */}
                    {product.status === "published" ? (
                      <button type="button" className="secondary-button draft-card-share" onClick={() => void share(product)}>
                        {shared === product.id ? "Endereço copiado" : "Compartilhar"}
                      </button>
                    ) : null}
                  </div>
                </div>
              </article>
            )) : null}
            {catalogState === "page_error" ? <p className="form-note" role="alert">Não foi possível carregar a próxima página. Os produtos já carregados foram preservados.</p> : null}
            {(catalogState === "idle" || catalogState === "loading_more" || catalogState === "page_error") && nextCursor ? <button type="button" className="secondary-button load-more" disabled={catalogState === "loading_more"} onClick={() => void loadProducts(selectedSeller.id, nextCursor)}>{catalogState === "loading_more" ? "Carregando…" : catalogState === "page_error" ? "Tentar carregar novamente" : "Carregar mais produtos"}</button> : null}
          </div>

          {/* O formulário desce para depois da lista e some atrás de um botão.
              Ele vinha primeiro, então quem já tem produtos era recebido por um
              pedido de criar mais um antes de ver o que já existe. */}
          <div className="draft-create">
            <button
              type="button"
              className="primary-button draft-create-toggle"
              aria-expanded={creating}
              aria-controls="draft-create-form"
              onClick={() => setCreating((open) => {
                // Fechar é cancelar: sem zerar a marca de alterações, o aviso
                // de "não salvo" continuaria travando a navegação por causa de
                // um formulário que já saiu da tela.
                if (open) {
                  formDirtyRef.current = false;
                  setFormDirty(false);
                }
                return !open;
              })}
            >
              {creating ? "Cancelar" : "Novo produto"}
            </button>
            {creating ? (
              <div id="draft-create-form">
            <form key={selectedSeller.id} className="product-draft-form" action={`/api/sellers/${selectedSeller.id}/products`} method="post" onInput={() => { formDirtyRef.current = true; setFormDirty(true); }} onSubmit={submit}>
              <div className="form-heading">
                <p className="eyebrow">Novo rascunho</p>
                <h2>Estruture seu próximo produto</h2>
                <p>Loja: <strong>{selectedSeller.display_name}</strong>. Nenhuma cobrança ou publicação acontece nesta etapa.</p>
              </div>
              <fieldset className="product-create-fields" disabled={saveState === "saving"}>
                <legend className="sr-only">Dados do novo produto</legend>
                <input type="hidden" name="_csrf" defaultValue="" />
                <input type="hidden" name="idempotency_key" defaultValue="" />
                <label>Nome do produto<input name="product_title" minLength={2} maxLength={160} required autoComplete="off" placeholder="Ex.: Codex na prática…" aria-invalid={isInvalid("product_title")} aria-describedby={isInvalid("product_title") ? "product-title-error" : undefined} />{isInvalid("product_title") ? <span id="product-title-error" className="form-error">Informe um nome entre 2 e 160 bytes, sem sobras nas pontas.</span> : null}</label>
                <label>Endereço do produto<input name="slug" minLength={3} maxLength={63} required autoComplete="off" autoCapitalize="none" spellCheck={false} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="codex-na-pratica…" aria-invalid={isInvalid("slug")} aria-describedby={isInvalid("slug") ? "product-slug-help product-slug-error" : "product-slug-help"} /><span id="product-slug-help">Somente letras minúsculas, números e hífens.</span>{isInvalid("slug") ? <span id="product-slug-error" className="form-error">Use de 3 a 63 caracteres válidos.</span> : null}</label>
                <label>Descrição curta<textarea name="product_description" maxLength={2000} rows={4} placeholder="Ex.: Aprenda a trabalhar com Codex com segurança…" aria-invalid={isInvalid("product_description")} aria-describedby={isInvalid("product_description") ? "product-description-error" : undefined} />{isInvalid("product_description") ? <span id="product-description-error" className="form-error">A descrição deve ter até 2.000 bytes e não pode conter controles.</span> : null}</label>
                <label>Nome do conteúdo<input name="course_title" minLength={2} maxLength={160} required autoComplete="off" placeholder="Ex.: Fundamentos do Codex…" aria-invalid={isInvalid("course_title")} aria-describedby={isInvalid("course_title") ? "course-title-error" : undefined} />{isInvalid("course_title") ? <span id="course-title-error" className="form-error">Informe um nome entre 2 e 160 bytes, sem sobras nas pontas.</span> : null}</label>
                <button className="primary-button" type="submit">{saveState === "saving" ? "Salvando rascunho…" : "Criar produto"}</button>
              </fieldset>
              <div className="form-feedback" aria-live="polite" aria-atomic="true">
                {saveState === "invalid" ? <p className="form-error">Revise o campo {fieldLabel(invalidField)}.</p> : null}
                {saveState === "conflict" ? <p className="form-error">Este endereço já existe nesta loja. Escolha outro.</p> : null}
                {saveState === "forbidden" ? <p className="form-error">Sua sessão ou permissão mudou. Entre novamente antes de tentar.</p> : null}
                {saveState === "unavailable" ? <p className="form-note">A API está indisponível. Seus campos e a chave desta tentativa foram preservados; tente novamente.</p> : null}
                {saveState === "error" ? <p className="form-error">Não foi possível salvar. Tente novamente sem alterar os dados.</p> : null}
              </div>
            </form>
              </div>
            ) : null}
          </div>
        </div>
      ) : sellerState === "loading" ? null : (
        <div className="tenant-required"><strong>Escolha a loja antes de editar.</strong><p>A loja nunca é escolhida por nós no seu lugar.</p></div>
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
    course_title: "nome do conteúdo"
  };
  return labels[field] ?? "indicado";
}

function productStatusLabel(status: ProductDraft["status"]) {
  if (status === "published") return "Publicado";
  if (status === "unpublished") return "Despublicado";
  if (status === "archived") return "Arquivado";
  return "Rascunho";
}
