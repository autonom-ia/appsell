# Base de leads da loja: desenho

Refs #619. Trilha 4 · Crescimento. Desenho aprovado pela Orquestração em
08/11/2026, com as decisões da seção 7, e implementado no PR #621. Envolve dado
pessoal, então passa pelo `sell-security-reviewer`, e o merge é do Rodrigo.

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

Há um botão de CSV, **"Exportar para marketing"**. Ele leva só quem pode
receber e-mail opcional da loja agora: consentimento `granted` e nenhuma
supressão (seção 3). O CSV tem **uma linha por pessoa**, com nome, e-mail,
os produtos juntos numa coluna e a primeira entrada no período. O mesmo e-mail
nunca sai duas vezes, e quem importa numa ferramenta de e-mail não manda em
dobro. Não há "Exportar todos" na v1 (decisão 7.1). A tela mostra
as primeiras 500 linhas, mais os totais. Quando há mais, ela pede para usar o
filtro.

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

- **0078** (reservada pela Orquestração). Ela cria só duas funções e entra no
  `schema_ready`. O `downgrade` apaga as duas, e não há dado a preservar;
- `app_private.read_store_leads(p_actor, p_seller, p_from, p_to, p_product)`
  devolve as primeiras 500 linhas, o total de linhas, o total de pessoas e
  quantas pessoas podem receber marketing;
- `app_private.export_store_leads(p_actor, p_seller, p_from, p_to, p_product,
  p_request)` devolve só o público de marketing, até 10.000 linhas;
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

- `app/(protected)/dashboard/leads/page.tsx` e
  `app/components/dashboard-leads.tsx`: a tela, com o `ChoiceSelect` de
  produto, sem `<select>` nativo. As opções de produto vêm dos leads do
  período;
- `app/(protected)/dashboard/leads/dados/route.ts`: a página de dados, com
  `no-store`;
- `app/(protected)/dashboard/leads/csv/route.ts`: o CSV;
- `lib/reports/leads-csv.ts`:
  - UTF-8 com BOM;
  - neutraliza fórmula: célula que começa com `=`, `+`, `-`, `@`, tab ou CR
    ganha `'` na frente. Nome é texto livre do Identity;
- `lib/reports/leads-pilot.ts`: piloto pela env **`APPSELL_LEADS_PILOT_SELLER_IDS`**,
  vazia por padrão. Fora da lista, a tela e as rotas respondem 404. A tela não
  tem item de menu, como a de vendas;
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
- cada situação de e-mail sai certa: global, loja, `revoked`, `granted` e
  nenhuma;
- o export de marketing bate com `email_optional_block_reason`;
- o 6º export no mesmo minuto é recusado;
- dois exports simultâneos respeitam a trava;
- `audit_event` gravado sem e-mail;
- período maior que 366 dias é recusado.

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

## 7. Decisões da Orquestração (08/11/2026)

1. **Sem "Exportar todos" na v1.** Saem a lista completa na tela e "Exportar
   para marketing".
2. **A captura do consentimento entra em dois lugares**, com uma caixa
   desmarcada por padrão:
   - no checkout, feita pela Frente D;
   - em "Minha conta", feita pela Frente E.

   A Frente F entrega o ponto único que as duas usam (seção 9). O texto da
   caixa foi para o pacote jurídico (#618, seção 9). Tudo fica atrás do piloto
   da base de leads.
3. **A 0078 está reservada para o #619.** O `leads_routes.py`, o
   `email_consent_routes.py`, `lib/email-consent/` e a 0078 entram no
   `TRILHAS.md` no próximo PR que tocar nele.

## 8. O que fica de fora

- valor gasto por lead: depende do #154;
- segmentação, tags ou importação de lista de fora;
- disparo de e-mail pela tela de leads;
- WhatsApp: telefone não existe no schema hoje.

## 9. Ponto único do consentimento (para as Frentes D e E)

A caixa e a gravação ficam num lugar só, para o checkout e a conta se
comportarem igual:

| Peça | Arquivo | Para quê |
|---|---|---|
| Texto | `lib/email-consent/texto.ts` | O texto `store_news` v1, igual ao do banco. Um teste da API compara as duas cópias. |
| Props do servidor | `lib/email-consent/servidor.ts` | A página chama `propsDaCaixaDeConsentimento(sessionId, sellerId)`. Fora do piloto, devolve `null`, e a página não mostra a caixa. Dentro do piloto, devolve `{ sellerId, csrf }`. |
| Caixa | `lib/email-consent/caixa-de-consentimento.tsx` | `<CaixaDeConsentimentoDeEmail marcada onChange/>`. Começa desmarcada. |
| Gravação | o mesmo arquivo | `gravarConsentimentoDeEmail({ sellerId, csrf, origem })`, com `origem` igual a `checkout` ou `account`. Nunca lança e devolve se gravou. Uma falha não trava a compra. |
| Revogação | o mesmo arquivo | `revogarConsentimentoDeEmail({ sellerId, csrf })`, para "Minha conta". Nunca lança e devolve se revogou. |
| Rota do web | `app/api/email/consent/route.ts` | Exige sessão, origem configurada e o token anti-CSRF da ação `POST:/api/email/consent`. O campo `decision` é `grant` (com `source`) ou `revoke` (sem `source`). Limite de 10 por minuto por pessoa, não por sessão. Recusa loja fora do piloto. |
| Rota da API | `apps/api/src/appsell_api/email_consent_routes.py` | `POST .../email-consent` chama `record_email_consent` com `store_news` v1; `POST .../email-consent/revoke` chama `revoke_email_consent`. O e-mail sai da identidade da sessão, nunca do pedido. |

No checkout, a Frente D chama `gravarConsentimentoDeEmail` só se a caixa estiver
marcada, antes de mandar a pessoa ao pagamento, e segue mesmo se a gravação
falhar. Caixa desmarcada no checkout não revoga nada.

Na conta, a Frente E mostra a caixa com o estado atual e, ao salvar, só chama
algo se o estado mudou: `gravarConsentimentoDeEmail` quando a pessoa marcou,
`revogarConsentimentoDeEmail` quando desmarcou. Gravar de novo a cada "salvar"
não pode acontecer, porque reconsentir é ação nova da pessoa: a 0066 tira o
descadastro da loja nessa hora. Por isso só um clique dela na caixa conta como
reconsentimento.

