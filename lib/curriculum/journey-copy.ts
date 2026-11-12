import {
  assetMediaLimit,
  assetMediaTypeForFile,
  mediaFitsContentRole,
  mediaLabel,
  VIDEO_MEDIA_TYPE
} from "@/lib/asset-media";

import type { UploadPhase } from "./lesson-readiness";

/**
 * O que a jornada de montar o curso diz (#649), num lugar só.
 *
 * Quem lê é quem vende, não quem programou. Nada de "arquivar",
 * "verificação", "rascunho", "multipart" ou "intenção": o teste
 * `curriculum-journey.test.ts` lê todos estes textos e recusa os termos da
 * lista abaixo. Todo erro diz o que fazer.
 */

export const FORBIDDEN_SELLER_WORDS: readonly string[] = [
  "arquivar",
  "arquivad",
  "verificaç",
  "verificand",
  "rascunho",
  "multipart",
  "intenção",
  "upload",
  "a api "
];

export const JOURNEY_COPY = {
  courseEyebrow: "Conteúdo do produto",
  courseTitle: "Seu curso",
  emptyTitle: "Comece pelo primeiro módulo",
  emptyHelp: "Um módulo junta aulas do mesmo assunto. Exemplo: “Boas-vindas”.",
  moduleName: "Nome do módulo",
  modulePlaceholder: "Ex.: Boas-vindas",
  createModule: "Criar módulo",
  newModule: "+ Novo módulo",
  newLesson: "+ Nova aula",
  lessonName: "Nome da nova aula",
  lessonPlaceholder: "Ex.: Como funciona o método",
  createLesson: "Criar e abrir a aula",
  cancel: "Cancelar",
  allReady: "Todas as aulas estão prontas. Agora é definir o preço.",
  goToPrice: "Ir para Preço",
  continue: "Continuar",
  back: "Voltar para o curso",
  backReady: "Aula pronta · voltar para o curso",
  deleteLesson: "Excluir aula",
  deleteLessonBlocked: "Espere o envio terminar, ou pare o envio, para excluir a aula.",
  mainTitle: "Vídeo ou PDF da aula",
  mainHint: "O aluno assiste ou lê aqui dentro, sem baixar.",
  pickMain: "Escolher vídeo ou PDF",
  pickAnotherMain: "+ Adicionar outro vídeo ou PDF",
  mainLimits: "Vídeo MP4 até 512 MB · PDF até 100 MB. Vídeo maior? Use um link do YouTube em Materiais para o aluno.",
  textTitle: "Texto da aula",
  textHelp: "Use **negrito**, *itálico* e - no começo da linha para listas.",
  textPlaceholder: "Escreva um resumo, instruções ou o roteiro da aula.",
  materialsTitle: "Materiais para o aluno",
  materialsHelp: "Apostilas, planilhas e links que o aluno pode baixar ou abrir.",
  addFile: "+ Adicionar arquivo",
  addLink: "+ Adicionar link",
  linkLabel: "Cole o link",
  linkHelp: "Pode ser um site, um arquivo no Drive ou um vídeo do YouTube ou Vimeo.",
  linkYoutube: "Vídeo do YouTube reconhecido. O aluno assiste dentro da aula.",
  linkVimeo: "Vídeo do Vimeo reconhecido. O aluno assiste dentro da aula.",
  linkName: "Nome que o aluno vai ver",
  saveLink: "Adicionar link",
  linkInvalid: "Este link não abre. Copie o endereço da barra do navegador, começando com https://, e cole aqui.",
  linkNameInvalid: "Escreva um nome com pelo menos 2 letras, para o aluno saber o que é.",
  optional: "opcional",
  saving: "Salvando…",
  saved: "✓ Salvo",
  notSaved: "Tentar de novo",
  saveFailed: "Não deu para salvar. Seu texto continua aqui.",
  replaceFile: "Trocar arquivo",
  removeFile: "Tirar da aula",
  downloadToggle: "O aluno pode baixar",
  stopUpload: "Parar envio",
  tryAgain: "Escolher o arquivo de novo",
  undo: "Desfazer",
  undoFailed: "Não deu para concluir a exclusão. O item voltou para a lista. Tente de novo.",
  offline: "Sem conexão com o servidor. Confira a internet e tente de novo.",
  reload: "Recarregar o curso",
  changedElsewhere: "O curso mudou em outra aba ou aparelho. Recarregue para ver a versão mais nova.",
  sessionExpired: "Sua sessão terminou. Entre de novo para continuar.",
  noPermission: "Você não tem permissão para mudar o conteúdo desta loja.",
  loading: "Carregando o curso…",
  loadFailed: "Não deu para carregar o curso. Confira a internet e tente de novo.",
  notFound: "Este curso não foi encontrado nesta loja.",
  archivedProduct: "Este produto foi encerrado. Dá para ver o curso, mas não para mudar.",
  limitModules: "Este curso chegou ao máximo de módulos. Exclua um para criar outro.",
  limitLessons: "Este curso chegou ao máximo de aulas. Exclua uma para criar outra.",
  nameTooShort: "Escreva um nome com pelo menos 2 letras."
} as const;

export interface StageCopy {
  title: string;
  detail: string;
}

/** Três momentos: enviando, quase lá, pronto. O resto é detalhe da máquina. */
export function uploadStage(phase: UploadPhase, progress: number): StageCopy {
  if (phase === "verifying") {
    return {
      title: "Quase lá: conferindo o arquivo",
      detail: "Leva menos de um minuto. Pode voltar para o curso: a lista mostra quando ficar pronto."
    };
  }
  if (phase === "delayed") {
    return {
      title: "Quase lá: conferindo o arquivo",
      detail: "Está levando mais que o normal. Pode continuar montando o curso: a lista mostra quando ficar pronto."
    };
  }
  const value = Math.max(0, Math.min(100, Math.round(progress)));
  return {
    title: `Enviando ${value}%`,
    detail: "Deixe esta página aberta até terminar. Você pode mexer no resto da aula."
  };
}

export type ContentRole = "course_content" | "support_material";

export type FileRefusal =
  | { refused: false }
  | { refused: true; title: string; fix: string };

const YOUTUBE_WAY_OUT =
  "envie para o YouTube como “não listado” e cole o link em Materiais para o aluno";

/** Por que este arquivo não entra aqui, e o que fazer. Nada é recusado sem saída. */
export function fileRefusal(
  file: { name: string; size: number; type: string },
  role: ContentRole
): FileRefusal {
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".mov") || file.type === "video/quicktime") {
    return {
      refused: true,
      title: "Vídeos .MOV do iPhone ainda não entram.",
      fix: `Exporte o vídeo como MP4 num editor de vídeo, como o CapCut, ou ${YOUTUBE_WAY_OUT}.`
    };
  }
  const mediaType = assetMediaTypeForFile(file.name, file.type);
  if (!mediaType) {
    return {
      refused: true,
      title: "Este tipo de arquivo não entra.",
      fix: role === "course_content"
        ? "Aqui entram vídeo MP4 e PDF. Documentos, planilhas e apresentações vão em Materiais para o aluno."
        : "Aqui entram PDF, Word, Excel, PowerPoint, texto e CSV. Vídeo vai em Vídeo ou PDF da aula."
    };
  }
  if (!mediaFitsContentRole(mediaType, role)) {
    return role === "course_content"
      ? {
          refused: true,
          title: "Este arquivo vai em Materiais para o aluno.",
          fix: "Aqui entram só vídeo MP4 e PDF. Documentos, planilhas e apresentações vão em Materiais para o aluno, onde o aluno pode baixar."
        }
      : {
          refused: true,
          title: "Vídeo vai no começo da aula.",
          fix: "Envie em Vídeo ou PDF da aula: o aluno assiste ali, sem baixar."
        };
  }
  if (file.size < 1) {
    return { refused: true, title: "Este arquivo está vazio.", fix: "Escolha o arquivo de novo." };
  }
  const limit = assetMediaLimit(mediaType);
  if (file.size > limit) {
    if (mediaType === VIDEO_MEDIA_TYPE) {
      return {
        refused: true,
        title: `Este vídeo tem ${humanSize(file.size)}. O limite é ${humanSize(limit)}.`,
        fix: `Grave em 720p, divida a aula em duas partes, ou ${YOUTUBE_WAY_OUT}.`
      };
    }
    return {
      refused: true,
      title: `Este arquivo tem ${humanSize(file.size)}. O limite para ${mediaLabel(mediaType)} é ${humanSize(limit)}.`,
      fix: "Diminua o arquivo ou divida o conteúdo em mais de um arquivo."
    };
  }
  return { refused: false };
}

const MB = 1024 * 1024;
const GB = 1024 * MB;

/** Tamanho como a pessoa fala: "84 MB", "1,4 GB". */
export function humanSize(bytes: number): string {
  if (bytes >= GB) return `${formatDecimal(bytes / GB)} GB`;
  if (bytes >= MB) return `${formatDecimal(bytes / MB)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function formatDecimal(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return rounded.toLocaleString("pt-BR", { maximumFractionDigits: rounded >= 100 ? 0 : 1 });
}
