"use client";

import { useCallback, useEffect, useState } from "react";

import { canConfigureStore } from "@/lib/shell-navigation";

interface Seller {
  id: string;
  role: string;
}

interface SalesRow {
  day: string;
  catalog_product_id: string;
  product_title: string;
  approved: number;
  refunded: number;
}

type Estado =
  | { fase: "carregando" }
  | { fase: "sem_acesso" }
  | { fase: "indisponivel" }
  | { fase: "erro" }
  | { fase: "pronto"; linhas: SalesRow[] };

const FETCH_TIMEOUT_MS = 8_000;
const DIAS_PADRAO = 30;

function hojeMenos(dias: number): string {
  const data = new Date(Date.now() - dias * 86_400_000);
  return data.toISOString().slice(0, 10);
}

export function DashboardSalesReport() {
  const [seller, setSeller] = useState<Seller | null>(null);
  const [de, setDe] = useState(() => hojeMenos(DIAS_PADRAO));
  const [ate, setAte] = useState(() => hojeMenos(0));
  const [estado, setEstado] = useState<Estado>({ fase: "carregando" });

  const carregar = useCallback(async (loja: Seller, inicio: string, fim: string) => {
    setEstado({ fase: "carregando" });
    const query = new URLSearchParams({ seller: loja.id, from: inicio, to: fim });
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    try {
      const resposta = await fetch(`/dashboard/vendas/dados?${query}`, {
        cache: "no-store",
        signal: controller.signal
      });
      if (resposta.status === 404 || resposta.status === 403) return setEstado({ fase: "indisponivel" });
      if (!resposta.ok) return setEstado({ fase: "erro" });
      const corpo = await resposta.json() as { rows?: SalesRow[] };
      setEstado({ fase: "pronto", linhas: corpo.rows ?? [] });
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
        await carregar(lojas[0]!, hojeMenos(DIAS_PADRAO), hojeMenos(0));
      })
      .catch(() => { if (ativo) setEstado({ fase: "erro" }); });
    return () => { ativo = false; };
  }, [carregar]);

  const totalVendas = estado.fase === "pronto" ? estado.linhas.reduce((t, l) => t + l.approved, 0) : 0;
  const totalEstornos = estado.fase === "pronto" ? estado.linhas.reduce((t, l) => t + l.refunded, 0) : 0;
  const csv = seller ? `/dashboard/vendas/csv?${new URLSearchParams({ seller: seller.id, from: de, to: ate })}` : null;

  return (
    <section className="panel" aria-labelledby="vendas-periodo">
      <form
        className="form-row"
        onSubmit={(evento) => {
          evento.preventDefault();
          if (seller) void carregar(seller, de, ate);
        }}
      >
        <h2 id="vendas-periodo" className="visually-hidden">Período</h2>
        <label>
          De
          <input type="date" value={de} max={ate} onChange={(e) => setDe(e.target.value)} required/>
        </label>
        <label>
          Até
          <input type="date" value={ate} min={de} onChange={(e) => setAte(e.target.value)} required/>
        </label>
        <button type="submit" className="button" disabled={!seller}>Atualizar</button>
        {csv && estado.fase === "pronto" ? <a className="button secondary" href={csv}>Baixar CSV</a> : null}
      </form>

      {estado.fase === "carregando" ? <p role="status">Carregando…</p> : null}
      {estado.fase === "sem_acesso" ? <p>Só quem é dono ou admin da loja vê as vendas.</p> : null}
      {estado.fase === "indisponivel" ? <p>O relatório de vendas ainda não está disponível para a sua loja.</p> : null}
      {estado.fase === "erro" ? <p role="alert">Não conseguimos carregar as vendas agora. Tente de novo.</p> : null}
      {estado.fase === "pronto" ? (
        <>
          <p>
            {totalVendas} {totalVendas === 1 ? "venda" : "vendas"} e {totalEstornos}{" "}
            {totalEstornos === 1 ? "estorno" : "estornos"} no período.
          </p>
          {estado.linhas.length === 0 ? <p>Nenhuma venda no período.</p> : (
            <table>
              <thead>
                <tr><th scope="col">Dia</th><th scope="col">Produto</th><th scope="col">Vendas</th><th scope="col">Estornos</th></tr>
              </thead>
              <tbody>
                {estado.linhas.map((linha) => (
                  <tr key={`${linha.day}-${linha.catalog_product_id}`}>
                    <td>{linha.day.split("-").reverse().join("/")}</td>
                    <td>{linha.product_title}</td>
                    <td>{linha.approved}</td>
                    <td>{linha.refunded}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : null}
    </section>
  );
}
