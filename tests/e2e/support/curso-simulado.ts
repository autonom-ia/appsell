import type { Page, Request, Route } from "@playwright/test";

import { E2E_BUILD_PUBLIC_ORIGIN } from "../../../lib/e2e-build-runtime";
import { E2E_STORAGE_ORIGIN } from "./storage";

/**
 * Um servidor de currículo com memória, para a jornada de montar o curso (#649).
 *
 * Os specs antigos respondiam cada rota com uma fotografia fixa. A jornada nova
 * cria, renomeia, reordena e exclui, e a tela relê o curso depois de cada
 * mudança: a resposta precisa refletir o que a pessoa acabou de fazer, como o
 * servidor de verdade. Cada pedido que muda alguma coisa fica registrado em
 * `pedidos`, para o teste provar o que saiu (e o que não saiu) da tela.
 *
 * Como o servidor de verdade, ele recusa com 409 o pedido que traz uma versão
 * do curso diferente da atual (`conflitos` conta quantos), e responde a uma
 * chave de repetição já vista com a resposta guardada, sem aplicar de novo.
 */

export const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
export const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
export const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";

export interface AulaSimulada {
  id: string;
  title: string;
  kind: "text";
  content: string;
  status: "draft";
  position: number;
}

export interface ModuloSimulado {
  id: string;
  title: string;
  status: "draft";
  position: number;
  lessons: AulaSimulada[];
}

export interface ArquivoSimulado {
  id: string;
  title: string;
  file_name: string;
  byte_size: number;
  media_type: string;
  status: "ready";
  content_role: "course_content" | "support_material";
  download_allowed: boolean;
}

export interface LinkSimulado {
  material_id: string;
  kind: "external" | "embed";
  title: string;
  url: string | null;
  provider: "youtube" | "vimeo" | null;
  video_id: string | null;
  position: number;
}

export interface PedidoRegistrado {
  method: string;
  path: string;
  fields: Record<string, string>;
}

export interface EnvioSimulado {
  /** Segura o PUT no storage até o teste liberar, para provar "Parar envio". */
  segurarStorage?: boolean;
}

let sequencia = 0;
export function novoId(prefixo: string): string {
  sequencia += 1;
  const sufixo = sequencia.toString(16).padStart(12, "0");
  return `${prefixo.padEnd(8, "0").slice(0, 8)}-0000-4000-8000-${sufixo}`;
}

export function aula(title: string, content = ""): AulaSimulada {
  return { id: novoId("a0a0a0a0"), title, kind: "text", content, status: "draft", position: 0 };
}

export function modulo(title: string, lessons: AulaSimulada[] = []): ModuloSimulado {
  return { id: novoId("b0b0b0b0"), title, status: "draft", position: 0, lessons };
}

export function arquivoPronto(partial: Partial<ArquivoSimulado> & Pick<ArquivoSimulado, "file_name" | "media_type">): ArquivoSimulado {
  return {
    id: novoId("c0c0c0c0"),
    title: partial.file_name,
    byte_size: 2_400_000,
    status: "ready",
    content_role: "course_content",
    download_allowed: false,
    ...partial
  };
}

const ASSET_CSRF = {
  intent: "csrf-intent", confirm: "csrf-confirm", cancel: "csrf-cancel",
  archive: "csrf-archive", policy: "csrf-policy", signPart: "csrf-sign"
};

export class CursoSimulado {
  versao = 2;
  modulos: ModuloSimulado[];
  arquivos = new Map<string, ArquivoSimulado[]>();
  links = new Map<string, LinkSimulado[]>();
  pedidos: PedidoRegistrado[] = [];
  conflitos = 0;
  envio: EnvioSimulado = {};
  private respostas = new Map<string, { status: number; json: unknown }>();
  private chaveAtual: string | null = null;
  private liberarStorage: ((concluir: boolean) => void) | null = null;

  constructor(modulos: ModuloSimulado[] = []) {
    this.modulos = modulos;
  }

  pedidosPara(method: string, final: string): PedidoRegistrado[] {
    return this.pedidos.filter((pedido) => pedido.method === method && pedido.path.endsWith(final));
  }

  /** Solta o PUT segurado: por padrão ele cai (a pessoa parou); com
   *  `concluir`, ele chega ao storage e o envio segue até o fim. */
  liberarEnvio({ concluir = false }: { concluir?: boolean } = {}) {
    this.liberarStorage?.(concluir);
    this.liberarStorage = null;
  }

  curriculo() {
    return {
      course: { id: COURSE_ID, title: "Curso", draft_version: this.versao },
      modules: this.modulos.map((entry, index) => ({
        ...entry,
        position: index + 1,
        lessons: entry.lessons.map((item, position) => ({ ...item, position: position + 1 }))
      })),
      limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200, title_bytes: 160, lesson_content_bytes: 8192 },
      usage: {
        modules_total: this.modulos.length,
        lessons_total: this.modulos.reduce((total, entry) => total + entry.lessons.length, 0),
        lessons_by_module: Object.fromEntries(this.modulos.map((entry) => [entry.id, entry.lessons.length]))
      },
      csrf: { create: "c", update: "u", reorder: "r", archive: "a" }
    };
  }

  /** Liga o servidor simulado às rotas da página. */
  async ligar(page: Page) {
    await page.route(`${E2E_STORAGE_ORIGIN}/**`, async (route) => {
      const cors = { "access-control-allow-origin": E2E_BUILD_PUBLIC_ORIGIN };
      if (route.request().method() === "OPTIONS") {
        return route.fulfill({ status: 204, headers: {
          ...cors,
          "access-control-allow-methods": "PUT,OPTIONS",
          "access-control-allow-headers": "content-type,x-amz-checksum-sha256,x-amz-meta-appsell-upload-id"
        } });
      }
      if (this.envio.segurarStorage) {
        const concluir = await new Promise<boolean>((resolve) => { this.liberarStorage = resolve; });
        if (!concluir) return route.abort("aborted").catch(() => undefined);
      }
      await route.fulfill({ status: 200, headers: { ...cors, etag: '"etag"' }, body: "" });
    });
    await page.route("**/api/sellers/**", (route) => this.atender(route));
  }

  private async atender(route: Route) {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const produto = `/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`;
    if (path === produto && method === "GET") return route.fulfill({ json: produtoSimulado() });
    if (path.endsWith("/access-links")) return route.fulfill({ json: { access_links: [] } });
    const prefixo = `${produto}/courses/${COURSE_ID}/`;
    if (!path.startsWith(prefixo)) return route.fallback();
    const partes = path.slice(prefixo.length).split("/");
    const fields = method === "GET" ? {} : camposDoFormulario(request);
    if (method !== "GET") this.pedidos.push({ method, path, fields });
    const chave = fields.idempotency_key ?? null;
    const guardada = chave ? this.respostas.get(chave) : undefined;
    if (guardada) return route.fulfill(guardada);
    if (fields.expected_course_version !== undefined && Number(fields.expected_course_version) !== this.versao) {
      this.conflitos += 1;
      return route.fulfill({ status: 409, json: { error: "course_version_conflict" } });
    }
    this.chaveAtual = chave;
    return this.rotear(route, method, partes, fields);
  }

  private async rotear(route: Route, method: string, partes: string[], fields: Record<string, string>) {
    const [raiz, moduleId, sub, lessonId, recurso, recursoId, acao] = partes;
    if (raiz === "curriculum" && method === "GET") return route.fulfill({ json: this.curriculo() });
    if (raiz !== "modules") return route.fallback();
    if (moduleId === undefined && method === "POST") {
      const novo = modulo(fields.title ?? "");
      this.modulos = [...this.modulos, novo];
      return this.mudou(route, { module_id: novo.id, created: true });
    }
    if (moduleId === "order" && method === "PUT") {
      const ordem = JSON.parse(fields.ordered_ids ?? "[]") as string[];
      this.modulos = ordem.map((id) => this.modulos.find((entry) => entry.id === id)!).filter(Boolean);
      return this.mudou(route, { reordered: true });
    }
    const alvo = this.modulos.find((entry) => entry.id === moduleId);
    if (!alvo) return route.fulfill({ status: 404, json: { error: "not_found" } });
    if (sub === undefined && method === "PATCH") {
      this.trocarModulo(alvo.id, { ...alvo, title: fields.title ?? alvo.title });
      return this.mudou(route, {});
    }
    if (sub === "archive" && method === "POST") {
      this.modulos = this.modulos.filter((entry) => entry.id !== alvo.id);
      return this.mudou(route, {});
    }
    if (sub !== "lessons") return route.fallback();
    if (lessonId === undefined && method === "POST") {
      const nova = aula(fields.title ?? "", fields.content ?? "");
      this.trocarModulo(alvo.id, { ...alvo, lessons: [...alvo.lessons, nova] });
      return this.mudou(route, { lesson_id: nova.id, created: true });
    }
    if (lessonId === "order" && method === "PUT") {
      const ordem = JSON.parse(fields.ordered_ids ?? "[]") as string[];
      const lessons = ordem.map((id) => alvo.lessons.find((entry) => entry.id === id)!).filter(Boolean);
      this.trocarModulo(alvo.id, { ...alvo, lessons });
      return this.mudou(route, { reordered: true });
    }
    const licao = alvo.lessons.find((entry) => entry.id === lessonId);
    if (!licao) return route.fulfill({ status: 404, json: { error: "not_found" } });
    if (recurso === undefined && method === "PATCH") {
      const lessons = alvo.lessons.map((entry) => entry.id === licao.id
        ? { ...entry, title: fields.title ?? entry.title, content: fields.content ?? entry.content }
        : entry);
      this.trocarModulo(alvo.id, { ...alvo, lessons });
      return this.mudou(route, {});
    }
    if (recurso === "archive" && method === "POST") {
      this.trocarModulo(alvo.id, { ...alvo, lessons: alvo.lessons.filter((entry) => entry.id !== licao.id) });
      return this.mudou(route, {});
    }
    if (recurso === "assets") return this.rotearArquivos(route, method, licao.id, recursoId, acao, partes[7], fields);
    if (recurso === "materials") return this.rotearLinks(route, method, licao.id, recursoId, acao, fields);
    return route.fallback();
  }

  private async rotearArquivos(
    route: Route, method: string, lessonId: string,
    recursoId: string | undefined, acao: string | undefined, extra: string | undefined,
    fields: Record<string, string>
  ) {
    const lista = this.arquivos.get(lessonId) ?? [];
    if (recursoId === undefined && method === "GET") {
      return route.fulfill({ json: { assets: lista, uploads: [], csrf: ASSET_CSRF } });
    }
    if (recursoId === "upload-intents" && method === "POST") {
      const uploadId = novoId("d0d0d0d0");
      const assetId = novoId("c0c0c0c0");
      this.enviosEmCurso.set(uploadId, {
        lessonId,
        arquivo: {
          id: assetId,
          title: fields.file_name ?? "",
          file_name: fields.file_name ?? "",
          byte_size: Number(fields.byte_size ?? 0),
          media_type: fields.media_type ?? "",
          status: "ready",
          // Como o banco: vídeo e PDF nascem conteúdo da aula; o resto, material.
          content_role: fields.media_type === "video/mp4" || fields.media_type === "application/pdf"
            ? "course_content"
            : "support_material",
          download_allowed: false
        }
      });
      return route.fulfill({ json: {
        upload_id: uploadId,
        asset_id: assetId,
        // `validIntent` só aceita endereço assinado, como o do storage real.
        upload_url: `${E2E_STORAGE_ORIGIN}/appsell-quarantine/${uploadId}?X-Amz-Signature=e2e`,
        upload_origin: E2E_STORAGE_ORIGIN,
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        required_headers: {
          "Content-Type": fields.media_type ?? "application/octet-stream",
          "x-amz-checksum-sha256": "checksum",
          "x-amz-meta-appsell-upload-id": uploadId
        }
      } });
    }
    if (recursoId === "uploads" && acao) {
      const envio = this.enviosEmCurso.get(acao);
      if (!envio) return route.fulfill({ status: 404, json: { error: "not_found" } });
      if (extra === "confirm") return route.fulfill({ status: 202, json: { upload_id: acao, status: "verifying" } });
      if (extra === "cancel") {
        this.enviosEmCurso.delete(acao);
        return route.fulfill({ json: { upload_id: acao, status: "cancelled", course_version: this.versao } });
      }
      if (extra === undefined && method === "GET") {
        const atual = this.arquivos.get(envio.lessonId) ?? [];
        if (!atual.some((entry) => entry.id === envio.arquivo.id)) {
          this.arquivos.set(envio.lessonId, [...atual, envio.arquivo]);
          this.versao += 1;
        }
        return route.fulfill({ json: { upload_id: acao, status: "ready", asset: envio.arquivo } });
      }
    }
    if (recursoId && acao === "archive" && method === "POST") {
      this.arquivos.set(lessonId, lista.filter((entry) => entry.id !== recursoId));
      return this.mudou(route, {});
    }
    if (recursoId && acao === "policy" && method === "PATCH") {
      const role = fields.content_role === "support_material" ? "support_material" : "course_content";
      const atualizado = lista.map((entry) => entry.id === recursoId
        ? { ...entry, content_role: role, download_allowed: fields.download_allowed === "true" } as ArquivoSimulado
        : entry);
      this.arquivos.set(lessonId, atualizado);
      this.versao += 1;
      return route.fulfill({ json: { asset: atualizado.find((entry) => entry.id === recursoId), course_version: this.versao } });
    }
    return route.fallback();
  }

  private enviosEmCurso = new Map<string, { lessonId: string; arquivo: ArquivoSimulado }>();

  private async rotearLinks(
    route: Route, method: string, lessonId: string,
    materialId: string | undefined, acao: string | undefined, fields: Record<string, string>
  ) {
    const lista = this.links.get(lessonId) ?? [];
    if (materialId === undefined && method === "GET") {
      return route.fulfill({ json: { materials: lista, csrf: { create: "m", archive: "n" } } });
    }
    if (materialId === undefined && method === "POST") {
      const novo: LinkSimulado = {
        material_id: novoId("e0e0e0e0"),
        kind: fields.kind === "embed" ? "embed" : "external",
        title: fields.title ?? "",
        url: fields.url || null,
        provider: fields.provider === "youtube" || fields.provider === "vimeo" ? fields.provider : null,
        video_id: fields.video_id || null,
        position: lista.length + 1
      };
      this.links.set(lessonId, [...lista, novo]);
      return this.mudou(route, { material_id: novo.material_id, created: true });
    }
    if (materialId && acao === "archive" && method === "POST") {
      this.links.set(lessonId, lista.filter((entry) => entry.material_id !== materialId));
      return this.mudou(route, {});
    }
    return route.fallback();
  }

  private trocarModulo(id: string, novo: ModuloSimulado) {
    this.modulos = this.modulos.map((entry) => entry.id === id ? novo : entry);
  }

  private mudou(route: Route, payload: Record<string, unknown>) {
    this.versao += 1;
    const resposta = { status: 200, json: { ...payload, course_version: this.versao } };
    if (this.chaveAtual) this.respostas.set(this.chaveAtual, resposta);
    return route.fulfill(resposta);
  }
}

function produtoSimulado() {
  return {
    id: PRODUCT_ID, seller_organization_id: SELLER_ID, seller_slug: "autonomia",
    slug: "ia-na-pratica", title: "IA na prática", description: "Conteúdo",
    status: "draft", draft_version: 3,
    course: { id: COURSE_ID, title: "Curso", description: "", status: "draft", draft_version: 2, position: 1 },
    courses: [],
    offer: { id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 0, currency: "BRL", billing_period: "one_time" }
  };
}

/**
 * Lê os campos de um `multipart/form-data` com operações de string: o corpo é
 * gerado pelo navegador, num formato fixo, e só os campos de texto importam.
 */
function camposDoFormulario(request: Request): Record<string, string> {
  const tipo = request.headers()["content-type"] ?? "";
  const marcador = "boundary=";
  const inicio = tipo.indexOf(marcador);
  const corpo = request.postData() ?? "";
  if (inicio < 0) return {};
  const fronteira = `--${tipo.slice(inicio + marcador.length)}`;
  const campos: Record<string, string> = {};
  for (const bloco of corpo.split(fronteira)) {
    const separador = bloco.indexOf("\r\n\r\n");
    if (separador < 0) continue;
    const cabecalho = bloco.slice(0, separador);
    const nomeInicio = cabecalho.indexOf('name="');
    if (nomeInicio < 0) continue;
    const nomeFim = cabecalho.indexOf('"', nomeInicio + 6);
    const nome = cabecalho.slice(nomeInicio + 6, nomeFim);
    const valor = bloco.slice(separador + 4);
    campos[nome] = valor.endsWith("\r\n") ? valor.slice(0, -2) : valor;
  }
  return campos;
}

export async function abrirCurso(page: Page, curso: CursoSimulado, aulaId?: string) {
  await curso.ligar(page);
  const query = aulaId ? `?section=curriculo&aula=${aulaId}` : "?section=curriculo";
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}${query}`);
}
