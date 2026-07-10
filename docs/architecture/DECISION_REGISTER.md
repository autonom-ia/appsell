# Registro de decisões e pendências

- Status: Active — approved direction with unresolved gates
- Atualização: 2026-07-10
- Regra: ausência de decisão de segurança falha fechada

## Estados

- `Fixed by approved direction`: requisito já definido pelo plano/direção e que o
  ADR apenas formaliza.
- `Verified local slice`: direção aprovada e comprovada somente em Local/CI;
  não certifica integração externa, staging ou produção.
- `Pending ADR approval`: proposta pronta, aguardando gate do Rodrigo.
- `Pending spike`: depende de evidência técnica real.
- `Pending business/legal`: depende de decisão não técnica.
- `Blocked`: impede a fase indicada.

## Registro

| ID | Decisão | Estado | Owner(s) | Gate | Default se não resolvida |
|---|---|---|---|---|---|
| D-001 | Nome operacional `Autonom.ia Sell` | Fixed by approved direction | Rodrigo | protótipo | usar apenas nome interno, sem lançamento de marca |
| D-002 | Stack do ADR 0001 | Fixed by approved direction | Rodrigo + Arquitetura | scaffold | slices não verificados continuam bloqueados |
| D-003 | Sessão opaca HttpOnly/BFF; zero JWT no browser | Verified local slice; Identity real blocked (#13) | Identity + AppSec | Foundation | login real bloqueado |
| D-004 | Bootstrap `comercial@autonomia.site` one-shot, verificado, por `sub` | Fixed by approved direction | Rodrigo + Identity | Foundation | nenhum superadmin criado |
| D-005 | Assurance/MFA/passkey disponível no Identity | Pending spike / Blocked | Identity + AppSec | superadmin e produção | bootstrap de produção bloqueado |
| D-006 | RBAC e matriz desta Issue | Fixed by approved direction | Produto + AppSec | Foundation | deny all não explicitamente permitido |
| D-007 | Tenant compartilhado com RLS/FKs compostas | Fixed by approved direction; migration pending | Arquitetura + Banco | primeira migration | nenhuma tabela privada liberada |
| D-008 | Buyer global com entitlements multi-seller | Fixed by approved direction | Produto + Arquitetura | buyer journey | sem `/me/products` multi-seller até spike A+B |
| D-009 | “Outros produtos” limitado ao seller selecionado | Fixed by approved direction | Produto | catálogo | não exibir recomendações |
| D-010 | Checkout externo no seller commerce; Financial só SaaS | Fixed by approved direction | Rodrigo + Financial | comércio | seller commerce no Financial bloqueado |
| D-011 | Hardening do Financial por operator | Blocked fora do AppSell | Financial + AppSec | checkout nativo futuro | nenhum CRUD seller ou `operator_id` do browser |
| D-012 | Fornecedor/região/storage/CDN | Pending business/legal | Rodrigo + Infra + LGPD | upload spike | usar apenas storage dev descartável; sem dado real |
| D-013 | Tipos/tamanho/retention de assets | Pending business/legal | Produto + LGPD + Infra | autoria | allowlist mínima; SVG/HTML/executáveis negados |
| D-014 | Política após produto/curso arquivado | Pending business | Rodrigo + Produto | publicação | preservar entitlement; impedir nova venda; sem apagar conteúdo |
| D-015 | Refund/cancel/chargeback e grace period | Pending business/legal | Rodrigo + Financeiro | webhook entitlement | evento registrado, efeito ambíguo vai para revisão; não conceder/reativar |
| D-016 | Preço público e moeda no MVP | Pending business | Rodrigo + Produto | oferta/vitrine | não apresentar promessa de cobrança nativa |
| D-017 | Política de suporte/impersonation | Pending business/security | Rodrigo + AppSec | suporte | impersonation indisponível |
| D-018 | Retenção, exportação, exclusão e base legal LGPD | Pending business/legal | DPO/Jurídico + Rodrigo | piloto | dados reais/onboarding bloqueados |
| D-019 | Política final de licenças | Pending business/legal | Jurídico + Engenharia | scaffold merge | licenças ambíguas/copyleft bloqueadas |
| D-020 | UUIDv7 versus UUIDv4 | Pending ADR approval | Banco + Arquitetura | primeira migration | UUIDv4 não sequencial como opção conservadora |
| D-021 | RPO 15 min / RTO 2 h e custo | Pending business/infra | Rodrigo + Infra | staging | sem SLA declarado; produção bloqueada |
| D-022 | `sell-staging.autonomia.site` | Pending business/infra | Rodrigo + Infra | staging | usar ambiente local/dev sem DNS |
| D-023 | Três tenants e critérios de piloto | Pending ADR approval | Rodrigo + Produto | piloto | não iniciar piloto |
| D-024 | PWA após certificação mobile web | Fixed by approved direction | Produto + AppSec | hardening | service worker privado desabilitado |
| D-025 | Cliente/callback/claims exatos do Identity | Blocked (#13; Identity Foundation #13; Identity Auth #13/#14) | Identity | Foundation | integração OAuth não liberada |
| D-026 | Contrato SaaS Admin/Financial | Pending spike | Admin + Financial | comércio SaaS | mock não habilita billing; nenhum billing real |
| D-027 | Política de entitlement manual/importação | Pending business | Produto + Operação | buyer journey | somente dados sintéticos; ação real bloqueada |
| D-028 | Métrica monotônica/reset de progresso | Pending business | Produto | player | update idempotente e sem reduzir progresso |
| D-029 | Provedor de e-mail/convites | Pending business/legal | Produto + Infra + LGPD | convite | não enviar para usuários reais |
| D-030 | Analytics/cookies/consentimento | Pending business/legal | Produto + LGPD | instrumentação | somente telemetria operacional necessária, sem marketing tracker |
| D-031 | Topologia de Postgres/Redis isolados | Redis Local/CI verified (#14); staging design pending | Infra + Banco | staging | não criar/tocar recursos na VPS/produção |
| D-032 | Certificado/rota/capacidade de `sell.autonomia.site` | Blocked fora desta Issue | Infra | produção | produção e DNS bloqueados |
| D-033 | Envelope encryption Local/CI com DEK por registro e keyring local | Verified local slice; merged (#17/#20) | AppSec + Auth | vault local | provider local proibido em produção |
| D-034 | Refresh distribuído com lease/fencing/CAS | Pending spike; local implementation/review in progress (#18), Identity real blocked (#13) | Identity + AppSec | refresh | sem refresh real |
| D-035 | Provider KMS real, vault durável/HA e restore | Blocked (#19) | Infra + AppSec | staging | provider local proibido em produção |

## Spikes obrigatórios antes da Foundation

| Spike | Critério de sucesso | Reviewer |
|---|---|---|
| OAuth/BFF | login, refresh/revogação/logout reais; tokens ausentes do browser | Identity + AppSec |
| Bootstrap | unverified/wrong aud/replay negados; one-shot concorrente | Identity + AppSec |
| Seller A/B | API, repository, RLS e SQL impedem acesso cruzado | Tenancy + Banco |
| Buyer A+B | `/me/products` agrega apenas o buyer autenticado e preserva seller | Authorization reviewer |
| Draft/published | draft nunca aparece; snapshot/rollback editorial funcionam | Conteúdo reviewer |
| Asset privado | upload adversarial, auth, expiração e cache/logout | Storage/AppSec |
| Webhook | assinatura, duplicata concorrente, replay, ordem e crash | Integrações reviewer |
| Build limpo | installs congelados, testes/build, SBOM/licenças e containers | Supply-chain reviewer |

## Gate de aprovação desta Issue

Rodrigo aprovou a direção dos ADRs 0001–0005 com gates. Itens `Pending
business/legal` podem permanecer abertos apenas se a fase correspondente ficar
bloqueada pelo default descrito. Itens `Blocked` não podem ser reclassificados
sem evidência e owner explícitos.

Esta aprovação não autoriza merge, produção, VPS, DNS, secrets, billing real ou
dados de clientes.
