// Período do relatório: datas AAAA-MM-DD, início <= fim, até 366 dias. A API
// e o banco conferem de novo; aqui é para não gastar uma ida à API à toa.
export const PERIODO_MAXIMO_DIAS = 366;
const TAMANHO_DATA = 10;
const DIA_MS = 86_400_000;

function dataIso(valor: string | null): number | null {
  if (valor === null || valor.length !== TAMANHO_DATA) return null;
  const [ano, mes, dia] = valor.split("-").map(Number);
  if (!Number.isInteger(ano) || !Number.isInteger(mes) || !Number.isInteger(dia)) return null;
  const instante = Date.UTC(ano!, mes! - 1, dia!);
  return new Date(instante).toISOString().slice(0, TAMANHO_DATA) === valor ? instante : null;
}

export function periodoValido(de: string | null, ate: string | null): { from: string; to: string } | null {
  const inicio = dataIso(de);
  const fim = dataIso(ate);
  if (inicio === null || fim === null || fim < inicio) return null;
  if ((fim - inicio) / DIA_MS > PERIODO_MAXIMO_DIAS) return null;
  return { from: de!, to: ate! };
}
