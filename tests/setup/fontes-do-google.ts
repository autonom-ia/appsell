import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Os testes que sobem `next dev` deixam de ir ao Google Fonts (#457).
//
// O app/layout.tsx importa quatro fontes de `next/font/google`. No primeiro
// compile, o Next busca o CSS em fonts.googleapis.com e os arquivos em
// fonts.gstatic.com, com timeout de 3 s e 3 retries por pedido. O tempo até o
// BFF responder passava a depender da rede, e o teste caía como "intermitente".
//
// O Next lê NEXT_FONT_GOOGLE_MOCKED_RESPONSES para o CSS. O Turbopack ainda
// baixa os arquivos de fonte de verdade, então o CSS de mock aponta para um
// servidor local, que responde qualquer `.woff2` com bytes fixos.
//
// A chave é a URL exata que o Next monta a partir do layout. Fonte ou peso novo
// no layout muda a URL, e o BFF falha com "Missing mocked response for URL":
// acrescentar a entrada aqui.
const FONTES = [
  { url: "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap", familia: "Inter", pesos: ["100 900"] },
  { url: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&display=swap", familia: "Space Grotesk", pesos: ["300 700"] },
  { url: "https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;800;900&display=swap", familia: "Montserrat", pesos: ["400", "600", "800", "900"] },
  { url: "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap", familia: "IBM Plex Mono", pesos: ["400", "500"] }
] as const;

const BYTES_DA_FONTE = Buffer.from("wOF2-mock-appsell");

function css(origem: string, familia: string, pesos: readonly string[]): string {
  const slug = familia.toLowerCase().split(" ").join("-");
  return pesos
    .map((peso) => [
      "/* latin */",
      "@font-face {",
      `  font-family: '${familia}';`,
      "  font-style: normal;",
      `  font-weight: ${peso};`,
      "  font-display: swap;",
      `  src: url(${origem}/${slug}-${peso.split(" ").join("-")}.woff2) format('woff2');`,
      "  unicode-range: U+0000-00FF;",
      "}",
      ""
    ].join("\n"))
    .join("");
}

export default async function preparar(): Promise<() => Promise<void>> {
  const servidor = createServer((pedido, resposta) => {
    if (pedido.method === "GET" && (pedido.url ?? "").endsWith(".woff2")) {
      resposta.writeHead(200, { "content-type": "font/woff2" });
      resposta.end(BYTES_DA_FONTE);
      return;
    }
    resposta.writeHead(404);
    resposta.end();
  });
  await new Promise<void>((pronto) => servidor.listen(0, "127.0.0.1", pronto));
  const { port } = servidor.address() as AddressInfo;
  const origem = `http://127.0.0.1:${port}`;

  const pasta = await mkdtemp(join(tmpdir(), "appsell-fontes-"));
  const arquivo = join(pasta, "respostas.cjs");
  const respostas = Object.fromEntries(FONTES.map((fonte) => [fonte.url, css(origem, fonte.familia, fonte.pesos)]));
  await writeFile(arquivo, `module.exports = ${JSON.stringify(respostas, null, 2)};\n`, "utf8");
  process.env.NEXT_FONT_GOOGLE_MOCKED_RESPONSES = arquivo;

  return async () => {
    await new Promise<void>((pronto) => servidor.close(() => pronto()));
    await rm(pasta, { recursive: true, force: true });
  };
}
