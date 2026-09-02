from __future__ import annotations

import asyncio
import smtplib
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import SecretStr

from appsell_api import email_worker
from appsell_api.config import Settings
from appsell_api.email_worker import FalhaDeEnvio, classificar_erro, montar_mensagem

SEGREDO = "SENHA-QUE-NAO-PODE-APARECER"
_NOMES = (
    "SMTP_HOST_TRANSACIONAL",
    "SMTP_PORT_TRANSACIONAL",
    "SMTP_SECURE_TRANSACIONAL",
    "SMTP_USER_TRANSACIONAL",
    "SMTP_PASS_TRANSACIONAL",
    "NEXT_PUBLIC_SITE_ORIGIN",
)


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for nome in _NOMES:
        monkeypatch.delenv(nome, raising=False)
    for nome, valor in env.items():
        monkeypatch.setenv(nome, valor)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _configurado(monkeypatch: pytest.MonkeyPatch, **extra: str) -> Settings:
    env = {
        "SMTP_HOST_TRANSACIONAL": "smtp.example.test",
        "SMTP_USER_TRANSACIONAL": "sell@autonomia.site",
        "SMTP_PASS_TRANSACIONAL": SEGREDO,
        **extra,
    }
    return _settings(monkeypatch, **env)


def test_settings_leem_os_nomes_da_b0_sem_prefixo(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _configurado(
        monkeypatch,
        SMTP_PORT_TRANSACIONAL="587",
        SMTP_SECURE_TRANSACIONAL="false",
        NEXT_PUBLIC_SITE_ORIGIN="https://preview.example.test/",
    )
    assert settings.email_configured
    assert settings.email_smtp_port_number == 587 and settings.email_smtp_secure_flag is False
    assert settings.email_smtp_password.get_secret_value() == SEGREDO
    assert SEGREDO not in repr(settings)
    assert settings.email_public_origin == "https://preview.example.test/"


def test_settings_nunca_derrubam_a_api_e_dizem_por_que_o_worker_nao_inicia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Configuração errada desliga o canal com motivo; jamais levanta na carga
    # das Settings, porque isso derrubaria a API inteira por causa de e-mail.
    assert _settings(monkeypatch).email_disabled_reason == "smtp_not_configured"
    parcial = _settings(monkeypatch, SMTP_HOST_TRANSACIONAL="smtp.example.test")
    assert parcial.email_disabled_reason == "smtp_partial" and not parcial.email_configured
    outro = _configurado(monkeypatch, SMTP_USER_TRANSACIONAL="sell.comercial@autonomia.site")
    assert outro.email_disabled_reason is None  # local: Mailpit sem conta
    # Fora de local/ci a igualdade vale. model_copy troca só o ambiente, sem
    # revalidar o resto (staging exige storage, auth interna etc.).
    em_staging = outro.model_copy(update={"environment": "staging"})
    assert em_staging.email_disabled_reason == "sender_mismatch"
    porta = _configurado(monkeypatch, SMTP_PORT_TRANSACIONAL="abc")
    assert porta.email_disabled_reason == "smtp_port_invalid"
    tls = _configurado(monkeypatch, SMTP_SECURE_TRANSACIONAL="talvez")
    assert tls.email_disabled_reason == "smtp_secure_invalid"
    origem_porta = _configurado(
        monkeypatch, NEXT_PUBLIC_SITE_ORIGIN="https://sell.autonomia.site:abc"
    )
    assert origem_porta.email_disabled_reason == "public_origin_invalid"
    origem = _configurado(monkeypatch, NEXT_PUBLIC_SITE_ORIGIN="http://sell.autonomia.site")
    assert origem.email_disabled_reason == "public_origin_invalid"
    assert _configurado(monkeypatch).email_disabled_reason is None


def test_classificacao_de_erro_nao_carrega_o_dialogo_smtp() -> None:
    auth = smtplib.SMTPAuthenticationError(
        535, f"5.7.8 authentication failed pass={SEGREDO}".encode()
    )
    falha = classificar_erro(auth)
    assert falha == FalhaDeEnvio("smtp_auth", permanent=False, delay_seconds=600, smtp_code=535)
    assert SEGREDO not in str(falha) and SEGREDO not in repr(falha)

    recusado = smtplib.SMTPRecipientsRefused({"a@b.test": (550, b"no such user")})
    assert classificar_erro(recusado).permanent is True
    assert classificar_erro(recusado).error_code == "recipient_refused"
    adiado = smtplib.SMTPRecipientsRefused({"a@b.test": (450, b"try later")})
    assert classificar_erro(adiado) == FalhaDeEnvio(
        "recipient_deferred", permanent=False, delay_seconds=300, smtp_code=450
    )
    assert (
        classificar_erro(smtplib.SMTPResponseException(421, b"busy")).error_code == "smtp_temporary"
    )
    assert classificar_erro(smtplib.SMTPResponseException(552, b"too big")).permanent is True
    assert classificar_erro(OSError("conexao recusada")).error_code == "smtp_unreachable"
    # STARTTLS/AUTH/SMTPUTF8 recusados: quase sempre é o canal, não o item — pausa e retenta.
    nao_suportado = classificar_erro(smtplib.SMTPNotSupportedError("STARTTLS"))
    assert nao_suportado.error_code == "smtp_unsupported" and nao_suportado.permanent is False
    # Parte local Unicode passa (o ingresso aceita); o SMTP decide.
    assert email_worker.destinatario_valido("josé.maría@exemplo.com.br")
    assert classificar_erro(RuntimeError("?")).error_code == "send_unexpected"


def _item(**extra: object) -> dict[str, object]:
    return {
        "seller_organization_id": "ee000000-0000-4000-8000-000000000200",
        "id": "ee000000-0000-4000-8000-000000000201",
        "kind": "purchase_confirmed",
        "lease_fence": 1,
        "attempt_count": 1,
        "recipient_email": "mariana@example.test",
        "recipient_name": "Mariana",
        "payload": {
            "schema_version": 1,
            "entitlement_id": "ee000000-0000-4000-8000-000000000202",
            "catalog_product_id": "ee000000-0000-4000-8000-000000000203",
            "product_title": "Fotografia com Luz Natural",
            "seller_display_name": "Luz & Sombra",
            "seller_slug": "luz-e-sombra",
        },
        **extra,
    }


def test_monta_mensagem_com_texto_e_html_e_message_id(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _configurado(monkeypatch)
    mensagem, message_id = montar_mensagem(_item(), settings)
    # formataddr cita o nome porque "Autonom.ia" tem ponto; o cliente mostra sem aspas.
    assert mensagem["From"] == '"Autonom.ia Sell" <sell@autonomia.site>'
    assert mensagem["To"] == "mariana@example.test"
    assert mensagem["Reply-To"] == "sell@autonomia.site"
    assert mensagem["Subject"] == "Seu acesso a Fotografia com Luz Natural está liberado"
    assert mensagem["Message-ID"] == f"<{message_id}>"
    # Estável por item: a segunda tentativa de um envio que saiu sem registro
    # leva o mesmo id, e o cliente de quem recebe reconhece a duplicata.
    assert message_id == "ee000000-0000-4000-8000-000000000201@autonomia.site"
    partes = {parte.get_content_type(): parte.get_content() for parte in mensagem.iter_parts()}
    assert "Olá, Mariana." in partes["text/plain"]
    assert "Luz &amp; Sombra" in partes["text/html"]
    assert SEGREDO not in mensagem.as_string()


def test_monta_mensagem_classifica_entrada_impossivel_como_permanente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configurado(monkeypatch)
    with pytest.raises(FalhaDeEnvio) as erro:
        montar_mensagem(_item(kind="outro"), settings)
    assert erro.value.error_code == "kind_unknown" and erro.value.permanent
    with pytest.raises(FalhaDeEnvio) as erro:
        montar_mensagem(_item(payload={"schema_version": 9}), settings)
    assert erro.value.error_code == "render_failed" and erro.value.permanent
    # Dois endereços num campo só virariam dois destinatários no smtplib.
    for ruim in [
        "a@b.test,c@d.test",
        "a@b.test;c@d.test",
        "Nome <a@b.test>",
        "a@b",
        "",
        "a@b@c.test",
    ]:
        with pytest.raises(FalhaDeEnvio) as erro:
            montar_mensagem(_item(recipient_email=ruim), settings)
        assert erro.value.error_code == "recipient_invalid" and erro.value.permanent


@pytest.mark.asyncio
async def test_worker_nao_inicia_sem_smtp_e_registra(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    eventos: list[tuple[str, dict[str, object]]] = []

    class Logger:
        def warning(self, evento: str, **campos: object) -> None:
            eventos.append((evento, campos))

    monkeypatch.setattr(email_worker, "logger", Logger())
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, internal_auth=object()))
    await asyncio.wait_for(
        email_worker.run_email_worker_loop(app, asyncio.Event()),  # type: ignore[arg-type]
        timeout=1,
    )
    assert eventos == [("email_worker_disabled", {"reason": "smtp_not_configured"})]
    eventos.clear()
    parcial = _settings(monkeypatch, SMTP_HOST_TRANSACIONAL="smtp.example.test")
    app = SimpleNamespace(state=SimpleNamespace(settings=parcial, internal_auth=object()))
    await asyncio.wait_for(
        email_worker.run_email_worker_loop(app, asyncio.Event()),  # type: ignore[arg-type]
        timeout=1,
    )
    assert eventos == [("email_worker_disabled", {"reason": "smtp_partial"})]


def test_transporte_nao_expoe_a_senha(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _configurado(monkeypatch)
    assert isinstance(settings.email_smtp_password, SecretStr)
    transporte = email_worker.Transporte(settings)
    assert SEGREDO not in repr(transporte)


class _Sessao:
    """Registra as chamadas SQL do worker; nunca fala com banco."""

    def __init__(self, registro: list[tuple[str, dict[str, object]]]) -> None:
        self._registro = registro

    async def execute(self, statement: object, params: dict[str, object]) -> None:
        self._registro.append((str(statement), params))

    async def scalar(self, statement: object, params: dict[str, object]) -> str:
        self._registro.append((str(statement), params))
        return "retry"


class _Auth:
    def __init__(self) -> None:
        self.chamadas: list[tuple[str, dict[str, object]]] = []
        self.envelopes: list[object] = []

    def trusted_domain_session(self, envelope: object) -> object:
        self.envelopes.append(envelope)
        auth = self

        class _Contexto:
            async def __aenter__(self) -> _Sessao:
                return _Sessao(auth.chamadas)

            async def __aexit__(self, *_: object) -> None:
                return None

        return _Contexto()


class _TransporteFalso:
    def __init__(self, erro: BaseException | None = None) -> None:
        self.erro = erro
        self.enviadas: list[Any] = []

    def enviar(self, mensagem: object) -> None:
        if self.erro is not None:
            raise self.erro
        self.enviadas.append(mensagem)


@pytest.mark.asyncio
async def test_entregar_conclui_com_message_id_no_contexto_do_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configurado(monkeypatch)
    auth = _Auth()
    transporte = _TransporteFalso()
    worker = email_worker.worker_identity(settings)
    await email_worker._entregar(cast(Any, auth), settings, worker, cast(Any, transporte), _item())
    assert len(transporte.enviadas) == 1
    assert len(auth.chamadas) == 1
    sql, params = auth.chamadas[0]
    assert "complete_email_outbox" in sql
    assert str(params["message_id"]).endswith("@autonomia.site") and "<" not in str(
        params["message_id"]
    )
    assert params["fence"] == 1 and str(params["seller"]) == "ee000000-0000-4000-8000-000000000200"
    assert params["message_id"] == "ee000000-0000-4000-8000-000000000201@autonomia.site"
    assert transporte.enviadas[0]["To"] == "mariana@example.test"
    assert cast(Any, auth.envelopes[0]).seller_organization_id is not None


@pytest.mark.asyncio
async def test_entregar_registra_falha_smtp_sem_vazar_e_nao_conclui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configurado(monkeypatch)
    eventos: list[tuple[str, dict[str, object]]] = []

    class Logger:
        def warning(self, evento: str, **campos: object) -> None:
            eventos.append((evento, campos))

        def error(self, evento: str, **campos: object) -> None:
            eventos.append((evento, campos))

    monkeypatch.setattr(email_worker, "logger", Logger())
    auth = _Auth()
    erro = smtplib.SMTPAuthenticationError(535, f"pass={SEGREDO}".encode())
    worker = email_worker.worker_identity(settings)
    await email_worker._entregar(
        cast(Any, auth), settings, worker, cast(Any, _TransporteFalso(erro)), _item()
    )
    assert len(auth.chamadas) == 1
    sql, params = auth.chamadas[0]
    assert "fail_email_outbox" in sql and "complete_email_outbox" not in sql
    assert (
        params["error"] == "smtp_auth" and params["permanent"] is False and params["delay"] == 600
    )
    assert eventos[0][0] == "email_not_sent" and eventos[0][1]["error_code"] == "smtp_auth"
    assert SEGREDO not in repr(eventos) and SEGREDO not in repr(auth.chamadas)


@pytest.mark.asyncio
async def test_entregar_trata_render_impossivel_como_permanente(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _configurado(monkeypatch)
    monkeypatch.setattr(email_worker, "logger", SimpleNamespace(warning=lambda *a, **k: None))
    auth = _Auth()
    transporte = _TransporteFalso()
    worker = email_worker.worker_identity(settings)
    await email_worker._entregar(
        cast(Any, auth),
        settings,
        worker,
        cast(Any, transporte),
        _item(payload={"schema_version": 7}),
    )
    assert transporte.enviadas == []
    _, params = auth.chamadas[0]
    assert params["error"] == "render_failed" and params["permanent"] is True
