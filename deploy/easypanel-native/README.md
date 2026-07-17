# EasyPanel nativo — Autonom.ia Sell

Este diretório substitui o transporte customizado de imagens/TARs por serviços
nativos do EasyPanel. O rollout da Issue #90 é somente uma homologação pública do
demo em modo `ci`, protegida por Basic Auth. Ele não pode ser promovido para
`sell.autonomia.site` nem chamado de produção real.

## Serviços novos no projeto `sell-autonomia`

| Serviço | Tipo/source | Porta/domínio | Limites CPU/RAM |
|---|---|---|---|
| `postgres-native-90` | PostgreSQL nativo | interna `5432` | `0.75/0.10`, `768/256 MiB` |
| `redis-native-90` | Redis nativo | interna `6379` | `0.25/0.05`, `256/64 MiB` |
| `bootstrap-native-90` | GitHub + `Dockerfile.bootstrap` | sem porta/domínio; temporário | `0.25/0.05`, `512/128 MiB` |
| `api` | GitHub + `Dockerfile.api` | interna `8000` | `0.75/0.10`, `768/256 MiB` |
| `web` | GitHub + `Dockerfile.web` | interna `3000` | `0.75/0.10`, `768/256 MiB` |

O primeiro número de cada par é limite; o segundo é reserva. App Services usam
uma réplica, `zeroDowntime=true`, `tiniInit=true` e `capDrop=["ALL"]`. O EasyPanel
2.31 não expõe limite de PIDs nem root filesystem read-only no schema nativo de
App Service; por isso os containers rodam non-root, sem mounts e sem capabilities.

Os serviços antigos `postgres`, `redis`, `storage`, `runtime-preview` e o
laboratório #71 permanecem intocados. Nenhum serviço novo pode entrar nas redes
`easypanel-flow` ou `easypanel-dados`.

## Gates antes da primeira escrita

1. SHA imutável em Draft PR, Harness 2.1.2 e reviews críticos verdes.
2. Workflow `EasyPanel native images` verde para os três Dockerfiles.
3. Inventário read-only de serviços, containers, redes, CPU, memória e disco.
4. Token GitHub do EasyPanel com acesso somente de leitura ao repo privado.
5. Builds sequenciais. Parar se memória disponível cair abaixo de 8 GiB, disco
   livre abaixo de 60 GiB ou load de 1 minuto permanecer acima de 6.

## Configuração exata da homologação

1. Criar `postgres-native-90` com usuário/database inicial
   `appsell_native_admin`, senha nova e sem porta publicada.
2. Criar `redis-native-90` com senha nova e sem porta publicada.
3. Criar `bootstrap-native-90` a partir da branch/SHA revisados, source path `/`,
   Dockerfile `deploy/easypanel-native/Dockerfile.bootstrap` e ambiente baseado em
   `bootstrap.staging.env.example`.
4. Implantar `bootstrap-native-90` com auto-deploy desligado. O `CMD` é somente
   `sleep infinity`, evitando o restart automático de um processo one-shot pelo
   Swarm. Confirmar uma única task em execução e, pelo Console nativo do EasyPanel,
   executar exatamente uma vez:

   ```sh
   /opt/app/deploy/easypanel-native/bootstrap-native-demo.sh
   ```

   O script possui trava contra concorrência no container, cria somente o banco
   `appsell_native_90`, aplica migrations/configuração e faz o primeiro seed demo.
   Se o banco já estiver concluído e as chaves forem idênticas, sai sem resetar o
   demo. Qualquer drift falha antes de rotacionar senhas.
5. Confirmar nos logs `native bootstrap=ready database=appsell_native_90` ou
   `native bootstrap=already-complete database=appsell_native_90`. Na mesma janela,
   ajustar réplicas para zero e destruir somente `bootstrap-native-90`, verificando
   que sua spec/env não existe mais. O marker final só é gravado depois de migrations,
   configuração, seed e `schema_ready`; eventual restart anterior retoma sem tratar
   execução incompleta como sucesso. Nunca remover os serviços de dados.
6. Criar `api` com `deploy/easypanel-native/Dockerfile.api`, ambiente baseado em
   `api.staging.env.example` e sem domínio. A chave
   `APPSELL_API_CONTEXT_HMAC_KEY_B64` deve ser o base64 dos mesmos 32 bytes usados
   em `APPSELL_CONTEXT_SIGNING_KEY_HEX` pelo bootstrap.
7. Criar `web` com `deploy/easypanel-native/Dockerfile.web`, ambiente baseado em
   `web.staging.env.example` e Basic Auth obrigatório com credencial exclusiva de
   homologação.
8. Adicionar pelo Domain & Proxy nativo:
   `sell-issue90.85-31-60-100.sslip.io` → `web:3000`, HTTPS habilitado.
9. Validar API `/health/ready` internamente e web `/api/health/ready` por HTTPS.
10. Rodar E2E real de admin, comprador, superadmin, cupom 100%, vídeo, PDF-aula
    protegido e PDF de apoio autorizado.

O login demo exige persona explícita e assina `state + persona`; remover ou trocar a
persona no redirecionamento OAuth é recusado. A persona superadmin só pode ser
exercitada atrás do Basic Auth da homologação. O fallback sem persona é recusado.

## Conteúdo das imagens

- `web`: bundle standalone, vídeo demo e somente o PDF de apoio autorizado
  `05-deploy-e-rollback.pdf`.
- `api`: código da API e somente as cinco páginas PNG pré-rasterizadas.
- `bootstrap-native-90`: migrations, manifesto e mídia integral para seed; fica
  inerte até uma execução manual única e é destruído na mesma janela.

Os PDFs-aula 1–4 não entram nas imagens públicas `web` ou `api`.

## Rollback desta homologação

O rollout é aditivo e não altera `sell.autonomia.site`; portanto rollback significa:

1. remover o domínio temporário do `web`;
2. parar somente `web` e `api` nativos;
3. preservar `postgres-native-90` e `redis-native-90` para diagnóstico;
4. provar por inventário que runtime antigo, n8n e outras redes não mudaram.

Nenhum banco, role ou volume é apagado no ciclo de rollback.

## Produção permanece bloqueada

Mover `sell.autonomia.site` exige outro slice com OAuth real da Autonom.ia Identity,
PostgreSQL/S3 TLS, ClamAV, sandbox isolado, financeiro real, Neuro.ai, review e um
cutover/rollback testado. Não existe marker local-demo de produção neste código.
