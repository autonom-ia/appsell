/**
 * The accepted asset media, mirrored from the server side catalogue.
 *
 * Documents are support material and are always downloaded, never opened inside
 * the page, so nothing in the player has to decide how to display a spreadsheet.
 * The macro bearing office formats are absent on purpose: an extension is a
 * claim, and the only place that claim is tested is the server, which opens the
 * container. This table exists so the browser refuses early and clearly, not so
 * it can be trusted.
 */
export interface AssetMediaSpec {
  extension: string;
  maxBytes: number;
  supportMaterialOnly: boolean;
  downloadable: boolean;
  /** Como o formato é nomeado ao vendedor e ao aluno. */
  label: string;
  /** O gênero do rótulo, para o demonstrativo concordar: "este PDF", "esta planilha". */
  feminine: boolean;
}

const MIB = 1024 * 1024;

export const PDF_MEDIA_TYPE = "application/pdf";
export const VIDEO_MEDIA_TYPE = "video/mp4";
export const DOCX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
export const XLSX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
export const PPTX_MEDIA_TYPE =
  "application/vnd.openxmlformats-officedocument.presentationml.presentation";

/**
 * O catálogo. Declarado sem anotação de tipo de propósito.
 *
 * Anotar como `Record<string, AssetMediaSpec>` fazia `keyof typeof` colapsar em
 * `string`, e `AssetMediaType` virava um apelido de `string` — um tipo que não
 * recusa nada. `satisfies` mantém a checagem do formato de cada entrada e
 * preserva as chaves literais.
 */
const CATALOGUE = {
  [PDF_MEDIA_TYPE]: { extension: "pdf", maxBytes: 100 * MIB, supportMaterialOnly: false, downloadable: true, label: "PDF", feminine: false },
  [VIDEO_MEDIA_TYPE]: { extension: "mp4", maxBytes: 100 * MIB, supportMaterialOnly: false, downloadable: false, label: "vídeo", feminine: false },
  [DOCX_MEDIA_TYPE]: { extension: "docx", maxBytes: 20 * MIB, supportMaterialOnly: true, downloadable: true, label: "documento", feminine: false },
  [XLSX_MEDIA_TYPE]: { extension: "xlsx", maxBytes: 20 * MIB, supportMaterialOnly: true, downloadable: true, label: "planilha", feminine: true },
  [PPTX_MEDIA_TYPE]: { extension: "pptx", maxBytes: 40 * MIB, supportMaterialOnly: true, downloadable: true, label: "apresentação", feminine: true },
  "text/plain": { extension: "txt", maxBytes: 2 * MIB, supportMaterialOnly: true, downloadable: true, label: "documento", feminine: false },
  "text/markdown": { extension: "md", maxBytes: 2 * MIB, supportMaterialOnly: true, downloadable: true, label: "documento", feminine: false },
  "text/csv": { extension: "csv", maxBytes: 5 * MIB, supportMaterialOnly: true, downloadable: true, label: "planilha", feminine: true },
  "application/json": { extension: "json", maxBytes: 5 * MIB, supportMaterialOnly: true, downloadable: true, label: "arquivo", feminine: false }
} satisfies Record<string, AssetMediaSpec>;

export type AssetMediaType = keyof typeof CATALOGUE & string;

export const SUPPORTED_ASSET_MEDIA: Readonly<Record<AssetMediaType, AssetMediaSpec>> = CATALOGUE;

/** Toda a mídia aceita, na ordem do catálogo. */
export const ASSET_MEDIA_TYPES = Object.keys(CATALOGUE) as readonly AssetMediaType[];

export const MAX_PDF_BYTES = SUPPORTED_ASSET_MEDIA[PDF_MEDIA_TYPE].maxBytes;
export const MAX_VIDEO_BYTES = SUPPORTED_ASSET_MEDIA[VIDEO_MEDIA_TYPE].maxBytes;
export const MAX_ASSET_BYTES = Math.max(
  ...Object.values(SUPPORTED_ASSET_MEDIA).map((spec) => spec.maxBytes)
);

export const ASSET_UPLOAD_ACCEPT = Object.entries(SUPPORTED_ASSET_MEDIA)
  .flatMap(([mediaType, spec]) => [mediaType, `.${spec.extension}`])
  .join(",");

/**
 * Os dois conjuntos que os painéis oferecem, derivados do catálogo.
 *
 * `Conteúdo da aula` só aceita o que o player sabe apresentar — a mesma regra
 * que `app_private.player_renders_media` declara no banco. `Anexos e links`
 * aceita tudo, porque o PDF é o único formato genuinamente ambíguo: ebook
 * protegido no leitor ou anexo para o aluno baixar. Aí a escolha é legítima, e
 * quem a expressa é o painel em que o vendedor clicou — não um `<select>`.
 */
export const PLAYER_RENDERED_MEDIA: readonly AssetMediaType[] = [PDF_MEDIA_TYPE, VIDEO_MEDIA_TYPE];

function acceptList(mediaTypes: readonly AssetMediaType[]): string {
  return mediaTypes
    .flatMap((mediaType) => [mediaType, `.${SUPPORTED_ASSET_MEDIA[mediaType].extension}`])
    .join(",");
}

/**
 * Anexo é o que o aluno baixa, então o MP4 fica fora.
 *
 * Não é preferência de tela: o vídeo tem `downloadable: false` no catálogo, e
 * `set_lesson_asset_policy` recusa download em vídeo. Oferecê-lo aqui deixaria o
 * vendedor substituir um documento baixável por um vídeo e cair numa política
 * que o servidor não aceita.
 */
export const ATTACHMENT_MEDIA: readonly AssetMediaType[] = ASSET_MEDIA_TYPES
  .filter((mediaType) => SUPPORTED_ASSET_MEDIA[mediaType].downloadable);

export function mediaForContentRole(
  contentRole: "course_content" | "support_material"
): readonly AssetMediaType[] {
  return contentRole === "course_content" ? PLAYER_RENDERED_MEDIA : ATTACHMENT_MEDIA;
}

export function acceptForContentRole(contentRole: "course_content" | "support_material"): string {
  return acceptList(mediaForContentRole(contentRole));
}

/**
 * O `accept` do input é uma dica, não uma regra: quem escolhe "todos os
 * arquivos" no seletor do sistema passa por cima dele. Esta é a regra.
 */
export function mediaFitsContentRole(
  mediaType: unknown,
  contentRole: "course_content" | "support_material"
): boolean {
  return (mediaForContentRole(contentRole) as readonly string[]).includes(String(mediaType));
}

/**
 * O formato cabe nos dois painéis, e por isso a escolha é do vendedor.
 *
 * Só o PDF: o player o desenha (ebook protegido) e o aluno também pode baixá-lo
 * (anexo). O MP4 o player desenha mas não é baixável; os documentos são
 * baixáveis mas o player não os desenha. Nos dois casos não há escolha a fazer.
 */
export function acceptedByBothPanels(mediaType: unknown): boolean {
  const spec = assetMediaSpec(mediaType);
  if (!spec) return false;
  return spec.downloadable && (PLAYER_RENDERED_MEDIA as readonly string[]).includes(String(mediaType));
}

export function assetMediaSpec(mediaType: unknown): AssetMediaSpec | null {
  if (typeof mediaType !== "string") return null;
  return (CATALOGUE as Record<string, AssetMediaSpec>)[mediaType] ?? null;
}

/** A pergunta que os validadores de fronteira fazem, com estreitamento de tipo. */
export function isAssetMediaType(value: unknown): value is AssetMediaType {
  return assetMediaSpec(value) !== null;
}

/**
 * A chave do bundle legado, espelhando `learning_routes.py`, que faz
 * `file_name.rsplit(".", 1)[0]`.
 *
 * O BFF fazia `slice(0, -4)`, que só acerta extensão de três letras — o espelho
 * quebrava em `.md`, `.docx`, `.xlsx`, `.pptx` e `.json`, e o download do anexo
 * era recusado como metadado inválido.
 */
export function assetSeedKey(fileName: unknown): string | null {
  if (typeof fileName !== "string") return null;
  const dot = fileName.lastIndexOf(".");
  return dot <= 0 ? null : fileName.slice(0, dot);
}

export function assetMediaLimit(mediaType: unknown) {
  return assetMediaSpec(mediaType)?.maxBytes ?? 0;
}

/** O nome do formato para quem lê a tela: "PDF", "vídeo", "planilha". */
export function mediaLabel(mediaType: unknown): string {
  return assetMediaSpec(mediaType)?.label ?? "arquivo";
}

/** "este PDF", "esta planilha" — o demonstrativo que concorda com o rótulo. */
export function mediaDemonstrative(mediaType: unknown): string {
  return assetMediaSpec(mediaType)?.feminine ? "esta" : "este";
}

export function isDocumentMedia(mediaType: unknown) {
  return assetMediaSpec(mediaType)?.supportMaterialOnly === true;
}

export function isDownloadableMedia(mediaType: unknown) {
  return assetMediaSpec(mediaType)?.downloadable === true;
}

/** Resolve the media type a picked file claims to be, refusing any disagreement. */
export function assetMediaTypeForFile(fileName: string, browserType: string): AssetMediaType | null {
  const lower = fileName.toLowerCase();
  for (const mediaType of ASSET_MEDIA_TYPES) {
    const spec = SUPPORTED_ASSET_MEDIA[mediaType];
    if (!lower.endsWith(`.${spec.extension}`)) continue;
    // An empty browser type is common for the office and text formats, so it is
    // accepted; a browser type that names a different format is not.
    if (browserType && browserType !== mediaType) return null;
    return mediaType;
  }
  return null;
}

export function validAssetMediaFile(
  fileName: unknown,
  mediaType: unknown,
  byteSize: unknown
): mediaType is AssetMediaType {
  if (
    typeof fileName !== "string"
    || typeof byteSize !== "number"
    || !Number.isSafeInteger(byteSize)
    || byteSize < 1
  ) return false;
  const spec = assetMediaSpec(mediaType);
  if (!spec) return false;
  return fileName.toLowerCase().endsWith(`.${spec.extension}`) && byteSize <= spec.maxBytes;
}
