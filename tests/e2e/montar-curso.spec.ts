import { expect as expectBase, test, type Locator, type Page } from "@playwright/test";

import { UNDO_WINDOW_MS } from "../../lib/curriculum/undo-queue";

import { login } from "./support/login";
import {
  abrirCurso,
  arquivoPronto,
  aula,
  CursoSimulado,
  modulo
} from "./support/curso-simulado";

// A suíte inteira roda sobre o build de produção e sob carga; 20 s são
// paciência, não tolerância: cada asserção continua medindo comportamento.
const expect = expectBase.configure({ timeout: 20_000 });

/**
 * Montar o curso, do começo ao fim, como quem vende (#649).
 *
 * A regra do Rodrigo: uma jornada tão simples que qualquer pessoa, sem
 * conhecimento de tecnologia, consegue usar. Na prática, cada teste abaixo
 * prova uma dessas promessas: uma ação principal por tela, palavras do dia a
 * dia, estado sempre à vista, "Desfazer" no lugar de pergunta de risco, erro
 * que diz como resolver, alvo de toque de 44 px e nenhum `<select>`.
 */

function painelPrincipal(page: Page): Locator {
  return page.getByRole("region", { name: "Vídeo ou PDF da aula" });
}

function painelMateriais(page: Page): Locator {
  return page.getByRole("region", { name: "Materiais para o aluno" });
}

test.describe("montar o curso do zero", () => {
  test("do curso vazio à aula pronta, sem nenhuma palavra técnica", async ({ page }) => {
    const curso = new CursoSimulado();
    await login(page);
    await abrirCurso(page, curso);

    // Curso vazio: uma ação só, com exemplo.
    const vazio = page.getByTestId("curriculum-empty");
    await expect(vazio).toBeVisible();
    await expect(vazio).toContainText("Comece pelo primeiro módulo");
    await vazio.getByLabel("Nome do módulo", { exact: true }).fill("Boas-vindas");
    await vazio.getByRole("button", { name: "Criar módulo" }).click();

    await expect(page.getByRole("heading", { level: 3, name: "Boas-vindas" })).toBeVisible();
    expect(curso.pedidosPara("POST", "/modules")).toHaveLength(1);
    expect(curso.pedidosPara("POST", "/modules")[0].fields.title).toBe("Boas-vindas");

    // O próximo passo já vem pronto: o nome da primeira aula, com o foco nele.
    const nomeDaAula = page.getByLabel("Nome da nova aula");
    await expect(nomeDaAula).toBeFocused();
    // E a aula nova abre sozinha: criar e preencher são o mesmo passo.
    await nomeDaAula.fill("Como funciona o método");
    await page.getByRole("button", { name: "Criar e abrir a aula" }).click();

    await expect(page).toHaveURL(/aula=/);
    const titulo = page.getByRole("heading", { level: 2, name: /Como funciona o método/ });
    await expect(titulo).toBeVisible();
    await expect(titulo).toBeFocused();

    // O vídeo: escolher, ver o andamento, ver pronto.
    const principal = painelPrincipal(page);
    await expect(principal.getByText("Vídeo MP4 até 512 MB")).toBeVisible();
    await principal.getByLabel("Escolher vídeo ou PDF").setInputFiles({
      name: "aula-1.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.from("conteudo de video de teste")
    });
    const card = principal.getByRole("article", { name: "aula-1.mp4" });
    await expect(card).toBeVisible();
    await expect(card).toContainText("✓ Pronto");
    await expect(card.getByRole("button", { name: "Trocar arquivo: aula-1.mp4" })).toBeVisible();
    await expect(principal.getByRole("status").filter({ hasText: "Pronto: aula-1.mp4 entrou na aula." })).toBeVisible();

    // O texto salva sozinho e diz que salvou.
    const texto = page.getByRole("textbox", { name: "Texto da aula" });
    await texto.fill("Assista ao vídeo e anote as três ideias principais.");
    await expect(page.getByText("✓ Salvo")).toBeVisible();
    await expect.poll(() => curso.modulos[0].lessons[0].content).toBe("Assista ao vídeo e anote as três ideias principais.");

    // O link: cola, a máquina reconhece, o nome vem sugerido.
    const materiais = painelMateriais(page);
    await materiais.getByRole("button", { name: "+ Adicionar link" }).click();
    const campoLink = materiais.getByLabel("Cole o link");
    await expect(campoLink).toBeFocused();
    await campoLink.fill("youtu.be/dQw4w9WgXcQ");
    await expect(materiais.getByText("Vídeo do YouTube reconhecido. O aluno assiste dentro da aula.")).toBeVisible();
    await expect(materiais.getByLabel("Nome que o aluno vai ver")).toHaveValue("Vídeo do YouTube");
    await materiais.getByLabel("Nome que o aluno vai ver").fill("Aula extra no YouTube");
    await materiais.getByRole("button", { name: "Adicionar link", exact: true }).click();
    await expect(materiais.getByRole("article", { name: "Aula extra no YouTube" })).toBeVisible();
    const criado = curso.pedidosPara("POST", "/materials")[0].fields;
    expect(criado).toMatchObject({ kind: "embed", provider: "youtube", video_id: "dQw4w9WgXcQ", title: "Aula extra no YouTube" });

    // Pronto: a aula diz que está pronta e devolve para o curso.
    await page.getByRole("button", { name: "Aula pronta · voltar para o curso" }).click();
    await expect(page).not.toHaveURL(/aula=/);
    await expect(page.getByText("1 de 1 aula pronta")).toBeVisible();
    await expect(page.getByRole("link", { name: "Ir para Preço" })).toBeVisible();
    await expect(page.getByTestId(`lesson-${curso.modulos[0].lessons[0].id}`)).toContainText("✓ Pronta");

    // Nenhum termo de máquina em nenhum momento da tela (a mesma lista de
    // FORBIDDEN_SELLER_WORDS, que o teste de unidade aplica aos textos).
    const palavras = (await page.getByTestId("curriculum-workspace").innerText()).toLowerCase();
    for (const proibida of ["arquivar", "arquivad", "verificaç", "verificand", "rascunho", "multipart", "intenção", "upload", "a api "]) {
      expect(palavras).not.toContain(proibida);
    }
    // E nenhuma mudança esbarrou na versão do curso: texto, vídeo e link
    // salvaram em sequência, cada um com a versão mais nova.
    expect(curso.conflitos).toBe(0);
  });

  test("o progresso aponta a próxima aula que falta", async ({ page }) => {
    const pronta = aula("Abertura", "Bem-vindo ao curso.");
    const falta = aula("Primeiro exercício");
    const curso = new CursoSimulado([modulo("Começo", [pronta, falta])]);
    await login(page);
    await abrirCurso(page, curso);

    await expect(page.getByText("1 de 2 aulas prontas")).toBeVisible();
    await expect(page.getByText("Próximo passo: coloque o vídeo, o PDF ou o texto de “Primeiro exercício”.")).toBeVisible();
    await expect(page.getByTestId(`lesson-${falta.id}`)).toContainText("Falta o vídeo, PDF ou texto");
    await page.getByRole("button", { name: "Continuar" }).click();
    await expect(page).toHaveURL(new RegExp(`aula=${falta.id}`));
    await expect(page.getByRole("heading", { level: 2, name: /Primeiro exercício/ })).toBeVisible();
  });
});

test.describe("organizar sem medo", () => {
  test("renomear e mudar a ordem com um toque, anunciando a nova posição", async ({ page }) => {
    const primeira = aula("Primeira", "texto");
    const segunda = aula("Segunda", "texto");
    const curso = new CursoSimulado([modulo("Módulo A", [primeira, segunda]), modulo("Módulo B")]);
    await login(page);
    await abrirCurso(page, curso);

    await page.getByRole("button", { name: "Mudar nome do módulo Módulo A" }).click();
    const nome = page.getByLabel("Nome do módulo", { exact: true });
    await expect(nome).toBeFocused();
    await nome.fill("Fundamentos");
    await page.getByRole("button", { name: "Salvar nome" }).click();
    await expect(page.getByRole("heading", { level: 3, name: "Fundamentos" })).toBeVisible();
    expect(curso.pedidosPara("PATCH", `/modules/${curso.modulos[0].id}`)[0].fields.title).toBe("Fundamentos");

    await page.getByRole("button", { name: "Descer aula Primeira" }).click();
    await expect(page.getByTestId("curriculum-live")).toHaveText("Aula Primeira agora é a 2ª de 2.");
    await expect.poll(() => curso.modulos[0].lessons.map((entry) => entry.title)).toEqual(["Segunda", "Primeira"]);
    // O foco segue a aula que andou: no fim da lista, o botão de subir dela.
    await expect(page.getByRole("button", { name: "Subir aula Primeira" })).toBeFocused();

    await page.getByRole("button", { name: "Descer módulo Fundamentos" }).click();
    await expect(page.getByTestId("curriculum-live")).toHaveText("Módulo Fundamentos agora é o 2º de 2.");
    await expect.poll(() => curso.modulos.map((entry) => entry.title)).toEqual(["Módulo B", "Fundamentos"]);
  });

  test("excluir aula some na hora, oferece Desfazer, e só apaga quando o aviso vence", async ({ page }) => {
    const fica = aula("Fica", "texto");
    const sai = aula("Sai", "texto");
    const curso = new CursoSimulado([modulo("Módulo", [fica, sai])]);
    await page.clock.install();
    await login(page);
    await abrirCurso(page, curso, sai.id);

    await page.getByRole("button", { name: "Excluir aula" }).click();
    // Nenhuma pergunta de risco: a aula sai da lista e o aviso aparece.
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByTestId(`lesson-${sai.id}`)).toHaveCount(0);
    const aviso = page.locator(".undo-toast");
    await expect(aviso).toContainText("Aula “Sai” excluída.");

    // Desfazer devolve tudo, e nada foi pedido ao servidor.
    await aviso.getByRole("button", { name: "Desfazer" }).click();
    await expect(page.getByTestId(`lesson-${sai.id}`)).toBeVisible();
    expect(curso.pedidosPara("POST", "/archive")).toHaveLength(0);

    // Sem desfazer, o pedido sai quando o aviso vence.
    await page.getByTestId(`lesson-${sai.id}`).getByRole("link").click();
    await page.getByRole("button", { name: "Excluir aula" }).click();
    await expect(aviso).toBeVisible();
    await page.clock.runFor(UNDO_WINDOW_MS + 500);
    await expect(aviso).toHaveCount(0);
    await expect.poll(() => curso.pedidosPara("POST", `/lessons/${sai.id}/archive`).length).toBe(1);
    await expect.poll(() => curso.modulos[0].lessons.map((entry) => entry.id)).toEqual([fica.id]);
  });

  test("excluir módulo diz quantas aulas vão junto e também pode ser desfeito", async ({ page }) => {
    const alvo = modulo("Bônus", [aula("Extra 1", "a"), aula("Extra 2", "b")]);
    const curso = new CursoSimulado([modulo("Principal", [aula("Aula", "c")]), alvo]);
    await page.clock.install();
    await login(page);
    await abrirCurso(page, curso);

    await page.getByRole("button", { name: "Mudar nome do módulo Bônus" }).click();
    await page.getByRole("button", { name: "Excluir módulo Bônus" }).click();
    await expect(page.getByTestId(`module-${alvo.id}`)).toHaveCount(0);
    await page.locator(".undo-toast").getByRole("button", { name: "Desfazer" }).click();
    await expect(page.getByTestId(`module-${alvo.id}`)).toBeVisible();
    expect(curso.pedidosPara("POST", "/archive")).toHaveLength(0);
  });

  test("a falha ao excluir devolve o item e diz o que fazer", async ({ page }) => {
    const sai = aula("Sai", "texto");
    const curso = new CursoSimulado([modulo("Módulo", [aula("Fica", "x"), sai])]);
    await page.clock.install();
    await login(page);
    await abrirCurso(page, curso);
    await page.route(`**/lessons/${sai.id}/archive`, (route) => route.fulfill({ status: 500, json: { error: "x" } }));

    await page.getByTestId(`lesson-${sai.id}`).getByRole("link").click();
    await page.getByRole("button", { name: "Excluir aula" }).click();
    await page.clock.runFor(UNDO_WINDOW_MS + 500);
    await expect(page.getByRole("alert").filter({ hasText: "Não deu para excluir “Sai”." })).toContainText("O item voltou para a lista. Tente de novo.");
    await expect(page.getByTestId(`lesson-${sai.id}`)).toBeVisible();
  });
});

test.describe("arquivos da aula", () => {
  test("tirar da aula não pergunta nada e pode ser desfeito", async ({ page }) => {
    const licao = aula("Com PDF");
    const pdf = arquivoPronto({ file_name: "apostila.pdf", media_type: "application/pdf" });
    const curso = new CursoSimulado([modulo("Módulo", [licao])]);
    curso.arquivos.set(licao.id, [pdf]);
    await page.clock.install();
    await login(page);
    await abrirCurso(page, curso, licao.id);

    const principal = painelPrincipal(page);
    const card = principal.getByRole("article", { name: "apostila.pdf" });
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Tirar da aula: apostila.pdf" }).click();
    await expect(card).toHaveCount(0);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.locator(".undo-toast").getByRole("button", { name: "Desfazer" }).click();
    await expect(card).toBeVisible();
    expect(curso.pedidosPara("POST", "/archive")).toHaveLength(0);

    await card.getByRole("button", { name: "Tirar da aula: apostila.pdf" }).click();
    await page.clock.runFor(UNDO_WINDOW_MS + 500);
    await expect.poll(() => curso.pedidosPara("POST", `/assets/${pdf.id}/archive`).length).toBe(1);
    // Sem o arquivo, a aula volta a oferecer a escolha principal, à vista.
    await expect(principal.getByText("Escolher vídeo ou PDF", { exact: true })).toBeVisible();
  });

  test("arquivo que não entra diz por quê e como resolver, sem enviar nada", async ({ page }) => {
    const licao = aula("Vídeo do celular");
    const curso = new CursoSimulado([modulo("Módulo", [licao])]);
    await login(page);
    await abrirCurso(page, curso, licao.id);

    const principal = painelPrincipal(page);
    await principal.getByLabel("Escolher vídeo ou PDF").setInputFiles({
      name: "IMG_0001.MOV",
      mimeType: "video/quicktime",
      buffer: Buffer.from("mov")
    });
    await expect(principal.getByText("Vídeos .MOV do iPhone ainda não entram.")).toBeVisible();
    await expect(principal.getByText(/Exporte o vídeo como MP4/)).toBeVisible();
    await expect(principal.getByText("Escolher o arquivo de novo", { exact: true })).toBeVisible();
    expect(curso.pedidosPara("POST", "/upload-intents")).toHaveLength(0);
  });

  test("parar o envio cancela no servidor e deixa escolher de novo", async ({ page }) => {
    const licao = aula("Envio longo");
    const curso = new CursoSimulado([modulo("Módulo", [licao])]);
    curso.envio.segurarStorage = true;
    await login(page);
    await abrirCurso(page, curso, licao.id);

    const principal = painelPrincipal(page);
    await principal.getByLabel("Escolher vídeo ou PDF").setInputFiles({
      name: "aula-longa.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(64 * 1024, 1)
    });
    await expect(principal.getByText(/^Enviando \d+%$/)).toBeVisible();
    await expect(principal.getByRole("progressbar", { name: "Andamento do envio" })).toBeVisible();
    // Durante o envio a aula não pode ser excluída, e diz por quê.
    await expect(page.getByRole("button", { name: "Excluir aula" })).toHaveAttribute("aria-disabled", "true");
    await expect(page.getByText("Espere o envio terminar, ou pare o envio, para excluir a aula.")).toBeVisible();

    await principal.getByRole("button", { name: "Parar envio" }).click();
    curso.liberarEnvio();
    await expect.poll(() => curso.pedidosPara("POST", "/cancel").length).toBe(1);
    await expect(principal.getByText("Escolher o arquivo de novo", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Excluir aula" })).not.toHaveAttribute("aria-disabled", "true");
  });

  test("voltar para o curso no meio do envio não interrompe o envio", async ({ page }) => {
    const licao = aula("Envio em segundo plano");
    const curso = new CursoSimulado([modulo("Módulo", [licao, aula("Outra", "texto")])]);
    curso.envio.segurarStorage = true;
    await login(page);
    await abrirCurso(page, curso, licao.id);

    await painelPrincipal(page).getByLabel("Escolher vídeo ou PDF").setInputFiles({
      name: "aula.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(32 * 1024, 2)
    });
    await expect(painelPrincipal(page).getByText(/^Enviando \d+%$/)).toBeVisible();
    await page.getByRole("button", { name: "Voltar para o curso", exact: true }).click();
    await expect(page).not.toHaveURL(/aula=/);
    await expect(page.getByTestId(`lesson-${licao.id}`)).toContainText(/Enviando \d+%/);
    expect(curso.pedidosPara("POST", "/cancel")).toHaveLength(0);

    // O envio segue sozinho com a lista na tela e chega ao fim.
    curso.liberarEnvio({ concluir: true });
    await expect.poll(() => curso.pedidosPara("POST", "/confirm").length).toBe(1);
    await expect(page.getByTestId(`lesson-${licao.id}`)).toContainText("✓ Pronta");
    expect(curso.pedidosPara("POST", "/cancel")).toHaveLength(0);
  });

  test("aula aberta pela lista: voltar no meio do envio mostra a lista sem pedir confirmação", async ({ page }) => {
    const licao = aula("Aberta pela lista");
    const curso = new CursoSimulado([modulo("Módulo", [licao])]);
    curso.envio.segurarStorage = true;
    await login(page);
    await abrirCurso(page, curso);

    await page.locator(`[data-lesson-link="${licao.id}"]`).click();
    await expect(page).toHaveURL(/aula=/);
    await painelPrincipal(page).getByLabel("Escolher vídeo ou PDF").setInputFiles({
      name: "aula.mp4",
      mimeType: "video/mp4",
      buffer: Buffer.alloc(32 * 1024, 2)
    });
    await expect(painelPrincipal(page).getByText(/^Enviando \d+%$/)).toBeVisible();

    // Com o envio em curso a guarda de saída está armada. Voltar para o curso
    // é ficar na página: nada de diálogo de saída, e a lista aparece.
    await page.getByRole("button", { name: "Voltar para o curso", exact: true }).click();
    await expect(page).not.toHaveURL(/aula=/);
    await expect(page.getByTestId(`lesson-${licao.id}`)).toContainText(/Enviando \d+%/);
    await expect(page.getByRole("dialog")).toHaveCount(0);

    curso.liberarEnvio({ concluir: true });
    await expect(page.getByTestId(`lesson-${licao.id}`)).toContainText("✓ Pronta");
    await expect(page).not.toHaveURL(/aula=/);
    await expect(page.locator(`[data-lesson-link="${licao.id}"]`)).toBeVisible();
  });

  test("material para o aluno também termina de subir com outra aula aberta", async ({ page }) => {
    const licao = aula("Aula com material");
    const outra = aula("Outra aula", "texto");
    const curso = new CursoSimulado([modulo("Módulo", [licao, outra])]);
    curso.envio.segurarStorage = true;
    await login(page);
    await abrirCurso(page, curso, licao.id);

    await painelMateriais(page).locator('input[type="file"]').setInputFiles({
      name: "roteiro.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# roteiro\n")
    });
    await expect(painelMateriais(page).getByText(/^Enviando \d+%$/)).toBeVisible();
    await page.getByRole("button", { name: "Voltar para o curso", exact: true }).click();
    // O foco volta ao card um quadro depois e rola a lista até ele; o clique
    // espera essa rolagem para não cair fora do card.
    await expect(page.locator(`[data-lesson-link="${licao.id}"]`)).toBeFocused();
    await page.locator(`[data-lesson-link="${outra.id}"]`).click();
    await expect(page.getByRole("heading", { level: 2, name: /Outra aula/ })).toBeVisible();

    // Sem o editor da primeira aula montado, o envio era cortado aqui.
    curso.liberarEnvio({ concluir: true });
    await expect.poll(() => curso.pedidosPara("POST", "/confirm").length).toBe(1);
    expect(curso.pedidosPara("POST", "/cancel")).toHaveLength(0);

    await page.getByRole("button", { name: "Voltar para o curso", exact: true }).click();
    await expect(page.locator(`[data-lesson-link="${outra.id}"]`)).toBeFocused();
    await page.locator(`[data-lesson-link="${licao.id}"]`).click();
    await expect(painelMateriais(page).getByRole("article", { name: "roteiro.md" })).toBeVisible();
  });
});

test.describe("chegar e sair da aula", () => {
  test("o endereço abre a aula direto e Voltar devolve o foco para ela na lista", async ({ page }) => {
    const licao = aula("Aula do link", "texto");
    const curso = new CursoSimulado([modulo("Módulo", [aula("Antes", "x"), licao])]);
    await login(page);
    await abrirCurso(page, curso, licao.id);

    await expect(page.getByRole("heading", { level: 2, name: /Aula do link/ })).toBeVisible();
    await page.getByRole("button", { name: "Voltar para o curso", exact: true }).click();
    await expect(page).not.toHaveURL(/aula=/);
    await expect(page.locator(`[data-lesson-link="${licao.id}"]`)).toBeFocused();
  });

  test("o nome da aula salva sozinho e recusa nome curto dizendo o mínimo", async ({ page }) => {
    const licao = aula("Nome antigo", "texto");
    const curso = new CursoSimulado([modulo("Módulo", [licao])]);
    await login(page);
    await abrirCurso(page, curso, licao.id);

    const nome = page.getByLabel("Nome da aula");
    await nome.fill("A");
    await nome.blur();
    await expect(page.getByRole("alert").filter({ hasText: "Escreva um nome com pelo menos 2 letras." })).toBeVisible();
    expect(curso.pedidosPara("PATCH", `/lessons/${licao.id}`)).toHaveLength(0);

    await nome.fill("Nome novo");
    await nome.blur();
    await expect.poll(() => curso.modulos[0].lessons[0].title).toBe("Nome novo");
    await expect(page.getByRole("heading", { level: 2, name: /Nome novo/ })).toBeVisible();
  });
});

test.describe("feito para qualquer pessoa, em qualquer tela", () => {
  async function conferir(page: Page) {
    const area = page.getByTestId("curriculum-workspace");
    await expect(area).toBeVisible();
    expect(await area.locator("select").count()).toBe(0);
    const pequenos = await area.evaluate((raiz) => {
      const alvos = raiz.querySelectorAll<HTMLElement>("button, a[href], label.primary-button, label.secondary-button, input[type=checkbox]");
      return [...alvos]
        .filter((alvo) => alvo.getClientRects().length > 0 && getComputedStyle(alvo).visibility !== "hidden")
        .filter((alvo) => {
          const caixa = alvo.getBoundingClientRect();
          // A caixa marcável conta pelo rótulo inteiro, que é o alvo do toque.
          const rotulo = alvo instanceof HTMLInputElement ? alvo.closest("label")?.getBoundingClientRect() : null;
          const medida = rotulo ?? caixa;
          return medida.height < 44 || medida.width < 44;
        })
        .map((alvo) => alvo.getAttribute("aria-label") || alvo.textContent?.trim() || alvo.tagName);
    });
    expect(pequenos).toEqual([]);
    const rolagemLateral = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(rolagemLateral).toBeLessThanOrEqual(0);
  }

  test("lista e aula: nenhum select, alvos de 44 px e nada vazando para o lado", async ({ page }) => {
    const licao = aula("Uma aula com um nome bem comprido para testar a quebra de linha no celular", "texto");
    const pdf = arquivoPronto({ file_name: "um_arquivo_com_nome_muito_comprido_sem_espacos_para_quebrar.pdf", media_type: "application/pdf" });
    const planilha = arquivoPronto({ file_name: "planilha.xlsx", media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", content_role: "support_material", download_allowed: true });
    const curso = new CursoSimulado([modulo("Módulo com nome comprido o bastante para quebrar", [licao, aula("Outra")])]);
    curso.arquivos.set(licao.id, [pdf, planilha]);
    curso.links.set(licao.id, [{ material_id: "e0e0e0e0-0000-4000-8000-000000000999", kind: "external", title: "Site", url: "https://exemplo.com.br/a", provider: null, video_id: null, position: 1 }]);
    await login(page);
    await abrirCurso(page, curso);
    await conferir(page);

    await page.getByTestId(`lesson-${licao.id}`).getByRole("link").click();
    await expect(painelPrincipal(page).getByRole("article", { name: pdf.file_name })).toBeVisible();
    await expect(painelMateriais(page).getByRole("article", { name: "planilha.xlsx" })).toBeVisible();
    await expect(painelMateriais(page).getByRole("switch", { name: "O aluno pode baixar: planilha.xlsx" })).toBeChecked();
    await conferir(page);
  });
});
