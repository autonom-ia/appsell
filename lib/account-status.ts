// Contratos da Minha conta (#351), do lado de quem consome a API de domínio.
// Cada parser recusa forma inesperada em vez de deixar passar: a resposta da
// API é confiável, mas a tela não pode virar espelho de um campo a mais.

export interface AccountStatus {
  readonly deletionRequestedAt: string | null;
}

export interface AccountDeletionReceipt {
  readonly requestedAt: string;
  readonly alreadyRequested: boolean;
}

export function parseAccountStatusPayload(value: unknown): AccountStatus | null {
  if (!hasExactKeys(value, ["deletion_requested_at"])) return null;
  const requested = value.deletion_requested_at;
  if (requested === null) return { deletionRequestedAt: null };
  return isInstant(requested) ? { deletionRequestedAt: requested } : null;
}

export function parseAccountDeletionPayload(value: unknown): AccountDeletionReceipt | null {
  if (!hasExactKeys(value, ["requested_at", "already_requested"])) return null;
  if (!isInstant(value.requested_at) || typeof value.already_requested !== "boolean") return null;
  return { requestedAt: value.requested_at, alreadyRequested: value.already_requested };
}

// A exportação é o que o banco montou; aqui só se confere que é o documento
// esperado, não um erro ou um corpo vazio disfarçado de sucesso.
export function isAccountExportPayload(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as Record<string, unknown>;
  return record.schema_version === 1 &&
    typeof record.identity === "object" && record.identity !== null &&
    typeof record.exported_at === "string";
}

export function accountExportFilename(now: Date) {
  const day = now.toISOString().slice(0, 10);
  return `meus-dados-autonomia-sell-${day}.json`;
}

function isInstant(value: unknown): value is string {
  return typeof value === "string" && value.length <= 40 && !Number.isNaN(Date.parse(value));
}

function hasExactKeys<T extends string>(
  value: unknown,
  keys: readonly T[]
): value is Record<T, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}
