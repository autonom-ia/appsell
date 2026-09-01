import { describe, expect, it } from "vitest";
import {
  browserCoverUploadIntent,
  browserMultipartUploadIntent,
  browserUploadIntent,
  browserUploadPart,
  configuredStorageUploadOrigin,
  storageUploadConfigurationReady,
  trustedProductCover,
  trustedStorageDownloadUrl
} from "../lib/storage-upload-policy.server";

const NOW = Date.parse("2026-07-11T20:00:00Z");
const APP_ORIGIN = "https://sell.autonomia.site";
const STORAGE_ORIGIN = "https://assets.autonomia.site";
const VALID = {
  upload_id: "55000000-0000-4000-8000-000000000001",
  asset_id: "55000000-0000-4000-8000-000000000002",
  upload_url: `${STORAGE_ORIGIN}/appsell-quarantine/object?X-Amz-Signature=signed`,
  expires_at: "2026-07-11T20:05:00.000Z",
  required_headers: {
    "Content-Type": "application/pdf",
    "x-amz-checksum-sha256": "checksum",
    "x-amz-meta-appsell-upload-id": "55000000-0000-4000-8000-000000000001"
  }
};

describe("storage upload boundary", () => {
  it("accepts only the configured cross-origin signed destination", () => {
    expect(browserUploadIntent(VALID, APP_ORIGIN, STORAGE_ORIGIN, NOW)).toMatchObject({
      upload_origin: STORAGE_ORIGIN,
      upload_url: VALID.upload_url
    });
  });

  it.each([
    { ...VALID, upload_url: `${APP_ORIGIN}/api/collect?X-Amz-Signature=signed` },
    { ...VALID, upload_url: "https://evil.example/object?X-Amz-Signature=signed" },
    { ...VALID, expires_at: "2026-07-11T19:59:59.000Z" },
    { ...VALID, expires_at: "2026-07-11T20:10:00.000Z" },
    { ...VALID, required_headers: { ...VALID.required_headers, Authorization: "secret" } }
  ])("rejects an unsafe or stale intent %#", (intent) => {
    expect(browserUploadIntent(intent, APP_ORIGIN, STORAGE_ORIGIN, NOW)).toBeNull();
  });

  it("requires HTTPS for the configured production storage origin", () => {
    expect(() => configuredStorageUploadOrigin({
      NODE_ENV: "production",
      APPSELL_STORAGE_PUBLIC_ORIGIN: "http://assets.autonomia.site"
    })).toThrow(/HTTPS/);
  });

  it("fails production readiness when the browser storage origin is absent or invalid", () => {
    expect(storageUploadConfigurationReady({ APP_ENV: "production" })).toBe(false);
    expect(storageUploadConfigurationReady({
      APP_ENV: "production",
      APPSELL_STORAGE_PUBLIC_ORIGIN: "https://assets.autonomia.site/"
    })).toBe(false);
    expect(storageUploadConfigurationReady({
      APP_ENV: "production",
      APPSELL_STORAGE_PUBLIC_ORIGIN: STORAGE_ORIGIN
    })).toBe(true);
    expect(storageUploadConfigurationReady({ APP_ENV: "ci" })).toBe(true);
  });

  it("accepts downloads only from the exact configured storage origin", () => {
    const signed = `${STORAGE_ORIGIN}/ready/material.pdf?X-Amz-Signature=signed`;
    expect(trustedStorageDownloadUrl(signed, 60, STORAGE_ORIGIN, {
      NODE_ENV: "production"
    })).toBe(true);
    expect(trustedStorageDownloadUrl(
      "https://evil.example/ready/material.pdf?X-Amz-Signature=signed",
      60,
      STORAGE_ORIGIN,
      { NODE_ENV: "production" }
    )).toBe(false);
    expect(trustedStorageDownloadUrl(
      "https://assets.autonomia.site.evil.example/material.pdf?X-Amz-Signature=signed",
      60,
      STORAGE_ORIGIN,
      { NODE_ENV: "production" }
    )).toBe(false);
  });

  it("rejects an HTTP download origin in production", () => {
    expect(trustedStorageDownloadUrl(
      "http://127.0.0.1:58333/ready/material.pdf?X-Amz-Signature=signed",
      60,
      "http://127.0.0.1:58333",
      { NODE_ENV: "production" }
    )).toBe(false);
  });
});

const VALID_COVER = {
  upload_id: "55000000-0000-4000-8000-000000000003",
  upload_url: `${STORAGE_ORIGIN}/appsell-quarantine/cover?X-Amz-Signature=signed`,
  expires_at: "2026-07-11T20:05:00.000Z",
  required_headers: {
    "content-type": "image/png",
    "x-amz-checksum-sha256": "checksum",
    "x-amz-meta-appsell-upload-id": "55000000-0000-4000-8000-000000000003"
  },
  required_content_length: 4096,
  product_version: 3
};

describe("cover upload boundary", () => {
  it("accepts only the configured cross-origin signed destination", () => {
    expect(browserCoverUploadIntent(VALID_COVER, APP_ORIGIN, STORAGE_ORIGIN, NOW)).toMatchObject({
      upload_origin: STORAGE_ORIGIN,
      upload_url: VALID_COVER.upload_url,
      required_content_length: 4096,
      product_version: 3
    });
  });

  it.each([
    { ...VALID_COVER, upload_url: `${APP_ORIGIN}/api/collect?X-Amz-Signature=signed` },
    { ...VALID_COVER, upload_url: "https://evil.example/cover?X-Amz-Signature=signed" },
    { ...VALID_COVER, upload_url: `https://user:pass@assets.autonomia.site/c?X-Amz-Signature=s` },
    { ...VALID_COVER, upload_url: `${STORAGE_ORIGIN}/appsell-quarantine/cover` },
    { ...VALID_COVER, expires_at: "2026-07-11T19:59:59.000Z" },
    { ...VALID_COVER, expires_at: "2026-07-11T20:10:00.000Z" },
    { ...VALID_COVER, required_headers: { ...VALID_COVER.required_headers, Authorization: "secret" } },
    { ...VALID_COVER, upload_id: "not-a-uuid" },
    { ...VALID_COVER, required_content_length: 0 },
    { ...VALID_COVER, required_content_length: 5 * 1024 * 1024 + 1 },
    { ...VALID_COVER, product_version: 0 }
  ])("rejects an unsafe or stale cover intent %#", (intent) => {
    expect(browserCoverUploadIntent(intent, APP_ORIGIN, STORAGE_ORIGIN, NOW)).toBeNull();
  });

  it("refuses to sign the app origin as a storage destination", () => {
    // Se os dois forem iguais, um PUT do navegador chegaria na nossa própria
    // aplicação carregando os cabeçalhos assinados.
    expect(browserCoverUploadIntent(VALID_COVER, STORAGE_ORIGIN, STORAGE_ORIGIN, NOW)).toBeNull();
  });
});

describe("cover image boundary", () => {
  const COVER = {
    url: `${STORAGE_ORIGIN}/appsell-ready/cover?X-Amz-Signature=signed`,
    media_type: "image/png",
    width: 320,
    height: 180
  };

  it("lets through a signed cover on the configured storage origin", () => {
    expect(trustedProductCover(COVER, STORAGE_ORIGIN)).toBe(COVER);
  });

  it.each([
    { ...COVER, url: "https://evil.example/pixel.png?X-Amz-Signature=signed" },
    { ...COVER, url: `${STORAGE_ORIGIN}/appsell-ready/cover` },
    { ...COVER, url: "data:image/png;base64,AAAA" },
    { ...COVER, url: `https://user:pass@assets.autonomia.site/c?X-Amz-Signature=s` },
    { ...COVER, width: 0 },
    { ...COVER, height: -3 },
    { ...COVER, media_type: 7 },
    null,
    "https://assets.autonomia.site/x"
  ])("turns an untrusted or malformed cover into no cover %#", (cover) => {
    // Sem capa a tela mostra a arte gerada. Um endereço de outra origem viraria
    // uma requisição do navegador do vendedor para um host que ninguém escolheu.
    expect(trustedProductCover(cover, STORAGE_ORIGIN)).toBeNull();
  });
});

const PART_BYTES = 16 * 1024 * 1024;
const INTENCAO_FATIADA = {
  upload_id: "55000000-0000-4000-8000-000000000001",
  asset_id: "55000000-0000-4000-8000-000000000002",
  expires_at: "2026-07-11T20:05:00.000Z",
  multipart: { part_bytes: PART_BYTES, part_count: 50 }
};
const PARTE = {
  part_number: 7,
  upload_url: `${STORAGE_ORIGIN}/appsell-quarantine/object?partNumber=7&X-Amz-Signature=signed`,
  expires_at: "2026-07-11T20:05:00.000Z",
  required_headers: { "x-amz-checksum-sha256": "checksum-da-parte" },
  required_content_length: PART_BYTES
};

describe("envio em partes", () => {
  it("aceita a intenção fatiada, que não carrega endereço nenhum", () => {
    expect(browserMultipartUploadIntent(INTENCAO_FATIADA, NOW)).toMatchObject({
      upload_id: INTENCAO_FATIADA.upload_id,
      multipart: { part_bytes: PART_BYTES, part_count: 50 }
    });
  });

  it("recusa uma intenção fatiada que também traz URL", () => {
    // Resposta com os dois formatos é divergência, e aceitá-la em silêncio
    // deixaria uma URL não conferida chegar ao navegador.
    expect(
      browserMultipartUploadIntent({ ...INTENCAO_FATIADA, upload_url: "https://exemplo.test/x" }, NOW)
    ).toBeNull();
  });

  it("recusa um plano com tamanho de parte diferente do combinado", () => {
    expect(
      browserMultipartUploadIntent({ ...INTENCAO_FATIADA, multipart: { part_bytes: 1024, part_count: 50 } }, NOW)
    ).toBeNull();
  });

  it("recusa um plano de uma parte só, que deveria ter sido envio único", () => {
    expect(
      browserMultipartUploadIntent({ ...INTENCAO_FATIADA, multipart: { part_bytes: PART_BYTES, part_count: 1 } }, NOW)
    ).toBeNull();
  });

  it("recusa uma intenção fatiada vencida", () => {
    expect(browserMultipartUploadIntent(INTENCAO_FATIADA, Date.parse("2026-07-11T20:06:00Z"))).toBeNull();
  });

  it("aceita a parte assinada no armazenamento configurado", () => {
    expect(browserUploadPart(PARTE, APP_ORIGIN, STORAGE_ORIGIN, NOW)).toMatchObject({
      part_number: 7,
      upload_origin: STORAGE_ORIGIN,
      required_content_length: PART_BYTES
    });
  });

  it("recusa uma parte apontada para fora do armazenamento", () => {
    expect(
      browserUploadPart({ ...PARTE, upload_url: "https://exfiltra.test/x?X-Amz-Signature=signed" }, APP_ORIGIN, STORAGE_ORIGIN, NOW)
    ).toBeNull();
  });

  it("recusa uma parte sem assinatura", () => {
    expect(
      browserUploadPart({ ...PARTE, upload_url: `${STORAGE_ORIGIN}/appsell-quarantine/object` }, APP_ORIGIN, STORAGE_ORIGIN, NOW)
    ).toBeNull();
  });

  it("recusa cabeçalho a mais numa parte, que viajaria assinado", () => {
    expect(
      browserUploadPart(
        { ...PARTE, required_headers: { ...PARTE.required_headers, "x-amz-meta-qualquer": "coisa" } },
        APP_ORIGIN,
        STORAGE_ORIGIN,
        NOW
      )
    ).toBeNull();
  });

  it("recusa uma parte maior que o tamanho combinado", () => {
    expect(
      browserUploadPart({ ...PARTE, required_content_length: PART_BYTES + 1 }, APP_ORIGIN, STORAGE_ORIGIN, NOW)
    ).toBeNull();
  });
});
