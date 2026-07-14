#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 DOWNLOAD_ROOT APPSELL_SHA AUTH_SHA FOUNDATION_SHA" >&2
  exit 2
fi
root="$(realpath -e "$1")"
appsell_sha="$2"
auth_sha="$3"
foundation_sha="$4"
for sha in "$appsell_sha" "$auth_sha" "$foundation_sha"; do
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
done

config="$root/config-$appsell_sha.tar.gz"
sell="$root/sell-$appsell_sha.tar"
domain="$root/domain-$appsell_sha.tar"
auth="$root/auth-$auth_sha.tar"
foundation="$root/foundation-$foundation_sha.tar"
for file in "$config" "$sell" "$domain" "$auth" "$foundation"; do
  [[ -f "$file" && ! -L "$file" ]] || { echo "Missing artifact: $file" >&2; exit 2; }
done

verify_attestation() {
  local file="$1" repo="$2" workflow="$3" sha="$4"
  gh attestation verify "$file" -R "$repo" \
    --signer-workflow "$workflow" \
    --source-digest "$sha" \
    --deny-self-hosted-runners
}
verify_attestation "$config" autonom-ia/appsell autonom-ia/appsell/.github/workflows/preview-images.yml "$appsell_sha"
verify_attestation "$sell" autonom-ia/appsell autonom-ia/appsell/.github/workflows/preview-images.yml "$appsell_sha"
verify_attestation "$domain" autonom-ia/appsell autonom-ia/appsell/.github/workflows/preview-images.yml "$appsell_sha"
gh attestation verify "$auth" -R autonom-ia/autonomia-identity-auth \
  --signer-workflow autonom-ia/autonomia-identity-auth/.github/workflows/trusted-preview-artifact.yml \
  --source-ref refs/heads/main \
  --deny-self-hosted-runners
verify_attestation "$foundation" autonom-ia/autonomia-identity-foundation autonom-ia/autonomia-identity-foundation/.github/workflows/preview-image.yml "$foundation_sha"

install -d -m 0700 "$root/upload/artifacts"
cp "$sell" "$domain" "$auth" "$foundation" "$root/upload/artifacts/"
(
  cd "$root/upload/artifacts"
  shasum -a 256 \
    "sell-$appsell_sha.tar" "domain-$appsell_sha.tar" \
    "auth-$auth_sha.tar" "foundation-$foundation_sha.tar" > SHA256SUMS
)
[[ ! -e "$root/upload/config" ]] || { echo "Refusing to overwrite upload/config" >&2; exit 2; }
tar -xzf "$config" -C "$root/upload"
echo "All five GitHub attestations verified; upload/ is ready for the VPS."
