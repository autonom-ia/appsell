# MODEL_ROUTING_POLICY.md

## Objetivo

Definir quando usar modelos fortes, médios ou rápidos.

## Regra

Nem toda tarefa precisa do modelo mais caro.

O custo deve cair por reduzir chamadas, contexto duplicado e rodadas. `critical` e `adversarial` não aceitam downgrade automático por budget, timeout ou indisponibilidade; falham fechados com `BLOCKED_MODEL_TIER`.

## Roteamento sugerido

| Tarefa | Classe de modelo |
|---|---|
| resumir docs | rápido/barato |
| listar arquivos | rápido/barato |
| investigar bug simples | médio |
| corrigir bug com testes | médio/forte |
| arquitetura | forte |
| auth/billing/produção | forte + humano |
| review de segurança | forte |
| deploy | humano + checklist |

## Convergência

- Inventário e resumo podem usar classe rápida.
- Implementação segue risco e complexidade.
- Review crítico/adversarial usa classe forte.
- Re-review recebe apenas delta e findings abertos.
- Uma única revisão ampla final usa a classe adequada ao maior risco da PR.
