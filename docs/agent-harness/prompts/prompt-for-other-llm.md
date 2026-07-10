# Prompt para outra LLM

```text
Você está trabalhando em um repositório da Autonom.ia.

Use obrigatoriamente o GitHub Project:

Project: Autonom.ia Dev
URL: https://github.com/users/autonom-ia/projects/3

Tarefa:
<descrever tarefa>

Regras:
- Inspecione antes de editar.
- Crie ou use a Issue relacionada.
- Crie branch separada.
- Não trabalhe direto na main.
- Faça uma PR pequena e revisável.
- Use `Refs #...` se for parte de épico.
- Não use `Closes #...` se a PR não concluir a issue inteira.
- Atualize o Project.
- Preencha Projeto, Status, Tipo, Prioridade, Risco, Próxima ação e Ambiente.
- Rode testes/lint/typecheck/build quando aplicável.
- Inclua evidências no corpo da PR.
- Não faça merge sem aprovação explícita do Rodrigo.

Se não tiver acesso ao Project:
- diga isso claramente;
- inclua na PR uma seção `Project update pendente`;
- liste os campos que precisam ser preenchidos manualmente.
```
