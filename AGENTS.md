# Autonom.ia Sell — regras para agentes

Este arquivo vale para Claude Code, Codex e qualquer outro agente. O `CLAUDE.md`
só importa este arquivo (`@AGENTS.md`): mude aqui, nunca lá. As regras de
`/Users/rodrigosilva/dev/CLAUDE.md` e as globais do Rodrigo continuam valendo;
este arquivo só acrescenta o que é deste projeto.

## 1. O produto

Plataforma de produtos digitais da Autonom.ia (`https://sell.autonomia.site`),
no caminho de ser "uma Hotmart". O **vendedor** tem uma loja com marca própria,
monta produto → currículo → oferta → preview → publicar, e vende. O
**comprador** é uma identidade global (login pelo Identity da Autonom.ia) que
recebe acesso por *entitlement* e estuda na biblioteca. O **superadmin** é
global. Cobrança passa pelo serviço **Financial** (repo `autonom-ia/autonomia-financial`).

Stack: Next.js (`app/`, `lib/`) como web + BFF; FastAPI (`apps/api/`) com
Postgres e Alembic (`apps/api/migrations/versions/`); asset-worker e
pdf-sandbox; deploy em VPS EasyPanel (Docker Swarm).

## 2. Fontes de verdade

| Pergunta | Onde ler | Cuidado |
|---|---|---|
| O que está no ar? | `docs/PRODUCTION_CURRENT.md` | Já divergiu do host várias vezes. Vale o que o host responde. |
| O que foi decidido? | `docs/DECISIONS.md` | |
| O que o programa está fazendo? | `docs/programa/` e a Issue fixada "Painel do programa Sell" | |
| Quem é dono de qual pasta? | `docs/programa/TRILHAS.md` | |
| Como subir? | `docs/runbooks/PRODUCTION_RELEASE_APPSSELL.md` | |

**Histórico, não fonte:** `docs/audit/`, `docs/plans/`, `docs/research/`,
`docs/STATUS.md` e qualquer handoff solto em `/Users/rodrigosilva/dev`. Use só
para contexto e confira no código antes de afirmar qualquer coisa.

## 3. Como o trabalho é organizado

O Sell é tocado por **trilhas**: chats em paralelo, cada um dono de um conjunto
de pastas, coordenados pela sessão **Orquestração Sell**. O programa, as ondas e
as trilhas estão em `docs/programa/PROGRAMA.md`.

- Cada trilha só escreve nas pastas que são dela (`docs/programa/TRILHAS.md`).
  Precisa mexer fora? Pede ao dono pela Orquestração Sell. Conflito, ela decide.
- A Orquestração Sell atribui Issues, reserva números de migration, monta os
  lotes e mergeia PR seguro (ver seção 6). Ela não escreve código de trilha.
- **Merge e deploy entram pelo trem da Orquestração Deploy**, que cuida dos
  deploys de todos os projetos (`chat2you/docs/processo-de-release.md`).
- O chat **Qualidade e Segurança** revisa e testa cada PR com agentes novos,
  sem o contexto da trilha.

## 4. Fluxo de uma entrega

1. Issue com o problema e o critério de pronto, com rótulos `trilha:*` e `onda:*`.
2. Worktree em `/Users/rodrigosilva/dev/worktrees/appsell/<issue>-<slug>` a partir de `origin/main`.
3. Teste antes do código. Rodar e **ler a saída inteira**.
4. PR com `Refs #N` (ou `Closes #N` quando fecha a Issue inteira), e a evidência:
   comandos rodados com o resultado lido, isolamento entre lojas quando toca
   dado, linha **Liberação** (sem flag / piloto na loja X até a data Y / todas).
5. Revisão pelo chat Qualidade: `sell-reviewer`, `sell-tester` e, quando toca
   dinheiro, auth, upload, webhook ou dado pessoal, `sell-security-reviewer`.
   CI verde é condição, não revisão.
6. A Orquestração Sell monta o lote e mergeia (ou pede o OK do Rodrigo).
7. Deploy pelo trem, com rollback escrito antes. Cada trilha confere a sua parte
   na versão no ar e responde "ok, SHA".
8. Limpeza: `git worktree remove` + `git worktree prune`, snapshots do MacCluster
   da entrega apagados pelo caminho exato.

## 5. Comandos

```sh
pnpm install
pnpm lint
pnpm typecheck
pnpm test:fast                     # vitest rápido (o que o CI roda no PR)
pnpm test                          # vitest completo
uv run --project apps/api ruff format --check apps/api
uv run --project apps/api ruff check apps/api
uv run --project apps/api mypy --config-file apps/api/pyproject.toml apps/api/src apps/api/tests
uv run --project apps/api pytest apps/api/tests/<arquivo>.py
```

A lista exata do que o CI roda está em `.github/workflows/app-ci.yml` (roteada
por área alterada). Rode cada comando separado e leia o resultado de cada um.

Teste com Postgres real roda pelo MacCluster (regras globais, seção R5), nunca
contra banco de produção. Nunca encadear teste e commit (`pnpm test && git commit`).

## 6. Merge, migration e o que exige o Rodrigo

**PR seguro** — a Orquestração Sell pode mergear sozinha (delegado pelo Rodrigo
em 23/10/2026) quando **tudo** for verdade:

- CI verde no head atual;
- revisado pelo chat Qualidade, sem achado CRÍTICO ou ALTO aberto;
- sem migration, ou com migration só aditiva com número reservado;
- não toca dinheiro (checkout, Financial, Stripe, comissão, repasse), auth
  (Identity, sessão, cookies), infra/deploy, secrets ou dado pessoal em massa.

Todo o resto espera OK explícito do Rodrigo: merge de PR não seguro, deploy,
migration não aditiva, qualquer coisa em produção, DNS, permissões, billing,
liberar funcionalidade para todas as lojas.

**Migrations:** cadeia única e forward-only. Número reservado pela Orquestração
Sell antes de escrever (`docs/programa/TRILHAS.md`, seção "Reserva de
migrations"). Só aditiva no fluxo normal; remoção é expand/contract com rollback
escrito. Ordem de deploy: web → api → migration (Decisão 020).

## 7. Gates que não se abrem

- Nada de funcionalidade de dinheiro sem log, alerta e conciliação entre Stripe
  e acesso liberado (#147).
- Nada de divisão de pagamento antes de o estorno revogar o acesso (#151).
- Nada de pixel, cookie de afiliado ou rastreio sem consentimento registrado.
- Nada ligado para todas as lojas sem piloto com data de decisão.
- Toda tabela nova segue o padrão de isolamento: RLS `enable` + `force`, acesso
  só por função `security definer` em `app_private`, PK com
  `seller_organization_id`, e entra na lista do `schema_ready`.
- O browser nunca decide preço nem acesso (Decisão 005).

## 8. Regras que mais quebram aqui

- Sem `<select>` nativo. O projeto **ainda não tem** o componente de escolha
  acessível (padrão *select-only combobox*; referência: Bio
  `src/components/ui/choice-select.tsx`). Quem tocar primeiro numa tela com
  `<select>` cria o componente em `app/components/ui/` e troca todos os da tela.
  Hoje há `<select>` em `product-editor-workspace.tsx`,
  `product-draft-workspace.tsx`, `lesson-material-panel.tsx`,
  `lesson-asset-panel.tsx` e no painel de demo.
- Sem regex para interpretar o que uma pessoa escreveu.
- Nunca suprimir stderr de git (`2>/dev/null`, `|| true`).
- Nunca imprimir, logar ou commitar secret; citar só o nome da variável.
- Ferramenta que reescreve código (`eslint --fix`, `ruff --fix`): ler o diff e
  rodar os testes de novo antes de commitar.
- Nada sai da pasta do projeto; documento de trabalho vai em `docs/`.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
