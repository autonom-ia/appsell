# Erros do web e da API no log, redigidos (#549)

Decisão de 05/11/2026: **opção C**. O erro sai estruturado no log do próprio
serviço, e quem lê é o alerta do #472. Nenhum dado vai para terceiro, e não há
serviço novo.

## O que sai

| Serviço | Onde nasce a linha | `marker` / `event` |
|---|---|---|
| API | `logging_setup`: exceção não tratada, `logger.exception`, o `logging` da biblioteca padrão e os hooks de exceção terminal | o `event` de cada chamada; `level` `error` ou acima |
| Web | `instrumentation.ts` (`onRequestError` do Next): erro de rota, página, server action e middleware | `appsell.web.request_error` |

A linha do web leva o método, o **template** da rota (`/c/[token]`, nunca o
caminho concreto), o tipo e a mensagem do erro, o stack e o `digest` do Next.
Ela nunca leva a query nem os cabeçalhos do pedido.

### O erro cru do Next

Além de chamar o `onRequestError`, o Next imprime o erro por conta própria:
`⨯ Error: <mensagem>` com o stack, pelo `logError` do base-server e pelo
tratador de erro de render. Medido com `next build` e `next start` (e com
`node .next/standalone/server.js`, que é como o web roda): sem tratamento, o
e-mail e o CPF da mensagem saíam inteiros no stderr.

Por isso o `register` de `instrumentation.ts` envolve o console do servidor
(`lib/console-redigido.ts`): `error`, `warn`, `log`, `info` e `debug` passam
pelo mesmo redator antes de escrever.

- Uma linha que é JSON (a do `onRequestError`, as de telemetria) continua
  JSON: os valores são redigidos e a linha é montada de novo.
- O resto sai em texto, redigido. O alerta do #472 só lê JSON, então a linha
  `⨯` não conta o erro duas vezes.
- Quem escrever direto em `process.stdout`/`process.stderr` passa por fora. O
  código do Sell não faz isso; o Next 16.4.3 usa o console para o erro.
- Em atualização do Next, refazer a prova: build, start, uma rota que lança
  erro com e-mail e CPF fictícios, e conferir o stderr.

## O que a redação tira

O redator roda sobre o texto que de fato sai, inclusive mensagem e stack:

- query string e fragmento (`#...`) de qualquer URL, absoluta ou caminho
  (`GET /c/link?token=...`), que viram `?<REDACTED>` e `#<REDACTED>`;
- valor de `authorization`, `proxy-authorization`, `cookie`, `set-cookie` e
  `x-api-key`: `Nome: valor`, `"nome": "valor"`, os formatos do Node (lista
  `[ ... ]`, Map `=>`), o par de bytes do ASGI e valor continuado na linha
  seguinte. Num campo estruturado com esse nome, o valor sai inteiro, seja
  texto, lista ou número;
- e-mail, inclusive com letra acentuada e com `%40` no lugar do `@`;
- CPF e CNPJ, pontuados ou não, só quando o dígito verificador bate; num campo
  numérico também;
- sequência de 32+ caracteres `[A-Za-z0-9_-]` que não seja UUID nem slug, e, na
  API, também base64, credencial em URL e cartão.

Texto maior que 16.384 caracteres perde o meio antes de redigir, sempre numa
fronteira de espaço (o começo e o fim ficam). Cada regra varre o texto uma vez.

Na API, em JSON, a última passada decodifica a linha pronta, redige os valores
e monta a linha de novo. Se a linha não decodificar, sai uma linha mínima
válida (`level`, `event: log_line_unparseable`, `request_id`): linha inválida
não seria contada pelo alerta.

API: `apps/api/src/appsell_api/redacao_pessoal.py` mais `logging_setup.py`.
Web: `lib/redacao.ts` e `lib/console-redigido.ts`. As duas implementações seguem as mesmas regras, e cada
uma tem teste que lê a linha escrita: `test_redacao_pessoal_no_log.py` e
`tests/error-log.test.ts`.

É rede de segurança, não garantia. A regra continua sendo não pôr dado pessoal
em campo de log.

### Riscos aceitos

- CPF ou CNPJ com dígito verificador errado fica no log. Redigir todo número de
  11 ou 14 dígitos apagaria carimbo de tempo e id de pedido.
- CPF ou CNPJ com espaço entre os grupos (`529 982 247 25`) ou colado a outros
  dígitos por pontuação (`7-529.982.247-25`) escapa. Juntar grupos separados
  por espaço confundiria números vizinhos de uma frase.
- No web, `console.error("%s", x)` sai sem a substituição do `%s`: o redator
  junta os argumentos com espaço.

## Destino e retenção

- **Destino:** o log do próprio serviço (stdout e stderr do contêiner). Não há
  coletor, banco nem arquivo à parte.
- **Retenção:** a rotação de log do serviço no host. O valor real precisa ser
  lido no host antes do deploy e registrado na Issue.
- **Quem lê:** o alerta do #472, a cada 5 minutos, que conta as linhas com
  `level` de erro por serviço e por `event`/`marker`.

## Evolução, se o volume pedir

- **A, coletor próprio no host (GlitchTip):** agrupa e mostra o stack
  navegável. O dado não sai da VPS. Custa mais um serviço com banco.
- **B, Sentry SaaS:** zero operação, mas é transferência de dado pessoal para
  terceiro. Exige decisão do Rodrigo, contrato/DPA e registro da região do dado.

Nos dois casos, o redator deste documento roda antes do envio.
