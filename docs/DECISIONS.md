# DECISIONS — decisões operacionais atuais

Última atualização: 2026-09-02.

O registro arquitetural detalhado está em
[DECISION_REGISTER.md](architecture/DECISION_REGISTER.md) e nos ADRs. Esta
página contém somente as decisões que orientam a operação atual.

## Decisão 001 — sessão e identidade

O browser recebe somente sessão protegida/opaca. A autorização permanente usa
o identificador imutável da identidade, não comparação dinâmica de e-mail.
O Identity real está integrado ao fluxo de produção; mocks de identidade
continuam limitados a Local/CI.

## Decisão 002 — hierarquia multitenant

`comercial@autonomia.site` é o `platform_superadmin` da plataforma. O grant
global não cria memberships falsas. Cada cliente opera dentro do seu tenant por
`seller_owner`; buyer é identidade global e recebe acesso por entitlement.

## Decisão 003 — tenants reais atuais

O ambiente de produção possui os tenants ativos `autonomia` e `hub2you`. O
superadmin pode listar e administrar o catálogo dos dois; um seller owner não
atravessa o tenant de outro seller.

## Decisão 004 — jornada de publicação

O editor segue Produto → Currículo → Oferta → Preview → Publicar. O catálogo é
publicado como snapshot versionado. Ativar vendas/checkout fica na etapa
Publicar e depende de catálogo publicado; publicação e cobrança são operações
distintas.

## Decisão 005 — dois modos de acesso

- **Gratuito:** link público → cadastro/login → entitlement idempotente →
  biblioteca; sem checkout e sem cobrança.
- **Pago:** oferta ativa → checkout → confirmação/fulfillment → entitlement;
  conteúdo não é liberado antes da confirmação.

O browser nunca decide preço final, pagamento ou entitlement.

## Decisão 006 — conteúdo privado

PDF/MP4 somente entra em publicação após upload, armazenamento e verificação de
segurança. O aluno acessa aula/PDF pela biblioteca e a API revalida buyer,
seller, produto, publicação e asset em cada leitura.

## Decisão 007 — produção na VPS

O destino operacional do Appsell é a VPS EasyPanel nativa, não AWS/EC2. O SHA
comprovado em produção é `2c8a36897eadf624955810ffdaee7b33c6239334`. Qualquer
novo cutover exige backup, rollback e smoke no mesmo SHA.

## Decisão 008 — produção não usa demo

Reset, seed e personas demo são exclusivamente Local/CI descartável. Não podem
ser executados em `appsell_native_90` nem no domínio público.

## Decisão 009 — dados e rollback

Não apagar/recriar tenant, produto, PDF ou membership para corrigir fluxo.
Migrations são forward-only; rollback de aplicação usa imagem anterior e
rollback de dados usa restore ensaiado em banco separado.

## Decisão 010 — os limites de mídia vivem no catálogo, e mudam em todas as camadas

Cada limite aparece em várias camadas: Python, TypeScript, restrições do banco,
funções PL/pgSQL. **Alargar parte delas não deixa o limite valendo pela metade —
muda a porta em que o vendedor bate**, do envio, onde a mensagem é clara, para o
`INSERT`, onde ele vê um erro de banco.

Uma mudança de limite só está pronta quando passa por todas. `test_limites_de_midia.py`
varre `lib/`, `app/`, `apps/api/src`, `deploy/` e `scripts/` procurando número
cru — três versões desse teste foram furadas por olhar só um padrão, só uma
linguagem, ou só a lista de arquivos de que alguém se lembrou.

O catálogo (`asset_media.py` / `lib/asset-media.ts` / `app_private.asset_media_*`)
é a fonte única de tamanho por tipo. Um módulo que precise de um teto próprio
tem de justificar por que não é o do catálogo — foi assim que o vídeo do PDF
ficou com 512 MiB enquanto o catálogo dizia 100.

Desde 31/08/2026 esse teto próprio também mora no catálogo, como
`PDF_LINK_VIDEO_MAX_SOURCE_BYTES` e `PDF_LINK_VIDEO_MAX_DERIVATIVE_BYTES`, com a
justificativa escrita ao lado: são caminhos diferentes (envio do vendedor num
`PUT` único de 300 s contra download nosso de 15 min), e não o mesmo número em
lugares diferentes. Antes disso o valor estava escrito à mão em cinco pontos, e
`MAX_VIDEO_BYTES` chegou a existir com dois valores distintos — 100 MiB no
catálogo e 512 MiB dentro de uma rota do BFF. `TestTetoDeVideo` fecha as duas
portas: número cru e nome repetido com valor diferente.

## Decisão 011 — número de limite vem de medição, não de estimativa

O teto de 300 páginas veio de medir a rasterização de um documento real de 300
páginas dentro do serviço isolado, não de arredondar para cima. A medição mudou o
desenho duas vezes: mostrou que paralelizar sem núcleo é **mais lento**, e que a
duração máxima de vídeo de 4 h nunca poderia ser entregue, porque recodificar
custa mais tempo do que o próprio vídeo.

Antes de subir um teto, medir o caminho inteiro e cruzar com os prazos que já
existem — o prazo do `pdftoppm` dava 164 páginas enquanto o código declarava 300.

## Decisão 012 — toda migration ensina o banco a se reconhecer

`app_private.schema_ready()` guarda a lista das versões conhecidas, e o
`/health/ready` consulta ela. Como esse endpoint alimenta o `HEALTHCHECK` do
contêiner, uma migration que sobe a versão sem estender a lista não devolve um
número errado: **derruba a API em laço de reinício**.

Toda migration nova estende as duas cópias da função e acompanha
`EXPECTED_MIGRATION`. A 0043 e a 0044 mostram o formato, com `raise` quando a
substituição não encontra o texto — uma troca silenciosa que não troca nada é
como um limite sobrevive a quem pensou tê-lo mudado.

## Decisões ainda pendentes

## Decisão 013 — a página pública é indexável, e as lojas dos vendedores também

`robots.txt` libera a página e as lojas em `/s/<slug>`, e bloqueia área logada,
fluxo de autenticação e link de convite. Cada loja passou a ter título,
descrição e canonical próprios: antes todas serviam o mesmo
`<title>Autonom.ia Sell</title>`, o que as fazia competir entre si e piorava a
cada vendedor novo.

A consequência que precisa ser dita: **o nome do vendedor pode aparecer no
Google associado ao Sell.** Se algum contrato exigir o contrário, será preciso
um controle por loja, que hoje não existe.

As lojas ficam fora do `sitemap.xml` até existir listagem no backend. Endereço
chutado em sitemap vira `404` no relatório do Search Console.

## Decisão 014 — robôs de IA liberados, com a distinção registrada

O `robots.txt` separa os robôs em dois grupos, porque o uso é diferente: os de
**busca** (`OAI-SearchBot`, `PerplexityBot`, `Claude-SearchBot` e afins) leem a
página na hora de responder alguém e podem devolver visita; os de
**treinamento** (`GPTBot`, `ClaudeBot`, `CCBot`, `Google-Extended`) levam o
texto para dentro do modelo sem citar a origem.

Os dois estão liberados enquanto o objetivo for ser descoberto. É decisão de
negócio, não técnica, e trocar o grupo de treinamento para `disallow` é uma
linha em `app/robots.ts`.

Vale registrar o que a auditoria mostrou: **não existe nenhuma menção ao
Autonom.ia Sell na web.** Nenhuma configuração técnica faz um modelo citar o
que não tem o que ler. O caminho é conteúdo e presença em fontes de terceiros.

## Decisão 015 — contagem de variáveis não é prova; comparação de nomes é

A trava de deploy manda abortar se o número de variáveis do serviço cair. Em
01/09 ele foi de 47 para 51 depois de adicionar cinco, e 47 + 5 são 52.

Não houve perda: `PORT` estava duplicada na spec anterior, 47 entradas para 46
nomes, e o `docker service update` deduplicou. **Comparar nomes contra a spec
salva, não contar entradas.** O número sozinho teria provocado um rollback
desnecessário.

## Decisão 016 — a página pública não tem menu no celular, e é de propósito

Medido em 01/09: em 320px sobram **16px** entre a marca e os botões da barra do
topo. Um botão de menu de 44px reintroduziria o corte que #343 acabara de
corrigir, quando o "VENDER" era partido no meio da palavra.

A alternativa, uma faixa horizontal de âncoras abaixo da barra, cabe, mas custa
~40px fixos no topo de todo celular e altera `--topo-h`, que governa o
`scroll-margin` das âncoras e o respiro do hero.

A página tem um objetivo único, cadastrar, e ele está a um toque em qualquer
ponto da rolagem pelo botão fixo. Preço, FAQ e contato têm link no rodapé.

Reabrir só com evidência de comportamento real, não por suposição. Registro em
[#347](https://github.com/autonom-ia/appsell/issues/347).

## Decisão 017 — e-mail transacional sai de `sell@autonomia.site`, e só ele

Decidida em 01/09 (#355, fecha a `D-029`). Todo e-mail transacional —
confirmação de compra, convite de equipe, segurança de conta — sai do
remetente transacional, com variáveis próprias `SMTP_*_TRANSACIONAL`. O
comercial `sell.comercial@autonomia.site` só recebe o formulário da página
pública. Um não é fallback do outro — requisito para o canal da #356: sem
variáveis, ele não envia.

Transacional é o e-mail sem o qual a pessoa perde acesso, dinheiro, segurança
ou um direito; não tem descadastro. O resto é opcional, exige consentimento
registrado e descadastro em um clique — e **nenhum opcional é enviado nesta
rodada**, porque não existe preferência no produto para registrar o
consentimento. Rodapé, base legal e o que nunca vai no corpo estão em
[EMAIL_POLICY.md](architecture/EMAIL_POLICY.md).

Duas mudanças de DNS ficam fora e exigem OK na hora: acrescentar `rua=` ao
DMARC (hoje `p=none` **sem** destinatário de relatório: os provedores avaliam,
mas o domínio não recebe o relatório agregado) e, semanas depois, subir para
`p=quarantine`.

## Decisão 018 — a passagem entre atos do hero toca no tempo, não no scroll

Filmado em 01/09 a 28 fps com roda real: a troca de ato vivia dentro do scrub e
ocupava 17% do curso pinado, ~170 px no desktop e ~120 px no celular. Quem rola
a 1500 px/s atravessava tudo em 150–300 ms, 4 a 8 quadros. O olho lia corte.
Somavam-se dois defeitos: as máscaras `.linha` de atos diferentes não coincidem
em altura, e o título que saía ficava visível por cima do que entrava; e o
indicador lia o scroll cru enquanto o texto seguia a timeline com `scrub`,
110 px de atraso no celular e 220 no desktop.

Agora o scroll só decide **qual** ato está na tela, com histerese de 3% para
não oscilar em cima do limite. A passagem é um tween de duração fixa (saída
0,34 s, entrada 0,62 s), igual com roda lenta, flick ou trackpad; a entrada
começa quando a saída está 96% fora da máscara; indicador, `aria-hidden`,
cortina do aparelho e texto saem do mesmo evento. Foto, parallax, giro do
aparelho e deriva continuam no scrub, porque esses são contínuos por natureza.

Junto: o bloco de texto passou a centrar na altura do aparelho
(`align-content: safe center`). Ancorado no rodapé, toda altura extra da tela
virava vão entre o header e o eyebrow, 418 px medidos a 2000×1090; centrado,
238 px. No celular os atos ancoram no topo da célula: centrar cada um fazia o
eyebrow pular 147 px entre atos. O pin do celular caiu de 180% para 150%, com
limites próprios (24% e 48%) para o ato do preço terminar de entrar antes do
unpin.

O que **não** mudou, por medição: abrir o scrim do celular no terço superior
para a foto voltar reprovou no gate de contraste (3,94:1 sob o eyebrow a
390×844). A foto no celular só volta movendo o texto, não clareando o fundo.
Registro em [#369](https://github.com/autonom-ia/appsell/issues/369).

## Decisão 019 — pedir a exclusão da conta registra o pedido; ninguém apaga na hora

Decidida em 02/09 (#351). Apagar a identidade de quem comprou destrói a prova
da compra, que tem retenção fiscal e contratual. O pedido vira um
`audit_event` (`account.deletion.requested`, ator = a própria pessoa, sem
loja) e a execução é manual, pelo runbook
[PEDIDO_DE_EXCLUSAO_DE_CONTA.md](runbooks/PEDIDO_DE_EXCLUSAO_DE_CONTA.md).
Um pedido fica **aberto** até a operação registrar `account.deletion.completed`
sobre a pessoa; enquanto houver pedido aberto, pedir de novo devolve o
existente. A primeira versão usava uma janela de 30 dias, e a revisão mostrou
o defeito: pedido não atendido "expirava", a tela oferecia o botão de novo e a
pessoa gerava um segundo registro em cima do primeiro. Não há prazo que
expire um direito não atendido.

Junto, duas escolhas: a exportação leva a anotação inteira — inclusive os
traços (`points`) — porque é conteúdo que a pessoa criou e o direito de
portabilidade cobre o que ela escreveu, não só onde e com que cor (a primeira
versão os deixava de fora "por volume", e a revisão derrubou o argumento). E
sai sem `metadata` de auditoria, que pode carregar dado de terceiro. A
exportação tem teto de 8 MiB, recusado pelo banco **antes** de agregar (por
bytes e contagens); acima disso é atendimento manual, pelo runbook.

O que esta onda **não** resolve, e está dito no runbook: a conta de login
vive no Identity, e enquanto a pessoa conseguir entrar, o callback recria
e-mail e nome (`sync_identity`, 0048) por cima de qualquer anonimização
local. A exclusão de verdade começa lá; o runbook segue essa ordem. Uma
marca por `issuer + sub` que `sync_identity` recuse, e revogação
administrativa de sessões, são follow-ups com issue própria. A retenção
final por tabela continua sendo a D-018, pendente de DPO/Jurídico.

E o que a issue pedia — "sem migration" — não cabia: a política
`audit_member_insert` só deixa o runtime escrever auditoria de quem tem
membership numa loja, e o comprador puro não tem. Toda auditoria deste
projeto nasce em função SECURITY DEFINER; as três da conta vivem na `0051`.

## Decisão 020 — a loja tem marca própria; a página do produto vira página de vendas; só dono e admin mexem

Decidida em 03/09 (#352). A vitrine falava a língua da plataforma ("catálogo
isolado e verificável", "Loja oficial na Autonom.ia Sell") e a página do
produto era um recibo. A `0052` dá à loja nome, frase, "sobre", cor de
destaque e logo, e ao produto uma promessa; a vitrine pública passa a devolver
isso, mais a capa e o resumo do conteúdo publicado (títulos de aula e contagem
de materiais, lidos do snapshot vigente — nunca do rascunho).

Cinco escolhas, e por quê:

1. **Editor e suporte ficam fora de Configurações.** A matriz dizia "SE
   condicional"; a issue fechou em negado. Quem escreve aula não troca o logo
   da loja. `resolve_seller_membership_role` decide, e o `coalesce` na guarda
   existe porque quem não tem membership recebe NULL — e `NULL not in (...)`
   não dispara o `if`. O teste de papéis pegou isso na primeira execução.
2. **Contraste é uma régua só: 4,5:1 contra branco.** A cor só aparece sob
   texto branco (botão de compra, fundo da identidade). O BFF recusa ao
   gravar, com a conta na mensagem; a página repete a conta ao pintar e cai no
   tom da Autonom.ia se reprovar. O banco valida só o formato — luminância em
   PL/pgSQL seria a mesma conta em dois lugares.
3. **Os campos novos só entram no JSON da vitrine quando existem, e a ordem
   de deploy é web → api → migration.** O parser do web exige chaves exatas,
   e capa e currículo já existem em produtos publicados: com o banco na
   frente, o web antigo cairia em 503. O web novo aceita as duas formas; a API
   nova troca chave de objeto por URL antes de responder; o banco vai por
   último. A revisão da PR pegou a ordem invertida na primeira versão.
4. **A promessa é campo "ao vivo" do produto, como a capa.** Trocar a frase
   da página de vendas não exige republicar o curso. Segue `catalog.draft.update`
   e a versão esperada; salva à parte do formulário grande para não disputar
   com o autosave.
5. **O logo reusa o verificador da capa, não a tabela dela.** Upload, antivírus,
   reescrita e promoção são os mesmos; a tabela e as chaves são por loja
   (`seller_logo_upload`, `tenants/<loja>/logo/…`), porque a da capa é presa ao
   produto por FK e por chave. O worker ganhou a derivação de chave por tipo e
   nada mais.

E o que a onda **não** faz, por decisão: a área logada continua Autonom.ia (a
biblioteca é multi-loja); nada de marca em e-mail (B3) nem em `og:image`;
FAQ, garantia e depoimentos ficam para a próxima; e, com a opção B escolhida
pelo Rodrigo, a página do produto pago diz "as vendas abrem em breve" até a
#382 fazer a venda de outra loja funcionar — não "Checkout indisponível".

## Decisão 021 — no alto da página de vendas, banner ou capa, nunca as duas; e o produto tem dois formatos

Decidida em 03/09 (#387, #398). Quando o produto ganhou banner, a página de
vendas passou a mostrar banner **e** capa empilhados, e o Rodrigo cortou a
dúvida em três palavras: "é uma ou outra". A regra é da **página de vendas**:
havendo banner do produto, aparece o banner; não havendo, a capa ocupa o mesmo
lugar, para nenhum produto abrir sem imagem. Nada de empilhar, nada de espaço
vazio.

Na **vitrine** o alto é outro lugar e outra imagem: quem manda ali é o banner
da **loja** (Decisão 020 e o release `a49e017`). Sem banner da loja, o hero
abre pela identidade, e a capa do produto aparece só nos cartões — não há, nem
deve haver, fallback de banner do produto no topo da vitrine.

Cinco escolhas, e por quê:

1. **Dois formatos, dois papéis.** Capa 16:9 é miniatura (cartão do painel,
   vitrine, biblioteca); banner 16:5 é a faixa larga do alto. Antes disso a
   mesma imagem servia aos dois lugares e era cortada em um deles. Pedir ao
   vendedor duas versões é aceitável — o que não é aceitável é cortar a
   imagem dele sem avisar.
2. **O painel explica em português por que precisa de cada uma**, e avisa
   quando a proporção enviada foge mais de 8% do esperado. Avisa, não recusa:
   o vendedor decide se aquilo serve.
3. **Sem versão quadrada para celular.** Foi cogitada e descartada: com "uma
   imagem ou outra", o banner 16:5 já se comporta no celular, e uma terceira
   versão só aumentaria o trabalho do vendedor.
4. **A miniatura do painel é 16:9, igual à imagem enviada.** O recorte
   quadrado dos cartões era decisão de layout que contradizia o formato que o
   próprio painel pede.
5. **Quando a folha de estilo controla a largura de um `<img>` que carrega
   `width`/`height` do arquivo, `height: auto` é obrigatório.** Os atributos
   entram como estilo do navegador: sobrescrever só a largura deixa a altura
   original de pé, e o `aspect-ratio` declarado é ignorado — uma capa de
   1683x954 desenhava um quadro de 200x954. A regra não vale para quem define
   as duas dimensões de propósito (`.lp-banner-image` e
   `.storefront-product-cover` usam `height: 100%` dentro de um contêiner que
   já tem a proporção). Foi o defeito real por trás de mais de uma "imagem
   cortada" desta onda.

E o que a onda **não** faz: não retoca imagem enviada (nada de corte
automático nem geração de variantes), não guarda versão por dispositivo, e não
remove os atalhos com os nomes antigos das funções de capa — eles seguem até
uma migration futura, porque foram o que permitiu entregar sem janela de
manutenção.

## Decisão 022 — o Sell passa a ser tocado por trilhas, e o merge de PR seguro é delegado

Em 23/10/2026, numa sessão do Claude Code com a Orquestração Sell, o Rodrigo
aprovou o plano do programa (`docs/programa/PROGRAMA.md`) e escreveu: "Sim.
Delego o merge de PR seguro." O pedido está registrado na Issue #435, e o OK
dele para este PR fica citado no próprio PR.

O que muda:

- o trabalho é dividido em trilhas, cada uma dona de pastas
  (`docs/programa/TRILHAS.md`), coordenadas pela Orquestração Sell, até 8 chats
  no pico, com rampa por condição;
- a Orquestração Sell pode mergear sem perguntar **só** o PR que cumpre o
  critério de PR seguro do `AGENTS.md`, seção 6. Todo o resto continua com o OK
  explícito do Rodrigo;
- mudança no próprio critério (seções 6 e 7 do `AGENTS.md`, `TRILHAS.md`,
  `.claude/agents/`) nunca é PR seguro.

Por quê: na Orquestração Deploy do Chat2You, a fila de merge na mão de uma
pessoa só virou gargalo quando vários chats passaram a trabalhar em paralelo.
A delegação é estreita de propósito: nada de dinheiro, acesso, infra,
dependência ou dado pessoal.

## Pendências que bloqueiam ampliação

- staging separado e DNS próprio;
- RPO/RTO formal e operação de backup off-host;
- refund, cancelamento, chargeback e grace period;
- recebimentos/Stripe por tenant e responsabilidades financeiras.

Essas pendências não anulam a release comprovada, mas bloqueiam qualquer
ampliação fora do escopo atual.
