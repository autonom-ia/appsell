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
| `APPSELL_IDENTITY_REFRESH_MODE` | `logical-handle` \| `provider-refresh` | `logical-handle` | `logical-handle`: refresh exige handle lógico opaco de 43 chars (`^[A-Za-z0-9_-]{43}$`) e **rotação obrigatória** (novo handle ≠ anterior, também 43 chars); handle ausente ou repetido = ambíguo. `provider-refresh`: aceita refresh token opaco do provedor (`^[A-Za-z0-9._~+/=-]{20,4096}$`), **rotação opcional** (se o provedor não devolver novo token, reusa o de entrada); em ambos os casos revalida assinatura RS256/`kid`/`iss`/`aud`/`sub`/`exp`/`token_use`/`email_verified` no resultado. O `nonce` do resultado segue o `APPSELL_IDENTITY_NONCE_MODE`: exigido ausente em `required` e `disabled`, não verificado em `broker`. |
| `APPSELL_IDENTITY_NONCE_MODE` | `required` \| `disabled` \| `broker` | `required` | `required`: envia `nonce` no `authorize` e exige `nonce` exato no ID token do code. `disabled`: **não envia** `nonce` e **rejeita** qualquer ID token que traga `nonce` inesperado (fail-closed). Para provedores que não ecoam `nonce`. `broker`: **não envia** `nonce` e **aceita** o ID token com ou sem `nonce`, sem comparar. Só para upstream que repassa ID token de terceiro (ex.: Cognito via Foundation) — o `nonce` presente é do fluxo do broker, não deste client. |

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

O leg de `refresh` exige `nonce` ausente no ID token renovado em `required` e
`disabled` (o `nonce` só participa do leg de `authorize`/code). Em `broker` o
`nonce` também não é verificado no refresh, pela mesma razão do leg de code: o
token renovado continua sendo emitido por terceiro.

### Quando usar `broker`

Use apenas quando o upstream **não é um OpenID Provider**, e sim um broker que
repassa um ID token que ele não emitiu para este client. É o caso da Foundation
(`auth.api-autonomia.com`): ela guarda os tokens Cognito atrelados ao
authorization code e os devolve no `/oauth/token`, então o ID token chega com
`iss` do Cognito, `aud` do client Cognito e um `nonce` do fluxo interno dela —
nunca o `nonce` que este adapter enviou. Em `required` a comparação nunca bate e
em `disabled` a presença do `nonce` é rejeitada: os dois falham fechado e o
login não completa.

Sendo preciso sobre o que cada mecanismo garante aqui:

- **o que o code de uso único + PKCE S256 + `state` garantem**: um terceiro não
  consegue trocar nem reusar o authorization code, e o ID token nunca transita
  pelo front-channel — ele só chega pelo backchannel, na resposta do
  `/oauth/token` desta troca;
- **o que eles não garantem**: que o bundle devolvido corresponda a *esta*
  autorização. Isso é o binding `code → client lógico + redirect URI + PKCE →
  tokens armazenados`, e quem o faz é a Foundation
  (`oauth-client.service.ts:124-137`), não este adapter.

Ou seja, `broker` transfere para a Foundation a garantia que o `nonce` daria a
este client. **Pré-condição obrigatória do modo**: a Foundation precisa manter
code de uso único, comparação do `client_id` lógico, comparação do redirect URI
e validação do `code_verifier` S256 antes de devolver os tokens. Se essa
garantia regredir, `broker` deixa de ser seguro — uma regressão lá não é
detectável aqui.

Consequência conhecida e **anterior** a este modo: `aud` é o client Cognito, que
é compartilhado entre produtos. O ID token, sozinho, não distingue `appsell-web`
de `neuroai-web` ou do Studio; essa separação existe apenas no binding da
Foundation. `broker` não cria esse fato, mas remove a última barreira residual
que ainda existia no adapter (`required` rejeitaria um nonce diferente).
Essa barreira está fixada por teste do lado da Foundation, incluindo o caso
confused deputy (code do client A resgatado como client B com verifier válido):
`autonom-ia/autonomia-identity-foundation#61`. Uma regressão lá quebra o teste
de lá — aqui ela continuaria invisível.

Todo o resto continua fail-closed — `iss`, `aud`, assinatura RS256 via JWKS
allowlisted, `kid`, expiração, `token_use`, `email_verified` e igualdade de
`sub` entre access e ID token.

`broker` é uma ponte, não o destino: quando
`autonom-ia/autonomia-identity-foundation#29` emitir ID token lógico assinado
pela Foundation (com `nonce`, `iss` e `aud` próprios), volte para `required` e
aponte `APPSELL_IDENTITY_ISSUER`/`APPSELL_IDENTITY_EXPECTED_AUDIENCE` para os
valores lógicos.

O adapter também exige assinatura `RS256` por JWKS allowlisted, `kid` explícito
e canônico, issuer e expiração válidos, `token_use`, subjects iguais e
`email_verified` booleano `true`. O tratamento do `nonce` é o único item que
varia por modo, conforme a tabela acima: exato em `required`, ausente em
`disabled`, não verificado em `broker`.

O `refresh_token` é opcional no retorno de code para preservar compatibilidade
explícita com a Foundation #29; o contrato #32 devolve um handle lógico opaco.
O adapter implementa `grant_type=refresh_token`, exige rotação do handle,
revalida assinatura/issuer/audience/subject e exige que o novo ID token omita
`nonce`. `401` vira `invalid_grant`, `409` vira retry transitório e timeout,
`502` ou qualquer resposta incerta viram resultado ambíguo, que o lease Redis
proíbe de repetir automaticamente.

## Estado da integração

Os contratos locais da Foundation #29/#32 vinculam o `nonce` ao authorization
code e emitem tokens lógicos com `aud=appsell-web` e JWKS próprio. Sob esses
contratos o code exige nonce exato e o refresh exige nonce ausente e handle
novo — isto é, `APPSELL_IDENTITY_NONCE_MODE=required`.

**A Foundation em produção ainda não implementa a #29.** Hoje ela repassa o ID
token Cognito, com `iss` do Cognito, `aud` do client Cognito e `nonce` do fluxo
interno dela, o que exige `broker` (ver seção acima). A pré-condição desse modo
— o binding `code → client + redirect + PKCE`, com consumo único — está fixada
por teste em `autonom-ia/autonomia-identity-foundation#61`.

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
