// Limite de taxa por chave (IP), na memória deste processo. Não é barreira
// distribuída: com várias réplicas, cada uma conta a sua. Serve para um robô
// não inflar a contagem de consentimento no ritmo da rede.
const MAXIMO_DE_CHAVES = 10_000;

export interface Limite {
  dentro(chave: string, agora?: number): boolean;
  limparParaTeste(): void;
}

export function criarLimite(maximoPorJanela: number, janelaMs: number): Limite {
  const contagens = new Map<string, { inicio: number; total: number }>();
  return {
    dentro(chave, agora = Date.now()) {
      const atual = contagens.get(chave);
      if (!atual || agora - atual.inicio >= janelaMs) {
        if (contagens.size >= MAXIMO_DE_CHAVES) contagens.clear();
        contagens.set(chave, { inicio: agora, total: 1 });
        return true;
      }
      if (atual.total >= maximoPorJanela) return false;
      contagens.set(chave, { inicio: atual.inicio, total: atual.total + 1 });
      return true;
    },
    limparParaTeste() {
      contagens.clear();
    }
  };
}
