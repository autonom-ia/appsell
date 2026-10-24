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
| `document_number` | `text` | forma canônica: CPF com 11 dígitos; CNPJ com 14 posições, sem pontuação e em maiúsculas (ver 2.2) |
| `country` | `text` | ISO 3166-1 alfa-2 em maiúsculas. **Na v1 só `'BR'`** (check). O checkout é só BRL; outro país entra por migration própria |
| `source` | `text` | `'seller_declared'` na v1. `'stripe_kyc'` e `'asaas_kyc'` ficam reservados para quando a leitura do KYC existir |
| `revision` | `bigint` | começa em 1; sobe a cada alteração (concorrência otimista, como as configurações da loja) |
| `created_at`, `updated_at` | `timestamptz` | `clock_timestamp()` |

Restrições:

- `check (document_type in ('cpf','cnpj'))` e `check (country = 'BR')`.
- `check (app_private.is_valid_document(document_type, document_number))`: o
  banco é a autoridade da validação.
- `unique (document_type, document_number)` **não** existe. Um mesmo CNPJ pode
  ter várias lojas (decisão A3, várias marcas).

### 1.2 Acesso

- `enable row level security` e `force row level security`, sem política para
  `appsell_runtime`. **O runtime não lê nem escreve a tabela**; só as funções
  `security definer` abaixo tocam nela, com `search_path` fechado e
  `revoke all from public`. É o mesmo padrão de `external_product_mapping`.
- `app_private.read_seller_legal_identity(actor uuid, seller uuid)`: exige a
  capability `seller.settings.read` e o contexto da loja.
  - `seller_owner` vê o documento inteiro.
  - `seller_admin` vê o documento mascarado (ver 3.2).
  - `seller_editor` e `seller_support` recebem `42501`.
- `app_private.update_seller_legal_identity(actor, seller, legal_name,
  document_type, document_number, expected_revision, request_id)`:
  - Só `seller_owner`, conferido no banco com
    `resolve_seller_membership_role(p_seller) = 'seller_owner'`, como no #469.
  - Advisory lock por loja; confere a `expected_revision`; valida; grava.
  - Audita `seller.legal_identity.updated` com `metadata = {document_type,
    changed: ['legal_name'|'document'], revision}`. **Nunca o número.**
  - Replay com o mesmo `request_id` devolve o resultado anterior sem gravar de
    novo.
- `app_private.seller_public_legal_identity(seller uuid)`: devolve só o que o
  comprador vê: `legal_name`, `document_type` e `document_display` (ver 3.2).
  Quem chama é a projeção pública e o fulfillment.

### 1.3 Onde a identidade vira fato da venda

A identidade de hoje não pode reescrever o recibo de ontem. No momento da venda,
a tabela de venda proposta em `VENDEDOR-E-GATEWAYS.md` §1.7 item 4 guarda uma
**cópia para exibição**: `seller_legal_name` e `seller_document_display`, já
mascarado. Não guarda o número inteiro. A loja que troca de CNPJ muda as vendas
seguintes, não as passadas.

### 1.4 Projeção pública

A projeção da vitrine (0052) ganha `legal_identity: {legal_name, document_type,
document_display}` **só quando a loja tem identidade**. Loja sem identidade
devolve o JSON de hoje, byte a byte, então o web antigo não quebra. A ordem de
deploy é a mesma da 0052: web → api → migration.

## 2. Validação de CPF e CNPJ

### 2.1 Por algoritmo, sem regex

A validação é feita **pelo dígito verificador**, com laço sobre os caracteres e
pertinência a um conjunto (`'0123456789'`), sem expressão regular. O mesmo
algoritmo existe em três lugares, com os mesmos casos de teste:

| Onde | Para quê |
|---|---|
| `app_private.is_valid_document(type, number)` (PL/pgSQL) | autoridade: `check` da tabela e da função de gravação |
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
- **CPF e nome de pessoa física** são dado pessoal. A base proposta é a
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
| `seller_owner`, em Configurações | inteiro | inteiro |
| `seller_admin` | inteiro | mascarado |
| `seller_editor`, `seller_support` | não vê | não vê |
| Comprador (checkout, vitrine, recibo) | inteiro | **CNPJ inteiro** (dado público de empresa); **CPF mascarado** |
| Superadmin da plataforma (inspeção) | inteiro | mascarado |
| Log, métrica, trace, mensagem de erro | **nunca** | **nunca** |

Máscara do CPF: `***.456.789-**`, mostrando só os 6 dígitos do meio. É o padrão
de exibição de CPF adotado em publicações do governo federal desde 2019. Quem
calcula a máscara é uma única função no banco (`document_display`); a API e o
web não remontam a máscara.

### 3.3 Logs e e-mail

- **Log**: nenhum campo de log recebe nome legal, documento ou o corpo da
  requisição de gravação. O que se loga é `seller_id`, `document_type`,
  `revision` e o resultado.
  - O redator do log (`logging_setup.py`) **não pega** CPF: 11 dígitos estão
    abaixo do limite dele, e ele mesmo se declara "rede de segurança, não
    garantia".
  - Por isso a garantia é um teste: o fluxo de gravação, com sucesso e com erro
    de validação, roda com captura de log, e o teste exige que nenhum valor
    contenha o documento, nem com nem sem pontuação.
- **Erro**: a resposta de validação diz qual regra falhou (`document_invalid`,
  `document_type_mismatch`), nunca ecoa o valor enviado.
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
- **Backup**: segue a política de `BACKUP_RESTORE_SELL.md`. Uma restauração não
  pode trazer de volta uma identidade apagada sem reaplicar o runbook. Isso fica
  registrado no runbook.

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
- País diferente de BR e documento estrangeiro.
- Nota fiscal.
