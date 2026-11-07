# Loja que cobra na própria conta conectada: entrada, trava e volta (#545)

Vale para a lista `APPSELL_FINANCIAL_CONNECT_SELLER_IDS` do `web` (#440): a loja
listada cobra na própria conta Stripe conectada, com a comissão da plataforma.
Toda mudança nesta lista é operação de produção e exige OK do Rodrigo.

> **Gate: a lista fica vazia até a disputa de cobrança direta estar coberta.**
> Na loja da lista, a cobrança é direta, na conta da loja. A disputa dessa
> cobrança nasce na conta da loja, e o tratamento de disputa (#542, v1) ainda não
> a lê. Nenhuma loja entra na lista antes da v2 (#590) estar em produção,
> mesmo com o pré-voo dando 0.

## As duas listas, e por que não basta uma

| Lista | Onde | O que liga |
|---|---|---|
| `APPSELL_FINANCIAL_SELF_SERVICE_SELLER_IDS` | `api` | a loja pode ativar as próprias vendas pelo Financial (#382) |
| `APPSELL_FINANCIAL_CONNECT_SELLER_IDS` | `web` | a loja cobra na **própria** conta conectada (#440) |

Uma pode estar ligada sem a outra. Uma loja ativa para vender que não está na
segunda lista cobra pelo gateway do operador, ou seja, na conta da plataforma.

## A trava

- **Em cada venda (código):** a loja da lista Connect só vende com a conta de
  recebimento **verificada**, `charges_enabled` **e** `payouts_enabled`, lidos do
  Sell (que o `account.updated` mantém). Sem os dois, o checkout recusa com 409
  `seller_not_ready_to_charge` e registra `appsell.connect.seller_not_ready`. A
  conta que perde a verificação depois de ativa para de vender na compra
  seguinte, sem passo manual. O alerta é esse evento de log.
- **Na entrada (este runbook):** antes de pôr a loja na lista, o pré-voo lê a
  Stripe e o Sell.

## Entrada de uma loja na lista

Quem roda é a Trilha 1 · Plataforma, que opera o host do Sell.

Lida no host em 05/11/2026: a variável está ausente no web, ou seja, a lista
não tem loja nenhuma. Por isso, no primeiro deploy da trava, o
pré-voo não roda em loja nenhuma: ele não é passo do deploy. É ele que trava a
entrada de **cada loja nova**, uma por vez, antes de ela entrar na lista.

1. No host, com o env do serviço (a chave da plataforma vem do env e nunca vai
   por argumento nem para a tela):

   ```sh
   python -m appsell_api.preflight_conta_conectada <seller_id>
   ```

   Usa `APPSELL_PREFLIGHT_DATABASE_URL` (leitura, transação READ ONLY) e
   `APPSELL_STRIPE_SECRET_KEY`.
2. Só o código de saída **0** com `"status":"ok"` permite seguir:
   - `1` `no_payout_account`: a loja não tem conta de recebimento;
   - `1` `provider_not_verified`: a Stripe ainda não libera cobrança ou repasse;
   - `1` `sell_out_of_sync`: o Sell guarda flags diferentes dos da Stripe. O
     `account.updated` não chegou, e a trava do checkout leria dado velho;
   - `2`: não deu para ler a Stripe ou o banco. **Não ler conta como não.**
3. A saída mostra só os flags e o motivo. Não copiar para Issue, PR ou
   comentário nada além disso.
4. Com 0 e OK do Rodrigo, incluir o `seller_id` na lista do `web` e fazer o
   deploy do `web`.

## Volta segura de uma loja

**Tirar a loja da lista não é volta.** Fora da lista, a loja continua vendendo,
mas a venda cai na conta da plataforma, pelo gateway do operador, e não na conta
da loja. Isso é exatamente o que a lista existe para impedir.

A volta é **pausar as vendas da loja**. Hoje o único controle que faz isso sem
efeito colateral grave é suspender a loja:

1. Com OK do Rodrigo (é escrita em produção), mudar a loja para
   `seller_organization.status = 'suspended'`. A vitrine pública só lê loja
   `active`, então nenhuma compra nova começa.
2. Compras já pagas e ainda não entregues ficam retidas enquanto a loja estiver
   suspensa: a entrada do webhook também exige loja `active`. A entrega ao Sell
   acontece dentro do webhook da Stripe no Financial, e a Stripe reentrega por
   até 3 dias. Pausa mais longa que isso deixa compra paga sem acesso, que
   precisa de reprocesso por runbook. Manter a pausa curta e conferir na
   conciliação (#147) depois da reativação.
3. Só depois de pausada, tirar a loja da lista, se for o caso.
4. Para voltar a vender: `status = 'active'` (com OK). Se a loja continua na
   lista, a trava de cada venda e o pré-voo valem de novo.

**Nunca arquivar ou desativar a oferta para pausar.** Não é um controle de
venda e tem efeito colateral grave no acesso ao conteúdo. A única volta é a
suspensão da loja acima.

O controle próprio de "pausar vendas", que não retém entregas já pagas, é
trabalho separado.
