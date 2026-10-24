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
#   harden-pdf-sandbox.sh --provar              # só as duas provas abaixo, sem update
#   harden-pdf-sandbox.sh --aplicar             # só com OK do Rodrigo
#   harden-pdf-sandbox.sh --rollback <backup>   # volta à spec do backup
#
# --provar e --aplicar pedem PDF_SANDBOX_PROVA_PDF: o caminho do
# output/pdf/codex/01-o-que-e-codex.pdf do repositório, levado ao host com
# `git show <sha>:output/pdf/codex/01-o-que-e-codex.pdf`. O sha256 dele está
# fixo abaixo; outro arquivo é recusado antes de qualquer coisa.
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
trabalhador="${PDF_SANDBOX_WORKER:-sell-autonomia_asset-worker}"
prova_pdf="${PDF_SANDBOX_PROVA_PDF:-}"
prova_sha256=1a182ac49637d0b6ae9bf105fd8eccdfd651d75e920aaa2d65869b826878f887
prova_paginas=1
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

# Prova (a): o worker manda um PDF conhecido ao sandbox pelo mesmo cliente que
# usa nos uploads (prepared_pdf_file, o mesmo de storage_worker.py) e recebe as
# páginas de volta. Não toca banco, storage nem produto: o PDF vai pelo stdin,
# fica no /tmp do worker (tmpfs) e é apagado no fim.
#
# Como todo trabalho de PDF, ela recicla o sandbox: com
# APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB=true o processo sai com 77 depois de
# responder (pdf_sandbox_service.py), e o Swarm sobe uma task nova
# (RestartPolicy on-failure). Por isso quem chama espera convergir de novo.
prova_pdf_pelo_worker() {
  local cid saida erro
  test -n "$prova_pdf" || { echo "falta PDF_SANDBOX_PROVA_PDF"; return 1; }
  test -f "$prova_pdf" || { echo "PDF de prova ausente: $prova_pdf"; return 1; }
  [ "$(sha256sum "$prova_pdf" | cut -d' ' -f1)" = "$prova_sha256" ] \
    || { echo "PDF de prova não é o do repositório"; return 1; }
  cid="$(container_do "$trabalhador")"
  test -n "$cid" || { echo "sem contêiner do $trabalhador"; return 1; }
  erro="$(mktemp)"
  # O PDF é lido pelo usuário que roda o script; o sudo é só do docker.
  # `-P` e não `-I`: o `-I` ignora o PYTHONPATH da imagem, e é por ele que o
  # worker acha o appsell_api (o pacote não é instalado no .venv). O `-P` só
  # tira o diretório atual do sys.path. A prova roda com o ambiente do worker.
  # shellcheck disable=SC2024
  if ! saida="$(sudo -n docker exec -i "$cid" python -P -c '
import json, shutil, sys, tempfile
from pathlib import Path
from appsell_api.pdf_derivatives import prepared_pdf_file
pasta = Path(tempfile.mkdtemp(prefix="prova-478-"))
try:
    origem = pasta / "prova.pdf"
    origem.write_bytes(sys.stdin.buffer.read())
    with prepared_pdf_file(origem) as arquivo:
        print("prova-478 " + json.dumps({"page_count": arquivo.page_count, "pages": len(arquivo.pages),
                          "source_sha256": arquivo.source_sha256_hex}, sort_keys=True), flush=True)
finally:
    shutil.rmtree(pasta, ignore_errors=True)
' < "$prova_pdf" 2>"$erro")"; then
    echo "prova pelo worker: o worker não processou o PDF: $(tail -c 300 "$erro")"
    rm -f -- "$erro"
    return 1
  fi
  rm -f -- "$erro"
  # Só a linha marcada é o resultado: um log no stdout não a confunde.
  local linha resultado=""
  while IFS= read -r linha; do
    case "$linha" in "prova-478 "*) resultado="${linha#prova-478 }" ;; esac
  done <<<"$saida"
  saida="$resultado"
  test -n "$saida" || { echo "prova pelo worker: sem a linha de resultado"; return 1; }
  echo "prova pelo worker: $saida"
  python3 -I -c '
import json, sys
r = json.loads(sys.argv[1])
sys.exit(0 if r["page_count"] == int(sys.argv[3]) and r["pages"] >= 1 and r["source_sha256"] == sys.argv[2] else 1)
' "$saida" "$prova_sha256" "$prova_paginas" || { echo "prova pelo worker: resultado diferente do esperado ($prova_paginas página, $prova_sha256)"; return 1; }
}

# Prova (b): de dentro do sandbox, nenhuma saída para fora nem para o host, e o
# socket interno responde. Só ENETUNREACH ou EHOSTUNREACH contam como sem
# saída: recusa (ECONNREFUSED) quer dizer que o pacote chegou ao destino, e
# timeout quer dizer que há rota.
prova_sandbox_isolado() {
  local cid
  cid="$(container_do "$service")"
  test -n "$cid" || { echo "sem contêiner do $service"; return 1; }
  sudo -n docker exec "$cid" python -I -c '
import errno, json, socket, sys
falhas = []
for alvo in (("1.1.1.1", 443), ("8.8.8.8", 53), ("172.17.0.1", 80)):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect(alvo)
        falhas.append("%s:%d conectou" % alvo)
    except socket.timeout:
        falhas.append("%s:%d timeout (há rota)" % alvo)
    except OSError as falha:
        if falha.errno not in (errno.ENETUNREACH, errno.EHOSTUNREACH):
            falhas.append("%s:%d %s" % (alvo[0], alvo[1], errno.errorcode.get(falha.errno, falha.errno)))
    finally:
        s.close()
try:
    u = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    u.settimeout(5)
    u.connect("/run/appsell-pdf/pdf.sock")
    u.sendall(b"GET /health HTTP/1.0\r\nHost: pdf-sandbox\r\n\r\n")
    dados = b""
    while True:
        parte = u.recv(4096)
        if not parte:
            break
        dados += parte
    cabeca, _, corpo = dados.partition(b"\r\n\r\n")
    if cabeca.split(b" ", 2)[1] != b"200" or json.loads(corpo) != {"status": "ok"}:
        falhas.append("socket interno sem 200 ok")
except (OSError, ValueError, IndexError) as falha:
    falhas.append("socket interno: %s" % falha)
print("prova do isolamento: " + ("; ".join(falhas) if falhas else "sem saída para fora e para o host, socket 200 ok"))
sys.exit(1 if falhas else 0)
'
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
  --provar)
    # O isolamento antes do PDF: a prova do PDF recicla a task do sandbox.
    falhou=0
    prova_sandbox_isolado || falhou=1
    prova_pdf_pelo_worker || falhou=1
    convergiu || { echo "o sandbox não voltou depois da reciclagem" >&2; falhou=1; }
    exit "$falhou"
    ;;
  seco|--aplicar)
    conferir
    comando=(sudo -n docker service update --detach=false --update-order stop-first
      --read-only --cap-drop ALL --limit-pids "$pids"
      --mount-add "type=tmpfs,destination=/tmp,tmpfs-size=${tmp_bytes},tmpfs-mode=1777"
      --network-add "$rede"
      "$service")
    printf 'comando:'; printf ' %q' "${comando[@]}"; echo
    [ "$mode" = "--aplicar" ] || { echo "seco: nada aplicado"; exit 0; }

    # Linha de base, antes de qualquer mudança: se o PDF já não passa agora, o
    # problema é anterior ao isolamento e nada é aplicado.
    prova_pdf_pelo_worker || { echo "ABORT: a prova pelo worker já falha antes do isolamento; problema anterior, nada aplicado" >&2; exit 1; }
    convergiu || { echo "ABORT: o sandbox não voltou da reciclagem da linha de base; nada aplicado" >&2; exit 1; }

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
    prova_sandbox_isolado || { echo "ABORT: o sandbox não ficou isolado" >&2; exit 1; }
    prova_pdf_pelo_worker || { echo "ABORT: o worker deixou de processar PDF pelo sandbox" >&2; exit 1; }
    # A prova do PDF reciclou a task: a nova também tem de subir endurecida.
    convergiu || { echo "ABORT: o sandbox não voltou da reciclagem depois do isolamento" >&2; exit 1; }
    container_endurecido || { echo "ABORT: a task reciclada não ficou endurecida" >&2; exit 1; }
    estado
    concluido=1
    echo "aplicado; depois, só observar no log do $trabalhador o próximo PDF de usuário (asset_worker_storage_retry com PdfSandboxUnavailable é falha)"
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
    echo "uso: $0 [--provar | --aplicar | --rollback <backup.json>]" >&2
    exit 2
    ;;
esac
