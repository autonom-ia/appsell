# shellcheck shell=bash
# Download de binário fixado por checksum, com cache entre runs (#634).
#
# O gate baixava binários do GitHub Releases em todo run. Em 08 e 09/11/2026
# o GitHub devolveu 504 várias vezes, e cada 504 derrubou uma certificação
# inteira com os testes verdes. O workflow agora restaura o arquivo com
# actions/cache (chave = nome + versão + plataforma + sha256) e este helper só
# baixa quando o arquivo não veio do cache.
#
# Este helper não confere checksum: quem chama confere o sha256 sempre, com
# cache ou sem, antes de executar qualquer coisa.
#
# Uso (depois de `source`):
#   pinned_fetch <url> <arquivo-destino>
# Saída 0: arquivo presente em <arquivo-destino>.
# Saída 3: download falhou depois das retentativas. É erro de infraestrutura
# (o GitHub fora do ar), não de validação, e aparece assim na anotação do run.

PINNED_FETCH_INFRA_EXIT=3

pinned_fetch() {
  local url="$1"
  local dest="$2"
  local partial="${dest}.partial"

  if [ -s "$dest" ]; then
    echo "pinned_fetch: $(basename "$dest") veio do cache; o checksum é conferido a seguir." >&2
    return 0
  fi

  mkdir -p "$(dirname "$dest")"
  rm -f "$partial"
  echo "pinned_fetch: $(basename "$dest") não está no cache; baixando." >&2
  # --retry-all-errors faz o curl repetir também 5xx como o 504 do GitHub;
  # sem --retry-delay o intervalo cresce em backoff exponencial.
  if ! curl --fail --silent --show-error --location \
    --connect-timeout 20 \
    --retry 5 --retry-all-errors --retry-max-time 120 \
    --output "$partial" "$url"; then
    rm -f "$partial"
    echo "::error title=Download externo indisponível (infraestrutura)::$(basename "$dest") não baixou depois das retentativas. Não é falha de validação nem de teste; o arquivo não está no cache e a origem não respondeu." >&2
    return "$PINNED_FETCH_INFRA_EXIT"
  fi
  mv "$partial" "$dest"
}
