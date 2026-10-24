#!/usr/bin/env bash
# Controle do storage pela rede do projeto (#461), pelo alias que o EasyPanel
# já cria. Ver docs/runbooks/storage-461-rede-interna.md; cada escrita só com o
# OK do Rodrigo.
#
#   storage-rede-interna.sh seco             # confere e não escreve nada
#   storage-rede-interna.sh worker           # env do worker: flag e endpoint interno
#   storage-rede-interna.sh api              # env da api: as mesmas duas variáveis
#   storage-rede-interna.sh desfazer <ts>    # volta a env ao backup desse ts
#
# Regra de cada escrita: backup da spec com a digital por campo, um update só
# com as duas variáveis, e a conferência (só a env mudou, mais uma variável,
# nenhum nome a menos, task Running com a imagem do backup, storage
# respondendo). Qualquer saída que não seja o fim verde (falha de conferência,
# erro do `set -e`, Ctrl-C, SIGTERM, queda do SSH) volta a env ao backup e sai
# com 1 (130 no sinal).
#
# A volta é outro update, que põe as duas variáveis como estavam no backup, na
# ordem de update da spec. Na api é start-first: a loja não para, ao contrário
# do `docker service rollback`, que usa o rollback order (stop-first). Se a
# spec difere do backup em algo além dessas duas variáveis, a volta só usa o
# `docker service rollback` quando a PreviousSpec do Swarm é igual ao backup (o
# único update desde ele foi o desta janela); em qualquer outro caso, ABORT e
# Rodrigo.
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

# Campos com nome próprio na digital, para a mensagem dizer o que mudou. A
# graça é contrato da api (330 s: o e-mail em lease termina antes do SIGKILL).
# O `Resto` é a spec inteira sem a env: healthcheck, comando, réplicas,
# placement e o que mais um deploy alheio mudar também contam.
campos=(Labels TaskTemplate.ContainerSpec.Image TaskTemplate.ContainerSpec.Env
  TaskTemplate.ContainerSpec.Mounts TaskTemplate.ContainerSpec.StopGracePeriod
  TaskTemplate.Networks TaskTemplate.Resources UpdateConfig RollbackConfig)
campo_da_env="TaskTemplate.ContainerSpec.Env"
# Prazo de cada `docker exec` de conferência: um probe travado não segura a
# janela além do prazo de convergência.
prazo_do_exec="${STORAGE461_PRAZO_EXEC:-60}"

digital() {
  # digital <serviço> [Spec|PreviousSpec] → "campo hash" por linha; só hashes
  # O `with` evita o erro de ponteiro nulo quando não há PreviousSpec.
  # Inspect que falha é erro, não hash de saída vazia. A env entra ordenada:
  # o `--env-add` do docker reordena a lista, e a ordem não muda o processo.
  local servico="$1" qual="${2:-Spec}" campo valor
  for campo in "${campos[@]}"; do
    valor="$(d service inspect "$servico" --format "{{with .${qual}}}{{json .${campo}}}{{end}}")" || return 1
    if [ "$campo" = "$campo_da_env" ]; then
      valor="$(printf '%s' "$valor" | jq -c 'sort')" || return 1
    fi
    printf '%s %s\n' "$campo" "$(printf '%s' "$valor" | sha256sum | cut -d' ' -f1)"
  done
  valor="$(d service inspect "$servico" --format "{{with .${qual}}}{{json .}}{{end}}")" || return 1
  valor="$(printf '%s' "$valor" | jq -cS 'del(.TaskTemplate.ContainerSpec.Env)')" || return 1
  printf 'Resto %s\n' "$(printf '%s' "$valor" | sha256sum | cut -d' ' -f1)"
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

imagem_da_task() {
  # A imagem da task que está rodando, não a da spec: um crash-loop com outra
  # imagem não passa por restaurado.
  local task
  task="$(d service ps -q "$1" --filter desired-state=running | head -1)"
  test -n "$task" || return 1
  d inspect "$task" --format '{{json .Spec.ContainerSpec.Image}}'
}

# HEAD assinado nos dois buckets, de dentro do contêiner, com o endpoint que
# ele tem agora. Sem o cache do /health/ready.
storage_responde() {
  local cid
  cid="$(container_do "$1")" || return 1
  d exec "$cid" timeout "$prazo_do_exec" python -c '
from appsell_api.config import get_settings
from appsell_api.storage_s3 import S3Storage
S3Storage(get_settings()).probe_availability()' >/dev/null 2>&1
}

ready_com_storage() {
  local cid
  cid="$(container_do "$api")" || return 1
  d exec "$cid" timeout "$prazo_do_exec" python -c '
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
  resolvido="$(d exec "$cid" timeout "$prazo_do_exec" getent hosts "$alias_do_storage" | while read -r ip _; do echo "$ip"; done | sort -u)" \
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

imagem_esperada=""
pronto() {
  # pronto <serviço>: a task roda a imagem do backup e o storage responde de
  # dentro dela.
  [ "$(imagem_da_task "$1")" = "$imagem_esperada" ] && storage_responde "$1"
}

resto_da_env() {
  # A env sem as duas variáveis desta janela, ordenada, só como hash.
  jq -c --arg flag "${flag}=" --arg endpoint "${chave_do_endpoint}=" \
    '[.[] | select((startswith($flag) or startswith($endpoint)) | not)] | sort' | sha256sum | cut -d' ' -f1
}

igual_ao_backup_depois_da_volta() {
  # igual_ao_backup_depois_da_volta <serviço> <base>: spec igual ao backup e
  # task Running na imagem do backup, com o storage respondendo.
  local servico="$1" base="$2" agora dif="(não li a spec depois da volta)"
  agora="$(mktemp)"
  if digital "$servico" > "$agora"; then
    dif="$(diferencas "${base}.digital" "$agora")"
  fi
  rm -f -- "$agora"
  if [ -n "$dif" ]; then
    echo "ABORT: $servico: depois da volta a spec difere do backup em: $dif; falar com o Rodrigo" >&2
    return 1
  fi
  imagem_esperada="$(jq -c '.[0].Spec.TaskTemplate.ContainerSpec.Image' "${base}.json")"
  convergiu "$servico" pronto || {
    echo "ABORT: $servico com a spec do backup, mas sem task Running na imagem do backup e com storage; falar com o Rodrigo" >&2
    return 1
  }
  echo "$servico: restaurado, igual ao backup"
}

voltar_as_duas_variaveis() {
  # Update na ordem da spec: as duas variáveis como no backup. O valor do
  # backup vai direto para o argumento, nunca para a tela.
  local servico="$1" base="$2" args=() chave valor
  for chave in "$flag" "$chave_do_endpoint"; do
    if valor="$(jq -er --arg chave "${chave}=" \
      '.[0].Spec.TaskTemplate.ContainerSpec.Env[] | select(startswith($chave))' "${base}.json")"; then
      args+=(--env-add "$valor")
    else
      args+=(--env-rm "$chave")
    fi
  done
  d service update --detach=false "${args[@]}" "$servico" >/dev/null || true
}

voltar_pela_previous_spec() {
  # docker service rollback, só se a PreviousSpec é o backup. Usa o rollback
  # order da spec, que tem de ser stop-first; na api a loja para na troca.
  local servico="$1" base="$2" anterior dif=""
  anterior="$(mktemp)"
  digital "$servico" PreviousSpec > "$anterior" || dif="(não li a PreviousSpec)"
  [ -n "$dif" ] || dif="$(diferencas "${base}.digital" "$anterior")"
  rm -f -- "$anterior"
  if [ -n "$dif" ]; then
    echo "ABORT: $servico: a spec difere do backup além das duas variáveis, e a PreviousSpec também difere em: $dif; falar com o Rodrigo" >&2
    return 1
  fi
  if [ "$(spec_json "$servico" Spec.RollbackConfig.Order)" != '"stop-first"' ]; then
    echo "ABORT: $servico: rollback order não é stop-first; falar com o Rodrigo" >&2
    return 1
  fi
  echo "$servico: a spec difere do backup além das duas variáveis; docker service rollback (stop-first)" >&2
  d service rollback --detach=false "$servico" >/dev/null || true
}

restaurar() {
  # restaurar <serviço> <ts>: volta ao backup, e só se for seguro
  local servico="$1" ts="$2" base agora dif
  base="$(caminho_do_backup "$servico" "$ts")"
  test -s "${base}.digital" && test -s "${base}.json" || { echo "ABORT: falta o backup de $servico com ts $ts" >&2; return 1; }
  agora="$(mktemp)"
  digital "$servico" > "$agora" || {
    rm -f -- "$agora"
    echo "ABORT: $servico: não li a spec atual; falar com o Rodrigo" >&2
    return 1
  }
  dif="$(diferencas "${base}.digital" "$agora")"
  rm -f -- "$agora"
  if [ -z "$dif" ]; then
    echo "$servico: spec já igual ao backup, nada a desfazer"
    return 0
  fi
  if [ "$dif" = "$campo_da_env " ] && [ "$(spec_json "$servico" Spec.TaskTemplate.ContainerSpec.Env | resto_da_env)" \
    = "$(jq '.[0].Spec.TaskTemplate.ContainerSpec.Env' "${base}.json" | resto_da_env)" ]; then
    voltar_as_duas_variaveis "$servico" "$base"
  else
    voltar_pela_previous_spec "$servico" "$base" || return 1
  fi
  igual_ao_backup_depois_da_volta "$servico" "$base"
}

tocado=""
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
  if [ -n "$tocado" ]; then
    echo "a etapa não terminou verde (código $codigo): restaurando" >&2
    restaurar "$tocado" "$ts_da_janela" || echo "ABORT: a restauração parou; falar com o Rodrigo" >&2
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
  # A api troca com start-first, a ordem da spec dela (lida em 24/10/2026): a
  # task nova só substitui a antiga depois de healthy, e o e-mail é claim com
  # lease e fence no banco. O script não muda a ordem; se ela não for essa,
  # a loja cairia na troca e na volta, e a decisão é do Rodrigo.
  if [ "$servico" = "$api" ]; then
    [ "$(spec_json "$api" Spec.UpdateConfig.Order)" = '"start-first"' ] \
      || abort "$api com update order diferente de start-first"
  fi
}

etapa_env() {
  # etapa_env <serviço>: a flag (nova) e o endpoint interno (substituído)
  local servico="$1" antes nomes_antes depois sumiram agora dif leitura
  conferir_janela "$servico"
  if env_no_alvo "$servico"; then
    echo "$servico: env já no alvo, nada a fazer"
    return 0
  fi
  # Sobe exatamente 1: a flag entra e o endpoint é substituído. Outro estado
  # de partida não é o lido em 24/10/2026.
  ! nomes_de_env "$servico" | grep -Fxq -- "$flag" || abort "$servico já tem $flag; falar com o Rodrigo"
  nomes_de_env "$servico" | grep -Fxq -- "$chave_do_endpoint" || abort "$servico sem $chave_do_endpoint; falar com o Rodrigo"
  ts_da_janela="$(date -u +%Y%m%dT%H%M%SZ)"
  guardar_backup "$servico" "$ts_da_janela"
  echo "backup: $(caminho_do_backup "$servico" "$ts_da_janela").json"
  antes="$(contagem_de_env "$servico")"
  nomes_antes="$(nomes_de_env "$servico")"
  imagem_esperada="$(spec_json "$servico" Spec.TaskTemplate.ContainerSpec.Image)"
  trap ao_sair EXIT
  trap 'exit 130' INT TERM HUP
  tocado="$servico"
  # Sem --update-order: vale a ordem da spec, conferida acima.
  d service update --detach=false \
    --env-add "${flag}=true" --env-add "${chave_do_endpoint}=${endpoint_interno}" \
    "$servico" >/dev/null || falhar "update de $servico saiu com erro"
  convergiu "$servico" pronto || falhar "$servico não convergiu (task na imagem do backup e storage)"
  agora="$(mktemp)"
  digital "$servico" > "$agora" || { rm -f -- "$agora"; falhar "não li a spec de $servico"; }
  dif="$(diferencas "$(caminho_do_backup "$servico" "$ts_da_janela").digital" "$agora")"
  rm -f -- "$agora"
  [ "$dif" = "$campo_da_env " ] || falhar "$servico: o update mudou mais que a env: $dif"
  depois="$(contagem_de_env "$servico")"
  echo "$servico: env $antes → $depois"
  [ "$depois" = "$((antes + 1))" ] || falhar "$servico: env $antes → $depois, esperado $((antes + 1))"
  sumiram="$(comm -23 <(printf '%s\n' "$nomes_antes") <(nomes_de_env "$servico") | tr '\n' ' ')"
  [ -z "${sumiram// /}" ] || falhar "$servico: sumiram as variáveis $sumiram"
  [ "$(spec_json "$servico" Spec.TaskTemplate.ContainerSpec.Env | resto_da_env)" \
    = "$(jq '.[0].Spec.TaskTemplate.ContainerSpec.Env' "$(caminho_do_backup "$servico" "$ts_da_janela").json" | resto_da_env)" ] \
    || falhar "$servico: o valor de outra variável mudou no update"
  env_no_alvo "$servico" || falhar "$servico sem a flag e o endpoint depois do update"
  alias_so_do_storage "$servico" || falhar "$servico não vê o storage pelo alias depois do update"
  if [ "$servico" = "$api" ]; then
    for leitura in 1 2 3; do
      pausa "$pausa_das_leituras"
      ready_com_storage || falhar "/health/ready sem storage na leitura $leitura"
    done
  fi
  concluido=1
  echo "$servico concluído; backup com ts $ts_da_janela"
}

validar_ts() {
  # O ts vira parte de um caminho: só o formato que o próprio script imprime.
  case "$1" in
    [0-9][0-9][0-9][0-9][0-1][0-9][0-3][0-9]T[0-2][0-9][0-5][0-9][0-5][0-9]Z) ;;
    *) abort "ts inválido: use o que a etapa imprimiu (AAAAMMDDTHHMMSSZ)" ;;
  esac
}

desfazer() {
  local ts="${1:-}" servico achou=0
  validar_ts "$ts"
  sudo -n true || abort "sudo -n não liberado neste host"
  # Um Ctrl-C ou a queda do SSH não cortam a volta no meio.
  trap '' INT TERM HUP
  for servico in "$api" "$worker"; do
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
  worker) etapa_env "$worker" ;;
  api) etapa_env "$api" ;;
  desfazer) desfazer "${2:-}" ;;
  *) echo "uso: $0 seco|worker|api|desfazer <ts>" >&2; exit 2 ;;
esac
