import { Readable } from "node:stream";
import { createHash, randomBytes } from "node:crypto";

import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  assetSeedKey,
  ASSET_MEDIA_TYPES,
  SUPPORTED_ASSET_MEDIA,
  VIDEO_MEDIA_TYPE,
  type AssetMediaType
} from "../lib/asset-media";

/**
 * O aluno tem de conseguir receber todo formato que o catálogo aceita.
 *
 * Este é o teste que faltava nas duas correções anteriores. As duas mudaram uma
 * lista fechada num arquivo e testaram naquele arquivo; nenhuma percorreu o
 * caminho do aluno formato a formato. Foi assim que a rota de download seguiu
 * exclusiva de PDF — com teto de 10 MiB, `content-type` fixo e uma chave de
 * bundle que cortava sempre quatro caracteres — depois de o player já mostrar o
 * link para todos os anexos. O aluno veria o link e receberia 503.
 *
 * A tabela é o próprio catálogo: acrescentar um formato lá dentro passa a
 * exigir que ele atravesse esta rota, sem ninguém lembrar de escrever o caso.
 */

vi.mock("server-only", () => ({}));

const { getSession, authorizeDomainLearningAssetDownload, DomainApiError } = vi.hoisted(() => {
  class DomainApiError extends Error {
    constructor(readonly status: number, readonly code: string) {
      super(code);
    }
  }
  return {
    getSession: vi.fn(),
    authorizeDomainLearningAssetDownload: vi.fn(),
    DomainApiError
  };
});

const { prepareVerifiedPrivateLearningAsset, configuredPrivateLearningAssetRoot } = vi.hoisted(() => ({
  prepareVerifiedPrivateLearningAsset: vi.fn(),
  configuredPrivateLearningAssetRoot: vi.fn(() => "/tmp/assets")
}));

vi.mock("@/lib/internal-api-client.server", () => ({
  authorizeDomainLearningAssetDownload,
  DomainApiError
}));

vi.mock("@/lib/oauth-service.server", () => ({
  oauthService: () => ({ getSession })
}));

vi.mock("@/lib/private-learning-asset.server", () => ({
  prepareVerifiedPrivateLearningAsset,
  configuredPrivateLearningAssetRoot
}));

import { GET } from "../app/api/library/items/[entitlementId]/assets/[assetId]/download/route";
import { learningPlayerResponse } from "../app/api/library/items/_route-shared";
import { isLearningPlayerPayload, lessonRenderer } from "../app/components/learning-player";

const ORIGIN = "http://localhost:3000";
const ENTITLEMENT_ID = "bee105e2-7809-5bf5-874a-d6332dc4a4ef";
const ASSET_ID = "a6f01626-8b83-55e5-becc-fbab785f1493";
const SHA256 = createHash("sha256").update("conteudo").digest("hex");
const BODY = Buffer.from("conteudo");

const context = { params: Promise.resolve({ entitlementId: ENTITLEMENT_ID, assetId: ASSET_ID }) };

function fileNameFor(mediaType: AssetMediaType) {
  return `material-de-apoio.${SUPPORTED_ASSET_MEDIA[mediaType].extension}`;
}

function authorization(mediaType: AssetMediaType, byteSize: number) {
  const fileName = fileNameFor(mediaType);
  return {
    asset: {
      id: ASSET_ID,
      delivery: "legacy_bundle",
      seed_key: fileName.slice(0, fileName.lastIndexOf(".")),
      file_name: fileName,
      title: "Material de apoio da aula",
      media_type: mediaType,
      byte_size: byteSize,
      sha256: SHA256
    }
  };
}

function download() {
  return GET(
    new NextRequest(
      `${ORIGIN}/api/library/items/${ENTITLEMENT_ID}/assets/${ASSET_ID}/download`,
      { headers: { cookie: "appsell_session=session-1" } }
    ),
    context
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  process.env.APPSELL_CSRF_HMAC_KEY_B64 = randomBytes(32).toString("base64");
  getSession.mockResolvedValue({ id: "session-1", subject: "aluno" });
  prepareVerifiedPrivateLearningAsset.mockResolvedValue({
    size: BODY.byteLength,
    handle: { createReadStream: () => Readable.from([BODY]) }
  });
});

const BAIXAVEIS = ASSET_MEDIA_TYPES.filter((tipo) => SUPPORTED_ASSET_MEDIA[tipo].downloadable);

describe("a rota de download entrega todo formato baixável do catálogo", () => {
  it("o catálogo tem mais de um formato baixável, senão a tabela não prova nada", () => {
    expect(BAIXAVEIS.length).toBeGreaterThan(5);
    expect(BAIXAVEIS).not.toContain(VIDEO_MEDIA_TYPE);
  });

  it.each(BAIXAVEIS)("entrega %s com o content-type do próprio formato", async (mediaType) => {
    authorizeDomainLearningAssetDownload.mockResolvedValue(authorization(mediaType, BODY.byteLength));
    const response = await download();
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe(mediaType);
    expect(response.headers.get("content-disposition")).toContain(`filename="${fileNameFor(mediaType)}"`);
  });

  // O helper de integridade é mockado, então o que estes dois provam é a
  // validação de metadados da rota — não a entrega de um arquivo de 100 MiB.
  it.each(BAIXAVEIS)("valida %s no teto do próprio formato, e recusa um byte além", async (mediaType) => {
    const teto = SUPPORTED_ASSET_MEDIA[mediaType].maxBytes;
    authorizeDomainLearningAssetDownload.mockResolvedValue(authorization(mediaType, teto));
    expect((await download()).status).toBe(200);

    authorizeDomainLearningAssetDownload.mockResolvedValue(authorization(mediaType, teto + 1));
    expect((await download()).status).toBe(503);
  });

  it("recusa o vídeo: o aluno assiste, não baixa", async () => {
    authorizeDomainLearningAssetDownload.mockResolvedValue(
      authorization(VIDEO_MEDIA_TYPE, BODY.byteLength)
    );
    const response = await download();
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: "asset_metadata_invalid" });
  });

  it("recusa um formato que o catálogo não conhece", async () => {
    authorizeDomainLearningAssetDownload.mockResolvedValue({
      asset: {
        ...authorization("application/pdf", BODY.byteLength).asset,
        file_name: "macro.docm",
        media_type: "application/vnd.ms-word.document.macroEnabled.12"
      }
    });
    expect((await download()).status).toBe(503);
  });

  it("a chave do bundle espelha o rsplit do Python, inclusive nos nomes estranhos", async () => {
    // `assetSeedKey` compara com o que `learning_routes.py` gravou. Divergir
    // aqui recusa arquivo legítimo com 503, que foi o defeito do `slice(0, -4)`.
    expect(assetSeedKey("guia.final.pdf")).toBe("guia.final");
    expect(assetSeedKey(".pdf")).toBe("");
    expect(assetSeedKey("sem-extensao")).toBeNull();
    for (const mediaType of BAIXAVEIS) {
      const nome = `material-de-apoio.${SUPPORTED_ASSET_MEDIA[mediaType].extension}`;
      expect(assetSeedKey(nome), mediaType).toBe("material-de-apoio");
    }
  });

  it("recusa quando a extensão do nome discorda da mídia declarada", async () => {
    const invalido = authorization("text/markdown", BODY.byteLength);
    invalido.asset.file_name = "material-de-apoio.pdf";
    authorizeDomainLearningAssetDownload.mockResolvedValue(invalido);
    expect((await download()).status).toBe(503);
  });
});

/**
 * O contrato entre as duas fronteiras, exercitado de verdade.
 *
 * O BFF monta o payload e o navegador o valida de novo. Foram duas listas
 * fechadas independentes, e a aula do vendedor tinha um PDF e cinco anexos: o
 * primeiro `.md` reprovava o payload inteiro, e o aluno não via nem o PDF.
 *
 * Aqui a saída real do BFF alimenta o validador real do player. Se um dos dois
 * lados voltar a fechar o conjunto, os dois deixam de concordar e isto reprova.
 */
describe("o BFF e o player concordam sobre o catálogo inteiro", () => {
  const LESSON_ID = "3f1c0e4a-1c1e-4c2a-8a34-9f0d5b2c7a11";
  const MODULE_ID = "5b2f4d61-2b7f-4d6b-9c45-7a1e3c9d4b22";
  const COURSE_ID = "7c3a5e72-3c8a-4e7c-ad56-8b2f4dae5c33";

  function dominioPara(
    assets: readonly { mediaType: AssetMediaType; suporte: boolean }[],
    { legadoVazio = false }: { legadoVazio?: boolean } = {}
  ) {
    const selecionados = assets.map((asset, indice) => ({
      id: `3b42248${indice}-ffa7-4d2f-ade9-0d4402c2625a`,
      title: "Material",
      media_type: asset.mediaType,
      content_role: asset.suporte ? "support_material" : "course_content",
      download_allowed: asset.suporte && SUPPORTED_ASSET_MEDIA[asset.mediaType].downloadable
    }));
    return {
      player: {
        // O contrato real. `read_learning_player_v3` devolve
        // `schema_version: 3`, `selected_assets` com política, e um
        // `selected_asset` herdado da v2 — sem política, só id/título/mídia.
        // Sem o `schema_version`, o BFF entrava no ramo de compatibilidade e o
        // teste provava um caminho que a produção não percorre.
        schema_version: 3,
        // `legadoVazio` é a forma que a produção devolve para material de
        // vendedor: `read_learning_player_v3` monta `selected_assets` de
        // `publication_asset`, e herda da v2 um `selected_asset` lido de
        // `learning_asset` — tabela legada, sem linha nenhuma fora da demo.
        selected_asset: selecionados[0] && !legadoVazio
          ? {
              id: selecionados[0].id,
              title: selecionados[0].title,
              media_type: selecionados[0].media_type
            }
          : null,
        seller: { id: "27310fea-1a96-42f0-807c-2b4f9669d1e4" },
        product: {
          id: "7d4602dc-229f-456b-82e8-ad5fb37a14aa",
          slug: "gerador-de-cotacoes",
          title: "Gerador de Cotações Comparativas",
          description: "Curso completo"
        },
        publication: { id: "72a4c583-0f46-4675-8d6c-ca4a9babe2f0", version: 1, source_sha256: SHA256 },
        entitlement_id: ENTITLEMENT_ID,
        enrollment_id: "6c817fd1-2441-4241-9610-eef1a778dbe2",
        course: {
          id: COURSE_ID,
          title: "Curso",
          description: "",
          position: 1,
          modules: [{
            id: MODULE_ID,
            title: "Módulo 1",
            status: "active",
            position: 1,
            lessons: [{
              id: LESSON_ID,
              title: "Aula 1",
              kind: "text",
              content: "Conteúdo",
              status: "active",
              position: 1
            }]
          }]
        },
        selected_lesson_id: LESSON_ID,
        resume_lesson_id: LESSON_ID,
        course_progress_percent: 0,
        state_version: 0,
        lesson_progress: [],
        selected_assets: selecionados
      }
    };
  }

  async function playerDoBff(
    assets: readonly { mediaType: AssetMediaType; suporte: boolean }[],
    opcoes: { legadoVazio?: boolean } = {}
  ) {
    const response = learningPlayerResponse(
      dominioPara(assets, opcoes),
      "session-1",
      { subject: "aluno" }
    );
    expect(response.status, "o BFF recusou o payload antes de o navegador ver").toBe(200);
    const body = await response.json();
    return { ...body.player, csrf: body.csrf };
  }

  it("a fixture percorre o contrato v3, não o ramo de compatibilidade", async () => {
    // Sem esta âncora, tirar `schema_version` da fixture faria os testes abaixo
    // continuarem verdes exercitando um caminho que a produção não percorre.
    const player = await playerDoBff([{ mediaType: "application/pdf", suporte: false }]);
    expect(player.schema_version).toBe(3);
    expect(Object.hasOwn(player.selected_asset, "content_role")).toBe(true);
  });

  it.each(ASSET_MEDIA_TYPES)("atravessa %s do BFF até o validador do player", async (mediaType) => {
    const suporte = SUPPORTED_ASSET_MEDIA[mediaType].supportMaterialOnly;
    const player = await playerDoBff([{ mediaType, suporte }]);
    expect(isLearningPlayerPayload(player)).toBe(true);
  });

  it("a aula do vendedor abre com o `selected_asset` legado vazio — o caso real", async () => {
    // O que o banco de produção devolve de fato: 6 materiais em
    // `publication_asset` e nenhuma linha em `learning_asset`. O BFF exigia que
    // os dois campos coincidissem e recusava a aula inteira com 503, apesar de
    // descartar o campo legado logo em seguida.
    const player = await playerDoBff([
      { mediaType: "application/pdf", suporte: false },
      { mediaType: "text/markdown", suporte: true },
      { mediaType: "text/markdown", suporte: true },
      { mediaType: "text/markdown", suporte: true },
      { mediaType: "text/plain", suporte: true },
      { mediaType: "text/markdown", suporte: true }
    ], { legadoVazio: true });
    expect(player.selected_assets).toHaveLength(6);
    // O BFF reescreve o campo legado a partir da lista, então o navegador
    // recebe os dois coerentes mesmo quando o domínio mandou `null`.
    expect(player.selected_asset.media_type).toBe("application/pdf");
    expect(isLearningPlayerPayload(player)).toBe(true);
  });

  it("um material só, com o legado vazio, também abre", async () => {
    const player = await playerDoBff(
      [{ mediaType: "text/markdown", suporte: true }],
      { legadoVazio: true }
    );
    expect(isLearningPlayerPayload(player)).toBe(true);
  });

  it("uma aula com PDF e cinco anexos abre — o caso que não abria", async () => {
    const anexos = ASSET_MEDIA_TYPES
      .filter((tipo) => SUPPORTED_ASSET_MEDIA[tipo].supportMaterialOnly)
      .slice(0, 5)
      .map((mediaType) => ({ mediaType, suporte: true }));
    expect(anexos).toHaveLength(5);
    const player = await playerDoBff([{ mediaType: "application/pdf", suporte: false }, ...anexos]);
    expect(isLearningPlayerPayload(player)).toBe(true);
    expect(player.selected_assets).toHaveLength(6);
  });

  it("todo anexo baixável chega com o link de download, e o vídeo não", async () => {
    const player = await playerDoBff([
      { mediaType: VIDEO_MEDIA_TYPE, suporte: false },
      ...BAIXAVEIS.map((mediaType) => ({
        mediaType,
        suporte: SUPPORTED_ASSET_MEDIA[mediaType].supportMaterialOnly
      }))
    ]);
    const porTipo = new Map(player.selected_assets.map((a: { media_type: string; download_href: string | null }) =>
      [a.media_type, a.download_href]));
    expect(porTipo.get(VIDEO_MEDIA_TYPE)).toBeNull();
    for (const mediaType of BAIXAVEIS) {
      if (!SUPPORTED_ASSET_MEDIA[mediaType].supportMaterialOnly) continue;
      expect(porTipo.get(mediaType), `${mediaType} ficou sem link de download`)
        .toMatch(/\/download$/);
    }
  });
});

describe("o player escolhe um componente que sabe apresentar o formato", () => {
  it.each(ASSET_MEDIA_TYPES)("%s cai num componente compatível", (mediaType) => {
    const renderer = lessonRenderer(mediaType);
    // O leitor paginado exige um PDF; o player de vídeo exige um MP4. Todo o
    // resto tem de cair no cartão de download — sem esta terceira resposta, um
    // `.md` ia para o leitor de PDF e virava "Não foi possível preparar este PDF".
    if (mediaType === "application/pdf") expect(renderer).toBe("reader");
    else if (mediaType === VIDEO_MEDIA_TYPE) expect(renderer).toBe("video");
    else expect(renderer).toBe("download");
  });
});
