import nodemailer from "nodemailer";

// Mesmo mecanismo e mesmas variaveis do google-saas, para o SMTP comercial ja
// configurado servir as duas landings sem duplicar credencial.
export type ContatoComercial = {
  readonly nome: string;
  readonly email: string;
  readonly loja: string;
  readonly mensagem: string;
  readonly origem?: string | null;
  readonly agente?: string | null;
  readonly ip?: string | null;
};

function escapar(valor: string) {
  return valor
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function transporteComercial() {
  const host = process.env.SMTP_HOST_COMERCIAL;
  const user = process.env.SMTP_USER_COMERCIAL;
  const pass = process.env.SMTP_PASS_COMERCIAL;
  if (!host || !user || !pass) return null;

  return {
    de: user,
    para: process.env.SMTP_TO_COMERCIAL ?? user,
    transporte: nodemailer.createTransport({
      host,
      port: Number(process.env.SMTP_PORT_COMERCIAL) || 465,
      secure: process.env.SMTP_SECURE_COMERCIAL !== "false",
      auth: { user, pass }
    })
  };
}

function linha(rotulo: string, valor: string) {
  return `<tr>
    <td style="padding:0 0 10px 0;color:#64748b;font-size:13px;">${escapar(rotulo)}</td>
    <td style="padding:0 0 10px 16px;color:#0f172a;font-size:14px;font-weight:600;">${escapar(valor)}</td>
  </tr>`;
}

function corpoHtml(dados: ContatoComercial) {
  const opcionais = [
    dados.origem ? linha("Origem", dados.origem) : "",
    dados.ip ? linha("IP", dados.ip) : "",
    dados.agente ? linha("Navegador", dados.agente) : ""
  ].join("");

  return `<div style="font-family:system-ui,-apple-system,sans-serif;background:#f7f8f4;padding:32px;">
  <div style="max-width:640px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;">
    <div style="background:#001b43;padding:20px 28px;">
      <p style="margin:0;color:#53d9f9;font-size:11px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;">Autonom.ia Sell</p>
      <h1 style="margin:8px 0 0;color:#fff;font-size:20px;">Novo contato pela página pública</h1>
    </div>
    <div style="padding:28px;">
      <table style="width:100%;border-collapse:collapse;">
        ${linha("Nome", dados.nome)}
        ${linha("E-mail", dados.email)}
        ${linha("Empresa ou loja", dados.loja)}
        ${opcionais}
      </table>
      <p style="margin:24px 0 8px;color:#64748b;font-size:13px;">Mensagem</p>
      <p style="margin:0;padding:16px;background:#f7f8f4;color:#0f172a;font-size:14px;line-height:1.6;white-space:pre-wrap;">${escapar(dados.mensagem)}</p>
    </div>
  </div>
</div>`;
}

function corpoTexto(dados: ContatoComercial) {
  return [
    "Novo contato pela página pública do Autonom.ia Sell",
    "",
    `Nome: ${dados.nome}`,
    `E-mail: ${dados.email}`,
    `Empresa ou loja: ${dados.loja}`,
    dados.origem ? `Origem: ${dados.origem}` : "",
    dados.ip ? `IP: ${dados.ip}` : "",
    "",
    "Mensagem:",
    dados.mensagem
  ].filter(Boolean).join("\n");
}

/** Retorna false quando o SMTP nao esta configurado ou o envio falha. */
export async function enviarContatoComercial(dados: ContatoComercial): Promise<boolean> {
  const canal = transporteComercial();
  if (!canal) return false;

  try {
    await canal.transporte.sendMail({
      from: canal.de,
      to: canal.para,
      replyTo: dados.email,
      subject: `Contato pela página do Sell: ${dados.nome} (${dados.loja})`,
      text: corpoTexto(dados),
      html: corpoHtml(dados)
    });
    return true;
  } catch {
    return false;
  }
}
