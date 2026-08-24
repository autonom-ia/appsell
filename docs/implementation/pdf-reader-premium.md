# Sell Reader Premium

Issues: #208, #214, #220 e #224

Status: implantado e validado em produção em 2026-08-24.

## Experiência entregue

- Leitor em modo foco, sem sidebar nem conteúdo da aula competindo com o documento.
- Leitura vertical contínua, ajuste à largura e pré-carregamento das páginas vizinhas.
- Zoom de 100% a 400%, gesto de pinça, toque duplo, rolagem e pan nativos.
- Navegação anterior/próxima, salto direto, teclado, tela cheia e retomada por sessão.
- Preview da aula sem limite artificial de altura e sem tarja sobre o conteúdo.
- Toolbar responsiva com alvos de toque e nomes acessíveis.
- Links do índice funcionais e player de vídeo protegido dentro da plataforma.
- Cache da sessão, favoritos e marcações vetoriais privadas e persistentes.
- Mão com arraste por mouse no desktop e Editar marcação com orientação de uso.

## Proteção preservada

- O PDF original continua fora do navegador para conteúdo sem download autorizado.
- Manifesto e páginas continuam condicionados à sessão, entitlement, publicação e tenant.
- Cada página segue privada, autenticada e `no-store`.
- A identificação é mascarada no BFF, transportada na query assinada da requisição interna e incorporada no raster pelo servidor.
- O rodapé de licença é acrescentado fora da área original da página. Não existe marca diagonal nem sobreposição no conteúdo.
- Revogação, acesso cruzado e tentativa de baixar o original continuam falhando de forma fechada pelos contratos existentes.

## Termos de aceite

1. No desktop, o modo foco usa a largura útil e mantém texto legível sem depender do zoom do navegador.
2. No celular, a página abre ajustada à largura e aceita zoom até 400% sem overflow da aplicação.
3. A leitura é contínua e as páginas anterior e seguinte são antecipadas sem baixar o documento completo.
4. A página atual e o zoom são retomados na mesma sessão.
5. Nenhuma identificação cruza o conteúdo original. Existe somente um rodapé discreto, mascarado e rasterizado no servidor.
6. O original não é exposto, impressão e salvamento permanecem bloqueados na interface e respostas continuam `private, no-store`.
7. Controles funcionam por toque e teclado, têm foco visível e nome acessível.
8. Chromium e WebKit mobile passam no fluxo de abrir, ampliar, navegar, retomar e fechar o leitor.
9. Não há mudança em checkout, billing, storage, banco ou política de download.

## Evidência local

| Gate | Resultado |
| --- | --- |
| TypeScript e lint | PASS |
| Testes frontend | PASS: 578 passaram, 9 ignorados por ambiente |
| Testes API e raster | PASS: suíte completa de `apps/api/tests` |
| E2E Chromium e WebKit mobile | PASS: 56 passaram, 10 combinações deliberadamente ignoradas |
| Build de produção | PASS |
| Preview integrado com API e banco reais | Bloqueado localmente: Docker indisponível |

O preview visual usa o mesmo componente e uma página PNG real do pacote demo, servida por uma rota simulada apenas no teste. Isso prova o layout renderizado, mas não substitui o smoke pós-deploy com sessão e dados reais.

## Evidência de produção

- PRs #211, #215 a #219, #223 e #225 mergeados.
- Web `1d2ea631`; API/asset-worker `50b6301`; migration
  `0033_pdf_study_state`.
- CI pós-merge `32768382697` verde; web healthy e readiness público saudável.
- Rodrigo validou Mão e Editar marcação na sessão autenticada real.
- Rollback do último hotfix: web `50b6301`; nenhuma mudança de API, worker,
  banco, auth ou billing no cutover do PR #225.
