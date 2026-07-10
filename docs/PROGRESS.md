# PROGRESS

## Fase atual

Fase 2 — design system e protótipo Studio mobile-first.

## Concluído

- scaffold Next.js 16.2.10 + React 19.2.7;
- OAuth mock HTTP com state, nonce e PKCE S256;
- callback server-side e code single-use;
- sessão opaca com cookie HttpOnly e store Redis dev-only;
- dashboard, introspecção mínima e logout same-origin;
- consumo somente após state/nonce/PKCE válidos;
- stores limitados, limpeza de expirados e rate limit local;
- testes Playwright automatizados em Chromium e WebKit mobile de login, sessão, logout e replay;
- suíte automatizada com `next start` para falha fechada em modo `production`
  local, sem alegação de produção real;
- 23 testes Vitest de replay, config, revogação, TTL, concorrência e dois BFFs reais;
- repository Redis assíncrono sem fallback em memória, IDs SHA-256 e TTL real;
- callback e criação de sessão em uma operação Lua atômica;
- rate limit e revogação atômicos;
- testes reais com Redis efêmero fora da porta 6379, incluindo 20 callbacks concorrentes.
- retry idempotente de callback com ID HMAC determinístico e receipt curto sem IDs crus;
- prova real com dois processos Next BFF distintos compartilhando a mesma sessão;
- Redis de teste autenticado e validado por PID antes de qualquer operação.
- token vault Local/CI com envelope encryption, DEK por registro, revision na
  AAD e keyring rotacionável; PR #20 mergeada e CI pós-merge verde;
- refresh server-side sem rota HTTP, coordenado por lease, fence decimal exato,
  intent idempotente e CAS Lua;
- máquina de estados `prepared/inflight/retryable/ambiguous`; somente transient
  comprovado habilita retry, enquanto crash inflight falha ambíguo;
- resultado upstream ambíguo persistido sem retry automático; `invalid_grant`
  e escritores obsoletos protegidos por revision/fence; logout explícito apaga
  o par e coordenação incondicionalmente;
- 16 testes adversariais de refresh em Redis real, totalizando 51 Vitest locais.
- PR #21 de refresh distribuído mergeada em `main`;
- landing pública qualificada como prévia local;
- shell persistente derivado do Studio, com sidebar desktop e bottom nav mobile;
- visões demonstrativas de produtor, catálogo e comprador multi-seller;
- gate server-side no Proxy antes de renderizar conteúdo protegido;
- proteção contra cookie HttpOnly no DOM/RSC e request anônimo sem payload;
- projeções tenant/buyer testadas e fail-closed para produção/sujeito desconhecido;
- matriz real 360/390/768/1024/1440, 44 px, WebKit/Chromium, reload,
  teclado e reduced motion;
- 55 unitários, 17 E2E e 1 teste production local verdes.

## Em andamento

- Issue #22 implementada e validada localmente;
- reviews independentes tenancy/AppSec, mobile UX/a11y e Next/testes verdes,
  P0-P3=0;
- PR da #22 ainda não aberta; CI remoto pendente;
- épica #8 permanece em desenvolvimento;
- Identity real #13 permanece bloqueado por ausência de dev/hml e P0 upstream.

## Pendente

- spike com Autonom.ia Identity real (#13);
- refresh real contra Identity (#18), bloqueado pelo contrato/ambiente da #13;
- KMS real, vault durável/HA e restore (#19), bloqueado;
- CSRF generalizado para todas as futuras mutações;
- RBAC e isolamento multi-tenant.

## Falhas encontradas e corrigidas

- cookie HttpOnly aparecia no canal de debug do RSC: dashboard passou a usar
  somente a visão sanitizada de `/api/session` no cliente;
- callback inválido consumia transaction/code antes das validações: consumo foi
  movido para depois de state, nonce e PKCE;
- stores não tinham limites/limpeza/rate limit: controles locais adicionados;
- PostCSS vulnerável: resolução fixada em 8.5.16 e audit zerado;
- rotas dev retornavam 500 no build de produção: agora falham de forma
  controlada, com 404/503/401 e `Cache-Control: no-store`.
- erro de callback ainda removia o cookie de transaction: a remoção agora só
  acontece após sucesso, preservando a tentativa legítima.
- leitura server-side do cookie no RSC dev serializou sessão no Flight inline:
  gate movido para `proxy.ts`, com teste explícito de ausência no DOM/responses;
- Vitest 4.0.18 tinha advisory crítico no UI server de desenvolvimento:
  atualizado para 4.1.0 e `pnpm audit` voltou a zero.

## Próximo passo recomendado

Abrir PR da #22, rodar CI remoto e aguardar aprovação de merge. Não integrar
Identity real nem iniciar KMS real #19; não reutilizar preview/mock em produção.
