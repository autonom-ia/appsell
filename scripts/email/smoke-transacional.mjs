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

function configuracao(env) {
  const host = env.SMTP_HOST_TRANSACIONAL;
  const user = env.SMTP_USER_TRANSACIONAL;
  const pass = env.SMTP_PASS_TRANSACIONAL;
  const faltando = [
    host ? null : "SMTP_HOST_TRANSACIONAL",
    user ? null : "SMTP_USER_TRANSACIONAL",
    pass ? null : "SMTP_PASS_TRANSACIONAL"
  ].filter(Boolean);
  if (faltando.length > 0) return { faltando };
  return {
    faltando: [],
    de: { name: REMETENTE_NOME, address: user },
    transporte: {
      host,
      port: Number(env.SMTP_PORT_TRANSACIONAL) || 465,
      secure: env.SMTP_SECURE_TRANSACIONAL !== "false",
      auth: { user, pass }
    }
  };
}

function destinatarioValido(valor) {
  return typeof valor === "string" && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(valor);
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
  if (config.faltando.length > 0) {
    console.error(`faltam variáveis: ${config.faltando.join(", ")}`);
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
      to: para,
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
