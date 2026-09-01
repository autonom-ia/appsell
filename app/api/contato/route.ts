import { NextRequest, NextResponse } from "next/server";
import { enviarContatoComercial } from "@/lib/email/contato-comercial";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const LIMITES = { nome: 120, email: 200, loja: 160, mensagem: 4_000 } as const;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

type Erro = { readonly campo: string; readonly motivo: string };

function texto(valor: unknown, campo: keyof typeof LIMITES, minimo: number): string | Erro {
  if (typeof valor !== "string") return { campo, motivo: "ausente" };
  const limpo = valor.trim();
  if (limpo.length < minimo) return { campo, motivo: "curto" };
  if (limpo.length > LIMITES[campo]) return { campo, motivo: "longo" };
  return limpo;
}

export async function POST(request: NextRequest) {
  let corpo: unknown;
  try {
    corpo = await request.json();
  } catch {
    return NextResponse.json({ error: "corpo_invalido" }, { status: 400, headers: { "cache-control": "no-store" } });
  }
  if (typeof corpo !== "object" || corpo === null) {
    return NextResponse.json({ error: "corpo_invalido" }, { status: 400, headers: { "cache-control": "no-store" } });
  }
  const dados = corpo as Record<string, unknown>;

  // Campo-isca: humano nunca preenche, robo preenche quase sempre. Responde 204
  // para o robo nao aprender a diferenca entre aceito e recusado.
  if (typeof dados.site_empresa === "string" && dados.site_empresa.trim()) {
    return new NextResponse(null, { status: 204, headers: { "cache-control": "no-store" } });
  }

  const campos = {
    nome: texto(dados.nome, "nome", 2),
    email: texto(dados.email, "email", 5),
    loja: texto(dados.loja, "loja", 2),
    mensagem: texto(dados.mensagem, "mensagem", 20)
  };
  const invalido = Object.values(campos).find((v) => typeof v !== "string") as Erro | undefined;
  if (invalido) {
    return NextResponse.json({ error: "campo_invalido", campo: invalido.campo }, { status: 422, headers: { "cache-control": "no-store" } });
  }
  if (!EMAIL.test(campos.email as string)) {
    return NextResponse.json({ error: "campo_invalido", campo: "email" }, { status: 422, headers: { "cache-control": "no-store" } });
  }

  // Envio direto por SMTP, com as mesmas variaveis do google-saas. Sem destino
  // configurado a mensagem se perderia, e responder 200 seria mentir para quem
  // escreveu: a pagina mostra o e-mail como alternativa quando isto falha.
  const enviado = await enviarContatoComercial({
    nome: campos.nome as string,
    email: campos.email as string,
    loja: campos.loja as string,
    mensagem: campos.mensagem as string,
    origem: request.headers.get("origin"),
    agente: request.headers.get("user-agent"),
    ip: request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? request.headers.get("x-real-ip")
  });
  if (!enviado) {
    return NextResponse.json({ error: "contato_indisponivel" }, { status: 503, headers: { "cache-control": "no-store" } });
  }

  return NextResponse.json({ ok: true }, { status: 200, headers: { "cache-control": "no-store" } });
}
