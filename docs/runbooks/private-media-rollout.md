# Rollout de mídia privada

O upload de vídeo nasce desativado (`APPSELL_API_VIDEO_UPLOAD_ENABLED=false`). A flag é uma trava de compatibilidade: nenhum MP4 novo pode entrar enquanto workers antigos ainda puderem consumir a fila compartilhada.

## Ordem obrigatória

1. Aplicar a migration `0013_private_media` mantendo a flag desativada.
2. Publicar o sandbox de mídia com `/health`, `/normalize` e `/probe-video`; confirmar `network_mode: none`, filesystem read-only, limites de processo/memória e `ffprobe` saudável.
3. Publicar somente workers `media-v1`, verificar ClamAV saudável e drenar/remover todos os workers anteriores.
4. Publicar API e BFF compatíveis, executar upload MP4 sintético em homologação e provar a ordem `quarentena -> ClamAV -> sandbox ffprobe -> ready`.
5. Ativar `APPSELL_API_VIDEO_UPLOAD_ENABLED=true` apenas na API após a drenagem. Fazer rollback da flag para `false` antes de qualquer rollback de worker.

## Rollback

Desativar primeiro a flag. Ela bloqueia novas escritas; não remove a necessidade de leitura compatível.

Antes do primeiro MP4 verificado, um rollback do reader para pré-`media-v1` só é permitido se este gate retornar `0`:

```sql
select count(*) from asset_blob where media_type = 'video/mp4';
```

Esse gate cobre o estado `ready` antes da publicação e os drafts ativos que o fluxo legado ainda poderia publicar. Após o primeiro resultado maior que zero, API e BFF `media-v1` são um piso forward-only. É proibido reverter o reader para uma versão pré-#72; uma falha deve ser corrigida por roll-forward. `VersionId` preserva os bytes, mas não torna o reader antigo compatível.

Worker e sandbox só podem recuar depois de desativar a flag e provar que este gate também retorna `0`:

```sql
select count(*)
from app_private.asset_upload
where expected_media_type = 'video/mp4'
  and status in ('pending', 'uploaded', 'verifying', 'ready');
```

Não apagar blobs, filas ou versões S3. Objetos não publicados permanecem sob reconciliação normal e objetos publicados continuam imutáveis por `VersionId`.
