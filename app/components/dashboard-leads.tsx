"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { ChoiceSelect, type ChoiceOption } from "@/app/components/ui/choice-select";
import { canConfigureStore } from "@/lib/shell-navigation";

interface Seller {
  id: string;
  role: string;
}

type SituacaoDoEmail = "granted" | "none" | "unsubscribed" | "suppressed";

interface LeadRow {
  entitlement_id: string;
  name: string | null;
  email: string;
  catalog_product_id: string;
  product_title: string;
  source: string;
  access_status: string;
  since: string;
  email_status: SituacaoDoEmail;
}

interface Leads {
  total_rows: number;
  total_people: number;
  marketing_people: number;
  rows: LeadRow[];
}

type Estado =
  | { fase: "carregando" }
  | { fase: "sem_acesso" }
  | { fase: "indisponivel" }
  | { fase: "erro" }
  | { fase: "pronto"; leads: Leads };

const FETCH_TIMEOUT_MS = 8_000;
const DIAS_PADRAO = 30;
const TODOS = "todos";
const SITUACAO: Record<SituacaoDoEmail, string> = {
  granted: "Aceitou",
  none: "Sem consentimento",
  unsubscribed: "Descadastrou",
  suppressed: "Bloqueado"
};
const ORIGEM: Record<string, string> = {
  billing: "Compra",
  free_access: "Acesso gratuito",
  manual: "Manual"
};

function hojeMenos(dias: number): string {
  return new Date(Date.now() - dias * 86_400_000).toISOString().slice(0, 10);
}

function dataBr(dia: string): string {
  return dia.split("-").reverse().join("/");
}

export function DashboardLeads() {
  const [seller, setSeller] = useState<Seller | null>(null);
  const [de, setDe] = useState(() => hojeMenos(DIAS_PADRAO));
  const [ate, setAte] = useState(() => hojeMenos(0));
  const [produto, setProduto] = useState<string>(TODOS);
  const [produtos, setProdutos] = useState<ChoiceOption<string>[]>([]);
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });

  const carregar = useCallback(async (loja: Seller, inicio: string, fim: string, filtro: string) => {
    setEstado({ fase: "carregando" });
    const query = new URLSearchParams({ seller: loja.id, from: inicio, to: fim });
    if (filtro !== TODOS) query.set("product", filtro);
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const resposta = await fetch(`/dashboard/leads/dados?${query}`, { cache: "no-store", signal: controller.signal });
      if (resposta.status === 404 || resposta.status === 403) return setEstado({ fase: "indisponivel" });
      if (!resposta.ok) return setEstado({ fase: "erro" });
      const leads = await resposta.json() as Leads;
      const linhas = leads.rows ?? [];
      // As opções de produto vêm dos leads do período sem filtro.
      if (filtro === TODOS) {
        const vistos = new Map(linhas.map((linha) => [linha.catalog_product_id, linha.product_title]));
        setProdutos([...vistos].map(([value, label]) => ({ value, label })));
      }
      setEstado({ fase: "pronto", leads: { ...leads, rows: linhas } });
    } catch {
      setEstado({ fase: "erro" });
    } finally {
      clearTimeout(timeout);
    }
  }, []);

  useEffect(() => {
    let ativo = true;
    void fetch("/api/sellers", { cache: "no-store" })
      .then(async (resposta) => {
        if (!ativo) return;
        if (!resposta.ok) return setEstado({ fase: "erro" });
        const corpo = await resposta.json() as { sellers?: Seller[] };
        const lojas = corpo.sellers ?? [];
        if (!canConfigureStore(lojas)) return setEstado({ fase: "sem_acesso" });
        setSeller(lojas[0]!);
        await carregar(lojas[0]!, hojeMenos(DIAS_PADRAO), hojeMenos(0), TODOS);
      })
      .catch(() => { if (ativo) setEstado({ fase: "erro" }); });
    return () => { ativo = false; };
  }, [carregar]);

  const opcoes = useMemo<ChoiceOption<string>[]>(
    () => [{ value: TODOS, label: "Todos os produtos" }, ...produtos],
    [produtos]
  );
  const csv = useMemo(() => {
    if (!seller) return null;
    const query = new URLSearchParams({ seller: seller.id, from: de, to: ate });
    if (produto !== TODOS) query.set("product", produto);
    return `/dashboard/leads/csv?${query}`;
  }, [seller, de, ate, produto]);

  return (
    <section className="panel" aria-labelledby="leads-filtro">
      <form
        className="form-row"
        onSubmit={(evento) => {
          evento.preventDefault();
          if (seller) void carregar(seller, de, ate, produto);
        }}
      >
        <h2 id="leads-filtro" className="visually-hidden">Filtro</h2>
        <label>
          De
          <input type="date" value={de} max={ate} onChange={(e) => setDe(e.target.value)} required/>
        </label>
        <label>
          Até
          <input type="date" value={ate} min={de} onChange={(e) => setAte(e.target.value)} required/>
        </label>
        <ChoiceSelect<string> label="Produto" id="leads_produto" value={produto} options={opcoes} onChange={setProduto} disabled={!seller}/>
        <button type="submit" className="button" disabled={!seller}>Atualizar</button>
      </form>

      {estado.fase === "carregando" ? <p role="status">Carregando…</p> : null}
      {estado.fase === "sem_acesso" ? <p>Só quem é dono ou admin da loja vê os leads.</p> : null}
      {estado.fase === "indisponivel" ? <p>A base de leads ainda não está disponível para a sua loja.</p> : null}
      {estado.fase === "erro" ? <p role="alert">Não conseguimos carregar os leads agora. Tente de novo.</p> : null}
      {estado.fase === "pronto" ? <Resultado leads={estado.leads} csv={csv}/> : null}
    </section>
  );
}

function Resultado({ leads, csv }: { leads: Leads; csv: string | null }) {
  return (
    <>
      <p>
        {leads.total_people} {leads.total_people === 1 ? "pessoa" : "pessoas"} no período.{" "}
        {leads.marketing_people} {leads.marketing_people === 1 ? "aceitou" : "aceitaram"} receber e-mail da loja.
      </p>
      {leads.marketing_people > 0 && csv ? (
        <p>
          <a className="button secondary" href={csv}>Exportar para marketing (CSV)</a>{" "}
          Leva só quem aceitou receber e-mail da loja e não se descadastrou.
        </p>
      ) : (
        <p>
          Ninguém pode receber e-mail de marketing ainda: só entra quem marcou “Quero receber novidades
          desta loja” na compra ou na conta.
        </p>
      )}
      {leads.total_rows > leads.rows.length ? (
        <p>Mostrando {leads.rows.length} de {leads.total_rows}. Use o filtro para ver o resto.</p>
      ) : null}
      {leads.rows.length === 0 ? <p>Ninguém entrou no período.</p> : (
        <table>
          <thead>
            <tr>
              <th scope="col">Nome</th><th scope="col">E-mail</th><th scope="col">Produto</th>
              <th scope="col">Como entrou</th><th scope="col">Desde</th><th scope="col">E-mail de marketing</th>
            </tr>
          </thead>
          <tbody>
            {leads.rows.map((linha) => (
              <tr key={linha.entitlement_id}>
                <td>{linha.name ?? "—"}</td>
                <td>{linha.email}</td>
                <td>{linha.product_title}</td>
                <td>{ORIGEM[linha.source] ?? linha.source}</td>
                <td>{dataBr(linha.since)}</td>
                <td>{SITUACAO[linha.email_status] ?? linha.email_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
