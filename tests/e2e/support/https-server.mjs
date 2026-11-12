// Sobe o build de produção em https://localhost:3000 para o e2e (#585).
//
// O e2e roda sobre o build, e o build emite os cookies como produção:
// prefixo __Host- e Secure. O WebKit do Playwright não guarda cookie Secure
// numa origem http, nem em localhost, e o login do webkit-mobile se perdia.
// Servir em HTTPS deixa o navegador tratar os cookies como no ar. O `next
// start` não tem HTTPS, então o mesmo handler do build roda num servidor
// https do Node, com um certificado autoassinado gerado pelo openssl a cada
// run. Chave e certificado ficam no $RUNNER_TEMP (o tmp do sistema fora do
// CI), são apagados do disco antes do servidor subir e nunca vão para o
// repositório nem para artefato.
//
// Host e porta vêm de APPSELL_PUBLIC_ORIGIN, que o playwright.config.ts
// preenche com E2E_BUILD_PUBLIC_ORIGIN: a URL existe num lugar só.
//
// Diferença de produção (#611): este servidor carrega o build pela API do
// Next (`next({ dev: false })`, sobre a pasta .next inteira). Produção roda o
// pacote standalone (`node server.js`, deploy/easypanel-native/Dockerfile.web),
// que leva só os arquivos que o build rastreou, mais .next/static e public
// copiados à parte. O e2e não pega arquivo que o rastreio deixou de fora, falta
// na cópia de static/public, nem diferença do servidor do standalone. O que
// cobre isso: o build da imagem web no CI e o smoke e o tester depois do
// deploy. Ver docs/ci/E2E_SOBRE_O_BUILD.md.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:https";
import { tmpdir } from "node:os";
import { join } from "node:path";

import next from "next";

const origin = new URL(process.env.APPSELL_PUBLIC_ORIGIN ?? "");
if (origin.protocol !== "https:" || !origin.port) {
  throw new Error("https-server.mjs exige APPSELL_PUBLIC_ORIGIN em https com porta explícita.");
}
const HOSTNAME = origin.hostname;
const PORT = Number(origin.port);

function selfSignedCertificate() {
  const directory = mkdtempSync(join(process.env.RUNNER_TEMP ?? tmpdir(), "appsell-e2e-tls-"));
  const keyPath = join(directory, "key.pem");
  const certPath = join(directory, "cert.pem");
  try {
    execFileSync("openssl", [
      "req", "-x509", "-nodes", "-days", "1",
      "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
      "-subj", `/CN=${HOSTNAME}`,
      "-addext", `subjectAltName=DNS:${HOSTNAME}`,
      "-keyout", keyPath,
      "-out", certPath
    ], { stdio: ["ignore", "ignore", "inherit"] });
    return { key: readFileSync(keyPath), cert: readFileSync(certPath) };
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

if (process.env.NODE_ENV !== "production") {
  throw new Error("https-server.mjs serve só o build de produção (NODE_ENV=production).");
}

const app = next({ dev: false, hostname: HOSTNAME, port: PORT });
await app.prepare();
const handle = app.getRequestHandler();
const server = createServer(selfSignedCertificate(), (request, response) => {
  handle(request, response);
});
server.listen(PORT, HOSTNAME);

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    server.close(() => process.exit(0));
  });
}
