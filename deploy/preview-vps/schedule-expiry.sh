#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 COMPOSE_FILE ENV_FILE" >&2
  exit 2
fi
compose_file="$(realpath -e "$1")"
env_file="$(realpath -e "$2")"
config_dir="$(dirname "$compose_file")"
source "$config_dir/load-preview-env.sh"
load_preview_env "$env_file"
unit="autonomia-sell-preview-66-${APPSELL_SOURCE_SHA:0:12}-expiry"
service_file="/etc/systemd/system/$unit.service"
timer_file="/etc/systemd/system/$unit.timer"
[[ ! -e "$service_file" && ! -e "$timer_file" ]] || { echo "Expiry unit collision" >&2; exit 2; }

umask 022
tmp_service="$(mktemp)"
tmp_timer="$(mktemp)"
cleanup() { rm -f -- "$tmp_service" "$tmp_timer"; }
trap cleanup EXIT
printf '%s\n' \
  '[Unit]' \
  'Description=Autonom.ia Sell Issue 66 preview teardown' \
  'After=docker.service' \
  'StartLimitIntervalSec=0' \
  '' \
  '[Service]' \
  'Type=oneshot' \
  "ExecStart=/usr/bin/env bash $config_dir/teardown.sh $compose_file $env_file" \
  'Restart=on-failure' \
  'RestartSec=60s' >"$tmp_service"
printf '%s\n' \
  '[Unit]' \
  'Description=Autonom.ia Sell Issue 66 persistent expiry timer' \
  '' \
  '[Timer]' \
  "OnCalendar=$PREVIEW_EXPIRES_AT" \
  'Persistent=true' \
  'AccuracySec=1min' \
  "Unit=$unit.service" \
  '' \
  '[Install]' \
  'WantedBy=timers.target' >"$tmp_timer"
install -o root -g root -m 0644 "$tmp_service" "$service_file"
install -o root -g root -m 0644 "$tmp_timer" "$timer_file"
systemctl daemon-reload
systemctl enable --now "$unit.timer" >/dev/null
echo "Scheduled persistent fail-closed teardown at $PREVIEW_EXPIRES_AT."
