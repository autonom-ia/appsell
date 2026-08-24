# Plano Executivo — Autonom.ia Sell

> **Leitura atual:** este arquivo registra a direção executiva e o backlog de
> evolução. O catálogo multi-tenant, publicação e acessos gratuito/pago já
> possuem release real verificado; consulte
> [`docs/PRODUCTION_CURRENT.md`](../PRODUCTION_CURRENT.md) para o estado atual.

- Status: Approved direction with gates
- Data: 2026-07-10
- Product Owner: Rodrigo / Autonom.ia
- Issue: https://github.com/autonom-ia/appsell/issues/2
- Domínio operacional: `sell.autonomia.site`

## 1. Decisão executiva solicitada

Aprovar a construção de um web app SaaS multi-tenant, mobile-first, para produtores, mentores e especialistas criarem produtos digitais, organizarem cursos, integrarem vendas realizadas em checkouts externos e entregarem conteúdo aos compradores com sua própria marca.

O produto operacional será chamado de **Autonom.ia Sell** neste ciclo. O nome comercial definitivo continua pendente.

Recomendação: **aprovar a direção com gates**, não aprovar ainda um cronograma fechado ou lançamento público. Os riscos técnicos mais relevantes precisam ser removidos nas fases de discovery e foundation.

## 2. O que estamos construindo

### Para o produtor/admin

- criar sua organização/tenant;
- configurar identidade visual e vitrine;
- criar produtos, ofertas e planos;
- associar produtos a um ou mais cursos;
- publicar aulas e materiais;
- cadastrar, importar, matricular e revogar compradores;
- integrar vendas por webhook;
- acompanhar acesso, progresso e falhas operacionais.

### Para o comprador

- usar uma identidade única;
- comprar em checkout externo e receber acesso de diferentes produtores;
- ver os produtos adquiridos em múltiplos tenants;
- descobrir outros produtos públicos do mesmo produtor;
- consumir conteúdo no celular com retomada de progresso;
- instalar a experiência como PWA quando o mobile web estiver certificado.

### Para a Autonom.ia

- operar tenants, planos SaaS e limites;
- auditar ações críticas;
- suspender e reativar tenants;
- diagnosticar falhas sem acessar conteúdo indevidamente;
- manter identidade, billing SaaS e catálogo corporativo integrados ao ecossistema atual.

## 3. Modelo de papéis proposto como baseline

| Papel | Escopo | Regra central |
|---|---|---|
| `platform_superadmin` | Plataforma | Administração global separada e auditada |
| `seller_owner` | Tenant produtor | Dono da organização e das regras comerciais |
| `seller_admin` | Tenant produtor | Operação de produtos, compradores e integrações |
| `seller_editor` | Tenant produtor | Conteúdo, sem poderes financeiros/globais |
| `seller_support` | Tenant produtor | Suporte restrito e auditado |
| `buyer` | Identidade global | Pode possuir acessos em múltiplos produtores |

`comercial@autonomia.site` será usado somente no bootstrap inicial do `platform_superadmin`. Após o primeiro vínculo, a autorização será baseada no identificador imutável do usuário no Autonom.ia Identity, e não em comparação permanente de e-mail.

O bootstrap deverá exigir e-mail verificado, issuer/audience corretos, auditoria e consumo único. MFA/passkey será gate obrigatório para superadmin em produção se a capacidade for confirmada no spike do Identity; não será declarada disponível antes desse teste real.

## 4. Separação comercial obrigatória

Existem dois produtos financeiros diferentes:

1. **Plano SaaS do AppSell:** o produtor paga à Autonom.ia para usar a plataforma. Deve usar Autonom.ia Admin/Financial.
2. **Oferta do produtor:** o comprador paga por um produto/plano de um tenant. Pertence ao domínio do AppSell e ao produtor.

O Admin e o Financial atuais não possuem autorização segura para expor CRUD global diretamente aos produtores. Portanto:

- o Admin cadastra somente o AppSell como produto corporativo da Autonom.ia;
- o Financial cobra inicialmente o SaaS do AppSell;
- produtos, ofertas, cursos e entitlements dos produtores ficam no AppSell;
- no MVP recomendado, vendas do produtor entram por checkout externo + webhook idempotente;
- checkout nativo multi-produtor exige uma fase própria de hardening por operador, gateway, reembolso, chargeback e segregação financeira.

Isso preserva a possibilidade futura de checkout nativo sem transformar o MVP, silenciosamente, em marketplace financeiro com split ou custódia.

## 5. Posicionamento recomendado

### Promessa

> Crie e entregue produtos digitais com sua marca; integre as vendas feitas no checkout que você já usa.

### ICP inicial

Produtores, mentores e consultores que:

- já vendem ou conseguem vender por checkout externo;
- possuem catálogo pequeno ou médio;
- valorizam marca própria e portabilidade;
- precisam de uma área de membros melhor no celular;
- aceitam participar de um piloto assistido.

Agências, franquias, escolas grandes e treinamento corporativo permanecem como expansão. Atendê-los no primeiro MVP ampliaria demais permissões, reporting, hierarquias e integrações.

## 6. Princípios de experiência

O design seguirá a linguagem do Autonom.ia Studio — azul profundo, superfícies leves, tipografia clara e baixo ruído — mas terá componentes próprios e auditados.

O Studio atual é referência visual, não pacote UI pronto. O AppSell deverá cumprir:

- touch targets mínimos de 44 × 44 px;
- texto-base de 16 px no celular;
- contraste WCAG AA;
- navegação principal com até cinco destinos;
- nenhum fluxo crítico dependente apenas de hover, swipe ou drag;
- safe areas, zoom, landscape e reduced motion;
- preservação de estado ao voltar, trocar de rede ou interromper uma edição;
- estados claros de loading, vazio, erro, offline e recuperação.

### Navegação do produtor

- Início
- Conteúdo
- Compradores
- Operação
- Mais

### Navegação do comprador

- Continuar
- Meus produtos
- Vitrine do produtor
- Perfil

“Vitrine do produtor” sempre opera dentro do contexto de um seller selecionado; não existe descoberta global ou marketplace no MVP. O player do comprador é a tela central do produto. O painel do produtor e a experiência do comprador serão superfícies distintas.

## 7. Escopo executivo do MVP

### Entra

- OAuth central da Autonom.ia;
- bootstrap seguro do superadmin;
- organizações, memberships e RBAC;
- isolamento multi-tenant;
- um portal operável por tenant na interface inicial;
- branding básico;
- produtos, ofertas, cursos, módulos e aulas;
- texto, vídeo externo, imagem, PDF/arquivo e link;
- draft, preview, publicação e autosave;
- comprador global com acesso em múltiplos tenants;
- matrícula, revogação e convite;
- catálogo público por produtor;
- outros produtos publicados do mesmo produtor;
- player, progresso e retomada;
- webhook genérico idempotente;
- billing SaaS integrado ao Financial;
- auditoria e exportação;
- experiência mobile-first;
- PWA segura antes do piloto final.

### Fica fora do MVP

- marketplace público global;
- split, comissão, repasse ou custódia;
- afiliados;
- app nativo;
- streaming/transcodificação próprios;
- DRM;
- domínio customizado por tenant;
- múltiplos conectores específicos de checkout;
- dark mode;
- comunidade/feed/chat social;
- gamificação e certificados avançados;
- SCORM/LTI;
- IA tutora ou geração integral de curso.

## 8. Fases executivas

| Fase | Resultado executivo | Gate de saída |
|---|---|---|
| 0. Governança | Repo, Harness, Issues, PRs e Project funcionando | CI verde e revisão independente |
| 1. Discovery | Arquitetura, modelo, ameaças, licenças e jornadas aprovadas | ADRs aprovadas por Rodrigo |
| 2. Protótipo mobile | Design system e jornadas navegáveis | Testes com usuários do piloto sem bloqueios críticos |
| 3. Foundation | Login, organizações, papéis e isolamento | Nenhum acesso cruzado; OAuth real funcionando |
| 4. Autoria | Vitrine, produto, curso, aulas e publicação | Jornada criar → publicar verde |
| 5. Comprador | Catálogo, matrícula, player, progresso e retomada | Jornada convite/compra → consumo verde no celular |
| 6. Comércio | Webhooks, billing SaaS e integrações internas | Duplicata/replay sem cobrança ou acesso duplicado |
| 7. Hardening | Infra, TLS, backup, PWA, observabilidade e LGPD | Restore e rollback executados; zero finding crítica |
| 8. Piloto | Três tenants operando com dados controlados | Limiar de piloto atendido e go/no-go documentado |

## 9. Métricas de decisão

### Ativação

- organização criada;
- portal configurado;
- primeiro produto publicado;
- primeira oferta publicada;
- primeiro comprador matriculado;
- primeiro acesso do comprador.

### Experiência

- tempo até primeira publicação;
- sucesso sem suporte nas jornadas de ouro;
- abandono por etapa;
- falha de convite/login;
- retomada de aula;
- erros mobile por dispositivo/navegador.

### Operação

- incidentes de autorização;
- webhooks processados, repetidos e falhos;
- duplicidade de entitlement;
- falhas de importação;
- latência p95;
- backlog de fila;
- sucesso de backup/restore.

Metas comerciais de conversão, retenção, CAC e receita serão definidas depois da instrumentação e do primeiro piloto. Fixá-las agora seria inventar precisão.

### Limiares propostos para o go/no-go do piloto

- 3 de 3 produtores concluem organização -> produto -> publicação -> matrícula sem intervenção em código ou banco;
- pelo menos 90% das tarefas roteirizadas das jornadas de ouro são concluídas sem ajuda operacional;
- 3 de 3 compradores concluem login -> consumo -> progresso -> retomada em dispositivo móvel suportado;
- zero vazamento ou enumeração cross-tenant;
- zero incidente crítico de autorização, cobrança ou perda de conteúdo;
- 100% dos webhooks do conjunto de certificação terminam em estado esperado, incluindo duplicata, replay, cancelamento e chargeback;
- restore e rollback são executados com sucesso;
- pelo menos 2 de 3 produtores confirmam que desejam continuar no piloto após usar o fluxo completo.

O tempo-alvo de primeira publicação será fixado ao final do protótipo, depois de medir o fluxo com conteúdo pré-preparado. Isso impede inventar um número agora e também impede mover a régua depois do início do piloto.

## 10. Riscos executivos

| Risco | Impacto | Decisão/mitigação |
|---|---|---|
| Vazamento entre produtores | Crítico | isolamento no app + banco + testes negativos |
| Admin atual com privilégio global | Crítico | não expor aos produtores; remediar antes de qualquer uso |
| Financial sem escopo por operador | Crítico | billing SaaS primeiro; seller commerce externo no MVP |
| Escopo virar clone da Hotmart | Alto | fora de escopo e gates por fase |
| Mobile virar desktop espremido | Alto | design system próprio e revisão mobile independente |
| TLS/host não pronto | Alto | produção bloqueada até certificado e capacidade comprovados |
| Studio ser copiado como pacote | Alto | usar somente linguagem/tokens; componentes próprios |
| PWA cachear conteúdo privado | Alto | shell público apenas; APIs privadas sem cache-first |
| Dependências vulneráveis | Alto | reverificar o snapshot de 16 vulnerabilidades do Financial, incluindo tooling/dev; o estado atual não foi certificado |
| ICP amplo demais | Alto | piloto focado em produtores/mentores |

## 11. Decisões operacionais ainda pendentes

Rodrigo aprovou a direção executiva e a execução por gates nesta tarefa. As
escolhas abaixo permanecem pendentes quando exigem decisão operacional,
financeira, jurídica ou de infraestrutura específica.

Direções já aprovadas nesta tarefa:

- [x] **Autonom.ia Sell** como nome operacional, não marca definitiva.
- [x] Produtores/mentores com checkout existente como ICP do piloto.
- [x] Checkout externo + webhook como seller commerce do MVP.
- [x] Admin/Financial somente para produto e billing SaaS no MVP.
- [x] `comercial@autonomia.site` como bootstrap one-shot do superadmin.
- [x] `sell.autonomia.site` como domínio do piloto.
- [x] PWA após certificação do mobile web, sem atrasar a primeira validação.

Decisões operacionais ainda pendentes:

- [ ] Aprovar `sell-staging.autonomia.site` como ambiente separado.
- [ ] Aprovar três tenants no piloto controlado.
- [ ] Aprovar os limiares objetivos de go/no-go descritos neste plano.
- [ ] Aprovar RPO de 15 minutos e RTO de 2 horas como metas iniciais sujeitas a custo.

## 12. O que esta aprovação autoriza

A aprovação deste plano autoriza:

- concluir discovery e ADRs;
- criar protótipos e spikes descartáveis;
- abrir Issues, branches e PRs pequenas;
- executar testes em local/dev/staging sem dados de cliente.

Não autoriza:

- merge automático;
- deploy em produção;
- alteração de DNS;
- cadastro de secrets;
- migrations em produção;
- ativação de billing real;
- onboarding de clientes reais;
- criação de split, repasse ou custódia.

Cada uma dessas ações continuará exigindo o gate operacional correspondente.
