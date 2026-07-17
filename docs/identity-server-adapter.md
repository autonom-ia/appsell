# Adapter server-only do Identity — Issue #13

Este slice cria o contrato OAuth/OIDC server-side e o liga às rotas do AppSell
quando `APPSELL_OAUTH_MODE=identity`. Ele não registra o client, não provisiona
contas e ainda não prova integração com staging.

## Clientes e claims esperados

- `APPSELL_IDENTITY_LOGICAL_OAUTH_CLIENT_ID` deve ser exatamente `appsell-web` e
  é enviado em `authorize` e `token` para identificar o produto lógico.
- `APPSELL_IDENTITY_EXPECTED_AUDIENCE` deve ser exatamente `appsell-web`. Tanto
  o ID token quanto o access token lógico emitidos pela Foundation precisam ter
  `aud` string exatamente igual. O adapter não aceita fallback por `client_id`
  Cognito nem token sem audience.

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
