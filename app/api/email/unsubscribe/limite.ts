// Limite de taxa do descadastro, por IP, na memória deste processo. Não é
// barreira distribuída: com várias réplicas, cada uma conta a sua. Basta para
// o que importa aqui, que é não deixar um robô varrer tokens no ritmo da rede;
// o token tem 256 bits, então adivinhar não é o risco, e sim o custo na API.
const JANELA_MS = 60_000;
const MAXIMO_POR_JANELA = 10;
const MAXIMO_DE_CHAVES = 10_000;

const contagens = new Map<string, { inicio: number; total: number }>();

export function dentroDoLimite(chave: string, agora: number = Date.now()): boolean {
  const atual = contagens.get(chave);
  if (!atual || agora - atual.inicio >= JANELA_MS) {
    if (contagens.size >= MAXIMO_DE_CHAVES) contagens.clear();
    contagens.set(chave, { inicio: agora, total: 1 });
    return true;
  }
  if (atual.total >= MAXIMO_POR_JANELA) return false;
  contagens.set(chave, { inicio: atual.inicio, total: atual.total + 1 });
  return true;
}

export function limparLimiteParaTeste(): void {
  contagens.clear();
}
