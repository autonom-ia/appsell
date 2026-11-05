// Leitura e escrita de cookie sem regex: o cabeçalho é separado por ";" e
// cada par pelo primeiro "=".

export function valorDoCookie(cabecalho: string, nome: string): string | undefined {
  for (const pedaco of cabecalho.split(";")) {
    const igual = pedaco.indexOf("=");
    if (igual < 0) continue;
    if (pedaco.slice(0, igual).trim() === nome) return pedaco.slice(igual + 1).trim();
  }
  return undefined;
}

// Cookie do próprio domínio da loja, só em HTTPS, mandado na navegação de
// topo (Lax). Não é HttpOnly porque quem grava é o banner, depois do "Aceitar".
export function cookieDoNavegador(nome: string, valor: string, maxAgeSegundos: number): string {
  return `${nome}=${valor}; Max-Age=${maxAgeSegundos}; Path=/; Secure; SameSite=Lax`;
}

export function apagarCookie(nome: string): string {
  return cookieDoNavegador(nome, "", 0);
}
