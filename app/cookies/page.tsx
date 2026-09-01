import type { Metadata } from "next";
import { PaginaLegal } from "../legal";

export const metadata: Metadata = { title: "Política de Cookies · Autonom.ia Sell",
  description: "Quais cookies a Autonom.ia Sell usa, para que servem e como recusar os que não são necessários."
};

export default function Pagina() {
  return <PaginaLegal chave="cookies" />;
}
