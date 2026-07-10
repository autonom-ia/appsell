# MODEL_ROUTING_POLICY.md

## Objetivo

Definir quando usar modelos fortes, médios ou rápidos.

## Regra

Nem toda tarefa precisa do modelo mais caro.

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
