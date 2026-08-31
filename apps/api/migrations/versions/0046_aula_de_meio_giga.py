"""O teto do vídeo do catálogo sobe de 100 MiB para 512 MiB.

Uma aula de 45 min em 1080p passa de 800 MB; uma de 30 min fica perto de 500.
Com 100 MiB o vendedor não conseguia publicar aula nenhuma de duração real.

## Por que só agora

Subir este número antes do envio em partes seria promessa no papel: o envio era
um `PUT` único, com URL assinada de 300 s e o cliente desistindo aos 290 s. O
arquivo grande não terminava, e o vendedor descobria isso no minuto cinco em vez
de na hora de escolher o arquivo. Com o envio fatiado, nenhuma requisição
precisa durar mais que alguns segundos.

## As medições que sustentam o número (Decisão 011)

Medido em 31/08/2026 sobre um MP4 real de 408 MiB e 36 min:

- trocar o invólucro com `+faststart`: **0,6 s** (729 MiB/s). Para 512 MiB dá
  menos de um segundo. Não é o gargalo, e nunca foi.
- calcular o SHA-256 do arquivo inteiro: **0,16 s** (2.552 MiB/s).
- conferir a estrutura do container: tempo não mensurável.

O lease do worker é de 600 s e não pode subir — o banco recusa. O que ele
precisa cobrir são as transferências, não a conversão.

Disco, que é o que costuma vetar: o worker segura a origem e o derivado ao mesmo
tempo, 1 GiB para um arquivo de 512 MiB, e o `/tmp` dele tem 2 GiB. O serviço
isolado segura os mesmos dois, e o `/tmp` dele também tem 2 GiB. Os dois já
carregavam pior caso maior por causa do PDF de 300 páginas (1.556 MiB no
worker), então o vídeo entra por baixo do que já estava dimensionado.

## Onde o número morava, e o que exatamente muda

Cinco lugares neste banco, e nenhum derivado do outro: o catálogo, a validação
do `confirm`, as duas funções `v1_legacy` (que continuam sendo chamadas pelas
atuais) e a restrição da fila de limpeza. Alargar parte deles não deixaria o
limite valendo pela metade — mudaria a porta em que o vendedor bate.

**Só um desses cinco é o teto por tipo.** É o catálogo, e nele muda apenas a
linha do vídeo: o PDF continua em 100 MiB, e é essa linha que o
`asset_blob_media_contract_check` consulta para aceitar ou recusar o material
gravado.

Os outros quatro nunca foram teto por tipo: são a mesma faixa genérica, "cabe no
maior arquivo que a plataforma aceita", escrita à mão em cada função. Esse maior
arquivo passa de 100 para 512 MiB porque o vídeo passou, e por isso os quatro
sobem junto — inclusive para PDF e documento. Não é afrouxamento do teto do PDF:
o PDF continua sendo recusado em 100 MiB na criação da intenção e na restrição
do `asset_blob`. O que muda para ele é só onde uma violação impossível seria
pega, se chegasse até lá.

`test_limites_de_midia.py` amarra essa faixa genérica ao maior tipo do catálogo,
para que os dois não voltem a divergir em silêncio.

Revision ID: 0046_aula_de_meio_giga
Revises: 0045_envio_em_partes
"""

from __future__ import annotations

from alembic import op

revision = "0046_aula_de_meio_giga"
down_revision = "0045_envio_em_partes"
branch_labels = None
depends_on = None

_ANTIGO = 104857600
_NOVO = 536870912

#: Quanto tempo a intenção de um envio fatiado vale.
#:
#: Quinze minutos servem para um `PUT` único de arquivo pequeno. Para 512 MiB
#: não: numa linha de 5 Mbit/s o envio leva catorze minutos, e numa de 2 Mbit/s
#: leva trinta e seis. A intenção venceria no meio e o vendedor perderia o
#: trabalho no fim — o mesmo desfecho que o envio em partes existe para acabar.
#:
#: Duas horas cobrem até uns 0,6 Mbit/s. O custo de segurar a intenção aberta é
#: a cota de envios abertos do vendedor, que ele libera cancelando.
_VALIDADE_FATIADA = "2 hours"

#: A linha do catálogo que muda. Só a do vídeo: o PDF carrega o mesmo número e
#: continua onde está.
_SPEC_DE = f"('video/mp4', 'mp4', {_ANTIGO}::bigint)"
_SPEC_PARA = f"('video/mp4', 'mp4', {_NOVO}::bigint)"

#: As funções que escrevem o maior asset aceito à mão, em vez de perguntar ao
#: catálogo. Todas dizem a mesma coisa: "cabe no que a plataforma aceita".
_FUNCOES_COM_O_TETO: tuple[str, ...] = (
    "app_private.confirm_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,"
    "bigint,text,text,uuid,text,uuid)",
    "app_private.finalize_asset_verification_v1_legacy(uuid,uuid,uuid,text,bigint,text,"
    "bigint,text,text,text,text,text,text,text,uuid,text,uuid)",
    "app_private.register_asset_ready_candidate_v1_legacy(uuid,uuid,uuid,text,text,text,"
    "bigint,text,text,text,text)",
)

_REESCREVE = """
do $migration$
declare
  definicao text;
  reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  if reescrita = definicao then
    raise exception 'ALVO não continha o texto esperado' using errcode='55000';
  end if;
  execute reescrita;
end $migration$;
"""

_READY_ATE_0045 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes'))"
)
_READY_ATE_0046 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga'))"
)

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)


def _reescreve(alvo: str, de: str, para: str) -> None:
    """Troca um texto dentro de uma função, e falha alto se ele não estiver lá.

    Uma substituição silenciosa que não substitui nada é exatamente como um
    limite sobrevive a quem pensou tê-lo mudado.
    """
    op.execute(_REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para))


#: A validade da intenção, antes e depois. Quem decide é o banco, pelo tamanho
#: declarado, e não um sinalizador de quem chama — o mesmo critério que decide se
#: a conferência de conteúdo é adiada.
_CRIA_INTENCAO = (
    "app_private.create_asset_upload_intent(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,"
    "text,text,text,bigint,text,text,text,text,uuid,text,uuid)"
)

_VALIDADE_DE = "clock_timestamp()+interval '15 minutes')"
_VALIDADE_PARA = (
    "clock_timestamp()+"
    "(case when p_size > app_private.asset_multipart_part_bytes()"
    f" then interval '{_VALIDADE_FATIADA}' else interval '15 minutes' end))"
)


def _troca_o_teto(de: int, para: int) -> None:
    _reescreve(
        "app_private.asset_media_spec(text)",
        _SPEC_DE if de == _ANTIGO else _SPEC_PARA,
        _SPEC_PARA if para == _NOVO else _SPEC_DE,
    )
    for funcao in _FUNCOES_COM_O_TETO:
        _reescreve(funcao, str(de), str(para))
    op.execute(
        f"""
        alter table app_private.asset_storage_cleanup_job
          drop constraint asset_storage_cleanup_job_byte_size_check,
          add constraint asset_storage_cleanup_job_byte_size_check
            check (byte_size is null or (byte_size >= 1 and byte_size <= {para}))
        """
    )


def _estende_o_ready(de: str, para: str) -> None:
    for alvo in _BASES_DO_READY:
        op.execute(_REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para))


def upgrade() -> None:
    _troca_o_teto(_ANTIGO, _NOVO)
    _reescreve(
        _CRIA_INTENCAO,
        _VALIDADE_DE,
        _VALIDADE_PARA,
    )
    _estende_o_ready(_READY_ATE_0045, _READY_ATE_0046)


def downgrade() -> None:
    # Só desce se nada tiver passado do teto antigo. Voltar com uma aula de 300
    # MiB gravada deixaria a tabela inconsistente com a própria regra, e o
    # material do vendedor irrecuperável pela porta normal.
    op.execute(
        f"""
        do $$
        declare quantos bigint;
        begin
          select count(*) into quantos from app_private.asset_upload
            where expected_byte_size > {_ANTIGO};
          if quantos > 0 then
            raise exception
              'não dá para voltar ao teto antigo: % envios acima dele', quantos
              using errcode='55000';
          end if;
          -- A fila de limpeza tem restrição própria, e ela também é apertada de
          -- volta logo abaixo. Sem esta guarda, um derivado maior que a origem
          -- faria o `ADD CONSTRAINT` estourar com erro cru de restrição em vez
          -- da mensagem que explica o que aconteceu.
          select count(*) into quantos from app_private.asset_storage_cleanup_job
            where byte_size > {_ANTIGO};
          if quantos > 0 then
            raise exception
              'não dá para voltar: % limpezas pendentes acima do teto antigo', quantos
              using errcode='55000';
          end if;
        end $$;
        """  # noqa: S608 - o número é constante deste módulo
    )
    _troca_o_teto(_NOVO, _ANTIGO)
    _reescreve(
        _CRIA_INTENCAO,
        _VALIDADE_PARA,
        _VALIDADE_DE,
    )
    _estende_o_ready(_READY_ATE_0046, _READY_ATE_0045)
