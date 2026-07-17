# Adapter server-only do Identity — Issue #13

Este slice cria o contrato OAuth/OIDC server-side e o liga às rotas do AppSell
quando `APPSELL_OAUTH_MODE=identity`. Ele não registra o client, não provisiona
contas e ainda não prova integração com staging.

## Clientes e claims esperados

- `APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID` é configurável e enviado em
  `authorize` e `token` para identificar o produto lógico. O default é
  `appsell-web`. Aceita apenas valores no padrão conservador
  `^[a-z0-9][a-z0-9._-]{0,63}$` (minúsculas, dígitos e `. _ -`, primeiro
  caractere alfanumérico, até 64 chars). Vazio, ausente, maiúsculas, espaços ou
  qualquer valor fora do padrão falham fechado (`IdentityOAuthConfigurationError`).
- `APPSELL_IDENTITY_EXPECTED_AUDIENCE` é configurável com o mesmo padrão e default
  `appsell-web`. Tanto o ID token quanto o access token emitidos pela Foundation
  precisam ter `aud` string exatamente igual ao valor configurado. O adapter não
  aceita fallback por `client_id` Cognito nem token sem audience. Tornar a
  audience configurável é o que permite apontar o adapter para o app-client id do
  provedor atual (Opção B) sem afrouxar a verificação de `aud`.

## Modos de compatibilidade (Opção B)

Para integrar com a Foundation atual (tokens Cognito) sem reescrever o núcleo, o
adapter expõe dois modos, ambos com default = comportamento legado intacto.

| Var | Valores | Default | Efeito |
|-----|---------|---------|--------|
| `APPSELL_IDENTITY_REFRESH_MODE` | `logical-handle` \| `provider-refresh` | `logical-handle` | `logical-handle`: refresh exige handle lógico opaco de 43 chars (`^[A-Za-z0-9_-]{43}$`) e **rotação obrigatória** (novo handle ≠ anterior, também 43 chars); handle ausente ou repetido = ambíguo. `provider-refresh`: aceita refresh token opaco do provedor (`^[A-Za-z0-9._~+/=-]{20,4096}$`), **rotação opcional** (se o provedor não devolver novo token, reusa o de entrada); em ambos os casos revalida assinatura RS256/`kid`/`iss`/`aud`/`sub`/`exp`/`token_use`/`email_verified` e ausência de `nonce` no resultado. |
| `APPSELL_IDENTITY_NONCE_MODE` | `required` \| `disabled` | `required` | `required`: envia `nonce` no `authorize` e exige `nonce` exato no ID token do code. `disabled`: **não envia** `nonce` e **rejeita** qualquer ID token que traga `nonce` inesperado (fail-closed). Para provedores que não ecoam `nonce`. |

Qualquer valor desconhecido em qualquer das duas vars falha fechado na
inicialização (`IdentityOAuthConfigurationError`).

Trade-offs:

- `provider-refresh` abre mão da garantia de rotação por transação — a defesa
  contra replay passa a depender da sessão Redis absoluta e da revalidação
  criptográfica do bundle, não da troca de handle. Só habilitar contra provedor
  cujo refresh token seja realmente opaco e de vida controlada.
- `nonce-disabled` remove a ligação `nonce`↔`authorization code`. A proteção
  anti-replay do code passa a depender só de PKCE (`S256`) + `state`. O adapter
  compensa parcialmente rejeitando ID token que traga `nonce` quando não pediu um.

O leg de `refresh` sempre exige `nonce` ausente no ID token renovado,
independente do `APPSELL_IDENTITY_NONCE_MODE` (o `nonce` só participa do leg de
`authorize`/code).

O adapter também exige assinatura `RS256` por JWKS allowlisted, `kid` explícito
e canônico, issuer e expiração válidos, `token_use`, subjects iguais,
`email_verified` booleano `true` e `nonce` exato no ID token.

O `refresh_token` é opcional no retorno de code para preservar compatibilidade
explícita com a Foundation #29; o contrato #32 devolve um handle lógico opaco.
O adapter implementa `grant_type=refresh_token`, exige rotação do handle,
revalida assinatura/issuer/audience/subject e exige que o novo ID token omita
`nonce`. `401` vira `invalid_grant`, `409` vira retry transitório e timeout,
`502` ou qualquer resposta incerta viram resultado ambíguo, que o lease Redis
proíbe de repetir automaticamente.

## Estado da integração

Os contratos locais da Foundation #29/#32 vinculam o `nonce` ao authorization
code e emitem tokens lógicos com `aud=appsell-web` e JWKS próprio. O code exige
nonce exato; o refresh exige nonce ausente e handle novo.

Enquanto a Foundation não retorna `refresh_expires_in`, o adapter exige
`APPSELL_IDENTITY_REFRESH_HANDLE_TTL_SECONDS` entre duas horas e trinta dias. O
valor deve ser conservador e não maior que o TTL do client na Foundation; a
sessão Redis absoluta continua limitando o uso. Esse valor configurado não é
prova do prazo real do handle e permanece um contrato a fechar antes de produção.

O BFF persiste o verifier PKCE junto da transação, troca o code no servidor,
cifra access token, handle de refresh e identidade verificada no vault e grava
sessão+vault+receipt atomicamente no Redis. Retries após commit autenticam o par
antes de aceitar a sessão, e refresh preserva a identidade cifrada. Um claim
Redis por transação garante que somente uma instância BFF envie o code à
Foundation; peers aguardam o receipt, e qualquer resultado externo ambíguo
marca a transação para impedir retry cego.

Isso prova o vertical local e a compatibilidade de contrato. Ainda não houve
Identity em staging, registro do client, rotação de chaves ou deploy. Uma falha
local depois de a Foundation consumir o code e antes do commit Redis também
continua sem reconciliação durável. 429, 5xx e timeout após envio do code são
classificados como ambíguos e falham fechados; esse gate permanece bloqueado
para produção.

## Limites operacionais

HTTP é aceito somente com `NODE_ENV=development` ou `NODE_ENV=test` e apenas em
loopback. `NODE_ENV` ausente ou desconhecido falha fechado. Produção exige HTTPS,
origins explícitas, callback exata, redirect manual, timeout e limite de corpo.
Tokens retornam apenas pelo módulo `server-only` e são persistidos cifrados no
vault; nunca são enviados ao browser, logs ou API pública. O provider AES local
continua proibido em produção, portanto habilitar `identity` sozinho não abre o
login comercial sem o provider KMS/Transit aprovado.
