# AppSell design system — Studio subset

- Fonte: Studio local em `apps/web/src/app/globals.css`, inspecionado em 2026-07-10.
- Escopo: subset próprio; não há dependência de runtime nem cópia de componentes do Studio.
- Estado: protótipo Fase 2; tokens podem evoluir por PR revisada.

## Direção

Interface clara, operacional e editorial. Canvas frio, superfícies brancas, sidebar azul-marinho no desktop e navegação inferior no celular. Azul comunica ação e estrutura; laranja é assinatura de marca, não cor dominante de CTA.

## Tokens principais

| Papel | Valor |
|---|---|
| canvas | `#f4f6fb` |
| surface | `#ffffff` |
| primary ink | `#00367d` |
| primary | `#1e3a8a` |
| primary bright | `#3b82f6` |
| atmosphere | `#54b3f1` |
| brand accent | `#e64f18` |
| text strong | `#0f172a` |
| text base | `#475569` |
| sidebar | `#0b1e3f` |

## Regras

- mobile-first a partir de 320 px; breakpoints principais em 680 e 1025 px;
- texto-base mínimo de 16 px em formulários futuros;
- alvo interativo mínimo de 44 px;
- foco visível de 3 px;
- máximo de cinco destinos na navegação móvel; o slice usa três;
- sem hover como único meio de descobrir ou executar uma ação;
- headings com tracking compacto e corpo com line-height entre 1.5 e 1.65;
- animações entre 150 e 300 ms e neutralizadas com `prefers-reduced-motion`;
- estados não dependem apenas de cor: sempre há texto;
- dados de demonstração e integrações bloqueadas devem permanecer rotulados.

## Anti-padrões

- copiar o monólito CSS ou os destinos de navegação do Studio;
- marketplace global fora do contexto de seller;
- inferir tenant, papel ou entitlement do `subject` da sessão;
- esconder integração inexistente atrás de números ou ações aparentemente reais;
- armazenar token, sessão, papel ou tenant em Web Storage.
