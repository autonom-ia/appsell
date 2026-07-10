# ADR 0004 — Produto, conteúdo, storage e publicação

- Status: Accepted with gates
- Data: 2026-07-10
- Decisores: Rodrigo / Produto / Conteúdo / AppSec
- Issue: [autonom-ia/appsell#9](https://github.com/autonom-ia/appsell/issues/9)

## Contexto

Produto comercial, oferta e conteúdo são conceitos diferentes. Sellers precisam
editar sem expor drafts, buyers precisam consumir versões estáveis, e assets
privados não podem se tornar públicos por URL previsível ou cache indevido.

## Decisão

### Agregados

- `CatalogProduct`: unidade apresentada e vendida pelo seller.
- `Offer`: termos comerciais externos, preço informativo, período e regras; não é
  autoridade de cobrança do checkout externo.
- `Course`: estrutura de aprendizagem reutilizável.
- `ProductCourse`: produto concede um ou mais cursos, sempre no mesmo seller.
- `Module`, `Lesson` e `ContentBlock`: estrutura editorial.
- `Asset`: metadados seller-scoped de objeto privado no storage.
- `Publication`: snapshot imutável do conteúdo publicado.

O browser nunca decide preço final, entitlement ou status de pagamento.

## Estados

### Produto

```text
draft -> published -> archived
          |    ^
          v    |
       unpublished
```

Uma transição exige validação server-side. `archived` impede novas vendas e não
revoga automaticamente entitlements existentes; a política de acesso após
arquivamento é uma decisão de produto pendente.

### Conteúdo

Edição ocorre em draft mutável com controle otimista (`version`/ETag). Publicar:

1. trava logicamente a versão esperada;
2. valida estrutura, referências e assets prontos;
3. produz snapshot imutável e versionado;
4. atualiza o ponteiro publicado em transação;
5. grava auditoria e outbox;
6. invalida somente caches públicos relacionados.

Buyers leem o snapshot publicado. Alterar draft não muda a experiência ativa.
Rollback editorial aponta para snapshot anterior após validação; não edita o
snapshot histórico.

## Autosave e conflitos

- client envia versão base/ETag e patch mínimo;
- server rejeita versão obsoleta com conflito explícito;
- UI preserva rascunho local e oferece recarregar/comparar, sem sobrescrever
  silenciosamente;
- confirmação de save significa commit durável, não apenas fila no browser;
- retry usa chave idempotente e não duplica blocos;
- ordenação possui controles acessíveis além de drag;
- interrupção/reload não descarta edição confirmada.

## Storage privado

Objetos usam chave não previsível e seller-scoped, por exemplo:

```text
seller/<seller_uuid>/asset/<asset_uuid>/version/<version_uuid>/<safe_name>
```

O nome original é metadado sanitizado, não parte confiável da chave. Bucket não
é público e não permite listagem anônima.

Fluxo de upload:

1. seller autorizado solicita intenção de upload com tamanho e tipo esperados;
2. API cria `Asset` em `pending` e concede upload curto para uma chave exata;
3. storage limita método, tamanho e headers quando suportado;
4. client confirma conclusão;
5. worker verifica tamanho, assinatura/magic bytes, MIME real e hash;
6. arquivo passa por malware scan/quarentena quando aplicável;
7. somente então `Asset` torna-se `ready` e pode integrar publicação.

SVG/HTML/executáveis são bloqueados no MVP. Imagens são reprocessadas; PDFs e
arquivos permitidos são servidos como attachment quando necessário. Conteúdo
ativo nunca é refletido no domínio principal sem sanitização e CSP apropriadas.

## Download e mídia

- API revalida sessão, entitlement ativo, seller, produto e asset;
- URL assinada tem duração curta, escopo para um objeto e resposta privada;
- CDN/storage não recebe cookie de sessão do AppSell;
- cache key não depende só do path quando houver autorização;
- logout/revogação bloqueiam emissão de novas URLs; o TTL limita URLs já emitidas;
- logs não guardam URL assinada completa;
- vídeo externo usa allowlist de provedores e sandbox; streaming próprio fica fora
  do MVP.

## Catálogo público

Somente projeções explicitamente públicas são retornadas: nome/slug/descrição,
cover aprovada, seller público, oferta visível e metadados necessários à vitrine.
Draft, asset privado, conteúdo integral, buyers, métricas e PII não entram na
projeção. Slugs são únicos dentro do seller.

## Mobile-first e acessibilidade

- editor inicia em 360 px e preserva estado em rede lenta/intermitente;
- ações persistentes ficam acessíveis ao polegar e respeitam safe area;
- upload mostra progresso, pausa/falha e retomada segura;
- reordenação funciona por botões/teclado, não só drag;
- preview deixa claro seller, produto, versão e estado não publicado;
- player suporta orientação, zoom, legendas quando fornecidas e reduced motion;
- nenhum asset privado entra em service worker/cache público.

## Alternativas rejeitadas

- editar diretamente a versão publicada: impede preview e rollback confiável.
- armazenar mídia no disco da VPS: não oferece durabilidade/escalabilidade
  adequadas e aumenta acoplamento ao host.
- confiar no MIME do client: permite conteúdo ativo/malicioso.
- URLs permanentes públicas: quebram revogação e isolamento.
- copiar o editor do Studio: não há pacote UI estável verificado e as jornadas
  diferem.

## Validação obrigatória

- draft nunca aparece em rota pública ou buyer;
- snapshot continua estável enquanto draft muda;
- rollback editorial recupera snapshot anterior;
- produto/curso/asset de A não pode ser ligado a B por API ou SQL;
- MIME falso, polyglot, SVG, executável, traversal, excesso de tamanho e arquivo
  incompleto são negados/quarentenados;
- URL expirada, alterada ou emitida para outro buyer é negada;
- storage indisponível não cria asset `ready` nem corrompe publicação;
- cache/service worker não retém resposta privada após logout;
- criação → edição → preview → publicação funciona no celular suportado.

## Consequências

Snapshots e processamento assíncrono consomem mais storage e exigem limpeza por
retenção. Em troca, publicação, rollback e leitura do buyer tornam-se estáveis e
auditáveis.
