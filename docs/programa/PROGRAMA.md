# Programa Sell

Aprovado pelo Rodrigo em 23/10/2026 (Issue #435). Fonte da análise: comparativo
"Mapa da visão Sell" — as 20 ideias do documento *AUTONOMIA SELL* contra o que
está em produção.

**Objetivo:** o produtor entra no Sell e não precisa assinar outra ferramenta.
A plataforma cobra por % da venda e por tokens.

## Como trabalhamos

- **Orquestração Sell** (uma sessão): painel, Issues, ordem do trabalho, reserva
  de migration, lotes, merge de PR seguro, relação com a Orquestração Deploy e
  com o Rodrigo. Não escreve código de trilha.
- **Qualidade e Segurança** (um chat): revisor, testador e revisor de segurança
  novos para cada PR (`.claude/agents/sell-*.md`), auditoria semanal do que
  entrou na `main`, guarda dos gates do `AGENTS.md`.
- **Trilhas de execução** (até 6 chats): cada uma dona das pastas listadas em
  [`TRILHAS.md`](TRILHAS.md).
- **Merge e deploy** pelo trem da Orquestração Deploy
  (`chat2you/docs/processo-de-release.md`). O Sell entra no trem depois do
  inventário de deploy preenchido (Onda 0).

Pico: 8 chats. Rampa por condição, não por data:

| Momento | Chats |
|---|---|
| Início | Orquestração, Qualidade, 1 Plataforma, 2 Dinheiro, 3 Membros |
| CI obrigatório verde há 1 semana | + 4 Crescimento e Dados |
| Checkout separado em `lib/payments/` pela trilha 2 | + 5 Checkout e Funil |
| Decisão do provedor de vídeo | + 6 Mídia e Comunidade |

Recalibrar o tamanho da equipe no fim da Onda 0, com a vazão medida (PRs por
semana revisados e no ar).

## Ondas

Esforço em semanas de trabalho de 1 dev (P = 1, M = 2, G = 6, GG = 10).

### Onda 0 — fundação (~13 semanas de trabalho)

Épico: Issue "Onda 0". Sem ela fechada, nada de dinheiro novo.

- Segurança: subir para produção as correções de RCE já na `main` (`7543df9`).
- Plataforma: CI obrigatório e verde (#422, #333, #266, #159), logs da API (#312),
  inventário de deploy para o trem.
- Dinheiro: decisão de quem é o vendedor de registro; Sell enviar conta
  conectada e comissão ao Financial; cadastro de vendedor self-service (#382);
  estorno revoga acesso (#151); conciliação (#147); conferir valor (#148); Pix.
- Membros: e-mail de compra validado em produção (#349, #357).
- Orquestração: triagem das PRs de julho, fechar Issues já resolvidas, Project
  em dia.

### Onda 1 — Hotmart completa (~22 semanas)

Área de membros por marca e cursos bloqueados; link de acesso e WhatsApp
pós-compra; base de leads, relatório e CSV; faturamento e placas; parcelamento e
boleto; assinatura recorrente; quadro de ideias.

### Onda 2 — conversão (~24 semanas)

Tracking (Meta primeiro, com consentimento); recuperação de carrinho pelo
Chat2You; IA de métricas; funil e páginas de obrigado; VSL via provedor.

### Onda 3 — escala (~78 semanas)

Afiliados e coprodução; moedas e métodos por país; idioma e domínio próprio;
construtor de páginas; comunidade; carteira única de tokens; antecipação
(depende de parecer regulatório).

Calendário estimado com a equipe cheia: 8 a 10 meses, limitado pela trilha 2
(dinheiro). Estimativa, não medição.

## Painel

O estado vivo fica na Issue fixada **"Painel do programa Sell"** e no Project
Autonom.ia Dev. Este arquivo muda só quando muda o plano.
