import { afterEach, describe, expect, it, vi } from "vitest";

// O módulo de rota importa `server-only`, que não existe fora do Next.
vi.mock("server-only", () => ({}));

import { learningError } from "../app/api/library/items/_route-shared";
import { DomainApiError } from "../lib/internal-api-client.server";

/**
 * O motivo da falha na área do aluno não pode se perder.
 *
 * Em 30/08/2026 um aluno clicou em "Começar curso" e recebeu "Não foi possível
 * carregar a aula — verifique sua conexão". A requisição tinha chegado ao nosso
 * servidor e voltado em 160 ms; conexão era a única coisa que certamente não
 * era.
 *
 * `web` e `api` não escreveram **uma única linha** durante duas tentativas: o
 * erro era descartado pelo catch-all dos dois lados. Uma investigação de uma
 * hora descartou publicação, funções do banco, rota, chave interna, Redis e
 * saúde da API — e não chegou ao motivo, porque o motivo tinha sido jogado
 * fora.
 *
 * A resposta ao aluno continua vaga de propósito: ele não pode descobrir, pelo
 * erro, o que existe do outro lado. O que muda é o servidor saber.
 */
describe("a falha na área do aluno deixa rastro", () => {
  afterEach(() => vi.restoreAllMocks());

  function capturar() {
    const linhas: string[] = [];
    vi.spyOn(console, "error").mockImplementation((linha: unknown) => {
      linhas.push(String(linha));
    });
    return linhas;
  }

  it.each([
    "learning_unavailable",
    "pdf_viewer_busy",
    // Emitido por `_domain_error()` de outro módulo. Uma allowlist de nomes não
    // o continha, e o caminho perdia a classificação — foi por isso que a
    // validação passou a ser de forma, não de lista.
    "domain_unavailable",
    "draft_version_conflict"
  ])("registra o código quando ele tem forma de código: %s", async (codigo) => {
    const linhas = capturar();
    const resposta = learningError(new DomainApiError(500, codigo));
    expect(resposta.status).toBe(503);
    const registro = JSON.parse(linhas[0]);
    expect(registro.event).toBe("learning_request_failed");
    expect(registro.upstream_status).toBe(500);
    expect(registro.reason).toBe(`domain_api_${codigo}`);
  });

  it.each([
    "usuario joao@exemplo.com nao encontrado",
    "select * from identity_user where email='joao@exemplo.com'",
    "Erro: senha inválida",
    ""
  ])("não copia para o log o que não tem forma de código: %s", async (detalhe) => {
    // `DomainApiError.code` é o `detail` da resposta, repassado sem validação.
    // Hoje são códigos fixos, mas uma rota nova ou um proxy poderiam pôr texto
    // livre ali — e a primeira versão deste teste **exigia** que ele fosse
    // gravado, consolidando o vazamento em vez de impedi-lo.
    const linhas = capturar();
    learningError(new DomainApiError(500, detalhe));
    // Todas as linhas, não só a primeira: um segundo registro vazando o valor
    // passaria despercebido se olhássemos apenas `linhas[0]`.
    expect(linhas).toHaveLength(1);
    expect(linhas.join(" ")).not.toContain("joao@exemplo.com");
    expect(linhas.join(" ")).not.toContain("identity_user");
    const registro = JSON.parse(linhas[0]);
    expect(registro.reason).toBe("domain_api_codigo_invalido");
    expect(registro.upstream_status).toBe(500);
  });

  it("o 403 é silencioso, como o 404: não revela existência", async () => {
    const linhas = capturar();
    const resposta = learningError(new DomainApiError(403, "forbidden"));
    expect(resposta.status).toBe(404);
    expect(linhas).toHaveLength(0);
  });

  it("separa envelope recusado de indisponibilidade", async () => {
    const linhas = capturar();
    // 401 da API é o envelope interno sendo recusado — um problema entre BFF e
    // API. Sem isto era indistinguível de o banco ter caído.
    const resposta = learningError(new DomainApiError(401, "invalid_internal_request"));
    expect(resposta.status).toBe(503);
    expect(JSON.parse(linhas[0]).reason).toBe("internal_request_rejected");
    expect(JSON.parse(linhas[0]).upstream_status).toBe(401);
  });

  it("classifica o que não é erro de domínio sem repetir a mensagem", async () => {
    const linhas = capturar();
    learningError(new Error("detalhe interno que não deve vazar"));
    const registro = JSON.parse(linhas[0]);
    expect(registro.reason).toBe("erro_nao_classificado");
    expect(linhas[0]).not.toContain("detalhe interno");
  });

  it("não registra nada quando a resposta é 404, que já é específica", async () => {
    const linhas = capturar();
    const resposta = learningError(new DomainApiError(404, "not_found"));
    expect(resposta.status).toBe(404);
    expect(linhas).toHaveLength(0);
  });
});
