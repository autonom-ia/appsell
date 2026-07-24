# Review Convergence

## Objetivo

Encontrar o conjunto completo de problemas antes de corrigir, evitar reviews amplos repetidos e preservar modelos fortes onde o risco exige.

## Pré-condições

Antes de implementar:

1. declarar `orchestration.owner` como `harness` ou `superpowers`;
2. produzir `docs/audit/TECHNICAL_MAP.md`;
3. registrar base SHA, head SHA e hash SHA-256 do diff;
4. definir reviewers necessários pelo risco;
5. iniciar o ledger a partir de `docs/audit/REVIEW_LEDGER_TEMPLATE.json`.

O owner é exclusivo. Se for `harness`, Superpowers pode fornecer uma skill de execução, mas não adiciona implementer/reviewer/final review próprios. Se for `superpowers`, os agentes equivalentes do Harness não são invocados de novo.

## Rodada de descoberta

1. Todos os reviewers recebem o mesmo head SHA e o escopo específico da sua especialidade.
2. Finding de segurança bloqueia merge e aciona o security reviewer, mas o reviewer geral continua todas as categorias não dependentes.
3. Reviewer não corrige código e não solicita nova rodada por conta própria.
4. O agente principal aguarda todos os reviewers previstos: esta é a barreira de junção.
5. Somente depois da barreira de junção, o agente principal consolida os resultados.

## Consolidação

Cada finding recebe:

- `id`;
- `fingerprint` SHA-256 estável;
- severidade `P0` a `P4`;
- categoria;
- status `new`, `open`, `carried`, `resolved`, `reopened` ou `accepted`;
- primeira e última rodada observada.

O fingerprint representa contrato afetado + failure mode + superfície estável. Findings com o mesmo fingerprint são um único item, mesmo quando dois reviewers os reportam.

## Correção e re-review

- O agente principal envia todos os findings abertos em uma única onda consolidada de correção.
- Durante a onda, rodam testes focados nos contratos alterados.
- O re-review cobre somente o delta e findings abertos.
- Findings fora do delta permanecem `carried`; não disparam outra revisão ampla.
- O limite padrão é de duas ondas.
- Depois da segunda onda, finding P1/P2 novo produz `STOP_REPLAN`: interromper correções, atualizar o mapa técnico e revisar o plano.

## Fechamento

Depois de zerar P0/P1/P2 abertos:

1. rodar a suíte completa uma única vez;
2. executar uma única revisão ampla final contra o diff integral;
3. validar o ledger;
4. entregar ao gate humano.

Nova revisão ampla só é permitida se o head SHA mudar depois da revisão final ou se Rodrigo reabrir o escopo.

## Modelos e custo

- `critical` e `adversarial`: sem downgrade automático; reviewer usa classe `strong` ou humano.
- Timeout ou indisponibilidade não autoriza tier inferior.
- Economia vem de reduzir chamadas, unir findings, limitar rodadas e enviar somente o delta no re-review.
- Evals pagos permanecem sujeitos aos gates de budget e opt-in do manifesto.

## Telemetria segura

Registrar somente os campos definidos em `OBSERVABILITY_AND_TRACING.md`. Não registrar prompt, resposta, conteúdo do finding, secret, cookie, credential, authorization, valor de token ou dado de cliente.

Validar:

```bash
bash scripts/harness-review-ledger.sh validate docs/audit/review-ledger.json platform-manifest.json
```

Resumir:

```bash
bash scripts/harness-review-ledger.sh summary docs/audit/review-ledger.json platform-manifest.json
```
