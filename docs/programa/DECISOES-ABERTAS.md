# Decisões abertas

O que depende do Rodrigo. Quando uma for tomada, ela entra em
`docs/DECISIONS.md` com data e motivo, e aqui fica só a linha com o número.

| # | Decisão | Bloqueia | Opções | Recomendação |
|---|---|---|---|---|
| A1 | **Quem é o vendedor de registro** (merchant of record): a Autonom.ia ou o produtor? E por onde passa a venda? (#439) | Onda 0, trilha 2 | (a1) produtor, Stripe Connect direto no Sell, como o #140 decidiu; (a2) produtor, Stripe Connect via Financial, que é o caminho de produção hoje; (b) Autonom.ia, recebendo tudo e repassando | (a2). O Financial já repassa à conta conectada e aplica a comissão quando recebe os dados (`src/gateway-client.ts`, montagem da Checkout Session); falta o Sell enviar. Exige registrar que o #140 foi superado. Produtor como vendedor também simplifica a nota fiscal. |
| A2 | **Planos**: manter mensalidade dos planos growth (R$99) e scale (R$399), ou cobrar só % e tokens? | #142 | manter / só % e tokens | Decidir antes de cobrar a comissão de verdade. |
| A3 | **Várias marcas**: um dono com várias lojas, ou várias marcas dentro de uma loja? Inclui rever a Decisão 020 (área logada com marca Autonom.ia). | Onda 1, trilha 3 | N lojas por dono / entidade "marca" dentro da loja | Entidade "marca" dentro da loja: mantém um vínculo financeiro e isola a área do aluno. |
| A4 | **Vídeo**: provedor (Mux, Bunny, Cloudflare Stream) ou construir streaming próprio? | trilha 6 | provedor (G) / próprio (GG) | Provedor. |
| A5 | **Saldo e tokens**: pagar tokens com o saldo de vendas | Onda 3 | — | Pedir parecer jurídico e fiscal antes. |
| A6 | **Regra "integrações só com anúncios"**: trocar por "o vendedor não precisa assinar outra ferramenta"? | — | — | Trocar. Pagamento, WhatsApp e nota fiscal sempre serão externos. |

## Decididas

| Data | Decisão |
|---|---|
| 23/10/2026 | Decisão 022 em `docs/DECISIONS.md`: programa com trilhas e merge de PR seguro delegado à Orquestração Sell. |
