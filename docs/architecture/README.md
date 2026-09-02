# Arquitetura — Autonom.ia Sell

- Status do pacote: direção aprovada; catálogo multitenant real verificado em produção
- Issue: [autonom-ia/appsell#9](https://github.com/autonom-ia/appsell/issues/9)
- Data-base: 2026-07-10; estado operacional atualizado em 2026-08-24
- Gate: direção aprovada; cada slice funcional continua sujeito a Issue, review,
  testes reais e aprovação de merge

> Para a fotografia atual de produção, use primeiro
> [PRODUCTION_CURRENT.md](../PRODUCTION_CURRENT.md). Este pacote reúne
> arquitetura, ADRs e regras duráveis; textos que dizem “futuro” ou “Local/CI”
> descrevem limites de um slice específico, não negam o release real atual.

## Objetivo

Converter os planos executivo e técnico em decisões verificáveis para um web app
mobile-first, multi-tenant e seguro. Este pacote não autoriza uma nova operação
de merge, deploy, alteração em produção, VPS, DNS, secrets, billing real ou
dados de clientes sem o gate correspondente.

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
- [Política de e-mail: remetentes, natureza e descadastro](EMAIL_POLICY.md)

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
10. Nenhum artefato deve alterar produção, VPS ou secrets sem o fluxo Issue →
    PR → review → aprovação → deploy e rollback. A existência da produção atual
    não transforma este documento em autorização para nova operação.

## Gate de aprovação

Antes de ampliar a operação, alterar billing, dados reais ou produção, devem
estar resolvidos ou formalmente aceitos:

- decisões marcadas como bloqueadoras no [registro](DECISION_REGISTER.md);
- spikes reais de OAuth/BFF, isolamento A/B, buyer A+B, publicação, asset
  privado e webhook idempotente;
- matriz de autorização aprovada por produto e segurança;
- threat model sem risco crítico sem tratamento aceito;
- auditoria completa de licenças e SBOM sobre os lockfiles atuais e futuros;
- plano de migração/rollback executado em ambiente descartável;
- smoke no SHA efetivamente implantado, quando a mudança tocar produção.

## Relação com os planos

Este pacote detalha, sem substituir:

- [Plano Executivo](../plans/EXECUTIVE_PLAN.md)
- [Plano Técnico](../plans/TECHNICAL_PLAN.md)
- [Relatório de Verificação](../research/VERIFICATION_REPORT.md)
- [Estado atual de produção](../PRODUCTION_CURRENT.md)
- [Runbook de release/rollback](../runbooks/PRODUCTION_RELEASE_APPSSELL.md)
