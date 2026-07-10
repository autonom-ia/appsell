# ADR 0003 — Multi-tenancy e buyer multi-seller

- Status: Proposed
- Data: 2026-07-10
- Decisores: Rodrigo / Arquitetura / Banco / AppSec
- Issue: [autonom-ia/appsell#9](https://github.com/autonom-ia/appsell/issues/9)

## Contexto

Cada seller administra produtos, conteúdo e compradores do próprio tenant. Uma
mesma identidade pode comprar de vários sellers e também administrar um seller.
Esse modelo não pode depender de um único “tenant ativo” global nem de filtros
voluntários em cada query.

## Decisão proposta

Usar identidade global e dados seller-scoped com defesa em profundidade:

- `identity_user` é global e referencia `identity_sub` imutável;
- `seller_organization` define a fronteira do tenant;
- memberships administrativas ligam usuário + seller + papel;
- buyer não precisa de membership no seller;
- entitlement liga buyer + seller + produto e é a fonte do acesso comercial;
- enrollment liga buyer + seller + curso e mantém a origem do entitlement;
- tabelas privadas incluem `seller_organization_id NOT NULL`;
- FKs compostas repetem o seller nos dois lados e impedem referência cruzada;
- repositories exigem contexto explícito de actor e seller;
- PostgreSQL RLS reforça a política para conexões normais;
- testes negativos verificam API, repository e banco.

## Contextos de leitura

### Seller admin

Uma operação administrativa seleciona exatamente um seller permitido pela
membership. Trocar o seller ativo requer nova verificação server-side. IDs do
browser nunca substituem a membership.

### Buyer — Meus produtos

É uma leitura buyer-scoped, não tenant-scoped. O repository recebe o
`identity_user_id` da sessão e retorna somente entitlements desse buyer,
mantendo `seller_organization_id` em cada item. Não ativa bypass global de RLS.

Implementação aceitável:

- policy RLS específica que compara `buyer_identity_user_id` com contexto de
  sessão autenticado e limita a projeção;
- ou função/query dedicada `SECURITY INVOKER`, com tabelas/policies que preservem
  as mesmas garantias.

Implementação proibida: conexão `BYPASSRLS`, role de superusuário ou filtro de
buyer recebido do browser.

### Vitrine e “outros produtos”

A vitrine pública resolve um seller por slug e retorna apenas produtos daquele
seller em estado publicável. “Outros produtos deste produtor” usa o
`seller_organization_id` do produto/vitrine corrente. Não agrega sellers nem
infere recomendações a partir de PII do buyer.

## Papel duplo

Um usuário pode ser `seller_admin` de A e buyer de B/C. Cada request escolhe uma
capability explícita:

- rota administrativa exige membership do seller;
- rota buyer exige identidade igual ao dono do entitlement;
- papel administrativo não concede conteúdo comprado;
- entitlement não concede administração;
- superadmin não herda automaticamente acesso ao conteúdo privado.

Suporte excepcional usa fluxo de impersonation/support separado, temporário,
consentido quando aplicável e integralmente auditado.

## Chaves e constraints mínimas

- unique `identity_user(identity_sub)`;
- unique `seller_membership(seller_organization_id, identity_user_id)`;
- unique `catalog_product(seller_organization_id, slug)`;
- unique `course(seller_organization_id, id)` para suportar FK composta;
- FK composta de ProductCourse para produto e curso do mesmo seller;
- unique/idempotency de entitlement por regra comercial aprovada;
- FK composta de enrollment/progress para seller, buyer e conteúdo coerentes;
- soft delete/estado nunca remove a coluna de tenant;
- audit log registra seller e actor separadamente.

O detalhamento está no [modelo de dados](../DATA_MODEL.md).

## RLS e conexão

- a aplicação conecta com role sem `BYPASSRLS` e sem ownership das tabelas;
- cada transação define actor, capability e seller/buyer context por mecanismo
  não controlável pelo SQL do usuário;
- pool limpa contexto no fim da transação;
- ausência de contexto falha fechada;
- migrations usam role separada, nunca a role de runtime;
- jobs carregam contexto por item e não processam lote multi-tenant com filtro
  opcional;
- consultas de plataforma são APIs/repositories específicos, auditados.

## Alternativas rejeitadas

- schema ou banco por tenant no MVP: isolamento forte, mas custo operacional e
  migrations multiplicados sem necessidade comprovada.
- apenas `WHERE tenant_id = ?`: um único esquecimento vaza dados.
- membership buyer por seller: confunde compra com administração e aumenta
  grants sem benefício.
- superadmin com bypass permanente: amplia blast radius e dificulta auditoria.

## Validação obrigatória

1. Sellers A/B com IDs conhecidos e tentativas cruzadas em toda ação CRUD.
2. IDs válidos de B enviados sob sessão de A falham sem revelar existência.
3. Buyer X com entitlements em A+B enxerga ambos; buyer Y não enxerga nenhum.
4. Buyer X não usa entitlement A para curso/produto de B.
5. Papel seller em A não concede administração ou conteúdo em B.
6. Jobs, exportação, busca, métricas e storage preservam seller.
7. Conexão sem contexto e pool reutilizado falham fechados.
8. Testes de constraints executam SQL direto para provar defesa no banco.

## Consequências

O modelo repete `seller_organization_id` deliberadamente e aumenta a disciplina
de migrations. Esse custo compra verificabilidade: relações cross-tenant são
inválidas no banco, não apenas “improváveis” pela aplicação.
