#!/usr/bin/env bash
# Controle do storage pela rede do projeto (#461), pelo alias que o EasyPanel
# já cria. Ver docs/runbooks/storage-461-rede-interna.md; cada escrita só com o
# OK do Rodrigo.
#
#   storage-rede-interna.sh seco                 # confere e não escreve nada
#   storage-rede-interna.sh worker               # env do worker: flag e endpoint interno
#   storage-rede-interna.sh api-antes            # backup da api antes do painel (só /tmp)
#   storage-rede-interna.sh api-depois <ts>      # confere a api depois do painel
#   storage-rede-interna.sh desfazer <ts>        # volta à spec dos backups desse ts
#
# A env da api muda no painel do EasyPanel, pelo Rodrigo: este script só
# fotografa antes e confere depois. A do worker muda aqui.
#
# Regra da escrita: backup da spec com a digital por campo, um update, e a
# conferência (task Running, nomes e contagem de env, storage respondendo).
# Qualquer saída que não seja o fim verde (falha de conferência, erro do
# `set -e`, Ctrl-C, SIGTERM, queda do SSH) volta o worker ao backup e sai com 1
# (130 no sinal). A volta só usa a PreviousSpec do Swarm quando ela é igual ao
# backup; em qualquer outro caso, ABORT e Rodrigo.
#
# Nunca imprime env: só nomes de variável, contagens, hashes e hostnames.
set -euo pipefail

storage="sell-autonomia_storage"
api="sell-autonomia_api"
worker="sell-autonomia_asset-worker"
# O alias que o EasyPanel dá ao storage na rede do projeto
# (easypanel-sell-autonomia), lido em 24/10/2026. Não `storage`: a api também
# está na rede compartilhada `easypanel`, onde esse nome pode ser de outro
# projeto. Nem `sell-autonomia_storage`: o MinIO responde 400 a Host com `_`.
alias_do_storage="sell-autonomia-storage"
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
# esperar o fim da pausa. O ao_sair mata o `sleep` que ficou.
pid_da_pausa=""
pausa() { sleep "$1" & pid_da_pausa="$!"; wait "$pid_da_pausa"; pid_da_pausa=""; }
spec_json() { d service inspect "$1" --format "{{json .${2}}}"; }
abort() { echo "ABORT: $*" >&2; exit 1; }

# Campos que um update desta janela muda ou que um deploy alheio mudaria. A
# graça é contrato da api (330 s: o e-mail em lease termina antes do SIGKILL).
campos=(Labels TaskTemplate.ContainerSpec.Image TaskTemplate.ContainerSpec.Env
  TaskTemplate.ContainerSpec.Mounts TaskTemplate.ContainerSpec.StopGracePeriod
  TaskTemplate.Networks TaskTemplate.Resources UpdateConfig RollbackConfig)
campo_da_env="TaskTemplate.ContainerSpec.Env"

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

nomes_do_backup() { jq -r '.[0].Spec.TaskTemplate.ContainerSpec.Env[] | split("=")[0]' "$1" | sort; }

redes_do_servico() { spec_json "$1" Spec.TaskTemplate.Networks | jq -r '(. // [])[].Target'; }

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

rede_do_alias() {
  # O Id da única rede em que o storage leva o alias. Zero ou mais de uma: falha.
  spec_json "$storage" Spec.TaskTemplate.Networks | jq -er --arg alias "$alias_do_storage" '
    [(. // [])[] | select(any((.Aliases // [])[]; . == $alias)) | .Target]
    | if length == 1 then .[0] else error("alias em \(length) redes") end'
}

vip_do_storage_na() {
  # A VIP do storage na rede dada, sem a máscara. Exatamente uma.
  d service inspect "$storage" --format '{{json .Endpoint.VirtualIPs}}' | jq -er --arg rede "$1" '
    [(. // [])[] | select(.NetworkID == $rede) | .Addr | split("/")[0]]
    | if length == 1 then .[0] else error("\(length) VIPs") end'
}

alias_so_do_storage() {
  # alias_so_do_storage <serviço>: o serviço está na rede do alias e, de dentro
  # dele, o alias resolve só para a VIP do storage nessa rede. Outro IP, ou
  # mais de um, é outro serviço respondendo pelo nome.
  local servico="$1" rede vip cid resolvido
  rede="$(rede_do_alias)" || { echo "o storage não tem o alias $alias_do_storage em exatamente uma rede" >&2; return 1; }
  redes_do_servico "$servico" | grep -Fxq -- "$rede" || { echo "$servico fora da rede do alias" >&2; return 1; }
  vip="$(vip_do_storage_na "$rede")" || { echo "o storage não tem uma VIP só na rede do alias" >&2; return 1; }
  cid="$(container_do "$servico")" || { echo "$servico sem contêiner" >&2; return 1; }
  resolvido="$(d exec "$cid" getent hosts "$alias_do_storage" | while read -r ip _; do echo "$ip"; done | sort -u)" \
    || { echo "$servico: $alias_do_storage não resolve" >&2; return 1; }
  [ "$resolvido" = "$vip" ] || { echo "$servico: $alias_do_storage resolve para outro endereço que não a VIP do storage" >&2; return 1; }
}

host_do_endpoint_interno() {
  # Só o hostname, nunca o valor inteiro.
  spec_json "$1" Spec.TaskTemplate.ContainerSpec.Env | jq -r --arg chave "${chave_do_endpoint}=" '
    [.[] | select(startswith($chave)) | ltrimstr($chave)
      | ltrimstr("http://") | ltrimstr("https://") | split("/")[0] | split(":")[0]] | first // ""'
}

env_no_alvo() {
  # A flag e o endpoint com os valores desta janela. Compara sem imprimir.
  spec_json "$1" Spec.TaskTemplate.ContainerSpec.Env | jq -e \
    --arg flag "${flag}=true" --arg endpoint "${chave_do_endpoint}=${endpoint_interno}" \
    'any(.[]; . == $flag) and any(.[]; . == $endpoint)' >/dev/null
}

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

tocado=0
ts_da_janela=""
concluido=0
ao_sair() {
  # Roda em toda saída depois de armado. Sem `set -e` aqui: um echo para um
  # SSH que caiu não pode interromper a restauração.
  local codigo=$?
  set +e
  # Um segundo Ctrl-C não corta a restauração no meio.
  trap - EXIT
  trap '' INT TERM HUP PIPE
  [ -n "$pid_da_pausa" ] && kill "$pid_da_pausa" 2>/dev/null
  [ "$concluido" = "1" ] && exit 0
  if [ "$tocado" = "1" ]; then
    echo "a etapa não terminou verde (código $codigo): restaurando" >&2
    restaurar "$worker" "$ts_da_janela" || echo "ABORT: a restauração parou; falar com o Rodrigo" >&2
  fi
  [ "$codigo" = "130" ] && exit 130
  exit 1
}

falhar() {
  # falhar <motivo>: sai com 1; quem desfaz é o ao_sair
  echo "FALHOU: $*" >&2
  exit 1
}

conferir_janela() {
  # conferir_janela <serviço>: o que vale antes de mexer na env de um cliente do storage
  local servico="$1" nos
  sudo -n true || abort "sudo -n não liberado neste host"
  # Com um nó, o http entre os contêineres nunca sai do host. Com dois, ele
  # atravessaria o overlay sem cifra: a decisão volta ao Rodrigo.
  nos="$(d node ls -q | wc -l | tr -d ' ')"
  [ "$nos" = "1" ] || abort "o Swarm tem $nos nós; http entre nós sem cifra: falar com o Rodrigo"
  task_running "$storage" || abort "$storage sem task Running antes de começar"
  task_running "$servico" || abort "$servico sem task Running antes de começar"
  alias_so_do_storage "$servico" || abort "$servico não vê o storage pelo alias; nada foi feito"
  # O rollback usa a ordem da spec atual (lida em 24/10/2026: stop-first).
  [ "$(spec_json "$servico" Spec.RollbackConfig.Order)" = '"stop-first"' ] \
    || abort "$servico com rollback order diferente de stop-first"
}

novas_variaveis() {
  # Quantas das duas variáveis ainda não existem no serviço.
  local novas=0
  nomes_de_env "$1" | grep -Fxq -- "$flag" || novas=$((novas + 1))
  nomes_de_env "$1" | grep -Fxq -- "$chave_do_endpoint" || novas=$((novas + 1))
  echo "$novas"
}

etapa_worker() {
  conferir_janela "$worker"
  if env_no_alvo "$worker"; then
    echo "$worker: env já no alvo, nada a fazer"
    return 0
  fi
  local antes novas nomes_antes depois sumiram
  ts_da_janela="$(date -u +%Y%m%dT%H%M%SZ)"
  guardar_backup "$worker" "$ts_da_janela"
  echo "backup: $(caminho_do_backup "$worker" "$ts_da_janela").json"
  antes="$(contagem_de_env "$worker")"
  novas="$(novas_variaveis "$worker")"
  nomes_antes="$(nomes_de_env "$worker")"
  trap ao_sair EXIT
  trap 'exit 130' INT TERM HUP
  tocado=1
  # O worker mantém a ordem de update da spec: ele não serve a loja.
  d service update --detach=false \
    --env-add "${flag}=true" --env-add "${chave_do_endpoint}=${endpoint_interno}" \
    "$worker" >/dev/null || falhar "update de $worker saiu com erro"
  convergiu "$worker" storage_responde || falhar "$worker não convergiu (storage_responde)"
  depois="$(contagem_de_env "$worker")"
  echo "$worker: env $antes → $depois"
  [ "$depois" = "$((antes + novas))" ] || falhar "$worker: env $antes → $depois, esperado $((antes + novas))"
  sumiram="$(comm -23 <(printf '%s\n' "$nomes_antes") <(nomes_de_env "$worker") | tr '\n' ' ')"
  [ -z "${sumiram// /}" ] || falhar "$worker: sumiram as variáveis $sumiram"
  env_no_alvo "$worker" || falhar "$worker sem a flag e o endpoint depois do update"
  concluido=1
  echo "worker concluído; backup com ts $ts_da_janela"
}

etapa_api_antes() {
  conferir_janela "$api"
  ts_da_janela="$(date -u +%Y%m%dT%H%M%SZ)"
  guardar_backup "$api" "$ts_da_janela"
  echo "$api: env=$(contagem_de_env "$api") endpoint_interno=$(host_do_endpoint_interno "$api")"
  echo "backup da api com ts $ts_da_janela; agora o painel, e depois: api-depois $ts_da_janela"
}

validar_ts() {
  # O ts vira parte de um caminho: só o formato que o próprio script imprime.
  case "$1" in
    [0-9][0-9][0-9][0-9][0-1][0-9][0-3][0-9]T[0-2][0-9][0-5][0-9][0-5][0-9]Z) ;;
    *) abort "ts inválido: use o que a etapa imprimiu (AAAAMMDDTHHMMSSZ)" ;;
  esac
}

etapa_api_depois() {
  # Só lê. Qualquer falha sai com 1 e diz qual rollback: o do painel, se só a
  # env está errada; o desfazer, se o painel mudou outro campo.
  local ts="${1:-}" base agora dif antes novas depois sumiram leitura
  validar_ts "$ts"
  sudo -n true || abort "sudo -n não liberado neste host"
  base="$(caminho_do_backup "$api" "$ts")"
  test -s "${base}.digital" && test -s "${base}.json" || abort "falta o backup da api com ts $ts"
  agora="$(mktemp)"
  digital "$api" Spec > "$agora" || { rm -f -- "$agora"; abort "não li a spec da api"; }
  dif="$(diferencas "${base}.digital" "$agora")"
  rm -f -- "$agora"
  dif="${dif//${campo_da_env} /}"
  [ -z "$dif" ] || abort "o painel mudou mais que a env: $dif; rollback: desfazer $ts (ver o runbook)"
  antes="$(jq '.[0].Spec.TaskTemplate.ContainerSpec.Env | length' "${base}.json")"
  novas=0
  nomes_do_backup "${base}.json" | grep -Fxq -- "$flag" || novas=$((novas + 1))
  nomes_do_backup "${base}.json" | grep -Fxq -- "$chave_do_endpoint" || novas=$((novas + 1))
  depois="$(contagem_de_env "$api")"
  echo "$api: env $antes → $depois"
  [ "$depois" = "$((antes + novas))" ] || abort "$api: env $antes → $depois, esperado $((antes + novas)); rollback no painel"
  sumiram="$(comm -23 <(nomes_do_backup "${base}.json") <(nomes_de_env "$api") | tr '\n' ' ')"
  [ -z "${sumiram// /}" ] || abort "$api: sumiram as variáveis $sumiram; rollback no painel"
  env_no_alvo "$api" || abort "$api sem a flag e o endpoint desta janela; rollback no painel"
  convergiu "$api" storage_responde || abort "$api não vê o storage; rollback no painel"
  alias_so_do_storage "$api" || abort "$api não vê o storage pelo alias; rollback no painel"
  for leitura in 1 2 3; do
    pausa "$pausa_das_leituras"
    ready_com_storage || abort "/health/ready sem storage na leitura $leitura; rollback no painel"
  done
  echo "api conferida: só a env mudou, e o storage responde pelo alias"
}

desfazer() {
  local ts="${1:-}" servico achou=0
  validar_ts "$ts"
  sudo -n true || abort "sudo -n não liberado neste host"
  for servico in "$worker" "$api"; do
    test -s "$(caminho_do_backup "$servico" "$ts").digital" || continue
    achou=1
    restaurar "$servico" "$ts" || abort "restauração parada em $servico; falar com o Rodrigo"
  done
  [ "$achou" = "1" ] || abort "nenhum backup com ts $ts"
}

seco() {
  local servico
  sudo -n true || abort "sudo -n não liberado neste host"
  echo "nós do Swarm: $(d node ls -q | wc -l | tr -d ' ')"
  echo "$storage: running=$(task_running "$storage" && echo sim || echo não)"
  for servico in "$api" "$worker"; do
    echo "$servico: env=$(contagem_de_env "$servico") endpoint_interno=$(host_do_endpoint_interno "$servico") no_alvo=$(env_no_alvo "$servico" && echo sim || echo não) running=$(task_running "$servico" && echo sim || echo não) alias=$(alias_so_do_storage "$servico" 2>/dev/null && echo ok || echo falha)"
  done
}

case "${1:-seco}" in
  seco) seco ;;
  worker) etapa_worker ;;
  api-antes) etapa_api_antes ;;
  api-depois) etapa_api_depois "${2:-}" ;;
  desfazer) desfazer "${2:-}" ;;
  *) echo "uso: $0 seco|worker|api-antes|api-depois <ts>|desfazer <ts>" >&2; exit 2 ;;
esac
