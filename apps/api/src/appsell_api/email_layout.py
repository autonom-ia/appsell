"""Moldura dos e-mails transacionais — porta fiel de ``lib/email/layout-transacional.ts``.

A moldura existe em dois lugares de propósito: a TS é onde se desenha e se vê
no navegador; esta é a que o worker usa, porque o worker vive na API. Um
fixture compartilhado (``tests/fixtures/email/``) é renderizado pelos dois e
tem de dar o mesmo HTML e o mesmo texto, byte a byte — se um mudar sem o
outro, o teste de paridade acusa.

Regras que a moldura carrega (docs/architecture/EMAIL_POLICY.md): remetente
identificado, "você recebe porque <motivo>" obrigatório, sem descadastro, sem
rastreador; tudo que vem de fora escapado; botão só na origem pública, sem
credencial e sem parâmetro cujo nome denuncie segredo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

TAGLINE = (
    "Autonom.ia Sell é a plataforma da Autonom.ia para criar, vender e entregar produtos digitais."
)
EMPRESA = "Autonom.ia Tecnologia Ltda (CNPJ 59.303.298/0001-51)"
CONTATO_DADOS = "contato@autonomia.site"
FONTE = "'Helvetica Neue', Helvetica, Arial, sans-serif"
LARGURA = 600

_CORES = {
    "navy": "#001b43",
    "ink": "#071321",
    "cream": "#f7f8f4",
    "laranja": "#c9420d",
    "cinza": "#5b6675",
    "linha": "#e3e7ee",
}

# C0, DEL, C1 (inclui NEL), separadores Unicode de linha/parágrafo e controles
# bidirecionais — a mesma classe da TS. Escritos como escapes de propósito:
# um byte de controle literal no fonte torna o arquivo irrevisável.
_CONTROLE = re.compile(
    "[\\u0000-\\u001f\\u007f-\\u009f\\u061c\\u200e\\u200f\\u2028\\u2029\\u202a-\\u202e\\u2066-\\u2069]"
)

_PALAVRA_PROIBIDA = frozenset(
    {
        "token",
        "tokens",
        "jwt",
        "sess",
        "session",
        "sessao",
        "sid",
        "senha",
        "password",
        "passwd",
        "pwd",
        "pass",
        "secret",
        "secrets",
        "cookie",
        "cookies",
        "auth",
        "authorization",
        "bearer",
        "key",
        "apikey",
        "accesskey",
        "credential",
        "credentials",
    }
)
_PALAVRA_LONGA_PROIBIDA = re.compile("token|session|password|secret|credential|cookie|bearer", re.I)
_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_SEPARADOR = re.compile(r"[^A-Za-z0-9]+")


class EmailInvalido(ValueError):
    """Entrada que a moldura recusa. A mensagem nunca repete o valor recebido."""


@dataclass(frozen=True)
class Acao:
    rotulo: str
    url: str


@dataclass(frozen=True)
class Detalhe:
    rotulo: str
    valor: str


@dataclass(frozen=True)
class EmailTransacional:
    assunto: str
    titulo: str
    paragrafos: tuple[str, ...]
    motivo: str
    eyebrow: str | None = None
    acao: Acao | None = None
    detalhes: tuple[Detalhe, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EmailRenderizado:
    html: str
    text: str


def escapar(valor: str) -> str:
    return (
        valor.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def origem_publica(origem: str) -> str:
    """Só https, sem credencial, sem caminho: a origem entra em src/href."""
    partes = urlsplit(origem)
    if partes.scheme != "https" or not partes.hostname or partes.username or partes.password:
        raise EmailInvalido("origem publica precisa ser https sem credencial")
    porta = f":{partes.port}" if partes.port and partes.port != 443 else ""
    return f"https://{partes.hostname.lower()}{porta}"


def _uma_linha(campo: str, valor: str) -> str:
    if _CONTROLE.search(valor):
        raise EmailInvalido(f"{campo} nao pode conter quebra de linha nem caractere de controle")
    return valor


def _palavras(nome: str) -> list[str]:
    return [p.lower() for p in _SEPARADOR.split(_CAMEL.sub(r"\1 \2", nome)) if p]


def _nome_proibido(nome: str) -> bool:
    return any(p in _PALAVRA_PROIBIDA for p in _palavras(nome)) or bool(
        _PALAVRA_LONGA_PROIBIDA.search(nome)
    )


def _url_de_acao(url: str, origem: str) -> str:
    erro = EmailInvalido("acao.url precisa ser uma URL https absoluta na origem publica")
    partes = urlsplit(url)
    if partes.scheme != "https" or not partes.hostname:
        raise erro
    if partes.username or partes.password:
        raise erro
    porta = f":{partes.port}" if partes.port and partes.port != 443 else ""
    if f"https://{partes.hostname.lower()}{porta}" != origem:
        raise erro
    chaves = list(parse_qs(partes.query, keep_blank_values=True).keys())
    if partes.fragment:
        chaves.extend(parse_qs(partes.fragment, keep_blank_values=True).keys())
    if any(_nome_proibido(chave) for chave in chaves):
        raise EmailInvalido(
            "acao.url carrega parametro proibido pela politica (segredo de sessao ou de conta)"
        )
    # A TS devolve `new URL(url).toString()`, que só difere de `url` em
    # normalizações (barra final na origem pura). Mantemos a entrada intacta e
    # cobrimos a barra final, que é o único caso que os templates produzem.
    return url if partes.path else f"{url}/"


def _paragrafo(texto: str) -> str:
    return (
        f'<p style="margin:0 0 16px;font-size:16px;line-height:1.6;color:{_CORES["ink"]};">'
        f"{escapar(texto)}</p>"
    )


def _tabela_detalhes(detalhes: tuple[Detalhe, ...]) -> str:
    if not detalhes:
        return ""
    linhas = "\n".join(
        "<tr>\n"
        f'  <td style="padding:10px 0;border-top:1px solid {_CORES["linha"]};font-size:13px;'
        f'color:{_CORES["cinza"]};vertical-align:top;width:38%;">{escapar(d.rotulo)}</td>\n'
        f'  <td style="padding:10px 0 10px 12px;border-top:1px solid {_CORES["linha"]};'
        f'font-size:15px;font-weight:700;color:{_CORES["ink"]};vertical-align:top;">'
        f"{escapar(d.valor)}</td>\n"
        "</tr>"
        for d in detalhes
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:8px 0 24px;border-collapse:collapse;">\n'
        f"{linhas}\n</table>"
    )


def _botao(acao: Acao, origem: str) -> str:
    href = escapar(_url_de_acao(acao.url, origem))
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="margin:8px 0 24px;">\n<tr>\n'
        f'  <td bgcolor="{_CORES["laranja"]}" style="border-radius:8px;">\n'
        f'    <a href="{href}" style="display:inline-block;padding:15px 28px;font-family:{FONTE};'
        f'font-size:16px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:8px;">'
        f"{escapar(acao.rotulo)} &rarr;</a>\n  </td>\n</tr>\n<tr>\n"
        f'  <td style="padding-top:12px;font-family:{FONTE};font-size:12px;line-height:1.5;'
        f'color:{_CORES["cinza"]};">Se o botão não abrir, copie este endereço no navegador:<br>'
        f'<a href="{href}" style="color:{_CORES["navy"]};word-break:break-all;">{href}</a></td>\n'
        "</tr>\n</table>"
    )


def _cabecalho(origem: str) -> str:
    icone = f"{origem}/lp/icone.png"
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{_CORES["navy"]}" style="background:{_CORES["navy"]};">\n<tr>\n'
        '  <td style="padding:28px 32px;">\n'
        '    <table role="presentation" cellpadding="0" cellspacing="0" border="0">\n    <tr>\n'
        '      <td style="padding-right:12px;vertical-align:middle;">'
        f'<img src="{icone}" width="36" '
        'height="36" alt="" style="display:block;width:36px;height:36px;border:0;"></td>\n'
        f'      <td style="vertical-align:middle;font-family:{FONTE};">\n'
        '        <span style="display:block;font-size:19px;font-weight:900;'
        'letter-spacing:-0.02em;line-height:1;color:#ffffff;">Autonom.ia</span>\n'
        '        <span style="display:block;margin-top:5px;font-size:10px;font-weight:900;'
        'letter-spacing:1.6px;text-transform:uppercase;color:#a9b4c6;">Sell</span>\n'
        "      </td>\n    </tr>\n    </table>\n  </td>\n</tr>\n</table>"
    )


def _rodape(motivo: str, origem: str) -> str:
    marca = f"{origem}/lp/marca.png"
    privacidade = f"{origem}/privacidade"
    cinza = _CORES["cinza"]
    navy = _CORES["navy"]
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        "\n<tr>\n"
        f'  <td style="padding:28px 32px 8px;font-family:{FONTE};">\n'
        f'    <img src="{marca}" width="140" alt="Autonom.ia" style="display:block;width:140px;'
        'height:auto;border:0;margin-bottom:12px;">\n'
        f'    <p style="margin:0 0 18px;font-size:13px;line-height:1.6;color:{cinza};">'
        f"{escapar(TAGLINE)}</p>\n"
        f'    <p style="margin:0 0 8px;font-size:12px;line-height:1.6;color:{cinza};">'
        f"Autonom.ia Sell &middot; enviado por {escapar(EMPRESA)}</p>\n"
        f'    <p style="margin:0 0 8px;font-size:12px;line-height:1.6;color:{cinza};">'
        "Este é um e-mail transacional sobre a sua conta ou a sua compra; você o recebe porque "
        f"{escapar(motivo)}. Ele não depende de inscrição e não tem descadastro.</p>\n"
        f'    <p style="margin:0 0 24px;font-size:12px;line-height:1.6;color:{cinza};">'
        f'Dúvidas sobre seus dados: <a href="mailto:{CONTATO_DADOS}" style="color:{navy};">'
        f'{CONTATO_DADOS}</a> &middot; <a href="{privacidade}" style="color:{navy};">'
        "Política de Privacidade</a></p>\n  </td>\n</tr>\n</table>"
    )


def _html(email: EmailTransacional, origem: str) -> str:
    eyebrow = (
        '<p style="margin:0 0 14px;font-size:11px;font-weight:900;letter-spacing:1.6px;'
        f'text-transform:uppercase;color:{_CORES["laranja"]};">{escapar(email.eyebrow)}</p>'
        if email.eyebrow
        else ""
    )
    corpo = "\n".join(_paragrafo(p) for p in email.paragrafos)
    detalhes = _tabela_detalhes(email.detalhes)
    acao = _botao(email.acao, origem) if email.acao else ""
    preheader = escapar(email.paragrafos[0] if email.paragrafos else email.titulo)
    cream = _CORES["cream"]
    return (
        '<!DOCTYPE html>\n<html lang="pt-BR">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        '<meta name="supported-color-schemes" content="light">\n'
        f"<title>{escapar(email.assunto)}</title>\n</head>\n"
        f'<body style="margin:0;padding:0;background:{cream};" bgcolor="{cream}">\n'
        '<div style="display:none;max-height:0;max-width:0;overflow:hidden;opacity:0;font-size:1px;'
        f'line-height:1px;color:transparent;mso-hide:all;">{preheader}</div>\n'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{cream}" style="background:{cream};">\n<tr>\n'
        '  <td align="center" style="padding:32px 12px;">\n'
        f'    <table role="presentation" width="{LARGURA}" cellpadding="0" cellspacing="0" '
        'border="0" '
        f'style="width:100%;max-width:{LARGURA}px;background:#ffffff;border-radius:12px;'
        f'overflow:hidden;font-family:{FONTE};">\n'
        f"    <tr><td>{_cabecalho(origem)}</td></tr>\n    <tr>\n"
        '      <td style="padding:36px 32px 12px;">\n'
        f"        {eyebrow}\n"
        '        <h1 style="margin:0 0 20px;font-size:26px;line-height:1.25;font-weight:900;'
        f'letter-spacing:-0.02em;color:{_CORES["navy"]};">{escapar(email.titulo)}</h1>\n'
        f"        {corpo}\n        {detalhes}\n        {acao}\n      </td>\n    </tr>\n"
        f'    <tr><td style="padding:0 32px;"><div style="height:1px;background:{_CORES["linha"]};'
        'line-height:1px;font-size:1px;">&nbsp;</div></td></tr>\n'
        f"    <tr><td>{_rodape(email.motivo, origem)}</td></tr>\n"
        "    </table>\n  </td>\n</tr>\n</table>\n</body>\n</html>"
    )


def _text(email: EmailTransacional, origem: str) -> str:
    linhas: list[str] = ["AUTONOM.IA SELL", ""]
    if email.eyebrow:
        linhas.append(email.eyebrow.upper())
    linhas.extend([email.titulo, ""])
    for paragrafo in email.paragrafos:
        linhas.extend([paragrafo, ""])
    if email.detalhes:
        linhas.extend(f"{d.rotulo}: {d.valor}" for d in email.detalhes)
        linhas.append("")
    if email.acao:
        linhas.extend([f"{email.acao.rotulo}: {_url_de_acao(email.acao.url, origem)}", ""])
    linhas.extend(
        [
            "--",
            TAGLINE,
            f"Autonom.ia Sell · enviado por {EMPRESA}",
            "Este é um e-mail transacional sobre a sua conta ou a sua compra; você o recebe "
            f"porque {email.motivo}. Ele não depende de inscrição e não tem descadastro.",
            f"Dúvidas sobre seus dados: {CONTATO_DADOS} · Política de Privacidade: "
            f"{origem}/privacidade",
        ]
    )
    return "\n".join(linhas)


def _validar(email: EmailTransacional) -> None:
    if not email.motivo.strip():
        raise EmailInvalido("motivo e obrigatorio: o rodape precisa dizer por que a pessoa recebe")
    _uma_linha("assunto", email.assunto)
    _uma_linha("titulo", email.titulo)
    _uma_linha("motivo", email.motivo)
    if email.eyebrow is not None:
        _uma_linha("eyebrow", email.eyebrow)
    if email.acao is not None:
        _uma_linha("acao.rotulo", email.acao.rotulo)
    for paragrafo in email.paragrafos:
        _uma_linha("paragrafo", paragrafo)
    for detalhe in email.detalhes:
        _uma_linha("detalhe.rotulo", detalhe.rotulo)
        _uma_linha("detalhe.valor", detalhe.valor)


def renderizar(email: EmailTransacional, origem: str) -> EmailRenderizado:
    _validar(email)
    base = origem_publica(origem)
    return EmailRenderizado(html=_html(email, base), text=_text(email, base))


def _limpo(valor: object, maximo: int) -> str:
    """Texto vindo do banco: controle vira espaço, e o tamanho é limitado."""
    texto = _CONTROLE.sub(" ", str(valor or "")).strip()
    return texto[:maximo]


def compra_confirmada(
    payload: dict[str, object], recipient_name: object, origem: str
) -> EmailTransacional:
    """O primeiro e-mail do canal: acesso liberado depois da compra."""
    if payload.get("schema_version") != 1:
        raise EmailInvalido("payload de compra confirmada em versao desconhecida")
    curso = _limpo(payload.get("product_title"), 160) or "o curso"
    loja = _limpo(payload.get("seller_display_name"), 120) or "a loja"
    nome = _limpo(recipient_name, 80)
    saudacao = f"Olá, {nome}. " if nome else "Olá. "
    return EmailTransacional(
        assunto=f"Seu acesso a {curso} está liberado",
        eyebrow="Do lado de quem comprou",
        titulo="A compra foi confirmada. O acesso já é seu.",
        paragrafos=(
            f"{saudacao}O curso {curso}, da loja {loja}, foi liberado no seu nome. Não precisa "
            "de link nem de senha: entre com a mesma conta que usou na compra e o curso estará "
            "na sua biblioteca.",
            "Você assiste vídeo e PDF pelo navegador do celular, sem instalar nada.",
        ),
        detalhes=(Detalhe("Curso", curso), Detalhe("Loja", loja)),
        acao=Acao("Abrir o curso", f"{origem_publica(origem)}/library"),
        motivo=f"comprou {curso} na loja {loja}",
    )
