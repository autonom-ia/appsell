# E2E sobre o build de produção

Desde o #585 (PR #610) o e2e do web roda sobre o build de produção, não sobre o
`next dev`. Este documento diz como cada suíte sobe o servidor e o que fica sem
cobertura.

## As duas suítes de navegador

| Suíte | Configuração | Servidor | Ambiente |
|---|---|---|---|
| `pnpm test:e2e` | `playwright.config.ts` | `tests/e2e/support/https-server.mjs`, em `https://localhost:3000` | as três travas do runtime de e2e (abaixo) |
| `pnpm test:e2e:production` | `playwright.production.config.ts` | `next start`, em `http://localhost:3000` | sem nenhuma das três travas |

As duas usam o mesmo `pnpm build`. No CI, o `cert_web` roda as duas em
sequência no `workflow_dispatch` e no push da `main` (`.github/workflows/app-ci.yml`).

## As três travas do runtime de e2e

`lib/e2e-build-runtime.ts` libera a persona demo no build de produção só quando
as três valem juntas:

1. `NODE_ENV=production` com `APP_ENV=ci`;
2. origem pública `https://localhost:3000` com `APPSELL_OAUTH_MODE=local`;
3. o marcador `APPSELL_E2E_BUILD_RUNTIME`, que só o `playwright.config.ts` passa.

Quem prova o quê:

- `tests/e2e-build-runtime.test.ts` prova a recusa com cada trava faltando,
  uma de cada vez.
- `tests/production/fail-closed.spec.ts` sobe o build sem nenhuma das três e
  prova que o Identity simulado, o login, a sessão e as rotas de demo ficam
  fechados. Ele não prova a recusa com só uma trava faltando.
- `tests/token-vault.test.ts` prova a regra da chave local do vault: num
  processo de produção, o ambiente recebido e o `process.env` precisam
  autorizar juntos (#611).

## O que o servidor de teste não cobre

Produção roda o pacote standalone do Next (`output: "standalone"` em
`next.config.ts`, `node server.js` em `deploy/easypanel-native/Dockerfile.web`).
As duas suítes rodam o build pela API do Next (`next({ dev: false })` e
`next start`) sobre a pasta `.next` inteira. Por isso o e2e não pega:

- arquivo que o rastreio do build deixou fora do standalone, como um JSON lido
  em tempo de execução ou um módulo carregado por caminho dinâmico;
- falta de `.next/static` ou de `public`, que o Dockerfile copia à parte;
- diferença de comportamento do `server.js` do standalone em relação ao
  `next start`.

O que cobre esses pontos hoje:

- o build da imagem web no CI (`image_web`), que falha se o Dockerfile não
  achar o que copia. Ele não sobe a imagem;
- o smoke e o tester depois do deploy, que rodam contra o standalone no ar.

Quem mexer em leitura de arquivo em tempo de execução, em `next.config.ts` ou
no Dockerfile do web confere esses pontos no smoke pós-deploy, e não só no e2e.
