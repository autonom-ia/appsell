import type { Metadata } from "next";
import { PaginaLegal } from "../legal";

export const metadata: Metadata = { title: "Política de Privacidade · Autonom.ia Sell",
  description: "Como a Autonom.ia Sell trata dados pessoais de vendedores e alunos, com que finalidade e por quanto tempo."
};

export default function Pagina() {
  return <PaginaLegal chave="privacidade" />;
}
