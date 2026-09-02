# TROUBLESHOOTING

## Problema: criação de produto retorna 403 após atualização

### Sintoma

Uma sessão local já aberta lista tenants e produtos, mas o primeiro POST de
produto retorna `forbidden_mutation`.

### Causa provável

A sessão foi criada antes do cookie `appsell_product_csrf` existir. O cookie é
derivado da sessão e da ação no callback; não há fallback genérico.

### Diagnóstico read-only

Confirme que a sessão é anterior à Fase 4A e que o browser não possui o cookie
legível `appsell_product_csrf`. Nunca registre o valor do cookie.

### Correção

Saia e entre novamente no ambiente. Isso cria uma nova sessão e os cookies CSRF
específicos sem expor a sessão opaca ao JavaScript.

### Validação

Selecione explicitamente o tenant, crie um rascunho sintético e confirme resposta
201 seguida da listagem persistida.

### Rollback / mitigação

O comportamento é fail-closed. Não relaxe origem ou CSRF; mantenha a orientação
de novo login até existir uma estratégia versionada de renovação de cookies.

## Problema: edição ou publicação retorna 403 após atualização

Sessões locais criadas antes da Fase 4B não possuem os cookies legíveis
`appsell_product_update_csrf` e `appsell_product_publish_csrf`. Saia e entre
novamente; não copie, registre ou reutilize os valores. Os tokens são separados
por ação de propósito. Não relaxe Origin/CSRF para preservar sessões antigas.

## Problema: publicação retorna 409

- `draft_version_conflict`: use **Recarregar versão do servidor**; a UI preserva
  os campos locais e permite novo envio com as versões atuais.
- `no_unpublished_changes`: o snapshot vigente já representa o draft atual;
  edite e salve antes de publicar uma nova versão.
- `slug_conflict`: escolha outro endereço para o produto.

Timeout/503 preserva a chave idempotente para retry do mesmo corpo. Uma nova
edição cria uma nova intenção; não force novas chaves manualmente.

## Problema: o PDF abre sem botão nenhum

O aluno abre o guia e não há botões de vídeo nem de índice — sem erro na tela, e
sem nada nos logs.

**Causa quase certa: o serviço isolado não está respondendo.** A leitura dos
links passa por ele, e uma falha ali virava `return []` — que é indistinguível de
"este PDF não tem links". Desde `ee194bf` a leitura insiste quatro vezes quando o
serviço responde `429` (ocupado), mas continua devolvendo vazio se ele estiver
fora.

**Como confirmar:**

```bash
# o socket existe?
sudo docker volume inspect sell-autonomia_pdf_socket --format '{{.Mountpoint}}' | xargs sudo ls -la
# o extrator real, contra o PDF real, dentro do container da api
sudo docker exec <api> python -c "..."   # ver PRODUCTION_CURRENT.md
```

**Não confie no `healthz` para isto.** Ele já respondeu `pdf_sandbox: true`
durante um incidente em que o socket não existia — corrigido em `ee194bf`, mas a
lição fica: a verificação é abrir um PDF.

**Causa mais provável de o socket sumir:** um deploy do `pdf-sandbox` com
`--update-order start-first`. **Nunca use** nesse serviço. O conserto é
`docker service update --update-order stop-first --force sell-autonomia_pdf-sandbox`.

## Problema: a API entra em laço de reinício depois de uma migration

O `/health/ready` alimenta o `HEALTHCHECK` do contêiner. Uma migration que sobe a
versão do banco **sem estender `app_private.schema_ready()`** deixa o banco "não
pronto", o health reprova, e o Swarm derruba a task — em laço.

**Como confirmar:**

```bash
docker exec <postgres> psql -U appsell_native_admin -d appsell_native_90 \
  -tAc "select version_num||' | ready: '||app_private.schema_ready()::text from alembic_version;"
```

Se a versão for nova e `ready` for `false`, é isto. Toda migration precisa
estender as duas cópias da função (`schema_ready_base_0016` e `_0017`) — a 0043 e
a 0044 mostram o formato. `EXPECTED_MIGRATION` em `reset_demo_tenant.py` também
precisa acompanhar.

## Problema: documento grande falha no envio, ou o worker fica reiniciando

O worker segura, ao mesmo tempo, o PDF de origem, o pacote de páginas, uma cópia
do pacote feita na verificação e as páginas extraídas — 1.556 MiB no pior caso
com 300 páginas. O `/tmp` dele é `tmpfs`, e **`tmpfs` conta contra a memória do
cgroup**: um `/tmp` maior que o limite de memória é espaço que não existe.

Conferir o que o host tem, e não o que o script declara:

```bash
docker service inspect sell-autonomia_asset-worker \
  --format '{{json .Spec.TaskTemplate.Resources.Limits}}'
```

`ensure-asset-worker.sh` **aborta quando o serviço já existe**, então ele não
atualiza nada sozinho — e o valor declarado nele já esteve abaixo do que o host
tinha. Ao passar `--limit-memory`, leia o valor atual antes.

## Problema: preciso saber o que a API fez, e o log não conta nada

Em 31/08/2026 a API de produção gravou **22 linhas em 24 horas** — vinte delas
`Started server process` e `Shutting down` do uvicorn. Não havia
`structlog.configure` em lugar nenhum do repositório, o `CMD` passava
`--no-access-log` e não existia handler de exceção global. Toda requisição, todo
500 e todo erro de negócio saíam do processo sem deixar rastro.

Desde a fundação da issue #312 o log tem contrato:

| Ambiente | Renderizador |
|---|---|
| `production`, `staging` | JSON, uma linha por evento |
| `local`, `ci` | console legível |

O log sai no **`stderr`**, não no `stdout`. Não é preferência: o `stdout` é
canal de protocolo — `pdf_verifier` roda o parser num subprocesso e faz
`json.loads(completed.stdout)` com teto de 1.024 bytes, e `pdf_runtime_probe` e
`capacity_telemetry` imprimem JSON puro. Uma linha de log ali quebra a leitura
de **todo** PDF. O Swarm captura os dois fluxos, então `docker service logs`
mostra tudo.

Tudo o que uvicorn, SQLAlchemy e boto3 registram passa pelos mesmos
processadores — mesmo formato, mesma redação. Sem isso, uma exceção não tratada
saía duas vezes: a linha estruturada com a credencial redigida, e logo abaixo o
traceback cru do uvicorn com a credencial em claro. `httpx`, `httpcore`,
`botocore`, `boto3`, `urllib3`, `s3transfer` e `asyncio` ficam em `WARNING`: o
`httpx` registra `HTTP Request: GET <url completa>` a cada chamada, e a API fala
com o sandbox e com o identity por `httpx`.

**Uma linha por requisição**, com o *template* da rota:

```json
{"method":"GET","route":"/v1/me/learning/items/{entitlement_id}/lessons/{lesson_id}/progress",
 "status":405,"duration_ms":0.1,"event":"http_request","request_id":"prova-001",
 "level":"info","timestamp":"2026-08-31T16:15:36.303059Z"}
```

O que **não** entra na linha, de propósito: o caminho concreto, a query string e
qualquer cabeçalho. Os três carregam identificador e token.

**Requisição que termina em exceção não produz `http_request`** — produz
`unhandled_exception`, com rota, método, `request_id`, `duration_ms`,
`response_started` e o traceback. Quem decide o status final é o
`ServerErrorMiddleware`, ou um handler global que pode responder 503, e os dois
ficam fora do middleware: registrar 500 por conta própria fazia a linha mentir.
Pelo mesmo motivo, **o `X-Request-Id` não volta na resposta de um erro não
tratado** — o `request_id` está no evento da exceção.

### Como ler

O host é o que responde por `sell.autonomia.site` — resolva o DNS antes, porque
o alias de SSH já apontou para uma máquina desativada:

```bash
dig +short sell.autonomia.site
```

```bash
sudo docker service logs --since 1h sell-autonomia_api
```

Para cruzar uma requisição com o erro que ela causou, use o `request_id`. Ele
vai e volta no cabeçalho `X-Request-Id`, então o valor visto no browser é o
mesmo que aparece no log:

```bash
sudo docker service logs --since 1h sell-autonomia_api | grep '"request_id":"<id>"'
```

### O que o log nunca vai mostrar

- **O que o redator apaga**, e só isto:

  | Padrão | Exemplo |
  |---|---|
  | 32+ caracteres de `[A-Za-z0-9_-]` sem trecho curto | `sk-a1B2c3D4…` |
  | base64 com padding final | `YWJj+ZGVm/…NDU2=` |
  | credencial de URL de conexão | `postgresql://<REDACTED>@db.internal/prod` |
  | cartão: prefixo de bandeira **e** Luhn | `4111 1111 1111 1111` |

  As bandeiras cobrem Visa, Mastercard, Amex, Diners, Discover (inclusive as
  faixas `644-649` e `62`), JCB e UnionPay. Cobrir só o prefixo mais conhecido
  de cada uma deixava `6445641386133871` passar inteiro.

  **O que ele preserva de propósito**, porque é diagnóstico e não segredo: UUID
  canônico, slug de produto e de seller, caminho de arquivo do traceback,
  carimbo de tempo, e identificador numérico. Cada um deles já foi apagado por
  alguma versão anterior do redator.

  O que separa slug de token são dois critérios, e os dois são necessários:
  nenhum trecho entre `-`/`_` acima de 16 caracteres, **e** nenhuma maiúscula.
  Só o primeiro deixava passar 5% dos `secrets.token_urlsafe(32)` que o próprio
  sistema gera — medido em 100.000 tokens, 5.027 escapavam.

  **O que ele não apaga:** senha curta solta num campo, CPF, e-mail, número de
  telefone, e base64 sem padding. O redator é rede de segurança para o descuido
  e para o traceback — cujo conteúdo ninguém escolhe. **A regra continua sendo
  não colocar credencial nem dado pessoal em campo de log.** Tratar o redator
  como garantia é o mesmo que não ter redator.

  Ele também não distingue um token de um hash de conteúdo, e trata os dois como
  segredo: perder o hash de um material custa uma consulta a mais no
  diagnóstico. Prefira registrar `asset_id` a registrar `key`.

  A redação roda **duas vezes**: sobre os campos e sobre a linha já montada. Em
  `local`/`ci` o traceback é formatado pelo `ConsoleRenderer` depois da primeira
  passada, e sem a segunda uma credencial no texto da exceção saía inteira — num
  run de CI, gravada no log público do GitHub.
- **Health check.** `/health/live` e `/health/ready` são silenciosos. O Swarm os
  sonda a cada poucos segundos, e sem isso o log seria milhares de linhas por dia
  de uma rota que não diz nada.

### Armadilhas

- **`structlog.reset_defaults()` desliga a fundação inteira**, em silêncio: ele
  restaura o padrão da biblioteca, não o desta aplicação — sem JSON e sem
  redator. Quem precisa trocar a configuração por um instante deve devolvê-la com
  `configure_logging_from_environment()`.
- **`exception` é nome reservado** do structlog para o traceback formatado. Um
  dado de negócio nesse campo faz o renderizador tratá-lo como traceback. Use
  `exception_type`.
- **Nada de `print()` para diagnóstico.** O `stdout` é protocolo. Use
  `logging_setup.get_logger(__name__)`.
- **Handler de logging próprio contorna a redação.** Quem instalar um
  `logging.Formatter` de terceiro, ou escrever direto no `stderr`, sai fora da
  cadeia. A exceção que derruba o processo já está coberta por **três** hooks
  que `configure_logging` instala, cada um para um caminho que os outros não
  veem:

  | Hook | Cobre | Evento |
  |---|---|---|
  | `sys.excepthook` | thread principal | `uncaught_exception` |
  | `threading.excepthook` | demais threads | `uncaught_thread_exception` |
  | `sys.unraisablehook` | `atexit`, `__del__`, `weakref` | `unraisable_exception` |

  Sem o primeiro, uma falha de arranque do `asset-worker` imprimia o traceback
  cru. Sem o terceiro, o processo terminava com código **0** e a credencial
  inteira no `stderr` — vazamento silencioso num encerramento que parece
  bem-sucedido. `KeyboardInterrupt` e `SystemExit` seguem pelos hooks originais:
  são controle de fluxo, não falha.
- **A cobertura ainda é pequena.** A fundação registra requisição e exceção; a
  instrumentação dos caminhos de mídia, catálogo e verificação é trabalho
  separado. Não conclua "não aconteceu" a partir de "não está no log".

## Problema: o formulário da página pública responde 503

O `POST /api/contato` devolve `503` quando o SMTP comercial não está
configurado no serviço `web`. A rota é explícita nisso: sem `SMTP_HOST_COMERCIAL`,
`SMTP_USER_COMERCIAL` e `SMTP_PASS_COMERCIAL`, ela não tenta enviar e não finge
sucesso.

Conferir só os nomes, nunca os valores:

```sh
docker service inspect sell-autonomia_web \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' \
  | cut -d= -f1 | grep -i smtp
```

São cinco: `SMTP_HOST_COMERCIAL`, `SMTP_PORT_COMERCIAL`, `SMTP_SECURE_COMERCIAL`,
`SMTP_USER_COMERCIAL` e `SMTP_PASS_COMERCIAL`. A conta é
`sell.comercial@autonomia.site` e as credenciais vêm do mesmo SMTP que atende as
outras landings. Adicione com `--env-add` em uma única atualização; nunca pelo
`updateEnv` do painel sem o campo `env` completo, porque omiti-lo apaga todas as
variáveis do serviço.

Há um segundo conjunto, `SMTP_*_TRANSACIONAL`, com os mesmos cinco nomes e a
conta `sell@autonomia.site`. É o remetente de confirmação de compra, convite e
segurança de conta, e é **separado de propósito** — nenhum envio transacional
cai no comercial como fallback, e vice-versa. Ele vive no serviço **`api`**,
não no `web`: quem envia é o worker de e-mail, que roda dentro do processo da
API (mesmo lugar do worker de fulfillment). Se o comercial está configurado e
o transacional não, o formulário funciona e o canal transacional fica
desligado — a API registra `email_worker_disabled reason=smtp_not_configured`
uma vez ao subir, e as linhas de `app_private.email_outbox` ficam `pending`.
Isso é o esperado, não um defeito. Regra completa em
[EMAIL_POLICY.md](architecture/EMAIL_POLICY.md).

```sh
docker service inspect sell-autonomia_api \
  --format '{{range .Spec.TaskTemplate.ContainerSpec.Env}}{{println .}}{{end}}' \
  | cut -d= -f1 | grep -i smtp
```

## Problema: o e-mail transacional não chega, ou chega como spam

Primeiro olhe a caixa de saída — ela diz se o e-mail nem foi enfileirado, se
está esperando o worker, ou se o SMTP recusou. Nada de corpo aí, só o
registro:

```sql
select kind,status,attempt_count,error_code,message_id,sent_at,created_at
from app_private.email_outbox order by created_at desc limit 20;
```

- nenhuma linha para a compra → o grant não aconteceu (olhe `fulfillment_job`);
- `pending` há minutos → o worker não está rodando: procure
  `email_worker_disabled` no log da API (SMTP não configurado) ou
  `email_worker_retry`;
- `retry` com `smtp_auth` → senha errada, o worker espera 10 min entre tentativas;
- `retry` com `smtp_unreachable`/`smtp_temporary` → host ou porta, ou o
  provedor fora do ar; `dead_letter` com `recipient_refused` → o endereço não
  existe, e a fila não insiste;
- `sent` com `message_id` → saiu; o resto é entrega, e o cabeçalho recebido
  decide (abaixo).

Depois confira os nomes das variáveis no serviço `api` (comando acima) e envie
um teste com o smoke. Ele **não roda dentro do container do `web`**: a imagem é o
standalone do Next, sem `scripts/` e sem `nodemailer` fora do chunk da rota.
Rode de um checkout com `pnpm install` feito, lendo as variáveis do arquivo de
segredos para o ambiente do subshell — nunca na linha de comando, que fica no
histórico:

```sh
( set -a; . "$ARQUIVO_DE_SEGREDOS"; set +a; \
  node scripts/email/smoke-transacional.mjs voce@exemplo.com )
```

O script imprime só o que o SMTP devolveu: aceitos, rejeitados e `Message-ID`.
Nunca imprime credencial, nem em erro.

O aceite é o cabeçalho **recebido**, não a resposta do SMTP. Na caixa de
destino, "Mostrar original" e procure `Authentication-Results`:

- `spf=pass dkim=pass dmarc=pass` — a autenticação passou. Não prova mais que
  isso: se caiu em spam, ainda contam reputação do IP e do domínio, alinhamento
  do envelope, PTR, TLS e a política do destinatário;
- `dkim=fail` ou `dkim=none` — a Hostinger não assinou; a conta não está no
  mesmo plano de e-mail que a comercial, ou o seletor `hostingermail-a` mudou;
- `spf=fail` — o envio não saiu pelo SMTP da Hostinger; confira
  `SMTP_HOST_TRANSACIONAL`;
- `535` na resposta do SMTP — senha errada; rotacione pela Hostinger e
  atualize a variável com `--env-add`, nunca pelo painel sem o `env` completo.

## Problema: o contador de variáveis caiu depois de um deploy

Não conclua perda pelo número. Em 01/09 o total foi de 47 para 51 depois de
adicionar cinco, quando o esperado era 52, e **nada tinha sido perdido**: a
variável `PORT` estava duplicada na spec anterior e o `docker service update`
deduplicou.

Compare os nomes contra a spec salva antes do deploy:

```sh
python3 -c "
import json, subprocess
antes = [e.split('=',1)[0] for e in json.load(open('spec_web_antes.json'))[0]['Spec']['TaskTemplate']['ContainerSpec']['Env']]
d = json.loads(subprocess.check_output(['docker','service','inspect','sell-autonomia_web']))
depois = [e.split('=',1)[0] for e in d[0]['Spec']['TaskTemplate']['ContainerSpec']['Env']]
print('perdidas:', sorted(set(antes) - set(depois)) or 'nenhuma')
"
```

## Problema: o ci-gate reprova sem que ninguém tenha mexido no código

O `fast_web` roda `pnpm audit --prod`, que consulta o registro de advisories ao
vivo. Um aviso publicado hoje reprova o PR de ontem. Foi o que aconteceu em
01/09: a `main` estava verde às 11h15 e passou a falhar por `browserslist`,
depois por `postcss` e `sharp`, todos em dependência transitiva do Next.

Quando a versão vulnerável chega por transitividade, não há como subir pela
árvore direta: o caminho é `pnpm.overrides` no `package.json`, com a versão
corrigida que o próprio `audit` aponta. Depois da troca, rode typecheck, build e
a suíte antes de confiar.

Se o log do gate disser `ci-gate rejected fast_web=failure` sem teste vermelho,
o erro está nos passos que vêm depois dos testes. Leia o fim do step, não só o
resumo.
