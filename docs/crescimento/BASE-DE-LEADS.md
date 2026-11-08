# Base de leads da loja: desenho

Refs #619. Trilha 4 · Crescimento. **Por enquanto é só desenho.** O código vem
depois do OK da Orquestração. Envolve dado pessoal, então passa pelo
`sell-security-reviewer`, e o merge é do Rodrigo.

O desenho foi lido no schema da `main`, não estimado. As tabelas citadas são da
cadeia de migrations em `apps/api/migrations/versions/`.

## 1. O que a loja ganha

No painel, em **Leads**, o dono ou o admin da loja vê quem comprou ou recebeu
acesso aos produtos dela:

| Coluna | De onde vem |
|---|---|
| Nome | `identity_user.display_name` (0048). Pode ser vazio. |
| E-mail | `identity_user.email_normalized` |
| Produto | `catalog_product.title` |
| Como entrou | `buyer_entitlement.source`: `billing` vira "Compra", `free_access` vira "Acesso gratuito" e `manual` vira "Manual". `demo_seed` e `demo_checkout` não aparecem. |
| Desde | `buyer_entitlement.created_at`, no dia de São Paulo |
| Acesso | `buyer_entitlement.status`: ativo, revogado ou expirado |
| E-mail de marketing | Situação do consentimento da loja (seção 3) |

Os filtros são por produto e por período (data de entrada, até 366 dias, as
duas pontas inclusivas), como no relatório de vendas. Uma pessoa com dois
produtos aparece em duas linhas. O total de pessoas distintas vai no topo.

Há dois botões de CSV:

- **"Exportar para marketing"**: só quem pode receber e-mail opcional da loja.
  Isso quer dizer consentimento `granted` e sem nenhuma supressão (seção 3);
- **"Exportar todos"**: a lista inteira, para suporte e entrega. O texto ao lado
  do botão diz que essa lista não serve para marketing. Ver a decisão 7.1.

## 2. Sem tabela nova, mas com uma migration de funções

A leitura junta tabelas que já existem:
- `buyer_entitlement`;
- `identity_user`;
- `catalog_product`;
- `app_private.email_consent`;
- `app_private.email_suppression`;
- `app_private.email_suppression_global`;
- `audit_event`.

**Não há tabela nova.** Mesmo assim é preciso uma migration, porque a
leitura passa por funções `security definer`, como todo acesso a dado da loja.
Ela segue o padrão da 0072:

- **0078** (pedida à Orquestração, que ainda precisa confirmar a reserva).
  Ela cria só duas funções e entra no `schema_ready`. O `downgrade` apaga as
  duas e não há dado a preservar;
- `app_private.read_store_leads(p_actor, p_seller, p_from, p_to, p_product,
  p_after, p_limit)` devolve uma página de até 200 linhas, com cursor, e o
  total de pessoas;
- `app_private.export_store_leads(p_actor, p_seller, p_from, p_to, p_product,
  p_audience, p_request)` recebe `p_audience` igual a `marketing` ou `all` e
  devolve até 10.000 linhas;
- capacidades novas: `growth.leads.read` e `growth.leads.export`;
- guarda igual à da 0072: contexto assinado da mesma pessoa e da mesma loja,
  capacidade certa e papel `seller_owner` ou `seller_admin`. Fora disso, erro
  `42501`;
- o export tem trava por pessoa (`pg_advisory_xact_lock`) e limite de 5 por
  minuto, contado em `audit_event`. Cada export grava um `audit_event`
  `growth.leads_exported` com período, produto, público e número de linhas,
  **sem e-mail**.

## 3. Situação do e-mail de marketing

Ela vem da mesma regra que decide o envio. É a regra de
`app_private.email_optional_block_reason` (0066), aplicada à finalidade
`store_news` da loja:

| Situação | Regra |
|---|---|
| **Suprimido** | O sha do e-mail está em `email_suppression_global`, por reclamação, hard bounce ou bloqueio manual. |
| **Descadastrou** | O sha do e-mail está em `email_suppression` da loja, ou o consentimento está `revoked`. |
| **Consentiu** | Consentimento `granted` para `store_news` da loja, sem supressão. |
| **Sem consentimento** | Nenhum dos casos acima. |

O export "para marketing" leva só **Consentiu**. A função usa a própria
`email_optional_block_reason`, em vez de repetir a regra. Assim a lista e o
envio nunca discordam. O teste prova isso: para cada linha do export de
marketing, `email_optional_block_reason(...)` retorna `null`.

**Achado:** hoje nada grava consentimento. Nenhum código do web ou da API chama
`record_email_consent`. A 0066 criou a regra, mas ainda não há tela de checkout
ou de conta que peça o aceite. Até essa tela existir, todo lead aparece como
"Sem consentimento", e o export de marketing sai vazio. Isso é o certo: sem
consentimento não há marketing. Mas a loja precisa entender o motivo, então a
tela explica isso quando o total for zero. A captura do consentimento é outro
item. Ver a pergunta 7.2.

## 4. Quem não aparece

- **Entradas de demo**: `source` igual a `demo_seed` ou `demo_checkout`.
- **Quem pediu exclusão da conta e ainda não foi atendido**: há um
  `account.deletion.requested` sem um `account.deletion.completed` depois (0051).
  Quem pediu para sair não entra numa lista exportável enquanto espera. Depois
  da exclusão, a identidade some, e a linha some junto.
- **Lead de outra loja**: tudo é filtrado por `seller_organization_id` do
  contexto, e as tabelas já têm RLS.

## 5. Web (BFF)

O padrão é o do relatório de vendas (`app/(protected)/dashboard/vendas/`),
dentro das pastas da trilha:

- `app/(protected)/dashboard/leads/page.tsx`: a tela, com o `ChoiceSelect` de
  produto, sem `<select>` nativo;
- `app/(protected)/dashboard/leads/dados/route.ts`: a página de dados, com
  `no-store`;
- `app/(protected)/dashboard/leads/csv/route.ts`: o CSV;
- `lib/reports/leads-csv.ts`:
  - UTF-8 com BOM;
  - neutraliza fórmula: célula que começa com `=`, `+`, `-`, `@`, tab ou CR
    ganha `'` na frente. Nome é texto livre do Identity;
- `lib/reports/leads-pilot.ts`: piloto pela env **`APPSELL_LEADS_PILOT_SELLER_IDS`**,
  vazia por padrão. Fora da lista, a tela e as rotas respondem 404, e o item do
  menu não aparece;
- a API ganha `apps/api/src/appsell_api/leads_routes.py` (novo) e uma linha em
  `main.py`. O arquivo novo precisa entrar na linha da trilha em `TRILHAS.md`,
  que é da Orquestração.

O e-mail aparece inteiro para o dono ou o admin, que é o controlador desse dado
(Política de Privacidade, seção 2). Ele **nunca** vai ao log. A resposta é
`no-store`, e o CSV sai com `Content-Disposition: attachment`.

## 6. Testes, antes do código

**Postgres real, pelo MacCluster:**
- loja A não vê lead da loja B, em leitura e em export;
- editor sem papel de dono ou admin recebe 42501;
- capacidade errada recebe 42501;
- demo não aparece;
- pedido de exclusão aberto esconde a pessoa;
- pedido de exclusão atendido volta a não esconder, porque a identidade some;
- cada situação de e-mail sai certa: global, loja, `revoked`, `granted` e
  nenhuma;
- o export de marketing bate com `email_optional_block_reason`;
- o 6º export no mesmo minuto é recusado;
- dois exports simultâneos respeitam a trava;
- `audit_event` gravado sem e-mail;
- mais de 10.000 linhas é recusado;
- período maior que 366 dias é recusado;
- cursor de página estável.

**Migration:**
- encadeia na cabeça pela cadeia (`cadeia_de_migrations`);
- lê o readiness da anterior;
- concede as funções só ao `appsell_runtime`;
- sobe, desce e sobe de novo.

**API:** rota a rota:
- 404 fora do piloto;
- 422 em período inválido;
- 429 no limite;
- 413 no export grande.

**Web (vitest):**
- CSV com BOM;
- fórmula neutralizada;
- vírgula, aspas e quebra de linha escapadas;
- piloto vazio esconde tudo.

**Hub2You:** o smoke de sempre, antes e depois. A tela nova fica atrás do
piloto, e a vitrine não muda.

## 7. Decisões para a Orquestração e o Rodrigo

1. **"Exportar todos" na v1?** A lista inteira tem e-mail de todo mundo, mesmo
   de quem não consentiu. A loja, como controladora, pode precisar dela para
   suporte e entrega. Mas é a exportação com mais risco de virar disparo sem
   consentimento.
   - **Recomendo:** v1 só com "Exportar para marketing" e a tela com a lista
     completa. "Exportar todos" entra só se uma loja do piloto pedir.
2. **Captura do consentimento.** Sem ela, o export de marketing fica vazio.
   Quem faz a caixa "Quero receber novidades desta loja" no checkout ou na
   conta, e quando? Ela grava pela `record_email_consent`, que já existe. Esse
   ponto toca checkout e conta, que não são da trilha Crescimento.
3. **Reservar a 0078** para esta migration de funções.

## 8. O que fica de fora

- valor gasto por lead: depende do #154;
- segmentação, tags ou importação de lista de fora;
- disparo de e-mail pela tela de leads;
- WhatsApp: telefone não existe no schema hoje.
