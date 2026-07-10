# ADR 0005 — Jobs, webhooks e fronteira com Financial

- Status: Accepted with gates
- Data: 2026-07-10
- Decisores: Rodrigo / Financial / Integrações / AppSec
- Issue: [autonom-ia/appsell#9](https://github.com/autonom-ia/appsell/issues/9)

## Contexto

O MVP recebe resultados de vendas feitas em checkouts externos. Entitlement deve
ser determinístico mesmo com duplicata, replay, ordem invertida, timeout e queda
do worker. O Financial existente atende o domínio corporativo, mas a verificação
encontrou autorização global e ausência de scoping seguro principal→operator.

## Decisão

Separar dois domínios:

| Domínio | Autoridade | Uso no MVP |
|---|---|---|
| Billing SaaS do AppSell | Admin/Financial corporativo da Autonom.ia | Seller paga para usar a plataforma |
| Seller commerce | Checkout externo + ledger/entitlements no AppSell | Buyer compra produto de um seller |

O Financial não recebe CRUD seller-scoped, `operator_id` do browser, preço de
oferta do seller ou acesso direto por seller/buyer até concluir hardening próprio
e testes cross-operator. Nenhum split, custódia, comissão ou repasse entra no MVP.

## Ingestão de webhook

Endpoint por conector:

1. lê raw body com limite de tamanho e timeout;
2. resolve credencial do endpoint sem aceitar seller arbitrário do payload;
3. valida assinatura e janela temporal antes de parsear efeitos;
4. calcula fingerprint e persiste `webhook_event` imutável;
5. unique `(webhook_endpoint_id, external_event_id)` — ou fingerprint formal
   quando o provedor não oferece ID — garante idempotência;
6. responde rapidamente após persistência durável;
7. publica outbox na mesma transação;
8. worker normaliza e aplica transição de estado idempotente;
9. auditoria relaciona evento, compra e entitlement sem registrar segredo/PII
   desnecessária.

Assinatura inválida nunca cria evento processável. Replay válido retorna o mesmo
resultado lógico, sem duplicar purchase ou entitlement.

## Estado comercial interno

O AppSell mantém uma projeção/ledger de fatos externos, não simula gateway:

- `external_purchase` identifica seller, provider, external purchase/customer e
  external product mapping;
- fatos normalizados incluem `paid`, `refunded`, `cancelled`, `chargeback` e
  outros que forem explicitamente mapeados;
- transição usa precedência/versionamento do provedor e não apenas ordem de
  chegada;
- evento desconhecido fica em estado observável, sem conceder acesso;
- entitlement referencia a origem comercial e registra `valid_from`, `valid_to`,
  status e motivo;
- política de revogação por refund/cancel/chargeback é configurada no servidor e
  aprovada antes do piloto.

## Outbox, fila e worker

PostgreSQL é a origem durável. Na transação que muda domínio, uma linha de outbox
é criada. Dispatcher/worker:

- usa lock/lease com timeout e `SKIP LOCKED` ou mecanismo equivalente;
- carrega `seller_organization_id` em cada job;
- possui unique/idempotency key no efeito final;
- retry exponencial com jitter e limite;
- classifica falha transitória versus permanente;
- envia falha permanente para dead-letter observável;
- nunca marca concluído antes do commit do efeito;
- renova lease ou permite reprocessamento seguro após crash;
- versiona payload e mantém compatibilidade N/N-1 no rollout;
- não depende de Redis para não perder fatos.

Redis pode acelerar agendamento, rate limit e coordenação, mas apagar Redis não
pode apagar compra, entitlement, publicação ou outbox.

## Chamadas corporativas

Integração com Admin/Financial:

- ocorre server-to-server com audience e credencial próprias;
- identifica a organização corporativa AppSell, não um seller escolhido pelo
  browser;
- usa contract tests e allowlist de operações mínimas;
- timeout/circuit breaker impedem cascata;
- billing indisponível não revoga silenciosamente entitlement de buyer;
- suspensão de tenant por SaaS segue política explícita, grace period e auditoria;
- respostas não são confiadas para elevar papel local sem validação de contrato.

## Reconciliação

Um job periódico compara eventos/processamentos e identifica:

- webhook persistido sem resultado;
- purchase sem mapping;
- entitlement divergente do último fato conhecido;
- dead letters e retries esgotados;
- billing SaaS inconsistente.

Reconciliação não corrige silenciosamente casos ambíguos. Gera item operacional
com seller, provider, IDs opacos e ação auditada.

## Alternativas rejeitadas

- processar todo o webhook antes de responder: aumenta retry/duplicata e acopla
  disponibilidade externa.
- usar Redis como fila sem outbox: pode perder efeito após commit ou reinício.
- confiar apenas em external event ID sem endpoint/seller: colisão entre tenants.
- usar Financial atual para seller commerce: scoping por operador não está
  certificado.
- exatamente uma vez distribuído: promessa irreal; adotamos entrega ao menos uma
  vez com efeitos idempotentes.

## Validação obrigatória

- assinatura inválida, segredo de outro endpoint e timestamp fora da janela;
- duplicata concorrente cria uma linha lógica e um efeito;
- replay e evento fora de ordem chegam ao estado aprovado;
- crash depois de persistir outbox e antes/depois do efeito;
- Redis indisponível não perde evento;
- seller A não usa mapping/endpoint de B;
- external product desconhecido não concede entitlement;
- refund/cancel/chargeback seguem política aprovada;
- schema/evento N funciona com worker N-1 durante rollback;
- contract test real com Financial em dev/staging, sem billing real;
- nenhum `operator_id`, total ou papel vindo do browser é confiado.

## Gate para seller commerce no Financial

Só reconsiderar a fronteira quando o Financial tiver:

1. vínculo criptograficamente validado principal organization→operator;
2. APIs seller-scoped e deny-by-default;
3. prevenção de troca de preço/plano entre operators;
4. testes adversariais cross-operator em CI com banco real;
5. trilha de auditoria e revogação;
6. threat model de gateway, refund, chargeback, split e custódia;
7. aprovação explícita por novo ADR.

## Consequências

Seller commerce fica simples e seguro para o piloto, mas depende de checkouts
externos e mapeamento operacional. Checkout nativo é uma evolução deliberada,
não uma capacidade implícita.
