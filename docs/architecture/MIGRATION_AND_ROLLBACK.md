# Plano de migração, release e rollback

- Status: Proposed
- Escopo atual: dev/staging isolados e bancos descartáveis
- Produção/VPS: explicitamente fora do escopo desta Issue

## 1. Princípios

1. Migrations são forward-only e seguem expand/contract.
2. Aplicação, API, schema, outbox e workers mantêm compatibilidade N/N-1 na
   janela de rollout.
3. Rollback de aplicação usa o digest anterior; não depende de `down` destrutivo.
4. Correção de dados usa migration compensatória revisada e auditada.
5. Backup não é rollback de rotina; restore acontece em banco novo antes de
   qualquer cutover.
6. Runtime, migration e backup usam roles distintas e least privilege.
7. Nenhuma migration de produção é autorizada por este documento.

## 2. Ambientes e isolamento

Cada ambiente possui, sem compartilhamento:

- PostgreSQL/database/role;
- Redis namespace ou instância e credencial;
- bucket/prefixo e chaves;
- OAuth client/callback;
- secrets e webhooks;
- filas/dead letters;
- telemetria e dashboards.

Dev/CI usa recursos efêmeros. Staging usa dados sintéticos, anonimizados ou
explicitamente autorizados. Nenhum dump de cliente entra em dev.

## 3. Convenção de migrations

- ID monotônico/ordenável e nome descritivo;
- checksum armazenado; migration aplicada não é editada;
- transaction quando o PostgreSQL permitir;
- locks e timeout explícitos;
- índices grandes com estratégia non-blocking quando necessário;
- backfill separado, retomável, medido e idempotente;
- constraint nova entra `NOT VALID`/validação posterior quando o volume exigir;
- coluna nova começa nullable/default compatível; `NOT NULL` só após backfill e
  validação;
- remoção/rename só na fase contract, depois de telemetria provar zero uso;
- policy RLS e grants fazem parte da mesma mudança revisável.

## 4. Pipeline por mudança

### Antes da PR

1. Definir invariante, risco, owner e rollback.
2. Classificar mudança: aditiva, backfill, constraint, destrutiva ou dados.
3. Confirmar compatibilidade N/N-1 de API, worker e eventos.
4. Gerar/atualizar ERD e teste negativo de tenant.

### CI

1. Aplicar todas as migrations em banco vazio.
2. Criar snapshot N-1, semear fixtures A/B e buyer X/Y.
3. Atualizar N-1 → N.
4. Rodar constraints, RLS e testes cross-tenant.
5. Iniciar aplicação N e worker N.
6. Persistir eventos/outbox N; executar worker N-1 quando compatível.
7. Reverter aplicação para N-1 sem reverter schema.
8. Rodar golden paths e verificar logs/locks/tempo.
9. Gerar evidência com SHA, migration IDs e versões das imagens.

### Staging

1. Backup/snapshot pré-release.
2. Dry-run/estimativa de locks e tempo em volume representativo.
3. Aplicar expansão com observabilidade ativa.
4. Subir código compatível e executar smoke.
5. Executar backfill em lotes com pause/resume.
6. Validar constraints e invariantes.
7. Somente em release posterior, contrair legado.

## 5. Ordem de criação inicial

1. extensões aprovadas e roles;
2. identity user, session references e grants;
3. seller organization e memberships;
4. catálogo/conteúdo/assets/publication;
5. integrations/webhooks/purchases;
6. entitlements/enrollments/progress;
7. outbox e audit;
8. índices, views públicas, RLS/policies e grants;
9. fixtures sintéticas A/B e buyer X/Y em dev/test.

Nenhuma tabela seller-scoped é liberada antes de tenant `NOT NULL`, FK composta e
policy correspondente.

## 6. Estratégias por mudança

### Adicionar coluna obrigatória

1. expand: coluna nullable sem default volátil;
2. código N escreve coluna antiga e nova;
3. backfill idempotente em lotes;
4. monitorar nulos/divergência;
5. validar constraint;
6. contract em release posterior: `NOT NULL` e remoção da escrita antiga.

### Renomear coluna/tabela

Adicionar novo nome, dual-write, backfill, dual-read temporário, trocar leitura,
provar ausência de uso antigo e remover somente depois da janela N/N-1. Rename
direto é proibido quando código/worker anterior ainda pode executar.

### Alterar enum/estado

Leitores antigos devem tolerar valor novo antes de qualquer writer produzi-lo.
Remoção de valor exige migrar dados e provar que nenhum evento N-1 o recria.

### Alterar evento/outbox

Adicionar `schema_version`; consumer aceita N e N-1; producer só muda depois do
deploy do consumer compatível. Evento persistido é imutável; transformação usa
adapter versionado.

### Alterar chave/tenant

Executar auditoria de órfãos/cross-tenant, adicionar unique/FK composta, validar,
trocar repositories e somente então remover FK simples. Qualquer divergência
interrompe a migration e vira incidente de dados.

## 7. Plano de rollback por camada

| Camada | Trigger | Ação segura | Não fazer |
|---|---|---|---|
| Web/BFF | erro/login/regressão | retornar digest N-1; manter schema compatível | invalidar todas as sessões sem necessidade |
| API | erro/autorização | bloquear mutações afetadas e voltar N-1 | remover coluna/constraint para “fazer funcionar” |
| Worker | duplicidade/falha | pausar fila, manter outbox, voltar N-1 compatível | apagar jobs/dead letters |
| Migration expand | lock/erro | cancelar se transacional; manter adições inertes | executar `down` destrutivo |
| Backfill | carga/divergência | pausar checkpoint, corrigir código e retomar | reiniciar sem idempotência |
| Evento novo | consumer falha | pausar producer, usar adapter/worker compatível | editar payload persistido |
| Storage | falha/corrupção | bloquear publish, manter asset pending, restaurar versão | marcar asset ready manualmente |
| Webhook | efeito errado | desabilitar endpoint/processor, preservar raw/evento | apagar evento ou conceder/revogar em massa |

## 8. Rollback de dados

Quando código N gravou dados válidos que N-1 não entende:

1. pausar writers/jobs afetados;
2. preservar backup e evidência;
3. classificar linhas por seller e versão;
4. aplicar adapter ou migration compensatória em banco clonado;
5. validar contagens, constraints, hashes e jornadas;
6. aprovar ação antes de qualquer ambiente persistente;
7. registrar audit/postmortem.

Restaurar snapshot inteiro só é aceitável com cálculo de perda, replay de eventos
e aprovação explícita. Nunca restaurar sobre o banco original sem validar em um
banco novo.

## 9. Backup e restore

Metas propostas, ainda sujeitas a custo/aprovação:

- RPO 15 minutos;
- RTO 2 horas;
- PITR/WAL ou equivalente;
- dump diário criptografado e off-host;
- storage versionado;
- restore mensal real.

Exercício mínimo:

1. provisionar banco descartável isolado;
2. restaurar base + WAL até ponto escolhido;
3. validar migration ledger/checksums;
4. validar sellers A/B, buyer A+B, entitlements, publications, outbox e audit;
5. conferir que secrets/tokens não foram materializados indevidamente;
6. subir mesma imagem por digest e executar golden paths;
7. medir RPO/RTO e guardar evidência;
8. destruir somente o recurso descartável, preservando relatório.

## 10. Rollback de sessão e OAuth

- callbacks de N e N-1 coexistem na janela aprovada;
- formato de sessão/vault é versionado;
- N-1 consegue rejeitar sessão nova de forma segura ou há adapter/migração;
- rotação de cookie/session ID não expõe token;
- rollback não reabre callback amplo nem desabilita PKCE/CSRF;
- incidente de chave exige revogação, rotação e reautenticação planejadas.

## 11. Gate e evidência

Uma migration só fica pronta para aprovação quando há:

- diff SQL e impacto descritos;
- testes vazio e N-1→N verdes;
- testes cross-tenant e RLS verdes;
- tempo/lock medidos em volume representativo;
- plano de backfill/abort;
- compatibilidade de API/worker/eventos;
- backup/restore ou justificativa proporcional;
- reviewer de banco e tenancy independente;
- SHA/digest e comandos/resultados anexados à PR.

Merge, migration persistente e produção continuam dependendo dos gates do Agent
Harness e de aprovação explícita do Rodrigo.
