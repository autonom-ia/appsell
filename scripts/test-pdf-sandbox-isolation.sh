#!/usr/bin/env bash
# Prova, no Linux, que o contêiner pdf-sandbox do compose nega rede e escrita
# fora do /tmp (#468). No Linux o parser de PDF roda sem sandbox do sistema, e
# esse contêiner é o único isolamento.
#
# Uso: test-pdf-sandbox-isolation.sh <projeto-do-compose>, com o pdf-sandbox
# já de pé. Prova a spec do compose; a de produção (Swarm) é lida no host.
set -euo pipefail

project="${1:?uso: test-pdf-sandbox-isolation.sh <projeto-do-compose>}"
probe="$(cat "$(dirname "$0")/ci/pdf_sandbox_probe.py")"

echo "pdf-sandbox: sonda dentro do contêiner do compose"
docker compose -p "$project" exec -T pdf-sandbox python -I -c "$probe"

# Controle negativo: a mesma imagem, com rede e sem read-only, precisa ser
# acusada. Sem isto, uma sonda que nunca acha nada passaria verde.
container="$(docker compose -p "$project" ps -q pdf-sandbox)"
image="$(docker inspect --format '{{.Image}}' "$container")"
echo "pdf-sandbox: controle negativo com rede e root gravável"
if docker run --rm --network bridge --user 10001:10001 --entrypoint python "$image" -I -c "$probe"; then
  echo "a sonda não acusou um contêiner com rede: ela não prova nada" >&2
  exit 1
fi
echo "pdf-sandbox: controle negativo acusado, como esperado"
