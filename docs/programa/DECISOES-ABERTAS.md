# Decisões abertas

O que depende do Rodrigo. Quando uma for tomada, ela entra em
`docs/DECISIONS.md` com data e motivo, e aqui fica só a linha com o número.

| # | Decisão | Bloqueia | Opções | Recomendação |
|---|---|---|---|---|
| A1 | ~~Quem é o vendedor de registro~~: **decidida em 24/10/2026**, ver abaixo. | — | — | — |
| A2 | **Planos**: manter mensalidade dos planos growth (R$99) e scale (R$399), ou cobrar só % e tokens? | #142 | manter / só % e tokens | Decidir antes de cobrar a comissão de verdade. |
| A3 | **Várias marcas**: um dono com várias lojas, ou várias marcas dentro de uma loja? Inclui rever a Decisão 020 (área logada com marca Autonom.ia). | Onda 1, trilha 3 | N lojas por dono / entidade "marca" dentro da loja | Entidade "marca" dentro da loja: mantém um vínculo financeiro e isola a área do aluno. |
| A4 | **Vídeo**: provedor (Mux, Bunny, Cloudflare Stream) ou construir streaming próprio? | trilha 6 | provedor (G) / próprio (GG) | Provedor. |
| A5 | **Saldo e tokens**: pagar tokens com o saldo de vendas | Onda 3 | — | Pedir parecer jurídico e fiscal antes. |
| A6 | **Regra "integrações só com anúncios"**: trocar por "o vendedor não precisa assinar outra ferramenta"? | — | — | Trocar. Pagamento, WhatsApp e nota fiscal sempre serão externos. |

## Decididas

| Data | Decisão |
|---|---|
| 24/10/2026 | A1 (#439): **a loja é a vendedora de registro**; a Autonom.ia é a plataforma e fica com a comissão. Stripe para internacional, Asaas no Brasil, sem regressão no Financial. Análise em `docs/programa/VENDEDOR-E-GATEWAYS.md`. |
| 23/10/2026 | Decisão 022 em `docs/DECISIONS.md`: programa com trilhas e merge de PR seguro delegado à Orquestração Sell. |
