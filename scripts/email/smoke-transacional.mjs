// Smoke do remetente transacional (docs/architecture/EMAIL_POLICY.md, §7).
//
// Envia UM e-mail de teste pelas variáveis SMTP_*_TRANSACIONAL do ambiente onde
// roda e imprime só o que o SMTP devolveu: aceitos, rejeitados e Message-ID.
// Nunca imprime host, usuário ou senha — nem em erro. O aceite real é o
// cabeçalho Authentication-Results na caixa de destino, não a saída daqui.
//
//   node scripts/email/smoke-transacional.mjs destinatario@exemplo.com
//
// Sai com 2 quando falta variável, 1 quando o SMTP recusa, 0 quando aceitou.

import nodemailer from "nodemailer";

const REMETENTE_NOME = "Autonom.ia Sell";
const RODAPE = [
  "Autonom.ia Sell · enviado por Autonom.ia Tecnologia Ltda (CNPJ 59.303.298/0001-51)",
  "Este é um e-mail transacional de verificação do canal; você o recebe porque",
  "pediu um teste de entrega. Ele não depende de inscrição e não tem descadastro.",
  "Dúvidas sobre seus dados: contato@autonomia.site · Política de Privacidade:",
  "https://sell.autonomia.site/privacidade"
].join("\n");

const PORTA_PADRAO = 465;
const PORTA_MAXIMA = 65535;

// Porta ausente vale 465; presente e inválida é erro, não 465 em silêncio —
// senão o smoke testa um canal diferente do configurado e ninguém percebe.
function porta(valor) {
  if (valor === undefined || valor === "") return PORTA_PADRAO;
  if (!/^\d{1,5}$/.test(valor)) return null;
  const numero = Number(valor);
  return numero >= 1 && numero <= PORTA_MAXIMA ? numero : null;
}

// Só "true" e "false" exatos; "0", "FALSE" ou lixo não viram TLS implícito por
// acidente.
function seguro(valor) {
  if (valor === undefined || valor === "") return true;
  if (valor === "true") return true;
  if (valor === "false") return false;
  return null;
}

function configuracao(env) {
  const host = env.SMTP_HOST_TRANSACIONAL;
  const user = env.SMTP_USER_TRANSACIONAL;
  const pass = env.SMTP_PASS_TRANSACIONAL;
  const port = porta(env.SMTP_PORT_TRANSACIONAL);
  const secure = seguro(env.SMTP_SECURE_TRANSACIONAL);
  const invalidas = [
    host ? null : "SMTP_HOST_TRANSACIONAL",
    user ? null : "SMTP_USER_TRANSACIONAL",
    pass ? null : "SMTP_PASS_TRANSACIONAL",
    port === null ? "SMTP_PORT_TRANSACIONAL (inteiro entre 1 e 65535)" : null,
    secure === null ? "SMTP_SECURE_TRANSACIONAL (true ou false)" : null
  ].filter(Boolean);
  if (invalidas.length > 0) return { invalidas };
  return {
    invalidas: [],
    de: { name: REMETENTE_NOME, address: user },
    transporte: { host, port, secure, auth: { user, pass } }
  };
}

// Exatamente um addr-spec (RFC 5322 dot-atom, sem quoted-string): parte local
// de átomos separados por ponto, sem ponto no início, no fim ou duplo; domínio
// de labels de até 63 caracteres, sem hífen nas pontas, TLD alfabético. Sem
// vírgula, ponto e vírgula, espaço ou "<>", porque o parser do nodemailer
// reinterpretaria "a,b@x.co" como nome "a" e endereço "b@x.co".
const ATOMO = "[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+";
const LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?";
const ADDR_SPEC = new RegExp(`^${ATOMO}(?:\\.${ATOMO})*@(?:${LABEL}\\.)+[A-Za-z]{2,63}$`);
const LOCAL_MAX = 64;
const ADDR_MAX = 254;

function destinatarioValido(valor) {
  if (typeof valor !== "string" || valor.length > ADDR_MAX) return false;
  if (!ADDR_SPEC.test(valor)) return false;
  return valor.indexOf("@") <= LOCAL_MAX;
}

// Mensagens de erro do nodemailer podem carregar o comando SMTP inteiro,
// inclusive o AUTH. Só o código e a fase da resposta saem daqui.
function resumoDoErro(erro) {
  const codigo = erro?.responseCode ?? erro?.code ?? "sem código";
  const fase = erro?.command ?? "sem fase";
  return `SMTP recusou: código ${codigo}, fase ${fase}`;
}

async function main() {
  const para = process.argv[2];
  if (!destinatarioValido(para)) {
    console.error("uso: node scripts/email/smoke-transacional.mjs destinatario@exemplo.com");
    return 2;
  }
  const config = configuracao(process.env);
  if (config.invalidas.length > 0) {
    console.error(`variáveis ausentes ou inválidas: ${config.invalidas.join(", ")}`);
    return 2;
  }

  const agora = new Date().toISOString();
  const texto = [
    "Teste de entrega do remetente transacional do Autonom.ia Sell.",
    "",
    `Enviado em ${agora}.`,
    "",
    "Se você está lendo isto, confira em \"Mostrar original\" o cabeçalho",
    "Authentication-Results: o aceite é spf=pass, dkim=pass e dmarc=pass.",
    "",
    RODAPE
  ].join("\n");

  try {
    const transporte = nodemailer.createTransport(config.transporte);
    const resultado = await transporte.sendMail({
      from: config.de,
      // Objeto, não string: o nodemailer não reparseia o endereço.
      to: [{ name: "", address: para }],
      replyTo: config.de.address,
      subject: `[teste] canal transacional do Autonom.ia Sell — ${agora}`,
      text: texto
    });
    console.log(`aceitos: ${resultado.accepted.length}`);
    console.log(`rejeitados: ${resultado.rejected.length}`);
    console.log(`message-id: ${resultado.messageId}`);
    return resultado.rejected.length === 0 ? 0 : 1;
  } catch (erro) {
    console.error(resumoDoErro(erro));
    return 1;
  }
}

process.exitCode = await main();
