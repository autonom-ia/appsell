# OpenBao Transit — contrato Local/CI

## Escopo certificado

Este slice integra um provider OpenBao Transit real ao token vault do AppSell,
mas deliberadamente o bloqueia em `NODE_ENV=production`. A certificação é
somente Local/CI e usa infraestrutura descartável, porta loopback e projeto
Compose exclusivo.
O runner falha antes de criar recursos se o Docker ativo não usar um socket
Unix local; `DOCKER_HOST`/contexts SSH ou TCP são recusados.

O provider:

- envia a DEK aleatória de 32 bytes apenas ao endpoint `encrypt` via HTTPS ou
  HTTP loopback;
- autentica o wrap com AAD canônica vinculada a provider, `kid` e contexto do
  vault;
- persiste somente `provider`, `kid`, `alg`, ciphertext opaco `vault:vN:*` e
  `keyVersion`;
- valida que a versão declarada coincide com o ciphertext;
- não possui fallback para provider local;
- usa token runtime apenas com `update` em `encrypt` e `decrypt` da chave exata.

A imagem de teste está fixada em OpenBao 2.5.4 pelo digest multi-arquitetura. O
bootstrap cria uma chave `aes256-gcm96` não exportável e sem backup em plaintext,
ativa `disable_upsert` e emite token órfão, não renovável e com TTL de 15 minutos.
O token também é criado sem a policy `default`; sua lista efetiva contém apenas
`appsell-transit-runtime`.
O root token existe apenas no processo de bootstrap/teste e nunca é entregue ao
Next.js.

Referências oficiais:

- [Transit API 2.5.x](https://openbao.org/api-docs/secret/transit/)
- [Transit secrets engine](https://openbao.org/docs/secrets/transit/)

## Executar a prova real

Pré-requisitos: Docker, `curl`, `jq`, `openssl`, Node 22 e dependências PNPM.

```bash
pnpm install --frozen-lockfile
./scripts/test-openbao-transit.sh
```

O script cria somente recursos com nome
`appsell-transit-test-<run>-<pid>`, usa porta aleatória, executa os testes e
remove contêiner, rede e tmpfs no `trap`. Ele não acessa VPS, Redis, Postgres ou
volume existente.

Provas executadas:

- wrap/unwrap real e envelope sem DEK, token, e-mail ou subject em plaintext;
- token runtime impedido de ler metadados da chave;
- indisponibilidade, token inválido, HTTP/protocolo incorreto e adulteração
  falham fechados com erro sanitizado;
- falha do Transit após exchange Identity não grava sessão/vault parcial;
- rotação v1→v2 mantém v1 legível e novas gravações usam v2;
- migração v1→v2 por unwrap/wrap autenticado no provider;
- formato local legado permanece legível e selecionado apenas explicitamente.

## Limite de rotação com AAD

Na API OpenBao 2.5.x, `encrypt` e `decrypt` aceitam `associated_data`, mas o
endpoint nativo `rewrap` não documenta nem aceita esse campo. Como o AppSell usa
AAD obrigatória, este slice não afirma suporte a `rewrap` nativo. A migração
testada abre a DEK pelo provider e a envolve novamente na versão ativa, zerando
o buffer intermediário em `finally`.

Antes de avançar `min_decryption_version`, um processo de produção deverá
inventariar todas as versões persistidas, migrar os envelopes e provar que não
resta ciphertext N-1. Esse processo ainda não existe.

## Bloqueios de produção que permanecem

- TLS e autenticação de workload para o BFF;
- cluster HA/Raft, unseal e política operacional;
- backup/restore e teste de desastre;
- reconciliação durável do callback Identity após consumo do code;
- inventário/read-repair de envelopes antes de retirar versões antigas;
- staging isolado com observabilidade, SLO e rollback comprovados.

Rollback Local/CI: reverter a Draft PR e selecionar
`APPSELL_VAULT_PROVIDER=local`. Os envelopes locais anteriores continuam no
mesmo formato. A Issue #19 permanece aberta até os itens de produção acima.
