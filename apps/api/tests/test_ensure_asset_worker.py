"""O ensure-asset-worker.sh põe o worker na rede isolada do storage (#461).

Roda o script de verdade com um `docker` falso no PATH, que responde ao
`inspect` e grava os argumentos do `service create`. Nada toca Docker real.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_SCRIPT = _RAIZ / "deploy/easypanel-native/ensure-asset-worker.sh"

_CANDIDATOS_A_BASH = ("bash", "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/usr/bin/bash")


def _bash_moderno() -> str | None:
    """O script usa `mapfile`, que o bash 3.2 do macOS não tem. O host e o CI
    rodam bash 5; aqui vale o primeiro bash 4 ou mais que existir."""
    for candidato in _CANDIDATOS_A_BASH:
        caminho = shutil.which(candidato)
        if caminho is None:
            continue
        versao = subprocess.run(  # noqa: S603
            [caminho, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True, text=True, check=False
        ).stdout.strip()
        if versao.isdigit() and int(versao) >= 4:
            return caminho
    return None


_BASH = _bash_moderno()
pytestmark = pytest.mark.skipif(
    _BASH is None, reason="sem bash 4 ou mais (o do macOS é 3.2); roda no CI e no host"
)

_REDE_DO_CLAMAV = "rede-clamav-id"
_REDE_DO_STORAGE = "rede-storage-id"

_ENV_DA_API = [
    "APPSELL_API_ENVIRONMENT=production",
    "APPSELL_API_DATABASE_URL=postgresql+asyncpg://runtime@db:5432/appsell?ssl=require",
    "APPSELL_API_INTERNAL_ISSUER=appsell-bff",
    "APPSELL_API_INTERNAL_AUDIENCE=appsell-api",
    "APPSELL_API_INTERNAL_HMAC_KEYRING_B64_JSON=[]",
    "APPSELL_API_SERVICE_CREDENTIAL_B64=x",
    "APPSELL_API_STORAGE_PROVIDER=s3",
    "APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://appsell-storage:9000",
    "APPSELL_API_STORAGE_CONTROL_ACCESS_KEY=k",
    "APPSELL_API_STORAGE_CONTROL_SECRET_KEY=s",
    "APPSELL_API_STORAGE_QUARANTINE_BUCKET=appsell-quarantine",
    "APPSELL_API_STORAGE_READY_BUCKET=appsell-ready",
    "APPSELL_API_STORAGE_ALLOWED_ORIGIN=https://sell.autonomia.site",
    "APPSELL_API_MALWARE_SCANNER_PROVIDER=clamav",
    "APPSELL_API_MALWARE_SCANNER_SOCKET=/run/appsell-clamav/clamd.sock",
    "APPSELL_API_PDF_SANDBOX_SOCKET=/run/appsell-pdf/pdf.sock",
]

_SPEC_DA_API = [
    {
        "Spec": {
            "TaskTemplate": {
                "ContainerSpec": {
                    "Image": "easypanel/sell-autonomia/api:teste",
                    "Env": _ENV_DA_API,
                    "Mounts": [
                        {"Type": "volume", "Source": "vol-clamav", "Target": "/run/appsell-clamav"},
                        {"Type": "volume", "Source": "vol-pdf", "Target": "/run/appsell-pdf"},
                    ],
                },
                "Networks": [{"Target": "rede-easypanel-id"}, {"Target": _REDE_DO_CLAMAV}],
            }
        }
    }
]

# Responde só ao que o script pergunta. `REDE` escolhe o estado da rede do
# storage; `WORKER_EXISTE` simula a segunda execução.
_DOCKER_FALSO = """#!/usr/bin/env bash
set -euo pipefail
case "$1 $2" in
  "service inspect")
    case "$3" in
      sell-autonomia_api) cat "$PASTA/api.json" ;;
      sell-autonomia_clamav)
        if [[ "${4:-}" == "--format" ]]; then echo "$REDE_DO_CLAMAV"; else echo "[]"; fi ;;
      sell-autonomia_asset-worker)
        if [[ "$WORKER_EXISTE" == "1" ]]; then echo "[]"
        else echo "no such service" >&2; exit 1; fi ;;
      *) exit 1 ;;
    esac ;;
  "network inspect")
    echo "$3" >> "$PASTA/redes-inspecionadas.txt"
    case "$REDE" in
      ausente) echo "Error: No such network: $3" >&2; exit 1 ;;
      certa) echo "$REDE_DO_STORAGE overlay true swarm" ;;
      sem-internal) echo "$REDE_DO_STORAGE overlay false swarm" ;;
      local) echo "$REDE_DO_STORAGE bridge true local" ;;
      a-do-clamav) echo "$REDE_DO_CLAMAV overlay true swarm" ;;
    esac ;;
  "service create")
    shift 2
    printf '%s\\n' "$@" > "$PASTA/create.txt" ;;
  *) echo "docker falso não conhece: $*" >&2; exit 1 ;;
esac
"""


@pytest.fixture
def pasta(tmp_path: Path) -> Path:
    if shutil.which("jq") is None:
        pytest.fail("jq é exigido pelo ensure-asset-worker.sh e precisa estar no PATH")
    binarios = tmp_path / "bin"
    binarios.mkdir()
    docker = binarios / "docker"
    docker.write_text(_DOCKER_FALSO)
    docker.chmod(0o755)
    (tmp_path / "api.json").write_text(json.dumps(_SPEC_DA_API))
    return tmp_path


def rodar(
    pasta: Path, *, rede: str, worker_existe: bool = False, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    ambiente = {
        "PATH": f"{pasta / 'bin'}:{os.environ['PATH']}",
        "PASTA": str(pasta),
        "REDE": rede,
        "WORKER_EXISTE": "1" if worker_existe else "0",
        "REDE_DO_CLAMAV": _REDE_DO_CLAMAV,
        "REDE_DO_STORAGE": _REDE_DO_STORAGE,
        "CONFIRM_APPSSELL_ASSET_WORKER": "appsell-asset-worker-v1",
        **(extra or {}),
    }
    return subprocess.run(  # noqa: S603
        [str(_BASH), str(_SCRIPT)], env=ambiente, capture_output=True, text=True, check=False
    )


def argumentos(pasta: Path) -> list[str]:
    return (pasta / "create.txt").read_text().splitlines()


def redes(args: list[str]) -> list[str]:
    return [args[i + 1] for i, arg in enumerate(args) if arg == "--network"]


def montagens(args: list[str]) -> list[str]:
    return [args[i + 1] for i, arg in enumerate(args) if arg == "--mount"]


def test_o_worker_entra_na_rede_do_clamav_e_na_do_storage(pasta: Path) -> None:
    resultado = rodar(pasta, rede="certa")
    assert resultado.returncode == 0, resultado.stderr
    assert redes(argumentos(pasta)) == [_REDE_DO_CLAMAV, _REDE_DO_STORAGE]


def test_os_sockets_do_clamav_e_do_pdf_sandbox_continuam_iguais(pasta: Path) -> None:
    resultado = rodar(pasta, rede="certa")
    assert resultado.returncode == 0, resultado.stderr
    assert montagens(argumentos(pasta)) == [
        "type=volume,source=vol-clamav,destination=/run/appsell-clamav,readonly=true",
        "type=volume,source=vol-pdf,destination=/run/appsell-pdf,readonly=true",
        "type=tmpfs,destination=/tmp,tmpfs-size=2147483648,tmpfs-mode=1777",
    ]


def test_sem_a_rede_do_storage_falha_sem_criar_nada(pasta: Path) -> None:
    resultado = rodar(pasta, rede="ausente")
    assert resultado.returncode == 1
    assert "storage internal network not found: sell-storage-internal" in resultado.stderr
    assert not (pasta / "create.txt").exists()


@pytest.mark.parametrize("rede", ["sem-internal", "local"])
def test_rede_do_storage_que_nao_e_overlay_interna_falha(pasta: Path, rede: str) -> None:
    resultado = rodar(pasta, rede=rede)
    assert resultado.returncode == 1
    assert "must be an internal swarm overlay" in resultado.stderr
    assert not (pasta / "create.txt").exists()


def test_a_mesma_rede_nao_entra_duas_vezes(pasta: Path) -> None:
    resultado = rodar(pasta, rede="a-do-clamav")
    assert resultado.returncode == 0, resultado.stderr
    assert redes(argumentos(pasta)) == [_REDE_DO_CLAMAV]


def test_rodar_de_novo_nao_cria_outro_worker(pasta: Path) -> None:
    resultado = rodar(pasta, rede="certa", worker_existe=True)
    assert resultado.returncode == 1
    assert "worker service already exists" in resultado.stderr
    assert not (pasta / "create.txt").exists()


@pytest.mark.parametrize(
    "variavel", ["APPSSELL_STORAGE_INTERNAL_NETWORK", "APPSELL_STORAGE_INTERNAL_NETWORK"]
)
def test_a_rede_do_storage_nao_aceita_override_pelo_ambiente(pasta: Path, variavel: str) -> None:
    resultado = rodar(pasta, rede="certa", extra={variavel: "outra-rede"})
    assert resultado.returncode == 0, resultado.stderr
    inspecionadas = (pasta / "redes-inspecionadas.txt").read_text().split()
    assert inspecionadas == ["sell-storage-internal"]
