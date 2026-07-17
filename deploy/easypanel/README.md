# EasyPanel — `sell-autonomia`

Este diretório documenta o hardening pós-deploy dos serviços exclusivos da
Autonom.ia Sell. Ele nunca opera serviços dos projetos `dados` ou `flow`.

## Invariante

- PostgreSQL, Redis e MinIO usam somente `easypanel-sell-autonomia`;
- nenhum deles publica portas, domínio ou rota Traefik;
- todos têm limites/reservas de CPU e memória;
- todos têm healthcheck contínuo e precisam estar `healthy`;
- alterações feitas pelo EasyPanel podem recriar a rede e o healthcheck, então
  o guard deve rodar depois de cada atualização pelo painel.
- o artefato de configuração da release inclui `easypanel/` com os scripts;
  a aplicação do hardening deve usar arquivos provenientes do mesmo SHA aprovado.

## Runtime preview da Issue #77

`compose.runtime-preview.yaml` entrega a primeira navegação Local/CI sem esperar
o artifact do Identity Auth. Ele contém somente:

- `domain-bootstrap`, que cria de forma aditiva o database marcado
  `appsell_preview_77`, aplica migrations e refaz exclusivamente a tenant demo;
- `domain-api`, privado na rede `easypanel-sell-autonomia`;
- `pdf-sandbox-init`, one-shot sem rede que aceita somente o volume UDS novo ou
  já pertencente ao UID/GID `10001:10001`;
- `pdf-sandbox`, sem rede IP, non-root e sem acesso aos PDFs incorporados na
  imagem; ele entrega apenas manifesto e PNG rasterizado por página e recicla
  o container inteiro depois de cada job, eliminando processos residuais;
- `sell-web`, executado com `next start`, cookies `Secure`/`__Host-`, OAuth local
  explicitamente limitado ao contrato imutável desta homologação e três personas demo;
- `ingress-proxy`, sem secrets e ligado somente à rede interna dedicada e ao
  ingress existente do EasyPanel;
- `evidence-init`, `e2e-transport-init`, `e2e-bridge` e `e2e`, habilitados
  apenas pelo profile `e2e`. O navegador de teste não recebe rede IP: um proxy
  loopback encaminha TLS bruto por Unix socket para a bridge efêmera, que é o
  único container na rede pública de teste.

O Compose reutiliza `sell-autonomia_postgres` e `sell-autonomia_redis`; não cria
outra infraestrutura e não referencia `dados` ou `flow`. Na primeira ativação,
somente o web publica `127.0.0.1:13200`. O database não é apagado no rollback.
Identity/GitHub OAuth real, Financial, Neuro.ai, novos uploads e storage público
ficam explicitamente fora desta preview. Os PDFs demo já incorporados são
visualizados apenas como páginas rasterizadas; o original continua disponível
somente na rota de download de material de apoio autorizada pelo admin.

Antes de qualquer carga na VPS, verifique os quatro artifacts do mesmo run/SHA:

```bash
APPSELL_ARTIFACT_BRANCH=infra/77-easypanel-runtime-preview-rc \
  deploy/preview-vps/verify-downloads-local.sh ROOT APPSELL_SHA APPSELL_RUN_ID
```

O diretório `ROOT/upload` conterá apenas config, web, API e base Alpine. Depois
envie o conteúdo de `ROOT/upload` para o caminho exato
`/opt/autonomia-sell-preview/issue-77/APPSELL_SHA_12`. Na VPS, carregue somente
as três imagens registradas e gere o env; o script lê
apenas os envs dos dois serviços exclusivos, cria chaves independentes e não
imprime valores:

```bash
RUN_ROOT=/opt/autonomia-sell-preview/issue-77/APPSELL_SHA_12
sudo "$RUN_ROOT/config/easypanel/load-runtime-preview-images.sh" \
  "$RUN_ROOT" APPSELL_SHA
sudo "$RUN_ROOT/config/easypanel/generate-runtime-preview-env.sh" \
  /root/sell-runtime-preview-env-77 APPSELL_SHA
```

O preflight recusa serviço, container ou porta preexistente, valida o isolamento
dos três serviços nativos e salva o hash da especificação de todos os serviços
Docker já existentes. As credenciais do EasyPanel são fornecidas somente como
env ao processo na própria VPS; a API aceita apenas loopback:

```bash
ENV_FILE=/root/sell-runtime-preview-env-77/runtime-preview.env
sudo -E "$RUN_ROOT/config/easypanel/preflight-runtime-preview.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA
sudo -E env CONFIRM_RUNTIME_PREVIEW_UP=appsell-runtime-preview-77 \
  "$RUN_ROOT/config/easypanel/up-runtime-preview.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA
sudo -E "$RUN_ROOT/config/easypanel/verify-runtime-preview.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA
```

O primeiro deploy permanece privado até a verificação acima ficar verde. Depois,
a ativação pública aditiva cria exatamente um domínio EasyPanel para
`sell-autonomia/runtime-preview`, exige DNS exclusivo em `85.31.60.100`, valida
o certificado emitido para `sell.autonomia.site` e registra uma attestation
root-only. Ela não altera nem reinicia `dados`, `flow` ou qualquer serviço n8n:

```bash
sudo -E env CONFIRM_RUNTIME_PREVIEW_PUBLIC=sell-autonomia-public-77 \
  "$RUN_ROOT/config/easypanel/activate-public-runtime-preview.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA
sudo -E env RUN_RUNTIME_PREVIEW_E2E=1 \
  "$RUN_ROOT/config/easypanel/verify-runtime-preview.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA
```

O E2E público resolve `sell.autonomia.site` pelo proxy loopback, mas mantém TLS
fim a fim até o Traefik e só passa com admin, buyer, superadmin, PDF e vídeo
verdes. Esta etapa é homologação pública com identidades sintéticas; OAuth,
billing e onboarding de clientes reais continuam dependendo dos respectivos
gates de integração.

Se uma execução anterior deixou o volume de evidências no contrato legado com
label de SHA, migre-o uma única vez depois que o novo runtime estiver ativo e
antes do E2E. O script exige o SHA legado observado, recusa consumidores,
arquiva o conteúdo em modo `0600` dentro do novo run root e só então substitui
o volume exclusivo por outro com ownership estável:

```bash
sudo -E env CONFIRM_RUNTIME_PREVIEW_EVIDENCE_MIGRATION=sell-autonomia-evidence-v1 \
  "$RUN_ROOT/config/easypanel/migrate-legacy-runtime-evidence.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA LEGACY_EVIDENCE_SOURCE_SHA
```

Antes de teardown, rollback ou troca de SHA, a rota pública deve ser removida
com ownership exato. A ação só apaga o único domínio pertencente a
`sell-autonomia/runtime-preview`, preserva o serviço, banco e volumes e confirma
novamente o snapshot dos demais serviços:

```bash
sudo -E env CONFIRM_RUNTIME_PREVIEW_PRIVATE=sell-autonomia-private-77 \
  "$RUN_ROOT/config/easypanel/deactivate-public-runtime-preview.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA
```

Se a emissão/validação TLS falhar durante a primeira ativação, o script usa uma
intent root-only e remove automaticamente somente o domínio recém-criado. Uma
interrupção pode ser retomada pelo mesmo SHA sem adotar domínio de outro serviço.
Se `createDomain` tiver resultado incerto e nenhum domínio aparecer durante a
janela de reconciliação, a intent é preservada e a repetição falha fechada. Uma
nova tentativa de criação exige revisão dessa intent e a confirmação adicional
`CONFIRM_RUNTIME_PREVIEW_PUBLIC_RETRY=sell-autonomia-public-retry-77`.

Rollback inicial: o comando abaixo remove apenas os containers Compose
`sell-autonomia_runtime-preview`, verifica que os hashes dos serviços anteriores
continuam iguais e preserva o registro EasyPanel habilitado sem containers,
database, volume e imagens. A cópia `.env` do EasyPanel é removida somente após
hash, ownership e Compose down verdes; a attestation root-only permite retomada segura;
as imagens só são
removidas pelo segundo comando, usando o registro local de tag, SHA e image ID:

```bash
sudo -E env CONFIRM_RUNTIME_PREVIEW_TEARDOWN=appsell-runtime-preview-77-teardown \
  "$RUN_ROOT/config/easypanel/teardown-runtime-preview.sh" \
  "$RUN_ROOT" "$ENV_FILE" APPSELL_SHA
sudo "$RUN_ROOT/config/easypanel/remove-runtime-preview-images.sh" \
  "$RUN_ROOT" APPSELL_SHA
```

Antes de migrar o database principal ou ativar outro runtime no mesmo cluster,
é obrigatório remover o Compose e aposentar os dois logins exclusivos da
preview. O script falha se houver sessão ativa, marker divergente ou role
parcial; não apaga o database nem as roles canônicas:

```bash
sudo env CONFIRM_RUNTIME_PREVIEW_RETIRE=appsell-preview-77-retire \
  "$RUN_ROOT/config/easypanel/retire-runtime-preview-credentials.sh"
```

## Verificação read-only

```bash
sudo deploy/easypanel/verify-sell-isolation.sh
```

## Reaplicação idempotente

Esta ação reinicia somente os três serviços novos do projeto Sell. Ela exige
confirmação explícita, valida os três serviços e o verificador antes da primeira
mutação, e termina executando a verificação read-only com valores exatos de
CPU, memória e healthcheck. Serviços já conformes não são reiniciados; quando
há drift, o comando só retorna sucesso depois de `UpdateStatus=completed`, troca
da task e saúde confirmada na task nova.

```bash
sudo env CONFIRM_SELL_AUTONOMIA_HARDENING=sell-autonomia-v1 \
  deploy/easypanel/harden-sell-services.sh
```

O EasyPanel continua sendo a fonte de configuração dos serviços. O script
existe apenas porque a versão 2.31 conecta bancos/apps à rede global e não
persiste healthchecks no modelo retornado pela API.
