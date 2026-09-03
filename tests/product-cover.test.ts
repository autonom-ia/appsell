import { describe, expect, it } from "vitest";

import {
  COVER_MAX_BYTES,
  coverAcceptAttribute,
  coverRejectionMessage,
  coverSizeLabel,
  describeCoverFile
} from "../lib/product-cover";
import { studentCountLabel } from "../lib/product-art";
import { failureState, nextClearKey, readCoverIntent } from "../app/components/product-image-panel";

describe("#232 — o navegador recusa antes de subir", () => {
  it("aceita os três formatos que o servidor aceita", () => {
    for (const type of ["image/jpeg", "image/png", "image/webp"]) {
      expect(describeCoverFile({ type, size: 4096 })).toEqual({
        ok: true,
        mediaType: type,
        byteSize: 4096
      });
    }
  });

  it("recusa um formato que o verificador recusaria depois", () => {
    // Recusar aqui é o que evita esperar 5 MB para ouvir um "não".
    for (const type of ["image/gif", "image/svg+xml", "application/pdf", ""]) {
      expect(describeCoverFile({ type, size: 4096 })).toEqual({ ok: false, reason: "type" });
    }
  });

  it("recusa arquivo vazio e arquivo acima do teto", () => {
    expect(describeCoverFile({ type: "image/png", size: 0 })).toEqual({ ok: false, reason: "empty" });
    expect(describeCoverFile({ type: "image/png", size: COVER_MAX_BYTES + 1 }))
      .toEqual({ ok: false, reason: "too_large" });
    expect(describeCoverFile({ type: "image/png", size: COVER_MAX_BYTES }).ok).toBe(true);
  });

  it("usa exatamente o mesmo teto do servidor", () => {
    // Se este número divergir do COVER_MAX_BYTES da API, o navegador aceita o
    // que o servidor depois recusa, e o erro chega tarde e sem explicação.
    expect(COVER_MAX_BYTES).toBe(5 * 1024 * 1024);
  });

  it("explica a recusa em português, com o tamanho legível", () => {
    expect(coverRejectionMessage("type")).toContain("JPEG");
    expect(coverRejectionMessage("too_large")).toContain("5 MB");
    expect(coverSizeLabel(12 * 1024 * 1024)).toBe("12 MB");
  });

  it("oferece ao seletor de arquivo só o que aceita", () => {
    expect(coverAcceptAttribute()).toBe("image/jpeg,image/png,image/webp");
  });
});

describe("#232 — o painel diz qual foi o problema", () => {
  it("separa sessão, conflito, recusa e indisponibilidade", () => {
    expect(failureState(401)).toBe("forbidden");
    expect(failureState(403)).toBe("forbidden");
    expect(failureState(409)).toBe("conflict");
    expect(failureState(422)).toBe("invalid");
    expect(failureState(413)).toBe("invalid");
    expect(failureState(503)).toBe("unavailable");
    expect(failureState(500)).toBe("error");
  });
});

describe("A4.6 — a contagem de alunos na lista", () => {
  it("diz zero em vez de sumir", () => {
    // Esconder o número faria a linha aparecer e sumir de card em card.
    expect(studentCountLabel(0)).toBe("0 alunos");
    expect(studentCountLabel(undefined)).toBe("0 alunos");
    expect(studentCountLabel(null)).toBe("0 alunos");
  });

  it("concorda com o singular", () => {
    expect(studentCountLabel(1)).toBe("1 aluno");
    expect(studentCountLabel(2)).toBe("2 alunos");
  });

  it("nunca inventa um número a partir de lixo", () => {
    expect(studentCountLabel(-5)).toBe("0 alunos");
    expect(studentCountLabel(1.5)).toBe("0 alunos");
  });

  it("separa milhar como o vendedor lê", () => {
    expect(studentCountLabel(1234)).toBe("1.234 alunos");
  });
});

describe("#232 — a chave de remoção vale para o estado em que nasceu", () => {
  const mint = () => "minted-key";

  it("repete a mesma chave enquanto a versão do produto não muda", () => {
    // Retentativa depois de conexão caída precisa repetir a operação, não somar
    // uma segunda.
    const first = nextClearKey(null, 7, mint);
    expect(nextClearKey(first, 7, () => "outra")).toBe(first);
  });

  it("troca de chave quando o produto mudou entre as tentativas", () => {
    // Salvar o formulário ou trocar a capa incrementa a versão. A operação já
    // não é a mesma, e reaproveitar a chave faria o servidor responder pela
    // antiga enquanto a tela relata a nova.
    const first = nextClearKey(null, 7, mint);
    const second = nextClearKey(first, 8, () => "nova");

    expect(second).not.toBe(first);
    expect(second).toEqual({ key: "nova", productVersion: 8 });
  });

  it("cunha uma chave quando não havia nenhuma", () => {
    expect(nextClearKey(null, 3, mint)).toEqual({ key: "minted-key", productVersion: 3 });
  });
});

describe("#232 — o intent é lido, não presumido", () => {
  const VALID = {
    upload_id: "u-1",
    upload_url: "https://assets.example/quarantine/cover?X-Amz-Signature=s",
    required_headers: { "content-type": "image/png" },
    required_content_length: 4096,
    product_version: 3
  };

  it("aceita o contrato completo", () => {
    expect(readCoverIntent(VALID)).toEqual(VALID);
  });

  it.each([
    { ...VALID, upload_id: "" },
    { ...VALID, upload_url: 42 },
    { ...VALID, required_headers: null },
    { ...VALID, required_headers: ["content-type"] },
    { ...VALID, required_headers: { "content-type": 7 } },
    { ...VALID, required_content_length: "4096" },
    { ...VALID, product_version: 1.5 },
    null,
    "intent"
  ])("recusa um contrato quebrado %#", (value) => {
    // Sem esta leitura, `intent.required_headers` indefinido viraria um PUT que
    // o armazenamento recusa sem dizer por quê.
    expect(readCoverIntent(value)).toBeNull();
  });
});
