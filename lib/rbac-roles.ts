// Os papéis que o banco aceita, transcritos aqui porque o BFF precisa recusar
// o que não reconhece em vez de repassar adiante. A autoridade continua sendo
// a constraint da tabela; esta lista é o filtro deny-by-default da borda.
//
// Fonte: seller_membership.role e platform_grant.role
// (apps/api/migrations/versions/0001_domain_foundation.py).
export const SELLER_ROLES = new Set([
  "seller_owner",
  "seller_admin",
  "seller_editor",
  "seller_support"
]);

export const PLATFORM_ROLES = new Set([
  "platform_superadmin",
  "platform_support",
  "platform_auditor"
]);
