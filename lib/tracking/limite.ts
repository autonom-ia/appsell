// Limite de taxa por chave (IP), na memória deste processo. Não é barreira
// distribuída: com várias réplicas, cada uma conta a sua. Serve para um robô
// não inflar a contagem de consentimento no ritmo da rede.
//
// Teto de chaves: passando dele, sai a chave mais antiga (o Map guarda a
// ordem de inserção, e cada janela nova reinsere a chave no fim). Zerar o mapa
// inteiro deixaria quem enche o mapa com endereços novos zerar o limite dos
// outros.
const MAXIMO_DE_CHAVES = 10_000;

export interface Limite {
  dentro(chave: string, agora?: number): boolean;
  limparParaTeste(): void;
  tamanhoParaTeste(): number;
}

export function criarLimite(
  maximoPorJanela: number,
  janelaMs: number,
  maximoDeChaves: number = MAXIMO_DE_CHAVES
): Limite {
  const contagens = new Map<string, { inicio: number; total: number }>();
  return {
    dentro(chave, agora = Date.now()) {
      const atual = contagens.get(chave);
      if (!atual || agora - atual.inicio >= janelaMs) {
        contagens.delete(chave);
        while (contagens.size >= maximoDeChaves) {
          const maisAntiga = contagens.keys().next().value;
          if (maisAntiga === undefined) break;
          contagens.delete(maisAntiga);
        }
        contagens.set(chave, { inicio: agora, total: 1 });
        return true;
      }
      if (atual.total >= maximoPorJanela) return false;
      contagens.set(chave, { inicio: atual.inicio, total: atual.total + 1 });
      return true;
    },
    limparParaTeste() {
      contagens.clear();
    },
    tamanhoParaTeste() {
      return contagens.size;
    }
  };
}
