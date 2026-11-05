#!/usr/bin/env python3
"""Alerta quando uma venda entra e não chega ao cliente (#550).

Roda no host da produção, por cron, a cada 5 minutos. Lê o banco só por duas
funções `security definer`, com o papel `appsell_reconciler`:

- `checkout_reconciliation_report` (0063): o lado do fulfillment;
- `chain_health_report` (0071): o lado do e-mail e o resumo da semana.

Nenhuma linha de cliente sai do banco: só categoria, quantidade e instante.
O e-mail de alerta usa o mesmo canal e a mesma config do alerta de logs (#472).

Decisão de 05/11/2026: o volume atual não sustenta alerta de frescor por
horário. O alerta é de **cadeia travada**, e "nenhuma venda na semana" vai no
resumo semanal (`--resumo-semanal`), não como alarme.

Só biblioteca padrão, compatível com Python 3.9, como o alerta de logs.
Runbook: docs/runbooks/cadeia-travada.md.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import tempfile
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from alerta_de_logs import (  # noqa: E402 - o módulo irmão entra pelo sys.path acima
    ConfigInvalida,
    enviar,
    ler_config,
    validar_config,
)

UTC = timezone.utc
# Limites aprovados na #550.
EMAIL_PARADO_APOS = timedelta(minutes=30)
GRANT_SEM_EMAIL_APOS = timedelta(minutes=15)
FULFILLMENT_PARADO_APOS = timedelta(minutes=15)
# A janela de evento recomeça um pouco antes do fim anterior: um evento gravado
# no limite não fica de fora. Depois de muito tempo parado, olha no máximo um dia.
SOBREPOSICAO = timedelta(minutes=5)
MAX_ATRASO = timedelta(days=1)
# O mesmo problema continua aparecendo enquanto não for reparado. O alerta
# repete uma vez por dia, não a cada 5 minutos.
REPETE_APOS = timedelta(hours=24)
LEITURA_TIMEOUT_S = 60

# Categorias que viram alerta, com o texto que vai no e-mail.
# `access_without_grant` fica de fora até ser medida (ver 0063), e o lado do
# estorno não é cadeia travada de venda.
ALERTAS = {
    "fulfillment_dead_letter": "entrega da compra esgotou as tentativas",
    "fulfillment_stalled": "entrega da compra parada há mais de 15 min",
    "approved_without_fulfillment": "venda aprovada sem job de entrega",
    "fulfillment_completed_without_grant": "entrega concluída sem acesso registrado",
    "grant_without_access": "compra com acesso registrado e comprador sem acesso",
    "grant_without_email": "acesso liberado sem e-mail há mais de 15 min",
    "email_stalled": "e-mail na fila há mais de 30 min",
    "email_dead_letter": "e-mail esgotou as tentativas",
}
SEMANA = {
    "week_sales": "vendas aprovadas",
    "week_grants": "acessos liberados",
    "week_emails_sent": "e-mails enviados",
}
CONFIG_DO_BANCO = ("CADEIA_PG_SERVICO", "CADEIA_PG_BANCO", "CADEIA_SENHA_ARQUIVO")
# O que cada leitura pode devolver. Categoria fora da lista é leitura quebrada,
# não "zero": o contrato do banco mudou e o checador precisa saber.
CATEGORIAS_DA_CONCILIACAO = frozenset(
    {
        "fulfillment_dead_letter",
        "fulfillment_stalled",
        "fulfillment_completed_without_grant",
        "grant_without_access",
        "access_without_grant",
        "refunded_with_access",
        "approved_without_fulfillment",
        "refund_without_fulfillment",
    }
)
# A 0071 devolve sempre as seis, mesmo com quantidade zero. Faltar uma é falha.
METRICAS_DA_CADEIA = frozenset(
    {
        "email_stalled",
        "email_dead_letter",
        "grant_without_email",
        "week_sales",
        "week_grants",
        "week_emails_sent",
    }
)
DIGITOS = frozenset("0123456789")

_SQL_CONCILIACAO = (
    "select category, count(*), max(observed_at) from app_private.checkout_reconciliation_report("
    "'{inicio}'::timestamptz, '{fim}'::timestamptz, interval '{parado} minutes') group by 1"
)
_SQL_CADEIA = (
    "select metric, quantity, observed_at from app_private.chain_health_report("
    "'{inicio}'::timestamptz, interval '{email} minutes', interval '{grant} minutes')"
)
# Cada conexão: só leitura e com teto de tempo, sem depender da config do papel.
_OPCOES_DA_CONEXAO = "-c default_transaction_read_only=on -c statement_timeout=20000"


class LeituraFalhou(Exception):
    pass


@dataclass(frozen=True)
class Leitura:
    quantidade: int
    instante: str | None


@dataclass(frozen=True)
class Decisao:
    alertar: bool
    linhas: tuple[str, ...]
    estado: dict[str, object]


def inicio_da_janela(estado: dict[str, object], *, agora: datetime) -> datetime:
    padrao = agora - timedelta(minutes=15)
    anterior = _instante(estado.get("fim"))
    if anterior is None or anterior > agora:
        return padrao
    return max(anterior - SOBREPOSICAO, agora - MAX_ATRASO)


def ler_leituras(
    saida: str, *, permitidas: frozenset[str], obrigatorias: frozenset[str] = frozenset()
) -> dict[str, Leitura]:
    """`psql -At -F '|'`: uma linha `categoria|quantidade|instante` por métrica.

    Linha fora do formato, quantidade que não é número, categoria desconhecida
    ou métrica obrigatória ausente são falha da leitura. Ler "zero" de uma
    saída quebrada seria o silêncio que a #550 proíbe.
    """
    leituras: dict[str, Leitura] = {}
    for linha in saida.splitlines():
        if not linha.strip():
            continue
        partes = linha.strip().split("|")
        if len(partes) != 3:
            raise LeituraFalhou("linha fora do formato")
        nome, quantidade, instante = (parte.strip() for parte in partes)
        if nome not in permitidas:
            raise LeituraFalhou(f"categoria desconhecida: {nome[:40]}")
        if not quantidade or not set(quantidade) <= DIGITOS:
            raise LeituraFalhou(f"quantidade inválida para {nome}")
        if nome in leituras:
            raise LeituraFalhou(f"categoria repetida: {nome}")
        leituras[nome] = Leitura(int(quantidade), instante or None)
    faltando = sorted(obrigatorias - leituras.keys())
    if faltando:
        raise LeituraFalhou("faltam métricas: " + ", ".join(faltando))
    return leituras


def decidir(
    leituras: dict[str, Leitura], estado: dict[str, object], *, agora: datetime
) -> Decisao:
    """Alerta quando um problema aparece, cresce ou tem um caso mais novo que o
    da rodada anterior, e repete uma vez por dia enquanto ele existir.

    O caso mais novo vem do `observed_at`. Ele cobre o "um sai, outro entra",
    em que a contagem fica igual e o problema é outro.
    """
    vazia = Leitura(0, None)
    atuais = {nome: leituras.get(nome, vazia) for nome in ALERTAS}
    contagens_antes = _dicionario(estado.get("contagens"))
    instantes_antes = _dicionario(estado.get("instantes"))
    novos = [
        nome
        for nome, leitura in atuais.items()
        if leitura.quantidade > 0
        and (
            leitura.quantidade > _inteiro(contagens_antes.get(nome))
            or _mais_novo(leitura.instante, instantes_antes.get(nome))
        )
    ]
    existe = [nome for nome, leitura in atuais.items() if leitura.quantidade > 0]
    ultimo = _instante(estado.get("ultimo_alerta"))
    repetir = bool(existe) and (ultimo is None or agora - ultimo >= REPETE_APOS)
    alertar = bool(novos) or repetir
    linhas = tuple(f"{ALERTAS[n]}: {atuais[n].quantidade}" for n in ALERTAS if n in existe)
    novo: dict[str, object] = {
        "fim": agora.isoformat(),
        "contagens": {nome: leitura.quantidade for nome, leitura in atuais.items()},
        "instantes": {
            nome: leitura.instante or instantes_antes.get(nome)
            for nome, leitura in atuais.items()
            if leitura.instante or instantes_antes.get(nome)
        },
        "ultimo_alerta": agora.isoformat() if alertar else estado.get("ultimo_alerta"),
    }
    return Decisao(alertar=alertar, linhas=linhas, estado=novo)


def decidir_falha(estado: dict[str, object], *, agora: datetime, motivo: str) -> Decisao:
    """A própria checagem falhando também alerta: silêncio não é "tudo bem".

    Repete no máximo uma vez por hora, e não avança a janela: a próxima rodada
    que funcionar olha desde o último fim bom.
    """
    ultima = _instante(estado.get("ultima_falha_alertada"))
    alertar = ultima is None or agora - ultima >= timedelta(hours=1)
    novo = dict(estado)
    if alertar:
        novo["ultima_falha_alertada"] = agora.isoformat()
    return Decisao(alertar=alertar, linhas=(f"a checagem falhou: {motivo}",), estado=novo)


def mensagem(
    titulo: str, linhas: tuple[str, ...], *, remetente: str, destino: str
) -> EmailMessage:
    email = EmailMessage()
    email["Subject"] = f"[Sell] {titulo}"
    email["From"] = remetente
    email["To"] = destino
    corpo = [*linhas, "", "Runbook: docs/runbooks/cadeia-travada.md"]
    email.set_content("\n".join(corpo) + "\n")
    return email


def resumo_semanal(leituras: dict[str, Leitura]) -> tuple[str, ...]:
    def quantidade(nome: str) -> int:
        leitura = leituras.get(nome)
        return leitura.quantidade if leitura else 0

    linhas = tuple(f"{texto}: {quantidade(nome)}" for nome, texto in SEMANA.items())
    if quantidade("week_sales") == 0:
        linhas += ("nenhuma venda aprovada nos últimos 7 dias",)
    return linhas


def _instante(valor: object) -> datetime | None:
    if not isinstance(valor, str):
        return None
    try:
        instante = datetime.fromisoformat(valor)
    except ValueError:
        return None
    return instante if instante.tzinfo else None


def _instante_do_banco(valor: object) -> datetime | None:
    """O `timestamptz` do psql sai como `2026-11-05 12:00:00.5+00`.

    O `fromisoformat` do Python 3.9 só aceita fração com 3 ou 6 dígitos e fuso
    com minutos. A fração vira 6 dígitos e o fuso ganha `:00`.
    """
    if not isinstance(valor, str):
        return None
    texto = valor.strip().replace(" ", "T", 1)
    inicio_do_fuso = max(texto.rfind("+"), texto.rfind("-"))
    if inicio_do_fuso <= texto.find("T"):
        return None
    corpo, fuso = texto[:inicio_do_fuso], texto[inicio_do_fuso:]
    if len(fuso) == 3:
        fuso += ":00"
    segundos, ponto, fracao = corpo.partition(".")
    if ponto:
        if not fracao or len(fracao) > 6 or not set(fracao) <= DIGITOS:
            return None
        corpo = f"{segundos}.{fracao.ljust(6, '0')}"
    return _instante(corpo + fuso)


def _mais_novo(atual: str | None, anterior: object) -> bool:
    novo = _instante_do_banco(atual)
    if novo is None:
        return False
    velho = _instante_do_banco(anterior)
    return velho is None or novo > velho


def _dicionario(valor: object) -> dict[str, object]:
    return valor if isinstance(valor, dict) else {}


def _inteiro(valor: object) -> int:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) else 0


def consultar(sql: str, config: dict[str, str]) -> str:
    """Roda a consulta dentro do contêiner do Postgres, como `appsell_reconciler`.

    A senha sai do arquivo 0600 pelo stdin do `docker exec` e só vira variável
    dentro do contêiner: nunca aparece em argumento de processo. A conexão vai
    pelo IP do contêiner, para passar por SCRAM.
    """
    servico = config["CADEIA_PG_SERVICO"]
    tarefas = _rodar(["docker", "ps", "-q", "--filter", f"name={servico}."], entrada=None)
    ids = tarefas.split()
    if len(ids) != 1:
        raise LeituraFalhou(f"esperava 1 contêiner de {servico}, achei {len(ids)}")
    senha = Path(config["CADEIA_SENHA_ARQUIVO"]).read_text(encoding="utf-8").strip()
    roteiro = (
        'read -r P; export PGPASSWORD="$P"; H=$(hostname -i | cut -d" " -f1); '
        'export PGOPTIONS="$3"; '
        'exec psql "host=$H dbname=$1 user=appsell_reconciler sslmode=disable" '
        "-X -At -F '|' -v ON_ERROR_STOP=1 -c \"$2\""
    )
    return _rodar(
        [
            "docker",
            "exec",
            "-i",
            ids[0],
            "sh",
            "-c",
            roteiro,
            "sh",
            config["CADEIA_PG_BANCO"],
            sql,
            _OPCOES_DA_CONEXAO,
        ],
        entrada=senha + "\n",
    )


def _rodar(argumentos: list[str], *, entrada: str | None) -> str:
    try:
        completo = subprocess.run(  # noqa: S603 - comando fixo, sem shell do host
            argumentos,
            input=entrada,
            capture_output=True,
            text=True,
            timeout=LEITURA_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as erro:
        raise LeituraFalhou(type(erro).__name__) from erro
    if completo.returncode != 0:
        # O stderr do psql pode citar a consulta, nunca a senha; mesmo assim
        # só o código sai no e-mail.
        raise LeituraFalhou(f"{argumentos[1]} saiu com {completo.returncode}")
    return completo.stdout


def ler_tudo(
    config: dict[str, str], *, inicio: datetime, agora: datetime
) -> dict[str, Leitura]:
    conciliacao = consultar(
        _SQL_CONCILIACAO.format(
            inicio=inicio.isoformat(),
            fim=(agora + timedelta(minutes=1)).isoformat(),
            parado=int(FULFILLMENT_PARADO_APOS.total_seconds() // 60),
        ),
        config,
    )
    cadeia = consultar(
        _SQL_CADEIA.format(
            inicio=inicio.isoformat(),
            email=int(EMAIL_PARADO_APOS.total_seconds() // 60),
            grant=int(GRANT_SEM_EMAIL_APOS.total_seconds() // 60),
        ),
        config,
    )
    return {
        **ler_leituras(conciliacao, permitidas=CATEGORIAS_DA_CONCILIACAO),
        **ler_leituras(
            cadeia, permitidas=METRICAS_DA_CADEIA, obrigatorias=METRICAS_DA_CADEIA
        ),
    }


def ler_estado(caminho: Path) -> dict[str, object]:
    """Estado ausente, ilegível ou com formato errado recomeça do zero."""
    try:
        bruto = json.loads(caminho.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    return bruto if isinstance(bruto, dict) else {}


def gravar_estado(caminho: Path, estado: dict[str, object]) -> None:
    """Grava inteiro ou não grava: temporário na mesma pasta, `fsync` e
    `os.replace`, com permissão 0600. Uma queda no meio não deixa JSON
    truncado, que faria a janela voltar e tudo alertar de novo."""
    caminho.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descritor, temporario = tempfile.mkstemp(dir=caminho.parent, prefix=".cadeia-")
    try:
        os.fchmod(descritor, 0o600)
        with os.fdopen(descritor, "w", encoding="utf-8") as arquivo:
            json.dump(estado, arquivo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
        os.replace(temporario, caminho)
    except BaseException:
        Path(temporario).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("/etc/sell-alerta/alerta.env"))
    parser.add_argument("--estado", type=Path, default=Path("/var/lib/sell-alerta/cadeia.json"))
    parser.add_argument("--seco", action="store_true", help="imprime o e-mail em vez de enviar")
    parser.add_argument("--resumo-semanal", action="store_true", help="envia o resumo da semana")
    argumentos = parser.parse_args(argv)

    try:
        config = ler_config(argumentos.config.read_text(encoding="utf-8"))
        validar_config(config)
        faltando = [nome for nome in CONFIG_DO_BANCO if not config.get(nome)]
        if faltando:
            raise ConfigInvalida("faltam na config: " + ", ".join(faltando))
    except (OSError, ConfigInvalida) as erro:
        print(f"cadeia-travada: config inválida: {erro}", file=sys.stderr)
        return 2

    agora = datetime.now(UTC).replace(microsecond=0)
    estado = ler_estado(argumentos.estado)
    try:
        inicio = inicio_da_janela(estado, agora=agora)
        leituras = ler_tudo(config, inicio=inicio, agora=agora)
        if argumentos.resumo_semanal:
            linhas = resumo_semanal(leituras)
            return _entregar("resumo da semana", linhas, config, argumentos.seco)
        decisao = decidir(leituras, estado, agora=agora)
        titulo = "venda travada no caminho até o cliente"
        codigo = 0
    except LeituraFalhou as erro:
        decisao = decidir_falha(estado, agora=agora, motivo=str(erro))
        titulo = "a checagem da cadeia de venda falhou"
        codigo = 1
    except Exception as erro:  # noqa: BLE001 - qualquer falha precisa virar alerta
        # Só o nome do tipo: a mensagem pode trazer conteúdo de arquivo.
        decisao = decidir_falha(estado, agora=agora, motivo=type(erro).__name__)
        titulo = "a checagem da cadeia de venda falhou"
        codigo = 1

    if decisao.alertar and _entregar(titulo, decisao.linhas, config, argumentos.seco) != 0:
        # Sem gravar o estado: a próxima rodada tenta alertar de novo.
        return 1
    if not argumentos.seco:
        gravar_estado(argumentos.estado, decisao.estado)
    return codigo


def _entregar(titulo: str, linhas: tuple[str, ...], config: dict[str, str], seco: bool) -> int:
    email = mensagem(
        titulo, linhas, remetente=config["SMTP_USER_TRANSACIONAL"], destino=config["ALERTA_PARA"]
    )
    if seco:
        print(email.as_string())
        return 0
    try:
        enviar(email, config)
    except (OSError, smtplib.SMTPException) as erro:
        print(f"cadeia-travada: envio falhou: {type(erro).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
