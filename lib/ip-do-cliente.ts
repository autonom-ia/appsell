// O endereço de quem fez o pedido, como a borda o viu.
//
// Na produção, a borda é a única coisa na frente da web: ela acrescenta ao fim
// do `X-Forwarded-For` o endereço da conexão que recebeu, e o Next só preenche
// o cabeçalho quando ele chega vazio. O que vem antes do último item foi
// escrito por quem mandou o pedido, e não é endereço de ninguém. Por isso vale
// o último item, e nunca o primeiro.
//
// Sem o cabeçalho (só fora da produção: teste, `next dev`), o endereço é o
// marcador fixo. Nenhum outro cabeçalho serve de reserva: um limite de taxa não
// pode cair num valor que o próprio pedido escolhe.
//
// Se um dia houver outro proxy entre a borda e a web, o último item passa a ser
// o dele, e o limite de taxa junta todo mundo. Quem mudar a frente da web
// revisa esta função.
export const IP_DESCONHECIDO = "desconhecido";

type ComCabecalhos = { headers: { get(nome: string): string | null } };

export function ipDoCliente(pedido: ComCabecalhos): string {
  const encaminhado = pedido.headers.get("x-forwarded-for");
  if (encaminhado !== null) {
    const itens = encaminhado.split(",").map((item) => item.trim()).filter(Boolean);
    const ultimo = itens.at(-1);
    if (ultimo) return ultimo;
  }
  return IP_DESCONHECIDO;
}
