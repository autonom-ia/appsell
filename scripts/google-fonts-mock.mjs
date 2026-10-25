import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

// Quem sobe `next dev` nos testes não vai ao Google Fonts (#457, #513).
//
// O app/layout.tsx importa quatro fontes de `next/font/google`. No primeiro
// compile, o Next busca o CSS em fonts.googleapis.com e os arquivos em
// fonts.gstatic.com. Sem mock, o teste depende da rede, e uma resposta do
// Google em outro formato (URL `/l/font?kit=…`) já derrubou o servidor do E2E
// (#513).
//
// O Next lê NEXT_FONT_GOOGLE_MOCKED_RESPONSES para o CSS. O Turbopack ainda
// baixa os arquivos de fonte de verdade, então o CSS de mock aponta para um
// servidor local, que responde qualquer `.woff2` com bytes fixos.
//
// A chave é a URL exata que o Next monta a partir do layout. Fonte ou peso novo
// no layout muda a URL, e o servidor falha com "Missing mocked response for
// URL": acrescentar a entrada aqui.
//
// Uso: o globalSetup do vitest importa `startGoogleFontsMock`; o E2E roda
// `node scripts/google-fonts-mock.mjs <comando> [...args]`, e o Playwright
// repassa o env ao webServer.
export const GOOGLE_FONTS = [
  { url: "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap", family: "Inter", weights: ["100 900"] },
  { url: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&display=swap", family: "Space Grotesk", weights: ["300 700"] },
  { url: "https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;800;900&display=swap", family: "Montserrat", weights: ["400", "600", "800", "900"] },
  { url: "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap", family: "IBM Plex Mono", weights: ["400", "500"] }
];

const FONT_BYTES = Buffer.from("wOF2-mock-appsell");

function css(origin, family, weights) {
  const slug = family.toLowerCase().split(" ").join("-");
  return weights
    .map((weight) => [
      "/* latin */",
      "@font-face {",
      `  font-family: '${family}';`,
      "  font-style: normal;",
      `  font-weight: ${weight};`,
      "  font-display: swap;",
      `  src: url(${origin}/${slug}-${weight.split(" ").join("-")}.woff2) format('woff2');`,
      "  unicode-range: U+0000-00FF;",
      "}",
      ""
    ].join("\n"))
    .join("");
}

export async function startGoogleFontsMock() {
  const server = createServer((request, response) => {
    if (request.method === "GET" && (request.url ?? "").endsWith(".woff2")) {
      response.writeHead(200, { "content-type": "font/woff2" });
      response.end(FONT_BYTES);
      return;
    }
    response.writeHead(404);
    response.end();
  });
  await new Promise((ready) => server.listen(0, "127.0.0.1", ready));
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Could not allocate the Google Fonts mock port.");
  const origin = `http://127.0.0.1:${address.port}`;

  const directory = await mkdtemp(join(tmpdir(), "appsell-fontes-"));
  const file = join(directory, "respostas.cjs");
  const responses = Object.fromEntries(GOOGLE_FONTS.map((font) => [font.url, css(origin, font.family, font.weights)]));
  await writeFile(file, `module.exports = ${JSON.stringify(responses, null, 2)};\n`, "utf8");

  return {
    file,
    async close() {
      await new Promise((closed) => server.close(() => closed()));
      await rm(directory, { recursive: true, force: true });
    }
  };
}

async function runWithMock(command, args) {
  if (!command) throw new Error("Usage: google-fonts-mock.mjs <command> [...args]");
  const mock = await startGoogleFontsMock();
  try {
    const child = spawn(command, args, {
      stdio: "inherit",
      env: { ...process.env, NEXT_FONT_GOOGLE_MOCKED_RESPONSES: mock.file }
    });
    const forward = (signal) => child.kill(signal);
    process.once("SIGINT", forward);
    process.once("SIGTERM", forward);
    process.exitCode = await new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code, signal) => resolve(code ?? (signal ? 1 : 0)));
    });
  } finally {
    await mock.close();
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? "").href) {
  await runWithMock(process.argv[2], process.argv.slice(3));
}
