# Política de conteúdo (CSP) em modo só-relatório (#548)

## O que muda

Em produção, toda página do web passa a enviar dois cabeçalhos:

- `Content-Security-Policy-Report-Only`, com a política de `lib/csp-report.ts`;
- `Reporting-Endpoints: csp="/api/csp-report"`.

O navegador **não bloqueia nada**: só avisa o que a política teria barrado. A
loja, o checkout e a biblioteca carregam e funcionam como antes. O `next dev`
não recebe o cabeçalho, porque o modo de desenvolvimento usa `eval` e encheria
os relatos de ruído.

A política começa apertada de propósito: só `'self'`, com `data:` e `blob:`
onde a página já usa. O que faltar nela (o storage das imagens assinadas, o
checkout do Financial, algum script de terceiro) aparece nos relatos, e é isso
que a semana de leitura precisa ver.

## O endpoint `/api/csp-report`

É público, porque o navegador de qualquer visitante manda o relato. Por isso:

| Proteção | Valor |
|---|---|
| Tipos aceitos | `application/csp-report`, `application/reports+json`, `application/json`. Outros recebem 415 |
| Tamanho | 8 KiB por pedido para `csp-report` e `json`; 64 KiB para `reports+json`, porque o Chrome junta os relatos de uma página num lote só. Contado no que chega, não só no `content-length`. Acima disso, 413 |
| Violações por pedido | no máximo 20 linhas gravadas por pedido. O resto do lote conta como descartado |
| Taxa | 120 violações gravadas por minuto por processo, contadas por violação, não por pedido. O excesso é descartado com 204 e vira uma linha `appsell.csp.dropped` com a contagem, na primeira gravação da janela seguinte |
| Envio interrompido | se o navegador fecha a conexão no meio do envio, 400, sem log |
| O que é gravado | uma linha `appsell.csp.violation` por violação, com `directive`, `blocked`, `document_path`, `source`, `line`, `column` e `disposition` |
| O que nunca é gravado | query string, fragmento, userinfo, `script-sample`, IP, user-agent e qualquer sequência de 32+ caracteres `[A-Za-z0-9_-+=%]`, que sai como `<REDACTED>`. No link de acesso (`/c/<token>`), o caminho inteiro sai mascarado em `document_path`, `blocked` e `source` |

**Limite conhecido da troca por `<REDACTED>`.** A `/` não entra na contagem:
com ela, todo caminho longo sumiria inteiro e o relato ficaria ilegível. Um
token em base64 padrão com `/` no meio pode sair em pedaços menores que 32
caracteres. Hoje isso não vaza nada: os tokens do Sell são hexadecimais ou
base64url, e a query string, onde ficam as assinaturas, nunca é gravada. Se
algum dia um token com `/` passar a ir no caminho, revisar esta regra.

O efeito colateral oposto também existe: um trecho legítimo de 32+ caracteres
sem separador, como um slug de produto longo, sai como `<REDACTED>`. Para a
leitura, o caminho da página ainda mostra em que loja o relato nasceu.

O limite de taxa é global por processo, então uma enxurrada também esconde
relatos legítimos por até um minuto. No modo só-relatório isso é aceitável. No
modo bloqueante, revisar.

## Origens que já se espera ver nos relatos

A política começa só com `'self'`, então estas origens legítimas vão aparecer
nos relatos desde o primeiro dia. Não são abuso; são o que a semana de leitura
existe para encontrar e liberar no PR do modo bloqueante:

| Diretiva | Origem | Por quê |
|---|---|---|
| `frame-src` | `https://www.youtube-nocookie.com` | aula com vídeo embutido do YouTube (`lib/lesson-embed.ts`) |
| `frame-src` | `https://player.vimeo.com` | aula com vídeo embutido do Vimeo (`lib/lesson-embed.ts`) |
| `form-action` | `https://checkout.stripe.com` | checkout do Stripe, se ele for aberto por formulário ou redirecionamento de formulário. Hoje o cartão de compra chama a rota por `fetch` e navega para a URL, e essa navegação a CSP não cobre; o relato pode nem aparecer |
| `img-src`, `media-src`, `connect-src` | origem do storage | imagens, vídeos e PDFs servidos por URL assinada |
| `form-action`, `connect-src` | origem do Financial | checkout pelo serviço Financial |

As origens exatas do storage e do Financial saem dos relatos, não deste
documento.

## Para o PR do modo bloqueante

- `script-src 'self' 'unsafe-inline'` sem nonce não protege contra XSS: um
  script injetado na página roda do mesmo jeito. O modo bloqueante deve usar
  nonce (ou hash) com `'strict-dynamic'` e tirar o `'unsafe-inline'` de
  `script-src`.
- Revisar o limite de taxa global por processo (seção acima).

## Retenção

Nada vai para banco nem para arquivo próprio. O relato vive só no log do
serviço web, com nível `warn`: ele não dispara o alerta de erro do #472, que lê
só `error` e acima. A retenção é a rotação de log do serviço, e o valor real
precisa ser lido no host antes do deploy e registrado na Issue.

## A semana de leitura

1. Depois do deploy, ler os relatos uma vez por dia, agrupados por
   `directive` e `blocked`. Só os campos acima existem.
2. Para cada origem legítima (storage, Financial), ajustar a política neste
   arquivo, em PR. Uma origem só entra na política depois de confirmada no
   código ou no host. Aparecer num relato não basta, porque qualquer um pode
   mandar um relato com a origem que quiser.
3. Depois de sete dias, ou quando os relatos restantes forem só de extensões
   do navegador, propor o modo bloqueante, em PR separado.

## Páginas conferidas, sem regressão

Conferir em produção, depois do deploy, com o console do navegador aberto. No
modo só-relatório, um aviso `[Report Only]` no console é esperado e não é
regressão. Regressão é qualquer coisa que deixe de carregar ou de funcionar.

| Página | Caminho |
|---|---|
| Início | `/` |
| Vitrine da Hub2You | `/s/hub2you` |
| Página de um produto da Hub2You | `/s/hub2you/p/<produto>` |
| Início do checkout de um produto | botão de compra na página do produto |
| Biblioteca do comprador | `/library` |
| Uma aula com vídeo e uma com PDF | `/library/items/<id>/lessons/<id>` |
| Painel do vendedor | `/dashboard` |
| Configurações da loja | `/settings/store` |
| Termos, privacidade e cookies | `/termos`, `/privacidade`, `/cookies` |

No PR do modo bloqueante, a mesma lista vale com outro critério: **nenhum erro
de CSP no console**.

## Rollback

Reverter o commit deste PR e fazer o deploy do web. O cabeçalho some, e o
endpoint deixa de existir. Não há dado para limpar.
