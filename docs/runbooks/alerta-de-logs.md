# Alerta de erro nos logs da produção (#312)

Hoje ninguém é avisado quando a API ou o web gravam erro. A API grava uma linha
JSON por evento desde 31/08 (`TROUBLESHOOTING.md`), e o webhook do Stripe no web
passa a gravar `appsell.stripe_webhook.dropped` e `retrying` com
`level:"error"` (#447). Este runbook instala um cron no host que lê esses logs e
manda um e-mail quando aparece erro.

Decisão do Rodrigo (23/10/2026, opção 1 do #312): script no host, cron, SMTP
transacional que já existe. Sem infra nova. **A instalação exige um OK separado
do Rodrigo**: é escrita no host de produção e cópia de credencial.

## O que o script faz

`scripts/ops/alerta_de_logs.py`, só biblioteca padrão do Python:

1. Lê `docker service logs --raw --timestamps --since <início>` de
   `sell-autonomia_api` e `sell-autonomia_web`, stdout e stderr.
2. Conta as linhas JSON cujo `level` é `error`, `critical` ou `fatal`, por
   serviço e por `event` (API) ou `marker` (web). Linha que não é JSON não conta.
3. Se houve erro, manda **um e-mail por janela** com a contagem e as três
   primeiras linhas. Das linhas só saem os campos de uma lista fechada
   (`event`, `marker`, `reason`, `route`, `status`, `request_id`, `sqlstate`…).
   E-mail de pessoa, cabeçalho, corpo e id de pedido nunca saem. Valor com 32+
   caracteres `[A-Za-z0-9_-]` sai como `<REDACTED>`.
4. Sem erro, não manda nada.
5. Se a leitura de um serviço falha, o e-mail diz "leitura falhou". Silêncio
   nunca quer dizer "não consegui ler".

**Janela.** O início de cada rodada é o fim da anterior, guardado em
`/var/lib/sell-alerta/estado.json`, e a linha conta na janela em que o horário
dela cai: `[início, fim)`. Nenhuma linha conta duas vezes e nenhuma fica de
fora. Depois de mais de 60 min parado, a rodada olha só os últimos 60 min, e o
início real aparece no e-mail. Se o envio falha, o estado não avança, e a
próxima rodada tenta a mesma janela.

**Limiar anti-spam.** No máximo um e-mail a cada 5 min (uma rodada), com a
contagem agregada. Uma rajada de mil erros vira um e-mail que diz "1000".

**O que ele não vê.** Erro do Next que sai como texto (stack trace, `⨯`) não é
JSON e não conta. Interpretar texto livre ficaria fora da regra do projeto (sem
regex para isso). Se fizer falta, o caminho é o web gravar JSON também.

## Arquivos no host

| Caminho | Dono e modo | Conteúdo |
|---|---|---|
| `/opt/sell-alerta/alerta_de_logs.py` | `root`, `0755` | o script, copiado do SHA registrado |
| `/etc/sell-alerta/alerta.env` | `root`, `0600` | config: nomes abaixo |
| `/var/lib/sell-alerta/estado.json` | `root`, `0600` | fim da última janela; o script cria |
| `/etc/cron.d/sell-alerta-logs` | `root`, `0644` | a linha do cron |

`alerta.env`, uma `CHAVE=valor` por linha. Os nomes abaixo; os **valores**
nunca neste repositório nem em chat:

- `SMTP_HOST_TRANSACIONAL`, `SMTP_PORT_TRANSACIONAL`,
  `SMTP_SECURE_TRANSACIONAL`, `SMTP_USER_TRANSACIONAL`,
  `SMTP_PASS_TRANSACIONAL`: os mesmos do worker de e-mail da API (os nomes que o
  `config.py` lê). O remetente é o próprio usuário SMTP.
- `ALERTA_PARA`: quem recebe. **A definir pelo Rodrigo.**

## Instalação (só com OK do Rodrigo)

Antes, leitura sem escrita no host, para confirmar o que este runbook supõe:

```sh
python3 --version                      # o script pede 3.9 ou mais novo
docker service logs --raw --timestamps --since 5m sell-autonomia_api | head -3
```

A segunda saída precisa começar pelo horário RFC 3339, seguido do JSON. Se vier
com o prefixo `serviço.N.id@nó |`, **parar**: o `--raw` não se comportou como o
script espera.

Depois, um comando por vez:

```sh
: "${SHA:?SHA vazio — parar}"
install -d -m 0755 /opt/sell-alerta
install -d -m 0700 /etc/sell-alerta /var/lib/sell-alerta
git -C /home/claudete/appsell-src-"$SHA" show "$SHA":scripts/ops/alerta_de_logs.py > /opt/sell-alerta/alerta_de_logs.py
chmod 0755 /opt/sell-alerta/alerta_de_logs.py
install -m 0600 /dev/null /etc/sell-alerta/alerta.env   # preencher com editor, sem ecoar valor
python3 /opt/sell-alerta/alerta_de_logs.py --seco       # lê os logs e imprime, sem enviar
python3 /opt/sell-alerta/alerta_de_logs.py --teste      # envia o alerta sintético
```

Se o `git show` não servir (fonte do release sem `.git`), copiar o arquivo com
`scp` do checkout no SHA e conferir com `sha256sum` dos dois lados.

`--seco` precisa terminar com saída 0 e, se houver erro na janela, imprimir o
e-mail. `--teste` precisa chegar à caixa de `ALERTA_PARA` com o assunto
`[Sell] 1 erro(s) em produção` e a linha `teste_do_alerta: 1`. Só então o cron:

```sh
printf '%s\n' '*/5 * * * * root /usr/bin/python3 /opt/sell-alerta/alerta_de_logs.py >>/var/log/sell-alerta.log 2>&1' > /etc/cron.d/sell-alerta-logs
chmod 0644 /etc/cron.d/sell-alerta-logs
```

Conferência, 10 min depois: `/var/lib/sell-alerta/estado.json` com `fim` dos
últimos 5 min, e `/var/log/sell-alerta.log` sem `config inválida` nem
`envio falhou`.

## Teste do alerta

- No repositório: `python3 -m unittest scripts/ci/tests/test_alerta_de_logs.py`
  (contagem por serviço e `event`/`marker`, janela sem buraco nem sobreposição,
  amostra sem PII e sem credencial, falha de leitura vira alerta, config
  incompleta diz o nome que falta). Roda no smoke do App CI.
- No host, depois de instalado: `--teste` (acima). Repetir a cada mudança do
  SMTP transacional.

## Rollback

Um comando, efeito imediato no próximo minuto:

```sh
rm -- /etc/cron.d/sell-alerta-logs
```

O resto (`/opt/sell-alerta`, `/etc/sell-alerta`, `/var/lib/sell-alerta`) pode
ficar. Para remover de vez, apagar só esses três caminhos exatos, depois de
`ls -la` em cada um. O `alerta.env` tem credencial: apagar com o rollback
completo e registrar.

Nada disso toca os serviços do Swarm. O script só lê log e envia e-mail.

## Quando mudar este runbook

- Serviço novo que grava erro em JSON: entra em `SERVICOS` no script.
- O web passar a gravar JSON nos erros do Next: a seção "O que ele não vê" sai.
- Troca do SMTP transacional: refazer o `--teste`.
