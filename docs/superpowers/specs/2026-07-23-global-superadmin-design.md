# Superadmin global real — desenho cross-repo

Data: 2026-07-23
Issues relacionadas: AppSell #93 e #105
Classe de modelo: strong + human
Ambientes: Local/CI primeiro; produção somente após aprovação explícita do Rodrigo

## Estado de implementação em 2026-07-23 14:25 BRT

Este documento continua sendo o PRD arquitetural. O estado operacional e os
comandos de retomada estão em
`docs/handoffs/2026-07-23-claude-superadmin.md`.

| Wave | Estado | PR |
|---|---|---|
| Desenho AppSell | draft, CI verde | AppSell #134 |
| Identity Foundation | implementada, draft, `validate` verde | Foundation #49 |
| Identity Auth UI | implementada, draft, dois checks verdes | Identity Auth #41 |
| Admin bootstrap explícito | não implementado | depende da reconciliação #12/#13/#15 |
| Admin → AppSell | não implementado | sem PR |
| Projeção AppSell | não implementada | sem PR |
| Projeção Financial | stack draft antiga | Financial #21/#23 |
| Ativação em produção | não iniciada | requer novo 🟢 |

`comercial@autonomia.site` ainda não é superadmin. Nenhum merge, deploy,
secret, banco ou grant de produção foi executado pelas Waves novas.

## Objetivo

Transformar `comercial@autonomia.site` no primeiro `platform_superadmin` real da
Autonom.ia, usando autenticação por passkey comprovada pelo servidor, com o Admin
como fonte única da autorização e projeções auditáveis para AppSell e Financial.

O resultado esperado é:

- a conta faz login pelo Autonom.ia Identity;
- conclui um step-up por passkey;
- o Admin executa um bootstrap one-shot e persiste `platform_superadmin`;
- a mesma transação produz snapshots de autorização para AppSell e Financial;
- o AppSell exibe uma visão real da plataforma, sem persona ou subject demo;
- `financial.admin` chega ao Financial pela projeção oficial;
- repetição, replay, subject divergente e login sem passkey falham sem escrita.

Este desenho não autoriza merge, deploy, secrets, banco ou concessão em produção.

## Estado encontrado

### Identity

O login Cognito e o OAuth de `comercial@autonomia.site` estão funcionando. O
fluxo WebAuthn atual, porém, acontece diretamente entre o browser e o Cognito.
Nenhum backend observa a conclusão do desafio e, portanto, não existe hoje uma
prova de passkey que outro serviço possa verificar.

### Admin

A PR `autonomia-admin#12` implementa o role `platform_superadmin`, contendo
permissões administrativas globais e `financial.admin`. O bootstrap está
acoplado ao primeiro `GET /admin/me`, usa subject e email verificado, mas não
exige uma prova verificável de passkey. A PR permanece draft e o deploy de
produção do Admin está bloqueado.

As permissões globais já seguem a relação correta:

```text
admin.user_roles -> admin.roles
```

A PR `autonomia-admin#15` já implementa a outbox transacional de acesso
financeiro. O bootstrap e alterações de role produzem snapshot sem PII para o
Financial.

### Financial

A branch `feat/20-admin-access-projection` já contém consumidor SQS e projeção
ordenada em `financial.admin_access_subjects`. O desenho trata duplicidade e
eventos fora de ordem por revisão monotônica. Essa projeção deve continuar sendo
a única forma de conceder `financial.admin`.

### AppSell

O callback real redireciona identidades normais para `/dashboard`. A rota
`/platform/demo-inspection` depende do subject sintético `demo-superadmin`.

O Domain API possui um bootstrap local de `platform_superadmin`, mas recebe
`assurance` como texto assinado por HMAC. Isso não comprova qual fator foi
utilizado no Identity e cria uma segunda fonte de autorização independente do
Admin.

## Alternativas avaliadas

### A. Admin como fonte única, com projeções — recomendada

O Identity emite prova one-shot de passkey. O Admin valida essa prova, persiste o
role e publica snapshots transacionais para AppSell e Financial.

Vantagens:

- uma única decisão de autorização;
- revogação e auditoria centralizadas;
- serviços consumidores continuam disponíveis mesmo com Admin temporariamente
  fora do ar;
- aproveita as outboxes e projeções já construídas;
- elimina INSERT manual e comparação permanente por email.

Custo:

- exige um pequeno protocolo de step-up no Identity;
- exige uma nova projeção Admin -> AppSell;
- envolve PRs coordenadas em quatro repositórios.

### B. Bootstrap independente no Admin e no AppSell — rejeitada

Cada produto persistiria seu próprio `platform_superadmin`.

Foi rejeitada porque pode gerar uma conta superadmin no AppSell sem
`financial.admin`, ou o inverso. Revogação, troca de titular e auditoria ficariam
divergentes.

### C. Colocar o role diretamente no token Identity — rejeitada

O Identity emitiria `platform_superadmin` como claim.

Foi rejeitada porque o Identity não deve virar a fonte de regras de negócio e
porque tokens já emitidos mantêm autorização obsoleta até expirar. O token prova
identidade e fator; o Admin decide autorização.

## Arquitetura aprovada

```text
Passkey no auth.autonomia.site
          |
          v
Identity step-up one-shot
          |
          v
Admin POST /admin/platform/bootstrap
          |
          +--> admin.user_roles -> platform_superadmin
          |
          +--> outbox Financial -> SQS -> financial.admin_access_subjects
          |
          +--> outbox AppSell   -> SQS -> platform_admin_access_subjects
                                      |
                                      v
                              AppSell /me e /platform
```

## 1. Prova verificável de passkey no Identity

### Princípio

O browser não declara `assurance`. Um backend do Identity precisa observar o
`RespondToAuthChallenge` bem-sucedido para `WEB_AUTHN` e só então emitir uma
prova.

`ClientMetadata`, query string, local storage e campo enviado pelo frontend não
são fontes de verdade.

### Fluxo

1. O AppSell BFF cria `state`, `nonce` e PKCE e redireciona para a tela de
   step-up em `auth.autonomia.site`.
2. O backend do Identity inicia `USER_AUTH` preferindo `WEB_AUTHN`, guarda a
   sessão Cognito vinculada a uma transação curta e devolve somente os dados
   públicos do desafio.
3. O browser executa a cerimônia WebAuthn no domínio correto.
4. O browser envia credencial e `transaction_id` ao backend do Identity.
5. O backend chama `RespondToAuthChallenge`.
6. Após receber tokens válidos, o backend verifica issuer, audience, client,
   subject e email verificado.
7. O Identity cria um authorization code de uso único, com TTL máximo de cinco
   minutos, vinculado ao redirect URI e ao PKCE.
8. O AppSell BFF troca o code server-to-server e recebe uma attestation JWS.

### Claims mínimos da attestation

```json
{
  "iss": "https://auth.api-autonomia.com",
  "aud": "autonomia-admin",
  "sub": "cognito-subject-uuid",
  "client_id": "appsell-web",
  "amr": ["webauthn"],
  "auth_time": 0,
  "iat": 0,
  "exp": 0,
  "jti": "attestation-uuid",
  "nonce": "flow-bound-nonce-hash"
}
```

Não entram na prova: senha, credential WebAuthn, session Cognito, tokens OAuth,
secret do client ou dados biométricos.

### Regras

- code e `jti` são single-use;
- TTL máximo de cinco minutos;
- `sub` da prova precisa ser igual ao `sub` da sessão AppSell e dos tokens
  enviados ao Admin;
- redirect URI e client são allowlistados;
- refresh token não cria nova prova;
- login por senha não cria `amr=webauthn`;
- logs registram somente ids, resultado e motivo normalizado, nunca tokens.

## 2. Admin como fonte única do superadmin

### Role

`platform_superadmin` permanece em `admin.roles`, mas o contrato passa a incluir
explicitamente:

```text
appsell.platform.superadmin
financial.admin
```

Além das permissões administrativas globais já previstas.

### Bootstrap explícito

Substituir o efeito colateral no `GET /admin/me` por:

```text
POST /admin/platform/bootstrap
```

O endpoint exige:

- access token e ID token válidos com o mesmo `sub`;
- `email_verified=true`;
- subject igual ao subject reservado em configuração;
- email exato `comercial@autonomia.site`;
- attestation JWS válida, fresca e com `amr=["webauthn"]`;
- `sub` da attestation igual ao principal;
- `jti` ainda não consumido;
- bootstrap ainda não vinculado a outro usuário.

Na mesma transação:

1. cria ou vincula o usuário Admin;
2. insere `admin.user_roles`;
3. grava o singleton de bootstrap;
4. consome o `jti`;
5. grava auditoria;
6. grava snapshots nas outboxes AppSell e Financial.

Qualquer falha produz zero escrita. `GET /admin/me` volta a ser estritamente
read-only.

### Proteções

- nenhuma rota genérica pode atribuir `platform_superadmin`;
- nenhuma organização concede esse papel;
- email serve somente para o bootstrap inicial;
- após o vínculo, a autoridade é o subject imutável;
- troca ou remoção exige fluxo break-glass separado, com dupla aprovação;
- bootstrap pode ser desabilitado imediatamente sem remover o role persistido.

## 3. Projeção Admin -> Financial

O fluxo existente é preservado:

```text
admin.roles contém financial.admin
  -> admin.user_roles
  -> admin.financial_access_outbox
  -> SQS
  -> financial.admin_access_subjects
```

Critérios:

- evento sem email, nome ou foto;
- snapshot completo, não delta;
- revisão monotônica por `admin_user_id`;
- duplicidade idempotente;
- evento antigo não reverte estado novo;
- mensagem inválida vai para DLQ;
- replay usa o runbook existente;
- nenhuma leitura direta das tabelas Admin pelo Financial.

## 4. Projeção Admin -> AppSell

### Evento

Adicionar snapshot PII-free:

```json
{
  "eventType": "admin.platform_access.snapshot",
  "schemaVersion": 1,
  "eventId": "event-uuid",
  "aggregate": {
    "id": "admin-user-uuid",
    "revision": 1
  },
  "subject": {
    "identityUserId": "identity-user-uuid",
    "status": "active",
    "platformSuperadmin": true
  },
  "occurredAt": "2026-07-23T12:00:00Z"
}
```

### Projeção AppSell

Criar na migration `0021_admin_platform_access_projection` a tabela:

```text
platform_admin_access_subjects
```

Se outra migration `0021` entrar em `main` antes do merge, a PR renumera o
arquivo sem alterar o contrato.

Ela guarda subject, status, booleano `platform_superadmin`, revisão, último
evento e timestamps. Só o consumidor da projeção escreve nela.

O `platform_superadmin` deixa de ser concedido pelo bootstrap local do AppSell:

- `/me.platform_roles` deriva esse papel exclusivamente da projeção Admin;
- `platform_grant` continua atendendo papéis locais que não sejam
  `platform_superadmin`;
- inserts locais de `platform_superadmin` ficam revogados/bloqueados;
- `POST /v1/platform/bootstrap-superadmin` é descontinuado e falha fechado;
- Local/CI usa fixtures explícitas da projeção, nunca persona de produção.

### Consumidor

- fila e DLQ dedicadas ao AppSell;
- batch parcial para não repetir mensagens válidas;
- idempotência por `eventId`;
- ordenação lógica por revisão, mesmo em SQS Standard;
- evento antigo é reconhecido e ignorado;
- payload inesperado falha fechado;
- nenhuma PII é persistida na projeção.

## 5. Visão real de plataforma no AppSell

### Redirecionamento

Depois do OAuth e da sincronização de identidade, o BFF consulta `/me`:

- `platform_superadmin` ativo -> `/platform`;
- seller/admin normal -> `/dashboard`;
- buyer -> `/library`;
- sem contexto válido -> falha fechada.

O redirecionamento real não usa `demoProfileForSubject`.

### Ativação inicial

Quando a conta reservada ainda não possui o role, o AppSell mostra:

```text
Ativar acesso de superadmin
```

O botão inicia o step-up no Identity. Não existe campo para declarar MFA ou
passkey manualmente.

### Escopo da tela `/platform`

A primeira versão real mostra:

- todas as organizações/sellers;
- status e responsáveis administrativos;
- todos os produtos, publicação e oferta;
- contagens operacionais de alunos e vendas;
- estado das projeções Admin, AppSell e Financial;
- trilha de auditoria do próprio acesso privilegiado.

Dados de pagamento, secrets, tokens, conteúdo privado e PII detalhada de
compradores não aparecem por padrão. Uma futura inspeção de PII exige propósito,
ticket curto e auditoria própria.

As APIs da tela verificam `platform_superadmin` no servidor. Esconder menu ou
alterar URL não é controle de acesso.

### Demo

`/platform/demo-inspection` e `demo-superadmin` permanecem somente em Local/CI
enquanto houver testes dependentes. Produção não reconhece persona demo. Após a
cobertura real equivalente, a demo será removida em PR separada.

## 6. Testes e evidências

### Identity

- start cria transação curta e não retorna session Cognito;
- complete só emite code após `WEB_AUTHN` real observado;
- senha não produz attestation passkey;
- code duplicado, expirado ou com PKCE errado falha;
- redirect/client não allowlistado falha;
- `sub` divergente falha;
- logs não contêm tokens ou credenciais.

### Admin com PostgreSQL real

- bootstrap sem attestation -> `403`, zero escrita;
- attestation sem `webauthn` -> `403`, zero escrita;
- assinatura, audience, issuer, client, nonce ou subject divergente -> `403`;
- prova expirada ou `jti` repetido -> `403`;
- bootstrap válido persiste role, auditoria e duas outboxes atomicamente;
- repetição idempotente pelo titular retorna o mesmo vínculo sem nova concessão;
- outro subject falha;
- `GET /admin/me` não escreve;
- falha simulada de outbox reverte toda a transação.

### AppSell com PostgreSQL real

- consumidor aplica snapshot novo;
- duplicata é idempotente;
- revisão antiga não sobrescreve a nova;
- revogação remove acesso;
- `/me` deriva superadmin somente da projeção;
- bootstrap local não concede o papel;
- callback real redireciona superadmin para `/platform`;
- admin comum permanece em `/dashboard`;
- rotas globais recusam admin comum;
- consultas globais respeitam paginação, limites e auditoria.

### Financial com PostgreSQL real

- snapshot positivo concede `financial.admin`;
- revogação remove;
- duplicidade e desordem não alteram estado incorretamente;
- endpoint administrativo aceita o titular projetado e recusa admin comum.

### E2E cross-repo

Local/CI usa chaves RSA de teste e adaptador Cognito determinístico:

```text
passkey observada
 -> attestation
 -> bootstrap Admin
 -> duas outboxes
 -> duas projeções
 -> /platform permitido
 -> /financial/operators permitido
```

O teste negativo repete o fluxo com login por senha e exige `403` sem qualquer
grant.

Um smoke de passkey real será executado em staging ou produção somente após
aprovação explícita.

## Organização das entregas

### Wave 1 — Identity Foundation/Auth

- Issue própria para step-up passkey server-observed;
- branch e PR próprias;
- contrato, threat model, testes e runbook;
- feature flag desativada por padrão.

### Wave 2 — Admin

- atualizar a PR #12 para bootstrap explícito com attestation;
- manter a PR #15 como outbox Financial;
- PR nova de outbox AppSell, baseada na PR #15;
- certificar caminho aditivo de deploy antes de retirar o bloqueio.

### Wave 3 — AppSell

- Issue #105: remover assurance auto-declarada e consumir projeção Admin;
- Issue #93: fechar o login real + ativação passkey + tela `/platform`;
- PRs separadas para projeção/backend e UI/BFF;
- Harness 2.2.0, checks rápidos por PR e testes pesados somente no escopo
  relacionado.

### Wave 3 — Financial

- concluir e revisar a projeção existente;
- nenhuma escrita manual em `financial.admin_access_subjects`;
- smoke de `financial.admin` somente depois das projeções.

### Wave 4 — ativação controlada

- merge e deploy somente com aprovação explícita;
- habilitação curta do bootstrap;
- ativação pela conta reservada com passkey;
- confirmação das duas projeções;
- fechamento do bootstrap antes do smoke financeiro.

## Sequência de rollout proposta

Cada etapa tem backup e rollback antes de qualquer PATCH:

1. publicar Identity step-up desativado;
2. aplicar migrations aditivas do Admin e publicar código com bootstrap
   desativado;
3. criar filas/DLQs e publicar consumidores Financial/AppSell;
4. aplicar migration aditiva AppSell e publicar `/platform` oculto;
5. configurar allowlists e subject reservado, mantendo o bootstrap fechado;
6. habilitar uma janela curta de bootstrap;
7. Rodrigo conclui passkey e ativa o acesso;
8. confirmar role Admin, outboxes, projeções e acesso às APIs;
9. fechar a janela de bootstrap;
10. executar o smoke financeiro e depois o checkout de R$ 1 conforme o runbook.

Produção, banco, secrets, infraestrutura, merge, deploy e concessão exigem um
novo 🟢 explícito no momento da execução.

## Rollback

- Identity: desabilitar a flag de step-up e manter login OAuth normal;
- Admin: desabilitar bootstrap; migrations são aditivas e não são revertidas
  destrutivamente;
- filas: pausar event source mappings sem apagar mensagens;
- AppSell: ocultar `/platform` e manter projeção persistida para replay;
- Financial: pausar consumidor e preservar tabela projetada;
- concessão já consumada: não remover por DELETE manual; usar snapshot de
  revogação originado no Admin e confirmar as duas projeções;
- imagens anteriores, envs completos e dumps são registrados antes do rollout.

## Critérios de pronto para pedir aprovação de produção

- todas as PRs com Harness verde;
- review independente sem P0/P1/P2 aberto;
- testes PostgreSQL reais e E2E cross-repo verdes;
- threat model e runbooks revisados;
- filas, policies e envs documentados sem valores de secrets;
- plano de backup, deploy, smoke e rollback com comandos exatos;
- nenhuma dependência de persona demo;
- nenhuma escrita direta em projeções;
- matriz indicando ordem de merge e compatibilidade entre versões.
