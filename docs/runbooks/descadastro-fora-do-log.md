# Token de descadastro fora do log de acesso (#561)

## Por que

O descadastro em um clique (RFC 8058) recebe o token na URL:
`POST /api/email/unsubscribe?t=<token>`. Quem tem o token descadastra a
pessoa. O provedor de e-mail faz o POST na URL do cabeçalho
`List-Unsubscribe`, então o token não pode ir no corpo nem no fragmento.

O log de acesso da borda (Traefik) grava o caminho com a query. Sem esta
mudança, todo descadastro deixaria um token válido no log.

O que já não leva token e fica como está:

- a página `/e/descadastro` usa o fragmento (`#t=`), que o navegador não manda
  ao servidor;
- a web repassa o token à api no corpo (`/v1/public/email/unsubscribe`), e a
  api não loga o corpo;
- o log de erro da web corta a query (#576).

## O que muda

`deploy/easypanel-native/appsell-descadastro.traefik.yaml` cria dois routers
(http e https) para `Host(sell.autonomia.site) && PathPrefix(/api/email/unsubscribe)`.
Eles têm prioridade 200, acima dos routers genéricos da loja (0), e
`observability.accessLogs: false`. O destino é o mesmo serviço da web, com os
mesmos middlewares (`redirect-to-https` no http, `bad-gateway-error-page`).

Só esse caminho perde a linha de log. Os outros caminhos da loja e os outros
projetos não mudam. O log global continua ligado.

**Precisa estar no ar antes do primeiro tipo de e-mail opcional** (o primeiro
link de descadastro de verdade). Hoje nenhum e-mail leva o link.

## Aplicar (com OK do Rodrigo)

Pré-requisitos: Traefik 3.4 ou mais novo (a opção por router existe desde
então) e o log de acesso global ligado.

1. **Antes:** rodar o smoke da Hub2You (AGENTS.md §7) e guardar a saída.
2. **Copiar** o arquivo do `main` do merge, com o `sha256sum` conferido contra o
   do repositório, para `<pasta de config dinâmica do Traefik>/appsell-descadastro.yaml`,
   com o mesmo dono e permissão dos arquivos ao lado. O provider de arquivo
   recarrega sozinho, sem reiniciar o Traefik.
3. **Conferir que o Traefik aceitou:** o log do Traefik não pode mostrar
   erro de configuração citando `appsell-descadastro` no minuto seguinte.
4. **Conferir que o caminho funciona e não loga:**

   ```sh
   curl -s -o /dev/null -w '%{http_code}\n' -X POST \
     'https://sell.autonomia.site/api/email/unsubscribe?t=sonda-sem-valor-0000'
   ```

   Esperado: `200`. É a mesma resposta para token desconhecido, e um token de
   sonda não descadastra ninguém. Depois, o log de acesso do Traefik não pode
   ter `sonda-sem-valor`. Uma página da loja, chamada logo depois, continua
   aparecendo no log.
5. **Depois:** o smoke da Hub2You precisa dar igual ao do passo 1.

Qualquer passo diferente do esperado: voltar.

## Volta

Remover `<pasta de config dinâmica do Traefik>/appsell-descadastro.yaml`. O
provider recarrega, e o caminho volta para os routers genéricos (com log).
Depois, o smoke da Hub2You.
