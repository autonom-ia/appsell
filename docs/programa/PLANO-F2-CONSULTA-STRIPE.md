# F2 — o worker de estorno consulta a Stripe

Refs #439 #487 · autonom-ia/autonomia-financial#150

O desenho do F2 v2 (o worker de estorno do Financial consulta a Stripe por uma
bridge isolada, em vez de esperar o webhook) foi aprovado pelo Rodrigo em
24/10/2026, opção B. Ele foi implementado no Financial, que é um repositório
privado:

- autonom-ia/autonomia-financial#150 — a Issue do estorno que revoga o acesso;
- autonom-ia/autonomia-financial#153 — o worker, a bridge de leitura e o envio;
- autonom-ia/autonomia-financial#159 — a chave liga/desliga por sistema de
  origem (o Sell inteiro), que substituiu a allowlist de operador como forma de
  ligar o F2.

O plano detalhado, com a configuração de produção, fica no Financial, e não
neste repositório, que é público. Aqui fica só o que o Sell precisa saber:

- o F2 avisa o Sell quando um pedido entregue é estornado por inteiro, com a
  mesma assinatura e o mesmo envio do fulfillment;
- nasce desligado, e a ligação é por sistema de origem, com OK do Rodrigo;
- cobre os pedidos sem conta conectada. Pedido com conta conectada da loja (S1)
  depende do receptor Connect (F1) e de estender a consulta (#440).
