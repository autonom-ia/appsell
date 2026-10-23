import { createServer, type Server, type Socket } from "node:net";

import { afterEach, describe, expect, it, vi } from "vitest";

import { enviarContatoComercial } from "@/lib/email/contato-comercial";

// O envio do contato comercial é o único caminho do web que usa o nodemailer
// de verdade. Um major do pacote (9 → 10, #449) só fica provado se uma conversa
// SMTP real acontecer: um mock do transporte passaria com a biblioteca quebrada.
type Recebido = { readonly comandos: string[]; readonly dados: string };

function servidorSmtp(): Promise<{ porta: number; recebido: Promise<Recebido>; fechar: () => Promise<void> }> {
  return new Promise((resolve) => {
    let entregar: (valor: Recebido) => void = () => undefined;
    const recebido = new Promise<Recebido>((pronto) => {
      entregar = pronto;
    });
    const conexoes = new Set<Socket>();
    const server: Server = createServer((socket) => {
      conexoes.add(socket);
      const comandos: string[] = [];
      let dados = "";
      let emDados = false;
      let pendente = "";
      socket.write("220 teste ESMTP\r\n");
      socket.on("data", (pedaco) => {
        pendente += pedaco.toString("utf8");
        let fim = pendente.indexOf("\r\n");
        while (fim >= 0) {
          const linha = pendente.slice(0, fim);
          pendente = pendente.slice(fim + 2);
          fim = pendente.indexOf("\r\n");
          if (emDados) {
            if (linha === ".") {
              emDados = false;
              socket.write("250 aceito\r\n");
            } else {
              dados += `${linha}\n`;
            }
            continue;
          }
          comandos.push(linha);
          const verbo = linha.split(" ", 1)[0]!.toUpperCase();
          if (verbo === "EHLO" || verbo === "HELO") socket.write("250-teste\r\n250 AUTH PLAIN LOGIN\r\n");
          else if (verbo === "AUTH") socket.write("235 ok\r\n");
          else if (verbo === "DATA") {
            emDados = true;
            socket.write("354 manda\r\n");
          } else if (verbo === "QUIT") {
            socket.write("221 tchau\r\n");
            socket.end();
            entregar({ comandos, dados });
          } else socket.write("250 ok\r\n");
        }
      });
      socket.on("close", () => {
        conexoes.delete(socket);
        entregar({ comandos, dados });
      });
    });
    server.listen(0, "127.0.0.1", () => {
      const endereco = server.address();
      if (endereco === null || typeof endereco === "string") throw new Error("porta indisponível");
      resolve({
        porta: endereco.port,
        recebido,
        fechar: () =>
          new Promise((pronto) => {
            for (const conexao of conexoes) conexao.destroy();
            server.close(() => pronto());
          })
      });
    });
  });
}

describe("contato comercial por SMTP", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("entrega a mensagem numa conversa SMTP real, com remetente, destino e resposta certos", async () => {
    const smtp = await servidorSmtp();
    try {
      vi.stubEnv("SMTP_HOST_COMERCIAL", "127.0.0.1");
      vi.stubEnv("SMTP_PORT_COMERCIAL", String(smtp.porta));
      vi.stubEnv("SMTP_SECURE_COMERCIAL", "false");
      vi.stubEnv("SMTP_USER_COMERCIAL", "comercial@example.test");
      vi.stubEnv("SMTP_PASS_COMERCIAL", "senha-de-teste");
      vi.stubEnv("SMTP_TO_COMERCIAL", "vendas@example.test");

      const enviado = await enviarContatoComercial({
        nome: "Maria",
        email: "maria@example.test",
        loja: "Loja da Maria",
        mensagem: "Quero vender meu curso."
      });

      expect(enviado).toBe(true);
      const { comandos, dados } = await smtp.recebido;
      expect(comandos).toContain("MAIL FROM:<comercial@example.test>");
      expect(comandos).toContain("RCPT TO:<vendas@example.test>");
      expect(comandos.some((comando) => comando.startsWith("AUTH "))).toBe(true);
      expect(dados).toContain("Reply-To: maria@example.test");
      expect(dados).toContain("Quero vender meu curso.");
    } finally {
      await smtp.fechar();
    }
  });

  it("devolve false, sem lançar, quando o servidor recusa o envio", async () => {
    const recusa = createServer((socket) => {
      socket.write("554 recusado\r\n");
      socket.end();
    });
    await new Promise<void>((pronto) => recusa.listen(0, "127.0.0.1", () => pronto()));
    const endereco = recusa.address();
    if (endereco === null || typeof endereco === "string") throw new Error("porta indisponível");
    try {
      vi.stubEnv("SMTP_HOST_COMERCIAL", "127.0.0.1");
      vi.stubEnv("SMTP_PORT_COMERCIAL", String(endereco.port));
      vi.stubEnv("SMTP_SECURE_COMERCIAL", "false");
      vi.stubEnv("SMTP_USER_COMERCIAL", "comercial@example.test");
      vi.stubEnv("SMTP_PASS_COMERCIAL", "senha-de-teste");

      await expect(
        enviarContatoComercial({ nome: "Maria", email: "maria@example.test", loja: "Loja", mensagem: "Oi" })
      ).resolves.toBe(false);
    } finally {
      await new Promise<void>((pronto) => recusa.close(() => pronto()));
    }
  });
});
