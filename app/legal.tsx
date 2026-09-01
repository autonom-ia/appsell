import Link from "next/link";
import type { ChaveLegal } from "@/lib/legal-doc";
import { lerDocumentoLegal } from "@/lib/legal-doc";
import "./legal.css";

export const DATA_EM_VIGOR = "31 de agosto de 2026";

export async function PaginaLegal({ chave }: { chave: ChaveLegal }) {
  const doc = await lerDocumentoLegal(chave);
  return (
    <div className="legal">
      <header className="legal-topo">
        <Link className="legal-marca" href="/">
          <span className="ico" />
          <span><b>Autonom.ia</b><small>Sell</small></span>
        </Link>
        <Link className="legal-voltar" href="/">Voltar ao site</Link>
      </header>
      <main className="legal-corpo">
        <p className="legal-eyebrow">Documentos da plataforma</p>
        <h1>{doc.titulo}</h1>
        <p className="legal-data">Em vigor desde {DATA_EM_VIGOR}</p>
        <div className="legal-texto" dangerouslySetInnerHTML={{ __html: doc.html }} />
      </main>
      <footer className="legal-rodape">
        <span>© 2026 Autonom.ia Tecnologia Ltda.</span>
        <span>Dúvidas: <a href="mailto:contato@autonomia.site">contato@autonomia.site</a></span>
      </footer>
    </div>
  );
}
