#!/usr/bin/env python3
"""Alerta por e-mail quando a API ou o web do Sell gravam erro (#312).

Roda no host da produção, por cron, a cada 5 minutos. Lê `docker service logs`
dos serviços, conta as linhas JSON com `level` de erro por serviço e por
`event` (API) ou `marker` (web) e manda um e-mail por janela, só se houver
erro. Runbook: docs/runbooks/alerta-de-logs.md.

Só biblioteca padrão, compatível com Python 3.9: o host não tem o ambiente da
API, e a versão do python3 dele ainda não foi lida.

O e-mail nunca leva a linha inteira. Leva a contagem e até três amostras com
os campos de uma lista fechada, e qualquer valor com cara de credencial sai
como <REDACTED>.
"""

from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

UTC = timezone.utc
SERVICOS = ("sell-autonomia_api", "sell-autonomia_web")
NIVEIS_DE_ERRO = frozenset({"error", "critical", "fatal"})
MAX_AMOSTRAS = 3
# Depois de muito tempo parado, olhar tudo de uma vez viraria spam. O e-mail
# diz o início real da janela, então o buraco fica visível.
MAX_ATRASO = timedelta(minutes=60)
LEITURA_TIMEOUT_S = 60
# Campos que podem ir no e-mail. Nada de identificador de pessoa, e-mail,
# cabeçalho, corpo ou id de pedido.
CAMPOS_DA_AMOSTRA = (
    "timestamp",
    "level",
    "event",
    "marker",
    "reason",
    "route",
    "method",
    "status",
    "status_code",
    "request_id",
    "sqlstate",
    "error_type",
    "stripe_event_type",
    "livemode",
)
MAX_VALOR = 160
# Regra do Rodrigo: sequência de 32+ caracteres [A-Za-z0-9_-] é tratada como
# credencial. Feito com método de string, sem regex.
TAMANHO_DE_CREDENCIAL = 32
CARACTERES_DE_CREDENCIAL = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
)
CONFIG_OBRIGATORIA = (
    "SMTP_HOST_TRANSACIONAL",
    "SMTP_PORT_TRANSACIONAL",
    "SMTP_USER_TRANSACIONAL",
    "SMTP_PASS_TRANSACIONAL",
    "ALERTA_PARA",
)


class ConfigInvalida(Exception):
    pass


@dataclass(frozen=True)
class Resumo:
    inicio: datetime
    fim: datetime
    contagem: dict[tuple[str, str], int]
    amostras: tuple[str, ...]
    falhas: dict[str, str] = field(default_factory=dict)


def resumir(
    linhas_por_servico: dict[str, list[str]],
    *,
    inicio: datetime,
    fim: datetime,
    falhas: dict[str, str] | None = None,
) -> Resumo:
    contagem: Counter[tuple[str, str]] = Counter()
    amostras: list[str] = []
    for servico, linhas in linhas_por_servico.items():
        for bruta in linhas:
            registro = _registro_de_erro(bruta, inicio=inicio, fim=fim)
            if registro is None:
                continue
            nome = str(registro.get("event") or registro.get("marker") or "<sem event>")
            contagem[(servico, nome)] += 1
            if len(amostras) < MAX_AMOSTRAS:
                amostras.append(f"{servico}: {_amostra(registro)}")
    return Resumo(
        inicio=inicio,
        fim=fim,
        contagem=dict(contagem),
        amostras=tuple(amostras),
        falhas=dict(falhas or {}),
    )


def _registro_de_erro(bruta: str, *, inicio: datetime, fim: datetime) -> dict[str, object] | None:
    # `--timestamps --raw`: "<RFC3339Nano> <mensagem>".
    momento_texto, _, mensagem = bruta.strip().partition(" ")
    momento = _momento(momento_texto)
    if momento is None or not (inicio <= momento < fim):
        return None
    mensagem = mensagem.strip()
    if not mensagem.startswith("{"):
        return None
    try:
        registro = json.loads(mensagem)
    except json.JSONDecodeError:
        return None
    if not isinstance(registro, dict):
        return None
    if str(registro.get("level", "")).lower() not in NIVEIS_DE_ERRO:
        return None
    return registro


def _momento(texto: str) -> datetime | None:
    # O Docker escreve nanossegundos; o datetime guarda microssegundos.
    base, ponto, fracao = texto.removesuffix("Z").partition(".")
    if ponto:
        base = f"{base}.{fracao[:6]}"
    try:
        momento = datetime.fromisoformat(base)
    except ValueError:
        return None
    return momento.replace(tzinfo=UTC) if momento.tzinfo is None else momento


def _amostra(registro: dict[str, object]) -> str:
    partes = []
    for campo in CAMPOS_DA_AMOSTRA:
        if campo in registro:
            partes.append(f"{campo}={_limpo(str(registro[campo]))}")
    return " ".join(partes)


def _limpo(valor: str) -> str:
    return _sem_credencial(valor)[:MAX_VALOR]


def _sem_credencial(valor: str) -> str:
    saida: list[str] = []
    trecho: list[str] = []
    for caractere in valor + " ":
        if caractere in CARACTERES_DE_CREDENCIAL:
            trecho.append(caractere)
            continue
        saida.append("<REDACTED>" if len(trecho) >= TAMANHO_DE_CREDENCIAL else "".join(trecho))
        trecho = []
        saida.append(caractere)
    return "".join(saida)[:-1]


def mensagem(resumo: Resumo, *, remetente: str, destino: str) -> EmailMessage | None:
    total = sum(resumo.contagem.values())
    if total == 0 and not resumo.falhas:
        return None
    linhas = [
        f"Janela: {resumo.inicio.isoformat()} a {resumo.fim.isoformat()} (UTC)",
        "",
    ]
    for servico in sorted({chave[0] for chave in resumo.contagem}):
        linhas.append(servico)
        for (dono, nome), quantos in sorted(resumo.contagem.items()):
            if dono == servico:
                linhas.append(f"  {nome}: {quantos}")
    for servico, motivo in sorted(resumo.falhas.items()):
        linhas.append(f"{servico}: leitura falhou ({_limpo(motivo)})")
    if resumo.amostras:
        linhas += ["", "Primeiras linhas (campos filtrados):", *resumo.amostras]
    linhas += ["", "Runbook: docs/runbooks/alerta-de-logs.md"]
    email = EmailMessage()
    assunto = f"[Sell] {total} erro(s) em produção"
    if resumo.falhas:
        assunto += " e leitura de log falhou"
    email["Subject"] = assunto
    email["From"] = remetente
    email["To"] = destino
    email.set_content("\n".join(linhas) + "\n")
    return email


def inicio_da_janela(
    estado: dict[str, str], *, agora: datetime, janela_minutos: int
) -> datetime:
    padrao = agora - timedelta(minutes=janela_minutos)
    texto = estado.get("fim")
    if not texto:
        return padrao
    try:
        anterior = datetime.fromisoformat(texto)
    except ValueError:
        return padrao
    if anterior > agora:
        return padrao
    return max(anterior, agora - MAX_ATRASO)


def ler_config(texto: str) -> dict[str, str]:
    config: dict[str, str] = {}
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        chave, igual, valor = linha.partition("=")
        if igual:
            config[chave.strip()] = valor.strip()
    return config


def validar_config(config: dict[str, str]) -> None:
    faltando = [nome for nome in CONFIG_OBRIGATORIA if not config.get(nome)]
    if faltando:
        raise ConfigInvalida("faltam na config: " + ", ".join(faltando))


def ler_logs(servico: str, inicio: datetime) -> list[str]:
    completo = subprocess.run(  # noqa: S603 - comando fixo, sem shell
        [
            "docker",
            "service",
            "logs",
            "--raw",
            "--timestamps",
            "--no-trunc",
            "--since",
            inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            servico,
        ],
        capture_output=True,
        text=True,
        timeout=LEITURA_TIMEOUT_S,
        check=False,
    )
    if completo.returncode != 0:
        raise RuntimeError(f"docker service logs saiu com {completo.returncode}")
    # O contêiner escreve em stdout e stderr; o Docker repassa cada um no seu.
    return completo.stdout.splitlines() + completo.stderr.splitlines()


def enviar(email: EmailMessage, config: dict[str, str]) -> None:
    host = config["SMTP_HOST_TRANSACIONAL"]
    porta = int(config["SMTP_PORT_TRANSACIONAL"])
    seguro = config.get("SMTP_SECURE_TRANSACIONAL", "true").lower() == "true"
    contexto = ssl.create_default_context()
    if seguro:
        with smtplib.SMTP_SSL(host, porta, context=contexto, timeout=30) as smtp:
            smtp.login(config["SMTP_USER_TRANSACIONAL"], config["SMTP_PASS_TRANSACIONAL"])
            smtp.send_message(email)
        return
    with smtplib.SMTP(host, porta, timeout=30) as smtp:
        smtp.starttls(context=contexto)
        smtp.login(config["SMTP_USER_TRANSACIONAL"], config["SMTP_PASS_TRANSACIONAL"])
        smtp.send_message(email)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("/etc/sell-alerta/alerta.env"))
    parser.add_argument("--estado", type=Path, default=Path("/var/lib/sell-alerta/estado.json"))
    parser.add_argument("--janela-minutos", type=int, default=5)
    parser.add_argument("--seco", action="store_true", help="imprime o e-mail em vez de enviar")
    parser.add_argument("--teste", action="store_true", help="envia um alerta sintético")
    argumentos = parser.parse_args(argv)

    try:
        config = ler_config(argumentos.config.read_text(encoding="utf-8"))
        validar_config(config)
    except (OSError, ConfigInvalida) as erro:
        print(f"alerta-de-logs: config inválida: {erro}", file=sys.stderr)
        return 2

    agora = datetime.now(UTC).replace(microsecond=0)
    estado: dict[str, str] = {}
    if argumentos.estado.exists():
        try:
            estado = json.loads(argumentos.estado.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            estado = {}
    inicio = inicio_da_janela(estado, agora=agora, janela_minutos=argumentos.janela_minutos)

    if argumentos.teste:
        sintetico = {"sell-autonomia_api": [
            f"{agora.isoformat()} " + json.dumps({"level": "error", "event": "teste_do_alerta"})
        ]}
        resumo = resumir(sintetico, inicio=inicio, fim=agora + timedelta(seconds=1))
    else:
        linhas: dict[str, list[str]] = {}
        falhas: dict[str, str] = {}
        for servico in SERVICOS:
            try:
                linhas[servico] = ler_logs(servico, inicio)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as erro:
                falhas[servico] = str(erro)
        resumo = resumir(linhas, inicio=inicio, fim=agora, falhas=falhas)

    email = mensagem(
        resumo, remetente=config["SMTP_USER_TRANSACIONAL"], destino=config["ALERTA_PARA"]
    )
    if email is not None:
        if argumentos.seco:
            print(email.as_string())
        else:
            try:
                enviar(email, config)
            except (OSError, smtplib.SMTPException) as erro:
                # Sem avançar o estado: a próxima rodada tenta a mesma janela.
                print(f"alerta-de-logs: envio falhou: {type(erro).__name__}", file=sys.stderr)
                return 1

    if not argumentos.teste and not argumentos.seco:
        argumentos.estado.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        argumentos.estado.write_text(json.dumps({"fim": agora.isoformat()}), encoding="utf-8")
    return 1 if resumo.falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
