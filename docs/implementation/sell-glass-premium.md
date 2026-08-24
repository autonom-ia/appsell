# Sell Glass Premium — entrega da Issue #204

## Objetivo

Aplicar ao Sell a hierarquia visual premium estudada no `vector-api`, preservando a identidade e os contratos funcionais do produto. A referência é de linguagem visual; nenhum componente, rota, dado ou texto do projeto de origem foi copiado.

## Decisões de design

- Canvas frio (`#f4f6fb`) com atmosfera azul suave.
- Inter no corpo e Space Grotesk nos títulos, carregadas por `next/font`.
- Glass restrito a shell, cabeçalhos, navegação e overlays: membrana de 74%, borda clara e blur entre 20 e 32 px.
- Cards, formulários, checkout, biblioteca, player e conteúdo editorial permanecem brancos e opacos.
- Sidebar desktop flutuante em gradiente `#07162e → #00367d`, com três níveis de sombra.
- Azul continua sendo a cor de ações operacionais; laranja (`#e64f18`) fica restrito a entrada/conversão, publicação, ativação de vendas e checkout. Os fundos desses CTAs usam tons mais escuros do token para manter contraste mínimo de 4,5:1 com texto branco.
- Breakpoint intermediário de 680 px e shell desktop a partir de 1024 px, com alvos mínimos de 44 px, foco visível e `prefers-reduced-motion` preservados.

## Escopo técnico

- `app/layout.tsx`: variáveis locais das fontes Inter e Space Grotesk.
- `app/styles.css`: tokens de superfície/sombra, shell desktop e móvel, superfícies opacas, CTAs comerciais e fallback sem `backdrop-filter`.
- `app/s/[sellerSlug]/*`: diferenciação entre acesso gratuito operacional e compra comercial.
- `app/components/library-workspace.tsx` e `app/components/product-editor-workspace.tsx`: consistência dos CTAs comerciais em estados vazios e confirmações.
- Nenhuma mudança de API, banco, autorização, sessão, upload, checkout, entitlement ou conteúdo.

## Matriz de fidelidade

| Ponto | Conceito aprovado | Implementação |
|---|---|---|
| Copy | Dashboard Sell com texto real | Todo texto e dado vêm dos componentes existentes; nenhuma copy inventada |
| Layout | Sidebar flutuante, header glass, métricas e cards opacos | Aplicado no desktop; topbar e navegação inferior flutuantes no móvel |
| Tipografia | Headings geométricos e corpo neutro | Space Grotesk + Inter via `next/font` |
| Paleta | Canvas frio, navy/azul e laranja pontual | Tokens preservados e hierarquia comercial/operacional explicitada |
| Superfícies | Glass no shell, branco no conteúdo | Blur limitado à membrana e overlays; zero glass-on-glass |
| Responsivo | Desktop amplo e móvel de 390 px | Regras para 1440/1024/390/320, sem alterar a navegação por papel |
| Movimento | Microinterações discretas | 180 ms e neutralização global por `prefers-reduced-motion` |

## Termos de aceite

- [x] A hierarquia premium está aplicada ao shell, sem transformar cards de conteúdo em glass-on-glass.
- [x] Azul representa ações operacionais; laranja identifica entrada, publicação, ativação e compra, com contraste mínimo de 4,5:1.
- [x] O layout não apresenta overflow horizontal em 320, 390, 1024 e 1440 px.
- [x] Alvos interativos móveis têm pelo menos 44 px, foco visível, zoom textual de 200% e redução de movimento preservados.
- [x] Storefront, checkout, biblioteca, editor, upload, publicação, entitlement e player mantêm seus contratos e negativas existentes.
- [x] Lint, typecheck, testes rápidos, testes integrais, E2E em Chromium/WebKit e build de produção passam localmente.
- [x] Não há mudança de banco, API pública, autenticação, billing, secrets ou infraestrutura.
- [x] Merge, deploy e smoke de produção concluídos no PR #205; a evolução do
  leitor foi validada pelo usuário nos ciclos #208 a #224.

## Verificação e rollback

### Resultado local

- `pnpm lint`: PASS.
- `pnpm typecheck`: PASS.
- `pnpm test:fast`: PASS, 562 testes executados e 2 skips condicionais.
- `pnpm test`: PASS, 576 testes executados e 9 skips condicionais.
- Testes focados de storefront, checkout, isolamento, assets e biblioteca: PASS, 91 testes executados e 1 skip condicional.
- `tests/e2e/mobile-shell.spec.ts`: PASS em Chromium e WebKit, 28 testes executados e 10 skips intencionais por projeto.
- `tests/e2e/learning-player.spec.ts`: PASS em Chromium e WebKit, 20 testes executados.
- `pnpm build`: PASS, com todas as rotas Next geradas.
- Revisão independente do diff: os apontamentos sobre arquivo gerado, consistência de CTAs e sombra em estados desabilitados foram corrigidos; nenhuma regressão funcional adicional foi encontrada.
- QA renderizado via Playwright headless em 1440×1000, 1024×768, 390×844 e 320×800: sem overflow horizontal; alvos móveis entre 54 e 57 px; foco de 3 px; `prefers-reduced-motion` efetivo; zoom textual de 200% sem overflow.
- Vitrine, produto pago e acesso gratuito foram renderizados contra uma API HTTP local simulada com o contrato público estrito. Cards ficaram opacos e CTAs com 48 px; compra permaneceu laranja e acesso gratuito azul.

Os perfis compartilhados do navegador estavam ocupados, por isso a inspeção usou processos headless descartáveis de Chromium e WebKit. O preview integrado com banco/API reais não foi iniciado porque Docker não está disponível nesta máquina; os contratos e as negativas correspondentes foram exercitados pelas suítes focadas e E2E do repositório.

O `test:fast` de `origin/main` inicialmente falhava porque o teste do EasyPanel exigia uma frase de segurança ausente no README. A frase foi restaurada sem mudança de runtime para devolver o gate ao estado verde.

Não houve migração de dados nessa entrega. O estado atual e o rollback vigente
estão em `docs/PRODUCTION_CURRENT.md` e
`docs/runbooks/PRODUCTION_RELEASE_APPSSELL.md`.

### Desvios intencionais do conceito

- Nomes, métricas, navegação e estados continuam vindo do Sell; nenhum conteúdo inventado pelo conceito foi incorporado.
- Não foram adicionados ícones ou imagens decorativas sem origem no produto.
- Em 1024 px o shell desktop é usado para evitar uma barra móvel excessivamente larga; abaixo disso permanece mobile-first.
- Cards do dashboard ficam horizontais apenas a partir de 1200 px para preservar leitura em 1024 px.
