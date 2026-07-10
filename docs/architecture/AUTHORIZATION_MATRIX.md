# Matriz de autorização

- Status: Proposed
- Regra: deny by default
- Fonte de identidade: `identity_sub` validado e sessão opaca do BFF
- Escopo tenant: `seller_organization_id` validado server-side

## Legenda

| Código | Significado |
|---|---|
| `A` | permitido diretamente, dentro do escopo indicado |
| `C` | permitido com condição adicional descrita |
| `N` | negado por padrão |
| `P` | projeção pública, sem autenticação e sem dado privado |

Papéis:

- `PSA`: platform_superadmin
- `PS`: platform_support
- `PA`: platform_auditor
- `SO`: seller_owner
- `SA`: seller_admin
- `SE`: seller_editor
- `SS`: seller_support
- `B`: buyer autenticado
- `V`: visitante

## Regras gerais

1. `SO/SA/SE/SS` só valem para o seller da membership ativa.
2. `B` só atua sobre a própria identidade e entitlements.
3. `PSA` não possui acesso automático ao conteúdo privado, PII integral ou
   downloads. Operação excepcional exige capability temporal separada.
4. `PS` não impersona, altera grants, publica conteúdo ou manipula entitlement.
5. `PA` é read-only sobre auditoria/metadados aprovados, sem conteúdo/segredo.
6. `comercial@autonomia.site` não aparece na avaliação normal; após bootstrap,
   somente o grant ligado ao `identity_sub` autoriza.
7. Toda ação `C` falha fechada quando a condição não puder ser provada.

## Fluxos pré-grant

Uma identidade autenticada sem grant não é `PSA`, `SO` ou `B`. Ela só pode
executar os fluxos abaixo, cada um com transação própria e sem criar privilégios
globais implicitamente:

| Ação | Ator pré-grant | Condição |
|---|---|---|
| Bootstrap inicial | identidade autenticada | e-mail verificado, issuer/audience/client corretos, MFA/passkey aprovado, one-shot e nenhum superadmin existente |
| Criar tenant próprio | identidade autenticada | onboarding SaaS aprovado; cria organização e membership `seller_owner` atomicamente, sem acesso a outro tenant |

Depois do onboarding, a capability passa a ser avaliada pela membership
persistida. `B` descreve somente uma identidade com entitlement; não é sinônimo
de qualquer usuário autenticado.

## Plataforma e tenants

| Ação | PSA | PS | PA | SO | SA | SE | SS | B | V | Condição/observação |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Conceder/revogar papel global | C | N | N | N | N | N | N | N | N | step-up, dupla aprovação e auditoria |
| Listar tenants operacionais | A | C | C | N | N | N | N | N | N | PS/PA recebem projeção mínima |
| Suspender/reativar tenant | C | N | N | N | N | N | N | N | N | motivo, step-up e política de grace period |
| Ler configuração do próprio tenant | N | N | N | A | A | C | C | N | N | SE/SS apenas campos necessários |
| Alterar branding/configuração | N | N | N | A | A | C | N | N | N | SE sem billing, memberships ou integrações secretas |
| Excluir tenant | C | N | N | C | N | N | N | N | N | fluxo assíncrono, retenção, export e janela de reversão |
| Ver audit log do tenant | C | C | C | A | C | N | N | N | N | SA sem eventos globais/secrets; filtros server-side |

## Memberships e compradores

| Ação | PSA | PS | PA | SO | SA | SE | SS | B | V | Condição/observação |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Convidar/remover seller admin/editor/support | N | N | N | A | C | N | N | N | N | SA não concede owner nem poder igual/superior proibido |
| Transferir ownership | N | N | N | C | N | N | N | N | N | step-up, aceite do novo owner e auditoria |
| Listar memberships do seller | N | N | C | A | A | N | C | N | N | PA apenas auditoria aprovada; SS projeção mínima |
| Listar buyers do seller | N | C | C | A | A | N | C | N | N | minimização; PS/PA somente caso operacional aprovado |
| Ver perfil completo de buyer | N | C | N | C | C | N | C | C | N | seller só campos necessários; B apenas próprio perfil |
| Importar buyers/entitlements | N | N | N | A | A | N | C | N | N | SS pode operar import aprovado, sem alterar regra comercial |
| Conceder entitlement manual | N | N | N | A | A | N | C | N | N | reason/source, produto do mesmo seller e auditoria |
| Revogar entitlement | N | N | N | A | A | N | C | N | N | política aprovada, reason e sem efeito em outro seller |
| Exportar buyers | N | N | C | C | C | N | N | C | N | finalidade, step-up, expiração; B exporta só os próprios dados |
| Impersonar buyer/seller | N | N | N | N | N | N | N | N | N | fora do MVP; exige ADR/capability temporal futura |

## Catálogo, ofertas e conteúdo

| Ação | PSA | PS | PA | SO | SA | SE | SS | B | V | Condição/observação |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Criar/editar produto draft | N | N | N | A | A | A | N | N | N | seller da membership |
| Criar/editar oferta | N | N | N | A | A | C | N | N | N | SE sem integrar cobrança/segredo; total nunca vem do client |
| Criar/editar curso/aula draft | N | N | N | A | A | A | N | N | N | optimistic locking |
| Upload de asset privado | N | N | N | A | A | A | N | N | N | allowlist/quarentena; seller da membership |
| Preview de draft | N | N | N | A | A | A | C | N | N | SS só via capability de suporte concedida pelo seller |
| Publicar/despublicar | N | N | N | A | A | C | N | N | N | SE se permissão explícita; snapshot validado |
| Arquivar produto/curso | N | N | N | A | A | C | N | N | N | política de acesso existente aplicada |
| Ler vitrine/produto publicado | P | P | P | P | P | P | P | P | P | somente projeção pública do seller selecionado |
| Ler “outros produtos” do seller | P | P | P | P | P | P | P | P | P | published do mesmo seller; sem marketplace global |
| Ler conteúdo privado publicado | N | N | N | C | C | C | C | C | N | seller staff quando necessário; buyer com entitlement próprio ativo |
| Baixar asset privado | N | N | N | C | C | C | C | C | N | revalida seller/entitlement e emite URL curta |

## Integrações, comércio e operação

| Ação | PSA | PS | PA | SO | SA | SE | SS | B | V | Condição/observação |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Configurar webhook/secret do seller | N | N | N | A | A | N | N | N | N | secret write-only, rotação e teste |
| Ver secret existente | N | N | N | N | N | N | N | N | N | nunca retornado; somente substituir/rotacionar |
| Mapear produto externo | N | N | N | A | A | N | C | N | N | mapping e produto do mesmo seller |
| Receber webhook | N | N | N | N | N | N | N | N | C | autenticação de máquina; endpoint resolve seller server-side |
| Reprocessar evento falho | C | N | N | C | C | N | C | N | N | efeito idempotente, reason e escopo do seller; PS/PA são read-only |
| Ver payload redigido/status de evento | C | C | C | A | A | N | C | N | N | nunca secret/token/PII desnecessária |
| Alterar purchase externo | N | N | N | N | N | N | N | N | N | fatos são imutáveis; correção por novo evento/ação auditada |
| Ver métricas operacionais do seller | C | C | C | A | A | C | C | N | N | sem dados de outro seller |
| Acessar Financial corporativo | C | N | C | N | N | N | N | N | N | server-to-server; sellers/buyers não acessam |
| Definir `operator_id` do Financial | N | N | N | N | N | N | N | N | N | AppSell nunca aceita do browser |
| Ver plano/status SaaS do próprio seller | C | N | C | A | C | N | N | N | N | projeção local minimizada; fonte de cobrança permanece server-to-server |
| Alterar assinatura SaaS do próprio seller | C | N | N | C | N | N | N | N | N | bloqueado até contrato Admin/Financial, preço server-side e gate de billing |
| Ativar billing real/deploy produção | N | N | N | N | N | N | N | N | N | exige gate operacional e aprovação explícita fora desta matriz |

## Buyer multi-seller

| Ação | Regra obrigatória |
|---|---|
| `GET /me/products` | filtra pelo `identity_user_id` da sessão; cada item mantém seller; não recebe buyer ID do client |
| selecionar produto A | contexto deriva do entitlement retornado e é revalidado no server |
| abrir produto B | exige entitlement B próprio; entitlement A nunca é reutilizado |
| ver vitrine do seller A | retorna apenas catálogo público A, mesmo que buyer também compre de B |
| progresso | chave inclui buyer + seller + enrollment/lesson coerentes; retry idempotente |
| papel seller + buyer | capability da rota decide; membership não substitui entitlement e vice-versa |

## Enforcement

- BFF autentica sessão e CSRF/origin.
- API autoriza ação e objeto usando grants da fonte local.
- Repository exige contexto tipado, não parâmetros opcionais.
- PostgreSQL reforça seller/buyer com RLS e constraints.
- Storage só recebe grants emitidos após autorização.
- Worker revalida tenant/estado e usa efeitos idempotentes.
- Testes negativos são obrigatórios em cada camada; sucesso apenas na UI não
  certifica autorização.
