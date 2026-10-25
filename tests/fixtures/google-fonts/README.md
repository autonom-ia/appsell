# Fontes do mock do Google Fonts

Arquivos `latin` servidos pelo Google Fonts em 25/10/2026, sem modificação,
para `scripts/google-fonts-mock.mjs`. O E2E mede layout (`boundingBox`); com a
fonte real, a medida é a mesma da produção, sem depender da rede (#513).

| Arquivo | Família | Pesos | Licença |
|---|---|---|---|
| `inter.woff2` | Inter v20 | 100–900 (variável) | `OFL-inter.txt` |
| `space-grotesk.woff2` | Space Grotesk v22 | 300–700 (variável) | `OFL-space-grotesk.txt` |
| `montserrat.woff2` | Montserrat v31 | 400–900 (variável) | `OFL-montserrat.txt` |
| `ibm-plex-mono-400.woff2`, `ibm-plex-mono-500.woff2` | IBM Plex Mono v20 | 400, 500 | `OFL-ibm-plex-mono.txt` |

Todas sob a SIL Open Font License 1.1. Fonte ou peso novo no `app/layout.tsx`
exige o arquivo aqui e a entrada no mock.
