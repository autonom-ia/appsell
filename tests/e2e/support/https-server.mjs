// Sobe o build de produção em https://localhost:3000 para o e2e (#585).
//
// O e2e roda sobre o build, e o build emite os cookies como produção:
// prefixo __Host- e Secure. O WebKit do Playwright não guarda cookie Secure
// numa origem http, nem em localhost, e o login do webkit-mobile se perdia.
// Servir em HTTPS deixa o navegador tratar os cookies como no ar. O `next
// start` não tem HTTPS, então o mesmo handler do build roda num servidor
// https do Node, com um certificado autoassinado gerado a cada run e apagado
// do disco antes do servidor subir.
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:https";
import { tmpdir } from "node:os";
import { join } from "node:path";

import next from "next";

const HOSTNAME = "localhost";
const PORT = 3000;

function selfSignedCertificate() {
  const directory = mkdtempSync(join(tmpdir(), "appsell-e2e-tls-"));
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
