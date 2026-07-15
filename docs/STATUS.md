# STATUS — Health snapshot do projeto

Atualizado em pontos de marco (PR aberta, deploy, incidente). Diferente de MEMORY_SESSION.md (que é estado da tarefa atual) — STATUS é estado do PROJETO.

## Indicadores

| Indicador | Status | Última verificação |
|-----------|:------:|--------------------|
| Build | 🟢 release candidate #61 compila em modo produção | 2026-07-14 |
| Testes | 🟢 vertical real API/PG/S3 e capacidade 100/1.000 verdes localmente | 2026-07-14 |
| CI | 🟡 Draft PR #62: 2 artifacts elegíveis; 1 run rejeitado; repetição sequencial em andamento | 2026-07-14 |
| Deploy produção | 🟡 não configurado/não executado | 2026-07-10 |
| Harness instalado em `main` | 2.0.2 | 2026-07-10 |
| Harness upgrade | 🟢 PR #37 em 2.1.2, review e 10/10 checks verdes; sem merge | 2026-07-11 |
| Harness no RC #61 | 🟢 2.1.2 | 2026-07-13 |

## Estado atual

O release candidate da Issue #61 consolida o protótipo Studio mobile-first, a
fundação persistente, a ponte BFF/API, autoria/publicação, buyer multi-seller,
player/progresso, storage privado, superadmin demo, Identity/OpenBao Local/CI,
pricing fail-closed e storefront público. A fundação inclui FastAPI,
PostgreSQL 16, Alembic forward-only,
roles owner/migrator/runtime, RLS forçado, contexto transacional assinado,
auditoria append-only e bootstrap one-shot do superadmin com atestação separada.
Este marco é Local/CI; não é o Identity real e não concede conta de produção.

## Última mudança relevante

2026-07-14 — Issue #63 iniciou a Fase 6A em branch própria, empilhada sobre a
release candidate. O webhook genérico Local/CI preserva os bytes no trânsito,
valida HMAC, timestamp e event ID, resolve tenant exclusivamente por endpoint
opaco server-side e grava evento imutável, auditoria e outbox na mesma transação.
O banco retém somente hashes/tamanhos e o fato normalizado. Provider real,
worker, entitlement, Financial, Neuro, staging e produção permanecem fora do
slice. O outbox nasce `blocked_retention` e não pode ser reclamado por worker.
A prova integrada inclui PostgreSQL real, duas BFFs, Redis indisponível
e restart da API; reviews AppSec, PostgreSQL e contrato ficaram GREEN. A Draft
PR #64 permanece sem merge/deploy e aguarda CI no topo da base corrigida.

Após sincronizar a base corrigida, o webhook passou a usar o mesmo outer
transaction/savepoint do replay: rejeições duravelmente consomem o request e
desfazem evento, outbox e auditoria. O teste real agora prova essa diferença de
estado. A composição passou 207 testes PostgreSQL/API, 24 testes de boundary e
worker, 6 testes BFF reais e 1 multi-seller, com artifact scan verde. O heading
assíncrono do editor ganhou timeout explícito de 20 segundos porque o default de
5 segundos falhou sob a carga do próprio gate, mesmo com a tela ainda em estado
de carregamento. Dois reviews críticos independentes ficaram GREEN para o
merge commit/push, com P0/P1/P2/P3 bloqueantes em zero. O CI do novo SHA
continua pendente; merge da PR e deploy permanecem proibidos.

2026-07-14 — A saturação sustentada do capacity gate foi rastreada ao replay
anti-repetição em uma transação durável separada de cada operação de domínio.
O autenticador agora consome o replay no outer transaction e executa o domínio
em savepoint; sucesso confirma ambos uma vez, enquanto falha de domínio ou de
constraint diferida desfaz somente o negócio e preserva o replay. Rejeições
pós-envelope e pré-domínio também consomem o request, e continuations ficam
vinculadas ao envelope completo, à instância do autenticador, à mesma asyncio
Task e a transições de capability explícitas. O worker privado valida shape,
identidade UUIDv5, issuer/audience, hashes e janela temporal exatos.

O gate PostgreSQL passou com 195 testes integrados e 24 testes explícitos de
boundary/worker; BFF real passou 5/5 e multi-seller 1/1. O storage vertical
agora cria e confirma o PDF pela rota FastAPI com autenticador, PostgreSQL e S3
reais antes de processar, publicar e autorizar o download, e passou 1/1. A
carga 100 admins/1.000 buyers passou sem erro com 107/487 amostras de espera
(21,97%, limite 25%) e uma única amostra isolada de pool cheio; por isso o
artifact local é deliberadamente inelegível e exige repetição no mesmo SHA.
Dois reviews críticos independentes ficaram GREEN para commit/push da Draft PR,
com P0/P1/P2/P3 bloqueantes em zero. Merge, deploy e certificação de produção
continuam não autorizados.

2026-07-14 — O SHA corrigido da Draft PR #62 registrou uma única amostra 5/5
esperando entre 724, sem duração sustentada, erro funcional, timeout, 5xx,
Lock ou BufferPin. Review independente classificou como P2 o hard gate de zero
amostras porque ele depende da fase aleatória do sampler. O gate agora reprova
persistência comprovada (duas amostras consecutivas), mantém integralmente o
limite global de 25% e registra uma amostra isolada como warning que torna o
run inelegível para calibração e exige repetição no mesmo SHA. Markdown e Job
Summary exibem a inelegibilidade; a certificação final aceita somente três
artifacts elegíveis, distintos e do mesmo head SHA por
`scripts/report_capacity_calibration.py --certify-three`. O job fixa o checkout
no head SHA, registra `git rev-parse HEAD` como autoridade e rejeita SHA não
canônico, divergência da evidência de origem e reruns do mesmo run ID. O
artifact vermelho anterior não foi promovido manualmente. O review crítico
final ficou GREEN (P0/P1/P2/P3 = 0) e o gate local completo passou com 268
testes, 8 skips condicionais, lint, typecheck, build e audit verdes.

2026-07-13 — Issue #61 consolida uma release candidate única sobre a PR #59
(UI + Identity + OpenBao), com inspeção superadmin #57, capacidade/multi-seller
#53/#55 e contratos de pricing/Neuro/PIX #60. A migration superadmin foi renumerada para
`0010_release_candidate`, sequencial a `0009_buyer_multi_seller`, evitando duas
heads Alembic. Nenhum merge em `main`, deploy ou produção foi executado.

A linha de capacidade #53, o runtime multi-seller #55 e o storefront público
foram incorporados. A projeção buyer continua derivada de entitlement ativo e
nunca aceita seller do browser. O storefront `/s/{sellerSlug}` expõe apenas
seller ativo e publicação vigente por uma projeção SQL mínima; a credencial de
serviço permanece server-side e o CTA informa que checkout ainda não está ativo.

## O que está funcionando

- OAuth/BFF local — state, nonce, PKCE, sessão opaca e browser sem token;
  evidência nas PRs #11/#15 e App CI.
- Redis Local/CI — TTL, Lua atômico, retry idempotente, rate limit, dois BFFs,
  Redis efêmero autenticado; 23/23 testes.
- Modo `production` local (`next start`) fail-closed — mock 404,
  login/callback 503 e sessão 401; 1/1 E2E.
- #17 apenas Local/CI — envelope encryption com DEK por registro, keyring
  local, AAD, sessão+vault atômico e 35/35 testes; reviews AppSec e concorrência
  verdes; PR #20 e App CI pós-merge verdes.
- Refresh distribuído Local/CI mergeado pela PR #21.
- Design system próprio derivado dos tokens do Studio, sem dependência runtime.
- Gate server-side para `/dashboard`, `/products` e `/library`, sujeito dev
  exato e produção fail-closed.
- Projeções demonstrativas testadas: seller não recebe produto de outro seller;
  buyer só recebe entitlement ativo/publicado; recomendações nunca incluem
  draft nem outro seller.
- Mobile/desktop: 360, 390, 768, 1024 e 1440 sem overflow; alvos >= 44 px;
  Chromium e WebKit, reduced motion, deep link e reload verdes.
- PostgreSQL 16 isolado com PUBLIC CONNECT revogado, runtime sem ownership,
  `SUPERUSER` ou `BYPASSRLS`, RLS e grants mínimos verificados diretamente.
- Bootstrap local one-shot: issuer/audience/client fixados, claims vinculados a
  atestação HMAC exclusiva, e-mail verificado exato, MFA/passkey, advisory lock,
  um vencedor entre 20 candidatos com fan-out concorrente limitado a 12,
  audit e auto-disable.
- CI Python com lock, Ruff, mypy strict, pip-audit, migration limpa, testes PG,
  build do container por digest e healthcheck real.
- Ponte BFF→API Local/CI com credencial de serviço e envelope HMAC vinculado a
  método, path, query, body, capability, sessão, identidade e janela temporal.
- Replay persistente no PostgreSQL, UUIDv5 compatível em TypeScript/Python/SQL,
  sincronização issuer-scoped e onboarding seller idempotente/atômico.
- POST de onboarding protegido por origem canônica, CSRF por sessão+ação e
  leitura streaming limitada a 16 KiB; fluxo real persiste entre dois BFFs.
- Produto/curso/oferta editáveis com versões esperadas, receipt idempotente e
  locks por produto sem serializar todo o tenant.
- Preview privado e publicação multi-course gerados server-side; snapshot
  idêntico é negado no PostgreSQL e retry exato retorna o receipt original.
- Editor 320 px com rebase de conflito, guard de Back/gesture, modal com foco
  contido, 200% de texto e oferta explicitamente sem cobrança.
- Currículo Course → Module → Lesson text-only com RLS forçado, runtime sem DML,
  CAS por `course.draft_version`, retry idempotente, archive e reordenação por
  conjunto completo; UI usa botões/teclado e preserva foco.
- Receipts curriculares têm janela operacional explícita de até 30 dias e
  retenção máxima de 5.000 por curso; retries fora dessa janela são nova
  operação e continuam sujeitos a versão esperada/autorização.
- Snapshot schema v2 determinístico inclui módulos/aulas; snapshots v1 continuam
  imutáveis. Payload >1 MiB falha em 422 antes de publication/receipt parcial.
- Tenant demo Local/CI usa reset sob advisory lock, IDs determinísticos e duas
  personas fixas; o buyer recebe apenas entitlement ativo e o admin permanece
  fora da biblioteca do comprador. O buyer recebe 403 para capabilities de
  seller/catálogo mesmo com CSRF válido e acesso direto a `/products` volta para
  `/library`. O admin demo não pode criar outra tenant nem usar catálogo fora do
  seller UUID fixo; o reset remove memberships e grants legados das personas.
- Cinco PDFs do curso Codex ficam fora de `public/`; cada leitura exige sessão,
  capability, entitlement, snapshot publicado e validação de hash/tamanho.
- Upload PDF seller-scoped usa intent curta, chave opaca, checksum SHA-256,
  quota transacional e objeto privado versionado; o browser nunca recebe
  credencial S3 e não consegue tornar um asset `ready` diretamente.
- Worker separado move o objeto de quarentena somente após conferir tamanho,
  hash, estrutura PDF e resultado do antimalware; staging/produção falham
  fechados sem ClamAV configurado.
- Publicação fixa `asset_version_id`, hash e tamanho no snapshot. Download do
  comprador revalida sessão, entitlement, publicação e origem do storage.
- Auditoria externa comprova acesso anônimo negado no endpoint S3-compatible;
  reconciliação e cleanup são duráveis e tenant-scoped.
- Uploader mobile permite cancelar, repetir e arquivar com gate de navegação,
  foco preservado, alvos de 44 px e cobertura em 320/360/390 px e texto 200%.
- Player retoma por aula/curso, grava progresso com versão esperada e receipt
  persistente, revalida revogação e não confia em seller vindo do browser.
- Testes cobrem mesma chave, versões concorrentes, revogação, republicação
  pinada, multi-course e 100 buyers sem bypass de RLS.
- Inspeção superadmin Local/CI usa persona oculta, grant ativo por issuer+sub,
  ticket de até 15 minutos, seller fixo server-side, projeção sem PII/assets e
  auditoria append-only; não concede membership e falha fechado no BFF e na
  API quando o ambiente não é Local/CI.
- A página de inspeção não lê cookies no React Server Component. O GET BFF
  sanitizado valida sessão/grant/ticket e retorna somente projeção mínima +
  CSRF; testes procuram o token e a sessão no HTML/RSC bruto e não os encontram.
- O callback da persona superadmin exige Domain API e grant ativo antes de
  emitir sessão; ausência da API revoga a sessão e expira cookies. O OpenAPI da
  Domain API está desabilitado, evitando enumerar as rotas restritas.
- Storefront público mobile-first por seller com 320/390 px, landscape,
  reduced motion, CTA >=44 px e ausência de overflow verificados; draft,
  buyer/e-mail, asset e dados de checkout não fazem parte da projeção pública.
- Certificado Local/CI de capacidade executou 100 owners + 1.000 buyers,
  1.100 callbacks/sessões, leituras, mutações e negações cross-tenant sem erro;
  thresholds e teardown passaram. É baseline provisória, não sizing de VPS.
- Vertical integrada atual: 193/193 testes PG/API, 272 unitários verdes com 9
  integrações condicionais ignoradas, 44/44 Playwright executados com 6 skips
  previstos, produção fail-closed 1/1, OpenBao 7/7, audits Node/Python verdes.

## O que está quebrado / em degradação

- Identity real — sem dev/hml isolado e com P0 upstream — Issue #13.
- Refresh real com Identity — bloqueado por #13.
- KMS real/vault HA — provider/infra/staging não decididos — Issue #19.
- Staging/VPS/TLS — não certificado e fora do escopo atual — Issue #5.
- Assets privados da Issue #40 estão na PR #41, com review crítico e CI verdes;
  ainda dependem de aprovação/merges ordenados e permanecem somente Local/CI.
- A baseline de regressão Local/CI para uma instalação com 100 tenants está
  comprovada e foi ampliada localmente na Issue #46 para dez buyers por tenant,
  mas capacidade de lançamento ainda não está
  certificada em staging/VPS: faltam carga com margem 2x, soak, restore,
  rollback e observabilidade reais.
- A inspeção superadmin da PR #57 foi portada para a migration sequencial 0010
  da release candidate #61; continua exclusiva de Local/CI e read-only.
- As PRs #35/#36 continuam sem merge; seus recursos não estão em produção.
- Checkout, criação de cobrança, webhooks e reconciliação financeira reais
  ainda não estão implementados na release candidate. Os contratos de pricing,
  Neuro e PIX não equivalem a uma cobrança executável.

## Bloqueios

- Identity Foundation/Auth — owners dos repositórios — fechar P0 upstream e
  disponibilizar ambiente dev isolado antes da integração real (#13).
- Produção — Infra/Rodrigo — hardening e gate explícito; nenhuma ação autorizada (#5).

## Próxima ação

Fechar os testes e três reviews independentes da Issue #63, corrigir achados e
abrir Draft PR empilhada sem merge/deploy. Em paralelo, corrigir o P2 do gate
de capacidade da #62 e reiniciar três runs verdes no mesmo SHA. Identity,
Financial/Admin/Neuro externos e staging continuam em slices seguintes com
ambientes isolados e mudanças somente aditivas.

## Links

- Project: https://github.com/users/autonom-ia/projects/3
- Issues abertas: https://github.com/autonom-ia/appsell/issues
- PRs: https://github.com/autonom-ia/appsell/pulls
- Runbook/troubleshooting: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Health report: [doctor-20260710-125244.md](harness-health/doctor-20260710-125244.md)
