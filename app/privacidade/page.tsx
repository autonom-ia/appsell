import type { Metadata } from "next";
import { PaginaLegal } from "../legal";

export const metadata: Metadata = { title: "Política de Privacidade · Autonom.ia Sell" };

export default function Pagina() {
  return <PaginaLegal chave="privacidade" />;
}
