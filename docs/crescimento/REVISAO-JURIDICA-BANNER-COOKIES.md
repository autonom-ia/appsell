# Revisão jurídica: banner de cookies e atribuição de campanha

Refs #563. Trilha 4 · Crescimento. Vai junto com os pedidos de parecer do #547
(Asaas e Pagar.me).

**Estado:** o código está no ar e **desligado** para todas as lojas. Nenhuma
loja liga o banner antes do OK deste documento. Tudo abaixo foi lido no código
da `main`, não estimado. Cada item cita o arquivo de onde saiu.

## 1. O que pedimos ao jurídico

1. Aprovar ou corrigir o **texto do banner** (seção 2).
2. Confirmar a **base legal** e o **papel de cada parte** (seção 5).
3. Aprovar os **prazos de guarda** (seção 4).
4. Aprovar as **mudanças na Política de Cookies e na Política de Privacidade**
   (seção 6). Hoje as duas dizem que não há cookie opcional. Precisam mudar
   antes de ligar o banner.
5. Responder às **perguntas abertas** (seção 7).
6. Aprovar o **texto da caixa de consentimento de e-mail** da loja (seção 9),
   que entra junto com a base de leads (#619).

Se o texto mudar, a versão do texto sobe (`v1` → `v2`). Quem já escolheu vê o
banner de novo.

## 2. Texto do banner (versão `v1`)

Fonte: `lib/tracking/banner-text.ts`.

> **Cookies**
>
> Esta loja usa um cookie para saber de qual link ou campanha você veio, por até
> 7 dias. Ele não guarda seu nome, e-mail nem IP. Se você comprar, a loja vê só a
> campanha que trouxe a venda. Você pode recusar, e a loja funciona do mesmo
> jeito. [Política de cookies]
>
> [Aceitar] [Recusar]

Como o banner se comporta (`lib/tracking/consent.ts`, `banner-logic.ts`,
`consent-banner.tsx`, `rastreio-da-loja.tsx`):

- aparece nas páginas públicas da loja: vitrine, página do produto e página de
  acesso ao produto;
- **a recusa é o padrão.** Enquanto a pessoa não escolhe, nenhum cookie de
  campanha é gravado;
- "Aceitar" e "Recusar" têm o mesmo peso visual e ficam lado a lado. Não há
  caixa marcada nem "continuar navegando é aceitar";
- a loja funciona igual com a recusa. Comprar, entrar e estudar não dependem do
  aceite;
- a escolha pode ser mudada a qualquer momento pelo botão **"Cookies"** no
  rodapé da loja. Recusar depois de ter aceitado apaga o cookie de campanha na
  hora;
- a escolha vale **só para aquela loja**. Aceitar numa loja não vale em outra.

## 3. O que fica no navegador

Fonte: `lib/tracking/attribution.ts`, `consent.ts`, `cookies.ts`.

Os dois cookies são do próprio domínio da loja, só trafegam em HTTPS
(`Secure`) e só vão junto na navegação dentro do site (`SameSite=Lax`). Não são
de terceiros e ninguém além da plataforma os lê.

| Cookie | O que guarda | Quando é gravado | Duração |
|---|---|---|---|
| `sell_choice_<loja>` | A escolha e a versão do texto, por exemplo `v1.accepted` ou `v1.refused`. | Quando a pessoa clica em "Aceitar" ou "Recusar". | 183 dias (cerca de 6 meses). Depois disso, o banner pergunta de novo. |
| `sell_utm_<loja>` | Os campos da campanha que vieram no link (`utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, cada um com até 100 caracteres), o código do link da loja, quando houver, e a data e hora da visita. | Só depois do "Aceitar", e só quando a pessoa chega por um link com campanha. Uma visita nova com campanha substitui a anterior. | 7 dias desde a visita. |

`<loja>` é o código interno da loja, não um dado da pessoa.

O cookie de campanha **não guarda** nome, e-mail, CPF, IP, identificador de
visitante nem impressão digital do navegador. Não há pixel, script de terceiro
nem compartilhamento com rede de anúncio.

O cookie `sell_choice_` guarda a escolha da pessoa, inclusive a recusa. O
objetivo é não perguntar de novo a cada página. Ver a pergunta 7.2.

## 4. O que fica no servidor, e por quanto tempo

Fonte: migration `apps/api/migrations/versions/0075_links_e_atribuicao_utm.py`
e `apps/api/src/appsell_api/attribution_retention_worker.py`.

### 4.1 Atribuição da compra (`checkout_attribution`)

Quando a pessoa **aceitou** e **começa um checkout** com o cookie de campanha
válido, o servidor grava uma linha com:

- a loja;
- um **hash SHA-256 da referência da compra** (não a referência em si);
- os campos da campanha e o código do link, copiados do cookie;
- a data da visita;
- a **versão do texto do banner que a pessoa aceitou**, como prova do
  consentimento;
- a data da gravação.

Não grava e-mail, nome, identidade da pessoa, IP nem identificador de
visitante.

**É dado pseudônimo, não anônimo.** O hash não tem sal, e é a mesma chave que o
registro de pagamento usa para a compra. Quem tem acesso ao banco consegue
ligar a campanha à compra e, pela compra, à pessoa que comprou.

A loja vê, no painel, **só o total de vendas por campanha**. Ela não vê a linha
de cada compra ligada à campanha.

**Guarda:**

- sem venda aprovada em **48 horas**, a linha é apagada;
- com venda aprovada, a linha fica **13 meses** e depois é apagada;
- a limpeza roda **a cada hora**, de forma automática.

### 4.2 Contagem de aceites e recusas (`cookie_consent_tally`)

Para cada loja, dia e versão do texto, o servidor soma quantas pessoas
aceitaram e quantas recusaram. É só um número. Não há linha por pessoa, IP,
identificador nem horário exato. Serve de prova agregada de que o banner é
mostrado e da taxa de aceite.

**Guarda:** hoje não há prazo de apagamento, porque não há dado pessoal. Ver a
pergunta 7.5.

### 4.3 Links de campanha (`campaign_link`)

São os links que o próprio vendedor monta no painel: nome, página de destino e
campos da campanha. É dado da loja, não da pessoa que visita. Ficam enquanto a
loja quiser, e o vendedor pode arquivar.

### 4.4 Proteção técnica

As três tabelas têm isolamento por loja (RLS forçada). Só funções específicas,
com verificação da loja e do papel (dono ou administrador), leem ou gravam
nelas. Uma loja nunca lê a campanha de outra.

## 5. Base legal e papéis (proposta para validar)

**Cookie de campanha e atribuição da compra.** Proposta: **consentimento** (LGPD,
art. 7º, I). Motivos:

- o cookie não é necessário para a loja funcionar;
- a pessoa escolhe, a recusa é o padrão e a escolha pode ser revogada a
  qualquer momento (art. 8º, § 5º);
- a versão do texto aceito fica gravada junto, como prova (art. 8º, § 2º).

**Cookie da escolha (`sell_choice_`).** Proposta: necessário para cumprir a
própria escolha da pessoa e não perguntar de novo a cada página. É tratado como
estritamente necessário e não pede consentimento.

**Contagem agregada.** Não tem dado pessoal, então não precisa de base legal da
LGPD.

**Papéis.** A Política de Privacidade hoje diz que, nos dados dos alunos de uma
loja, **o vendedor é o controlador e a Autonom.ia é operadora**. A proposta é
seguir o mesmo modelo:

- o **vendedor** decide usar campanhas, ligar o banner e ler o resultado, e é o
  controlador da atribuição das compras da loja dele;
- a **Autonom.ia** opera a ferramenta: grava, protege, aplica os prazos e apaga.

Isso conversa com o #439, em que a loja é a vendedora de registro.

## 6. Mudanças nos textos legais (rascunho para aprovar)

Hoje a Política de Cookies (`content/legal/POLITICA_DE_COOKIES.md`, seções 3 e
4) e a Política de Privacidade (`content/legal/POLITICA_DE_PRIVACIDADE.md`,
seção 10) dizem que **não há cookie opcional nem banner**. O banner aponta para
a Política de Cookies. As duas precisam mudar **antes** de ligar a primeira loja.

Proposta para a seção 3 da Política de Cookies, "Cookies opcionais":

> Algumas lojas usam um cookie opcional para saber de qual link ou campanha você
> chegou. Ele só é gravado se você clicar em "Aceitar" no aviso de cookies da
> loja.
>
> | Cookie | Para que serve | Duração |
> |---|---|---|
> | `sell_utm_<loja>` | Guarda os campos da campanha do link que trouxe você (origem, meio, campanha, conteúdo e termo), o código do link e a data da visita. Não guarda nome, e-mail nem IP. | 7 dias |
>
> Se você comprar nos 7 dias, a loja fica sabendo qual campanha trouxe a venda. A
> loja vê o total por campanha, não quem comprou por qual campanha. Esse
> registro é apagado em 48 horas se a compra não for aprovada, e em 13 meses se
> for.
>
> Não usamos cookie de publicidade, de rede social nem ferramenta de análise de
> terceiros.

Proposta para a seção 2, acrescentando uma linha à tabela de essenciais:

> | `sell_choice_<loja>` | Lembra se você aceitou ou recusou o cookie de campanha daquela loja, para não perguntar de novo a cada página. Dura cerca de 6 meses. |

Proposta para a seção 4, "Como gerenciar sua escolha":

> Nas lojas que usam o cookie de campanha, um aviso pergunta se você aceita.
> Recusar é tão fácil quanto aceitar, e a loja funciona do mesmo jeito. Você
> pode mudar a escolha quando quiser pelo botão "Cookies" no rodapé da loja.
> Recusar depois de aceitar apaga o cookie de campanha na hora.

Proposta para a seção 10 da Política de Privacidade:

> Usamos cookies essenciais e, nas lojas que escolhem usar, um cookie opcional de
> campanha, gravado só com o seu aceite. A lista e os prazos estão na Política
> de Cookies. Não usamos rastreamento publicitário nem ferramentas de análise de
> terceiros.

## 7. Perguntas abertas ao jurídico

1. **Papel do vendedor e da Autonom.ia** na atribuição (seção 5). O modelo
   "vendedor controlador, Autonom.ia operadora" vale aqui? Precisa de cláusula
   nova no contrato com o vendedor?
2. **Guardar a recusa** no cookie `sell_choice_` como estritamente necessário,
   sem pedir consentimento para isso. Está correto?
3. **Prazos.** 7 dias para o cookie, 6 meses para a escolha, 48 horas para a
   atribuição sem venda e 13 meses com venda. Os prazos estão adequados? Os 13
   meses foram escolhidos para comparar campanhas de um ano com o anterior.
   Precisam cobrir também algum prazo de contestação da compra?
4. **Texto livre nos campos da campanha.** Os campos UTM são texto livre de até
   100 caracteres, definidos por quem monta o link. Um vendedor, ou alguém que
   repasse o link, pode pôr um dado pessoal num desses campos, como um e-mail no
   `utm_term`. Hoje o sistema não barra isso. Basta uma regra de uso para o
   vendedor, ou devemos bloquear no sistema?
5. **Contagem agregada sem prazo.** A contagem diária por loja não tem dado
   pessoal. Pode ficar sem prazo de apagamento, ou preferem um prazo, como 5
   anos?
6. **Pedido de exclusão.** Como a atribuição é pseudônima e não guarda e-mail
   nem identidade, um pedido de exclusão vindo da pessoa chega pela compra. Hoje
   nenhum fluxo de exclusão apaga a atribuição antes do prazo: ela só sai pela
   limpeza automática (48 horas ou 13 meses). Isso basta, ou a exclusão precisa
   ser imediata?
7. **Texto do banner.** "Se você comprar, a loja vê só a campanha que trouxe a
   venda" descreve o que acontece? O painel mostra o total por campanha, sem
   ligar a compra à pessoa.

## 8. Como ligar depois do OK

1. Aplicar no texto do banner e nas duas políticas as correções do jurídico,
   num PR da trilha dona de `content/legal/`. Se o texto do banner mudar, subir
   `VERSAO_DO_TEXTO`.
2. Ligar o piloto por loja (`APPSELL_UTM_PILOT_SELLER_IDS`), com loja e data de
   decisão escolhidas pelo Rodrigo. A Hub2You só entra com a liberação dele.
3. Conferir, na loja do piloto, que:
   - sem escolha, não há cookie de campanha;
   - a recusa não grava;
   - o aceite grava;
   - a recusa depois do aceite apaga.

## 9. Caixa de consentimento de e-mail da loja (#619)

A base de leads (#619) mostra para a loja quem comprou ou recebeu acesso. Ela
só deixa exportar para marketing quem consentiu em receber e-mail daquela
loja. Hoje nenhuma tela pede esse consentimento.

Por decisão da Orquestração Sell, em 08/11/2026, uma caixa entra em dois
lugares:
- no checkout, feita pela Frente D;
- em "Minha conta", feita pela Frente E.

As duas usam o mesmo componente e a mesma gravação, e ficam atrás do piloto da
base de leads.

### 9.1 Texto da caixa

O texto já está gravado no banco como `store_news` versão 1, na migration
`0066_consentimento_de_email.py` (`TEXTOS_V1`):

> ☐ Quero receber por e-mail as novidades desta loja, como produtos e módulos
> novos. Posso parar quando quiser, pelo link de descadastro em cada e-mail.

### 9.2 Como a caixa se comporta

- Vem **desmarcada**. Comprar e entrar não dependem dela.
- Marcar grava o consentimento só para **aquela loja**, com:
  - a data;
  - a origem (`checkout` ou `account`);
  - a versão do texto e o sha256 do texto exato que a pessoa viu, como prova
    (art. 8º, § 2º);
  - um registro de auditoria.
- O e-mail gravado é o da conta da pessoa logada, nunca um digitado no
  formulário.
- Cada e-mail opcional leva um link de descadastro em um clique. Ele vale na
  hora e grava uma supressão que vence qualquer consentimento posterior da
  mesma loja, até a pessoa marcar a caixa de novo.
- Excluir a conta apaga o consentimento. A supressão fica, guardada só como
  sha256 do e-mail, para o descadastro continuar valendo.
- O e-mail transacional (compra, acesso) não depende da caixa e continua
  saindo.

### 9.3 Base legal e o que pedimos

Proposta: **consentimento** (LGPD, art. 7º, I) para o e-mail de marketing da
loja. O vendedor é o controlador, e a Autonom.ia é a operadora (seção 5).

Pedimos:
1. aprovar o texto da caixa. Se ele mudar, entra como versão 2, e quem
   consentiu na versão 1 continua com a prova da versão 1;
2. confirmar que a loja, como controladora, pode exportar a lista de quem
   consentiu para mandar e-mail por fora da plataforma;
3. dizer se a loja precisa aceitar um termo de uso dessa lista, por exemplo
   "só para e-mail desta loja, respeitando o descadastro", antes do primeiro
   export.

