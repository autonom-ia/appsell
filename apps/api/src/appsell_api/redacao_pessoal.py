"""Redação de dado pessoal em texto livre de log (#549).

Complementa o redator de `logging_setup`, que pega o que tem cara de segredo
(token comprido, base64, credencial em URL, cartão). Aqui entram os casos que
o tamanho não denuncia: e-mail, CPF, CNPJ, a query string de uma URL e o
valor de um cabeçalho sensível que apareça no meio de um traceback.

Tudo com método de string, sem regex (regra do Rodrigo, 20/09/2026). CPF e
CNPJ só saem quando o dígito verificador bate: um número de 11 ou 14 dígitos
qualquer (carimbo de tempo, id de pedido) continua no log.

É rede de segurança, não garantia. A regra segue sendo não pôr dado pessoal
em campo de log.
"""

from __future__ import annotations

from collections.abc import Callable

REDIGIDO = "<REDACTED>"

# Caracteres que podem compor um e-mail. Qualquer outro encerra o trecho.
_CARACTERES_DE_EMAIL = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-@"
)
# Um documento pode vir pontuado: 123.456.789-09 ou 12.345.678/0001-95.
_PONTUACAO_DE_DOCUMENTO = frozenset(".-/")
# Onde termina a query de uma URL dentro de um texto.
_FIM_DE_URL = frozenset(" \t\r\n\"'<>`)]},")
# Cabeçalhos cujo valor nunca deve aparecer.
_CABECALHOS_SENSIVEIS = (
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
)
_FIM_DE_VALOR_DE_CABECALHO = frozenset("\r\n\"'")


def redigir_dado_pessoal(texto: str) -> str:
    texto = _sem_query(texto)
    texto = _sem_cabecalho_sensivel(texto)
    texto = _por_trecho(texto, _CARACTERES_DE_EMAIL.__contains__, _redigir_email)
    return _por_trecho(texto, _digitos_e_pontuacao, _redigir_documento)


def _sem_query(texto: str) -> str:
    """Corta a query de cada URL: ela carrega token, e-mail e assinatura."""
    saida: list[str] = []
    resto = texto
    while (inicio := resto.find("://")) >= 0:
        fim = inicio + 3
        while fim < len(resto) and resto[fim] not in _FIM_DE_URL:
            fim += 1
        url = resto[:fim]
        corte = url.find("?", inicio)
        # A segunda passada do redator vê `?<REDACTED>` e não repete.
        if corte >= 0 and not resto.startswith(REDIGIDO, corte + 1):
            url = url[: corte + 1] + REDIGIDO
        saida.append(url)
        resto = resto[fim:]
    saida.append(resto)
    return "".join(saida)


def _sem_cabecalho_sensivel(texto: str) -> str:
    minusculo = texto.lower()
    saida: list[str] = []
    posicao = 0
    while True:
        achado = _proximo_cabecalho(minusculo, posicao)
        if achado is None:
            break
        nome_fim, valor_inicio = achado
        valor_fim = valor_inicio
        while valor_fim < len(texto) and texto[valor_fim] not in _FIM_DE_VALOR_DE_CABECALHO:
            valor_fim += 1
        saida.append(texto[posicao:valor_inicio])
        if valor_fim > valor_inicio:
            saida.append(REDIGIDO)
        posicao = max(valor_fim, nome_fim)
    saida.append(texto[posicao:])
    return "".join(saida)


def _proximo_cabecalho(minusculo: str, desde: int) -> tuple[int, int] | None:
    """(fim do nome, início do valor) do próximo cabeçalho sensível com valor."""
    achados = [
        achado
        for nome in _CABECALHOS_SENSIVEIS
        if (achado := _primeiro_com_valor(minusculo, nome, desde)) is not None
    ]
    return min(achados) if achados else None


def _primeiro_com_valor(minusculo: str, nome: str, desde: int) -> tuple[int, int] | None:
    inicio = minusculo.find(nome, desde)
    while inicio >= 0:
        fim = inicio + len(nome)
        anterior = minusculo[inicio - 1] if inicio > 0 else " "
        valor = _inicio_do_valor(minusculo, fim)
        if not (anterior.isalnum() or anterior == "-") and valor is not None:
            return fim, valor
        inicio = minusculo.find(nome, fim)
    return None


def _inicio_do_valor(minusculo: str, fim_do_nome: int) -> int | None:
    posicao = fim_do_nome
    # `'authorization': 'Bearer x'` (repr de dict) ou `Authorization: Bearer x`.
    if posicao < len(minusculo) and minusculo[posicao] in "\"'":
        posicao += 1
    while posicao < len(minusculo) and minusculo[posicao] == " ":
        posicao += 1
    if posicao >= len(minusculo) or minusculo[posicao] not in ":=":
        return None
    posicao += 1
    while posicao < len(minusculo) and minusculo[posicao] in " \"'":
        posicao += 1
    return posicao


def _digitos_e_pontuacao(caractere: str) -> bool:
    return caractere.isdigit() or caractere in _PONTUACAO_DE_DOCUMENTO


def _por_trecho(texto: str, pertence: Callable[[str], bool], redigir: Callable[[str], str]) -> str:
    """Aplica `redigir` a cada trecho máximo de caracteres que `pertence`."""
    saida: list[str] = []
    trecho: list[str] = []
    for caractere in texto + "\0":
        if caractere != "\0" and pertence(caractere):
            trecho.append(caractere)
            continue
        if trecho:
            saida.append(redigir("".join(trecho)))
            trecho = []
        saida.append(caractere)
    return "".join(saida)[:-1]


def _redigir_email(trecho: str) -> str:
    local, arroba, dominio = trecho.rpartition("@")
    if not arroba or not local or "." not in dominio.strip("."):
        return trecho
    return REDIGIDO


def _redigir_documento(trecho: str) -> str:
    digitos = [int(caractere) for caractere in trecho if caractere.isdigit()]
    if len(digitos) == 11 and _cpf_valido(digitos):
        return REDIGIDO
    if len(digitos) == 14 and _cnpj_valido(digitos):
        return REDIGIDO
    return trecho


def _cpf_valido(d: list[int]) -> bool:
    if len(set(d)) == 1:
        return False
    for posicao in (9, 10):
        soma = sum(d[i] * (posicao + 1 - i) for i in range(posicao))
        if (soma * 10 % 11) % 10 != d[posicao]:
            return False
    return True


def _cnpj_valido(d: list[int]) -> bool:
    if len(set(d)) == 1:
        return False
    pesos = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    for posicao in (12, 13):
        usados = pesos[13 - posicao :]
        soma = sum(d[i] * usados[i] for i in range(posicao))
        resto = soma % 11
        if (0 if resto < 2 else 11 - resto) != d[posicao]:
            return False
    return True
