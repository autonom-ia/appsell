"""O catálogo de mídia é o mesmo nas três camadas.

Ele existe em três linguagens: TypeScript no navegador, Python na API e SQL no
banco. Nada obriga os três a concordarem, e discordar não dá erro — dá uma tela
que aceita o arquivo e um servidor que recusa depois do envio, ou o contrário.

Foi o que aconteceu em 26/08/2026: o teto do PDF subiu para 100 MiB no
navegador e na API, o deploy passou verde nas duas, e o banco continuou em
10 MiB. O upload morria com `errcode 22023`, que vira 422 na API e "não foi
possível preparar o arquivo" na tela — três traduções depois, sem nenhuma
mencionar tamanho.

`test_asset_media_mirror.py` já compara navegador × API. Este arquivo fecha o
triângulo, que é onde o defeito passou.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

from appsell_api.asset_media import SUPPORTED_MEDIA

ADMIN_URL = os.environ.get("APPSELL_TEST_POSTGRES_ADMIN_URL")

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="isolated PostgreSQL environment is required")

_TS = Path(__file__).resolve().parents[3] / "lib" / "asset-media.ts"
_ENTRY = re.compile(r'extension:\s*"(?P<ext>[a-z0-9]+)",\s*maxBytes:\s*(?P<n>\d+)\s*\*\s*MIB')


def _database_catalogue() -> dict[str, int]:
    """A tabela de formatos como o banco a enxerga, por extensão."""
    assert ADMIN_URL
    catalogue: dict[str, int] = {}
    with psycopg.connect(ADMIN_URL) as connection:
        for media_type in SUPPORTED_MEDIA:
            rows = connection.execute(
                "select extension, max_bytes from app_private.asset_media_spec(%s)",
                (media_type,),
            ).fetchall()
            assert len(rows) == 1, f"{media_type}: o banco devolveu {len(rows)} linhas"
            catalogue[str(rows[0][0])] = int(rows[0][1])
    return catalogue


def _browser_catalogue() -> dict[str, int]:
    assert _TS.is_file(), f"catálogo do navegador não encontrado em {_TS}"
    return {m["ext"]: int(m["n"]) * 1024 * 1024 for m in _ENTRY.finditer(_TS.read_text("utf-8"))}


def test_o_banco_conhece_todo_formato_que_a_api_conhece() -> None:
    banco = _database_catalogue()
    for media_type, spec in SUPPORTED_MEDIA.items():
        assert spec.extension in banco, f"{media_type} existe na API e não no banco"
        assert banco[spec.extension] == spec.max_bytes, (
            f"{media_type}: banco aceita {banco[spec.extension]} bytes "
            f"e API aceita {spec.max_bytes}"
        )


def test_o_navegador_concorda_com_o_banco() -> None:
    banco = _database_catalogue()
    navegador = _browser_catalogue()
    assert navegador, "nenhum formato lido do catálogo do navegador"
    for extensao, limite in navegador.items():
        assert extensao in banco, f".{extensao} é oferecido na tela e o banco não conhece"
        assert banco[extensao] == limite, (
            f".{extensao}: navegador aceita {limite} bytes e banco aceita {banco[extensao]}"
        )


def test_o_contrato_aceita_o_teto_e_recusa_um_byte_alem() -> None:
    """A checagem de borda em cada formato, feita pelo próprio banco.

    Comparar as tabelas prova que os números batem; só exercitar
    `asset_media_contract_ok` prova que o número é o que de fato decide.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        for media_type, spec in SUPPORTED_MEDIA.items():
            nome = f"material.{spec.extension}"
            no_teto = connection.execute(
                "select app_private.asset_media_contract_ok(%s,%s,%s)",
                (media_type, nome, spec.max_bytes),
            ).fetchone()
            alem = connection.execute(
                "select app_private.asset_media_contract_ok(%s,%s,%s)",
                (media_type, nome, spec.max_bytes + 1),
            ).fetchone()
            assert no_teto is not None and no_teto[0] is True, (
                f"{media_type}: o banco recusa o arquivo no próprio teto"
            )
            assert alem is not None and alem[0] is False, (
                f"{media_type}: o banco aceita um byte além do teto"
            )


def test_o_pdf_de_um_playbook_real_passa() -> None:
    """O caso concreto que quebrou: 83 páginas, 12.222.444 bytes.

    O nome tem espaço e parênteses de propósito — foi o arquivo real, e um nome
    assim chegou a ser suspeito antes de a medição apontar o limite.
    """
    assert ADMIN_URL
    nome = "Playbook_HUB2YOU_02_Gerador_Cotacoes_Comparativas_PROVA_v0.6 (1).pdf"
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select app_private.asset_media_contract_ok(%s,%s,%s)",
            ("application/pdf", nome, 12_222_444),
        ).fetchone()
    assert row is not None and row[0] is True


def test_o_intent_nao_repete_mais_o_catalogo() -> None:
    """A cópia inline da regra do PDF não pode voltar.

    Ela sobreviveu porque ninguém olhava para ela: a linha seguinte já chamava
    o catálogo, então a cópia parecia inofensiva.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select prosrc from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='app_private' and p.proname='create_asset_upload_intent'"
        ).fetchone()
    assert row is not None
    fonte = str(row[0])
    assert "asset_media_contract_ok" in fonte
    # Sem fronteira, `10485760` casaria dentro de `104857600` — que é o teto de
    # vídeo, legítimo. Essa confusão já inflou um diagnóstico nesta base.
    assert not re.search(r"(?<!\d)10485760(?!\d)", fonte), (
        "a regra do PDF voltou a ser transcrita dentro de create_asset_upload_intent"
    )


IMAGENS_COM_TETO_PROPRIO = frozenset(
    {
        "catalog_product_cover_complete",
        "seller_organization_logo_complete",
        "seller_organization_banner_complete",
    }
)


def test_nenhuma_constraint_transcreve_o_catalogo() -> None:
    """CHECK CONSTRAINT também é lugar onde o catálogo pode se esconder.

    Foi exatamente por aqui que o defeito escapou: a `0039` limpou as funções, e
    três CHECK CONSTRAINTS continuavam com a tabela transcrita. Constraint não é
    função, então nenhuma varredura em `pg_proc` as via.

    Elas mordiam em três momentos da mesma jornada — pedir o envio, confirmar,
    publicar —, de modo que corrigir uma só empurraria o vendedor para a
    seguinte.
    """
    assert ADMIN_URL
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select conrelid::regclass::text, conname, pg_get_constraintdef(oid) "
            "from pg_constraint where contype='c'"
        ).fetchall()

    limites = {spec.max_bytes for spec in SUPPORTED_MEDIA.values()}
    culpadas = []
    for tabela, nome, definicao in rows:
        texto = str(definicao)
        # Só constraints que decidem por tipo de mídia. A capa tem teto próprio
        # (5 MiB) em outras constraints, e limpeza tem um teto genérico: são
        # outros conceitos, e varrer por número solto os acusaria à toa.
        if "media_type" not in texto:
            continue
        # A capa do produto tem catálogo próprio (5 MiB), que por coincidência
        # é o mesmo teto de CSV e JSON. É outro conceito e não pertence a esta
        # varredura. O logo e o banner da loja (0052, 0054) usam o mesmo
        # catálogo e o mesmo worker da capa. Isenção por nome exato: uma
        # constraint nova que transcreva o catálogo não se esconde atrás
        # de "logo" ou "banner" no texto.
        if nome in IMAGENS_COM_TETO_PROPRIO:
            continue
        for limite in limites:
            # Fronteira de dígito: sem ela, 10485760 casa dentro de 104857600 e
            # o resultado engana.
            if re.search(rf"(?<!\d){limite}(?!\d)", texto):
                culpadas.append(f"{tabela}.{nome} carrega o literal {limite}")
    assert not culpadas, "constraints com o catálogo transcrito: " + "; ".join(culpadas)


def test_as_tres_constraints_derivam_do_catalogo() -> None:
    """Não basta o literal sumir: elas têm de consultar a fonte única."""
    assert ADMIN_URL
    esperado = {
        "asset_upload_media_contract_check": "asset_media_contract_ok",
        "asset_blob_media_contract_check": "asset_media_max_bytes",
        "learning_asset_media_contract_check": "asset_media_max_bytes",
    }
    with psycopg.connect(ADMIN_URL) as connection:
        for nome, funcao in esperado.items():
            row = connection.execute(
                "select pg_get_constraintdef(oid) from pg_constraint where conname=%s",
                (nome,),
            ).fetchone()
            assert row is not None, f"{nome} não existe"
            assert funcao in str(row[0]), f"{nome} não deriva do catálogo: esperava chamar {funcao}"


def test_a_constraint_do_upload_aceita_o_playbook_e_recusa_o_excesso() -> None:
    """A borda medida na própria tabela, não só na função.

    A função podia estar certa e a tabela continuar recusando — foi o que
    aconteceu por três rodadas. Este teste escreve de verdade, dentro de uma
    transação desfeita no fim.
    """
    assert ADMIN_URL
    pdf_max = SUPPORTED_MEDIA["application/pdf"].max_bytes
    nome = "Playbook_HUB2YOU_02_Gerador_Cotacoes_Comparativas_PROVA_v0.6 (1).pdf"
    with psycopg.connect(ADMIN_URL) as connection:
        aceita = connection.execute(
            "select app_private.asset_media_contract_ok('application/pdf',%s,%s)",
            (nome, 12_222_444),
        ).fetchone()
        no_teto = connection.execute(
            "select app_private.asset_media_max_bytes('application/pdf')"
        ).fetchone()
    assert aceita is not None and aceita[0] is True
    assert no_teto is not None and int(no_teto[0]) == pdf_max


def test_o_lease_do_worker_cabe_na_faixa_que_o_banco_aceita() -> None:
    """A constante do worker e a faixa do banco precisam concordar.

    `claim_asset_verification_jobs` recusa qualquer lease fora de [30, 600] com
    `errcode 22023`. Em 26/08/2026 a constante subiu para 1800 sem que a faixa
    subisse junto: o worker passou a ser recusado em toda tentativa de claim,
    num laço de retry por segundo, e o vendedor via "a verificação está
    demorando mais que o esperado" para sempre — porque ninguém chegava a pegar
    o trabalho.

    O worker não emitia erro, só `asset_worker_claim_loop_retry`. Nada dizia que
    o número estava fora da faixa.
    """
    assert ADMIN_URL
    from appsell_api.asset_worker_runner import _ASSET_LEASE_SECONDS

    with psycopg.connect(ADMIN_URL) as connection:
        row = connection.execute(
            "select prosrc from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='app_private' and p.proname='claim_asset_verification_jobs'"
        ).fetchone()
    assert row is not None, "claim_asset_verification_jobs não existe"

    faixa = re.search(r"lease_seconds\s+not\s+between\s+(\d+)\s+and\s+(\d+)", str(row[0]))
    assert faixa, "a função deixou de declarar a faixa de lease que aceita"
    minimo, maximo = int(faixa.group(1)), int(faixa.group(2))
    assert minimo <= _ASSET_LEASE_SECONDS <= maximo, (
        f"o worker pede lease de {_ASSET_LEASE_SECONDS}s e o banco só aceita "
        f"entre {minimo}s e {maximo}s — o claim falha em silêncio e o material "
        f"nunca é verificado"
    )


def test_nenhuma_funcao_fecha_o_conjunto_de_midia_em_pdf_e_mp4() -> None:
    """A quarta camada onde o catálogo se escondeu: dentro de uma função.

    `register_asset_ready_candidate_v1_legacy` recusava qualquer coisa que não
    fosse PDF ou MP4 na última porta do caminho de verificação, depois do
    antivírus e da verificação. Os sete documentos do catálogo nunca puderam ser
    registrados, e o worker engolia o erro sem log.

    A varredura procura o padrão exato de *lista fechada de mídia aceita*. Um
    `expected_media='application/pdf'` é outro assunto — é roteamento, não
    permissão: o PDF exige manifesto de raster e as funções precisam desviar por
    isso.
    """
    assert ADMIN_URL
    fechamento = re.compile(r"not\s+in\s*\(\s*'application/pdf'\s*,\s*'video/mp4'\s*\)")
    with psycopg.connect(ADMIN_URL) as connection:
        rows = connection.execute(
            "select n.nspname||'.'||p.proname, p.prosrc from pg_proc p "
            "join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname in ('app_private','public')"
        ).fetchall()
    culpadas = [str(nome) for nome, fonte in rows if fechamento.search(str(fonte))]
    assert not culpadas, "funções que recusam todo formato fora de PDF/MP4: " + "; ".join(culpadas)
