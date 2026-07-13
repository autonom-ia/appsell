# Plano Técnico Detalhado — Autonom.ia Sell

- Status: Approved direction with gates
- Data: 2026-07-10
- Issue: https://github.com/autonom-ia/appsell/issues/2
- Repositório: https://github.com/autonom-ia/appsell

## 1. Objetivo técnico

Construir um modular monolith multi-tenant, mobile-first, com isolamento por tenant, comprador global multi-produtor, conteúdo privado, integrações idempotentes e operação verificável.

O plano prioriza fronteiras seguras com os ativos internos da Autonom.ia. Reutilização ocorrerá por contrato/SDK quando a autorização estiver adequada. Código e shells de outros produtos não serão copiados indiscriminadamente.

## 2. Baseline arquitetural aprovada com gates

A direção foi aceita e registrada nos ADRs. Componentes ainda não implementados,
integrações reais e materialização de dados continuam condicionados aos spikes,
testes e aprovações de cada fase:

| Camada | Baseline proposta | Motivo |
|---|---|---|
| Monorepo | pnpm + Turborepo | alinhamento com Studio e SDKs internos |
| Web/BFF | Next.js 16, React 19 estável, TypeScript strict | mobile web, same-origin BFF e ecossistema atual |
| UI | Tailwind CSS 4 + componentes próprios | linguagem Studio sem herdar CSS monolítico |
| API de domínio | FastAPI + Python 3.12 + SQLAlchemy 2 async | padrão já operado no Studio e bom isolamento de domínio |
| Banco | PostgreSQL 16+ | constraints, RLS, transações, outbox e portabilidade |
| Cache/fila | Redis 7 + worker dedicado | jobs, rate limit e locks; outbox no Postgres como origem |
| Storage | S3 compatível privado | mídia fora da VPS, versionamento e URLs assinadas |
| Auth | Autonom.ia Identity OAuth Code + PKCE | SSO corporativo existente |
| Billing SaaS | Autonomia Financial | plano do produtor para usar o AppSell |
| Seller commerce | Checkout externo + webhook no MVP | evita expor Financial global sem escopo por operador |
| Deploy | containers imutáveis por digest | rollback e promoção do mesmo artefato |

Supabase-centric, Payload-centric e backend TypeScript único foram alternativas
consideradas. Qualquer mudança da baseline aprovada exige novo ADR; sua execução
continua condicionada aos spikes de isolamento, autoria, mídia, OAuth e build
limpo.

## 3. Topologia

```text
Internet
  -> Easypanel/Traefik + Let's Encrypt
    -> sell.autonomia.site
      -> Next.js Web/BFF
        -> AppSell API privada
          -> PostgreSQL
          -> Redis
          -> Worker / scheduler singleton
          -> S3 privado + CDN
        -> Autonom.ia Identity
        -> Autonomia Financial
        -> Autonomia Admin (somente operação corporativa autorizada)
```

Regras:

- somente Web/BFF público;
- API de domínio e serviços de dados na rede privada;
- webhook público em rota explícita, preservando raw body para assinatura;
- nenhuma chave interna em `NEXT_PUBLIC_*`;
- mídia privada fora do disco local;
- staging e produção com bancos, buckets, OAuth clients e secrets separados.

## 4. Modelo de identidade e autorização

### Identidade global

`identity_user` referencia o `sub` imutável emitido pelo Autonom.ia Identity. E-mail é atributo mutável e não é chave de autorização.

### Papéis globais

- `platform_superadmin`
- `platform_support`
- `platform_auditor`

### Memberships de tenant

- `seller_owner`
- `seller_admin`
- `seller_editor`
- `seller_support`

### Comprador

O comprador não é membership administrativa do seller. Uma identidade global pode possuir entitlements em múltiplos tenants e também ser membro administrativo de um tenant específico.

“Meus produtos” será atendido por um repositório buyer-scoped, filtrado pelo `identity_sub` da sessão. Ele agrega somente os entitlements daquele comprador e mantém `seller_organization_id` em cada linha; não usa bypass global de RLS nem troca silenciosamente o tenant ativo.

### Bootstrap do superadmin

1. validar token do issuer e audience esperados;
2. exigir `email_verified` e MFA/passkey;
3. localizar `comercial@autonomia.site` apenas no bootstrap;
4. persistir `identity_sub -> platform_superadmin`;
5. registrar actor, target, request, IP/device minimizados e timestamp;
6. marcar bootstrap como consumido;
7. remover/desabilitar a configuração de bootstrap;
8. administrar grants futuros por fluxo auditado e dupla aprovação.

### Sessão

Não usar access/refresh token em `localStorage` como sessão final.

- `state` e PKCE verifier em cookie curto, assinado e `HttpOnly`;
- troca do authorization code no BFF;
- refresh token fora do JavaScript;
- cookie host-only com `Secure`, `HttpOnly`, `SameSite=Lax` e prefixo `__Host-` quando aplicável;
- BFF same-origin injeta tokens somente server-side;
- toda mutação iniciada pelo browser exige allowlist estrita de `Origin`/`Referer` e token CSRF vinculado à sessão, ou mecanismo equivalente formalmente aprovado;
- origens irmãs sob `*.autonomia.site` não são confiadas apenas por serem same-site;
- webhooks são exceção explícita ao CSRF e usam autenticação HMAC sobre raw body;
- logout revoga sessão e limpa caches privados.

## 5. Modelo de domínio

```text
IdentityUser
  |- PlatformRole
  |- SellerMembership -> SellerOrganization
  |- BuyerProfile
       |- Entitlement -> CatalogProduct -> SellerOrganization
       |- Enrollment -> Course
       |- Progress -> Lesson

SellerOrganization
  |- Storefront
  |- CatalogProduct
       |- Offer (seller commerce plan/terms)
       |- ProductCourse -> Course
  |- Course -> Module -> Lesson -> ContentBlock
  |- Integration -> ExternalProductMapping
  |- WebhookEndpoint -> WebhookEvent
```

### Invariantes

- toda entidade privada do seller possui `seller_organization_id` inequívoco;
- `CatalogProduct` é unidade comercial; `Course` é conteúdo;
- um produto pode conceder acesso a um ou mais cursos;
- uma oferta contém preço, período e regras; o browser não decide total;
- entitlement usa, no mínimo, buyer + seller + product;
- FKs compostas garantem que entitlement, ProductCourse, enrollment e progress referenciem recursos do mesmo `seller_organization_id`;
- uniques compostas impedem duplicidade e associação cross-tenant;
- toda leitura de conteúdo privado revalida entitlement ativo do `identity_sub` para seller + product;
- draft e published são estados/versionamentos separados;
- webhook recebido é imutável; metadados de processamento ficam separados;
- idempotência é garantida por constraint no banco;
- superadmin não usa conexão comum com bypass permanente.

### Catálogo público

Rotas propostas:

- `GET /public/sellers/:sellerSlug`
- `GET /public/sellers/:sellerSlug/products`
- `GET /public/sellers/:sellerSlug/products/:productSlug`

Só retornam seller ativo e produto `published`, ativo e publicamente vendável. Nunca retornam draft, arquivos privados, compradores, pedidos, métricas internas ou PII.

## 6. Fronteiras com sistemas existentes

### Studio

Usar como fonte de:

- paleta;
- tipografia;
- superfícies e sombras;
- princípios de hierarquia;
- referências de Docker/health check.

Não usar como dependência de runtime do UI. Criar subset próprio com tokens versionados, touch targets de 44 px e texto-base de 16 px.

### Identity

Reutilizar OAuth Code + PKCE e allowlist de callback. Registrar:

- `client_id`: `appsell-web`;
- callback produção: `https://sell.autonomia.site/auth/callback`;
- logout produção: `https://sell.autonomia.site/auth/login`;
- callbacks/origins explícitos de localhost e staging.

### Admin

Usar apenas para registrar o AppSell como produto corporativo, branding e OAuth client.

Bloqueador confirmado: o backend atual concede permissões administrativas globais para qualquer JWT válido e cria vínculos privilegiados por padrão. Não expor `AdminWorkspace` ou rotas Admin a sellers/buyers antes de hardening próprio.

### Financial

Uso inicial:

- catálogo corporativo AppSell;
- planos SaaS pagos pelo seller à Autonom.ia;
- entitlements/limites da plataforma.

Não usar para seller commerce até existir autorização `principal organization -> operator`, APIs seller-scoped e testes cross-operator. Nunca aceitar `operator_id` arbitrário vindo do browser.

## 7. Orquestração dinâmica

Cada Issue é decomposta pelo orquestrador conforme risco e dependências.

### Papéis de agentes

| Papel | Responsabilidade | Pode editar? |
|---|---|---|
| Orchestrator | dependências, contratos, gates e síntese | somente mudanças coordenadas |
| Investigator | localizar contratos, riscos e evidência | não |
| Builder | implementação pequena e delimitada | sim, arquivos atribuídos |
| Domain reviewer | invariantes e regras de negócio | não |
| Security reviewer | auth, tenancy, secrets, uploads, webhooks | não |
| Mobile UX reviewer | jornadas, rede lenta, estado e touch | não |
| Accessibility reviewer | teclado, foco, contraste, semântica | não |
| Test reviewer | qualidade e realidade dos testes | não |
| Release checker | artefato, deploy, rollback e evidência | não |

### Regras

1. Investigações independentes podem ocorrer em paralelo.
2. Um builder possui cada conjunto de arquivos/contrato.
3. O autor não aprova o próprio trabalho.
4. Auth, tenancy, billing, migrations e deploy sempre recebem revisão dedicada.
5. Nenhum agente declara sucesso sem comando e saída real.
6. Mock não certifica integração externa.
7. O próximo bloco começa somente após o gate da fase anterior.
8. Toda PR vincula Issue, Project, evidências e rollback.
9. Merge e produção dependem de aprovação explícita do Rodrigo.

## 8. Fases técnicas

### Fase 0 — Fundação operacional

Entregas:

- repositório privado;
- Agent Harness;
- templates, CI e Project;
- pacote documental importado/reconciliado;
- inventário de fontes e SHAs.

Revisores:

- Harness/process reviewer;
- security reviewer dos hooks/CI.

Testes/gate:

- clone limpo;
- validator/Doctor sem erro;
- hooks com regressões reais;
- secret scan redigido;
- CI verde;
- PR pequena e sem produto.

Rollback: reverter commit de instalação do Harness.

### Fase 1 — Discovery e ADRs

Entregas:

- ADR stack;
- ADR multitenancy/auth;
- ADR conteúdo/mídia;
- ADR jobs/integrações;
- threat model;
- auditoria de licenças/vulnerabilidades;
- ERD e matriz de autorização;
- protótipo de contratos com Identity, Admin e Financial.

Spikes reais:

- OAuth Code + PKCE com cliente dev;
- capacidade real de MFA/passkey para o cliente AppSell e bootstrap de superadmin;
- dois sellers e tentativa de acesso cruzado;
- buyer com entitlement em dois sellers;
- draft/published;
- asset privado com URL assinada;
- webhook idempotente;
- build/instalação limpos.

Revisores: arquiteto, auth, tenancy, AppSec, licença e representantes dos serviços internos.

Gate: ADRs aprovadas pelo Rodrigo; nenhuma vulnerabilidade crítica sem plano aceito; superadmin de produção bloqueado até autenticação forte ser comprovada ou uma alternativa formal ser aprovada.

### Fase 2 — Design system e protótipo mobile

Entregas:

- tokens derivados do Studio;
- primitives e componentes base;
- shell produtor;
- shell comprador;
- protótipos das jornadas de ouro;
- contrato de eventos analíticos.

Testes reais:

- 360, 380, 390, 768, 1024 e desktop;
- iOS Safari e Android Chrome reais;
- teclado e screen reader;
- zoom 200%;
- reduced motion;
- rede lenta/intermitente;
- ausência de scroll horizontal;
- estado preservado em back/reload.

Revisores: mobile journey, acessibilidade e design system.

Gate: nenhum bloqueio crítico nas jornadas e protótipo aprovado pelo Product Owner.

### Fase 3 — Foundation executável

Entregas:

- scaffold e containers;
- migrations;
- OAuth/BFF/session;
- organizações e memberships;
- bootstrap superadmin;
- tenant context;
- RLS/defesa no banco;
- audit log;
- observabilidade básica.

Testes reais:

- OAuth completo, state, PKCE, callback, refresh, logout e replay;
- mutações com Origin/Referer ausente, externo ou de subdomínio irmão são negadas sem token CSRF válido;
- token CSRF de outra sessão é negado;
- e-mail não verificado e callback fora da allowlist;
- bootstrap único/idempotente;
- mudança de e-mail não altera papel;
- seller A não lê/escreve B;
- buyer não acessa rota administrativa;
- migrations forward-only/expand-contract em banco vazio e vindo da versão anterior;
- lint, typecheck, unit, integração e build.

Revisores: auth, tenancy adversarial, banco/migration e DevOps.

Gate: zero acesso cruzado e sessão server-side certificada.

### Fase 4 — Portal, produto e autoria

Entregas:

- Storefront/branding;
- CatalogProduct, Offer e ProductCourse;
- Course/Module/Lesson;
- editor de blocos;
- autosave e proteção contra conflito/perda;
- preview e publish snapshot;
- storage privado.

Testes reais:

- criar -> editar -> preview -> publicar;
- draft nunca aparece no público;
- produto de A nunca aparece na vitrine de B;
- upload com MIME falso, SVG, executável, excesso de tamanho e traversal;
- URL assinada expira;
- storage indisponível não corrompe metadados;
- reordenação funciona sem drag/mouse;
- interrupção de rede não perde edição confirmada.

Revisores: conteúdo, storage/AppSec, mobile authoring e acessibilidade.

Gate: jornada de publicação verde em celular e desktop.

### Fase 5 — Comprador e aprendizagem

Entregas:

- BuyerProfile;
- convite/matrícula/revogação;
- catálogo multi-seller;
- “outros produtos deste produtor”;
- player;
- progresso e retomada;
- importação/exportação.

Testes reais:

- buyer acessa produtos de A e B;
- “Meus produtos” agrega somente entitlements do `identity_sub` autenticado, mantendo o seller de cada item;
- buyer não acessa produto sem entitlement;
- tentativa de criar ProductCourse, enrollment, entitlement ou progress com IDs de sellers diferentes falha por aplicação e constraint;
- revogação bloqueia conforme regra aprovada;
- progresso é idempotente e não altera terceiro;
- vitrine só mostra published do seller correto;
- CSV registra erro por linha e pode ser retomado;
- E2E em dispositivos móveis reais.

Revisores: learning UX, authorization, accessibility e QA cross-device.

Gate: convite/compra -> login -> consumo -> conclusão -> retomada verde.

### Fase 6 — Comércio e integrações

Entregas:

- integração SaaS com Admin/Financial;
- external product mapping;
- webhook genérico;
- outbox, worker, retry e dead-letter;
- estados determinísticos de compra/revogação.

Testes reais:

- contract tests com serviços reais em dev/staging;
- assinatura inválida;
- replay e duplicata;
- evento fora de ordem;
- timeout/retry;
- refund, cancel e chargeback;
- reinício do worker no meio da operação;
- compatibilidade N/N-1 de schema, API, worker e versões de evento/outbox;
- falha externa não remove entitlement válido já persistido;
- preço sempre recalculado server-side.

Revisores: Financial, Admin/Neuro, webhook security e contract testing.

Gate: duplicatas não geram cobrança, pedido ou entitlement duplicado.

### Fase 7 — Infra, PWA e hardening

Entregas:

- staging/prod separados;
- TLS válido;
- imagens imutáveis;
- health/readiness;
- service worker seguro;
- backups e PITR;
- observabilidade/alertas;
- runbooks e LGPD;
- rollout/rollback.

Testes reais:

- smoke no host real;
- inspeção do cache após login/logout;
- conteúdo privado ausente de cache público;
- load test com margem de 2× sobre meta aprovada;
- restore em banco descartável;
- rollback para digest anterior;
- rollback para worker N-1 depois de eventos da versão N já terem sido persistidos;
- DAST e scan de container/SBOM;
- simulação de indisponibilidade de banco, Redis, storage e serviço externo.

Revisores: SRE, AppSec, PWA/cache, performance, backup e LGPD.

Gate: zero finding crítica; restore e rollback executados.

### Fase 8 — Piloto controlado

Entregas:

- três tenants;
- onboarding assistido;
- instrumentação e suporte;
- relatório de fricções e incidentes;
- decisão go/no-go.

Testes/gate:

- três produtores publicam sem intervenção no banco;
- 3 de 3 produtores completam publicação e matrícula sem intervenção em código ou banco;
- pelo menos 90% das tarefas roteirizadas são concluídas sem ajuda operacional;
- 3 de 3 buyers concluem login, consumo, progresso e retomada no celular;
- nenhuma falha de isolamento;
- golden paths 100% verdes no artefato candidato;
- conjunto de certificação de webhooks termina 100% no estado esperado;
- exportação funciona;
- restore e rollback são executados com sucesso;
- pelo menos 2 de 3 produtores confirmam continuidade do piloto;
- incidentes possuem owner e resolução;
- aprovação executiva antes de venda aberta.

## 9. Pipeline CI/CD

### Toda PR

- lockfile congelado;
- lint e formatter check bloqueantes;
- typecheck;
- unit tests;
- integração com PostgreSQL/Redis efêmeros;
- migrations em banco vazio e versão anterior;
- testes negativos de tenancy;
- build de produção;
- build de containers;
- secret scan redigido;
- dependency/license audit;
- SAST e scan de imagem;
- SBOM;
- E2E relevante;
- revisão independente.

### Promoção

1. construir uma vez;
2. publicar imagem no GHCR com SHA/digest;
3. assinar e armazenar proveniência/SBOM;
4. implantar automaticamente em staging;
5. executar smoke, E2E, integração real, DAST e carga;
6. vincular evidência ao SHA/digest;
7. promover o mesmo digest para produção após aprovação no GitHub Environment.

Não copiar do Studio:

- lint com `|| true`;
- deploy automático de produção em push para `main`;
- tag mutável `latest`;
- webhook de deploy que falha e deixa workflow verde;
- atualização de SDK para `latest` durante deploy.

## 10. Infra atual e gates

Confirmado em 2026-07-10:

- `sell.autonomia.site` resolve para `85.31.60.100`;
- HTTP redireciona para HTTPS;
- HTTPS apresenta certificado autoassinado `CN=Easypanel`;
- a resposta atual é `404` padrão do Easypanel;
- TLS 1.2 e 1.3 negociam;
- não houve alteração nem varredura de portas.

Gates P0 antes de staging/produção:

1. confirmar acesso e responsabilidade sobre `85.31.60.100`;
2. medir CPU, RAM, disco, IOPS, espaço Docker e workloads existentes;
3. aprovar `sell-staging.autonomia.site`;
4. criar rota no Easypanel e emitir certificado válido;
5. escolher storage/CDN;
6. separar redes, dados e secrets por ambiente;
7. configurar branch protection e GitHub Environments;
8. executar restore real;
9. registrar OAuth clients separados;
10. liberar staging antes de qualquer produção.

## 11. Backup, observabilidade e rollback

Metas iniciais propostas:

- RPO: 15 minutos;
- RTO: 2 horas;
- PITR/WAL ou equivalente;
- dump diário criptografado e off-host;
- storage versionado;
- restore mensal real.

Logs JSON devem conter `request_id`, `job_id`, actor e tenant opacos, operação, resultado e error code. Não registrar tokens, conteúdo de aula ou PII desnecessária.

Rollback:

- aplicação retorna ao digest anterior;
- migrations são forward-only e seguem expand/contract; rollback de aplicação não depende de `down` destrutivo;
- eventos e mensagens possuem versão explícita;
- API, schema e workers mantêm compatibilidade N/N-1 durante a janela de rollout;
- o teste de rollback inclui eventos/outbox persistidos pela versão nova antes de reativar o worker anterior;
- corrupção de dados restaura em banco novo antes de cutover;
- callbacks OAuth atual e anterior coexistem na janela de transição;
- incidente recebe postmortem em até 24 horas.

## 12. Bloqueadores conhecidos

Os itens 1 a 6 abaixo são snapshots de discovery registrados em 2026-07-10 e
precisam ser reverificados nos repositórios de origem antes de orientar uma
implementação. Não descrevem o estado atual certificado do AppSell.

1. Admin backend atual tem autorização global insegura para sellers/buyers.
2. Financial admin não possui escopo seguro por operator/organization.
3. O snapshot do Financial apresentou 16 vulnerabilidades no install audit,
   incluindo quatro críticas, concentradas também em tooling/dev; o alcance em
   runtime e o estado atual do repositório não foram certificados nesta Issue.
4. Identity Auth não possui suíte automatizada real; build sozinho não certifica OAuth.
5. Admin API tem testes condicionais a `DATABASE_URL` e não cobre tenancy/permissão.
6. Studio não possui pacote UI reutilizável pronto.
7. TLS do domínio está inválido.
8. Capacidade e blast radius do host receberam medição read-only na Issue #5,
   mas a adequação para AppSell e o risco operacional não foram aprovados nem
   encerrados.
9. Política comercial de cancelamento/refund/chargeback ainda precisa de decisão.
10. AppSell ainda não existe como opção no campo `Projeto` do Project central.

Nenhuma fase deve esconder esses bloqueios atrás de mocks ou builds verdes.
