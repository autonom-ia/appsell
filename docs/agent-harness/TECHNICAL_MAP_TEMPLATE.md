# Mapa técnico ponta a ponta

> Copiar para `docs/audit/TECHNICAL_MAP.md`. O agente principal preenche antes da implementação e atualiza quando o head SHA ou o plano mudar materialmente.

## Identidade

- Issue:
- branch:
- base SHA:
- head SHA analisado:
- owner de orquestração: `harness` | `superpowers`
- perfil: `lean` | `standard` | `critical` | `adversarial`

## Objetivo e invariantes

- Resultado observável:
- Invariantes que não podem regredir:
- Fora de escopo:

## Entradas e contratos

| Entrada | Caller | Contrato | Validação | Arquivo/símbolo |
|---|---|---|---|---|

## Fluxo ponta a ponta

| Ordem | Componente | Chamada/transformação | Saída | Próximo consumidor |
|---:|---|---|---|---|

## Writes e efeitos colaterais

| Write/efeito | Sistema | Idempotência | Consistência/rollback | Evidência |
|---|---|---|---|---|

## Auth, autorização e tenant

| Gate | Identidade/capability | Predicado tenant | Falha fechada | Teste |
|---|---|---|---|---|

## Integrações e dependências

| Dependência | Timeout | Retry | Rate limit | Degradação |
|---|---:|---:|---|---|

## Failure modes

| Failure mode | Detecção | Estado após falha | Recuperação | Teste focado |
|---|---|---|---|---|

## Observabilidade

| Sinal | Onde é emitido | Conteúdo permitido | Alerta/uso |
|---|---|---|---|

## Plano de testes

- Testes focados por onda:
- Check de lint/typecheck/build:
- Suíte completa final:
- Smoke necessário:

## Lacunas e decisões

- Hipóteses ainda não comprovadas:
- Decisões aprovadas:
- Motivo para qualquer caminho não inspecionado:
