#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 BEFORE_DIRECTORY AFTER_DIRECTORY" >&2
  exit 2
fi

before="$(realpath -e "$1")"
after="$(realpath -e "$2")"
failed=0

for inventory in swarm-services.tsv swarm-nodes.tsv containers.tsv networks.tsv volumes.tsv images.tsv listeners.tsv; do
  comm -23 "$before/$inventory" "$after/$inventory" >"$after/missing-$inventory"
  if [[ -s "$after/missing-$inventory" ]]; then
    echo "Pre-existing state disappeared or degraded in $inventory:" >&2
    cat "$after/missing-$inventory" >&2
    failed=1
  fi
done

after_disk="$(<"$after/docker-disk-available-kb.txt")"
after_memory="$(<"$after/memory-available-kb.txt")"
(( after_disk >= 30 * 1024 * 1024 )) || { echo "Docker disk headroom fell below 30 GiB" >&2; failed=1; }
(( after_memory >= 4 * 1024 * 1024 )) || { echo "Available memory fell below 4 GiB" >&2; failed=1; }

if [[ "$failed" -ne 0 ]]; then
  echo "Existing VPS operational comparison failed closed." >&2
  exit 1
fi

echo "All pre-existing VPS resources, health states, replicas, listeners and node states remain present."
