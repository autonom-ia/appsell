# Registro de decisões e pendências

- Status: Active — approved direction; current catalog slice verified in production
- Atualização: 2026-09-01
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
| D-003 | Sessão opaca HttpOnly/BFF; zero JWT no browser | Verified in current production slice | Identity + AppSec | Foundation | manter deny-by-default |
| D-004 | Bootstrap `comercial@autonomia.site` one-shot, verificado, por `sub` | Verified in current production grant | Rodrigo + Identity | Foundation | não criar novo grant manual fora do fluxo |
| D-005 | Assurance/MFA/passkey disponível no Identity | Pending broader assurance review | Identity + AppSec | future auth hardening | não ampliar privilégios sem step-up/capability |
| D-006 | RBAC e matriz desta Issue | Fixed by approved direction | Produto + AppSec | Foundation | deny all não explicitamente permitido |
| D-007 | Tenant compartilhado com RLS/FKs compostas | Verified in production head `0032` | Arquitetura + Banco | future schema changes | toda mudança mantém FK/RLS e teste negativo |
| D-008 | Buyer global com entitlements multi-seller | Verified in current buyer journey | Produto + Arquitetura | future buyer expansion | cada entitlement permanece seller-scoped |
| D-009 | “Outros produtos” limitado ao seller selecionado | Fixed by approved direction | Produto | catálogo | não exibir recomendações |
| D-010 | Checkout externo no seller commerce; Financial só SaaS | Current direction; paid checkout remains separately gated | Rodrigo + Financial | billing expansion | não ativar cobrança nova sem contrato/webhook/rollback |
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
| D-025 | Cliente/callback/claims exatos do Identity | Current production identity verified; broader provider changes pending | Identity | auth changes | não alterar claims/callback sem smoke real |
| D-026 | Contrato SaaS Admin/Financial | Pending spike | Admin + Financial | comércio SaaS | mock não habilita billing; nenhum billing real |
| D-027 | Política de entitlement manual/importação | Pending business | Produto + Operação | buyer journey | somente dados sintéticos; ação real bloqueada |
| D-028 | Métrica monotônica/reset de progresso | Pending business | Produto | player | update idempotente e sem reduzir progresso |
| D-029 | Provedor de e-mail/convites | Decided 2026-09-01 (#355): SMTP Hostinger por `sell@autonomia.site`, separado do comercial; natureza, rodapé e descadastro em [EMAIL_POLICY.md](EMAIL_POLICY.md) | Produto + Infra + LGPD | convite | só transacional sai; opcional exige consentimento registrado, remetente próprio e descadastro em um clique (requisitos da #356: sem variáveis, o canal não envia e não cai no comercial) |
| D-030 | Analytics/cookies/consentimento | Pending business/legal | Produto + LGPD | instrumentação | somente telemetria operacional necessária, sem marketing tracker |
| D-031 | Topologia de Postgres/Redis isolados | Production VPS topology verified for current release | Infra + Banco | future infra changes | não reutilizar banco/secret/rede sem inventário |
| D-032 | Certificado/rota/capacidade de `sell.autonomia.site` | Verified for current release on VPS | Infra | future capacity/DNS changes | nova alteração exige smoke e rollback |
| D-033 | Envelope encryption Local/CI com DEK por registro e keyring local | Verified local slice; merged (#17/#20) | AppSec + Auth | vault local | provider local proibido em produção |
| D-034 | Refresh distribuído com lease/fencing/CAS | Future hardening; não foi alterado pelo release atual | Identity + AppSec | refresh changes | não alterar sessão distribuída sem smoke real |
| D-035 | Provider KMS real, vault durável/HA e restore | Pending broader infra hardening | Infra + AppSec | backup/PITR/secret expansion | não declarar backup recorrente concluído |
| D-036 | Produção do catálogo em VPS nativa, não AWS/EC2 | Verified `2c8a3689` | Infra + Produto | release atual | não trocar topologia por presunção |
| D-037 | Dois modos de entrada: gratuito direto e pago com checkout | Verified in current catalog/access flow | Produto + Engenharia | billing expansion | gratuito não cria cobrança; pago não concede antes do checkout |
| D-038 | Produção real sem reset/seed/personas demo | Verified | Operação + Produto | todos os releases | demo somente Local/CI/homologação isolada |

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

Esta aprovação não autoriza uma nova operação de merge, produção, VPS, DNS,
secrets, billing real ou dados de clientes. O estado de produção já verificado
está registrado em [PRODUCTION_CURRENT.md](../PRODUCTION_CURRENT.md); qualquer
mudança posterior abre novo gate.
