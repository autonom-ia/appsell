import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

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
// servidor local, que serve os arquivos reais de tests/fixtures/google-fonts.
// Bytes falsos fariam o navegador cair na fonte de fallback, e a medida de
// layout do E2E deixaria de ser a da produção: no runner Linux, sem Arial, um
// botão a 320px passou a sair da tela (#513).
//
// A chave é a URL exata que o Next monta a partir do layout. Fonte ou peso novo
// no layout muda a URL, e o servidor falha com "Missing mocked response for
// URL": acrescentar a entrada aqui.
//
// Uso: o globalSetup do vitest importa `startGoogleFontsMock`; o E2E roda
// `node scripts/google-fonts-mock.mjs <comando> [...args]`, e o Playwright
// repassa o env ao webServer.
export const GOOGLE_FONTS = [
  {
    url: "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap",
    family: "Inter",
    faces: [{ weight: "100 900", file: "inter.woff2" }]
  },
  {
    url: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&display=swap",
    family: "Space Grotesk",
    faces: [{ weight: "300 700", file: "space-grotesk.woff2" }]
  },
  {
    url: "https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;800;900&display=swap",
    family: "Montserrat",
    // Um arquivo variável para os quatro pesos, como o próprio Google serve.
    faces: ["400", "600", "800", "900"].map((weight) => ({ weight, file: "montserrat.woff2" }))
  },
  {
    url: "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap",
    family: "IBM Plex Mono",
    faces: [
      { weight: "400", file: "ibm-plex-mono-400.woff2" },
      { weight: "500", file: "ibm-plex-mono-500.woff2" }
    ]
  }
];

const FONTS_DIRECTORY = fileURLToPath(new URL("../tests/fixtures/google-fonts/", import.meta.url));
// O mesmo intervalo do bloco `latin` que o Google serve.
const LATIN_RANGE =
  "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, " +
  "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD";

function css(origin, family, faces) {
  return faces
    .map(({ weight, file }) => [
      "/* latin */",
      "@font-face {",
      `  font-family: '${family}';`,
      "  font-style: normal;",
      `  font-weight: ${weight};`,
      "  font-display: swap;",
      `  src: url(${origin}/${file}) format('woff2');`,
      `  unicode-range: ${LATIN_RANGE};`,
      "}",
      ""
    ].join("\n"))
    .join("");
}

export async function startGoogleFontsMock() {
  const files = new Map();
  for (const font of GOOGLE_FONTS) {
    for (const { file } of font.faces) {
      if (!files.has(`/${file}`)) files.set(`/${file}`, await readFile(join(FONTS_DIRECTORY, file)));
    }
  }
  // Só os arquivos da lista, pelo nome exato: nada do disco além deles.
  const server = createServer((request, response) => {
    const bytes = request.method === "GET" ? files.get(request.url ?? "") : undefined;
    if (bytes) {
      response.writeHead(200, { "content-type": "font/woff2" });
      response.end(bytes);
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
  const responses = Object.fromEntries(GOOGLE_FONTS.map((font) => [font.url, css(origin, font.family, font.faces)]));
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
