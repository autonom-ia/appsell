#!/usr/bin/env bash
# O contrato de multipart contra o MinIO, que é o storage da produção (#460).
#
# O resto do cert-storage roda contra o SeaweedFS, e foi assim que o fechamento
# sem checksum das partes passou: o SeaweedFS aceita, o MinIO responde
# InvalidPart. Este script repete os testes de multipart e de assinatura contra
# um MinIO de verdade.
#
# O MinIO community foi arquivado: não há mais imagem pública nem dl.min.io
# (410). O binário vem da release do GitHub, com o SHA-256 fixo abaixo. A
# versão da produção ainda não foi lida no host; ver #461.
#
#   test-storage-minio.sh --cache-key    imprime a chave do actions/cache
#   test-storage-minio.sh --fetch-only   garante o binário no cache e confere
#   test-storage-minio.sh                confere o binário e roda o contrato
#
# APPSELL_PINNED_CACHE_DIR aponta o diretório que o workflow restaura com
# actions/cache (#634): o GitHub Releases só é consultado quando o binário
# não veio do cache. O sha256 é conferido em todo caso.
set -euo pipefail

minio_release="RELEASE.2025-09-07T16-13-09Z"
minio_sha256="7c5bd8512c6e966455b1d198209358b2d191c77a83ab377c4073281065fb855f"
minio_url="https://github.com/minio/minio/releases/download/${minio_release}/minio.linux-amd64.${minio_release}"

if [ "${1:-}" = --cache-key ]; then
  echo "minio-${minio_release}-linux_x86_64-${minio_sha256}"
  exit 0
fi

if [ "$(uname -s)-$(uname -m)" != "Linux-x86_64" ]; then
  echo "test-storage-minio.sh só roda no runner linux-amd64 do CI" >&2
  exit 1
fi

workdir="$(mktemp -d -t appsell-minio.XXXXXX)"
minio_pid=""
cleanup() {
  if [ -n "$minio_pid" ]; then
    kill "$minio_pid" 2>/dev/null || true
    wait "$minio_pid" 2>/dev/null || true
  fi
  rm -rf -- "$workdir"
}
trap cleanup EXIT INT TERM

# shellcheck source=scripts/ci/pinned_download.sh
source "$(dirname "${BASH_SOURCE[0]}")/ci/pinned_download.sh"
minio_download="${APPSELL_PINNED_CACHE_DIR:-$workdir}/minio.linux-amd64.${minio_release}"
pinned_fetch "$minio_url" "$minio_download"
# A conferência é feita na cópia que vai rodar, no temporário; o arquivo do
# cache não muda.
cp "$minio_download" "$workdir/minio"
if ! printf '%s  %s\n' "$minio_sha256" "$workdir/minio" | sha256sum --check --status; then
  echo "::error title=Checksum do MinIO não confere::o binário não tem o sha256 fixado. Ele não é executado." >&2
  exit 1
fi
if [ "${1:-}" = --fetch-only ]; then
  exit 0
fi
chmod +x "$workdir/minio"

port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
access_key="contract-$(openssl rand -hex 8)"
secret_key="$(openssl rand -hex 32)"
mkdir -p "$workdir/data"

# O MinIO não tem CORS por bucket: a origem permitida é global. A mesma que o
# contrato usa (storage_allowed_origin no teste).
MINIO_ROOT_USER="$access_key" MINIO_ROOT_PASSWORD="$secret_key" \
MINIO_API_CORS_ALLOW_ORIGIN="http://localhost:3000" \
  "$workdir/minio" server "$workdir/data" --address "127.0.0.1:${port}" --quiet \
  >"$workdir/minio.log" 2>&1 &
minio_pid=$!

for _ in $(seq 1 30); do
  if curl --fail --silent "http://127.0.0.1:${port}/minio/health/ready"; then
    break
  fi
  sleep 1
done
curl --fail --silent "http://127.0.0.1:${port}/minio/health/ready"

APPSELL_TEST_S3_ENDPOINT="http://127.0.0.1:${port}" \
APPSELL_TEST_S3_ACCESS_KEY="$access_key" \
APPSELL_TEST_S3_SECRET_KEY="$secret_key" \
  uv run --project apps/api pytest -ra apps/api/tests/test_storage_s3_contract.py \
    -k "multipart_round_trip or presign_rejects"
