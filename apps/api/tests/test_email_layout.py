from __future__ import annotations

import json
from pathlib import Path

import pytest

from appsell_api import email_layout
from appsell_api.email_layout import Acao, Detalhe, EmailInvalido, EmailTransacional, renderizar

# A moldura existe em TS (lib/email/layout-transacional.ts) e aqui. Os fixtures
# em tests/fixtures/email/ são renderizados pelos dois lados e comparados byte
# a byte com o golden gerado pela TS — se um lado mudar sem o outro, este
# teste e o tests/email-layout-parity.test.ts acusam.
#
# Caracteres de controle montados com chr() de propósito: um byte literal no
# fonte torna o arquivo irrevisável.

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "email"
ORIGEM = "https://sell.autonomia.site"
NUL, ESC, NEL, LSEP, RLO, LRM = chr(0), chr(0x1B), chr(0x85), chr(0x2028), chr(0x202E), chr(0x200E)


def _carregar(nome: str) -> tuple[str, EmailTransacional]:
    dados = json.loads((FIXTURES / f"{nome}.json").read_text(encoding="utf-8"))
    corpo = dados["email"]
    email = EmailTransacional(
        assunto=corpo["assunto"],
        titulo=corpo["titulo"],
        paragrafos=tuple(corpo["paragrafos"]),
        motivo=corpo["motivo"],
        eyebrow=corpo.get("eyebrow"),
        acao=Acao(**corpo["acao"]) if "acao" in corpo else None,
        detalhes=tuple(Detalhe(**d) for d in corpo.get("detalhes", [])),
    )
    return dados["origem"], email


@pytest.mark.parametrize("nome", ["compra-confirmada", "minimo"])
def test_paridade_com_a_moldura_ts(nome: str) -> None:
    origem, email = _carregar(nome)
    renderizado = renderizar(email, origem)
    assert renderizado.html == (FIXTURES / f"{nome}.html").read_text(encoding="utf-8")
    assert renderizado.text == (FIXTURES / f"{nome}.txt").read_text(encoding="utf-8")


BASE = EmailTransacional(
    assunto="Assunto",
    titulo="Título",
    paragrafos=("Um parágrafo.",),
    motivo="tem conta no Autonom.ia Sell",
)
HOSTIL = "&<>\"' fim"
HOSTIL_ESCAPADO = "&amp;&lt;&gt;&quot;&#39; fim"


def _variante(**caso: object) -> EmailTransacional:
    return EmailTransacional(**{**BASE.__dict__, **caso})


def test_escapa_todo_campo_externo() -> None:
    html = renderizar(
        EmailTransacional(
            assunto=HOSTIL,
            eyebrow=HOSTIL,
            titulo=f"<script>alert(1)</script> {HOSTIL}",
            paragrafos=(f"<b>negrito</b> {HOSTIL}",),
            detalhes=(Detalhe(f"<i>Curso</i> {HOSTIL}", f"Foto & Vídeo {HOSTIL}"),),
            acao=Acao(f"<img src=x onerror=alert(1)> {HOSTIL}", f"{ORIGEM}/library?a=1&b=2"),
            motivo=HOSTIL,
        ),
        ORIGEM,
    ).html
    assert "<script>" not in html and "<b>negrito</b>" not in html and "<img src=x" not in html
    assert html.count(HOSTIL_ESCAPADO) == 9
    assert HOSTIL not in html
    assert f'href="{ORIGEM}/library?a=1&amp;b=2"' in html


def test_recusa_motivo_vazio_e_controle_em_qualquer_campo() -> None:
    with pytest.raises(EmailInvalido, match="motivo"):
        renderizar(EmailTransacional("a", "t", ("p",), "   "), ORIGEM)
    casos: list[dict[str, object]] = [
        {"motivo": "linha um\nlinha dois"},
        {"assunto": f"assunto{NUL}nulo"},
        {"titulo": f"separador{LSEP}de linha"},
        {"eyebrow": f"nel{NEL}aqui"},
        {"motivo": f"bidi{RLO}invertido"},
        {"motivo": f"marca{LRM}esquerda"},
        {"paragrafos": ("ok", "quebra\ndentro")},
        {"acao": Acao("abrir\n", f"{ORIGEM}/x")},
        {"detalhes": (Detalhe("Curso", "a\tb"),)},
    ]
    for caso in casos:
        with pytest.raises(EmailInvalido, match="controle"):
            renderizar(_variante(**caso), ORIGEM)


def test_botao_so_na_origem_publica_sem_credencial_nem_segredo() -> None:
    recusadas = [
        "javascript:alert(1)",
        "http://sell.autonomia.site/x",
        "/library",
        "https://evil.com/",
        "https://sell.autonomia.site@evil.com/",
        "https://u:p@sell.autonomia.site/library",
        "https://sell.autonomia.site.evil.com/",
        "https://sell.autonomia.site:8443/",
    ]
    for url in recusadas:
        with pytest.raises(EmailInvalido, match="https"):
            renderizar(_variante(acao=Acao("Abrir", url)), ORIGEM)
    proibidas = [
        f"{ORIGEM}/x?session_token=a",
        f"{ORIGEM}/x?token=a",
        f"{ORIGEM}/x?Access_Token=a",
        f"{ORIGEM}/invite#access_token=a",
        f"{ORIGEM}/x?jwt=a",
        f"{ORIGEM}/x?credential=a",
        f"{ORIGEM}/x?accessKey=a",
        f"{ORIGEM}/x?refreshToken=a",
        f"{ORIGEM}/x?x-auth=a",
        f"{ORIGEM}/x?senha_nova=a",
        f"{ORIGEM}/x?sessiontoken=a",
    ]
    for url in proibidas:
        with pytest.raises(EmailInvalido, match="proibido") as erro:
            renderizar(_variante(acao=Acao("Abrir", url)), ORIGEM)
        assert "=a" not in str(erro.value)
    permitidas = [
        f"{ORIGEM}/invite#t=abc",
        f"{ORIGEM}/redefinir?codigo=abc",
        f"{ORIGEM}/avaliacao?assessment_id=1",
        f"{ORIGEM}/blog?author=ana",
        f"{ORIGEM}/x?passo=2",
        ORIGEM,
    ]
    for url in permitidas:
        renderizar(_variante(acao=Acao("Abrir", url)), ORIGEM)
    assert f"Abrir: {ORIGEM}/" in renderizar(_variante(acao=Acao("Abrir", ORIGEM)), ORIGEM).text


def test_origem_publica_normalizada_e_recusada_quando_hostil() -> None:
    assert (
        email_layout.origem_publica("https://preview.example.test/")
        == "https://preview.example.test"
    )
    assert email_layout.origem_publica('https://x.test/"onload="alert(1)') == "https://x.test"
    for ruim in ["http://x.test", "https://u:p@x.test", "sem-esquema", ""]:
        with pytest.raises(EmailInvalido):
            email_layout.origem_publica(ruim)


def test_compra_confirmada_monta_o_email_a_partir_do_payload() -> None:
    payload = {
        "schema_version": 1,
        "entitlement_id": "x",
        "product_title": f"Curso{ESC}com controle",
        "seller_display_name": "Loja <Ltda>",
    }
    email = email_layout.compra_confirmada(payload, "Ana\n", ORIGEM)
    assert email.assunto == "Seu acesso a Curso com controle está liberado"
    assert email.paragrafos[0].startswith(
        "Olá, Ana. O curso Curso com controle, da loja Loja <Ltda>,"
    )
    assert email.detalhes == (
        Detalhe("Curso", "Curso com controle"),
        Detalhe("Loja", "Loja <Ltda>"),
    )
    assert email.acao == Acao("Abrir o curso", f"{ORIGEM}/library")
    assert email.motivo == "comprou Curso com controle na loja Loja <Ltda>"
    assert "Loja &lt;Ltda&gt;" in renderizar(email, ORIGEM).html
    sem_nome = email_layout.compra_confirmada(payload, None, ORIGEM)
    assert sem_nome.paragrafos[0].startswith("Olá. O curso")
    with pytest.raises(EmailInvalido, match="versao"):
        email_layout.compra_confirmada({"schema_version": 2}, None, ORIGEM)
