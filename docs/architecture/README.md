# Arquitetura — Autonom.ia Sell

- Status do pacote: Approved direction with gates
- Issue: [autonom-ia/appsell#9](https://github.com/autonom-ia/appsell/issues/9)
- Data-base: 2026-07-10
- Gate: direção aprovada; cada slice funcional continua sujeito a Issue, review,
  testes reais e aprovação de merge

## Objetivo

Converter os planos executivo e técnico em decisões verificáveis para um web app
mobile-first, multi-tenant e seguro. Este pacote não autoriza merge, deploy,
alterações em produção, VPS, DNS, secrets, billing real ou dados de clientes.

## Decisões arquiteturais

| ADR | Decisão | Status |
|---|---|---|
| [0001](adr/0001-stack-and-runtime.md) | Monorepo, Web/BFF, API, dados e runtime | Accepted with gates |
| [0002](adr/0002-identity-bff-rbac.md) | Identity, sessão opaca, BFF e RBAC | Accepted with gates |
| [0003](adr/0003-multitenancy-and-buyer-multiseller.md) | Isolamento por seller e comprador multi-seller | Accepted with gates |
| [0004](adr/0004-product-content-storage-publication.md) | Produto, conteúdo, storage e publicação | Accepted with gates |
| [0005](adr/0005-jobs-webhooks-financial-boundary.md) | Jobs, webhooks e fronteira com Financial | Accepted with gates |

## Artefatos de segurança e operação

- [Threat model](THREAT_MODEL.md)
- [Matriz de autorização](AUTHORIZATION_MATRIX.md)
- [Modelo de dados e ERD](DATA_MODEL.md)
- [Plano de migração e rollback](MIGRATION_AND_ROLLBACK.md)
- [Auditoria de dependências e licenças](DEPENDENCY_AND_LICENSE_AUDIT.md)
- [Registro de decisões pendentes](DECISION_REGISTER.md)

## Regras não negociáveis

1. A experiência é mobile-first; desktop não pode ser a única superfície
   certificada.
2. O browser recebe somente uma sessão opaca em cookie `HttpOnly`; access,
   refresh e ID tokens nunca são expostos ao JavaScript da aplicação.
3. `comercial@autonomia.site` serve apenas para localizar o bootstrap one-shot.
   E-mail não verificado, issuer/audience incorretos ou ausência de autenticação
   forte não concedem superadmin.
4. Autorização permanente usa o `sub` imutável do Identity e grants persistidos,
   nunca comparação dinâmica de e-mail.
5. Seller owner/admin/editor/support são memberships dentro de um tenant. Buyer é
   identidade global, sem papel administrativo implícito.
6. Toda entidade seller-scoped carrega `seller_organization_id`; associações
   usam chaves compostas para impedir referências entre tenants.
7. Um buyer pode ter entitlements em sellers diferentes. “Outros produtos” é
   sempre a vitrine pública do seller selecionado, não um marketplace global.
8. Financial corporativo cobra o SaaS do AppSell. Seller commerce permanece no
   AppSell via checkout externo e webhook até o Financial ser endurecido e
   certificado por operador.
9. Nenhuma conexão comum de aplicação possui bypass permanente de RLS.
10. Nenhum artefato deste pacote toca produção, VPS ou secrets.

## Gate de aprovação

Antes de habilitar Identity real, tenancy, billing, dados reais ou produção,
devem estar resolvidos ou formalmente aceitos:

- decisões marcadas como bloqueadoras no [registro](DECISION_REGISTER.md);
- spikes reais de OAuth/BFF, isolamento A/B, buyer A+B, publicação, asset
  privado e webhook idempotente;
- matriz de autorização aprovada por produto e segurança;
- threat model sem risco crítico sem tratamento aceito;
- auditoria completa de licenças e SBOM sobre os lockfiles atuais e futuros;
- plano de migração/rollback executado em ambiente descartável.

## Relação com os planos

Este pacote detalha, sem substituir:

- [Plano Executivo](../plans/EXECUTIVE_PLAN.md)
- [Plano Técnico](../plans/TECHNICAL_PLAN.md)
- [Relatório de Verificação](../research/VERIFICATION_REPORT.md)
