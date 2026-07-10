# TOOLING_POLICY.md

## Objetivo

Definir como ferramentas devem ser projetadas para agentes.

## Regras

- Nome claro.
- Descrição curta.
- Schema restritivo.
- Enums quando possível.
- Defaults explícitos.
- Exemplos de uso.
- Risco documentado.
- Permissões documentadas.
- Validação de retorno.
- Logs seguros.

## Exemplo ruim

```text
forecast_growth(channel: string)
```

## Exemplo melhor

```text
forecast_growth(channel: "youtube" | "substack" = "youtube")
```

## Regra MCP

Se a ferramenta precisa funcionar em várias plataformas, considerar MCP.
