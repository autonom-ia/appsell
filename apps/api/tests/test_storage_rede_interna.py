"""O storage-rede-interna.sh para, desfaz e não volta para spec errada (#461).

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
REDE = "sell-storage-internal"


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
elif comando == "network create":
    nome = args[-1]
    estado["redes"][nome] = {"Id": nome, "Driver": opcao("--driver"),
                             "Internal": "--internal" in args, "Scope": "swarm"}
    salvar()
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
    if falha("ROLLBACK_STOP_FIRST", nome):
        nova["RollbackConfig"]["Order"] = "stop-first"
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
    sys.exit(1 if estado["servicos"][nome].get("quebrado") else 0)
else:
    print("docker falso não conhece: " + " ".join(args), file=sys.stderr); sys.exit(1)
"""

_SUDO_FALSO = """#!/usr/bin/env bash
[[ "$1" == "-n" ]] && shift
exec "$@"
"""


def _spec(rotulo: str, env: list[str], redes: list[str]) -> dict[str, Any]:
    return {
        "Labels": {"versao": rotulo},
        "TaskTemplate": {
            "ContainerSpec": {"Image": "imagem:teste", "Env": env, "Mounts": []},
            "Networks": [{"Target": rede} for rede in redes],
            "Resources": {},
        },
        "UpdateConfig": {"Order": "stop-first"},
        "RollbackConfig": {"Order": "stop-first"},
    }


def _servico(env: list[str], redes: list[str]) -> dict[str, Any]:
    # A PreviousSpec é de um deploy antigo, como na produção: um rollback
    # sem conferência voltaria para ela.
    return {"Spec": _spec("atual", env, redes), "PreviousSpec": _spec("antiga", env, redes)}


ENV_DA_API = [
    "APPSELL_API_ENVIRONMENT=production",
    "APPSELL_API_STORAGE_INTERNAL_ENDPOINT=https://minio.autonomia.site",
    "APPSELL_API_STORAGE_CONTROL_SECRET_KEY=s",
]


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
    estado = {
        "redes": {},
        "servicos": {
            STORAGE: _servico(["MINIO_ROOT_USER=u", "MINIO_ROOT_PASSWORD=p"], ["easypanel"]),
            API: _servico(list(ENV_DA_API), ["easypanel", "clamav"]),
            WORKER: _servico(list(ENV_DA_API), ["clamav"]),
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
    ambiente = _ambiente(pasta, **falhas)
    return subprocess.run(  # noqa: S603
        [str(_BASH), str(_SCRIPT), *argumentos],
        env=ambiente,
        capture_output=True,
        text=True,
        check=False,
    )


def estado(pasta: Path) -> dict[str, Any]:
    resultado: dict[str, Any] = json.loads((pasta / "estado.json").read_text())
    return resultado


def spec(pasta: Path, servico: str) -> dict[str, Any]:
    resultado: dict[str, Any] = estado(pasta)["servicos"][servico]["Spec"]
    return resultado


def atualizados(pasta: Path) -> list[str]:
    chamadas = (pasta / "chamadas.log").read_text().splitlines()
    return [linha.split()[-1] for linha in chamadas if linha.startswith("service update")]


def ultimo_ts(pasta: Path) -> str:
    nomes = sorted(p.name for p in (pasta / "backups").glob(f"{STORAGE}_spec_pre461_*.json"))
    return nomes[-1][len(f"{STORAGE}_spec_pre461_") : -len(".json")]


def redes(pasta: Path, servico: str) -> list[dict[str, Any]]:
    resultado: list[dict[str, Any]] = spec(pasta, servico)["TaskTemplate"]["Networks"]
    return resultado


# Etapa 4


def test_rede_poe_o_alias_so_no_storage_e_os_tres_na_rede(pasta: Path) -> None:
    resultado = rodar(pasta, "rede")
    assert resultado.returncode == 0, resultado.stderr
    assert estado(pasta)["redes"][REDE] == {
        "Id": REDE,
        "Driver": "overlay",
        "Internal": True,
        "Scope": "swarm",
    }
    assert {"Target": REDE, "Aliases": ["appsell-storage"]} in redes(pasta, STORAGE)
    assert {"Target": REDE} in redes(pasta, API)
    assert {"Target": REDE} in redes(pasta, WORKER)
    assert spec(pasta, API)["TaskTemplate"]["ContainerSpec"]["Env"] == ENV_DA_API


def test_rede_existente_sem_internal_aborta_sem_update(pasta: Path) -> None:
    atual = estado(pasta)
    atual["redes"][REDE] = {"Id": REDE, "Driver": "overlay", "Internal": False, "Scope": "swarm"}
    (pasta / "estado.json").write_text(json.dumps(atual))
    resultado = rodar(pasta, "rede")
    assert resultado.returncode == 1
    assert "não é overlay interna" in resultado.stderr
    assert atualizados(pasta) == []


def test_update_do_storage_que_falha_nao_toca_api_nem_worker(pasta: Path) -> None:
    antes = spec(pasta, STORAGE)
    resultado = rodar(pasta, "rede", FALHA_UPDATE=STORAGE)
    assert resultado.returncode == 1
    assert atualizados(pasta) == [STORAGE]
    assert spec(pasta, STORAGE) == antes
    assert "nada a desfazer" in resultado.stdout


def test_api_sem_storage_depois_do_update_volta_api_e_storage(pasta: Path) -> None:
    antes = {servico: spec(pasta, servico) for servico in (STORAGE, API, WORKER)}
    resultado = rodar(pasta, "rede", SEM_STORAGE=API)
    assert resultado.returncode == 1
    assert atualizados(pasta) == [STORAGE, API]
    for servico in (STORAGE, API, WORKER):
        assert spec(pasta, servico) == antes[servico], servico


def test_rede_de_novo_nao_atualiza_nada(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "rede")
    assert resultado.returncode == 0, resultado.stderr
    assert atualizados(pasta) == []


def test_swarm_com_dois_nos_aborta_antes_de_escrever(pasta: Path) -> None:
    resultado = rodar(pasta, "rede", NOS="2")
    assert resultado.returncode == 1
    assert "--opt encrypted" in resultado.stderr
    assert atualizados(pasta) == []
    assert estado(pasta)["redes"] == {}


# Etapa 6


def test_env_liga_a_flag_e_troca_o_endpoint(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    resultado = rodar(pasta, "env")
    assert resultado.returncode == 0, resultado.stderr
    for servico in (API, WORKER):
        env = spec(pasta, servico)["TaskTemplate"]["ContainerSpec"]["Env"]
        assert len(env) == len(ENV_DA_API) + 1
        assert "APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true" in env
        assert "APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://appsell-storage:9000" in env


def test_env_sem_a_etapa_4_aborta(pasta: Path) -> None:
    resultado = rodar(pasta, "env")
    assert resultado.returncode == 1
    assert atualizados(pasta) == []


def test_update_da_api_que_falha_nao_toca_o_worker(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "env", FALHA_UPDATE=API)
    assert resultado.returncode == 1
    assert atualizados(pasta) == [API]
    assert WORKER not in atualizados(pasta)


def test_env_zerada_aborta_e_volta_a_api(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    antes = spec(pasta, API)
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "env", ZERA_ENV=API)
    assert resultado.returncode == 1
    assert "esperado 4" in resultado.stderr
    assert spec(pasta, API) == antes
    assert atualizados(pasta) == [API]


def test_task_que_nao_sobe_volta_ao_backup(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    antes = spec(pasta, API)
    resultado = rodar(pasta, "env", NAO_SOBE=API)
    assert resultado.returncode == 1
    assert spec(pasta, API) == antes


# Desfazer


def test_desfazer_duas_vezes_volta_uma_vez_so(pasta: Path) -> None:
    antes = {servico: spec(pasta, servico) for servico in (STORAGE, API, WORKER)}
    assert rodar(pasta, "rede").returncode == 0
    ts = ultimo_ts(pasta)
    primeiro = rodar(pasta, "desfazer", ts)
    assert primeiro.returncode == 0, primeiro.stderr
    for servico in (STORAGE, API, WORKER):
        assert spec(pasta, servico) == antes[servico], servico
    segundo = rodar(pasta, "desfazer", ts)
    assert segundo.returncode == 0, segundo.stderr
    assert segundo.stdout.count("nada a desfazer") == 3
    for servico in (STORAGE, API, WORKER):
        assert spec(pasta, servico) == antes[servico], servico


def test_desfazer_depois_de_update_recusado_nao_volta_para_spec_antiga(pasta: Path) -> None:
    antes = spec(pasta, STORAGE)
    assert rodar(pasta, "rede", FALHA_UPDATE=STORAGE).returncode == 1
    resultado = rodar(pasta, "desfazer", ultimo_ts(pasta))
    assert resultado.returncode == 0, resultado.stderr
    assert spec(pasta, STORAGE) == antes
    assert spec(pasta, STORAGE)["Labels"] == {"versao": "atual"}


def test_desfazer_aborta_se_outro_deploy_entrou_no_meio(pasta: Path) -> None:
    # Um deploy do EasyPanel depois da etapa: a PreviousSpec deixa de ser o
    # backup, e um rollback cairia na spec dele.
    assert rodar(pasta, "rede").returncode == 0
    ts = ultimo_ts(pasta)
    atual = estado(pasta)
    api = atual["servicos"][API]
    api["PreviousSpec"] = api["Spec"]
    api["Spec"] = {**api["Spec"], "Labels": {"versao": "easypanel"}}
    (pasta / "estado.json").write_text(json.dumps(atual))
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "desfazer", ts)
    assert resultado.returncode == 1
    assert "falar com o Rodrigo" in resultado.stderr
    rollbacks = [
        linha
        for linha in (pasta / "chamadas.log").read_text().splitlines()
        if linha.startswith("service rollback") and linha.endswith(API)
    ]
    assert rollbacks == []
    # Parou no ABORT da api: o storage não perdeu o alias.
    assert {"Target": REDE, "Aliases": ["appsell-storage"]} in redes(pasta, STORAGE)


def gravar(pasta: Path, atual: dict[str, Any]) -> None:
    (pasta / "estado.json").write_text(json.dumps(atual))


def test_rede_poe_start_first_na_api_e_stop_first_no_storage(pasta: Path) -> None:
    # A api troca sem derrubar a loja; o MinIO nunca tem duas tasks no volume.
    assert rodar(pasta, "rede").returncode == 0
    assert spec(pasta, API)["UpdateConfig"] == {"Order": "start-first"}
    assert spec(pasta, API)["RollbackConfig"] == {"Order": "start-first"}
    assert spec(pasta, STORAGE)["UpdateConfig"] == {"Order": "stop-first"}
    assert spec(pasta, STORAGE)["RollbackConfig"] == {"Order": "stop-first"}
    assert spec(pasta, WORKER)["UpdateConfig"] == {"Order": "stop-first"}


def test_env_poe_start_first_na_api(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    assert rodar(pasta, "env").returncode == 0
    assert spec(pasta, API)["UpdateConfig"] == {"Order": "start-first"}
    assert spec(pasta, API)["RollbackConfig"] == {"Order": "start-first"}


def test_api_com_rollback_stop_first_na_spec_nova_nao_volta(pasta: Path) -> None:
    # A volta da api usa a ordem da spec atual; stop-first ali derrubaria a loja.
    resultado = rodar(pasta, "rede", SEM_STORAGE=API, ROLLBACK_STOP_FIRST=API)
    assert resultado.returncode == 1
    assert "rollback order não é start-first" in resultado.stderr
    assert rollbacks(pasta) == []


def test_worker_sem_rollback_stop_first_aborta_antes_do_backup(pasta: Path) -> None:
    atual = estado(pasta)
    atual["servicos"][WORKER]["Spec"]["RollbackConfig"]["Order"] = "start-first"
    gravar(pasta, atual)
    resultado = rodar(pasta, "rede")
    assert resultado.returncode == 1
    assert "rollback order diferente de stop-first" in resultado.stderr
    assert atualizados(pasta) == []
    assert list((pasta / "backups").iterdir()) == []


def test_sigterm_nas_leituras_do_ready_volta_api_e_worker(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    antes = {servico: spec(pasta, servico) for servico in (API, WORKER)}
    processo = subprocess.Popen(  # noqa: S603
        [str(_BASH), str(_SCRIPT), "env"],
        env=_ambiente(pasta, STORAGE461_PAUSA_LEITURAS="30"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert processo.stdout is not None
    for linha in processo.stdout:
        if linha.startswith(f"{WORKER}: env"):
            break
    processo.send_signal(signal.SIGTERM)
    _, erros = processo.communicate(timeout=20)
    assert processo.returncode == 130
    assert "restaurando" in erros
    for servico in (API, WORKER):
        assert spec(pasta, servico) == antes[servico], servico


def test_erro_do_set_e_depois_do_update_volta_a_api(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    antes = spec(pasta, API)
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "env", FALHA_LEITURA_ENV=API)
    assert resultado.returncode == 1
    assert "restaurando" in resultado.stderr
    assert spec(pasta, API) == antes
    assert atualizados(pasta) == [API]


def test_env_com_variavel_trocada_falha_pelo_nome(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    antes = spec(pasta, API)
    resultado = rodar(pasta, "env", TROCA_NOME=API)
    assert resultado.returncode == 1
    assert "sumiram as variáveis APPSELL_API_ENVIRONMENT" in resultado.stderr
    assert spec(pasta, API) == antes


def test_desfazer_da_etapa_4_com_a_6_no_ar_para_no_primeiro_abort(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    ts_da_4 = ultimo_ts(pasta)
    time.sleep(1.1)  # o ts tem resolução de segundo
    assert rodar(pasta, "env").returncode == 0
    depois_da_6 = {servico: spec(pasta, servico) for servico in (STORAGE, API, WORKER)}
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "desfazer", ts_da_4)
    assert resultado.returncode == 1
    assert f"restauração parada em {WORKER}" in resultado.stderr
    chamadas = (pasta / "chamadas.log").read_text().splitlines()
    assert [linha for linha in chamadas if linha.startswith("service rollback")] == []
    for servico in (STORAGE, API, WORKER):
        assert spec(pasta, servico) == depois_da_6[servico], servico


def test_storage_nao_volta_enquanto_a_api_usa_o_alias(pasta: Path) -> None:
    atual = estado(pasta)
    for qual in ("Spec", "PreviousSpec"):
        env = atual["servicos"][API][qual]["TaskTemplate"]["ContainerSpec"]["Env"]
        env[1] = "APPSELL_API_STORAGE_INTERNAL_ENDPOINT=http://appsell-storage:9000"
    gravar(pasta, atual)
    assert rodar(pasta, "rede").returncode == 0
    resultado = rodar(pasta, "desfazer", ultimo_ts(pasta))
    assert resultado.returncode == 1
    assert f"{API} ainda usa appsell-storage" in resultado.stderr
    assert {"Target": REDE, "Aliases": ["appsell-storage"]} in redes(pasta, STORAGE)


@pytest.mark.parametrize("ts", ["../../etc/x", "20261023T1200Z", "", "20261023T120000Z;id"])
def test_desfazer_recusa_ts_fora_do_formato(pasta: Path, ts: str) -> None:
    resultado = rodar(pasta, "desfazer", ts)
    assert resultado.returncode != 0
    assert (
        not (pasta / "chamadas.log").exists()
        or "service rollback" not in (pasta / "chamadas.log").read_text()
    )


def rollbacks(pasta: Path) -> list[str]:
    chamadas = (pasta / "chamadas.log").read_text().splitlines()
    return [linha.split()[-1] for linha in chamadas if linha.startswith("service rollback")]


def test_desfazer_volta_worker_api_e_storage_nesta_ordem(pasta: Path) -> None:
    assert rodar(pasta, "rede").returncode == 0
    (pasta / "chamadas.log").unlink()
    assert rodar(pasta, "desfazer", ultimo_ts(pasta)).returncode == 0
    assert rollbacks(pasta) == [WORKER, API, STORAGE]


def test_falha_no_meio_volta_do_ultimo_tocado_para_o_primeiro(pasta: Path) -> None:
    resultado = rodar(pasta, "rede", SEM_STORAGE=WORKER)
    assert resultado.returncode == 1
    assert rollbacks(pasta) == [WORKER, API, STORAGE]


def test_restauracao_para_no_primeiro_abort_e_nao_toca_os_anteriores(pasta: Path) -> None:
    # O worker falha e não pode voltar (rollback start-first na spec nova):
    # api e storage, tocados antes, ficam como estão para o Rodrigo decidir.
    resultado = rodar(pasta, "rede", SEM_STORAGE=WORKER, ROLLBACK_START_FIRST=WORKER)
    assert resultado.returncode == 1
    assert "rollback order não é stop-first" in resultado.stderr
    assert "a restauração parou" in resultado.stderr
    assert rollbacks(pasta) == []


def test_restaura_mesmo_com_stderr_que_nao_aceita_escrita(pasta: Path) -> None:
    # Como um SSH que caiu: toda escrita no stderr falha (EPIPE, com o SIGPIPE
    # ignorado, herdado do Python). Sem o `set +e` do ao_sair, a primeira
    # mensagem dele derrubaria a restauração.
    assert rodar(pasta, "rede").returncode == 0
    antes = spec(pasta, API)
    leitura, escrita = os.pipe()
    os.close(leitura)
    try:
        resultado = subprocess.run(  # noqa: S603
            [str(_BASH), str(_SCRIPT), "env"],
            env=_ambiente(pasta, ZERA_ENV=API),
            stdout=subprocess.PIPE,
            stderr=escrita,
            text=True,
            check=False,
            restore_signals=False,
        )
    finally:
        os.close(escrita)
    assert resultado.returncode == 1
    assert spec(pasta, API) == antes
    assert f"{API}: restaurado, igual ao backup" in resultado.stdout
