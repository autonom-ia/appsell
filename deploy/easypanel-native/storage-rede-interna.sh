#!/usr/bin/env bash
# Etapas 4 e 6 do docs/runbooks/storage-461-rede-interna.md (#461). Cada uma
# só com o OK do Rodrigo.
#
#   storage-rede-interna.sh seco                 # confere e não escreve nada
#   storage-rede-interna.sh rede                 # etapa 4: rede, alias, api e worker nela
#   storage-rede-interna.sh env                  # etapa 6: flag ligada e endpoint interno
#   storage-rede-interna.sh desfazer <backup-ts> # volta à spec dos backups desse ts
#
# Regra de cada escrita: backup da spec com a digital por campo, um update por
# serviço, e depois de cada um a conferência (task Running, nomes e contagem de
# env, storage respondendo). Qualquer saída que não seja o fim verde (falha de
# conferência, erro do `set -e`, Ctrl-C, SIGTERM, queda do SSH) volta os
# serviços já tocados ao backup, do último para o primeiro, e sai com 1 (130
# no sinal). A volta só usa a PreviousSpec do Swarm quando ela é igual ao
# backup; em qualquer outro caso, ABORT e Rodrigo, sem tocar no resto.
#
# Nunca imprime env: só nomes de variável, contagens e hashes.
set -euo pipefail

storage="sell-autonomia_storage"
api="sell-autonomia_api"
worker="sell-autonomia_asset-worker"
rede="sell-storage-internal"
alias_do_storage="appsell-storage"
endpoint_interno="http://${alias_do_storage}:9000"
flag="APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED"
chave_do_endpoint="APPSELL_API_STORAGE_INTERNAL_ENDPOINT"
# Os prazos vêm do ambiente só para o teste; os padrões são os da produção.
prazo_segundos="${STORAGE461_PRAZO_SEGUNDOS:-180}"
intervalo_segundos="${STORAGE461_INTERVALO_SEGUNDOS:-10}"
pausa_das_leituras="${STORAGE461_PAUSA_LEITURAS:-15}"
pasta_dos_backups="${STORAGE461_PASTA:-/tmp}"

for ferramenta in jq sha256sum join comm; do
  command -v "$ferramenta" >/dev/null || { echo "ABORT: falta $ferramenta neste host" >&2; exit 1; }
done

d() { sudo -n docker "$@"; }
# `sleep` em segundo plano: o `wait` deixa o trap rodar já no sinal, sem
# esperar o fim da pausa.
pausa() { sleep "$1" & wait "$!"; }
spec_json() { d service inspect "$1" --format "{{json .${2}}}"; }
abort() { echo "ABORT: $*" >&2; exit 1; }

# Campos que um update desta janela muda ou que um deploy alheio mudaria.
campos=(Labels TaskTemplate.ContainerSpec.Image TaskTemplate.ContainerSpec.Env
  TaskTemplate.ContainerSpec.Mounts TaskTemplate.Networks TaskTemplate.Resources
  UpdateConfig RollbackConfig)

digital() {
  # digital <serviço> Spec|PreviousSpec → "campo hash" por linha; só hashes
  # O `with` evita o erro de ponteiro nulo quando não há PreviousSpec.
  # Inspect que falha é erro, não hash de saída vazia.
  local servico="$1" qual="$2" campo valor
  for campo in "${campos[@]}"; do
    valor="$(d service inspect "$servico" --format "{{with .${qual}}}{{json .${campo}}}{{end}}")" || return 1
    printf '%s %s\n' "$campo" "$(printf '%s' "$valor" | sha256sum | cut -d' ' -f1)"
  done
}

diferencas() {
  join <(sort "$1") <(sort "$2") | while read -r campo a b; do
    [ "$a" = "$b" ] || printf '%s ' "$campo"
  done
}

contagem_de_env() { spec_json "$1" Spec.TaskTemplate.ContainerSpec.Env | jq 'length'; }

nomes_de_env() {
  spec_json "$1" Spec.TaskTemplate.ContainerSpec.Env | jq -r '.[] | split("=")[0]' | sort
}

redes_do_servico() { spec_json "$1" Spec.TaskTemplate.Networks | jq -r '(. // [])[].Target'; }

id_da_rede() { d network inspect "$rede" --format '{{json .Id}}' | jq -r '.'; }

rede_isolada() {
  # A rede precisa existir e ser overlay, interna e do Swarm. Nada entra nela antes disso.
  local forma
  forma="$(d network inspect "$rede" --format '{{json .Driver}} {{json .Internal}} {{json .Scope}}')" || return 1
  [ "$forma" = '"overlay" true "swarm"' ]
}

task_running() {
  local total=0 estado_da_task
  while read -r estado_da_task _; do
    [ "$estado_da_task" = "Running" ] && total=$((total + 1))
  done < <(d service ps "$1" --filter desired-state=running --format '{{.CurrentState}}')
  [ "$total" = "1" ]
}

container_do() {
  local task
  task="$(d service ps -q "$1" --filter desired-state=running | head -1)"
  test -n "$task" || return 1
  d inspect "$task" --format '{{json .Status.ContainerStatus.ContainerID}}' | jq -r '.'
}

# HEAD assinado nos dois buckets, de dentro do contêiner, com o endpoint que
# ele tem agora. Sem o cache do /health/ready.
storage_responde() {
  local cid
  cid="$(container_do "$1")" || return 1
  d exec "$cid" python -c '
from appsell_api.config import get_settings
from appsell_api.storage_s3 import S3Storage
S3Storage(get_settings()).probe_availability()' >/dev/null 2>&1
}

ready_com_storage() {
  local cid
  cid="$(container_do "$api")" || return 1
  d exec "$cid" python -c '
import json, sys, urllib.request
corpo = json.loads(urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=5).read())
sys.exit(0 if corpo.get("storage") is True else 1)' >/dev/null 2>&1
}

convergiu() {
  # convergiu <serviço> <checagem>: 1 task Running e a checagem verde, dentro do prazo
  local servico="$1" checagem="$2" fim
  # Confere ao menos uma vez, mesmo que o relógio vire o segundo no meio.
  fim=$(($(date +%s) + prazo_segundos))
  while :; do
    if task_running "$servico" && "$checagem" "$servico"; then
      return 0
    fi
    [ "$(date +%s)" -lt "$fim" ] || return 1
    pausa "$intervalo_segundos"
  done
}

storage_pela_api() { storage_responde "$api"; }

caminho_do_backup() { printf '%s/%s_spec_pre461_%s' "$pasta_dos_backups" "$1" "$2"; }

guardar_backup() {
  local servico="$1" ts="$2" base
  base="$(caminho_do_backup "$servico" "$ts")"
  (umask 077 && d service inspect "$servico" > "${base}.json" && digital "$servico" Spec > "${base}.digital")
  test -s "${base}.json" && test -s "${base}.digital" || abort "backup de $servico vazio"
}

restaurar() {
  # restaurar <serviço> <ts>: volta à spec do backup, e só se for seguro
  local servico="$1" ts="$2" backup agora anterior dif
  backup="$(caminho_do_backup "$servico" "$ts").digital"
  test -s "$backup" || { echo "ABORT: falta $backup" >&2; return 1; }
  agora="$(mktemp)"; anterior="$(mktemp)"
  digital "$servico" Spec > "$agora" || {
    rm -f -- "$agora" "$anterior"
    echo "ABORT: $servico: não li a spec atual; falar com o Rodrigo" >&2
    return 1
  }
  if [ -z "$(diferencas "$backup" "$agora")" ]; then
    rm -f -- "$agora" "$anterior"
    echo "$servico: spec já igual ao backup, nada a desfazer"
    return 0
  fi
  digital "$servico" PreviousSpec > "$anterior" || dif="(não li a PreviousSpec)"
  [ -n "${dif:-}" ] || dif="$(diferencas "$backup" "$anterior")"
  if [ -n "$dif" ]; then
    rm -f -- "$agora" "$anterior"
    echo "ABORT: $servico: a spec anterior do Swarm difere do backup em: $dif; falar com o Rodrigo" >&2
    return 1
  fi
  if [ "$servico" = "$storage" ]; then
    storage_sem_clientes_do_alias || { rm -f -- "$agora" "$anterior"; return 1; }
  fi
  if [ "$(spec_json "$servico" Spec.RollbackConfig.Order)" != '"stop-first"' ]; then
    rm -f -- "$agora" "$anterior"
    echo "ABORT: $servico: rollback order não é stop-first; falar com o Rodrigo" >&2
    return 1
  fi
  d service rollback --detach=false "$servico" >/dev/null || true
  dif="(não li a spec depois do rollback)"
  if digital "$servico" Spec > "$agora"; then
    dif="$(diferencas "$backup" "$agora")"
  fi
  rm -f -- "$agora" "$anterior"
  if [ -n "$dif" ]; then
    echo "ABORT: $servico: depois do rollback a spec difere do backup em: $dif; falar com o Rodrigo" >&2
    return 1
  fi
  task_running "$servico" || convergiu "$servico" true || {
    echo "ABORT: $servico restaurado, mas sem task Running; falar com o Rodrigo" >&2
    return 1
  }
  echo "$servico: restaurado, igual ao backup"
}

tocados=()
ts_da_janela=""
desfazer_tocados() {
  # Para no primeiro ABORT: restaurar o resto por cima de um serviço que não
  # voltou pode deixar a api apontando para um storage fora da rede.
  local i
  for ((i = ${#tocados[@]} - 1; i >= 0; i--)); do
    restaurar "${tocados[$i]}" "$ts_da_janela" || return 1
  done
}

concluido=0
ao_sair() {
  # Roda em toda saída depois de armado. Sem `set -e` aqui: um echo para um
  # SSH que caiu não pode interromper a restauração.
  local codigo=$?
  set +e
  # Um segundo Ctrl-C não corta a restauração no meio.
  trap - EXIT
  trap '' INT TERM HUP PIPE
  [ "$concluido" = "1" ] && exit 0
  if [ "${#tocados[@]}" -gt 0 ]; then
    echo "a etapa não terminou verde (código $codigo): restaurando" >&2
    desfazer_tocados || echo "ABORT: a restauração parou; falar com o Rodrigo" >&2
  fi
  [ "$codigo" = "130" ] && exit 130
  exit 1
}

armar_restauracao() {
  trap ao_sair EXIT
  trap 'exit 130' INT TERM HUP
}

falhar() {
  # falhar <motivo>: sai com 1; quem desfaz é o ao_sair
  echo "FALHOU: $*" >&2
  exit 1
}

preparar_janela() {
  sudo -n true || abort "sudo -n não liberado neste host"
  local nos
  nos="$(d node ls -q | wc -l | tr -d ' ')"
  [ "$nos" = "1" ] || abort "o Swarm tem $nos nós; a rede só nasce com --opt encrypted, ensaiada"
  local servico
  for servico in "$storage" "$api" "$worker"; do
    task_running "$servico" || abort "$servico sem task Running antes de começar"
  done
  # O rollback usa a ordem da spec atual. storage e api recebem stop-first no
  # próprio update desta janela; o worker já nasce assim (ensure-asset-worker.sh).
  [ "$(spec_json "$worker" Spec.RollbackConfig.Order)" = '"stop-first"' ] \
    || abort "$worker com rollback order diferente de stop-first"
  ts_da_janela="$(date -u +%Y%m%dT%H%M%SZ)"
  for servico in "$storage" "$api" "$worker"; do
    guardar_backup "$servico" "$ts_da_janela"
  done
  echo "backups: $(caminho_do_backup '<serviço>' "$ts_da_janela").json"
  armar_restauracao
}

esta_na_rede() {
  local id
  id="$(id_da_rede)" || return 1
  redes_do_servico "$1" | grep -Fxq -- "$id"
}

ordem_do() {
  # storage e api entram com stop-first nos dois sentidos: MinIO de instância
  # única não pode ter duas tasks no mesmo volume, e a api é uma task só com o
  # lease do e-mail. O worker mantém a ordem com que o script dele o cria.
  [ "$1" = "$worker" ] || printf '%s\n' --update-order stop-first --rollback-order stop-first
}

host_do_endpoint_interno() {
  # Só o hostname, nunca o valor inteiro.
  spec_json "$1" Spec.TaskTemplate.ContainerSpec.Env | jq -r --arg chave "${chave_do_endpoint}=" '
    [.[] | select(startswith($chave)) | ltrimstr($chave)
      | ltrimstr("http://") | ltrimstr("https://") | split("/")[0] | split(":")[0]] | first // ""'
}

storage_sem_clientes_do_alias() {
  # Voltar o storage tira o alias. Se a api ou o worker ainda usam o alias, o
  # storage some para eles e o /health/ready cai.
  local servico host
  for servico in "$api" "$worker"; do
    # Falha fechada: sem ler o endpoint, o storage não volta.
    host="$(host_do_endpoint_interno "$servico")" || {
      echo "ABORT: $storage não volta: não li o endpoint de $servico; falar com o Rodrigo" >&2
      return 1
    }
    if [ "$host" = "$alias_do_storage" ]; then
      echo "ABORT: $storage não volta: $servico ainda usa $alias_do_storage; desfazer antes a etapa 6; falar com o Rodrigo" >&2
      return 1
    fi
  done
}

atualizar() {
  # atualizar <serviço> <checagem> <variáveis novas> <args do update...>
  # Update, conferência e, na falha, volta de tudo o que esta execução tocou.
  local servico="$1" checagem="$2" novas="$3" antes depois nomes_antes sumiram
  shift 3
  antes="$(contagem_de_env "$servico")"
  nomes_antes="$(nomes_de_env "$servico")"
  tocados+=("$servico")
  d service update --detach=false "$@" "$servico" >/dev/null || falhar "update de $servico saiu com erro"
  convergiu "$servico" "$checagem" || falhar "$servico não convergiu ($checagem)"
  depois="$(contagem_de_env "$servico")"
  echo "$servico: env $antes → $depois"
  [ "$depois" = "$((antes + novas))" ] || falhar "$servico: env $antes → $depois, esperado $((antes + novas))"
  sumiram="$(comm -23 <(printf '%s\n' "$nomes_antes") <(nomes_de_env "$servico") | tr '\n' ' ')"
  [ -z "${sumiram// /}" ] || falhar "$servico: sumiram as variáveis $sumiram"
}

etapa_rede() {
  preparar_janela
  if ! d network inspect "$rede" >/dev/null 2>&1; then
    d network create --driver overlay --internal "$rede" >/dev/null || abort "não criou a rede $rede"
    echo "rede $rede criada"
  fi
  rede_isolada || abort "a rede $rede existe, mas não é overlay interna do Swarm; nada foi atualizado"

  if esta_na_rede "$storage"; then
    echo "$storage: já na rede"
  else
    atualizar "$storage" true 0 \
      --update-order stop-first --rollback-order stop-first \
      --network-add "name=${rede},alias=${alias_do_storage}"
    convergiu "$api" storage_pela_api || falhar "a api deixou de ver o storage depois do update dele"
  fi
  local servico ordem
  for servico in "$api" "$worker"; do
    if esta_na_rede "$servico"; then
      echo "$servico: já na rede"
      continue
    fi
    mapfile -t ordem < <(ordem_do "$servico")
    atualizar "$servico" storage_responde 0 ${ordem[@]+"${ordem[@]}"} --network-add "$rede"
  done
  concluido=1
  echo "etapa 4 concluída; backups com ts $ts_da_janela"
}

etapa_env() {
  preparar_janela
  rede_isolada || abort "a rede $rede não existe ou não é isolada: a etapa 4 não fechou"
  local servico novas ordem
  for servico in "$storage" "$api" "$worker"; do
    esta_na_rede "$servico" || abort "$servico fora da rede $rede: a etapa 4 não fechou"
  done
  # api primeiro: se ela não subir, o worker nem é tocado
  for servico in "$api" "$worker"; do
    novas=0
    nomes_de_env "$servico" | grep -Fxq -- "$flag" || novas=$((novas + 1))
    nomes_de_env "$servico" | grep -Fxq -- "$chave_do_endpoint" || novas=$((novas + 1))
    mapfile -t ordem < <(ordem_do "$servico")
    atualizar "$servico" storage_responde "$novas" ${ordem[@]+"${ordem[@]}"} \
      --env-add "${flag}=true" --env-add "${chave_do_endpoint}=${endpoint_interno}"
    nomes_de_env "$servico" | grep -Fxq -- "$flag" || falhar "$servico sem $flag depois do update"
  done
  local leitura
  for leitura in 1 2 3; do
    pausa "$pausa_das_leituras"
    ready_com_storage || falhar "/health/ready sem storage na leitura $leitura"
  done
  concluido=1
  echo "etapa 6 concluída; backups com ts $ts_da_janela"
}

desfazer() {
  local ts="${1:?uso: desfazer <ts-do-backup>}" servico
  # O ts vira parte de um caminho: só o formato que o próprio script imprime.
  case "$ts" in
    [0-9][0-9][0-9][0-9][0-1][0-9][0-3][0-9]T[0-2][0-9][0-5][0-9][0-5][0-9]Z) ;;
    *) abort "ts inválido: use o que a etapa imprimiu (AAAAMMDDTHHMMSSZ)" ;;
  esac
  sudo -n true || abort "sudo -n não liberado neste host"
  for servico in "$worker" "$api" "$storage"; do
    restaurar "$servico" "$ts" || abort "restauração parada em $servico; os seguintes não foram tocados; falar com o Rodrigo"
  done
}

seco() {
  local servico
  sudo -n true || abort "sudo -n não liberado neste host"
  echo "nós do Swarm: $(d node ls -q | wc -l | tr -d ' ')"
  if rede_isolada; then echo "rede $rede: overlay interna"; else echo "rede $rede: ausente ou não isolada"; fi
  for servico in "$storage" "$api" "$worker"; do
    echo "$servico: env=$(contagem_de_env "$servico") na_rede=$(esta_na_rede "$servico" && echo sim || echo não) running=$(task_running "$servico" && echo sim || echo não)"
  done
}

case "${1:-seco}" in
  seco) seco ;;
  rede) etapa_rede ;;
  env) etapa_env ;;
  desfazer) desfazer "${2:-}" ;;
  *) echo "uso: $0 seco|rede|env|desfazer <ts>" >&2; exit 2 ;;
esac
