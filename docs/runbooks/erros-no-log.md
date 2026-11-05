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

## O que a redação tira

O redator roda sobre o texto que de fato sai, inclusive mensagem e stack:

- query string de qualquer URL, que vira `?<REDACTED>`;
- valor de `authorization`, `proxy-authorization`, `cookie`, `set-cookie` e
  `x-api-key`, no formato `Nome: valor` ou `"nome": "valor"`;
- e-mail;
- CPF e CNPJ, pontuados ou não, só quando o dígito verificador bate;
- sequência de 32+ caracteres `[A-Za-z0-9_-]` que não seja UUID nem slug, e, na
  API, também base64, credencial em URL e cartão.

API: `apps/api/src/appsell_api/redacao_pessoal.py` mais `logging_setup.py`.
Web: `lib/redacao.ts`. As duas implementações seguem as mesmas regras, e cada
uma tem teste que lê a linha escrita: `test_redacao_pessoal_no_log.py` e
`tests/error-log.test.ts`.

É rede de segurança, não garantia. A regra continua sendo não pôr dado pessoal
em campo de log.

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
