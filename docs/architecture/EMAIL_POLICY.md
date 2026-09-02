# Política de e-mail — remetentes, natureza e descadastro

- Status: decidida em 2026-09-01 (issue [#355](https://github.com/autonom-ia/appsell/issues/355), fecha a `D-029`)
- Owner: Produto + Infra + LGPD
- Escopo: todo e-mail que a plataforma envia a uma pessoa real. Não cobre o
  formulário de contato da página pública, que é e-mail **de entrada** e segue
  pelo remetente comercial.

Esta página é a regra. O canal de envio (`#356`), os templates (`#357`) e a fila
(`#358`) implementam o que está aqui; se divergirem, a página vence e o código
está errado.

## 1. Dois remetentes, nunca cruzados

| Remetente | Conta | Usa para | Variáveis |
|---|---|---|---|
| **Transacional** | `sell@autonomia.site` | todo e-mail transacional (§2) a compradores, vendedores e membros de equipe — hoje o único canal de saída | `SMTP_*_TRANSACIONAL` |
| **Comercial** | `sell.comercial@autonomia.site` | receber o formulário da página pública (`POST /api/contato`) | `SMTP_*_COMERCIAL` |
| **Opcional** | **sem remetente definido** | não existe nesta rodada; nasce junto da tela de preferência, com conta própria — nunca `sell@` nem a comercial | — |

Separar não é organização: é reputação. Provedores pontuam **cada endereço**
e o domínio. Um formulário comercial marcado como spam por um destinatário
derrubaria a entrega da confirmação de compra se os dois saíssem da mesma
conta. Por isso:

- nenhum envio transacional usa `SMTP_*_COMERCIAL`, nem como fallback quando
  o transacional não está configurado. Requisito para o canal da `#356`: sem
  variáveis, ele **não envia** e registra que não enviou;
- nenhum e-mail de contato, prospecção ou marketing sai de `sell@autonomia.site`.

Nome de exibição do remetente transacional: **Autonom.ia Sell**. `From` é
sempre `sell@autonomia.site`; o vendedor nunca aparece como remetente, porque o
domínio dele não assina DKIM pelo nosso SMTP e o e-mail seria classificado como
falsificação.

`Reply-To` é o próprio `sell@autonomia.site`, cuja caixa é lida no Google
Workspace. Quando o assunto é do vendedor (dúvida sobre o curso), o corpo traz o
contato dele; a resposta ao e-mail não vai ao vendedor.

## 2. Natureza: o que é transacional e o que é opcional

A pergunta que classifica: **se a pessoa não receber este e-mail, ela perde
acesso, dinheiro, segurança ou um direito?** Se sim, é transacional.

### Transacional — não se descadastra

A base legal é **por evento**, e nenhuma delas é consentimento — por isso não
há opção de saída: a saída deixaria a pessoa sem o que ela comprou, sem aviso
do que aconteceu com a conta dela, ou sem um aviso que a lei nos obriga a dar.

| E-mail | Destinatário | Gatilho | Base legal (LGPD) |
|---|---|---|---|
| Acesso liberado / confirmação de compra | comprador | entitlement concedido (checkout pago ou acesso gratuito) | execução de contrato, art. 7º, V |
| Recibo ou comprovante da compra | comprador | pagamento confirmado | execução de contrato, art. 7º, V — o recibo confirma a contratação; a guarda fiscal do documento é retenção, não envio |
| Convite para a equipe da loja | pessoa convidada | convite emitido (`SPEC_CONVITES_MEMBROS.md`) | legítimo interesse, art. 7º, IX — o e-mail diz **quem** convidou e **para qual loja**, é enviado **uma vez** por convite e não é reenviado sem novo pedido do emissor |
| Segurança da conta | titular | login novo, alteração de e-mail | legítimo interesse e prevenção à fraude, art. 7º, IX |
| Confirmação de pedido de exclusão | titular | pedido registrado | atendimento a direito do titular, art. 18, VI e §§ 3º e 4º — obrigação legal, art. 7º, II |
| Loja suspensa ou reativada | dono da loja | mudança de status pela plataforma | execução de contrato, art. 7º, V |
| Alteração **relevante** dos Termos de Uso | contas que a mudança atinge: vendedores, compradores ou ambos | nova versão com mudança relevante — o critério é o do próprio documento (Termos §14); revisão de redação não gera e-mail | execução de contrato, art. 7º, V |
| Alteração **relevante** da Política de Privacidade | **titulares afetados**: quem tem dados tratados na finalidade que mudou (mudança no tratamento de dados do Aluno vai a compradores; do Vendedor, a vendedores; das duas, a ambos) | nova versão com mudança relevante (Privacidade §12) | transparência ao titular, arts. 6º, VI e 9º — obrigação legal, art. 7º, II |
| Incidente de segurança com risco relevante | titulares afetados | dever do controlador, art. 48, no prazo e forma da ANPD (Privacidade §11); o encarregado executa, não decide se comunica | obrigação legal, art. 7º, II |

### Opcional — só com consentimento, e se descadastra em um clique

Base legal: consentimento (art. 7º, I), revogável a qualquer momento.

| E-mail | Condição |
|---|---|
| Lembrete de conteúdo não concluído | comprador optou por receber |
| Novidades da loja (novo produto, novo módulo) | comprador optou por receber daquela loja |
| Comunicação da plataforma (novidades do produto Sell) | vendedor optou por receber |

**Nesta rodada, nenhum e-mail opcional é enviado.** Não existe preferência de
notificação no produto — ficou fora do escopo por decisão de 01/09, no plano
de configurações por papel (épico `#349`) —, logo não existe consentimento
registrado, logo não existe base legal para enviar. O canal opcional só nasce
junto da tela de preferência, com remetente próprio, e obedece à RFC 8058:
link de descadastro em um clique **sem login**; cabeçalho `List-Unsubscribe`
com URI **HTTPS**; `List-Unsubscribe-Post: List-Unsubscribe=One-Click`, e o
`POST` nessa URI descadastra sem pedir confirmação; os dois cabeçalhos cobertos
pela assinatura DKIM.

Uma comunicação que mistura as duas naturezas é opcional: um "seu acesso foi
liberado, e veja também estes outros cursos" **não** pode sair como
transacional. Transacional diz uma coisa só.

## 3. Rodapé obrigatório

Todo e-mail transacional termina com o mesmo bloco, em texto e em HTML:

```
Autonom.ia Sell · enviado por Autonom.ia Tecnologia Ltda (CNPJ 59.303.298/0001-51)
Este é um e-mail transacional sobre a sua conta ou a sua compra; você o recebe
porque <motivo em uma linha, ex.: "comprou o curso X na loja Y">. Ele não
depende de inscrição e não tem descadastro.
Dúvidas sobre seus dados: contato@autonomia.site · Política de Privacidade:
https://sell.autonomia.site/privacidade
```

O e-mail opcional troca a terceira frase por:

```
Você recebe porque optou por receber <o quê> em <data>. Para parar:
<link de descadastro em um clique, sem login>.
```

O rodapé identifica **remetente**, **natureza** e **motivo**. Um destinatário
que não entende por que recebeu marca como spam; o motivo em uma linha é a
mitigação mais barata que existe.

## 4. O que nunca vai no corpo

- senha, código de acesso permanente ou token de sessão;
- link de uso único com validade acima de **7 dias** para convite e acima de
  1 h para segurança de conta. Sete e não 24 h porque convite de equipe é
  aberto quando o convidado vê, não na hora, e 24 h gera reenvio; e não 14
  dias porque é um link que concede papel na loja (decisão do Rodrigo em
  02/09);
- dados de terceiros: o comprador não vê dados de outro comprador, o convidado
  não vê a lista da equipe;
- dados de pagamento além dos quatro últimos dígitos e do valor;
- qualquer rastreador de abertura ou clique. Telemetria de entrega é o que o
  SMTP devolve, não pixel no corpo (`D-030`).

## 5. Segredo do remetente

A senha de `sell@autonomia.site` vive **apenas** em `SMTP_PASS_TRANSACIONAL`,
no env do serviço que envia. Nunca em commit, em `.env.example`, em log, em
mensagem de erro, em documento ou em chat. Erro de autenticação SMTP é
registrado com host, porta e código da resposta — nunca com o usuário e a senha
tentados.

Rotação: trocar a senha na Hostinger, atualizar a variável no serviço com
`--env-add` numa única atualização (nunca `updateEnv` do painel sem o campo
`env` completo), validar com o smoke da §7.

## 6. Estado de entrega do domínio (verificado em 2026-09-01)

| Registro | Valor encontrado | Situação |
|---|---|---|
| SPF `autonomia.site` | `v=spf1 include:_spf.mail.hostinger.com include:amazonses.com ~all` | passa para o SMTP da Hostinger; o include do SES é de outro sistema e não deste canal |
| DKIM `hostingermail-a._domainkey` | CNAME para `hostingermail-a.dkim.mail.hostinger.com`, chave RSA publicada | assina |
| DMARC `_dmarc` | `v=DMARC1; p=none` | observação apenas; **sem `rua=`, o domínio não recebe os relatórios agregados** — os destinatários seguem avaliando DMARC, mas nós não vemos o resultado |
| MX | Google Workspace | recebe pelo Google, envia pela Hostinger — assimétrico e funcional |

Consulta feita em 2026-09-01 23:40 -03 pelo resolvedor `dns.google` (DoH), e
reproduzível assim:

```sh
for n in "autonomia.site&type=TXT" "_dmarc.autonomia.site&type=TXT" \
         "hostingermail-a._domainkey.autonomia.site&type=TXT" "autonomia.site&type=MX"; do
  echo "## $n"; curl -s "https://dns.google/resolve?name=$n" | jq -r '.Answer[]?.data'
done
```

Saída literal daquela consulta; a única edição é o corte da chave DKIM,
marcado com `[...]`:

```
## autonomia.site&type=TXT
google-site-verification=pWPDrMgJ6sdHiM-EliYh5mddAF4LB602mq11bhUeJQ8
v=spf1 include:_spf.mail.hostinger.com include:amazonses.com ~all
## _dmarc.autonomia.site&type=TXT
v=DMARC1; p=none
## hostingermail-a._domainkey.autonomia.site&type=TXT
hostingermail-a.dkim.mail.hostinger.com.
v=DKIM1;k=rsa;p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAs+RijpkYCOFcf5HdHXcwSJJdaqCIf9jyybiM8GFNhzhQMYKXmKGefVdmAIaqcKstjtkCMktZRl6uqrd+gnAsslRoEOtfBbWuqtL/1NRovLakhL[...]
## autonomia.site&type=MX
10 ALT3.ASPMX.L.GOOGLE.COM.
5 ALT2.ASPMX.L.GOOGLE.COM.
1 ASPMX.L.GOOGLE.COM.
10 ALT4.ASPMX.L.GOOGLE.COM.
5 ALT1.ASPMX.L.GOOGLE.COM.
```

Consequência para as próximas decisões, ambas mudanças de DNS que exigem OK
explícito na hora:

1. **Antes** de endurecer o DMARC, acrescentar `rua=mailto:<caixa de
   relatórios>` ao registro. Hoje o domínio não recebe relatório agregado
   nenhum, então "ler os relatórios por algumas semanas" não é possível sem
   esse passo.
2. Só depois de semanas de relatório sem falha legítima, subir para
   `p=quarantine`. Endurecer antes de observar derruba e-mail legítimo.

## 7. Verificação de aceite

O smoke `scripts/email/smoke-transacional.mjs` envia um e-mail de teste pelo
remetente transacional com as variáveis do ambiente onde roda, e imprime só o
que o SMTP devolve (aceitos, rejeitados, `Message-ID`). Nunca imprime
credencial, nem em erro. Roda de um checkout com dependências instaladas, não
de dentro da imagem do `web` (standalone, sem `scripts/`). As variáveis entram
pelo ambiente de um subshell, lidas do arquivo de segredos — nunca digitadas
na linha de comando:

```sh
( set -a; . "$ARQUIVO_DE_SEGREDOS"; set +a; \
  node scripts/email/smoke-transacional.mjs destinatario@exemplo.com )
```

O aceite não é "o SMTP aceitou"; é o cabeçalho **recebido** na caixa de
destino mostrar:

```
Authentication-Results: ... spf=pass ... dkim=pass header.d=autonomia.site ... dmarc=pass
```

`spf=pass` sem `dkim=pass` quer dizer que a Hostinger não assinou — conferir
se a conta está no mesmo plano de e-mail que a comercial.

## 8. Reputação e ritmo de entrada

Hipótese operacional, não medição: o domínio não tem histórico de envio
transacional, e reputação se constrói pela ausência de reclamação e pelo
engajamento do destinatário — que não medimos, porque não há rastreador no
corpo (§4). O que se mede é o que o SMTP e a caixa `sell@` devolvem: aceites,
rejeições 5xx e 4xx, devoluções e reclamações que chegam ao `postmaster`. A
ordem de ligação segue a expectativa de engajamento:

1. acesso liberado / confirmação de compra — aberto por quase todo destinatário;
2. convite de equipe — pedido pela própria pessoa que convida;
3. segurança de conta e avisos de status;
4. alteração de termos, em lote, só depois de os três acima rodarem sem
   reclamação por algumas semanas.

Rejeição permanente (5xx) marca o endereço e a fila não tenta de novo; rejeição
temporária (4xx) tenta com recuo. O detalhe é da `#358`; a regra é esta.

## 8.1. Contrato de deploy do serviço que envia

O worker vive no processo da API e pode estar no meio de um envio quando o
deploy começa. O serviço `api` precisa de **`--stop-grace-period 330s`** — o
lease de 300 s mais folga —, senão o orquestrador mata o processo entre o
`send` aceito e o registro, e o e-mail sai de novo na tentativa seguinte (com
o mesmo `Message-ID`, que é a mitigação, não a garantia).

Um comando só liga o canal e dá o tempo — imagem, variáveis e graça na mesma
atualização, para um único `stop-first`. Os valores vêm do arquivo de
segredos para o ambiente do shell, com os **mesmos nomes** que o serviço usa;
nada é digitado na linha de comando, que fica no histórico:

```sh
set -a; . "$ARQUIVO_DE_SEGREDOS"; set +a   # define SMTP_*_TRANSACIONAL
docker service update \
  --image easypanel/sell-autonomia/api:<sha> \
  --stop-grace-period 330s \
  --env-add SMTP_HOST_TRANSACIONAL="$SMTP_HOST_TRANSACIONAL" \
  --env-add SMTP_PORT_TRANSACIONAL="${SMTP_PORT_TRANSACIONAL:-465}" \
  --env-add SMTP_SECURE_TRANSACIONAL="${SMTP_SECURE_TRANSACIONAL:-true}" \
  --env-add SMTP_USER_TRANSACIONAL=sell@autonomia.site \
  --env-add SMTP_PASS_TRANSACIONAL="$SMTP_PASS_TRANSACIONAL" \
  --update-order stop-first sell-autonomia_api
```

Confira antes e depois — o backup da spec vem antes de tudo, e a comparação é
nome a nome, não contagem:

```sh
docker service inspect sell-autonomia_api --format '{{.Spec.TaskTemplate.ContainerSpec.StopGracePeriod}}'
```

## 9. Rollback

Remover as cinco variáveis `SMTP_*_TRANSACIONAL` do serviço `api` desliga o
canal: o worker não inicia, registra `email_worker_disabled`, e as linhas de
`app_private.email_outbox` ficam pendentes — nada se perde, e voltam a sair
quando as variáveis voltarem. O downgrade da migration `0049` só depois de ter
certeza de que nada pendente precisa sair, porque ele apaga a caixa.
