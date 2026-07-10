# DECISIONS

Resumo operacional das decisões ativas. O registro canônico, alternativas,
riscos e gates está em
[DECISION_REGISTER.md](architecture/DECISION_REGISTER.md) e nos ADRs.

## Decisão 001 — direção arquitetural aceita com gates

- Data: 2026-07-10
- Responsável: Rodrigo / Arquitetura / Segurança / Operações
- Decisão: aceitar a direção dos ADRs 0001–0005; cada slice continua sujeito a
  Issue, testes reais, review independente e aprovação explícita de merge.
- Limite: não autoriza produção, VPS, DNS, secrets, billing ou dados de clientes.

## Decisão 002 — sessão opaca no BFF

- Data: 2026-07-10
- Decisão: browser recebe somente cookie `HttpOnly` opaco; tokens OAuth não são
  expostos ao JavaScript. As rotas runtime do slice Local/CI usam Redis sem
  fallback em memória; implementações in-memory permanecem apenas em testes.
- Estado: verificado localmente nos PRs #11 e #15; Identity real segue bloqueado
  na Issue #13.

## Decisão 003 — bootstrap do superadmin

- Data: 2026-07-10
- Decisão: `comercial@autonomia.site` é apenas seletor do bootstrap one-shot.
  E-mail verificado, issuer/audience corretos, autenticação forte e grant
  persistido pelo `sub` imutável são gates obrigatórios.
- Limite: o bootstrap real ainda não foi implementado.

## Decisão 004 — separação multi-tenant e comprador multi-seller

- Data: 2026-07-10
- Decisão: seller admin opera somente seu tenant; buyer é identidade global e
  pode manter entitlements em sellers diferentes sem papel administrativo.
- Limite: RBAC, RLS, entidades de domínio e testes A/B ainda não existem.

## Decisão 005 — fronteira financeira do MVP

- Data: 2026-07-10
- Decisão: Financial corporativo cobra o SaaS do produtor; seller commerce usa
  checkout externo e webhook idempotente no MVP. Admin não será exposto como
  backoffice direto do seller.
- Limite: integrações reais ainda não foram implementadas nem certificadas.

## Decisão 006 — produção permanece proibida

- Data: 2026-07-10
- Decisão: `sell.autonomia.site` é o domínio-alvo, não evidência de ambiente
  publicado. Nenhuma ação na VPS ou produção faz parte dos slices atuais.
- Próximo gate: staging isolado, TLS, secrets, observabilidade, restore/rollback,
  testes reais e aprovação explícita de Rodrigo.

## Decisão 007 — preview protegido no Proxy, não no RSC

- Data: 2026-07-10
- Decisão: o slice visual usa `proxy.ts` para validar a sessão antes de servir
  `/dashboard`, `/products` e `/library`. Server Components não leem cookie nem
  objeto de sessão, porque a prova adversarial mostrou que valores aguardados
  no RSC dev podiam aparecer no Flight inline.
- Limite: o sujeito `dev-user-001` libera apenas uma prévia demonstrativa e
  fail-closed fora de desenvolvimento; não representa grant seller/buyer.
- Próximo gate: substituir a regra demo por RBAC/tenant context persistido e
  testes cross-tenant na Fase 3.
