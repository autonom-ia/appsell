# Evals

Promptfoo é um gate condicional: só roda quando o diff contém arquivos de prompt/agente e o repositório possui `promptfooconfig.yaml`, `.yml` ou `.json`. Mudança de aplicação sem prompt não consome tempo de eval.

## Default sem custo

O manifesto nasce com:

```json
{
  "ci": {
    "prompt_eval": {
      "on_change": true,
      "live_providers": false,
      "budget_usd": 0
    }
  }
}
```

O conteúdo do config não é usado para classificar providers: referências `https`, `file://`, JavaScript, Python, variáveis, includes ou providers desconhecidos podem carregar código, ambiente ou rede. Por isso, nenhum config do projeto é carregado ou executado sem todos os gates abaixo:

1. arquivo de prompt/agente alterado no diff e config presente;
2. `ci.prompt_eval.live_providers=true` no manifesto revisado;
3. `ci.prompt_eval.budget_usd` maior que `0` no manifesto;
4. `HARNESS_LIVE_EVAL=true` no ambiente autorizado.

Budget `0`, flag ausente ou live providers desligados resultam em `SKIPPED: live_eval_not_authorized`; o Promptfoo não é chamado. `HARNESS_EVAL_BUDGET_USD` não substitui o valor revisado no manifesto. O budget é apenas autorização; não é um hard cap nem um medidor de cobrança do provider.

Sem autorização, só são permitidas verificações de presença do arquivo, versão pinada da CLI e estrutura que comprovadamente não carreguem nem executem o config. Em caso de dúvida, o eval é pulado.

## Contrato do config

- usar casos determinísticos e assertions locais sempre que possível;
- não imprimir prompts, respostas, tokens ou dados de cliente;
- não gravar output bruto em artifact;
- anonimizar fixtures e manter secrets fora do config;
- qualquer execução do config exige todos os gates explícitos acima.

Comando reproduzível:

```bash
bash scripts/harness-ci.sh --phase plan
bash scripts/harness-ci.sh --phase promptfoo
```
