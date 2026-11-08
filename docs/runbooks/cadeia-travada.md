# Alerta de cadeia travada (#550)

## Por que não é alerta de frescor

Decisão de 05/11/2026: o volume atual de vendas não sustenta um limite por
horário. "Faz X horas sem venda" dispararia sempre ou nunca. O dano real é a
venda que entrou e não chegou ao cliente, e é isso que este alerta vigia. Ele
vale com uma venda ou com mil. "Nenhuma venda na semana" vai no resumo
semanal, como informação, não como alarme.

## O que dispara o alerta

A checagem roda a cada 5 minutos (`scripts/ops/cadeia_travada.py`).

| Categoria | Quando | Fonte |
|---|---|---|
| entrega esgotou as tentativas | job de fulfillment em `dead_letter` | 0063 |
| entrega parada | job pendente, em retry ou com lease há mais de 15 min | 0063 |
| venda sem job de entrega | `purchase.approved` aceito sem job | 0063 |
| entrega concluída sem acesso registrado | job concluído sem `entitlement_grant` | 0063 |
| acesso registrado e comprador sem acesso | grant ativo, `buyer_entitlement` não ativo | 0063 |
| acesso liberado sem e-mail | grant ativo há mais de 15 min cujo e-mail da compra não está `sent` | 0071 |
| e-mail na fila | `pending`, `retry` ou `leased` há mais de 30 min | 0071 |
| e-mail esgotou as tentativas | foi a `dead_letter` desde a rodada anterior; o descadastro (`suppressed`, `no_consent`) fica fora | 0071 |
| **a própria checagem falhou** | banco, contêiner ou consulta fora do ar | o script |

Regra do envio:
- um problema que aparece, cresce ou tem um caso mais novo que o da rodada
  anterior alerta na hora. O caso mais novo vem do instante que a leitura
  devolve, e cobre o "um sai, outro entra", em que a contagem fica igual;
- o mesmo problema, parado, repete uma vez por dia;
- a falha da checagem repete no máximo uma vez por hora e não avança a janela.
  É falha qualquer exceção, a saída fora do formato, a categoria desconhecida
  e a métrica da 0071 que não veio. Uma saída quebrada nunca vira zero.

O acesso sem e-mail e o e-mail esgotado são eventos: alertam quando acontecem.
A janela do acesso anda deslocada pelos 15 min do limite, para cada grant ser
olhado quando passa do limite. Os parados (entrega e e-mail na fila) são
estado: aparecem enquanto não forem reparados, e repetem uma vez por dia.

O estado do checador é gravado inteiro ou não é gravado (temporário, `fsync` e
troca), com permissão 0600.

Ficam fora: `access_without_grant`, até ser medido em produção (ver a 0063), e
o lado do estorno, que não é venda travada.

## Como o banco é lido

Só por duas funções `security definer`, que devolvem categoria, quantidade e
instante, nunca linha de cliente:

- `app_private.checkout_reconciliation_report` (0063);
- `app_private.chain_health_report` (0071).

O papel é o `appsell_reconciler`: `connect`, `usage` em `app_private` e
`execute` nas duas funções. Ele não lê nenhuma tabela. A conexão do checador
liga `default_transaction_read_only` e um `statement_timeout` de 20 s por conta
própria, sem depender da config do papel. O script entra no contêiner do Postgres e conecta pelo IP
dele, com SCRAM. A senha sai de um arquivo 0600 pelo stdin, e nunca vai em
argumento de processo.

## Instalação (produção, com OK do Rodrigo)

Pré-requisitos:
- a 0071 aplicada;
- o alerta de logs (#472) instalado, porque esta checagem usa o mesmo canal e
  a mesma config;
- o destino do e-mail definido em `ALERTA_PARA`, no host. O canal é e-mail
  (decisão do Rodrigo, 08/11), e o endereço não entra no repositório.

1. **Login do papel.** O `appsell_reconciler` existe sem login. Para esta
   checagem, ele passa a ter login. A senha já está no arquivo 0600 do host:
   `alter role appsell_reconciler login;`. Conferir depois com
   `select rolcanlogin from pg_roles where rolname='appsell_reconciler'`.
2. **Config.** Acrescentar à config do alerta de logs, com nomes e caminho
   reais lidos no host:
   ```
   CADEIA_PG_SERVICO=<serviço do Postgres do Sell>
   CADEIA_PG_BANCO=<banco do Sell>
   CADEIA_SENHA_ARQUIVO=<arquivo 0600 com a senha do appsell_reconciler>
   ```
3. **Cópia do script**, ao lado do alerta de logs e do mesmo SHA registrado.
   O checador importa o `alerta_de_logs.py` da mesma pasta:
   ```sh
   git -C <home-do-operador>/appsell-src-"$SHA" show "$SHA":scripts/ops/cadeia_travada.py > /opt/sell-alerta/cadeia_travada.py
   chmod 0755 /opt/sell-alerta/cadeia_travada.py
   ```
4. **Ensaio seco.** `python3 /opt/sell-alerta/cadeia_travada.py --seco` imprime
   o e-mail que sairia e não grava estado.
5. **Cron.** Em `/etc/cron.d` a linha precisa do campo de usuário. Sem ele, o
   cron ignora a linha e não avisa nada. A saída vai para o log, com `2>&1`:
   ```sh
   printf '%s\n' \
     '*/5 * * * * root /usr/bin/python3 /opt/sell-alerta/cadeia_travada.py >>/var/log/sell-alerta.log 2>&1' \
     '0 12 * * 1 root /usr/bin/python3 /opt/sell-alerta/cadeia_travada.py --resumo-semanal >>/var/log/sell-alerta.log 2>&1' \
     > /etc/cron.d/sell-cadeia-travada
   chmod 0644 /etc/cron.d/sell-cadeia-travada
   ```
6. **Conferir que rodou.** 10 minutos depois, `/var/lib/sell-alerta/cadeia.json`
   existe e o campo `fim` é de menos de 5 minutos atrás. Se não existir, o cron
   não rodou: ler `/var/log/sell-alerta.log` antes de seguir.
7. **Primeira semana.** Registrar cada alerta e se ele era real, para medir o
   falso alarme.

## Quando o alerta chegar

| Categoria | Primeiro passo |
|---|---|
| entrega esgotou ou parada | rodar a conciliação (`appsell_api.webhook_reconciliation`) e ver o `error_code` do job |
| venda sem job ou concluída sem acesso | conciliação; depois o runbook de fulfillment |
| acesso sem e-mail ou e-mail na fila | log do worker de e-mail e o SMTP |
| e-mail esgotou | `error_code` da linha em `dead_letter`; reenviar só depois da causa |
| a checagem falhou | ver se o Postgres e o contêiner estão de pé; o script não diz mais que o código, de propósito |

## Volta

1. Tirar o cron: `rm -- /etc/cron.d/sell-cadeia-travada`.
2. `alter role appsell_reconciler nologin;`, se a conciliação manual não
   precisar mais do login.
3. A 0071 pode ficar, porque só lê. Se for preciso tirar a função, o alvo do
   downgrade é a migration logo antes da 0071 na cadeia, lida no
   `down_revision` dela. Hoje: `alembic downgrade 0074_acesso_em_disputa`.
   Nunca descer abaixo da versão que está em produção antes da 0071: conferir
   `select version_num from alembic_version` antes e depois.
