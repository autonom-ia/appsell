"""O storage-rede-interna.sh confere o alias, para, desfaz e não volta para spec errada (#461).

Roda o script de verdade contra um `docker` falso com estado: cada serviço
tem Spec e PreviousSpec, o update guarda a anterior e o rollback troca as
duas, como o Swarm. As falhas entram pelo ambiente. Nada toca Docker real.
"""

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

_RAIZ = Path(__file__).resolve().parents[3]
_SCRIPT = _RAIZ / "deploy/easypanel-native/storage-rede-interna.sh"

STORAGE = "sell-autonomia_storage"
API = "sell-autonomia_api"
WORKER = "sell-autonomia_asset-worker"
REDE_DO_PROJETO = "rede-projeto-id"
REDE_COMPARTILHADA = "rede-easypanel-id"
VIP = "10.0.5.7"
ALIAS = "sell-autonomia-storage"


def _bash_moderno() -> str | None:
    """O script usa recursos do bash 4. O do macOS é 3.2; o host e o CI, 5."""
    for candidato in ("bash", "/opt/homebrew/bin/bash", "/usr/local/bin/bash", "/usr/bin/bash"):
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
_FERRAMENTAS = all(shutil.which(nome) for nome in ("jq", "sha256sum", "join"))
_PODE_RODAR = _BASH is not None and _FERRAMENTAS
# No CI a falta de ferramenta é falha, não skip: um teste pulado em silêncio
# deixaria o script sem prova.
pytestmark = pytest.mark.skipif(
    not _PODE_RODAR and not os.environ.get("CI"),
    reason="pede bash 4, jq, sha256sum e join (o macOS não tem); roda no CI e no host",
)

# O docker falso: lê e grava o estado num JSON, e entende só o que o script usa.
_DOCKER_FALSO = r"""#!/usr/bin/env python3
import copy, json, os, sys
PASTA = os.environ["PASTA"]
ESTADO = os.path.join(PASTA, "estado.json")
estado = json.load(open(ESTADO))
args = sys.argv[1:]
with open(os.path.join(PASTA, "chamadas.log"), "a") as log:
    log.write(" ".join(args) + "\n")

def salvar():
    json.dump(estado, open(ESTADO, "w"))

def valor(obj, caminho):
    for parte in caminho.strip(".").split("."):
        if obj is None:
            return None
        obj = obj.get(parte)
    return obj

def avaliar(fmt, obj):
    saida, i = "", 0
    while (j := fmt.find("{{", i)) >= 0:
        saida += fmt[i:j]
        k = fmt.index("}}", j)
        acao = fmt[j + 2:k].strip()
        if acao.startswith("with "):
            fim = fmt.index("{{end}}", k)
            dentro = valor(obj, acao[5:])
            if dentro is not None:
                saida += avaliar(fmt[k + 2:fim], dentro)
            i = fim + len("{{end}}")
            continue
        if acao.startswith("json "):
            saida += json.dumps(valor(obj, acao[5:]), separators=(",", ":"))
        else:
            saida += str(valor(obj, acao))
        i = k + 2
    return saida + fmt[i:]

def opcao(nome):
    return args[args.index(nome) + 1] if nome in args else None

def falha(chave, servico):
    return servico in os.environ.get(chave, "").split(",")

comando = " ".join(args[:2])
if comando == "node ls":
    print("\n".join(f"no-{n}" for n in range(int(os.environ.get("NOS", "1")))))
elif comando == "network inspect":
    rede = estado["redes"].get(args[2])
    if rede is None:
        print(f"Error: No such network: {args[2]}", file=sys.stderr); sys.exit(1)
    print(avaliar(opcao("--format"), rede) if "--format" in args else json.dumps([rede]))
elif comando == "service inspect":
    servico = estado["servicos"][args[2]]
    # Uma leitura da env falha uma vez depois do update: erro do `set -e`.
    leitura_da_env = "{{json .Spec.TaskTemplate.ContainerSpec.Env}}"
    if servico.get("ler_env_falha") and opcao("--format") == leitura_da_env:
        servico["ler_env_falha"] = False
        salvar()
        print("erro de leitura", file=sys.stderr); sys.exit(1)
    print(avaliar(opcao("--format"), servico) if "--format" in args else json.dumps([servico]))
elif comando == "service update":
    nome = args[-1]
    if falha("FALHA_UPDATE", nome):
        print("update recusado", file=sys.stderr); sys.exit(1)
    servico = estado["servicos"][nome]
    nova = copy.deepcopy(servico["Spec"])
    for i, arg in enumerate(args):
        alvo = args[i + 1] if i + 1 < len(args) else ""
        if arg == "--update-order":
            nova["UpdateConfig"]["Order"] = alvo
        elif arg == "--rollback-order":
            nova["RollbackConfig"]["Order"] = alvo
        elif arg == "--network-add":
            partes = {"name": alvo}
            if "=" in alvo:
                partes = dict(p.split("=", 1) for p in alvo.split(","))
            rede = {"Target": partes["name"]}
            if "alias" in partes:
                rede["Aliases"] = [partes["alias"]]
            nova["TaskTemplate"]["Networks"].append(rede)
        elif arg == "--env-add":
            chave = alvo.split("=", 1)[0]
            container = nova["TaskTemplate"]["ContainerSpec"]
            env = [e for e in container["Env"] if e.split("=", 1)[0] != chave]
            container["Env"] = env + [alvo]
    if falha("ZERA_ENV", nome):
        nova["TaskTemplate"]["ContainerSpec"]["Env"] = []
    if falha("ROLLBACK_START_FIRST", nome):
        nova["RollbackConfig"]["Order"] = "start-first"
    if falha("TROCA_NOME", nome):
        nova["TaskTemplate"]["ContainerSpec"]["Env"][0] = "OUTRA_VARIAVEL=x"
    servico["ler_env_falha"] = falha("FALHA_LEITURA_ENV", nome)
    servico["PreviousSpec"], servico["Spec"] = servico["Spec"], nova
    servico["quebrado"] = falha("SEM_STORAGE", nome) or falha("NAO_SOBE", nome)
    servico["parado"] = falha("NAO_SOBE", nome)
    salvar()
elif comando == "service rollback":
    servico = estado["servicos"][args[-1]]
    if servico.get("PreviousSpec") is None:
        print("no previous spec", file=sys.stderr); sys.exit(1)
    servico["Spec"], servico["PreviousSpec"] = servico["PreviousSpec"], servico["Spec"]
    servico["quebrado"] = servico["parado"] = False
    salvar()
elif comando == "service ps":
    nome = args[3] if args[2] == "-q" else args[2]
    if args[2] == "-q":
        print(f"task-{nome}")
    elif not estado["servicos"][nome].get("parado"):
        print("Running 5 seconds ago")
elif args[0] == "inspect":
    print(json.dumps("cid-" + args[1][len("task-"):]))
elif args[0] == "exec":
    nome = args[1][len("cid-"):]
    if args[2] == "getent":
        ips = estado["dns"].get(nome, [])
        print("\n".join(f"{ip}      {args[-1]}" for ip in ips))
        sys.exit(0 if ips else 2)
    sys.exit(1 if estado["servicos"][nome].get("quebrado") else 0)
else:
    print("docker falso não conhece: " + " ".join(args), file=sys.stderr); sys.exit(1)
"""

_SUDO_FALSO = """#!/usr/bin/env bash
[[ "$1" == "-n" ]] && shift
exec "$@"
"""


def _spec(rotulo: str, env: list[str], redes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "Labels": {"versao": rotulo},
        "TaskTemplate": {
            "ContainerSpec": {
                "Image": "imagem:teste",
                "Env": env,
                "Mounts": [],
                "StopGracePeriod": 330000000000,
            },
            "Networks": redes,
            "Resources": {},
        },
        "UpdateConfig": {"Order": "stop-first"},
        "RollbackConfig": {"Order": "stop-first"},
    }


def _servico(env: list[str], redes: list[dict[str, Any]]) -> dict[str, Any]:
    # A PreviousSpec é de um deploy antigo, como na produção: um rollback
    # sem conferência voltaria para ela.
    return {"Spec": _spec("atual", env, redes), "PreviousSpec": _spec("antiga", env, redes)}


ENV_DA_API = [
    "APPSELL_API_ENVIRONMENT=production",
    "APPSELL_API_STORAGE_INTERNAL_ENDPOINT=https://minio.autonomia.site",
    "APPSELL_API_STORAGE_CONTROL_SECRET_KEY=segredo-de-teste",
]
FLAG = "APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true"
ENDPOINT = "APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://sell-autonomia-storage:9000"


@pytest.fixture
def pasta(tmp_path: Path) -> Path:
    if not _PODE_RODAR:
        pytest.fail("o CI precisa de bash 4, jq, sha256sum e join para este teste")
    binarios = tmp_path / "bin"
    binarios.mkdir()
    for nome, conteudo in (("docker", _DOCKER_FALSO), ("sudo", _SUDO_FALSO)):
        arquivo = binarios / nome
        arquivo.write_text(conteudo)
        arquivo.chmod(0o755)
    (tmp_path / "backups").mkdir()
    # Como na produção (24/10/2026): o storage com o alias na rede do projeto
    # e sem alias na compartilhada; a api nas duas; o worker só na do projeto.
    storage = _servico(
        ["MINIO_ROOT_USER=u", "MINIO_ROOT_PASSWORD=p"],
        [
            {"Target": REDE_DO_PROJETO, "Aliases": [ALIAS, "storage"]},
            {"Target": REDE_COMPARTILHADA},
        ],
    )
    storage["Endpoint"] = {
        "VirtualIPs": [
            {"NetworkID": REDE_DO_PROJETO, "Addr": f"{VIP}/24"},
            {"NetworkID": REDE_COMPARTILHADA, "Addr": "10.0.1.9/24"},
        ]
    }
    estado = {
        "redes": {},
        "dns": {API: [VIP], WORKER: [VIP]},
        "servicos": {
            STORAGE: storage,
            API: _servico(
                list(ENV_DA_API), [{"Target": REDE_COMPARTILHADA}, {"Target": REDE_DO_PROJETO}]
            ),
            WORKER: _servico(list(ENV_DA_API), [{"Target": REDE_DO_PROJETO}]),
        },
    }
    (tmp_path / "estado.json").write_text(json.dumps(estado))
    return tmp_path


def _ambiente(pasta: Path, **falhas: str) -> dict[str, str]:
    return {
        "PATH": f"{pasta / 'bin'}:{os.environ['PATH']}",
        "PASTA": str(pasta),
        "STORAGE461_PASTA": str(pasta / "backups"),
        "STORAGE461_PRAZO_SEGUNDOS": "2",
        "STORAGE461_INTERVALO_SEGUNDOS": "0",
        "STORAGE461_PAUSA_LEITURAS": "0",
        **falhas,
    }


def rodar(pasta: Path, *argumentos: str, **falhas: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(_BASH), str(_SCRIPT), *argumentos],
        env=_ambiente(pasta, **falhas),
        capture_output=True,
        text=True,
        check=False,
    )


def estado(pasta: Path) -> dict[str, Any]:
    resultado: dict[str, Any] = json.loads((pasta / "estado.json").read_text())
    return resultado


def gravar(pasta: Path, atual: dict[str, Any]) -> None:
    (pasta / "estado.json").write_text(json.dumps(atual))


def spec(pasta: Path, servico: str) -> dict[str, Any]:
    resultado: dict[str, Any] = estado(pasta)["servicos"][servico]["Spec"]
    return resultado


def env(pasta: Path, servico: str) -> list[str]:
    resultado: list[str] = spec(pasta, servico)["TaskTemplate"]["ContainerSpec"]["Env"]
    return resultado


def chamadas(pasta: Path) -> list[str]:
    log = pasta / "chamadas.log"
    return log.read_text().splitlines() if log.exists() else []


def atualizados(pasta: Path) -> list[str]:
    return [linha.split()[-1] for linha in chamadas(pasta) if linha.startswith("service update")]


def rollbacks(pasta: Path) -> list[str]:
    return [linha.split()[-1] for linha in chamadas(pasta) if linha.startswith("service rollback")]


def backups(pasta: Path) -> list[str]:
    return sorted(p.name for p in (pasta / "backups").iterdir())


def ultimo_ts(pasta: Path, servico: str) -> str:
    nomes = sorted(p.name for p in (pasta / "backups").glob(f"{servico}_spec_pre461_*.json"))
    return nomes[-1][len(f"{servico}_spec_pre461_") : -len(".json")]


def painel(pasta: Path, nova_env: list[str], **outros: Any) -> None:
    """O deploy do EasyPanel: a spec nova e a de antes vira a PreviousSpec."""
    atual = estado(pasta)
    api = atual["servicos"][API]
    nova = json.loads(json.dumps(api["Spec"]))
    nova["TaskTemplate"]["ContainerSpec"]["Env"] = nova_env
    nova["TaskTemplate"]["ContainerSpec"].update(outros)
    api["PreviousSpec"], api["Spec"] = api["Spec"], nova
    gravar(pasta, atual)


ENV_DO_PAINEL = [ENV_DA_API[0], ENDPOINT, ENV_DA_API[2], FLAG]


# Worker


def test_worker_liga_a_flag_e_troca_o_endpoint_so_nele(pasta: Path) -> None:
    antes = {servico: spec(pasta, servico) for servico in (STORAGE, API)}
    resultado = rodar(pasta, "worker")
    assert resultado.returncode == 0, resultado.stderr
    assert atualizados(pasta) == [WORKER]
    assert len(env(pasta, WORKER)) == len(ENV_DA_API) + 1
    assert FLAG in env(pasta, WORKER)
    assert ENDPOINT in env(pasta, WORKER)
    for servico in (STORAGE, API):
        assert spec(pasta, servico) == antes[servico], servico
    # A ordem de update do worker fica a da spec.
    assert spec(pasta, WORKER)["UpdateConfig"] == {"Order": "stop-first"}


def test_worker_de_novo_nao_atualiza_nada(pasta: Path) -> None:
    assert rodar(pasta, "worker").returncode == 0
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "worker")
    assert resultado.returncode == 0, resultado.stderr
    assert "nada a fazer" in resultado.stdout
    assert atualizados(pasta) == []


def test_o_script_nao_cria_nem_mexe_em_rede(pasta: Path) -> None:
    assert rodar(pasta, "worker").returncode == 0
    assert not any(linha.startswith("network ") for linha in chamadas(pasta))
    assert not any("--network" in linha for linha in chamadas(pasta))


def _sem_escrita(pasta: Path, resultado: subprocess.CompletedProcess[str], motivo: str) -> None:
    assert resultado.returncode == 1
    assert motivo in resultado.stderr
    assert atualizados(pasta) == []
    assert backups(pasta) == []


def test_worker_fora_da_rede_do_alias_aborta_sem_escrever(pasta: Path) -> None:
    atual = estado(pasta)
    atual["servicos"][WORKER]["Spec"]["TaskTemplate"]["Networks"] = [{"Target": REDE_COMPARTILHADA}]
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "worker"), "fora da rede do alias")


@pytest.mark.parametrize(
    "resolve", [["10.0.9.9"], [VIP, "10.0.9.9"], []], ids=["outro-ip", "dois-ips", "nao-resolve"]
)
def test_alias_que_nao_resolve_so_para_a_vip_aborta_sem_escrever(
    pasta: Path, resolve: list[str]
) -> None:
    # Outro serviço respondendo pelo nome, ou nome que não existe.
    atual = estado(pasta)
    atual["dns"][WORKER] = resolve
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "worker"), "não vê o storage pelo alias")


def test_alias_em_duas_redes_do_storage_aborta_sem_escrever(pasta: Path) -> None:
    atual = estado(pasta)
    atual["servicos"][STORAGE]["Spec"]["TaskTemplate"]["Networks"][1]["Aliases"] = [ALIAS]
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "worker"), "exatamente uma rede")


def test_swarm_com_dois_nos_aborta_sem_escrever(pasta: Path) -> None:
    _sem_escrita(pasta, rodar(pasta, "worker", NOS="2"), "o Swarm tem 2 nós")


def test_worker_sem_rollback_stop_first_aborta_antes_do_backup(pasta: Path) -> None:
    atual = estado(pasta)
    atual["servicos"][WORKER]["Spec"]["RollbackConfig"]["Order"] = "start-first"
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "worker"), "rollback order diferente de stop-first")


def test_update_recusado_nao_volta_para_a_spec_antiga(pasta: Path) -> None:
    antes = spec(pasta, WORKER)
    resultado = rodar(pasta, "worker", FALHA_UPDATE=WORKER)
    assert resultado.returncode == 1
    assert "nada a desfazer" in resultado.stdout
    assert spec(pasta, WORKER) == antes
    assert rollbacks(pasta) == []


@pytest.mark.parametrize("falha", ["SEM_STORAGE", "NAO_SOBE", "ZERA_ENV", "FALHA_LEITURA_ENV"])
def test_falha_depois_do_update_volta_o_worker(pasta: Path, falha: str) -> None:
    antes = spec(pasta, WORKER)
    resultado = rodar(pasta, "worker", **{falha: WORKER})
    assert resultado.returncode == 1
    assert "restaurando" in resultado.stderr
    assert spec(pasta, WORKER) == antes
    assert rollbacks(pasta) == [WORKER]


def test_env_zerada_falha_pela_contagem(pasta: Path) -> None:
    resultado = rodar(pasta, "worker", ZERA_ENV=WORKER)
    assert resultado.returncode == 1
    assert "esperado 4" in resultado.stderr


def test_variavel_trocada_falha_pelo_nome(pasta: Path) -> None:
    antes = spec(pasta, WORKER)
    resultado = rodar(pasta, "worker", TROCA_NOME=WORKER)
    assert resultado.returncode == 1
    assert "sumiram as variáveis APPSELL_API_ENVIRONMENT" in resultado.stderr
    assert spec(pasta, WORKER) == antes


def test_worker_com_rollback_order_trocado_pelo_update_nao_volta(pasta: Path) -> None:
    resultado = rodar(pasta, "worker", SEM_STORAGE=WORKER, ROLLBACK_START_FIRST=WORKER)
    assert resultado.returncode == 1
    assert "rollback order não é stop-first" in resultado.stderr
    assert "a restauração parou" in resultado.stderr
    assert rollbacks(pasta) == []


def test_sigterm_durante_a_conferencia_volta_o_worker(pasta: Path) -> None:
    antes = spec(pasta, WORKER)
    processo = subprocess.Popen(  # noqa: S603
        [str(_BASH), str(_SCRIPT), "worker"],
        env=_ambiente(
            pasta,
            SEM_STORAGE=WORKER,
            STORAGE461_PRAZO_SEGUNDOS="60",
            STORAGE461_INTERVALO_SEGUNDOS="30",
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    limite = time.monotonic() + 15
    while WORKER not in atualizados(pasta):
        assert time.monotonic() < limite, "o update não aconteceu"
        time.sleep(0.05)
    time.sleep(0.3)
    processo.send_signal(signal.SIGTERM)
    _, erros = processo.communicate(timeout=20)
    assert processo.returncode == 130
    assert "restaurando" in erros
    assert spec(pasta, WORKER) == antes


def test_restaura_mesmo_com_stderr_que_nao_aceita_escrita(pasta: Path) -> None:
    # Como um SSH que caiu: toda escrita no stderr falha (EPIPE, com o SIGPIPE
    # ignorado, herdado do Python). Sem o `set +e` do ao_sair, a primeira
    # mensagem dele derrubaria a restauração.
    antes = spec(pasta, WORKER)
    leitura, escrita = os.pipe()
    os.close(leitura)
    try:
        resultado = subprocess.run(  # noqa: S603
            [str(_BASH), str(_SCRIPT), "worker"],
            env=_ambiente(pasta, ZERA_ENV=WORKER),
            stdout=subprocess.PIPE,
            stderr=escrita,
            text=True,
            check=False,
            restore_signals=False,
        )
    finally:
        os.close(escrita)
    assert resultado.returncode == 1
    assert spec(pasta, WORKER) == antes
    assert f"{WORKER}: restaurado, igual ao backup" in resultado.stdout


# Api: o painel muda, o script fotografa antes e confere depois


def test_api_antes_so_guarda_o_backup(pasta: Path) -> None:
    antes = spec(pasta, API)
    resultado = rodar(pasta, "api-antes")
    assert resultado.returncode == 0, resultado.stderr
    assert atualizados(pasta) == []
    assert spec(pasta, API) == antes
    ts = ultimo_ts(pasta, API)
    assert backups(pasta) == [f"{API}_spec_pre461_{ts}.digital", f"{API}_spec_pre461_{ts}.json"]
    assert "endpoint_interno=minio.autonomia.site" in resultado.stdout
    assert "segredo-de-teste" not in resultado.stdout + resultado.stderr


def test_api_antes_sem_o_alias_aborta(pasta: Path) -> None:
    atual = estado(pasta)
    atual["dns"][API] = ["10.0.9.9"]
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "api-antes"), "não vê o storage pelo alias")


def test_api_depois_aceita_o_painel_que_so_mudou_a_env(pasta: Path) -> None:
    assert rodar(pasta, "api-antes").returncode == 0
    ts = ultimo_ts(pasta, API)
    painel(pasta, list(ENV_DO_PAINEL))
    resultado = rodar(pasta, "api-depois", ts)
    assert resultado.returncode == 0, resultado.stderr
    assert "env 3 → 4" in resultado.stdout
    assert atualizados(pasta) == []
    assert rollbacks(pasta) == []


@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("Image", "easypanel/sell-autonomia/api:latest"),
        ("StopGracePeriod", 10000000000),
        ("Mounts", None),
    ],
)
def test_api_depois_recusa_o_painel_que_mudou_outro_campo(
    pasta: Path, campo: str, valor: Any
) -> None:
    # Um deploy pelo painel que reconstrói do fonte troca a imagem, a graça
    # de 330 s ou os mounts que entraram por fora dele.
    assert rodar(pasta, "api-antes").returncode == 0
    ts = ultimo_ts(pasta, API)
    painel(pasta, list(ENV_DO_PAINEL), **{campo: valor})
    resultado = rodar(pasta, "api-depois", ts)
    assert resultado.returncode == 1
    assert f"o painel mudou mais que a env: TaskTemplate.ContainerSpec.{campo}" in resultado.stderr
    assert f"desfazer {ts}" in resultado.stderr


@pytest.mark.parametrize(
    ("nova_env", "motivo"),
    [
        ([ENV_DA_API[0], ENDPOINT, ENV_DA_API[2]], "esperado 4"),
        ([ENV_DA_API[0], ENV_DA_API[1], ENV_DA_API[2], FLAG], "sem a flag e o endpoint"),
        (
            [ENDPOINT, ENV_DA_API[2], FLAG, "OUTRA=1"],
            "sumiram as variáveis APPSELL_API_ENVIRONMENT",
        ),
        (
            [ENV_DA_API[0], ENDPOINT, ENV_DA_API[2], "APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=1"],
            "sem a flag e o endpoint",
        ),
    ],
    ids=["faltou-a-flag", "endpoint-velho", "variavel-trocada", "flag-com-outro-valor"],
)
def test_api_depois_recusa_env_errada_e_manda_ao_painel(
    pasta: Path, nova_env: list[str], motivo: str
) -> None:
    assert rodar(pasta, "api-antes").returncode == 0
    ts = ultimo_ts(pasta, API)
    painel(pasta, nova_env)
    resultado = rodar(pasta, "api-depois", ts)
    assert resultado.returncode == 1
    assert motivo in resultado.stderr
    assert "rollback no painel" in resultado.stderr
    assert "segredo-de-teste" not in resultado.stdout + resultado.stderr


def test_api_depois_sem_storage_manda_ao_painel(pasta: Path) -> None:
    assert rodar(pasta, "api-antes").returncode == 0
    ts = ultimo_ts(pasta, API)
    painel(pasta, list(ENV_DO_PAINEL))
    atual = estado(pasta)
    atual["servicos"][API]["quebrado"] = True
    gravar(pasta, atual)
    resultado = rodar(pasta, "api-depois", ts)
    assert resultado.returncode == 1
    assert "não vê o storage; rollback no painel" in resultado.stderr


# Desfazer


def test_desfazer_do_worker_duas_vezes_volta_uma_vez_so(pasta: Path) -> None:
    antes = spec(pasta, WORKER)
    assert rodar(pasta, "worker").returncode == 0
    ts = ultimo_ts(pasta, WORKER)
    primeiro = rodar(pasta, "desfazer", ts)
    assert primeiro.returncode == 0, primeiro.stderr
    assert spec(pasta, WORKER) == antes
    segundo = rodar(pasta, "desfazer", ts)
    assert segundo.returncode == 0, segundo.stderr
    assert "nada a desfazer" in segundo.stdout
    assert rollbacks(pasta) == [WORKER]


def test_desfazer_da_api_volta_a_spec_de_antes_do_painel(pasta: Path) -> None:
    antes = spec(pasta, API)
    assert rodar(pasta, "api-antes").returncode == 0
    ts = ultimo_ts(pasta, API)
    painel(pasta, list(ENV_DO_PAINEL), Image="easypanel/sell-autonomia/api:latest")
    resultado = rodar(pasta, "desfazer", ts)
    assert resultado.returncode == 0, resultado.stderr
    assert spec(pasta, API) == antes
    assert rollbacks(pasta) == [API]


def test_desfazer_aborta_se_outro_deploy_entrou_no_meio(pasta: Path) -> None:
    # Dois deploys do painel depois do backup: a PreviousSpec deixa de ser o
    # backup, e um rollback cairia na spec do primeiro deles.
    assert rodar(pasta, "api-antes").returncode == 0
    ts = ultimo_ts(pasta, API)
    painel(pasta, list(ENV_DO_PAINEL))
    painel(pasta, list(ENV_DO_PAINEL), Image="easypanel/sell-autonomia/api:latest")
    resultado = rodar(pasta, "desfazer", ts)
    assert resultado.returncode == 1
    assert "falar com o Rodrigo" in resultado.stderr
    assert rollbacks(pasta) == []


def test_desfazer_sem_backup_desse_ts_aborta(pasta: Path) -> None:
    resultado = rodar(pasta, "desfazer", "20261024T120000Z")
    assert resultado.returncode == 1
    assert "nenhum backup com ts" in resultado.stderr
    assert rollbacks(pasta) == []


@pytest.mark.parametrize("ts", ["../../etc/x", "20261023T1200Z", "", "20261023T120000Z;id"])
@pytest.mark.parametrize("modo", ["desfazer", "api-depois"])
def test_ts_fora_do_formato_e_recusado(pasta: Path, modo: str, ts: str) -> None:
    resultado = rodar(pasta, modo, ts)
    assert resultado.returncode != 0
    assert "ts inválido" in resultado.stderr
    assert chamadas(pasta) == []


# Seco


def test_seco_so_le_e_nao_imprime_valor(pasta: Path) -> None:
    resultado = rodar(pasta, "seco")
    assert resultado.returncode == 0, resultado.stderr
    assert atualizados(pasta) == []
    assert backups(pasta) == []
    assert f"{WORKER}: env=3 endpoint_interno=minio.autonomia.site no_alvo=não" in resultado.stdout
    assert "alias=ok" in resultado.stdout
    assert "segredo-de-teste" not in resultado.stdout + resultado.stderr
