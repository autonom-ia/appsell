# TROUBLESHOOTING

## Problema: criação de produto retorna 403 após atualização

### Sintoma

Uma sessão local já aberta lista tenants e produtos, mas o primeiro POST de
produto retorna `forbidden_mutation`.

### Causa provável

A sessão foi criada antes do cookie `appsell_product_csrf` existir. O cookie é
derivado da sessão e da ação no callback; não há fallback genérico.

### Diagnóstico read-only

Confirme que a sessão é anterior à Fase 4A e que o browser não possui o cookie
legível `appsell_product_csrf`. Nunca registre o valor do cookie.

### Correção

Saia e entre novamente no ambiente. Isso cria uma nova sessão e os cookies CSRF
específicos sem expor a sessão opaca ao JavaScript.

### Validação

Selecione explicitamente o tenant, crie um rascunho sintético e confirme resposta
201 seguida da listagem persistida.

### Rollback / mitigação

O comportamento é fail-closed. Não relaxe origem ou CSRF; mantenha a orientação
de novo login até existir uma estratégia versionada de renovação de cookies.

## Problema: edição ou publicação retorna 403 após atualização

Sessões locais criadas antes da Fase 4B não possuem os cookies legíveis
`appsell_product_update_csrf` e `appsell_product_publish_csrf`. Saia e entre
novamente; não copie, registre ou reutilize os valores. Os tokens são separados
por ação de propósito. Não relaxe Origin/CSRF para preservar sessões antigas.

## Problema: publicação retorna 409

- `draft_version_conflict`: use **Recarregar versão do servidor**; a UI preserva
  os campos locais e permite novo envio com as versões atuais.
- `no_unpublished_changes`: o snapshot vigente já representa o draft atual;
  edite e salve antes de publicar uma nova versão.
- `slug_conflict`: escolha outro endereço para o produto.

Timeout/503 preserva a chave idempotente para retry do mesmo corpo. Uma nova
edição cria uma nova intenção; não force novas chaves manualmente.
