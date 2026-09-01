import type { Metadata } from "next";
import { PaginaLegal } from "../legal";

export const metadata: Metadata = { title: "Termos de Uso · Autonom.ia Sell",
  description: "Termos de uso da Autonom.ia Sell: como funciona a venda, o repasse, o acesso do aluno e as responsabilidades de cada parte."
};

export default function Pagina() {
  return <PaginaLegal chave="termos" />;
}
