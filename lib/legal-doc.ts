import { readFile } from "node:fs/promises";
import path from "node:path";

// Os tres documentos legais moram em markdown para serem revisados como texto,
// nao como JSX. O conversor abaixo cobre exatamente o que eles usam: titulos,
// paragrafos, listas, negrito, codigo e link. Nada alem disso, para nao virar
// um renderizador de markdown de proposito geral.
export type DocumentoLegal = {
  readonly titulo: string;
  readonly html: string;
};

const ARQUIVOS = {
  termos: "TERMOS_DE_USO.md",
  privacidade: "POLITICA_DE_PRIVACIDADE.md",
  cookies: "POLITICA_DE_COOKIES.md"
} as const;

export type ChaveLegal = keyof typeof ARQUIVOS;

const TITULOS: Record<ChaveLegal, string> = {
  termos: "Termos de Uso",
  privacidade: "Política de Privacidade",
  cookies: "Política de Cookies"
};

function escapar(texto: string) {
  return texto
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function inline(texto: string) {
  return escapar(texto)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((\/[^)]*|https:\/\/[^)]*|mailto:[^)]*)\)/g, '<a href="$2">$1</a>');
}

function converter(markdown: string) {
  const saida: string[] = [];
  let lista: string[] = [];
  let tipo: "ul" | "ol" | null = null;

  const fecharLista = () => {
    if (!lista.length || !tipo) return;
    saida.push(`<${tipo}>${lista.map((i) => `<li>${i}</li>`).join("")}</${tipo}>`);
    lista = [];
    tipo = null;
  };

  for (const linha of markdown.split("\n").map((l) => l.trimEnd())) {
    // O bloco de citacao no topo do arquivo e nota de trabalho, nao conteudo.
    if (!linha.trim() || linha.startsWith(">")) {
      fecharLista();
      continue;
    }
    if (linha.startsWith("### ")) {
      fecharLista();
      saida.push(`<h3>${inline(linha.slice(4))}</h3>`);
    } else if (linha.startsWith("## ")) {
      fecharLista();
      const numerado = /^(\d+)\.\s*(.*)$/.exec(linha.slice(3));
      saida.push(numerado
        ? `<h2><i>${numerado[1]}</i>${inline(numerado[2])}</h2>`
        : `<h2>${inline(linha.slice(3))}</h2>`);
    } else if (linha.startsWith("# ")) {
      fecharLista();
    } else if (/^\d+\.\s/.test(linha)) {
      if (tipo !== "ol") { fecharLista(); tipo = "ol"; }
      lista.push(inline(linha.replace(/^\d+\.\s/, "")));
    } else if (linha.startsWith("- ")) {
      if (tipo !== "ul") { fecharLista(); tipo = "ul"; }
      lista.push(inline(linha.slice(2)));
    } else {
      fecharLista();
      saida.push(`<p>${inline(linha)}</p>`);
    }
  }
  fecharLista();
  // O markdown quebra linha por largura de coluna; parágrafos vizinhos são a
  // mesma frase e voltam a ser um só.
  return saida.join("\n").replace(/<\/p>\n<p>/g, " ");
}

export async function lerDocumentoLegal(chave: ChaveLegal): Promise<DocumentoLegal> {
  const arquivo = path.join(process.cwd(), "content", "legal", ARQUIVOS[chave]);
  return { titulo: TITULOS[chave], html: converter(await readFile(arquivo, "utf8")) };
}
