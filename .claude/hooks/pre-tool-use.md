# Hook: PreToolUse

Executa `pre-tool-use.sh` antes de cada tool call.

Comportamento depende de `enforcement_mode` em `platform-manifest.json`:
- `warn` (default): bloqueia apenas patterns catastróficos; resto emite stderr e exit 0
- `strict`: bloqueia patterns adicionais

Catastrophic (sempre bloqueia, qualquer modo):
- `rm -rf /` (root delete)
- `DROP DATABASE`
- `destroyService` (Easypanel destroy)

Strict-only block (bloqueia se modo=strict; warning se modo=warn):
- DROP TABLE, TRUNCATE, deleteService, push --force, reset --hard, clean -fd, wipe, purge

Warning-only (nunca bloqueia, informacional):
- production, .env, secret, credential, token, password, billing, ALTER TABLE, migrate
