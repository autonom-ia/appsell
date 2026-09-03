"use client";

import Link from "next/link";
import { productArt } from "@/lib/product-art";
import { useCallback, useEffect, useState } from "react";

import { payoutStageFrom, type PayoutAccountStatus, type PayoutStage } from "@/lib/payout-status";
import { ProductCard } from "./product-card";
import { SellerOnboardingCard } from "./seller-onboarding-card";
import { dashboardHeader } from "../../lib/dashboard-audience";
import { canOpenFirstStore } from "./shell-profile-context";
import { useShellProfile } from "./shell-profile-context";
import { DEMO_SELLER_ID, demoProducts } from "../../lib/demo-catalog";
import { listSellerProducts } from "../../lib/catalog-projection";
import {
  dashboardCatalogMetrics,
  formatProductCount,
  type DashboardDraftStatus
} from "../../lib/dashboard-projection";

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
  status: DashboardDraftStatus;
  access_mode?: "free" | "paid";
  course?: { id: string; title: string } | null;
  course_title?: string;
}

type CatalogState =
  | { status: "loading" }
  | { status: "unauthorized" }
  | { status: "unavailable" }
  | { status: "error" }
  | { status: "ready"; sellers: Seller[]; drafts: ProductDraft[]; truncated: boolean };

// The drafts endpoint is cursor-paginated; the overview walks a bounded number
// of pages so the metric stays honest without unbounded fan-out.
const MAX_DRAFT_PAGES = 20;
const FETCH_TIMEOUT_MS = 8_000;

type SessionView = "loading" | "failed" | "demo" | "real";

export function DashboardOverview() {
  // The shell already resolved /api/session; reuse it instead of refetching.
  const profile = useShellProfile();
  // Sessão pronta com papel não resolvido não é sessão real: o shell já mostra
  // recuperação, e entrar em modo real aqui renderizaria o painel do vendedor
  // e dispararia consultas de catálogo em cima de uma sessão que não sabemos
  // ler. A falha fechada precisa valer também para o conteúdo.
  const view: SessionView = profile.status === "ready"
    ? (profile.profile ? "demo" : profile.identity ? "real" : "failed")
    : profile.status;
  const [catalog, setCatalog] = useState<CatalogState>({ status: "loading" });

  // Initial state is already "loading"; reloads keep the previous snapshot on
  // screen until the fresh response lands (and stay lint-safe inside effects).
  // Each load gets its own timeout-bound controller so a stalled endpoint
  // cannot hang the overview, while the parent signal still cancels on unmount.
  const loadCatalog = useCallback(async (parentSignal: AbortSignal) => {
    const controller = new AbortController();
    const abortFromParent = () => controller.abort();
    parentSignal.addEventListener("abort", abortFromParent, { once: true });
    const timeout = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    const signal = controller.signal;
    try {
      const sellersResponse = await fetch("/api/sellers", { cache: "no-store", signal });
      if (sellersResponse.status === 401) return setCatalog({ status: "unauthorized" });
      if (sellersResponse.status === 503) return setCatalog({ status: "unavailable" });
      if (!sellersResponse.ok) return setCatalog({ status: "error" });
      const sellersPayload = await sellersResponse.json() as { sellers?: Seller[] };
      const sellers = Array.isArray(sellersPayload.sellers) ? sellersPayload.sellers : [];
      if (sellers.length === 0) {
        return setCatalog({ status: "ready", sellers, drafts: [], truncated: false });
      }
      const drafts: ProductDraft[] = [];
      let cursor: string | null = null;
      let truncated = false;
      for (let page = 0; page < MAX_DRAFT_PAGES; page += 1) {
        const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
        const response: Response = await fetch(`/api/sellers/${sellers[0].id}/products${query}`, {
          cache: "no-store",
          signal
        });
        if (response.status === 401) return setCatalog({ status: "unauthorized" });
        if (response.status === 503) return setCatalog({ status: "unavailable" });
        if (!response.ok) return setCatalog({ status: "error" });
        const payload = await response.json() as { products?: ProductDraft[]; next_cursor?: unknown };
        drafts.push(...(Array.isArray(payload.products) ? payload.products : []));
        cursor = typeof payload.next_cursor === "string" && payload.next_cursor ? payload.next_cursor : null;
        if (!cursor) break;
        if (page === MAX_DRAFT_PAGES - 1) truncated = true;
      }
      setCatalog({ status: "ready", sellers, drafts, truncated });
    } catch {
      // A timeout abort must surface as an error; only unmount stays silent.
      if (!parentSignal.aborted) setCatalog({ status: "error" });
    } finally {
      window.clearTimeout(timeout);
      parentSignal.removeEventListener("abort", abortFromParent);
    }
  }, []);

  useEffect(() => {
    if (view !== "real") return;
    const controller = new AbortController();
    const start = window.setTimeout(() => void loadCatalog(controller.signal), 0);
    const reload = () => {
      void loadCatalog(controller.signal);
    };
    window.addEventListener("appsell:sellers-changed", reload);
    return () => {
      window.clearTimeout(start);
      window.removeEventListener("appsell:sellers-changed", reload);
      controller.abort();
    };
  }, [view, loadCatalog]);

  // The CTA is plain navigation to /products (which owns onboarding), so it
  // renders whenever the session view is resolved — including while the
  // catalog request is still in flight or unavailable.
  // null enquanto a sessão não resolveu: não sabemos se há loja, e o painel
  // não pode afirmar que não há.
  // O superadmin sem nenhuma loja ativa não é um produtor por ativar: o
  // callback já o manda para cá, e o painel precisa concordar com ele.
  const hasStore = profile.status === "ready"
    ? (profile.identity
        ? profile.identity.sellers.length > 0 || profile.identity.platformRoles.length > 0
        : null)
    : null;
  const header = dashboardHeader(view, hasStore);

  return (
    <>
      <header className="page-header">
        {header.activation ? (
          <div><p className="eyebrow">Ativação do produtor</p><h1>Comece criando sua loja.</h1><p>A loja é onde seus produtos ficam e onde seus alunos compram.</p></div>
        ) : (
          <div><p className="eyebrow">Visão geral</p><h1>Seu negócio em um só lugar.</h1><p>Acompanhe o catálogo e continue de onde parou.</p></div>
        )}
        {header.cta ? <Link className="primary-button" href="/products">Criar produto</Link> : null}
      </header>
      {view === "loading" ? <p className="empty-state" role="status">Carregando sua visão geral…</p> : null}
      {view === "failed" ? <p className="empty-state" role="alert">Não foi possível validar sua sessão. <a href="/auth/login">Entre novamente</a>.</p> : null}
      {view === "demo" ? <DemoDashboard/> : null}
      {view === "real" ? <RealDashboard catalog={catalog} canOpenStore={canOpenFirstStore(profile)}/> : null}
    </>
  );
}

function RealDashboard({ catalog, canOpenStore }: { catalog: CatalogState; canOpenStore: boolean }) {
  const ready = catalog.status === "ready" ? catalog : null;
  const hasSeller = (ready?.sellers.length ?? 0) > 0;
  const metrics = dashboardCatalogMetrics(ready?.drafts ?? [], ready?.truncated ?? false);
  const visibleDrafts = (ready?.drafts ?? []).filter((draft) => draft.status !== "archived").slice(0, 4);

  return (
    <>
      {catalog.status === "loading" ? <p className="empty-state" role="status">Carregando seu catálogo…</p> : null}
      {catalog.status === "unauthorized" ? <p className="empty-state" role="alert">Sua sessão expirou. <a href="/auth/login">Entre novamente</a>.</p> : null}
      {catalog.status === "unavailable" ? <p className="empty-state" role="alert">A API está indisponível no momento. Tente novamente em instantes.</p> : null}
      {catalog.status === "error" ? <p className="empty-state" role="alert">Não foi possível carregar seu catálogo. Recarregue a página.</p> : null}
      {ready && !hasSeller && canOpenStore ? <SellerOnboardingCard/> : null}
      {ready && hasSeller && metrics.total === 0 ? (
        <section className="empty-state next-step" aria-label="Próximo passo">
          <h2>Sua loja está pronta. Falta o primeiro produto.</h2>
          <p>
            Enquanto não houver um produto publicado, não há o que vender nem
            o que um aluno possa abrir.
          </p>
          <Link className="primary-button" href="/products">Criar meu primeiro produto</Link>
        </section>
      ) : null}
      {ready && hasSeller && metrics.total > 0 ? (
        <section className="metric-grid" aria-label="Resumo do negócio">
          <article><span>Produtos</span><strong>{formatProductCount(metrics)}</strong><small>{metrics.published} publicados</small></article>
          <article><span>Alunos</span><strong>–</strong><small>Sem matrículas registradas ainda</small></article>
          <article><span>Receita</span><strong>–</strong><small>Financeiro ainda não conectado</small></article>
        </section>
      ) : null}
      {ready && hasSeller ? <PayoutNotice seller={ready.sellers[0]} drafts={ready.drafts}/> : null}
      {ready && hasSeller ? (
        <>
          <section className="section-heading">
            <div><p className="eyebrow">{ready.sellers[0].display_name}</p><h2>Seus produtos</h2></div>
            <Link href="/products">Ver todos <span aria-hidden="true">→</span></Link>
          </section>
          {ready.sellers.length > 1 ? <p className="form-note">Mostrando {ready.sellers[0].display_name}. Gerencie as outras lojas em Produtos.</p> : null}
          {visibleDrafts.length ? (
            <div className="draft-list draft-list-bare">
              {visibleDrafts.map((draft) => (
                /* Mesmo objeto, mesmo cartão. A Visão geral tinha uma cópia da
                   marcação antiga do catálogo, então a #257 a teria quebrado em
                   silêncio: as regras de estilo mudaram junto com a estrutura
                   lá, e aqui a estrutura ficou para trás. */
                <article className="draft-card" key={draft.id}>
                  <div className="draft-card-media">
                    <div className="draft-card-art" aria-hidden="true" style={{ background: productArt(draft.title).gradient }}>
                      <span>{productArt(draft.title).initials}</span>
                    </div>
                  </div>
                  <div className="draft-card-body">
                    <div className="draft-card-badges">
                      <span className={`status status-${draft.status}`}>{draftStatusLabel(draft.status)}</span>
                    </div>
                    <h3>{draft.title}</h3>
                    <p className="draft-card-meta">@{draft.slug} · {draft.course?.title ?? draft.course_title ?? "Conteúdo em rascunho"}</p>
                    <div className="draft-card-actions">
                      <Link className="draft-card-action" href={`/products/${encodeURIComponent(ready.sellers[0].id)}/${encodeURIComponent(draft.id)}`}>Abrir</Link>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <p className="empty-state">Nenhum produto ainda. <Link href="/products">Crie o primeiro como rascunho privado</Link>.</p>
          )}
        </>
      ) : null}
    </>
  );
}

// Loja com produto pago publicado e conta de recebimentos que não recebe:
// o aviso vivia só dentro do editor, e quem publicou e foi embora não sabia
// que o dinheiro não tinha para onde ir (#352). Aqui ele fica na cara.
// A leitura do status é de dono e admin; para os outros papéis a rota nega e
// o aviso simplesmente não aparece — eles não teriam como resolver.
function PayoutNotice({ seller, drafts }: { seller: Seller; drafts: ProductDraft[] }) {
  const [stage, setStage] = useState<PayoutStage | "unknown">("unknown");
  const paidPublished = drafts.some((draft) => draft.status === "published" && draft.access_mode === "paid");

  useEffect(() => {
    if (!paidPublished) return;
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    void fetch(`/api/sellers/${encodeURIComponent(seller.id)}/payout-account`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (!active || !response.ok) return;
        const payload = await response.json() as { payout_account?: PayoutAccountStatus | null };
        if (active) setStage(payoutStageFrom(payload.payout_account ?? null));
      })
      .catch(() => undefined)
      .finally(() => window.clearTimeout(timeout));
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [seller.id, paidPublished]);

  if (!paidPublished || stage === "unknown" || stage === "active") return null;
  return (
    <section className="dashboard-notice" role="alert" aria-labelledby="payout-notice-title">
      <h2 id="payout-notice-title">Você tem produto pago publicado e a conta de recebimentos não está pronta.</h2>
      <p>
        {stage === "not_connected"
          ? "Sem a conta conectada, nenhuma venda tem para onde ir. Leva uns dez minutos: documento e dados bancários de quem recebe."
          : stage === "action_required"
            ? "A verificação da conta apontou uma pendência. Revise os dados para voltar a receber."
            : "A verificação da conta ainda não terminou. Enquanto isso, as vendas não são liberadas."}
      </p>
      <Link className="primary-button" href="/settings/payouts">Abrir Recebimentos</Link>
    </section>
  );
}

function DemoDashboard() {
  const products = listSellerProducts(demoProducts, DEMO_SELLER_ID);
  const published = products.filter((product) => product.status === "published").length;

  return (
    <>
      <section className="metric-grid" aria-label="Resumo demonstrativo do negócio">
        <article><span>Produtos</span><strong>{String(products.length).padStart(2, "0")}</strong><small>{published} publicados</small></article>
        <article><span>Alunos</span><strong>128</strong><small>Dados demonstrativos</small></article>
        <article><span>Receita</span><strong>–</strong><small>Financeiro ainda não conectado</small></article>
      </section>
      <section className="section-heading"><div><p className="eyebrow">Catálogo demonstrativo</p><h2>Seus produtos</h2></div><Link href="/products">Ver todos <span aria-hidden="true">→</span></Link></section>
      {products.length ? <div className="product-grid">{products.slice(0, 2).map((product) => <ProductCard key={product.id} product={product}/>)}</div> : <p className="empty-state">Nenhum produto demonstrativo disponível.</p>}
      <section className="blocked-card"><span className="blocked-icon" aria-hidden="true">i</span><div><strong>Ambiente de demonstração</strong><p>Pagamentos e login comercial ainda estão desativados. Criação, edição, conteúdo e publicação local já podem ser demonstrados com isolamento entre lojas.</p></div></section>
    </>
  );
}

function draftStatusLabel(status: DashboardDraftStatus) {
  if (status === "published") return "Publicado";
  if (status === "unpublished") return "Despublicado";
  if (status === "archived") return "Arquivado";
  return "Rascunho";
}
