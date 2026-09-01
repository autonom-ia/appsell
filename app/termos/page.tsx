import type { Metadata } from "next";
import { PaginaLegal } from "../legal";

export const metadata: Metadata = { title: "Termos de Uso · Autonom.ia Sell" };

export default function Pagina() {
  return <PaginaLegal chave="termos" />;
}
