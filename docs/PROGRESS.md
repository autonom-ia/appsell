# PROGRESS

## Fase atual

Fase 1 — spike local de Identity/BFF.

## Concluído

- scaffold Next.js 16.2.10 + React 19.2.7;
- OAuth mock HTTP com state, nonce e PKCE S256;
- callback server-side e code single-use;
- sessão opaca com cookie HttpOnly e vault in-memory dev-only;
- dashboard, introspecção mínima e logout same-origin;
- consumo somente após state/nonce/PKCE válidos;
- stores limitados, limpeza de expirados e rate limit local;
- testes Playwright automatizados em Chromium e WebKit mobile de login, sessão, logout e replay;
- suíte automatizada com `next start` para falha fechada em produção;
- 12 testes Vitest de replay, config de logs, adulteração, revogação, TTL, limites e cookies.

## Em andamento

- preparação da PR draft após terceira revisão independente aprovada sem P0-P2.

## Pendente

- spike com Autonom.ia Identity real;
- vault persistente/HA e rotação;
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

Abrir PR draft ligada à Issue #8 e aguardar checks/review humano. Não reutilizar
o mock em produção e não fazer merge enquanto os gaps do Identity real não
estiverem explicitamente aceitos.
