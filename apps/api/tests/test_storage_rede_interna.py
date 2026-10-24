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
    if not caminho.strip("."):
        return obj
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
        elif arg in ("--env-add", "--env-rm"):
            chave = alvo.split("=", 1)[0]
            container = nova["TaskTemplate"]["ContainerSpec"]
            env = [e for e in container["Env"] if e.split("=", 1)[0] != chave]
            # Como o docker, o --env-add não preserva a ordem da lista.
            container["Env"] = [alvo] + env if arg == "--env-add" else env
    # As falhas valem só no update da janela, o que liga a flag; a volta é
    # outro update, que precisa funcionar.
    janela = "APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=true" in args
    def falha_da_janela(chave):
        return janela and falha(chave, nome)
    if falha_da_janela("ZERA_ENV"):
        nova["TaskTemplate"]["ContainerSpec"]["Env"] = []
    if falha_da_janela("MUDA_VALOR"):
        env = nova["TaskTemplate"]["ContainerSpec"]["Env"]
        nova["TaskTemplate"]["ContainerSpec"]["Env"] = [
            "APPSELL_API_STORAGE_CONTROL_SECRET_KEY=outro" if "SECRET_KEY=" in e else e for e in env
        ]
    if falha_da_janela("MUDA_HEALTHCHECK"):
        nova["TaskTemplate"]["ContainerSpec"]["Healthcheck"] = {"Test": ["NONE"]}
    if falha_da_janela("MUDA_GRACA"):
        nova["TaskTemplate"]["ContainerSpec"]["StopGracePeriod"] = 10000000000
    if falha_da_janela("TROCA_NOME"):
        env = nova["TaskTemplate"]["ContainerSpec"]["Env"]
        nova["TaskTemplate"]["ContainerSpec"]["Env"] = [
            "OUTRA_VARIAVEL=x" if e.startswith("APPSELL_API_ENVIRONMENT=") else e for e in env
        ]
    servico["ler_env_falha"] = falha_da_janela("FALHA_LEITURA_ENV")
    servico["PreviousSpec"], servico["Spec"] = servico["Spec"], nova
    servico["quebrado"] = falha_da_janela("SEM_STORAGE") or falha_da_janela("NAO_SOBE")
    servico["parado"] = falha_da_janela("NAO_SOBE")
    servico["imagem_da_task"] = "outra:imagem" if falha_da_janela("IMAGEM_DA_TASK") else None
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
    nome = args[1][len("task-"):]
    if "Image" in opcao("--format"):
        servico = estado["servicos"][nome]
        da_spec = servico["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"]
        imagem = servico.get("imagem_da_task") or da_spec
        print(json.dumps(imagem))
    else:
        print(json.dumps("cid-" + nome))
elif args[0] == "exec":
    nome = args[1][len("cid-"):]
    if "getent" in args:
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
    # A api em produção troca com start-first (lido em 24/10/2026).
    estado["servicos"][API]["Spec"]["UpdateConfig"]["Order"] = "start-first"
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


def updates_da_volta(pasta: Path) -> list[str]:
    return [
        linha.split()[-1]
        for linha in chamadas(pasta)
        if linha.startswith("service update") and "--env-rm" in linha
    ]


def sorted_env(pasta: Path, servico: str) -> list[str]:
    return sorted(env(pasta, servico))


MODO = {WORKER: "worker", API: "api"}
OS_DOIS = pytest.mark.parametrize("servico", [WORKER, API])


# A janela: só a env muda, mais uma variável


@OS_DOIS
def test_liga_a_flag_e_troca_o_endpoint_so_no_servico(pasta: Path, servico: str) -> None:
    outros = [s for s in (STORAGE, API, WORKER) if s != servico]
    antes = {s: spec(pasta, s) for s in (STORAGE, API, WORKER)}
    resultado = rodar(pasta, MODO[servico])
    assert resultado.returncode == 0, resultado.stderr
    assert atualizados(pasta) == [servico]
    assert sorted_env(pasta, servico) == sorted([ENV_DA_API[0], ENDPOINT, ENV_DA_API[2], FLAG])
    assert f"{servico}: env 3 → 4" in resultado.stdout
    # Fora a env, a spec é a mesma.
    depois, de_antes = spec(pasta, servico), antes[servico]
    for qual in (depois, de_antes):
        qual["TaskTemplate"]["ContainerSpec"].pop("Env")
    assert depois == de_antes
    for s in outros:
        assert spec(pasta, s) == antes[s], s


def test_a_api_troca_com_start_first_sem_o_script_mudar_a_ordem(pasta: Path) -> None:
    assert rodar(pasta, "api").returncode == 0
    linha = next(c for c in chamadas(pasta) if c.startswith("service update"))
    assert "--update-order" not in linha
    assert spec(pasta, API)["UpdateConfig"] == {"Order": "start-first"}
    assert spec(pasta, API)["RollbackConfig"] == {"Order": "stop-first"}


def test_api_sem_start_first_na_spec_aborta_sem_escrever(pasta: Path) -> None:
    atual = estado(pasta)
    atual["servicos"][API]["Spec"]["UpdateConfig"]["Order"] = "stop-first"
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "api"), "update order diferente de start-first")


@OS_DOIS
def test_de_novo_nao_atualiza_nada(pasta: Path, servico: str) -> None:
    assert rodar(pasta, MODO[servico]).returncode == 0
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, MODO[servico])
    assert resultado.returncode == 0, resultado.stderr
    assert "nada a fazer" in resultado.stdout
    assert atualizados(pasta) == []


def test_o_script_nao_cria_nem_mexe_em_rede(pasta: Path) -> None:
    assert rodar(pasta, "worker").returncode == 0
    assert rodar(pasta, "api").returncode == 0
    assert not any(linha.startswith("network ") for linha in chamadas(pasta))
    assert not any("--network" in linha for linha in chamadas(pasta))
    assert STORAGE not in atualizados(pasta)


def _sem_escrita(pasta: Path, resultado: subprocess.CompletedProcess[str], motivo: str) -> None:
    assert resultado.returncode == 1
    assert motivo in resultado.stderr
    assert atualizados(pasta) == []
    assert backups(pasta) == []


@OS_DOIS
def test_fora_da_rede_do_alias_aborta_sem_escrever(pasta: Path, servico: str) -> None:
    atual = estado(pasta)
    atual["servicos"][servico]["Spec"]["TaskTemplate"]["Networks"] = [
        {"Target": REDE_COMPARTILHADA}
    ]
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, MODO[servico]), "fora da rede do alias")


@OS_DOIS
@pytest.mark.parametrize(
    "resolve", [["10.0.9.9"], [VIP, "10.0.9.9"], []], ids=["outro-ip", "dois-ips", "nao-resolve"]
)
def test_alias_que_nao_resolve_so_para_a_vip_aborta_sem_escrever(
    pasta: Path, servico: str, resolve: list[str]
) -> None:
    # Outro serviço respondendo pelo nome (na api, pela rede compartilhada), ou
    # nome que não existe.
    atual = estado(pasta)
    atual["dns"][servico] = resolve
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, MODO[servico]), "não vê o storage pelo alias")


def test_alias_em_duas_redes_do_storage_aborta_sem_escrever(pasta: Path) -> None:
    atual = estado(pasta)
    atual["servicos"][STORAGE]["Spec"]["TaskTemplate"]["Networks"][1]["Aliases"] = [ALIAS]
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "api"), "exatamente uma rede")


def test_swarm_com_dois_nos_aborta_sem_escrever(pasta: Path) -> None:
    _sem_escrita(pasta, rodar(pasta, "worker", NOS="2"), "o Swarm tem 2 nós")


@pytest.mark.parametrize(
    ("env_de_partida", "motivo"),
    [
        ([*ENV_DA_API, "APPSELL_API_STORAGE_INTERNAL_HTTP_ALLOWED=false"], "já tem"),
        ([ENV_DA_API[0], ENV_DA_API[2]], "sem APPSELL_API_STORAGE_INTERNAL_ENDPOINT"),
    ],
    ids=["flag-ja-existe", "sem-endpoint"],
)
def test_partida_diferente_da_lida_aborta_sem_escrever(
    pasta: Path, env_de_partida: list[str], motivo: str
) -> None:
    atual = estado(pasta)
    atual["servicos"][API]["Spec"]["TaskTemplate"]["ContainerSpec"]["Env"] = env_de_partida
    gravar(pasta, atual)
    _sem_escrita(pasta, rodar(pasta, "api"), motivo)


@OS_DOIS
def test_update_recusado_nao_faz_volta(pasta: Path, servico: str) -> None:
    antes = spec(pasta, servico)
    resultado = rodar(pasta, MODO[servico], FALHA_UPDATE=servico)
    assert resultado.returncode == 1
    assert "nada a desfazer" in resultado.stdout
    assert spec(pasta, servico) == antes
    assert updates_da_volta(pasta) == []
    assert rollbacks(pasta) == []


@OS_DOIS
@pytest.mark.parametrize(
    "falha", ["SEM_STORAGE", "NAO_SOBE", "FALHA_LEITURA_ENV", "IMAGEM_DA_TASK"]
)
def test_falha_depois_do_update_volta_as_duas_variaveis_sem_rollback(
    pasta: Path, servico: str, falha: str
) -> None:
    # Só as duas variáveis mudaram: a volta é update na ordem da spec (na api,
    # start-first, sem parar a loja), nunca o rollback stop-first.
    antes = spec(pasta, servico)
    resultado = rodar(pasta, MODO[servico], **{falha: servico})
    assert resultado.returncode == 1
    assert "restaurando" in resultado.stderr
    assert f"{servico}: restaurado, igual ao backup" in resultado.stdout
    assert sorted_env(pasta, servico) == sorted(antes["TaskTemplate"]["ContainerSpec"]["Env"])
    assert updates_da_volta(pasta) == [servico]
    assert rollbacks(pasta) == []


@OS_DOIS
@pytest.mark.parametrize(
    "falha", ["ZERA_ENV", "TROCA_NOME", "MUDA_GRACA", "MUDA_VALOR", "MUDA_HEALTHCHECK"]
)
def test_update_que_estragou_mais_que_as_duas_volta_pela_previous_spec(
    pasta: Path, servico: str, falha: str
) -> None:
    # A volta das duas variáveis não conserta isso. A PreviousSpec é o backup
    # (o único update foi o desta janela): rollback, e a spec volta inteira.
    antes = spec(pasta, servico)
    resultado = rodar(pasta, MODO[servico], **{falha: servico})
    assert resultado.returncode == 1
    assert "docker service rollback (stop-first)" in resultado.stderr
    assert f"{servico}: restaurado, igual ao backup" in resultado.stdout
    assert spec(pasta, servico) == antes
    assert rollbacks(pasta) == [servico]
    assert updates_da_volta(pasta) == []


def test_rollback_order_diferente_de_stop_first_nao_volta(pasta: Path) -> None:
    atual = estado(pasta)
    atual["servicos"][WORKER]["Spec"]["RollbackConfig"]["Order"] = "start-first"
    gravar(pasta, atual)
    resultado = rodar(pasta, "worker", ZERA_ENV=WORKER)
    assert resultado.returncode == 1
    assert "rollback order não é stop-first" in resultado.stderr
    assert "a restauração parou" in resultado.stderr
    assert rollbacks(pasta) == []


def test_env_zerada_falha_pela_contagem(pasta: Path) -> None:
    resultado = rodar(pasta, "worker", ZERA_ENV=WORKER)
    assert resultado.returncode == 1
    assert "esperado 4" in resultado.stderr


def test_variavel_trocada_falha_pelo_nome(pasta: Path) -> None:
    resultado = rodar(pasta, "api", TROCA_NOME=API)
    assert resultado.returncode == 1
    assert "sumiram as variáveis APPSELL_API_ENVIRONMENT" in resultado.stderr


def test_task_em_outra_imagem_nao_conta_como_pronta(pasta: Path) -> None:
    resultado = rodar(pasta, "api", IMAGEM_DA_TASK=API)
    assert resultado.returncode == 1
    assert "não convergiu" in resultado.stderr


def test_valor_de_outra_variavel_trocado_falha_sem_imprimir(pasta: Path) -> None:
    # Mesmos nomes e mesma contagem: só o hash do resto da env pega.
    resultado = rodar(pasta, "api", MUDA_VALOR=API)
    assert resultado.returncode == 1
    assert "o valor de outra variável mudou no update" in resultado.stderr
    assert "outro" not in resultado.stdout + resultado.stderr.replace("outra", "")


def test_campo_fora_da_lista_entra_pelo_resto(pasta: Path) -> None:
    resultado = rodar(pasta, "api", MUDA_HEALTHCHECK=API)
    assert resultado.returncode == 1
    assert "o update mudou mais que a env: Resto TaskTemplate.ContainerSpec.Env" in resultado.stderr


def test_update_que_muda_mais_que_a_env_falha(pasta: Path) -> None:
    resultado = rodar(pasta, "api", MUDA_GRACA=API)
    assert resultado.returncode == 1
    assert "o update mudou mais que a env:" in resultado.stderr
    assert "TaskTemplate.ContainerSpec.StopGracePeriod" in resultado.stderr


def test_sigterm_nas_leituras_do_ready_volta_a_api(pasta: Path) -> None:
    antes = spec(pasta, API)
    processo = subprocess.Popen(  # noqa: S603
        [str(_BASH), str(_SCRIPT), "api"],
        env=_ambiente(pasta, STORAGE461_PAUSA_LEITURAS="30"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert processo.stdout is not None
    for linha in processo.stdout:
        if linha.startswith(f"{API}: env"):
            break
    time.sleep(0.3)
    processo.send_signal(signal.SIGTERM)
    saida, erros = processo.communicate(timeout=20)
    assert processo.returncode == 130
    assert "restaurando" in erros
    assert f"{API}: restaurado, igual ao backup" in saida
    assert sorted_env(pasta, API) == sorted(antes["TaskTemplate"]["ContainerSpec"]["Env"])


def test_restaura_mesmo_com_stderr_que_nao_aceita_escrita(pasta: Path) -> None:
    # Como um SSH que caiu: toda escrita no stderr falha (EPIPE, com o SIGPIPE
    # ignorado, herdado do Python). Sem o `set +e` do ao_sair, a primeira
    # mensagem dele derrubaria a restauração.
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
    assert f"{WORKER}: restaurado, igual ao backup" in resultado.stdout


@OS_DOIS
def test_nenhum_valor_de_env_aparece_na_saida(pasta: Path, servico: str) -> None:
    textos = []
    for argumentos, falhas in (((MODO[servico],), {"ZERA_ENV": servico}), (("seco",), {})):
        resultado = rodar(pasta, *argumentos, **falhas)
        textos.append(resultado.stdout + resultado.stderr)
    for texto in textos:
        assert "segredo-de-teste" not in texto
        assert "https://minio.autonomia.site" not in texto


# Desfazer


@OS_DOIS
def test_desfazer_duas_vezes_volta_uma_vez_so(pasta: Path, servico: str) -> None:
    antes = spec(pasta, servico)
    assert rodar(pasta, MODO[servico]).returncode == 0
    ts = ultimo_ts(pasta, servico)
    primeiro = rodar(pasta, "desfazer", ts)
    assert primeiro.returncode == 0, primeiro.stderr
    assert sorted_env(pasta, servico) == sorted(antes["TaskTemplate"]["ContainerSpec"]["Env"])
    segundo = rodar(pasta, "desfazer", ts)
    assert segundo.returncode == 0, segundo.stderr
    assert "nada a desfazer" in segundo.stdout
    assert updates_da_volta(pasta) == [servico]
    assert rollbacks(pasta) == []


def outro_deploy(pasta: Path, mudar: Any) -> None:
    """Um update alheio na api, como o Swarm: a spec de antes vira a PreviousSpec."""
    atual = estado(pasta)
    api = atual["servicos"][API]
    nova = json.loads(json.dumps(api["Spec"]))
    mudar(nova)
    api["PreviousSpec"], api["Spec"] = api["Spec"], nova
    gravar(pasta, atual)


def test_desfazer_aborta_se_outra_variavel_mudou_depois(pasta: Path) -> None:
    assert rodar(pasta, "api").returncode == 0
    ts = ultimo_ts(pasta, API)
    outro_deploy(pasta, lambda nova: nova["TaskTemplate"]["ContainerSpec"]["Env"].append("NOVA=1"))
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "desfazer", ts)
    assert resultado.returncode == 1
    assert "a PreviousSpec também difere" in resultado.stderr
    assert atualizados(pasta) == []


def test_desfazer_aborta_se_outro_deploy_mudou_a_imagem(pasta: Path) -> None:
    assert rodar(pasta, "api").returncode == 0
    ts = ultimo_ts(pasta, API)
    outro_deploy(
        pasta, lambda nova: nova["TaskTemplate"]["ContainerSpec"].update(Image="api:outra")
    )
    (pasta / "chamadas.log").unlink()
    resultado = rodar(pasta, "desfazer", ts)
    assert resultado.returncode == 1
    assert "a PreviousSpec também difere" in resultado.stderr
    assert atualizados(pasta) == []


def test_desfazer_sem_backup_desse_ts_aborta(pasta: Path) -> None:
    resultado = rodar(pasta, "desfazer", "20261024T120000Z")
    assert resultado.returncode == 1
    assert "nenhum backup com ts" in resultado.stderr
    assert atualizados(pasta) == []


@pytest.mark.parametrize("ts", ["../../etc/x", "20261023T1200Z", "", "20261023T120000Z;id"])
def test_ts_fora_do_formato_e_recusado(pasta: Path, ts: str) -> None:
    resultado = rodar(pasta, "desfazer", ts)
    assert resultado.returncode != 0
    assert "ts inválido" in resultado.stderr
    assert chamadas(pasta) == []


# Seco


def test_seco_so_le(pasta: Path) -> None:
    resultado = rodar(pasta, "seco")
    assert resultado.returncode == 0, resultado.stderr
    assert atualizados(pasta) == []
    assert backups(pasta) == []
    assert f"{WORKER}: env=3 endpoint_interno=minio.autonomia.site no_alvo=não" in resultado.stdout
    assert resultado.stdout.count("alias=ok") == 2
