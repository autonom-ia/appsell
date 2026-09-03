# PROGRESS — Appsell

Última atualização documental: 2026-09-03.

## Fase atual

**Trilho A do épico [#349](https://github.com/autonom-ia/appsell/issues/349)
fechado em 03/09**, com `api` e `web` em `89233e2` e o banco em
`0058_capa_na_biblioteca`. Foram quatro entregas em produção no mesmo dia:
Configurações da loja e página de vendas (`8a0eca0`, `0052`), o dono das
funções de recebimento (`0053`), o refino da loja com banner (`a49e017`,
`0054`) e o fecho do [#387](https://github.com/autonom-ia/appsell/issues/387)
(`89233e2`, `0056`–`0058`): banner do produto, cartões em 16:9, capa na Visão
geral e capa em faixa na biblioteca.

A regra que sobrou dessa onda está na Decisão 021: no alto da página de vendas
aparece **uma** imagem, nunca duas — banner do produto se houver, capa se não.
No alto da vitrine quem manda é o banner da loja, que é outra imagem. E a ordem de deploy `web → api → migration` deixou de ser preferência:
o parser do web recusa chave desconhecida, então acender campo no banco antes
do web derruba a tela de quem estiver com a versão anterior.

Dois defeitos conhecidos ficaram de fora do fecho: a intermitência do envio de
imagem (o servidor grava, o navegador perde a resposta) e o **App CI
`disabled_manually`**, que faz toda PR passar sem rodar teste nenhum.

**Página pública nova em produção desde 01/09** (`9ebb93f`), no lugar da
anterior. Hero em três atos, contraste medido sob os glifos contra o quadro mais claro do
vídeo de fundo, e a página finalmente encontrável: `robots.txt` e `sitemap.xml`
existem, cada loja tem título próprio, e o formulário de contato envia por SMTP
sem intermediário. Fora do escopo entregue: a página não tem menu no celular, e
o sitemap ainda não foi submetido ao Search Console.

**Em produção desde 02/09 (`9655a13`, só o `web`), [#369](https://github.com/autonom-ia/appsell/issues/369):** a
passagem entre os atos do hero deixou o scrub e passou a tocar no tempo
(decisão 018). Filmada a 28 fps, a troca antiga cabia em 4 a 8 quadros e
deixava o título antigo por cima do novo; a nova dura ~0,9 s em qualquer
velocidade de rolagem. O bloco de texto centra no aparelho, e no celular o
eyebrow fica na mesma linha nos três atos.

**PDF de 300 páginas em produção desde 31/08** (`712e5bb`). O playbook do
vendedor, com 83 páginas, deixou de estar a 83% do teto.

### O que ficou aberto, em ordem de valor

1. **[#320](https://github.com/autonom-ia/appsell/issues/320) — o serviço isolado
   tem um lugar só.** Leitura de links, rasterização e conversão de vídeo
   disputam. É o que impede o adiantamento dos vídeos (o aluno espera ~2 s no
   primeiro clique) e o que limita o teto de páginas a 300.
2. **[#325](https://github.com/autonom-ia/appsell/issues/325) — vídeo longo é
   impossível hoje.** A duração de 4 h é aceita e nunca fica pronta; o envio é
   limitado a 100 MiB, o que dá ~4 min de aula em 1080p. Subir de verdade exige
   **envio em pedaços** — hoje é um PUT único com 5 min de prazo.
3. **[#312](https://github.com/autonom-ia/appsell/issues/312) — a API não grava
   log.** Enquanto isso, "0 linhas de erro" não é evidência de nada, e todo
   diagnóstico depende de reproduzir o processo real.
4. **[#266](https://github.com/autonom-ia/appsell/issues/266) — cinco suítes fora
   do CI**, incluindo a de isolamento entre lojas.
5. **[#321](https://github.com/autonom-ia/appsell/issues/321)** — os limites de
   preparo são locais ao processo e não sobrevivem a mais de um worker.

## Entregue

- Autenticação real e sessão protegida no ambiente de produção.
- Hierarquia de plataforma: `platform_superadmin` global, tenants e
  `seller_owner` isolados.
- Tenants reais Autonom.ia e Hub2You.
- Editor com sequência Produto → Currículo → Oferta → Preview → Publicar.
- Produto, curso, oferta, publicação versionada e materiais verificados.
- Link gratuito com cadastro/login, entitlement e biblioteca.
- Link pago com checkout obrigatório e fulfillment antes do entitlement.
- Player/aulas/PDFs protegidos por buyer + seller + produto + publicação.
- Isolamento negativo entre tenants e bloqueio do comprador no editor.
- Entitlement gratuito idempotente.
- Backup pré-cutover e tags de rollback na VPS.
- PR #201 mergeada; CI pós-merge verde; deploy real validado.
- Issue #195 encerrada com 14/14 aceites PASS.

## Evidência final

| Gate | Evidência |
|---|---|
| CI | [run 32656218821](https://github.com/autonom-ia/appsell/actions/runs/32656218821) — success |
| Merge | [PR #201](https://github.com/autonom-ia/appsell/pull/201) |
| Produção | web/API/asset-worker em `2c8a3689` |
| Health | API `/health/ready` com database, storage e pdf sandbox saudáveis |
| Banco | 3 sellers, 2 memberships, 3 produtos, 11 assets, 0 entitlements persistidos |
| Aceite | [Issue #195](https://github.com/autonom-ia/appsell/issues/195), 14/14 PASS |

## Trabalho documental desta sessão

- Issue: [#202](https://github.com/autonom-ia/appsell/issues/202)
- Branch: `codex/202-docs-production-handoff`
- Objetivo: consolidar o estado real e separar produção de Local/CI/demo.
- PR: será aberta após revisão dos documentos alterados.

## Pendências reais

- Recebimentos/Stripe por tenant e operação financeira continuam separados do
  catálogo/publicação.
- Staging próprio, RPO/RTO formal e política de refund/chargeback ainda não são
  aceites de produção.
- Há Issues históricas abertas sobre slices anteriores; elas não devem ser
  interpretadas como regressão desta release sem reprodução no SHA atual.

## Regra de retomada

Antes de alterar código ou banco, revalidar branch, `origin/main`, SHA das
imagens, migration head, backup e comportamento público. Não usar documentos de
auditoria de julho como fotografia atual. Não executar reset/seed demo no banco
de produção.

O histórico detalhado permanece nos commits anteriores, ADRs e arquivos
`docs/audit/`; este arquivo é o resumo atual para handoff.
