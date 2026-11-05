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
| Tamanho | 8 KiB por pedido, contado no que chega, não só no `content-length`. Acima disso, 413 |
| Taxa | 120 relatos por minuto por processo. O excesso é descartado com 204 e vira uma linha `appsell.csp.dropped` com a contagem |
| O que é gravado | uma linha `appsell.csp.violation` por violação, com `directive`, `blocked`, `document_path`, `source`, `line`, `column` e `disposition` |
| O que nunca é gravado | query string, fragmento, `script-sample`, IP, user-agent e qualquer sequência de 32+ caracteres `[A-Za-z0-9_-]`, que sai como `<REDACTED>`. No link de acesso (`/c/<token>`), o caminho inteiro sai mascarado |

O limite de taxa é global por processo, então uma enxurrada também esconde
relatos legítimos por até um minuto. No modo só-relatório isso é aceitável. No
modo bloqueante, revisar.

## Retenção

Nada vai para banco nem para arquivo próprio. O relato vive só no log do
serviço web, com nível `warn`: ele não dispara o alerta de erro do #472, que lê
só `error` e acima. A retenção é a rotação de log do serviço, e o valor real
precisa ser lido no host antes do deploy e registrado na Issue.

## A semana de leitura

1. Depois do deploy, ler os relatos uma vez por dia, agrupados por
   `directive` e `blocked`. Só os campos acima existem.
2. Para cada origem legítima (storage, Financial), ajustar a política neste
   arquivo, em PR.
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
