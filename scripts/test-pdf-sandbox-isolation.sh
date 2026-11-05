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

# Controle negativo: a mesma imagem, com rede, root gravável e sem limites,
# precisa ser acusada com código 1 e exatamente estes ids. "Qualquer código
# diferente de 0" não basta: outras checagens sempre disparam aqui, e uma
# checagem de rede quebrada passaria despercebida. O root é o único id que não
# entra, porque o controle roda com o mesmo usuário 10001.
esperados="capbnd escrita memoria noexec nonewprivs pids rede tmp"
container="$(docker compose -p "$project" ps -q pdf-sandbox)"
image="$(docker inspect --format '{{.Image}}' "$container")"
echo "pdf-sandbox: controle negativo com rede, root gravável e sem limites"
set +e
saida="$(docker run --rm --network bridge --user 10001:10001 --entrypoint python "$image" -I -c "$probe")"
rc=$?
set -e
printf '%s\n' "$saida"
if [ "$rc" -ne 1 ]; then
  echo "controle negativo saiu com $rc, esperado 1 (2 é erro da sonda)" >&2
  exit 1
fi
linha="$(printf '%s\n' "$saida" | grep '^IDS: ' | tail -1)"
achados="$(printf '%s\n' "${linha#IDS: }" | tr ',' '\n' | sort | tr '\n' ' ')"
if [ "$achados" != "$esperados " ]; then
  echo "controle negativo acusou [$achados], esperado [$esperados]" >&2
  exit 1
fi
echo "pdf-sandbox: controle negativo acusado com os ids esperados"
