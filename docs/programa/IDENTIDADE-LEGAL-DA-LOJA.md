# Identidade legal da loja: desenho

Refs #439. Trilha 2 · Dinheiro. **É só desenho.** Não há código nem migration
ainda. A Qualidade revisa este documento antes de qualquer implementação.

## Autorização

O Rodrigo autorizou o Sell a guardar a identidade legal da loja: nome ou razão
social, CPF/CNPJ e país. Resposta dele no #487: "2. autorizo" (24/10/2026).

Motivo: a loja é a vendedora de registro (#439,
`VENDEDOR-E-GATEWAYS.md`), e o comprador precisa saber **quem vende** antes de
pagar e no recibo.

## 1. Modelo

### Migration

O nome proposto é `0065_identidade_legal_da_loja`, o próximo número livre depois
da 0064 (#469). **O número é reservado pela Orquestração**, e a `down_revision`
segue a cabeça da cadeia no momento do PR.

A migration é isolada: só acrescenta uma tabela e funções. Nenhuma coluna de
`seller_organization` muda e nenhuma função existente é reescrita, exceto a
projeção pública (ver 1.4), que ganha campos **só quando existem**, como na 0052.

### 1.1 Tabela própria, separada da loja

O dado pessoal fica numa tabela própria, `public.seller_legal_identity`, e não
em `seller_organization`. Assim o grant, a RLS e a retenção ficam num lugar só,
e nada que já lê a loja passa a ver CPF por acidente.

| Coluna | Tipo | Regra |
|---|---|---|
| `seller_organization_id` | `uuid` PK, FK → `seller_organization(id)` | uma identidade por loja |
| `legal_name` | `text` | 2 a 200 caracteres (`length`), sem espaço nas pontas; nome da pessoa (CPF) ou razão social (CNPJ) |
| `document_type` | `text` | `'cpf'` ou `'cnpj'` |
| `cnpj_number` | `text`, nulo para CPF | CNPJ na forma canônica, 14 posições sem pontuação e em maiúsculas (ver 2.2). CNPJ é dado de empresa, público, e fica inteiro |
| `document_display` | `text` | o que aparece para quem vê: CNPJ formatado inteiro, ou o CPF mascarado `***.456.789-**`. **O CPF inteiro não é guardado** (ver 1.5) |
| `country` | `text` | ISO 3166-1 alfa-2 em maiúsculas. **Na v1 só `'BR'`** (check). O checkout é só BRL; outro país entra por migration própria |
| `source` | `text` | `'seller_declared'` na v1. `'stripe_kyc'` e `'asaas_kyc'` ficam reservados para quando a leitura do KYC existir |
| `revision` | `bigint` | começa em 1; sobe a cada alteração (concorrência otimista, como as configurações da loja) |
| `created_at`, `updated_at` | `timestamptz` | `clock_timestamp()` |

Restrições:

- `check (document_type in ('cpf','cnpj'))` e `check (country = 'BR')`.
- `check (document_type = 'cnpj') = (cnpj_number is not null)`.
- `check (cnpj_number is null or app_private.is_valid_document('cnpj', cnpj_number))`.
- A validação do CPF acontece **dentro da função de gravação**, sobre o número
  recebido como parâmetro, antes de ele ser descartado. O banco continua sendo
  a autoridade da validação dos dois tipos.
- Não existe `unique` por documento. Um mesmo CNPJ pode
  ter várias lojas (decisão A3, várias marcas).

### 1.2 Acesso

- `enable row level security` e `force row level security`, com **uma única
  política, a do dono**, como em `0012_checkout_webhook_ingress.py:182-183`:
  `create policy seller_legal_identity_owner_all on seller_legal_identity to
  appsell_owner using (true) with check (true)`. Sem ela, as funções `security
  definer` (que rodam como `appsell_owner`) leriam vazio.
- **Nenhuma política nem grant para `appsell_runtime`**: o runtime não lê nem
  escreve a tabela. Só as funções abaixo tocam nela, com `search_path` fechado e
  `revoke all from public`.
- `app_private.read_seller_legal_identity(actor uuid, seller uuid)`: exige a
  capability `seller.settings.read` e o contexto da loja.
  - **O papel vem do contexto, não do parâmetro**:
    `resolve_seller_membership_role(p_seller)`, que lê
    `context_uuid('identity_user_id')` (`0025:103-117`). Um `actor` diferente da
    identidade do contexto dá `42501` antes de qualquer leitura.
  - `seller_owner` e `seller_admin` veem `legal_name` e `document_display`
    (ver 3.2). Como o CPF inteiro não é guardado, ninguém o lê.
  - `seller_editor` e `seller_support` recebem `42501`.
- `app_private.update_seller_legal_identity(actor, seller, legal_name,
  document_type, document_number, expected_revision, request_id)`:
  - Exige a capability `seller.settings.update` e o contexto da loja.
  - Só `seller_owner`, conferido no banco com
    `resolve_seller_membership_role(p_seller) = 'seller_owner'`, como no #469.
  - Advisory lock por loja; confere a `expected_revision`; valida; grava.
  - Audita `seller.legal_identity.updated` com `metadata = {document_type,
    legal_name_changed, document_changed, revision}`. **Nunca o número, a
    máscara nem um hash dele.** Os dois campos de mudança são booleanos,
    calculados por comparação dentro da função:
    - `legal_name_changed`: o nome novo contra o gravado.
    - `document_changed`, para CNPJ: `cnpj_number` novo contra o gravado.
    - Para CPF, o inteiro não é guardado e não há com que comparar. A máscara
      não serve: dois CPFs diferentes podem ter a mesma. Um hash também não,
      porque guardaria o CPF de outra forma. Por isso o campo registra só
      `document_submitted: true`, ou seja, que o pedido trouxe um CPF. Mudou ou
      não, fica desconhecido por desenho.
  - Replay com o mesmo `request_id` devolve o resultado anterior sem gravar de
    novo. **O registro de replay guarda só `request_id`, `revision` e status**,
    nunca o corpo nem o documento.
- `app_private.seller_public_legal_identity(seller uuid)`: devolve só o que o
  comprador vê: `legal_name`, `document_type` e `document_display` (ver 3.2).
  - **Não tem grant para ninguém.** Só é chamada de dentro de outras funções
    `security definer`: a projeção pública e o registro da venda.
  - Na projeção pública, responde só para a loja que a vitrine já mostra: o
    mesmo critério da 0052, `organization.status = 'active'`, buscada pelo slug
    (`0052_configuracoes_da_loja.py:803,925`). Loja suspensa ou inexistente não
    devolve identidade.
  - No registro da venda, responde só para a loja da própria venda.

### 1.3 Onde a identidade vira fato da venda

A identidade de hoje não pode reescrever o recibo de ontem. No momento da venda,
a tabela de venda proposta em `VENDEDOR-E-GATEWAYS.md` §1.7 item 4 guarda uma
**cópia para exibição**: `seller_legal_name` e `seller_document_display`. A loja que troca de CNPJ muda as vendas
seguintes, não as passadas.

### 1.4 Projeção pública

A projeção da vitrine (0052) ganha `legal_identity: {legal_name, document_type,
document_display}` **só quando a loja tem identidade**. Loja sem identidade
devolve o JSON de hoje, byte a byte, então o web antigo não quebra. A ordem de
deploy é a mesma da 0052: web → api → migration.

### 1.5 Por que o CPF inteiro não é guardado (MÉDIO 4 da Qualidade)

Minimização (LGPD art. 6º, III): na v1, o CPF inteiro não tem leitor. O
comprador vê o CPF mascarado, a auditoria não leva o número, e a loja não
precisa ver o próprio CPF; para trocá-lo, digita de novo. Por isso a função de
gravação recebe o CPF, valida o dígito verificador, calcula `document_display`
e **descarta o número**. Nada em repouso, nada em backup.

Cifrar pelo transit do OpenBao foi a primeira opção considerada, e não serve
agora:

- o cofre que o Sell já tem é o de **tokens de sessão** do web
  (`lib/token-vault.ts:97-101`, contexto de sessão);
- o provedor OpenBao **recusa produção**
  (`lib/openbao-transit-key-envelope-provider.server.ts:38-40`);
- pelo plano do OpenBao, a API não recebe credencial de cofre
  (`docs/plans/OPENBAO_PROD_PLAN.md:29`);
- qual provedor roda em produção não está escrito no repositório.

Cifrar exigiria tirar do banco a validação do CPF e criar um fluxo novo de
cofre no web, sem um leitor que justifique.

**O que reabre a questão** é a pergunta jurídica do §3.5. Se o comprador tiver
de ver o CPF **inteiro**, o número passa a ser público por desenho. Aí ele é
guardado como o CNPJ, e o `BACKUP_RESTORE_SELL.md` passa a dizer que o backup
carrega CPF.

Cuidado com o parâmetro: o CPF passa pela chamada da função. **Antes do deploy
do passo 1 (§5), é bloqueio no runbook** conferir no Postgres de produção, só
leitura (`show …`), que:

- `log_statement` não é `all` (`mod` também loga a chamada; o aceitável é
  `none` ou `ddl`);
- `log_parameter_max_length` é `0`, **ou** `log_min_duration_statement` é `-1`
  (sem isso, uma chamada lenta loga o parâmetro);
- `log_parameter_max_length_on_error` é `0`;
- `auto_explain` não está em `shared_preload_libraries` com
  `auto_explain.log_parameter_max_length` diferente de `0`.

Qualquer item fora disso bloqueia o deploy até a configuração ser corrigida, com
OK do Rodrigo, porque é infra de produção.

## 2. Validação de CPF e CNPJ

### 2.1 Por algoritmo, sem regex

A validação é feita **pelo dígito verificador**, com laço sobre os caracteres e
pertinência a um conjunto (`'0123456789'`), sem expressão regular. O mesmo
algoritmo existe em três lugares, com os mesmos casos de teste:

| Onde | Para quê |
|---|---|
| `app_private.is_valid_document(type, number)` (PL/pgSQL) | autoridade: dentro da função de gravação (CPF e CNPJ) e no `check` do `cnpj_number` |
| `apps/api/src/appsell_api/legal_identity.py` | recusa cedo, com o motivo certo, antes do banco |
| `lib/legal-identity.ts` | erro no campo enquanto a pessoa digita |

Entrada: o formulário aceita pontuação (`.`, `-`, `/`) e espaços. A
normalização **remove esses caracteres um a um** e converte para maiúsculas.
Qualquer outro caractere recusa o valor; não é descartado em silêncio.

### 2.2 Regras

**CPF (11 dígitos):**

1. Todos os caracteres são dígitos.
2. Os 11 dígitos não podem ser todos iguais (`000.000.000-00` a
   `999.999.999-99` passam no cálculo, mas não são válidos).
3. 1º verificador: soma dos 9 primeiros dígitos com pesos 10 a 2; `resto = soma
   mod 11`; o dígito é `0` se `resto < 2`, senão `11 - resto`.
4. 2º verificador: o mesmo, com os 10 primeiros dígitos e pesos 11 a 2.

**CNPJ (14 posições, numérico ou alfanumérico):**

- A Receita Federal passou a emitir CNPJ **alfanumérico** a partir de julho de
  2026 (IN RFB nº 2.229/2024). As 12 primeiras posições aceitam `0-9` e `A-Z`;
  as 2 últimas, os verificadores, são sempre dígitos. O CNPJ só numérico
  continua válido.
- Valor de cada posição: o código ASCII do caractere menos 48 (`'0'` vale 0,
  `'9'` vale 9, `'A'` vale 17, `'Z'` vale 42). Para o CNPJ numérico, isso é o
  próprio dígito.
- 1º verificador: soma das 12 primeiras posições com pesos `5,4,3,2,9,8,7,6,5,4,3,2`;
  o dígito é `0` se `soma mod 11 < 2`, senão `11 - (soma mod 11)`.
- 2º verificador: as 13 primeiras posições com pesos
  `6,5,4,3,2,9,8,7,6,5,4,3,2`, com a mesma regra.
- As 14 posições não podem ser o mesmo caractere repetido.

> A regra do alfanumérico precisa ser conferida contra a publicação oficial da
> Receita antes da implementação. Os casos de teste devem incluir os exemplos
> que a própria Receita publicar.

### 2.3 Testes previstos

- Uma lista fixa de CPF e CNPJ válidos e inválidos, com CNPJ numérico e
  alfanumérico, **usada pelos três** (PL/pgSQL, Python, TS). O teste falha se
  algum dos três divergir em algum caso.
- Para cada regra, um caso que só aquela regra recusa: tamanho, caractere fora
  do conjunto, todos iguais, 1º verificador errado, 2º verificador errado.
- O `check` da tabela recusa a gravação direta como `appsell_owner` com
  documento inválido.

## 3. LGPD

### 3.1 Base legal e finalidade

- **CNPJ e razão social** são dados de pessoa jurídica: não são dado pessoal.
- **CPF e nome de pessoa física** são dado pessoal. **O `document_display` do
  CPF também é**: nome completo mais 6 dígitos do meio reidentificam a pessoa.
  Ele recebe o mesmo tratamento do nome: fica fora de log, aparece só onde a
  tabela do §3.2 manda, e a retenção do §3.4 vale para ele. A base proposta é a
  **execução de contrato** com a loja e o cumprimento de obrigação legal de
  informar o fornecedor ao consumidor (LGPD art. 7º, II e V).
- A finalidade é identificar o vendedor ao comprador, no checkout e no recibo, e
  fechar a conciliação. Não há outro uso: nada de marketing nem de
  enriquecimento.

> Os dois parágrafos acima são proposta técnica. O texto da política de
> privacidade sai com o Rodrigo (é jurídico), como o §8 dos termos.

### 3.2 Quem vê o quê

| Quem | Nome legal | Documento |
|---|---|---|
| `seller_owner` e `seller_admin`, em Configurações | inteiro | CNPJ inteiro; CPF mascarado (o inteiro não é guardado) |
| `seller_editor`, `seller_support` | não vê | não vê |
| Comprador (checkout, vitrine, recibo) | inteiro | **CNPJ inteiro** (dado público de empresa); **CPF mascarado** |
| Superadmin da plataforma (inspeção) | inteiro | mascarado |
| Log, métrica, trace, mensagem de erro | **nunca** | **nunca** |

Máscara do CPF: `***.456.789-**`, mostrando só os 6 dígitos do meio. Quem
calcula a máscara é uma única função no banco (`document_display`); a API e o
web não remontam a máscara.

### 3.3 Logs e e-mail

- **Log**: nenhum campo de log recebe nome legal, documento ou o corpo da
  requisição de gravação, **nem na API nem na rota Next (BFF)** por onde o corpo
  passa antes. O que se loga é `seller_id`, `document_type`,
  `revision` e o resultado.
  - O redator do log (`logging_setup.py`) **não pega** CPF: 11 dígitos estão
    abaixo do limite dele, e ele mesmo se declara "rede de segurança, não
    garantia".
  - Por isso a garantia é um teste, **em dois lugares**: a rota da API e a rota
    Next do BFF. O fluxo de gravação, com sucesso e com erro de validação, roda
    com captura de log, e o teste exige que nenhum valor contenha o documento,
    nem com nem sem pontuação.
- **Erro**: a resposta de validação diz qual regra falhou (`document_invalid`,
  `document_type_mismatch`) e nunca ecoa o valor enviado.
  - O 422 padrão do FastAPI ecoa o valor (`input`) quando o pydantic recusa o
    campo. Por isso o campo do documento é `str` **sem restrição de forma no
    pydantic** (nem tamanho, nem padrão): só o limite de tamanho do corpo. Toda a
    validação fica no código, que responde `document_invalid` sem o valor.
  - O teste de log e de resposta cobre também o caminho do 422: um corpo com o
    documento num campo de tipo errado não pode devolver o documento.
- **E-mail**: a fila (`email_outbox`, 0049) recebe só `seller_legal_name` e
  `seller_document_display`, já mascarado. **O CPF inteiro nunca entra na fila**,
  porque a fila é persistida e reenviada.

### 3.4 Retenção e exclusão

- Enquanto a loja existir, a identidade fica.
- A loja pode trocar a identidade a qualquer momento. A anterior não é guardada
  na tabela; o rastro é o `audit_event`, que não tem o número.
- **Exclusão da loja ou da conta do dono**: a identidade é apagada pelo runbook
  de exclusão (`docs/runbooks/PEDIDO_DE_EXCLUSAO_DE_CONTA.md`, com um passo
  novo).
  - As vendas passadas mantêm a cópia de exibição (nome e documento mascarado),
    porque o recibo é documento da compra do comprador.
  - Prazo proposto para a cópia nas vendas: **5 anos após a venda**, alinhado ao
    prazo de guarda de documentos fiscais. **Validar com o jurídico.**
  - **Quem apaga e como:** um job de retenção da API, diário e idempotente,
    troca `seller_legal_name` e `seller_document_display` das vendas com mais de
    5 anos por `null` e audita a contagem. Ele não apaga a venda. A primeira
    venda com cópia só vence 5 anos depois do passo 4 do §5, então o job tem
    Issue própria e entra antes dessa data, com teste de fronteira (dia 1825 ×
    1826).
- **Backup**: segue a política de `BACKUP_RESTORE_SELL.md`. Uma restauração não
  pode trazer de volta uma identidade apagada sem reaplicar o runbook. Isso fica
  registrado no runbook.

### 3.5 Pergunta aberta, para o Rodrigo e o jurídico (não decidida)

O Decreto 7.962/2013, art. 2º, I, pede que o site de comércio eletrônico
informe o "número de inscrição do fornecedor" no CPF ou no CNPJ.

**Mostrar o CPF mascarado ao comprador atende a isso, ou o CPF tem de aparecer
inteiro?**

A resposta muda o §1.5: mascarado mantém o CPF fora do banco; inteiro o torna
público e ele passa a ser guardado. O mesmo parecer deve confirmar a base legal
do §3.1 e o prazo de 5 anos do §3.4. Até a resposta, vale o mascarado.

## 4. Onde aparece para o comprador

| Lugar | O que mostra | Depende de |
|---|---|---|
| Card de compra e checkout do cadastro | "Vendido por {nome legal} · CNPJ {número}" ou "· CPF {mascarado}" | projeção pública (1.4) |
| Rodapé da vitrine | "Vendido por {nome legal}. Plataforma: Autonom.ia Sell." e links para os termos e para a política de reembolso da loja | projeção pública |
| E-mail `compra_confirmada` (o recibo) | valor, data, número do pedido, meio de pagamento e "Vendido por {nome legal} · {documento para exibição}". O rodapé passa a dizer "A Autonom.ia é a plataforma, não a vendedora deste produto." | tabela de venda (1.3) e fila de e-mail |
| Página de recibo (`/library?checkout=success` hoje ignora o parâmetro) | o mesmo do e-mail | tabela de venda |

**Loja sem identidade não vende pelo autosserviço.** A ativação do #469 ganha
mais uma pré-condição atrás de flag: sem identidade legal, `blocked` com o
motivo `legal_identity_missing`. A Hub2You fica fora: a flag é por loja, e a
identidade dela é cadastrada antes de a flag ser ligada para ela.

## 5. Ordem de entrega

Cada passo é uma Issue e um PR, e nenhum vai a produção sem OK do Rodrigo:

1. Migration 0065 com tabela, funções, validação e testes: RED, A×B (loja A não
   lê nem grava a identidade de B), replay e máscara.
2. API e tela de Configurações → Identidade legal (só `seller_owner` grava; sem
   `<select>` nativo para o tipo de documento: usa o componente de escolha do
   site).
3. Projeção pública e "Vendido por" no checkout e na vitrine.
4. Tabela de venda com a cópia de exibição, e o recibo no e-mail. Este passo
   depende da #440.
5. Pré-condição na ativação (#469) e o passo novo no runbook de exclusão.

## Fora deste desenho

- Leitura automática do KYC do Stripe ou do Asaas (`source` já reservado).
- **Caso futuro: subconta Asaas.** Criar a subconta da loja no Asaas pede o
  `cpfCnpj` inteiro. Como o Sell não guarda o CPF, o fluxo de criação **pede o
  documento de novo** à loja, valida, repassa ao Asaas na mesma requisição e
  descarta, sem gravar no Sell nem em log. Vale o mesmo cuidado do 422 e do log
  desta seção. O CNPJ pode ser lido da tabela.
- País diferente de BR e documento estrangeiro.
- Nota fiscal.
