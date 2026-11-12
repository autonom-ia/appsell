import { expect as expectBase, test, type Page } from "@playwright/test";
import { login } from "./support/login";
import { E2E_STORAGE_ORIGIN } from "./support/storage";

// Ver o comentário no topo: 20 s cobrem o carregamento sob carga da suíte
// inteira. As asserções continuam medindo comportamento; só a paciência muda.
const expect = expectBase.configure({ timeout: 20_000 });

/**
 * O painel de conteúdo da aula, do ponto de vista de quem cadastra um produto.
 *
 * Escrito depois de o Rodrigo tentar subir cinco materiais numa aula e bater em
 * quatro defeitos seguidos, todos no mesmo componente:
 *
 * 1. **A tela desmontava a cada ação.** `handleAssetCommitted` chamava `load()`,
 *    que começa com `setLoadState("loading")` — e o `return` antecipado troca a
 *    página inteira por "Carregando conteúdo…". Todo painel aberto fechava, e
 *    como a página encolhia para uma linha, o scroll voltava ao topo. O painel
 *    dispara isso em oito lugares: enviar, cancelar, falhar, arquivar,
 *    substituir, mudar o papel.
 * 2. **Todo material recebia o nome da aula.** `body.set("title", lessonTitle)`
 *    era fixo no código, então cinco arquivos viravam cinco cards idênticos e
 *    não havia como saber qual era qual. O nome real estava no banco o tempo
 *    todo, em `file_name`, sem nunca ser mostrado.
 * 3. **O rótulo dizia "PDF" para tudo**, inclusive num `.md` de 10 KiB.
 * 4. **O papel do material era uma pergunta**, num `<select>`, quando o painel
 *    em que o vendedor clica já é a resposta.
 *
 * Desde o #649 a aula abre em tela própria (`?aula=`), com os dois lugares à
 * vista: "Vídeo ou PDF da aula" e "Materiais para o aluno". Nada mais fica
 * recolhido, e tirar um arquivo da aula tem "Desfazer" em vez de diálogo.
 */

const SELLER_ID = "4df98a2e-d1dd-5f25-bbea-58b8072a0e7b";
const PRODUCT_ID = "82efefc5-a3c9-5126-a7d1-4918d5428d63";
const COURSE_ID = "9a4a73d6-bf11-5429-8582-89434000af44";
const MODULE_ID = "3f2b1a90-77c4-5d18-9e63-0a1b2c3d4e5f";
const LESSON_ID = "6a5b4c3d-2e1f-5a09-8b77-1c2d3e4f5a6b";

const PDF = {
  id: "aa000000-0000-4000-8000-000000000001",
  title: "Gerador de Cotações Comparativas",
  file_name: "Playbook_HUB2YOU_02_Gerador_Cotacoes_Comparativas.pdf",
  byte_size: 12_789_714,
  media_type: "application/pdf",
  status: "ready",
  content_role: "course_content",
  download_allowed: false
};

const MARKDOWN = {
  id: "aa000000-0000-4000-8000-000000000002",
  title: "Gerador de Cotações Comparativas",
  file_name: "SKILL-01-COTACAO-COMPARATIVA-COMERCIAL.md",
  byte_size: 10_557,
  media_type: "text/markdown",
  status: "ready",
  content_role: "support_material",
  download_allowed: true
};

const TEXTO = {
  id: "aa000000-0000-4000-8000-000000000003",
  title: "Gerador de Cotações Comparativas",
  file_name: "perfil_corretora.txt",
  byte_size: 930,
  media_type: "text/plain",
  status: "ready",
  content_role: "support_material",
  download_allowed: true
};

const CSRF = {
  intent: "csrf-intent", confirm: "csrf-confirm", cancel: "csrf-cancel",
  archive: "csrf-archive", policy: "csrf-policy"
};

/** Aulas suficientes para a página ser mais alta que a janela — sem isso o
 *  scroll não tem para onde voltar e o defeito não aparece. */
function curriculum() {
  return {
    course: { id: COURSE_ID, title: "Especialista", draft_version: 2 },
    modules: [{
      id: MODULE_ID,
      title: "Módulo 1",
      position: 1,
      lessons: Array.from({ length: 8 }, (_, index) => ({
        id: index === 0 ? LESSON_ID : `6a5b4c3d-2e1f-5a09-8b77-1c2d3e4f5a${(index + 10).toString(16)}`,
        title: index === 0 ? "Gerador de Cotações Comparativas" : `Aula ${index + 1}`,
        content: "Conteúdo da aula, longo o bastante para a página crescer e o scroll existir.",
        position: index + 1
      }))
    }],
    limits: { modules_per_course: 20, lessons_per_module: 30, lessons_per_course: 200 },
    usage: { modules_total: 1, lessons_total: 8, lessons_by_module: { [MODULE_ID]: 8 } },
    csrf: { create: "c", update: "u", reorder: "r", archive: "a" }
  };
}

type MaterialView = typeof PDF;

async function openCurriculum(page: Page, assets: MaterialView[] = [PDF, MARKDOWN, TEXTO], uploads: unknown[] = []) {
  await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: {
      id: PRODUCT_ID, seller_organization_id: SELLER_ID, seller_slug: "autonomia",
      slug: "ia-na-pratica", title: "IA na prática", description: "Conteúdo",
      status: "draft", draft_version: 3,
      course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
      courses: [],
      offer: { id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 0, currency: "BRL", billing_period: "one_time" }
    } });
  });
  await page.route("**/api/sellers/**/curriculum", async (route) => {
    await route.fulfill({ json: curriculum() });
  });
  await page.route("**/lessons/*/assets", async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: { assets, uploads, csrf: CSRF } });
  });
  await page.route("**/materials", async (route) => {
    await route.fulfill({ json: { materials: [], csrf: { create: "m", archive: "n" } } });
  });
  await page.route("**/access-links", async (route) => {
    await route.fulfill({ json: { access_links: [] } });
  });
  await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=curriculo&aula=${LESSON_ID}`);
  await expect(page.getByTestId("curriculum-workspace")).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: /Gerador de Cotações Comparativas/ })).toBeVisible();
}

/** "Vídeo ou PDF da aula": o que o aluno assiste ou lê sem baixar. */
function conteudoDaAula(page: Page) {
  return page.getByRole("region", { name: "Vídeo ou PDF da aula" });
}

/** "Materiais para o aluno": arquivos para baixar, links e vídeos. */
function materiais(page: Page) {
  return page.getByRole("region", { name: "Materiais para o aluno" });
}

const CARD = ".lesson-file";

test.describe("o painel de conteúdo da aula não se desmonta a cada clique", () => {
  test("tirar um material da aula mantém a aula aberta e a lista no lugar", async ({ page }) => {
    await login(page);
    await page.clock.install();
    await openCurriculum(page);

    const anexos = materiais(page);
    await expect(anexos.locator(CARD)).toHaveCount(2);
    await anexos.getByRole("button", { name: `Tirar da aula: ${MARKDOWN.file_name}` }).click();
    await expect(anexos.locator(CARD)).toHaveCount(1);
    await page.clock.runFor(8_500);
    await expect.poll(() => page.evaluate(() => document.querySelectorAll(".undo-toast").length)).toBe(0);

    // O defeito: `load()` punha a tela em "Carregando…", o que desmontava todos
    // os painéis e levava o scroll ao topo.
    await expect(page.getByText("Carregando o curso…")).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 2, name: /Gerador de Cotações Comparativas/ })).toBeVisible();
    await expect(anexos.locator(CARD)).toHaveCount(1);
    await expect(anexos).toContainText(TEXTO.file_name);
  });
});

test.describe("cada material se identifica", () => {
  test("o card mostra o nome do arquivo, não o nome da aula cinco vezes", async ({ page }) => {
    await login(page);
    await openCurriculum(page);

    const anexos = materiais(page);

    // O defeito: `body.set("title", lessonTitle)` era fixo, e os cinco materiais
    // da aula viravam cinco cards "Gerador de Cotações Comparativas".
    await expect(anexos).toContainText("SKILL-01-COTACAO-COMPARATIVA-COMERCIAL.md");
    await expect(anexos).toContainText("perfil_corretora.txt");
  });

  test("o card nomeia o formato, e não PDF sempre", async ({ page }) => {
    await login(page);
    await openCurriculum(page);

    const markdown = materiais(page).locator(CARD).filter({ hasText: "SKILL-01" });
    await expect(markdown).toContainText("Documento");
    await expect(markdown).not.toContainText("PDF");
    await expect(markdown.getByRole("switch", { name: `O aluno pode baixar: ${MARKDOWN.file_name}` })).toBeChecked();
  });
});

test.describe("o painel em que se clica é a resposta", () => {
  test("o seletor \"Uso deste material\" não existe mais", async ({ page }) => {
    await login(page);
    await openCurriculum(page);

    const aula = page.getByTestId("curriculum-workspace");
    // Perguntar o papel era pedir ao vendedor uma resposta que o painel já dá.
    await expect(aula.getByText("Uso deste material")).toHaveCount(0);
    await expect(aula.getByRole("combobox")).toHaveCount(0);
    await expect(aula.locator("select")).toHaveCount(0);
  });

  test("cada painel mostra só o que lhe pertence", async ({ page }) => {
    await login(page);
    await openCurriculum(page);

    const conteudo = conteudoDaAula(page);
    const anexos = materiais(page);

    await expect(conteudo.locator(CARD)).toHaveCount(1);
    await expect(conteudo).toContainText("Playbook_HUB2YOU_02_Gerador_Cotacoes_Comparativas.pdf");
    await expect(conteudo).not.toContainText("SKILL-01");

    await expect(anexos.locator(CARD)).toHaveCount(2);
    await expect(anexos).not.toContainText("Playbook_HUB2YOU_02");
  });

  test("Conteúdo da aula não oferece formato que o player não sabe abrir", async ({ page }) => {
    await login(page);
    await openCurriculum(page);

    const conteudo = conteudoDaAula(page);
    const anexos = materiais(page);

    // O `accept` deriva do catálogo: o player desenha PDF e MP4, o resto o
    // aluno baixa. O PDF aparece nos dois de propósito — é o único formato
    // genuinamente ambíguo.
    const aceitaConteudo = await conteudo.locator('input[type="file"]').getAttribute("accept");
    expect(aceitaConteudo).toContain(".pdf");
    expect(aceitaConteudo).toContain(".mp4");
    expect(aceitaConteudo).not.toContain(".docx");
    expect(aceitaConteudo).not.toContain(".md");

    const aceitaAnexos = await anexos.locator('input[type="file"]').getAttribute("accept");
    for (const extensao of [".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv", ".json"]) {
      expect(aceitaAnexos).toContain(extensao);
    }
  });

  test("links e vídeos ficam em Materiais para o aluno", async ({ page }) => {
    await login(page);
    await openCurriculum(page);

    // Eram dois painéis quase homônimos — "Materiais da aula" e "Material
    // complementar" — e foi por isso que o vendedor procurou o arquivo no lugar
    // errado. Agora arquivo, link e vídeo dividem o mesmo lugar.
    const anexos = materiais(page);
    await expect(anexos.getByRole("button", { name: "+ Adicionar link" })).toBeVisible();
    await expect(anexos.getByRole("button", { name: "+ Adicionar arquivo" }).or(anexos.getByLabel("+ Adicionar arquivo"))).toHaveCount(1);
  });
});

test.describe("o material verificado aparece onde o vendedor está olhando", () => {
  test("entra no fim da lista, e não no topo fora da tela", async ({ page }) => {
    await login(page);

    const NOVO = {
      id: "aa000000-0000-4000-8000-00000000000f",
      title: "Gerador de Cotações Comparativas",
      file_name: "INSTRUCOES_PROJETO.md",
      byte_size: 8_192,
      media_type: "text/markdown",
      status: "ready",
      content_role: "support_material",
      download_allowed: true
    };
    const uploadId = "bb000000-0000-4000-8000-000000000001";
    // A lista do servidor, que o teste muta quando a verificação termina — como
    // o servidor real faz. O painel vai buscá-la em vez de montá-la sozinho.
    const lista: MaterialView[] = [PDF, MARKDOWN, TEXTO];

    await page.clock.install();
    await openCurriculum(page, lista);
    await page.route("**/assets/upload-intents", async (route) => {
      await route.fulfill({ json: {
        upload_id: uploadId,
        asset_id: NOVO.id,
        upload_url: `${E2E_STORAGE_ORIGIN}/appsell-quarantine/obj?X-Amz-Signature=assinado`,
        upload_origin: E2E_STORAGE_ORIGIN,
        // `validIntent` recusa validade acima de 330 s: uma assinatura de vida
        // longa demais e uma assinatura vazada valem quase a mesma coisa.
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        required_headers: {
          "Content-Type": "text/markdown",
          "x-amz-checksum-sha256": "checksum",
          "x-amz-meta-appsell-upload-id": uploadId
        }
      } });
    });
    await page.route(`${E2E_STORAGE_ORIGIN}/**`, async (route) => {
      await route.fulfill({ status: 200, headers: { etag: '"etag"' }, body: "" });
    });
    await page.route(`**/assets/uploads/${uploadId}/confirm`, async (route) => {
      await route.fulfill({ status: 202, json: { upload_id: uploadId, status: "verifying" } });
    });
    await page.route(`**/assets/uploads/${uploadId}`, async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      // O servidor passa a conhecer o material. É dele que o painel vai buscar
      // a lista — ordem e papel vêm de quem grava, não de um palpite do cliente.
      if (!lista.some((asset) => asset.id === NOVO.id)) lista.push(NOVO);
      await route.fulfill({ json: { upload_id: uploadId, status: "ready", asset: NOVO } });
    });

    const anexos = materiais(page);
    await expect(anexos.locator(CARD)).toHaveCount(2);

    // `window.scrollY` não serve de prova: o shell tem o próprio container
    // rolável. O que prova o comportamento é a rolagem ter sido pedida, e para
    // qual elemento — combinada com o card acabar visível.
    await page.evaluate(() => {
      const janela = window as unknown as { __rolou: string[] };
      janela.__rolou = [];
      const original = Element.prototype.scrollIntoView;
      Element.prototype.scrollIntoView = function (this: Element, ...args: unknown[]) {
        const resultado = (original as (...a: unknown[]) => void).apply(this, args);
        // Registrar DEPOIS de rolar. Registrando antes, o teste via a marca e
        // media a posição enquanto a rolagem ainda não tinha acontecido — o que
        // só aparecia com a suíte inteira sob carga.
        janela.__rolou.push((this as HTMLElement).className || this.tagName);
        return resultado;
      } as typeof Element.prototype.scrollIntoView;
    });

    await anexos.locator('input[type="file"]').setInputFiles({
      name: NOVO.file_name,
      mimeType: "text/markdown",
      buffer: Buffer.from("# instruções\n")
    });

    await expect(anexos.locator(CARD)).toHaveCount(3);
    // Sem esta asserção o teste passaria mesmo sem existir rolagem nenhuma,
    // bastando o card cair por acaso dentro da janela.
    await expect.poll(() => page.evaluate(
      () => (window as unknown as { __rolou: string[] }).__rolou.join(" | ")
    )).toContain("state-just-ready");
    // O defeito: o card entrava no topo. O vendedor estava no fim da lista,
    // perto do botão de adicionar, e via a mensagem de pronto sem card nenhum
    // por perto — e concluía, com razão, que não tinha entrado. Recarregar
    // fazia o card pular do topo para o fim, porque o servidor ordena por
    // `position`.
    const nomes = await anexos.locator(`${CARD} .lesson-file-name`).allInnerTexts();
    expect(nomes[nomes.length - 1]).toBe(NOVO.file_name);

    // E o vendedor é levado até ele, com destaque que some sozinho.
    const cardNovo = anexos.locator(CARD).filter({ hasText: NOVO.file_name });
    await expect(cardNovo).toHaveClass(/state-just-ready/);
    // `toBeInViewport` mede uma vez a cada tentativa do locator e, com a suíte
    // inteira sob carga, chegava a medir entre o quadro em que a rolagem foi
    // pedida e o quadro em que ela assentou. Medido de dentro, o poll refaz a
    // conta até o layout parar — o card fica com `top` bem abaixo da altura da
    // janela nos dois navegadores.
    await expect.poll(() => cardNovo.evaluate((card) => {
      const caixa = card.getBoundingClientRect();
      return caixa.top < window.innerHeight && caixa.bottom > 0;
    })).toBe(true);
    // O destaque some sozinho: marcar o card para sempre transformaria "este
    // acabou de chegar" em ruído permanente.
    await page.clock.runFor(3_000);
    await expect(cardNovo).not.toHaveClass(/state-just-ready/);
  });
});

test.describe("o PDF em Anexos só entra na lista depois da política", () => {
  /**
   * O caminho de maior risco desta correção, e o único onde a corrida existe.
   *
   * O PDF é o único formato que os dois painéis aceitam. O banco o classifica
   * como conteúdo da aula, então enviado em "Anexos e links" ele nasce filtrado
   * para **fora** deste painel, e só entra quando o PATCH de política volta. O
   * efeito que rola e destaca rodava antes disso, não encontrava o card, e não
   * rodava de novo — o material aparecia sem destaque e sem levar o vendedor
   * até ele.
   */
  const PDF_NOVO = {
    id: "aa000000-0000-4000-8000-0000000000ff",
    title: "Gerador de Cotações Comparativas",
    file_name: "anexo-para-baixar.pdf",
    byte_size: 4_096,
    media_type: "application/pdf",
    status: "ready",
    content_role: "course_content",
    download_allowed: false
  };
  const uploadId = "bb000000-0000-4000-8000-0000000000ff";

  async function pdfEntraComPoliticaDemorada(page: Page, quadroAtrasadoMs: number) {
    await login(page);
    await page.clock.install();
    const lista: MaterialView[] = [PDF, MARKDOWN, TEXTO];
    await openCurriculum(page, lista);
    if (quadroAtrasadoMs > 0) {
      // Aparelho lento: o navegador entrega o quadro de render depois do tempo
      // em que o destaque some. O foco tem de chegar mesmo assim (#605).
      await page.evaluate((atraso) => {
        window.requestAnimationFrame = (callback) =>
          window.setTimeout(() => callback(performance.now()), atraso);
        window.cancelAnimationFrame = (id) => window.clearTimeout(id);
      }, quadroAtrasadoMs);
    }

    let liberarPolitica: (() => void) | null = null;
    let registrada: Promise<unknown> = Promise.resolve();
    const politicaPedida = new Promise<void>((resolve) => {
      registrada = page.route("**/assets/*/policy", async (route) => {
        resolve();
        // A resposta só chega quando o teste mandar: é a demora que criava a
        // corrida, e sem controlá-la o teste passaria por sorte.
        await new Promise<void>((liberar) => { liberarPolitica = liberar; });
        // Só agora o servidor o conhece como anexo: antes da política ele é
        // conteúdo da aula, e a lista deste painel não o traz.
        lista.push({ ...PDF_NOVO, content_role: "support_material", download_allowed: true });
        await route.fulfill({ json: {
          asset_id: PDF_NOVO.id, content_role: "support_material",
          download_allowed: true, course_version: 5, applied: true, replayed: false
        } });
      });
    });
    await registrada;

    await page.route("**/assets/upload-intents", async (route) => {
      await route.fulfill({ json: {
        upload_id: uploadId,
        asset_id: PDF_NOVO.id,
        upload_url: `${E2E_STORAGE_ORIGIN}/appsell-quarantine/obj?X-Amz-Signature=assinado`,
        upload_origin: E2E_STORAGE_ORIGIN,
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        required_headers: {
          "Content-Type": "application/pdf",
          "x-amz-checksum-sha256": "checksum",
          "x-amz-meta-appsell-upload-id": uploadId
        }
      } });
    });
    await page.route(`${E2E_STORAGE_ORIGIN}/**`, async (route) => {
      await route.fulfill({ status: 200, headers: { etag: '"etag"' }, body: "" });
    });
    await page.route(`**/assets/uploads/${uploadId}/confirm`, async (route) => {
      await route.fulfill({ status: 202, json: { upload_id: uploadId, status: "verifying" } });
    });
    await page.route(`**/assets/uploads/${uploadId}`, async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({ json: { upload_id: uploadId, status: "ready", asset: PDF_NOVO } });
    });

    const anexos = materiais(page);
    await expect(anexos.locator(CARD)).toHaveCount(2);

    await anexos.locator('input[type="file"]').setInputFiles({
      name: PDF_NOVO.file_name,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF")
    });

    await politicaPedida;
    // Enquanto a política não volta, o PDF é conteúdo da aula: ele não pertence
    // a este painel, e mostrá-lo aqui seria mentir sobre o que já vale.
    await expect(anexos.locator(CARD)).toHaveCount(2);

    liberarPolitica!();

    await expect(anexos.locator(CARD)).toHaveCount(3);
    const cardNovo = anexos.locator(CARD).filter({ hasText: PDF_NOVO.file_name });
    await expect(cardNovo).toHaveClass(/state-just-ready/);
    // O foco é a prova de que o efeito rodou **depois** do PATCH: antes dele o
    // card nem existia neste painel. A lista aqui é curta e o card já nasce
    // visível, então não rolar é o comportamento certo.
    await expect(cardNovo).toBeFocused();
  }

  test("aparece com destaque e foco quando a política demora", async ({ page }) => {
    await pdfEntraComPoliticaDemorada(page, 0);
  });

  test("o foco chega ao card mesmo com o quadro de render atrasado", async ({ page }) => {
    await pdfEntraComPoliticaDemorada(page, 3_000);
  });

  test("a política que falha avisa o vendedor e não põe o PDF nos anexos", async ({ page }) => {
    await login(page);
    await page.clock.install();
    // A política falha, então o servidor nunca passa a listá-lo como anexo.
    await openCurriculum(page, [PDF, MARKDOWN, TEXTO]);

    await page.route("**/assets/*/policy", async (route) => {
      await route.fulfill({ status: 503, json: { error: "policy_unavailable" } });
    });
    await page.route("**/assets/upload-intents", async (route) => {
      await route.fulfill({ json: {
        upload_id: uploadId, asset_id: PDF_NOVO.id,
        upload_url: `${E2E_STORAGE_ORIGIN}/appsell-quarantine/obj?X-Amz-Signature=assinado`,
        upload_origin: E2E_STORAGE_ORIGIN,
        expires_at: new Date(Date.now() + 300_000).toISOString(),
        required_headers: {
          "Content-Type": "application/pdf",
          "x-amz-checksum-sha256": "checksum",
          "x-amz-meta-appsell-upload-id": uploadId
        }
      } });
    });
    await page.route(`${E2E_STORAGE_ORIGIN}/**`, async (route) => {
      await route.fulfill({ status: 200, headers: { etag: '"etag"' }, body: "" });
    });
    await page.route(`**/assets/uploads/${uploadId}/confirm`, async (route) => {
      await route.fulfill({ status: 202, json: { upload_id: uploadId, status: "verifying" } });
    });
    await page.route(`**/assets/uploads/${uploadId}`, async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({ json: { upload_id: uploadId, status: "ready", asset: PDF_NOVO } });
    });

    const anexos = materiais(page);
    await anexos.locator('input[type="file"]').setInputFiles({
      name: PDF_NOVO.file_name,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF")
    });

    // O vendedor precisa saber. O pior desfecho aqui seria o silêncio: o
    // arquivo subiu, foi verificado, e não está em painel nenhum.
    await expect(anexos.getByRole("alert")).toContainText("Não deu para salvar esta mudança.");
    // E não entra nos anexos: enquanto a política não valer, ele continua sendo
    // conteúdo da aula, e mostrá-lo aqui seria mentir sobre o que está gravado.
    // Este teste não vai além disso — que ele reapareça no painel de conteúdo
    // depois de recarregar é o que a mensagem promete, e provar isso exigiria
    // recarregar a página aqui.
    await expect(anexos.locator(CARD)).toHaveCount(2);
    // E diz o que fazer, em vez de deixar o vendedor adivinhar.
    await expect(anexos.getByRole("alert")).toContainText("tente de novo");
  });
});

test.describe("mover um PDF entre os painéis", () => {
  /**
   * Cada aula renderiza duas instâncias do painel, cada uma com a própria
   * lista. Mover o PDF tirava o card da origem e o destino — já aberto — não
   * ficava sabendo: o material sumia de vista até recarregar a página. Que é
   * exatamente a experiência que esta entrega existe para acabar.
   */
  test("o card sai de um painel e aparece no outro, sem recarregar", async ({ page }) => {
    await login(page);
    const lista: MaterialView[] = [PDF, MARKDOWN, TEXTO];
    await openCurriculum(page, lista);

    await page.route("**/assets/*/policy", async (route) => {
      // O servidor grava e passa a listar o PDF como anexo.
      const indice = lista.findIndex((asset) => asset.id === PDF.id);
      lista[indice] = { ...PDF, content_role: "support_material", download_allowed: true };
      await route.fulfill({ json: {
        asset_id: PDF.id, content_role: "support_material",
        download_allowed: true, course_version: 5, applied: true, replayed: false
      } });
    });

    // Os dois lugares sempre à vista: é a situação em que o defeito aparecia.
    const conteudo = conteudoDaAula(page);
    const anexos = materiais(page);
    await expect(conteudo.locator(CARD)).toHaveCount(1);
    await expect(anexos.locator(CARD)).toHaveCount(2);

    await conteudo.getByRole("button", { name: "Mover para Materiais para o aluno" }).click();

    await expect(conteudo.locator(CARD)).toHaveCount(0);
    await expect(anexos.locator(CARD)).toHaveCount(3);
    await expect(anexos).toContainText(PDF.file_name);
  });
});

test.describe("o aviso chega enquanto o painel de destino ainda carrega", () => {
  /**
   * A corrida mais difícil desta entrega, e a que quase escapou.
   *
   * O painel de destino está aberto com o carregamento inicial ainda em voo
   * quando o irmão anuncia a mudança. A revalidação vence — e é ela que entrega
   * a primeira lista. Se ela não **fechar** o carregamento, a lista aparece com
   * "Carregando materiais…" pendurado embaixo, para sempre.
   */
  test("a lista aparece e o carregamento não fica pendurado", async ({ page }) => {
    await login(page);

    const lista: MaterialView[] = [PDF, MARKDOWN, TEXTO];
    // A lista do curso pede o resumo de cada aula assim que abre. Só depois de
    // abrir a aula os dois painéis pedem a própria lista, na ordem da tela:
    // primeiro "Vídeo ou PDF da aula", depois "Materiais para o aluno".
    let aulaAberta = false;
    let chamadas = 0;
    let liberarSegunda: (() => void) | null = null;
    const segundaPedida = new Promise<void>((resolve) => {
      void page.route("**/lessons/*/assets", async (route) => {
        if (route.request().method() !== "GET") return route.fallback();
        if (aulaAberta && route.request().url().includes(`/lessons/${LESSON_ID}/`)) chamadas += 1;
        // A segunda é a do painel de materiais: fica presa até o teste soltar,
        // reproduzindo o carregamento inicial ainda em voo.
        if (aulaAberta && chamadas === 2) {
          resolve();
          await new Promise<void>((liberar) => { liberarSegunda = liberar; });
        }
        await route.fulfill({ json: { assets: lista, uploads: [], csrf: CSRF } });
      });
    });

    await page.route(`**/api/sellers/${SELLER_ID}/products/${PRODUCT_ID}`, async (route) => {
      if (route.request().method() !== "GET") return route.fallback();
      await route.fulfill({ json: {
        id: PRODUCT_ID, seller_organization_id: SELLER_ID, seller_slug: "autonomia",
        slug: "ia-na-pratica", title: "IA na prática", description: "Conteúdo",
        status: "draft", draft_version: 3,
        course: { id: COURSE_ID, title: "Especialista", description: "", status: "draft", draft_version: 2, position: 1 },
        courses: [],
        offer: { id: "b1d0c9f6-5a1e-52a4-9c3f-2f0f1d3a7e55", title: "Padrão", summary: "", status: "draft", draft_version: 1, unit_amount_minor: 0, currency: "BRL", billing_period: "one_time" }
      } });
    });
    await page.route("**/api/sellers/**/curriculum", async (route) => {
      await route.fulfill({ json: curriculum() });
    });
    await page.route("**/materials", async (route) => {
      await route.fulfill({ json: { materials: [], csrf: { create: "m", archive: "n" } } });
    });
    await page.route("**/access-links", async (route) => {
      await route.fulfill({ json: { access_links: [] } });
    });
    await page.route("**/assets/*/policy", async (route) => {
      const indice = lista.findIndex((asset) => asset.id === PDF.id);
      lista[indice] = { ...PDF, content_role: "support_material", download_allowed: true };
      await route.fulfill({ json: {
        asset_id: PDF.id, content_role: "support_material",
        download_allowed: true, course_version: 5, applied: true, replayed: false
      } });
    });

    await page.goto(`/products/${SELLER_ID}/${PRODUCT_ID}?section=curriculo`);
    await expect(page.getByTestId("curriculum-workspace")).toBeVisible();
    // Os resumos das oito aulas chegam antes de a aula abrir.
    await expect(page.getByTestId(`lesson-${LESSON_ID}`)).toContainText("✓ Pronta");

    aulaAberta = true;
    await page.getByTestId(`lesson-${LESSON_ID}`).getByRole("link").click();
    const conteudo = conteudoDaAula(page);
    const anexos = materiais(page);
    await expect(conteudo.locator(CARD)).toHaveCount(1);
    await segundaPedida;

    // O irmão anuncia enquanto o destino ainda espera a própria lista.
    await conteudo.getByRole("button", { name: "Mover para Materiais para o aluno" }).click();
    liberarSegunda!();

    await expect(anexos.locator(CARD)).toHaveCount(3);
    await expect(anexos).not.toContainText("Carregando os arquivos…");
  });
});

test.describe("um envio que não terminou não trava o painel", () => {
  // Um `pending` na carga da página é um envio que este painel não está
  // fazendo. Antes ele virava "verificação em andamento" e desligava o seletor
  // por até duas horas, sem nada sendo verificado (#645).
  test("envio dentro do prazo: avisa os dois casos e deixa escolher de novo", async ({ page }) => {
    await login(page);
    await openCurriculum(page, [], [{
      id: "bb000000-0000-4000-8000-000000000001",
      status: "pending",
      file_name: "aula-1.mp4",
      byte_size: 209_420_000,
      safe_reason: null
    }]);

    const conteudo = conteudoDaAula(page);
    await expect(conteudo).not.toContainText("Quase lá");
    await expect(conteudo).toContainText("O envio de aula-1.mp4 não terminou.");
    await expect(conteudo).toContainText("Se ele ainda estiver subindo em outra aba, espere por lá");
    await expect(conteudo).not.toContainText("A verificação está demorando");
    await expect(conteudo.locator('input[type="file"]')).toBeEnabled();
  });

  test("envio vencido chega expirado e não deixa aviso nenhum", async ({ page }) => {
    await login(page);
    await openCurriculum(page, [], [{
      id: "bb000000-0000-4000-8000-000000000002",
      status: "expired",
      file_name: "aula-1.mp4",
      byte_size: 209_420_000,
      safe_reason: "O prazo do envio terminou. Escolha o arquivo novamente."
    }]);

    const conteudo = conteudoDaAula(page);
    await expect(conteudo.locator('input[type="file"]')).toBeAttached();
    await expect(conteudo).not.toContainText("não terminou");
    await expect(conteudo).not.toContainText("A verificação está demorando");
    await expect(conteudo.locator('input[type="file"]')).toBeEnabled();
  });
});
