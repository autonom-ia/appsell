# PROGRESS

## Fase atual

Fase 1 — Identity/BFF local com refresh Redis distribuído Local/CI.

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

## Em andamento

- Issue #18 implementando e validando refresh distribuído Local/CI;
- reviews independentes AppSec, concorrência e contrato verdes, P0-P2=0;
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

## Próximo passo recomendado

Abrir PR draft da #18 e fechar o App CI. Não integrar Identity real nem iniciar KMS real #19; não
reutilizar mock/provider local em produção.
