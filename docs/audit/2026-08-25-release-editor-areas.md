# Release 2026-08-25 (terceiro) — editor por áreas e autosave (#237)

```text
issue:             #237 (part of #228)
pr:                #244
merge_sha:         adc3fa498d54140a2296da62fab903e34365f93b
ci_run:            32854651433 — success
previous_release:  50f5849 (mesmo dia)
backup:            /home/claudete/backups/appsell/appsell_native_90_20260825T134930Z.dump
                   950.748 bytes · permissão 600
migration_head:    0036_lesson_material_links (INALTERADO — release sem migration)
web_image:         easypanel/sell-autonomia/web  @ adc3fa4
api_image:         easypanel/sell-autonomia/api:adc3fa4
worker_image:      easypanel/sell-autonomia/api:adc3fa4
health:            api  {"status":"ok","database":true,"storage":true,"pdf_sandbox":true}
                   web  {"status":"ok","redis":true,"api":true,"storage_upload":true}
rollback_decision: não acionado
```

## O que mudou na tela

O trilho de cinco etapas virou navegação por áreas, sem ordem obrigatória. A
coluna de prontidão passa a vir de `detail.readiness`, a mesma validação que o
servidor roda para liberar a publicação — antes o cliente calculava a sua,
criando duas fontes de verdade sobre a mesma pergunta. A prévia deixou de ser
etapa e virou "Ver como aluno", disponível de qualquer área. Autosave com pausa
de 2,5s, usando a trava de versão que já existia.

## Achados fechados antes do merge

- **CRITICAL, regressão introduzida na própria PR:** o envio pendente na saída
  da página reenviava ao servidor a edição que o vendedor tinha acabado de
  descartar. "Descartar e voltar" nunca zerava o estado sujo; antes do autosave
  não havia envio de saída, então o descarte funcionava por omissão.
- **MEDIUM:** o autosave lia a área ao vivo no disparo em vez da que estava
  aberta no agendamento, dependendo de três guardas de navegação para nunca
  salvar um grupo de campos achando que era outro.
- **LOW:** literal `"current"` inalcançável em `WorkflowStepState`.

## Sobre o teste de teclado (A3.5)

A primeira versão do e2e passava com o anel de foco apagado do CSS: usava
`.focus()` programático e aceitava `:focus-visible` como prova. A segunda ainda
passava para um elemento, porque aceitava qualquer `box-shadow` e o botão
secundário tem sombra permanente. A versão final mede só a largura computada do
contorno e anda com Tab de verdade; verificada apagando o anel de novo, quando
2 dos 3 testes falham. O perfil móvel é dispensado com motivo, não acomodado.

## Anotação desatualizada corrigida

A #237 registrava "duas portas para ativar venda". Verificado no código: o painel
legado em Oferta é só texto informativo, e os controles de ativação estão
exclusivamente na área Publicar. A segunda porta foi fechada na Onda 1.

## O que continua fora

- **#242:** o player entrega URLs de material de aulas que o aluno não abriu.
  Exige migration.
- **#232:** capa, catálogo e vocabulário.
