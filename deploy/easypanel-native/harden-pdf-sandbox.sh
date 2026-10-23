#!/usr/bin/env bash
# Endurece o serviço sell-autonomia_pdf-sandbox no Swarm (#468).
#
# No Linux o parser de PDF roda sem sandbox do sistema; o contêiner é o único
# isolamento. A leitura de 23/10/2026 achou ReadOnly=false, CapabilityDrop=null,
# PIDs sem limite e a task no `bridge` padrão (masquerade ligado): saída para a
# internet e para o host por 172.17.0.1. Este script aplica, num update só:
# root read-only, cap-drop ALL, /tmp em tmpfs de 2 GiB, no máximo 64
# processos e a rede overlay `--internal` abaixo, que tira o bridge.
#
# Por que overlay internal e não `none`: o Swarm recusa `--network none`
# ("network none not found"). Provado no runner com Swarm de verdade (branch
# prova/468-swarm-rede). A api fala com o sandbox pelo socket do volume, que
# não depende de rede.
#
# O que o Swarm não deixa aplicar, e por isso fica de fora: `no-new-privileges`
# (não existe em `docker service`) e `noexec,nosuid` no tmpfs (só `tmpfs-size` e
# `tmpfs-mode`). O compose tem os dois.
#
# Uso:
#   harden-pdf-sandbox.sh                       # seco: confere e imprime o comando
#   harden-pdf-sandbox.sh --aplicar             # só com OK do Rodrigo
#   harden-pdf-sandbox.sh --rollback <backup>   # volta à spec do backup
#
# Nunca mexe em env nem imagem. A ordem é sempre stop-first: start-first no
# pdf-sandbox já derrubou a produção (incidente ee194bf). Os nomes vêm do
# ambiente só para o ensaio no Swarm do CI; os padrões são os de produção.
set -euo pipefail

service="${PDF_SANDBOX_SERVICE:-sell-autonomia_pdf-sandbox}"
expected_image="${PDF_SANDBOX_IMAGE:-easypanel/sell-autonomia/pdf-sandbox:d5e00ec}"
rede="${PDF_SANDBOX_NETWORK:-sell-autonomia_pdf-sandbox-isolada}"
cliente="${PDF_SANDBOX_CLIENT:-sell-autonomia_api}"
tmp_bytes="${PDF_SANDBOX_TMP_BYTES:-2147483648}"
pids=64
# O tmpfs conta como memória do contêiner: 2 GiB de /tmp mais o pior trabalho
# de PDF (~1.170 MiB, pdf_derivatives.py) pedem pelo menos 3 GiB de limite.
memoria_minima=3221225472
prazo_segundos=180
verdes_seguidos=3
mode="${1:-seco}"

fmt() { sudo -n docker service inspect "$service" --format "$1"; }

# Impressão digital de uma spec: o hash de cada campo que este script ou um
# deploy mudaria. Não dá para comparar o JSON inteiro: o `inspect` de `.Spec`
# acrescenta padrões (StopGracePeriod, RestartPolicy) que a `.PreviousSpec`
# guardada não tem (provado no Swarm do CI). Só hashes: a env nunca aparece.
campos=(Labels TaskTemplate.ContainerSpec.Image TaskTemplate.ContainerSpec.Args
  TaskTemplate.ContainerSpec.Env TaskTemplate.ContainerSpec.Mounts
  TaskTemplate.ContainerSpec.ReadOnly TaskTemplate.ContainerSpec.CapabilityDrop
  TaskTemplate.ContainerSpec.User TaskTemplate.Resources TaskTemplate.Networks)
digital() {
  # digital Spec|PreviousSpec → uma linha "campo hash" por campo
  local qual="$1" campo
  for campo in "${campos[@]}"; do
    printf '%s %s\n' "$campo" "$(fmt "{{with .${qual}}}{{json .${campo}}}{{end}}" | sha256sum | cut -d' ' -f1)"
  done
}
diferencas() {
  # diferencas <arquivo-a> <arquivo-b> → nomes dos campos que diferem
  join <(sort "$1") <(sort "$2") | while read -r campo a b; do
    [ "$a" = "$b" ] || printf '%s ' "$campo"
  done
}

estado() {
  echo "image=$(fmt '{{.Spec.TaskTemplate.ContainerSpec.Image}}')"
  echo "ro=$(fmt '{{.Spec.TaskTemplate.ContainerSpec.ReadOnly}}')"
  echo "capdrop=$(fmt '{{json .Spec.TaskTemplate.ContainerSpec.CapabilityDrop}}')"
  echo "pids=$(fmt '{{.Spec.TaskTemplate.Resources.Limits.Pids}}')"
  echo "memoria=$(fmt '{{.Spec.TaskTemplate.Resources.Limits.MemoryBytes}}')"
  echo "order=$(fmt '{{.Spec.UpdateConfig.Order}}') rollback_order=$(fmt '{{.Spec.RollbackConfig.Order}}')"
  echo "mounts=$(fmt '{{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{.Type}}:{{.Target}} {{end}}')"
  echo "redes=$(fmt '{{range .Spec.TaskTemplate.Networks}}{{.Target}} {{end}}')"
  echo "env_count=$(fmt '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
}

contem() {
  # contem "<lista separada por espaço>" <item>
  case " $1 " in *" $2 "*) return 0 ;; esac
  return 1
}

container_do() {
  sudo -n docker ps -q --filter "name=${1}." | head -1
}

# O sandbox responde /health pelo socket. Pergunta direto a ele, de dentro do
# contêiner cliente, sem passar pelo /health/ready da api: aquele tem cache de
# 30 s e cai junto com banco ou storage, que não têm nada com este update.
sandbox_responde() {
  local cid
  cid="$(container_do "$cliente")"
  test -n "$cid" || return 1
  sudo -n docker exec "$cid" python -I -c '
import json, socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect("/run/appsell-pdf/pdf.sock")
s.sendall(b"GET /health HTTP/1.0\r\nHost: pdf-sandbox\r\n\r\n")
dados = b""
while True:
    parte = s.recv(4096)
    if not parte:
        break
    dados += parte
cabeca, _, corpo = dados.partition(b"\r\n\r\n")
sys.exit(0 if cabeca.split(b" ", 2)[1] == b"200" and json.loads(corpo) == {"status": "ok"} else 1)
' >/dev/null 2>&1
}

tasks_running() {
  local total=0 estado_task
  while read -r estado_task _; do
    [ "$estado_task" = "Running" ] && total=$((total + 1))
  done < <(sudo -n docker service ps "$service" --filter desired-state=running --format '{{.CurrentState}}')
  echo "$total"
}

# Verde só com 1 task Running e o sandbox respondendo N vezes seguidas, 10 s
# entre elas, dentro do prazo medido no relógio.
convergiu() {
  local fim seguidos=0
  fim=$(($(date +%s) + prazo_segundos))
  while [ "$(date +%s)" -lt "$fim" ]; do
    if [ "$(tasks_running)" = "1" ] && sandbox_responde; then
      seguidos=$((seguidos + 1))
      [ "$seguidos" -ge "$verdes_seguidos" ] && return 0
    else
      seguidos=0
    fi
    sleep 10
  done
  return 1
}

# Confere no contêiner que subiu, não só na spec: a troca de rede pode não ter
# pegado e o socket continuar verde.
container_endurecido() {
  local cid redes
  cid="$(container_do "$service")"
  test -n "$cid" || { echo "sem contêiner do $service"; return 1; }
  redes="$(sudo -n docker inspect "$cid" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}')"
  echo "contêiner: redes=$redes"
  [ "$redes" = "$rede " ] || return 1
  [ "$(sudo -n docker inspect "$cid" --format '{{.HostConfig.ReadonlyRootfs}}')" = "true" ] || { echo "root gravável"; return 1; }
  contem "$(sudo -n docker inspect "$cid" --format '{{range .HostConfig.CapDrop}}{{.}} {{end}}')" ALL || { echo "sem cap-drop ALL"; return 1; }
  [ "$(sudo -n docker inspect "$cid" --format '{{.HostConfig.PidsLimit}}')" = "$pids" ] || { echo "pids diferente de $pids"; return 1; }
  contem "$(sudo -n docker inspect "$cid" --format '{{range .Mounts}}{{.Type}}:{{.Destination}} {{end}}')" tmpfs:/tmp || { echo "sem tmpfs em /tmp"; return 1; }
}

# Volta à spec do backup, e só se for seguro. Se o update nem foi aceito, a
# spec continua igual ao backup e a PreviousSpec é a do deploy anterior: um
# rollback ali voltaria para uma spec velha.
restaurar() {
  local backup="$1" digital_backup="${1%.json}.digital" agora anterior dif
  test -s "$digital_backup" || { echo "ABORT: falta $digital_backup" >&2; return 1; }
  agora="$(mktemp)"; anterior="$(mktemp)"
  digital Spec > "$agora"
  if [ -z "$(diferencas "$digital_backup" "$agora")" ]; then
    rm -f -- "$agora" "$anterior"
    echo "spec já igual ao backup (update não aceito ou já restaurado): nada a desfazer"
    return 0
  fi
  digital PreviousSpec > "$anterior"
  dif="$(diferencas "$digital_backup" "$anterior")"
  if [ -n "$dif" ]; then
    rm -f -- "$agora" "$anterior"
    echo "ABORT: a spec anterior do Swarm difere do backup em: $dif; rollback manual com o Rodrigo" >&2
    return 1
  fi
  [ "$(fmt '{{.Spec.RollbackConfig.Order}}')" = "stop-first" ] || { echo "ABORT: rollback order não é stop-first" >&2; return 1; }
  sudo -n docker service rollback --detach=false "$service" || true
  digital Spec > "$agora"
  dif="$(diferencas "$digital_backup" "$agora")"
  rm -f -- "$agora" "$anterior"
  if [ -n "$dif" ]; then
    echo "ABORT: depois do rollback a spec difere do backup em: $dif; escalar ao Rodrigo" >&2
    return 1
  fi
  echo "spec restaurada: igual ao backup"
  convergiu || { echo "ABORT: restaurado, mas o sandbox não respondeu; escalar ao Rodrigo" >&2; return 1; }
  echo "rollback concluído"
}

conferir() {
  local image memoria
  estado
  image="$(fmt '{{.Spec.TaskTemplate.ContainerSpec.Image}}')"
  [ "$image" = "$expected_image" ] || { echo "ABORT: imagem $image, esperado $expected_image" >&2; exit 1; }
  [ "$(fmt '{{.Spec.UpdateConfig.Order}}')" = "stop-first" ] || { echo "ABORT: update order não é stop-first" >&2; exit 1; }
  [ "$(fmt '{{.Spec.RollbackConfig.Order}}')" = "stop-first" ] || { echo "ABORT: rollback order não é stop-first" >&2; exit 1; }
  if contem "$(fmt '{{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{.Target}} {{end}}')" /tmp; then
    echo "ABORT: já existe mount em /tmp; revisar antes" >&2; exit 1
  fi
  memoria="$(fmt '{{.Spec.TaskTemplate.Resources.Limits.MemoryBytes}}')"
  if [ "$memoria" = "0" ] || [ "$memoria" -lt "$memoria_minima" ]; then
    echo "ABORT: limite de memória $memoria; o tmpfs de /tmp pede pelo menos $memoria_minima" >&2; exit 1
  fi
  if sudo -n docker network inspect "$rede" >/dev/null 2>&1; then
    [ "$(sudo -n docker network inspect "$rede" --format '{{.Driver}} {{.Internal}} {{.Scope}}')" = "overlay true swarm" ] \
      || { echo "ABORT: $rede existe e não é overlay internal de swarm" >&2; exit 1; }
    echo "rede $rede: já existe, overlay internal"
  else
    echo "rede $rede: será criada (overlay --internal)"
  fi
}

case "$mode" in
  seco|--aplicar)
    conferir
    comando=(sudo -n docker service update --detach=false --update-order stop-first
      --read-only --cap-drop ALL --limit-pids "$pids"
      --mount-add "type=tmpfs,destination=/tmp,tmpfs-size=${tmp_bytes},tmpfs-mode=1777"
      --network-add "$rede"
      "$service")
    printf 'comando:'; printf ' %q' "${comando[@]}"; echo
    [ "$mode" = "--aplicar" ] || { echo "seco: nada aplicado"; exit 0; }

    backup="/tmp/pdf-sandbox_spec_$(date -u +%Y%m%dT%H%M%SZ).json"
    # A spec tem as variáveis de ambiente: o arquivo nasce 0600, do usuário que
    # roda o script (o redirect fica fora do sudo de propósito). Apagar depois
    # da conferência de 24 h, pelo nome exato.
    # shellcheck disable=SC2024
    (umask 077 && sudo -n docker service inspect "$service" --format '{{json .Spec}}' > "$backup")
    test -s "$backup" || { echo "ABORT: backup vazio" >&2; exit 1; }
    (umask 077 && digital Spec > "${backup%.json}.digital")
    echo "backup: $backup (e ${backup%.json}.digital)"

    if ! sudo -n docker network inspect "$rede" >/dev/null 2>&1; then
      sudo -n docker network create --driver overlay --internal "$rede" >/dev/null
      [ "$(sudo -n docker network inspect "$rede" --format '{{.Driver}} {{.Internal}}')" = "overlay true" ] \
        || { echo "ABORT: $rede criada sem internal" >&2; exit 1; }
    fi

    # Daqui em diante, qualquer saída que não seja o fim verde restaura: falha
    # do próprio update (set -e), conferência que não passa, ou interrupção.
    concluido=0
    ao_sair() {
      local codigo=$?
      [ "$concluido" = "1" ] && return
      echo "aplicação não terminou verde (código $codigo): restaurando" >&2
      restaurar "$backup" || true
      exit 1
    }
    trap ao_sair EXIT
    trap 'exit 130' INT TERM

    env_antes="$(fmt '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
    "${comando[@]}"
    env_depois="$(fmt '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')"
    [ "$env_antes" = "$env_depois" ] || { echo "ABORT: env $env_antes -> $env_depois" >&2; exit 1; }
    convergiu || { echo "ABORT: sandbox não respondeu $verdes_seguidos vezes seguidas em ${prazo_segundos}s" >&2; exit 1; }
    container_endurecido || { echo "ABORT: contêiner não ficou endurecido" >&2; exit 1; }
    estado
    concluido=1
    echo "aplicado; conferir na loja: PDF novo numa aula de teste e leitura pelo comprador"
    ;;
  --rollback)
    backup="${2:?uso: --rollback <backup.json>}"
    test -s "$backup" || { echo "backup ausente: $backup" >&2; exit 1; }
    # O .digital ao lado do backup é o que compara; o .json é para o humano.
    restaurar "$backup"
    # A rede interna fica: sem serviço ligado ela não faz nada. Removê-la é
    # `docker network rm sell-autonomia_pdf-sandbox-isolada`, com OK.
    ;;
  *)
    echo "uso: $0 [--aplicar | --rollback <backup.json>]" >&2
    exit 2
    ;;
esac
