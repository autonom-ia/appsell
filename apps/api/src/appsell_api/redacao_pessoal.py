"""Redação de dado pessoal em texto livre de log (#549).

Complementa o redator de `logging_setup`, que pega o que tem cara de segredo
(token comprido, base64, credencial em URL, cartão). Aqui entram os casos que
o tamanho não denuncia: e-mail, CPF, CNPJ, a query string e o fragmento de uma
URL, e o valor de um cabeçalho sensível que apareça no meio de um traceback.

Tudo com método de string, sem regex (regra do Rodrigo, 20/09/2026). CPF e
CNPJ só saem quando o dígito verificador bate: um número de 11 ou 14 dígitos
qualquer (carimbo de tempo, id de pedido) continua no log.

Cada regra varre o texto uma vez só, avançando o índice. A primeira versão
procurava cada nome de cabeçalho de novo a cada achado, e 38 KB levavam 26 s.
O teto (`limitar_tamanho`) vem antes de qualquer regra.

É rede de segurança, não garantia. A regra segue sendo não pôr dado pessoal
em campo de log.
"""

from __future__ import annotations

from collections.abc import Callable

REDIGIDO = "<REDACTED>"

# Teto de um texto antes de redigir. Um traceback encadeado cabe com folga; o
# que passa disso perde o meio, nunca o começo nem o fim.
TETO_DE_TEXTO = 16_384
_PARTE_DO_COMECO = TETO_DE_TEXTO // 4
_MARCA_DE_CORTE = " [...truncated...] "
_ESPACOS = " \t\r\n"

# Além de letra e dígito de qualquer alfabeto (`josé@`, `exémplo.com`).
_PONTUACAO_DE_EMAIL = frozenset("._%+-@_")
# `%40` é o `@` codificado em URL.
_ARROBA_CODIFICADA = "%40"
# Um documento pode vir pontuado: 123.456.789-09 ou 12.345.678/0001-95.
_PONTUACAO_DE_DOCUMENTO = frozenset(".-/")
# Onde termina uma URL dentro de um texto.
_FIM_DE_URL = frozenset(" \t\r\n\"'<>`)]},")
# Onde começa o que não é caminho: a query e o fragmento.
_INICIO_DA_QUERY = "?#"

# Cabeçalhos cujo valor nunca deve aparecer.
CABECALHOS_SENSIVEIS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
    }
)
_ASPAS = "\"'"
# Linha de pilha não é continuação de cabeçalho (`  File "..."`, `    at f`).
_INICIO_DE_LINHA_DE_PILHA = ("File ", "at ")


def redigir_dado_pessoal(texto: str) -> str:
    texto = _sem_query(texto)
    texto = _sem_cabecalho_sensivel(texto)
    texto = _por_trecho(texto, _parte_de_email, _redigir_email)
    return _por_trecho(texto, _digito_ou_pontuacao, _redigir_documento)


def eh_cabecalho_sensivel(nome: str) -> bool:
    """`Authorization`, `set_cookie`, `HTTP_AUTHORIZATION` e afins."""
    normalizado = nome.lower().replace("_", "-").removeprefix("http-")
    return normalizado in CABECALHOS_SENSIVEIS


def limitar_tamanho(texto: str) -> str:
    """Corta o meio de um texto grande demais, nas fronteiras de espaço.

    Cortar no meio de uma palavra deixaria um e-mail pela metade
    (`ana@exem`), que já não parece e-mail e sairia no log. O resultado nunca
    passa do teto, então cortar de novo não muda nada.
    """
    if len(texto) <= TETO_DE_TEXTO:
        return texto
    comeco = texto[:_PARTE_DO_COMECO]
    fim = texto[len(texto) - (TETO_DE_TEXTO - _PARTE_DO_COMECO - len(_MARCA_DE_CORTE)) :]
    ultimo_espaco = max(comeco.rfind(espaco) for espaco in _ESPACOS)
    comeco = comeco[: ultimo_espaco + 1] if ultimo_espaco >= 0 else ""
    primeiro_espaco = min(
        (posicao for espaco in _ESPACOS if (posicao := fim.find(espaco)) >= 0),
        default=-1,
    )
    fim = fim[primeiro_espaco:] if primeiro_espaco >= 0 else ""
    return comeco + _MARCA_DE_CORTE + fim


def _sem_query(texto: str) -> str:
    """Corta query e fragmento de cada URL: eles carregam token e e-mail.

    Vale para URL absoluta (`https://h/a?t=1`) e para caminho
    (`GET /c/link?token=abc`). Host, porta e caminho ficam.
    """
    saida: list[str] = []
    posicao = 0
    indice = 0
    tamanho = len(texto)
    while indice < tamanho:
        if texto[indice] in _FIM_DE_URL:
            indice += 1
            continue
        fim = indice + 1
        while fim < tamanho and texto[fim] not in _FIM_DE_URL:
            fim += 1
        corte = _corte_da_url(texto, indice, fim)
        if corte is not None:
            saida.append(texto[posicao : corte + 1])
            # A segunda passada do redator vê `?<REDACTED>` e não repete.
            if not (corte + 1 == fim and texto.startswith(REDIGIDO, fim)):
                saida.append(REDIGIDO)
            posicao = fim
        indice = fim
    saida.append(texto[posicao:])
    return "".join(saida)


def _corte_da_url(texto: str, inicio: int, fim: int) -> int | None:
    trecho = texto[inicio:fim]
    esquema = trecho.find("://")
    base = esquema + 3 if esquema >= 0 else 0
    cortes = [p for marca in _INICIO_DA_QUERY if (p := trecho.find(marca, base)) >= 0]
    if not cortes:
        return None
    corte = min(cortes)
    if esquema < 0 and "/" not in trecho[:corte]:
        return None
    return inicio + corte


def _parte_de_nome(caractere: str) -> bool:
    return caractere.isalnum() or caractere == "-"


def _sem_cabecalho_sensivel(texto: str) -> str:
    saida: list[str] = []
    posicao = 0
    indice = 0
    tamanho = len(texto)
    while indice < tamanho:
        if not _parte_de_nome(texto[indice]):
            indice += 1
            continue
        fim_do_nome = indice + 1
        while fim_do_nome < tamanho and _parte_de_nome(texto[fim_do_nome]):
            fim_do_nome += 1
        valor = (
            _valor_do_cabecalho(texto, fim_do_nome)
            if texto[indice:fim_do_nome].lower() in CABECALHOS_SENSIVEIS
            else None
        )
        if valor is None:
            indice = fim_do_nome
            continue
        inicio_do_valor, fim_do_valor = valor
        saida.append(texto[posicao:inicio_do_valor])
        saida.append(REDIGIDO)
        posicao = indice = fim_do_valor
    saida.append(texto[posicao:])
    return "".join(saida)


def _valor_do_cabecalho(texto: str, fim_do_nome: int) -> tuple[int, int] | None:
    """(início, fim) do valor que segue o nome de um cabeçalho sensível.

    Formatos: `Nome: valor`, `nome=valor`, `"nome": "valor"`, repr de dict,
    `'nome' => 'valor'` (Map do Node), `(b'nome', b'valor')` (ASGI),
    `'nome': [ 'a', 'b' ]` (lista do Node) e valor continuado na linha
    seguinte.
    """
    posicao = fim_do_nome
    nome_entre_aspas = posicao < len(texto) and texto[posicao] in _ASPAS
    if nome_entre_aspas:
        posicao += 1
    posicao = _depois_de_espacos(texto, posicao)
    so_com_aspas = False
    if texto.startswith("=>", posicao):
        posicao += 2
    elif texto.startswith((":", "="), posicao):
        posicao += 1
    elif nome_entre_aspas and texto.startswith(",", posicao):
        # `(b'authorization', b'...')`. Sem aspas, `authorization, cookie e
        # x-api-key faltaram` é prosa, não par.
        posicao += 1
        so_com_aspas = True
    else:
        return None
    posicao = _depois_de_espacos(texto, posicao)
    if texto.startswith("[", posicao):
        fim = texto.find("]", posicao)
        return _intervalo(posicao + 1, fim if fim >= 0 else len(texto))
    if texto.startswith(("b'", 'b"'), posicao):
        posicao += 1
    if posicao < len(texto) and texto[posicao] in _ASPAS:
        return _intervalo(posicao + 1, _fim_entre_aspas(texto, posicao + 1, texto[posicao]))
    if so_com_aspas:
        return None
    return _intervalo(posicao, _fim_sem_aspas(texto, posicao))


def _depois_de_espacos(texto: str, posicao: int) -> int:
    while posicao < len(texto) and texto[posicao] in " \t":
        posicao += 1
    return posicao


def _intervalo(inicio: int, fim: int) -> tuple[int, int] | None:
    return (inicio, fim) if fim > inicio else None


def _fim_entre_aspas(texto: str, posicao: int, aspa: str) -> int:
    while posicao < len(texto) and texto[posicao] != aspa and texto[posicao] not in "\r\n":
        posicao += 2 if texto[posicao] == "\\" else 1
    return min(posicao, len(texto))


def _fim_sem_aspas(texto: str, posicao: int) -> int:
    while posicao < len(texto):
        caractere = texto[posicao]
        if caractere in _ASPAS:
            return posicao
        if caractere in "\r\n":
            continuacao = _continuacao(texto, posicao)
            if continuacao is None:
                return posicao
            posicao = continuacao
            continue
        posicao += 1
    return posicao


def _continuacao(texto: str, quebra: int) -> int | None:
    """Início da linha seguinte quando ela continua o valor (espaço ou tab)."""
    seguinte = quebra + (2 if texto.startswith("\r\n", quebra) else 1)
    if seguinte >= len(texto) or texto[seguinte] not in " \t":
        return None
    conteudo = texto[_depois_de_espacos(texto, seguinte) :]
    if conteudo.startswith(_INICIO_DE_LINHA_DE_PILHA):
        return None
    return seguinte


def _parte_de_email(caractere: str) -> bool:
    return caractere.isalnum() or caractere in _PONTUACAO_DE_EMAIL


def _digito_ascii(caractere: str) -> bool:
    # `isdigit()` aceita `²` e `①`, e `int("²")` levanta: "50 m²" derrubava o
    # log. Só o dígito ASCII compõe documento, como em `lib/redacao.ts`.
    return "0" <= caractere <= "9"


def _digito_ou_pontuacao(caractere: str) -> bool:
    return _digito_ascii(caractere) or caractere in _PONTUACAO_DE_DOCUMENTO


def _por_trecho(texto: str, pertence: Callable[[str], bool], redigir: Callable[[str], str]) -> str:
    """Aplica `redigir` a cada trecho máximo de caracteres que `pertence`."""
    saida: list[str] = []
    posicao = 0
    indice = 0
    tamanho = len(texto)
    while indice < tamanho:
        if not pertence(texto[indice]):
            indice += 1
            continue
        fim = indice + 1
        while fim < tamanho and pertence(texto[fim]):
            fim += 1
        trecho = texto[indice:fim]
        redigido = redigir(trecho)
        if redigido != trecho:
            saida.append(texto[posicao:indice])
            saida.append(redigido)
            posicao = fim
        indice = fim
    saida.append(texto[posicao:])
    return "".join(saida)


def _redigir_email(trecho: str) -> str:
    local, arroba, dominio = trecho.replace(_ARROBA_CODIFICADA, "@").rpartition("@")
    if not arroba or not local or "." not in dominio.strip("."):
        return trecho
    return REDIGIDO


def _redigir_documento(trecho: str) -> str:
    digitos = [ord(caractere) - ord("0") for caractere in trecho if _digito_ascii(caractere)]
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
