# Runbook — webhook genérico de checkout (Local/CI)

> **Status:** contrato histórico de Local/CI. Não representa um webhook de
> produção nem autoriza billing; a fotografia atual está em
> [`docs/PRODUCTION_CURRENT.md`](../PRODUCTION_CURRENT.md).

## Escopo e estado

Este runbook cobre somente a Fase 6A da Issue #63. A entrada assinada é
genérica e Local/CI; não existe provider, cobrança, entitlement, worker,
reconciliação, staging ou produção neste slice.

## Contrato público

`POST /api/webhooks/checkouts/{endpointKey}` aceita `application/json` de até
64 KiB e exige:

- `x-appsell-webhook-event-id`;
- `x-appsell-webhook-timestamp` em epoch seconds;
- `x-appsell-webhook-signature: v1=<hex sha256>`.

O HMAC usa bytes exatos do body e a forma canônica:

```text
appsell.webhook.generic-hmac.v1
{endpoint_key}
{timestamp}
{event_id}
{payload_sha256}
```

O payload normalizado tem exatamente `event_type`, `external_product_id`,
`external_purchase_id` e `occurred_at`. Campo de seller, preço, papel, e-mail
ou qualquer chave adicional é rejeitado.

## Respostas

- `202 {"status":"accepted"}`: evento e outbox bloqueado criados na mesma transação;
- `202 {"status":"replayed"}`: replay idêntico, sem nova linha lógica;
- `400/401`: contrato, endpoint, timestamp ou assinatura rejeitados;
- `409`: mesmo endpoint/event ID com payload diferente;
- `413`: body acima de 64 KiB;
- `503`: API, banco ou referência de segredo indisponível.

As respostas públicas não expõem tenant, IDs internos, referência de segredo
ou detalhes de validação.

## Persistência e privacidade

O raw body só existe em memória durante BFF→API para autenticação. O banco
retém hash/tamanho do raw, fato normalizado e hashes/tamanhos das referências.
Não retém raw, assinatura, endpoint key, segredo, e-mail ou PII em plaintext.
Essa escolha impede reprocessar o raw; nenhum worker/entitlement pode consumir
o outbox antes do slice de retenção cifrada e reconciliação. O status inicial é
`blocked_retention`, deliberadamente fora do índice de itens `pending`.

As chaves de sessão, CSRF e vault são comparadas por hash com o segredo efêmero
do webhook no boot Local/CI. Material repetido entre endpoints também é
rejeitado. Logs de requisição do Next ignoram esta rota e o Uvicorn roda sem
access log; qualquer futuro ingress/proxy deve provar a mesma redação antes de
um provider real.

## Validação Local/CI

```bash
bash scripts/test-postgres-foundation.sh
```

O gate executa migration forward-only, PostgreSQL real, replay concorrente,
RLS/grants, imutabilidade, duas BFFs, Redis indisponível, restart da API e
replay após restart. Secrets são gerados de forma efêmera e não são impressos.

## Operação e rollback

Não há operação de produção autorizada. A migration é forward-only. Em futura
promoção, rollback significa voltar ao artefato anterior e preservar tabelas,
eventos, outbox e auditoria; nunca executar `DROP`, `TRUNCATE` ou apagar dados.
