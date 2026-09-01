import { NextRequest } from "next/server";
import { beginAuthEntry } from "@/lib/auth-entry.server";

export const dynamic = "force-dynamic";

// Mesma transacao OAuth do login, aberta na tela de criacao de conta. Serve o
// "Vender agora" da pagina publica: quem nunca teve conta nao passa pelo login
// para so entao achar o link de cadastro.
export async function GET(request: NextRequest) {
  return beginAuthEntry(request, "register");
}
