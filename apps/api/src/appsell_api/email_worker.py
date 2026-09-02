"""Worker do canal transacional: consome ``app_private.email_outbox`` e envia por SMTP.

Mesmo protocolo do worker de fulfillment: identidade própria registrada no
banco (revogar a linha para o worker sem rotação de chave), claim com lease e
fence, e cada resultado gravado por função SQL que exige o lease vivo. O que
este worker acrescenta é a fronteira com o SMTP — e a regra da política de
e-mail: sem as variáveis ``SMTP_*_TRANSACIONAL`` ele **não inicia**, registra
que não iniciou, e as linhas ficam pendentes onde qualquer um pode vê-las.
Nunca cai no remetente comercial.

O que chega ao log é código e fase, nunca credencial nem corpo.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import smtplib
import ssl
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import structlog
from anyio import to_thread
from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from . import email_layout
from .config import EMAIL_REMETENTE, Settings
from .internal_requests import InternalRequestAuthenticator
from .service_auth import ServiceEnvelope, ServiceIdentity

WORKER_SUBJECT = "email-worker-v1"
WORKER_CAPABILITY = "notification.email.worker"
_WORKER_EMAIL = "email-worker@system.appsell.internal"
_WORKER_LABEL = f"api-email-{os.getpid()}"
_IDLE_POLL_SECONDS = 2.0
# Um item por claim e o lease no teto da função (300 s). O timeout do smtplib
# vale por comando — banner, EHLO, STARTTLS, AUTH, MAIL, RCPT, DATA, QUIT —,
# então um envio travado pode custar até oito timeouts; com 15 s cada, 120 s,
# folgado dentro do lease. Se o lease vencesse no meio, outro worker
# reivindicaria e o comprador receberia duas vezes. O teste confere a faixa.
LEASE_SECONDS = 300
CLAIM_LIMIT = 1
_SMTP_TIMEOUT_SECONDS = 15
# Falha do canal não é falha do item: o worker para em vez de percorrer a fila
# inteira queimando conexões — dez minutos para credencial recusada, dois
# para host fora.
_PAUSA_POR_FALHA_DO_CANAL = {
    "smtp_auth": 600.0,
    "smtp_unreachable": 120.0,
    "smtp_no_starttls": 300.0,
    "smtp_no_auth": 300.0,
}
REMETENTE_NOME = "Autonom.ia Sell"
# Exatamente um mailbox: um só "@", nada que abra outro endereço (vírgula,
# ponto e vírgula, "<>", aspas, parênteses, colchetes), nada de espaço ou
# controle, e domínio com labels ASCII e um ponto. A parte local aceita
# Unicode, como o ingresso — o SMTP decide se sabe entregar (SMTPUTF8).
_LOCAL = r"[^\s@,;<>()\"\[\]\x00-\x1f\x7f]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
# O último label também é _LABEL, não "só letras": "xn--p1ai" é um TLD válido.
_UM_DESTINATARIO = re.compile(rf"^{_LOCAL}@(?:{_LABEL}\.)+{_LABEL}$")

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FalhaDeEnvio(Exception):
    """Resultado de um envio que não aconteceu, já classificado para a fila."""

    error_code: str
    permanent: bool
    delay_seconds: int
    smtp_code: int | None = None

    def __str__(self) -> str:
        return self.error_code


class Transporte:
    """Fronteira com o SMTP. Um objeto por envio, sem estado entre eles."""

    def __init__(self, settings: Settings) -> None:
        self._host = settings.email_smtp_host
        # Settings garantem os dois antes de o worker iniciar (email_disabled_reason).
        self._port = settings.email_smtp_port_number or 465
        self._secure = settings.email_smtp_secure_flag is not False
        self._user = settings.email_smtp_user
        self._password = settings.email_smtp_password.get_secret_value()

    def enviar(self, mensagem: EmailMessage) -> None:
        # SMTPNotSupportedError não diz por si só qual extensão faltou, e a
        # diferença importa: STARTTLS e AUTH ausentes são o canal inteiro (o
        # servidor está errado, e a fila espera), enquanto SMTPUTF8 é deste
        # endereço (só ele morre). Por isso cada fase é traduzida aqui.
        contexto = ssl.create_default_context()
        if self._secure:
            cliente: smtplib.SMTP = smtplib.SMTP_SSL(
                self._host, self._port, timeout=_SMTP_TIMEOUT_SECONDS, context=contexto
            )
        else:
            cliente = smtplib.SMTP(self._host, self._port, timeout=_SMTP_TIMEOUT_SECONDS)
        with cliente:
            if not self._secure:
                try:
                    cliente.starttls(context=contexto)
                except smtplib.SMTPNotSupportedError as erro:
                    raise FalhaDeEnvio(
                        "smtp_no_starttls", permanent=False, delay_seconds=300
                    ) from erro
            try:
                cliente.login(self._user, self._password)
            except smtplib.SMTPNotSupportedError as erro:
                raise FalhaDeEnvio("smtp_no_auth", permanent=False, delay_seconds=300) from erro
            try:
                # Envelope explícito: o From e o único To vêm dos cabeçalhos que
                # montar_mensagem validou, não de um parse dos cabeçalhos.
                cliente.send_message(mensagem, from_addr=EMAIL_REMETENTE, to_addrs=[mensagem["To"]])
            except smtplib.SMTPNotSupportedError as erro:
                # O endereço exige SMTPUTF8 e este servidor não fala: insistir
                # não muda nada, e a fila não pode parar por causa dele.
                raise FalhaDeEnvio("smtp_no_utf8", permanent=True, delay_seconds=1) from erro


def classificar_erro(erro: BaseException) -> FalhaDeEnvio:
    """Traduz a exceção do smtplib para o que a fila precisa saber.

    Sem mensagem: `str(erro)` do smtplib pode carregar o diálogo inteiro,
    inclusive o AUTH.
    """
    if isinstance(erro, smtplib.SMTPRecipientsRefused):
        codigos = [codigo for codigo, _ in erro.recipients.values()]
        permanente = all(500 <= codigo < 600 for codigo in codigos) if codigos else True
        return FalhaDeEnvio(
            "recipient_refused" if permanente else "recipient_deferred",
            permanent=permanente,
            delay_seconds=1 if permanente else 300,
            smtp_code=codigos[0] if codigos else None,
        )
    if isinstance(erro, smtplib.SMTPAuthenticationError):
        # Senha errada derruba todos os envios: recuo longo, sem dead-letter —
        # a correção é rotacionar a variável, e a fila espera.
        return FalhaDeEnvio(
            "smtp_auth", permanent=False, delay_seconds=600, smtp_code=erro.smtp_code
        )
    if isinstance(erro, smtplib.SMTPResponseException):
        permanente = 500 <= erro.smtp_code < 600
        return FalhaDeEnvio(
            "smtp_permanent" if permanente else "smtp_temporary",
            permanent=permanente,
            delay_seconds=1 if permanente else 120,
            smtp_code=erro.smtp_code,
        )
    if isinstance(erro, (smtplib.SMTPException, OSError)):
        return FalhaDeEnvio("smtp_unreachable", permanent=False, delay_seconds=120)
    return FalhaDeEnvio("send_unexpected", permanent=False, delay_seconds=300)


def destinatario_valido(valor: object) -> bool:
    return isinstance(valor, str) and len(valor) <= 254 and bool(_UM_DESTINATARIO.match(valor))


def montar_mensagem(item: dict[str, Any], settings: Settings) -> tuple[EmailMessage, str]:
    """Renderiza o e-mail do item e devolve a mensagem pronta e o Message-ID."""
    kind = item.get("kind")
    if kind != "purchase_confirmed":
        raise FalhaDeEnvio("kind_unknown", permanent=True, delay_seconds=1)
    payload = item.get("payload")
    if not isinstance(payload, dict):
        raise FalhaDeEnvio("payload_invalid", permanent=True, delay_seconds=1)
    destinatario = item.get("recipient_email")
    if not destinatario_valido(destinatario):
        raise FalhaDeEnvio("recipient_invalid", permanent=True, delay_seconds=1)
    try:
        email = email_layout.compra_confirmada(
            payload, item.get("recipient_name"), settings.email_public_origin
        )
        renderizado = email_layout.renderizar(email, settings.email_public_origin)
    except email_layout.EmailInvalido as erro:
        raise FalhaDeEnvio("render_failed", permanent=True, delay_seconds=1) from erro

    # Message-ID estável por item: se o envio sair e o registro falhar, a
    # segunda tentativa leva o mesmo id, e o cliente de e-mail de quem recebe
    # reconhece a duplicata.
    dominio = EMAIL_REMETENTE.rsplit("@", 1)[-1]
    message_id = f"<{UUID(str(item['id']))}@{dominio}>"
    mensagem = EmailMessage()
    mensagem["From"] = formataddr((REMETENTE_NOME, EMAIL_REMETENTE))
    mensagem["To"] = str(destinatario)
    mensagem["Reply-To"] = EMAIL_REMETENTE
    mensagem["Subject"] = email.assunto
    mensagem["Message-ID"] = message_id
    mensagem.set_content(renderizado.text)
    mensagem.add_alternative(renderizado.html, subtype="html")
    return mensagem, message_id.strip("<>")


async def run_email_worker_loop(app: FastAPI, stop: asyncio.Event) -> None:
    settings: Settings = app.state.settings
    auth: InternalRequestAuthenticator | None = app.state.internal_auth
    if auth is None:
        return
    motivo = settings.email_disabled_reason
    if motivo is not None:
        # Fail-closed com registro: nada sai, e o motivo fica no log uma vez.
        logger.warning("email_worker_disabled", reason=motivo)
        return
    worker_id = worker_identity(settings)
    transporte = Transporte(settings)
    registered = False
    while not stop.is_set():
        try:
            if not registered:
                await _ensure_identity(auth, settings, worker_id)
                registered = True
            itens = await _claim(auth, settings, worker_id)
            pausa = 0.0
            motivo_pausa = ""
            for item in itens:
                falha = await _entregar(auth, settings, worker_id, transporte, item)
                if falha is not None and falha.error_code in _PAUSA_POR_FALHA_DO_CANAL:
                    pausa = _PAUSA_POR_FALHA_DO_CANAL[falha.error_code]
                    motivo_pausa = falha.error_code
            if pausa:
                logger.warning("email_worker_paused", reason=motivo_pausa, seconds=pausa)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=pausa)
                except TimeoutError:
                    pass
                continue
            if itens:
                continue
        except (DBAPIError, RuntimeError, ValueError, KeyError, HTTPException) as error:
            logger.warning("email_worker_retry", error_type=type(error).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_IDLE_POLL_SECONDS)
        except TimeoutError:
            pass


async def _ensure_identity(
    auth: InternalRequestAuthenticator, settings: Settings, worker_id: UUID
) -> None:
    async with auth.trusted_domain_session(worker_envelope(settings, worker_id)) as session:
        await session.execute(
            text("select app_private.ensure_email_worker(:worker,:issuer,:subject,:email,:label)"),
            {
                "worker": worker_id,
                "issuer": settings.identity_issuer,
                "subject": WORKER_SUBJECT,
                "email": _WORKER_EMAIL,
                "label": _WORKER_LABEL,
            },
        )


async def _claim(
    auth: InternalRequestAuthenticator, settings: Settings, worker_id: UUID
) -> list[dict[str, Any]]:
    async with auth.trusted_domain_session(worker_envelope(settings, worker_id)) as session:
        value = await session.scalar(
            text("select app_private.claim_email_outbox(:worker,:label,:limit,:lease)"),
            {
                "worker": worker_id,
                "label": _WORKER_LABEL,
                "limit": CLAIM_LIMIT,
                "lease": LEASE_SECONDS,
            },
        )
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise RuntimeError("invalid email claim response")
    return value


async def _entregar(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    transporte: Transporte,
    item: dict[str, Any],
) -> FalhaDeEnvio | None:
    """Envia um item e registra o resultado; devolve a falha, se houve."""
    seller_id = UUID(str(item["seller_organization_id"]))
    outbox_id = UUID(str(item["id"]))
    fence = int(item["lease_fence"])
    try:
        mensagem, message_id = montar_mensagem(item, settings)
        await to_thread.run_sync(transporte.enviar, mensagem)
    except FalhaDeEnvio as falha:
        await _registrar_falha(auth, settings, worker_id, seller_id, outbox_id, fence, falha)
        return falha
    except Exception as erro:  # noqa: BLE001 — a classificação decide, e o log não vaza.
        classificada = classificar_erro(erro)
        await _registrar_falha(auth, settings, worker_id, seller_id, outbox_id, fence, classificada)
        return classificada
    try:
        async with auth.trusted_domain_session(
            worker_envelope(settings, worker_id, seller_id)
        ) as session:
            await session.execute(
                text(
                    "select app_private.complete_email_outbox("
                    ":worker,:label,:seller,:outbox,:fence,:message_id,:request_id)"
                ),
                {
                    "worker": worker_id,
                    "label": _WORKER_LABEL,
                    "seller": seller_id,
                    "outbox": outbox_id,
                    "fence": fence,
                    "message_id": message_id,
                    "request_id": uuid4(),
                },
            )
    except DBAPIError:
        # O e-mail saiu e o registro não. O lease expira e a fila tenta de
        # novo com o MESMO Message-ID, que o cliente de quem recebe usa para
        # reconhecer a duplicata. Fica no log para a reconciliação.
        logger.error("email_sent_but_not_recorded", outbox_id=str(outbox_id), message_id=message_id)
    return None


async def _registrar_falha(
    auth: InternalRequestAuthenticator,
    settings: Settings,
    worker_id: UUID,
    seller_id: UUID,
    outbox_id: UUID,
    fence: int,
    falha: FalhaDeEnvio,
) -> None:
    logger.warning(
        "email_not_sent",
        outbox_id=str(outbox_id),
        error_code=falha.error_code,
        permanent=falha.permanent,
        smtp_code=falha.smtp_code,
        # Host e porta, como manda a EMAIL_POLICY.md §5 — nunca usuário, senha
        # ou o diálogo SMTP.
        smtp_host=settings.email_smtp_host,
        smtp_port=settings.email_smtp_port_number,
    )
    try:
        async with auth.trusted_domain_session(
            worker_envelope(settings, worker_id, seller_id)
        ) as session:
            await session.scalar(
                text(
                    "select app_private.fail_email_outbox("
                    ":worker,:label,:seller,:outbox,:fence,:error,:delay,:permanent,:request_id)"
                ),
                {
                    "worker": worker_id,
                    "label": _WORKER_LABEL,
                    "seller": seller_id,
                    "outbox": outbox_id,
                    "fence": fence,
                    "error": falha.error_code,
                    "delay": falha.delay_seconds,
                    "permanent": falha.permanent,
                    "request_id": uuid4(),
                },
            )
    except DBAPIError:
        logger.warning("email_failure_record_unavailable", outbox_id=str(outbox_id))


def worker_envelope(
    settings: Settings, worker_id: UUID, seller_id: UUID | None = None
) -> ServiceEnvelope:
    now = int(time.time())
    return ServiceEnvelope(
        version=1,
        kid="api-internal",
        issuer=settings.internal_issuer,
        audience=settings.internal_audience,
        session_ref="email-worker",
        session_epoch=1,
        identity=ServiceIdentity(
            issuer=settings.identity_issuer,
            sub=WORKER_SUBJECT,
            user_id=worker_id,
        ),
        capability=WORKER_CAPABILITY,
        seller_organization_id=seller_id,
        method="POST",
        path="/v1/internal/email-worker",
        query_sha256=hashlib.sha256(b"").hexdigest(),
        body_sha256=hashlib.sha256(b"").hexdigest(),
        request_id=uuid4(),
        iat=now,
        exp=now + 60,
    )


def worker_identity(settings: Settings) -> UUID:
    return uuid5(NAMESPACE_URL, f"{settings.identity_issuer}\0{WORKER_SUBJECT}")
