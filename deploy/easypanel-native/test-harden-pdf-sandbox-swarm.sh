#!/usr/bin/env bash
# Ensaio do harden-pdf-sandbox.sh num Swarm descartável do runner do CI (#478).
#
# O script endurece o pdf-sandbox de produção num update só, e a volta dele é
# um trap. Nada disso se prova com docker falso: aqui sobe um Swarm de verdade,
# com um sandbox da mesma forma do de produção (imagem da api, usuário 10001,
# socket num volume, reciclagem depois de cada PDF, stop-first, sem
# healthcheck, ainda sem endurecer) e dois clientes ("api" e "asset-worker") que
# montam o socket como em produção. Roda o script real em todos os modos e
# confere a spec e o comportamento depois de cada um:
#
#   seco                      → nada muda
#   --provar antes            → (a) verde, (b) vermelho: há saída para fora
#   --aplicar com PDF errado  → aborta na linha de base, nada muda
#   --aplicar com falha       → /tmp minúsculo: (a) falha depois do update, o
#                               trap restaura e a spec volta à original
#   --aplicar                 → spec endurecida, (a) e (b) verdes
#   --provar depois           → verde
#   --rollback <backup>       → spec original de volta
#   --provar depois da volta  → (b) vermelho de novo: a volta foi de verdade
#
# Só roda no runner do GitHub e só num Docker sem Swarm: nunca no host de
# produção, que já é um nó de Swarm.
set -euo pipefail

raiz="$(git rev-parse --show-toplevel)"
script="$raiz/deploy/easypanel-native/harden-pdf-sandbox.sh"
pdf="$raiz/output/pdf/codex/01-o-que-e-codex.pdf"

prefixo=ensaio478
sandbox="${prefixo}_pdf-sandbox"
api="${prefixo}_api"
worker="${prefixo}_asset-worker"
rede="${prefixo}_pdf-sandbox-isolada"
volume="${prefixo}_pdf_socket"
imagem="ensaio478/pdf-sandbox:$(git -C "$raiz" rev-parse --short=7 HEAD)"

[ "${GITHUB_ACTIONS:-}" = "true" ] || { echo "ABORT: este ensaio só roda no runner do GitHub" >&2; exit 1; }
[ "$(sudo -n docker info --format '{{.Swarm.LocalNodeState}}')" = "inactive" ] \
  || { echo "ABORT: o Docker daqui já está num Swarm; o ensaio só cria o seu" >&2; exit 1; }

trabalho="$(mktemp -d)"
iniciou_swarm=0
limpar() {
  local codigo=$?
  if [ "$iniciou_swarm" = "1" ]; then
    sudo -n docker service rm "$sandbox" "$api" "$worker" >/dev/null 2>&1 || true
    sudo -n docker swarm leave --force >/dev/null || true
  fi
  rm -rf -- "$trabalho"
  exit "$codigo"
}
trap limpar EXIT

falhar() { echo "FALHOU: $*" >&2; exit 1; }

echo "== imagem $imagem (Dockerfile.api, a mesma do pdf-sandbox de produção)"
sudo -n docker build --quiet -f "$raiz/deploy/easypanel-native/Dockerfile.api" \
  --build-arg "GIT_SHA=$(git -C "$raiz" rev-parse HEAD)" -t "$imagem" "$raiz" >/dev/null

echo "== swarm descartável"
endereco="$(hostname -I | cut -d' ' -f1)"
sudo -n docker swarm init --advertise-addr "$endereco" >/dev/null
iniciou_swarm=1

# O runner do sandbox exige o diretório do socket 0700 e de 10001:10001, como
# o pdf-sandbox-init do compose deixa.
sudo -n docker volume create "$volume" >/dev/null
sudo -n docker run --rm --user 0:0 --network none -v "$volume:/run/appsell-pdf" "$imagem" \
  sh -ec 'chown 10001:10001 /run/appsell-pdf && chmod 0700 /run/appsell-pdf'

# Produção tem 4 núcleos; o runner pode ter menos, e o Swarm recusa pedir mais
# do que existe.
nucleos="$(nproc)"
[ "$nucleos" -gt 4 ] && nucleos=4

echo "== sandbox com a forma de produção, ainda sem endurecer"
sudo -n docker service create --quiet --detach=false --no-resolve-image --name "$sandbox" \
  --user 10001:10001 --env APPSELL_PDF_SANDBOX_RECYCLE_AFTER_JOB=true \
  --limit-memory 4g --limit-cpu "$nucleos" \
  --update-order stop-first --update-failure-action pause --rollback-order stop-first \
  --restart-condition on-failure --restart-delay 5s --no-healthcheck \
  --mount "type=volume,source=$volume,destination=/run/appsell-pdf" \
  "$imagem" python -m appsell_api.pdf_sandbox_runner >/dev/null

for cliente in "$api" "$worker"; do
  sudo -n docker service create --quiet --detach=false --no-resolve-image --name "$cliente" \
    --user 10001:10001 --env APPSELL_API_PDF_SANDBOX_SOCKET=/run/appsell-pdf/pdf.sock \
    --read-only --mount type=tmpfs,destination=/tmp --no-healthcheck \
    --mount "type=volume,source=$volume,destination=/run/appsell-pdf,readonly=true" \
    "$imagem" sleep infinity >/dev/null
done

ver() { sudo -n docker service inspect "$sandbox" --format "$1"; }

# Os campos que o endurecimento ou a volta mudariam, para comparar estados.
fotografia() {
  ver '{{json .Spec.TaskTemplate.ContainerSpec.Image}} {{json .Spec.TaskTemplate.ContainerSpec.Args}}
{{json .Spec.TaskTemplate.ContainerSpec.Env}} {{json .Spec.TaskTemplate.ContainerSpec.User}}
{{json .Spec.TaskTemplate.ContainerSpec.Mounts}} {{json .Spec.TaskTemplate.ContainerSpec.ReadOnly}}
{{json .Spec.TaskTemplate.ContainerSpec.CapabilityDrop}} {{json .Spec.TaskTemplate.Resources}}
{{json .Spec.TaskTemplate.Networks}} {{json .Spec.Labels}}' | sha256sum | cut -d' ' -f1
}

# rodar <nome> <rc esperado> [VAR=valor ...] -- <argumentos do script>
rodar() {
  local nome="$1" esperado="$2" rc variaveis=()
  shift 2
  while [ "$1" != "--" ]; do variaveis+=("$1"); shift; done
  shift
  echo "== $nome: harden-pdf-sandbox.sh ${*:-(seco)}"
  set +e
  env PDF_SANDBOX_SERVICE="$sandbox" PDF_SANDBOX_IMAGE="$imagem" PDF_SANDBOX_NETWORK="$rede" \
    PDF_SANDBOX_CLIENT="$api" PDF_SANDBOX_WORKER="$worker" PDF_SANDBOX_PROVA_PDF="$pdf" \
    "${variaveis[@]}" bash "$script" "$@" >"$trabalho/$nome.log" 2>&1
  rc=$?
  set -e
  sed 's/^/   | /' "$trabalho/$nome.log"
  [ "$rc" = "$esperado" ] || falhar "$nome: código $rc, esperado $esperado"
}

tem() { grep -qF -- "$2" "$trabalho/$1.log" || falhar "$1: faltou na saída: $2"; }

backup_de() {
  local linha
  while IFS= read -r linha; do
    case "$linha" in
      "backup: "*) linha="${linha#backup: }"; echo "${linha%% (e *}"; return 0 ;;
    esac
  done <"$trabalho/$1.log"
  falhar "$1: sem linha de backup"
}

original="$(fotografia)"
[ "$(ver '{{.Spec.TaskTemplate.ContainerSpec.ReadOnly}}')" = "false" ] || falhar "o sandbox já nasceu read-only"
[ "$(ver '{{json .Spec.TaskTemplate.Networks}}')" = "null" ] || falhar "o sandbox já nasceu numa rede"

rodar seco 0 --
[ "$(fotografia)" = "$original" ] || falhar "seco mudou a spec"
sudo -n docker network inspect "$rede" >/dev/null 2>&1 && falhar "seco criou a rede"

rodar provar-antes 1 -- --provar
tem provar-antes '"page_count": 1'
tem provar-antes "conectou"

errado="$trabalho/outro.pdf"
printf '%%PDF-1.4\n%%%%EOF\n' >"$errado"
rodar pdf-errado 1 PDF_SANDBOX_PROVA_PDF="$errado" -- --aplicar
tem pdf-errado "nada aplicado"
[ "$(fotografia)" = "$original" ] || falhar "o PDF errado deixou a spec mudada"
sudo -n docker network inspect "$rede" >/dev/null 2>&1 && falhar "o PDF errado criou a rede"

# Falha injetada depois do update: um /tmp de 4 KiB não cabe as páginas do
# PDF, a prova pelo worker falha com a spec já endurecida e só o trap volta.
rodar falha-injetada 1 PDF_SANDBOX_TMP_BYTES=4096 -- --aplicar
tem falha-injetada "aplicação não terminou verde"
tem falha-injetada "rollback concluído"
[ "$(fotografia)" = "$original" ] || falhar "o trap não devolveu a spec original"

rodar aplicar 0 -- --aplicar
[ "$(ver '{{.Spec.TaskTemplate.ContainerSpec.ReadOnly}}')" = "true" ] || falhar "aplicar: root gravável"
[ "$(ver '{{json .Spec.TaskTemplate.ContainerSpec.CapabilityDrop}}')" = '["ALL"]' ] || falhar "aplicar: sem cap-drop ALL"
[ "$(ver '{{.Spec.TaskTemplate.Resources.Limits.Pids}}')" = "64" ] || falhar "aplicar: pids"
# O Swarm não guarda a ordem dos mounts: confere os dois e que só há dois.
montagens="$(ver '{{range .Spec.TaskTemplate.ContainerSpec.Mounts}}{{.Type}}:{{.Target}} {{end}}')"
read -ra lista <<<"$montagens"
[ "${#lista[@]}" = "2" ] || falhar "aplicar: mounts $montagens"
case " $montagens" in *" tmpfs:/tmp "*) ;; *) falhar "aplicar: sem tmpfs em /tmp" ;; esac
case " $montagens" in *" volume:/run/appsell-pdf "*) ;; *) falhar "aplicar: sem o volume do socket" ;; esac
[ "$(ver '{{len .Spec.TaskTemplate.Networks}}')" = "1" ] || falhar "aplicar: redes"
[ "$(ver '{{len .Spec.TaskTemplate.ContainerSpec.Env}}')" = "1" ] || falhar "aplicar: env mudou"
[ "$(sudo -n docker network inspect "$rede" --format '{{.Driver}} {{.Internal}}')" = "overlay true" ] || falhar "aplicar: rede não é overlay internal"

rodar provar-depois 0 -- --provar

rodar rollback 0 -- --rollback "$(backup_de aplicar)"
tem rollback "rollback concluído"
[ "$(fotografia)" = "$original" ] || falhar "o --rollback não devolveu a spec original"

rodar provar-depois-da-volta 1 -- --provar
tem provar-depois-da-volta '"page_count": 1'
tem provar-depois-da-volta "conectou"

echo "ensaio verde: seco, provas, linha de base, trap, aplicar e rollback"
