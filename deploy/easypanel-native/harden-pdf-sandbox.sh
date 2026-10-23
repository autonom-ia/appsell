#!/usr/bin/env bash
# Endurece o serviço sell-autonomia_pdf-sandbox no Swarm (#468).
#
# No Linux o parser de PDF roda sem sandbox do sistema; o contêiner é o único
# isolamento. A leitura de 23/10/2026 achou ReadOnly=false, CapabilityDrop=null
# e PIDs sem limite. Este script aplica o que o compose já tem e o CI prova
# (scripts/test-pdf-sandbox-isolation.sh): root read-only, cap-drop ALL,
# /tmp em tmpfs de 2 GiB e no máximo 64 processos.
#
# Rede: fora deste script. O serviço não declara rede; qual rede a task usa de
# fato depende de uma leitura do contêiner ainda não autorizada. Ver #468.
#
# Uso:
#   harden-pdf-sandbox.sh            # seco: confere o estado e imprime o comando
#   harden-pdf-sandbox.sh --aplicar  # só com OK do Rodrigo
#   harden-pdf-sandbox.sh --rollback   # volta à spec anterior (PreviousSpec)
#
# Nunca mexe em env, imagem ou rede. A ordem é sempre stop-first: start-first
# no pdf-sandbox já derrubou a produção (incidente ee194bf).
set -euo pipefail

service="sell-autonomia_pdf-sandbox"
expected_image="easypanel/sell-autonomia/pdf-sandbox:d5e00ec"
tmp_bytes=2147483648
pids=64
mode="${1:-seco}"

fmt() { sudo -n docker service inspect "$service" --format "$1"; }

estado() {
  echo "image=$(fmt '{{.Spec.TaskTemplate.ContainerSpec.Image}}')"
  echo "ro=$(fmt '{{.Spec.TaskTemplate.ContainerSpec.ReadOnly}}')"
  echo "capdrop=$(fmt '{{json .Spec.TaskTemplate.ContainerSpec.CapabilityDrop}}')"
  echo "pids=$(fmt '{{.Spec.TaskTemplate.Resources.Limits.Pids}}')"
  echo "order=$(fmt '{{.Spec.UpdateConfig.Order}}')"
  echo "mounts=$(fmt '{{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{.Type}}:{{.Target}} {{end}}')"
  echo "env_count=$(fmt '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
}

saudavel() {
  # O healthz da api diz se ela alcança o pdf-sandbox pelo socket.
  local api
  api="$(sudo -n docker ps -q --filter name=sell-autonomia_api. | head -1)"
  test -n "$api" || return 1
  sudo -n docker exec "$api" python -c \
    "import urllib.request,json; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health/ready',timeout=5)); assert r.get('pdf_sandbox') is True, r"
}

tasks_running() {
  local total=0 estado_task
  while read -r estado_task _; do
    [ "$estado_task" = "Running" ] && total=$((total + 1))
  done < <(sudo -n docker service ps "$service" --filter desired-state=running --format '{{.CurrentState}}')
  echo "$total"
}

convergiu() {
  local _
  for _ in $(seq 1 36); do
    if [ "$(tasks_running)" = "1" ] && saudavel; then
      return 0
    fi
    sleep 5
  done
  return 1
}

case "$mode" in
  seco|--aplicar)
    antes="$(estado)"
    echo "$antes"
    image="$(fmt '{{.Spec.TaskTemplate.ContainerSpec.Image}}')"
    [ "$image" = "$expected_image" ] || { echo "ABORT: imagem $image, esperado $expected_image" >&2; exit 1; }
    [ "$(fmt '{{.Spec.UpdateConfig.Order}}')" = "stop-first" ] || { echo "ABORT: update order não é stop-first" >&2; exit 1; }
    case " $(fmt '{{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{.Target}} {{end}}') " in
      *" /tmp "*) echo "ABORT: já existe mount em /tmp; revisar antes" >&2; exit 1 ;;
    esac
    [ "$(fmt '{{.Spec.RollbackConfig.Order}}')" = "stop-first" ] || { echo "ABORT: rollback order não é stop-first" >&2; exit 1; }
    comando=(sudo -n docker service update --detach=false --update-order stop-first
      --read-only --cap-drop ALL --limit-pids "$pids"
      --mount-add "type=tmpfs,destination=/tmp,tmpfs-size=${tmp_bytes},tmpfs-mode=1777"
      "$service")
    printf 'comando:'; printf ' %q' "${comando[@]}"; echo
    [ "$mode" = "--aplicar" ] || { echo "seco: nada aplicado"; exit 0; }

    backup="/tmp/pdf-sandbox_spec_$(date -u +%Y%m%dT%H%M%SZ).json"
    # A spec tem as variáveis de ambiente: o arquivo nasce 0600, do usuário que
    # roda o script (o redirect fica fora do sudo de propósito).
    # shellcheck disable=SC2024
    (umask 077 && sudo -n docker service inspect "$service" --format '{{json .Spec}}' > "$backup")
    echo "backup: $backup"
    env_antes="$(fmt '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
    "${comando[@]}"
    env_depois="$(fmt '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
    if [ "$env_antes" != "$env_depois" ] || ! convergiu; then
      echo "ABORT: env $env_antes -> $env_depois ou serviço sem saúde; rollback" >&2
      "$0" --rollback
      exit 1
    fi
    estado
    echo "aplicado; conferir na loja: PDF novo numa aula de teste e leitura pelo comprador"
    ;;
  --rollback)
    # O Swarm guarda a spec anterior; o RollbackConfig do serviço é
    # stop-first (conferido acima antes de aplicar). O backup em /tmp é a
    # prova do estado de antes, não o caminho de volta.
    [ "$(fmt '{{.Spec.RollbackConfig.Order}}')" = "stop-first" ] || { echo "ABORT: rollback order não é stop-first" >&2; exit 1; }
    sudo -n docker service rollback --detach=false "$service"
    estado
    convergiu || { echo "rollback sem saúde: escalar ao Rodrigo" >&2; exit 1; }
    echo "rollback concluído"
    ;;
  *)
    echo "uso: $0 [--aplicar | --rollback]" >&2
    exit 2
    ;;
esac
