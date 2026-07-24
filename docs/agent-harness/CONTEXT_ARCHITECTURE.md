# Context Architecture

## Mapa técnico

O agente principal mantém `docs/audit/TECHNICAL_MAP.md` a partir de `TECHNICAL_MAP_TEMPLATE.md`. O mapa técnico é o contrato de compreensão ponta a ponta, não um resumo de arquivos.

Ele contém:

- entradas e contratos;
- sequência de chamadas e consumidores;
- writes e efeitos colaterais;
- auth, autorização e tenant;
- integrações, timeouts e retries;
- failure modes;
- observabilidade;
- testes focados e completos.

## Pacotes de contexto

- Implementer recebe spec, mapa técnico e escopo.
- Reviewers recebem o mesmo head SHA, o diff e sua categoria.
- O re-review recebe somente o delta, o ledger e findings abertos.
- A revisão ampla final recebe o diff integral uma única vez.

Não reenviar histórico narrativo acumulado quando spec, mapa, ledger e diff já são fontes canônicas.
