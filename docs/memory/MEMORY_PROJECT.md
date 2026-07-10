# MEMORY_PROJECT.md — Contexto do projeto

Preenchido na instalação do harness e atualizado quando arquitetura, integrações ou ambientes mudarem.

## Objetivo do projeto

[Uma a duas frases descrevendo o que este projeto/serviço faz e para quem.]

## Arquitetura resumida

[Stack, principais módulos, fluxo de dados em alto nível. Não duplicar README — apontar para `docs/architecture.md` se existir.]

## Comandos principais

```bash
# Instalar dependências
[comando]

# Rodar localmente
[comando]

# Rodar testes
[comando]

# Build / typecheck / lint
[comando]
```

## Integrações

| Sistema | Tipo | Risco | Doc |
|---------|------|:-----:|-----|
| [ex: Postgres] | DB | Alto | [link] |
| [ex: Stripe] | Billing | Alto | [link] |
| [ex: WhatsApp/WAHA] | Mensageria | Alto | [link] |

## Ambientes

| Ambiente | URL | Branch | Deploy |
|----------|-----|--------|--------|
| Local | localhost | qualquer | manual |
| Dev | [url] | main | auto |
| Staging | [url] | release | manual + aprovação |
| Produção | [url] | tag | manual + aprovação Rodrigo |

## Riscos conhecidos

- [Risco 1 — mitigação]
- [Risco 2 — mitigação]

## O que NÃO fazer

- Sem merge sem aprovação Rodrigo
- Sem deploy em produção sem checklist `release-checker`
- Sem alteração em [tabela sensível] sem migration + backup
- Sem operação destrutiva em Easypanel sem backup confirmado (Regra 001 MEMORY_CORE)
- Sem omitir campo `env` em updateEnv (Regra 002 MEMORY_CORE)
