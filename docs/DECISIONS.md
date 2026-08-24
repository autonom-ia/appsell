# DECISIONS — decisões operacionais atuais

Última atualização: 2026-08-24.

O registro arquitetural detalhado está em
[DECISION_REGISTER.md](architecture/DECISION_REGISTER.md) e nos ADRs. Esta
página contém somente as decisões que orientam a operação atual.

## Decisão 001 — sessão e identidade

O browser recebe somente sessão protegida/opaca. A autorização permanente usa
o identificador imutável da identidade, não comparação dinâmica de e-mail.
O Identity real está integrado ao fluxo de produção; mocks de identidade
continuam limitados a Local/CI.

## Decisão 002 — hierarquia multitenant

`comercial@autonomia.site` é o `platform_superadmin` da plataforma. O grant
global não cria memberships falsas. Cada cliente opera dentro do seu tenant por
`seller_owner`; buyer é identidade global e recebe acesso por entitlement.

## Decisão 003 — tenants reais atuais

O ambiente de produção possui os tenants ativos `autonomia` e `hub2you`. O
superadmin pode listar e administrar o catálogo dos dois; um seller owner não
atravessa o tenant de outro seller.

## Decisão 004 — jornada de publicação

O editor segue Produto → Currículo → Oferta → Preview → Publicar. O catálogo é
publicado como snapshot versionado. Ativar vendas/checkout fica na etapa
Publicar e depende de catálogo publicado; publicação e cobrança são operações
distintas.

## Decisão 005 — dois modos de acesso

- **Gratuito:** link público → cadastro/login → entitlement idempotente →
  biblioteca; sem checkout e sem cobrança.
- **Pago:** oferta ativa → checkout → confirmação/fulfillment → entitlement;
  conteúdo não é liberado antes da confirmação.

O browser nunca decide preço final, pagamento ou entitlement.

## Decisão 006 — conteúdo privado

PDF/MP4 somente entra em publicação após upload, armazenamento e verificação de
segurança. O aluno acessa aula/PDF pela biblioteca e a API revalida buyer,
seller, produto, publicação e asset em cada leitura.

## Decisão 007 — produção na VPS

O destino operacional do Appsell é a VPS EasyPanel nativa, não AWS/EC2. O SHA
comprovado do web é `1d2ea631`; API e asset-worker permanecem em `50b6301`.
Qualquer novo cutover exige backup, rollback e smoke no SHA afetado.

## Decisão 008 — produção não usa demo

Reset, seed e personas demo são exclusivamente Local/CI descartável. Não podem
ser executados em `appsell_native_90` nem no domínio público.

## Decisão 009 — dados e rollback

Não apagar/recriar tenant, produto, PDF ou membership para corrigir fluxo.
Migrations são forward-only; rollback de aplicação usa imagem anterior e
rollback de dados usa restore ensaiado em banco separado.

## Decisão 010 — leitor protegido e estudo privado

O leitor entrega páginas rasterizadas sob autorização, sem expor o PDF original.
Links do índice e vídeos abrem dentro da plataforma. Favoritos e marcações
vetoriais são privados e persistidos; Mão move o documento no desktop e
Editar marcação altera ou remove anotações existentes. A proteção reduz cópia
casual, mas não promete impedir captura de tela pelo sistema operacional.

## Decisões ainda pendentes

- staging separado e DNS próprio;
- RPO/RTO formal e operação de backup off-host;
- refund, cancelamento, chargeback e grace period;
- recebimentos/Stripe por tenant e responsabilidades financeiras.

Essas pendências não anulam a release comprovada, mas bloqueiam qualquer
ampliação fora do escopo atual.
