"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";

import { canConfigureStore } from "@/lib/shell-navigation";
import { urlDoLink, type LinkDaLoja } from "@/lib/tracking/link-url";

interface Seller {
  id: string;
  slug: string;
  role: string;
}

interface Link extends LinkDaLoja {
  name: string;
  status: "active" | "archived";
}

interface LinhaDeCampanha {
  link_id: string | null;
  link_name: string | null;
  utm_source: string | null;
  utm_medium: string | null;
  utm_campaign: string | null;
  approved: number;
  refunded: number;
}

type Estado =
  | { fase: "carregando" }
  | { fase: "sem_acesso" }
  | { fase: "indisponivel" }
  | { fase: "erro" }
  | { fase: "pronto"; links: Link[]; csrf: string; vendas: LinhaDeCampanha[] };

const TEMPO_MAXIMO_MS = 8_000;
const DIAS_DO_RELATORIO = 30;
const CAMPOS: ReadonlyArray<{ nome: string; rotulo: string; obrigatorio?: boolean }> = [
  { nome: "name", rotulo: "Nome do link", obrigatorio: true },
  { nome: "utm_source", rotulo: "Origem (utm_source)", obrigatorio: true },
  { nome: "utm_medium", rotulo: "Meio (utm_medium)" },
  { nome: "utm_campaign", rotulo: "Campanha (utm_campaign)" },
  { nome: "utm_content", rotulo: "Conteúdo (utm_content)" },
  { nome: "utm_term", rotulo: "Termo (utm_term)" },
  { nome: "destination_page_slug", rotulo: "Página do produto (vazio = vitrine)" }
];

function dia(menos: number): string {
  return new Date(Date.now() - menos * 86_400_000).toISOString().slice(0, 10);
}

async function buscar(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TEMPO_MAXIMO_MS);
  try {
    return await fetch(url, { cache: "no-store", ...init, signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

export function DashboardCampaignLinks() {
  const [seller, setSeller] = useState<Seller | null>(null);
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });
  const [aviso, setAviso] = useState<string | null>(null);

  const carregar = useCallback(async (loja: Seller) => {
    try {
      const query = new URLSearchParams({ seller: loja.id });
      const periodo = new URLSearchParams({ seller: loja.id, from: dia(DIAS_DO_RELATORIO), to: dia(0) });
      const [dados, relatorio] = await Promise.all([
        buscar(`/dashboard/links/dados?${query}`),
        buscar(`/dashboard/links/relatorio?${periodo}`)
      ]);
      if (dados.status === 404 || dados.status === 403) return setEstado({ fase: "indisponivel" });
      if (!dados.ok || !relatorio.ok) return setEstado({ fase: "erro" });
      const corpo = await dados.json() as { links?: Link[]; csrf?: string };
      const vendas = await relatorio.json() as { rows?: LinhaDeCampanha[] };
      setEstado({ fase: "pronto", links: corpo.links ?? [], csrf: corpo.csrf ?? "", vendas: vendas.rows ?? [] });
    } catch {
      setEstado({ fase: "erro" });
    }
  }, []);

  useEffect(() => {
    let ativo = true;
    void buscar("/api/sellers")
      .then(async (resposta) => {
        if (!ativo) return;
        if (!resposta.ok) return setEstado({ fase: "erro" });
        const corpo = await resposta.json() as { sellers?: Seller[] };
        const lojas = corpo.sellers ?? [];
        if (!canConfigureStore(lojas)) return setEstado({ fase: "sem_acesso" });
        setSeller(lojas[0]!);
        await carregar(lojas[0]!);
      })
      .catch(() => { if (ativo) setEstado({ fase: "erro" }); });
    return () => { ativo = false; };
  }, [carregar]);

  async function enviar(caminho: string, form: FormData) {
    if (!seller || estado.fase !== "pronto") return;
    form.set("seller", seller.id);
    form.set("_csrf", estado.csrf);
    try {
      const resposta = await buscar(caminho, { method: "POST", body: form });
      if (resposta.status === 409) return setAviso("A loja chegou ao limite de 200 links ativos. Arquive um para criar outro.");
      if (resposta.status === 422) return setAviso("Confira os campos: a página do produto precisa existir na loja.");
      if (!resposta.ok) return setAviso("Não foi possível salvar agora. Tente de novo.");
      setAviso(null);
      await carregar(seller);
    } catch {
      setAviso("Não foi possível salvar agora. Tente de novo.");
    }
  }

  function criar(evento: FormEvent<HTMLFormElement>) {
    evento.preventDefault();
    const form = new FormData(evento.currentTarget);
    evento.currentTarget.reset();
    void enviar("/dashboard/links/criar", form);
  }

  function arquivar(link: Link) {
    const form = new FormData();
    form.set("link", link.id);
    void enviar("/dashboard/links/arquivar", form);
  }

  if (estado.fase === "carregando") return <p role="status">Carregando os links…</p>;
  if (estado.fase === "sem_acesso") return <p>Só quem é dono ou admin da loja vê os links de campanha.</p>;
  if (estado.fase === "indisponivel") return <p>Os links de campanha ainda não estão disponíveis para esta loja.</p>;
  if (estado.fase === "erro") return <p role="alert">Não foi possível carregar os links agora.</p>;

  const origem = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <>
      <section className="panel" aria-labelledby="links-novo">
        <h2 id="links-novo">Novo link</h2>
        <form onSubmit={criar}>
          {CAMPOS.map((campo) => (
            <div className="form-row" key={campo.nome}>
              <label htmlFor={`link-${campo.nome}`}>{campo.rotulo}</label>
              <input id={`link-${campo.nome}`} name={campo.nome} required={campo.obrigatorio} maxLength={campo.nome === "name" ? 80 : 100}/>
            </div>
          ))}
          <button type="submit" className="primary-button">Criar link</button>
        </form>
        {aviso ? <p role="alert">{aviso}</p> : null}
      </section>

      <section className="panel" aria-labelledby="links-lista">
        <h2 id="links-lista">Links da loja</h2>
        {estado.links.length === 0 ? <p>Nenhum link ainda.</p> : <ul role="list">
          {estado.links.map((link) => (
            <li key={link.id}>
              <strong>{link.name}</strong> {link.status === "archived" ? <span>(arquivado)</span> : null}
              <p><code>{seller ? urlDoLink(origem, seller.slug, link) : ""}</code></p>
              {link.status === "active"
                ? <button type="button" className="secondary-button" onClick={() => arquivar(link)}>Arquivar</button>
                : null}
            </li>
          ))}
        </ul>}
      </section>

      <section className="panel" aria-labelledby="links-vendas">
        <h2 id="links-vendas">Vendas por campanha nos últimos 30 dias</h2>
        {estado.vendas.length === 0 ? <p>Nenhuma venda atribuída a campanha no período.</p> : <table>
          <thead><tr><th scope="col">Link</th><th scope="col">Origem</th><th scope="col">Campanha</th><th scope="col">Vendas</th><th scope="col">Estornos</th></tr></thead>
          <tbody>
            {estado.vendas.map((linha, indice) => (
              <tr key={`${linha.link_id ?? "sem-link"}-${linha.utm_source ?? ""}-${linha.utm_campaign ?? ""}-${indice}`}>
                <td>{linha.link_name ?? "—"}</td>
                <td>{linha.utm_source ?? "—"}</td>
                <td>{linha.utm_campaign ?? "—"}</td>
                <td>{linha.approved}</td>
                <td>{linha.refunded}</td>
              </tr>
            ))}
          </tbody>
        </table>}
      </section>
    </>
  );
}
