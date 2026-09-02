// Layout base de todo e-mail transacional do Sell. O que entra aqui e a regra
// da docs/architecture/EMAIL_POLICY.md: remetente identificado, natureza dita
// em uma linha ("voce recebe porque ..."), sem rastreador, sem descadastro
// porque nao ha o que descadastrar. Os templates da #357 preenchem as pecas;
// este arquivo desenha a moldura uma vez so.
//
// A moldura e a da pagina publica: navy escuro no topo com o icone da
// Autonom.ia, cartao branco sobre creme, laranja no botao — as mesmas cores de
// app/lp.css. Tabelas e estilo inline porque cliente de e-mail nao le
// stylesheet nem flexbox; imagens por URL absoluta porque anexo inline vira
// "imagem bloqueada" e base64 e cortado pelo Gmail.

export type AcaoEmail = {
  readonly rotulo: string;
  readonly url: string;
};

export type DetalheEmail = {
  readonly rotulo: string;
  readonly valor: string;
};

export type EmailTransacional = {
  /** Assunto do e-mail; tambem vira o <title>. */
  readonly assunto: string;
  /** Linha pequena acima do titulo, ex.: "Do lado de quem comprou". */
  readonly eyebrow?: string;
  readonly titulo: string;
  /** Paragrafos em texto puro; o layout escapa e quebra. */
  readonly paragrafos: readonly string[];
  /** Botao unico. So https, senao o layout recusa. */
  readonly acao?: AcaoEmail;
  /** Pares rotulo/valor, ex.: curso, loja, valor pago. */
  readonly detalhes?: readonly DetalheEmail[];
  /** Por que a pessoa recebe. Vai no rodape, obrigatorio pela politica (§3). */
  readonly motivo: string;
};

export type EmailRenderizado = {
  readonly html: string;
  readonly text: string;
};

const CORES = {
  navy: "#001b43",
  ink: "#071321",
  cream: "#f7f8f4",
  laranja: "#c9420d",
  cinza: "#5b6675",
  linha: "#e3e7ee"
} as const;

const FONTE = "'Helvetica Neue', Helvetica, Arial, sans-serif";
const LARGURA = 600;

const ORIGEM_PADRAO = "https://sell.autonomia.site";

// Mesma fonte de lib/site.ts (SITE.origem). Lida aqui, e nao importada,
// porque este arquivo tambem roda direto no Node (scripts/email/*.mjs), e o
// ESM do Node exige extensao no import — que o tsc do projeto nao aceita. O
// teste do layout confere que as duas leituras concordam.
//
// Normalizada para a origem pura (sem caminho, sem barra final, sem
// credenciais) porque ela entra em atributos src/href: uma aspa ou uma barra
// a mais aqui quebra todo e-mail que sair.
export function origemPublica(): string {
  const bruta = process.env.NEXT_PUBLIC_SITE_ORIGIN ?? ORIGEM_PADRAO;
  let analisada: URL;
  try {
    analisada = new URL(bruta);
  } catch {
    throw new Error("NEXT_PUBLIC_SITE_ORIGIN precisa ser uma origem https valida");
  }
  if (analisada.protocol !== "https:" || analisada.username !== "" || analisada.password !== "") {
    throw new Error("NEXT_PUBLIC_SITE_ORIGIN precisa ser uma origem https valida");
  }
  return analisada.origin;
}

// Texto de uma linha: assunto, titulo, eyebrow, rotulos, motivo. Quebra de
// linha ou controle aqui e como se imita o rodape na versao em texto puro.
const CONTROLE = /[\u0000-\u001f\u007f]/;

function umaLinha(campo: string, valor: string): string {
  if (CONTROLE.test(valor)) {
    throw new Error(`${campo} nao pode conter quebra de linha nem caractere de controle`);
  }
  return valor;
}

export const TAGLINE =
  "Autonom.ia Sell é a plataforma da Autonom.ia para criar, vender e entregar produtos digitais.";
const EMPRESA = "Autonom.ia Tecnologia Ltda (CNPJ 59.303.298/0001-51)";
const CONTATO_DADOS = "contato@autonomia.site";

function escapar(valor: string): string {
  return valor
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// O botao so leva para a nossa origem. "https://sell.autonomia.site@evil.com/"
// e uma URL https valida cujo host e evil.com; "https://u:p@host/" carrega
// credencial para dentro do HTML. Ambas caem aqui.
function urlDeAcao(url: string): string {
  const erro = new Error(`acao.url precisa ser uma URL https absoluta em ${origemPublica()}`);
  let analisada: URL;
  try {
    analisada = new URL(url);
  } catch {
    throw erro;
  }
  if (analisada.username !== "" || analisada.password !== "") throw erro;
  if (analisada.origin !== origemPublica()) throw erro;
  return analisada.toString();
}

function paragrafo(texto: string): string {
  return `<p style="margin:0 0 16px;font-size:16px;line-height:1.6;color:${CORES.ink};">${escapar(texto)}</p>`;
}

function tabelaDetalhes(detalhes: readonly DetalheEmail[]): string {
  if (detalhes.length === 0) return "";
  const linhas = detalhes
    .map(
      ({ rotulo, valor }) => `<tr>
  <td style="padding:10px 0;border-top:1px solid ${CORES.linha};font-size:13px;color:${CORES.cinza};vertical-align:top;width:38%;">${escapar(rotulo)}</td>
  <td style="padding:10px 0 10px 12px;border-top:1px solid ${CORES.linha};font-size:15px;font-weight:700;color:${CORES.ink};vertical-align:top;">${escapar(valor)}</td>
</tr>`
    )
    .join("\n");
  return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 24px;border-collapse:collapse;">
${linhas}
</table>`;
}

function botao(acao: AcaoEmail): string {
  const href = escapar(urlDeAcao(acao.url));
  // Duas linhas da mesma tabela, sem margem negativa: o Outlook de Word
  // ignora margem negativa e o texto de apoio cairia longe do botao.
  return `<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:8px 0 24px;">
<tr>
  <td bgcolor="${CORES.laranja}" style="border-radius:8px;">
    <a href="${href}" style="display:inline-block;padding:15px 28px;font-family:${FONTE};font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">${escapar(acao.rotulo)} &rarr;</a>
  </td>
</tr>
<tr>
  <td style="padding-top:12px;font-family:${FONTE};font-size:12px;line-height:1.5;color:${CORES.cinza};">Se o botão não abrir, copie este endereço no navegador:<br><a href="${href}" style="color:${CORES.navy};word-break:break-all;">${href}</a></td>
</tr>
</table>`;
}

function cabecalho(): string {
  const icone = `${origemPublica()}/lp/icone.png`;
  return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="${CORES.navy}" style="background:${CORES.navy};">
<tr>
  <td style="padding:28px 32px;">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0">
    <tr>
      <td style="padding-right:12px;vertical-align:middle;"><img src="${icone}" width="36" height="36" alt="" style="display:block;width:36px;height:36px;border:0;"></td>
      <td style="vertical-align:middle;font-family:${FONTE};">
        <span style="display:block;font-size:19px;font-weight:900;letter-spacing:-0.02em;line-height:1;color:#ffffff;">Autonom.ia</span>
        <span style="display:block;margin-top:5px;font-size:10px;font-weight:900;letter-spacing:1.6px;text-transform:uppercase;color:#a9b4c6;">Sell</span>
      </td>
    </tr>
    </table>
  </td>
</tr>
</table>`;
}

function rodape(motivo: string): string {
  const marca = `${origemPublica()}/lp/marca.png`;
  const privacidade = `${origemPublica()}/privacidade`;
  return `<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
  <td style="padding:28px 32px 8px;font-family:${FONTE};">
    <img src="${marca}" width="140" alt="Autonom.ia" style="display:block;width:140px;height:auto;border:0;margin-bottom:12px;">
    <p style="margin:0 0 18px;font-size:13px;line-height:1.6;color:${CORES.cinza};">${escapar(TAGLINE)}</p>
    <p style="margin:0 0 8px;font-size:12px;line-height:1.6;color:${CORES.cinza};">Autonom.ia Sell &middot; enviado por ${escapar(EMPRESA)}</p>
    <p style="margin:0 0 8px;font-size:12px;line-height:1.6;color:${CORES.cinza};">Este é um e-mail transacional sobre a sua conta ou a sua compra; você o recebe porque ${escapar(motivo)}. Ele não depende de inscrição e não tem descadastro.</p>
    <p style="margin:0 0 24px;font-size:12px;line-height:1.6;color:${CORES.cinza};">Dúvidas sobre seus dados: <a href="mailto:${CONTATO_DADOS}" style="color:${CORES.navy};">${CONTATO_DADOS}</a> &middot; <a href="${privacidade}" style="color:${CORES.navy};">Política de Privacidade</a></p>
  </td>
</tr>
</table>`;
}

function html(email: EmailTransacional): string {
  const eyebrow = email.eyebrow
    ? `<p style="margin:0 0 14px;font-size:11px;font-weight:900;letter-spacing:1.6px;text-transform:uppercase;color:${CORES.laranja};">${escapar(email.eyebrow)}</p>`
    : "";
  const corpo = email.paragrafos.map(paragrafo).join("\n");
  const detalhes = tabelaDetalhes(email.detalhes ?? []);
  const acao = email.acao ? botao(email.acao) : "";
  // O preheader e o que o Gmail mostra ao lado do assunto. Sem ele, mostra o
  // inicio do HTML, que e o nome da marca repetido.
  const preheader = escapar(email.paragrafos[0] ?? email.titulo);

  return `<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
<title>${escapar(email.assunto)}</title>
</head>
<body style="margin:0;padding:0;background:${CORES.cream};" bgcolor="${CORES.cream}">
<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;font-size:1px;line-height:1px;color:transparent;mso-hide:all;">${preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="${CORES.cream}" style="background:${CORES.cream};">
<tr>
  <td align="center" style="padding:32px 12px;">
    <table role="presentation" width="${LARGURA}" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:${LARGURA}px;background:#ffffff;border-radius:12px;overflow:hidden;font-family:${FONTE};">
    <tr><td>${cabecalho()}</td></tr>
    <tr>
      <td style="padding:36px 32px 12px;">
        ${eyebrow}
        <h1 style="margin:0 0 20px;font-size:26px;line-height:1.25;font-weight:900;letter-spacing:-0.02em;color:${CORES.navy};">${escapar(email.titulo)}</h1>
        ${corpo}
        ${detalhes}
        ${acao}
      </td>
    </tr>
    <tr><td style="padding:0 32px;"><div style="height:1px;background:${CORES.linha};line-height:1px;font-size:1px;">&nbsp;</div></td></tr>
    <tr><td>${rodape(email.motivo)}</td></tr>
    </table>
  </td>
</tr>
</table>
</body>
</html>`;
}

function text(email: EmailTransacional): string {
  const detalhes = (email.detalhes ?? []).map(({ rotulo, valor }) => `${rotulo}: ${valor}`);
  const acao = email.acao ? [`${email.acao.rotulo}: ${urlDeAcao(email.acao.url)}`] : [];
  return [
    "AUTONOM.IA SELL",
    "",
    email.eyebrow ? email.eyebrow.toUpperCase() : null,
    email.titulo,
    "",
    ...email.paragrafos.flatMap((p) => [p, ""]),
    ...(detalhes.length > 0 ? [...detalhes, ""] : []),
    ...(acao.length > 0 ? [...acao, ""] : []),
    "--",
    TAGLINE,
    `Autonom.ia Sell · enviado por ${EMPRESA}`,
    `Este é um e-mail transacional sobre a sua conta ou a sua compra; você o recebe porque ${email.motivo}. Ele não depende de inscrição e não tem descadastro.`,
    `Dúvidas sobre seus dados: ${CONTATO_DADOS} · Política de Privacidade: ${origemPublica()}/privacidade`
  ]
    .filter((linha): linha is string => linha !== null)
    .join("\n");
}

function validar(email: EmailTransacional): EmailTransacional {
  if (email.motivo.trim() === "") {
    throw new Error("motivo e obrigatorio: o rodape precisa dizer por que a pessoa recebe");
  }
  umaLinha("assunto", email.assunto);
  umaLinha("titulo", email.titulo);
  umaLinha("motivo", email.motivo);
  if (email.eyebrow !== undefined) umaLinha("eyebrow", email.eyebrow);
  if (email.acao) umaLinha("acao.rotulo", email.acao.rotulo);
  for (const p of email.paragrafos) umaLinha("paragrafo", p);
  for (const { rotulo, valor } of email.detalhes ?? []) {
    umaLinha("detalhe.rotulo", rotulo);
    umaLinha("detalhe.valor", valor);
  }
  return email;
}

export function renderizarEmailTransacional(email: EmailTransacional): EmailRenderizado {
  const valido = validar(email);
  return { html: html(valido), text: text(valido) };
}
