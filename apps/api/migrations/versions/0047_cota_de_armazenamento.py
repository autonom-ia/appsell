"""A cota de armazenamento do vendedor deixa de caber uma hora de aula.

Ela estava escrita como "1 GiB de material por vendedor". Vídeo de aula em 1080p
ocupa perto de 1 GB por hora, então em linguagem de produto a regra era: **cada
vendedor guarda cerca de uma hora de aula, no total, para sempre**.

Isso fazia sentido enquanto o teto por arquivo era 100 MiB, porque o curso era
feito de clipes curtos e 1 GiB dava umas dez peças. Depois da 0046 o vendedor
consegue subir a aula de 30 minutos que a plataforma promete — e consegue subir
**duas**, porque a parede continuou onde estava. Um curso de dez aulas de meia
hora ocupa perto de 5 GB.

Passa para **20 GiB**, cerca de dezoito horas de curso. Decisão do Rodrigo em
01/09/2026, com o custo aceito de forma explícita: se o disco acabar, compra-se
mais.

## A cota vira função, e não número repetido

O número morava em três funções, escrito à mão em cada uma. É o mesmo defeito
que a revisão da 0046 apontou: mudar um limite assim é caçar texto. Agora existe
`app_private.seller_storage_quota_bytes()`, e as três perguntam a ela.

## E a segunda cota, a que contava a coisa errada

Existe outra cota, de envios simultâneos, que conta **quantidade**: cem por
vendedor, dez por pessoa. Quantidade era uma boa medida quando todo arquivo
cabia em 100 MiB. Com o teto em 512 MiB, os mesmos cem envios passaram a poder
estacionar cinco vezes mais bytes em quarentena, sem ninguém ter decidido isso.

As contagens ficam — elas seguem protegendo contra muitos arquivos pequenos — e
passa a existir também um teto de **bytes** em voo. São 4 GiB, o equivalente a
oito vídeos no tamanho máximo ao mesmo tempo, muito acima do que uma pessoa
envia de fato: a tela manda um arquivo por vez.

Revision ID: 0047_cota_de_armazenamento
Revises: 0046_aula_de_meio_giga
"""

from __future__ import annotations

from alembic import op

revision = "0047_cota_de_armazenamento"
down_revision = "0046_aula_de_meio_giga"
branch_labels = None
depends_on = None

_GIB = 1024 * 1024 * 1024

#: Quanto material publicado um vendedor guarda. Dezoito horas de curso.
_COTA_ANTIGA = 1 * _GIB
_COTA_NOVA = 20 * _GIB

#: Quantos bytes um vendedor pode ter em voo, somando os envios abertos.
#:
#: Oito vídeos no tamanho máximo ao mesmo tempo. A tela manda um por vez, então
#: isto não aperta ninguém: existe para que o abandono em massa não vire disco
#: ocupado sem aparecer em listagem de objeto nenhuma.
_COTA_EM_VOO = 4 * _GIB

_FUNCOES_COM_A_COTA: tuple[str, ...] = (
    "app_private.create_asset_upload_intent(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,"
    "text,text,text,bigint,text,text,text,text,uuid,text,uuid)",
    "app_private.finalize_asset_verification_v1_legacy(uuid,uuid,uuid,text,bigint,text,"
    "bigint,text,text,text,text,text,text,text,uuid,text,uuid)",
    "app_private.finalize_asset_verification_v2(uuid,uuid,uuid,text,bigint,text,"
    "bigint,text,text,text,text,text,text,text,uuid,text,uuid,text)",
)

_CRIA_INTENCAO = _FUNCOES_COM_A_COTA[0]

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

#: O bloco de bytes em voo, encaixado depois da contagem por pessoa.
_ANCORA_EM_VOO = """            raise exception 'open asset upload limit exceeded' using errcode='P4290';
          end if;"""

_COM_BYTES_EM_VOO = """            raise exception 'open asset upload limit exceeded' using errcode='P4290';
          end if;
          if coalesce((select sum(upload.expected_byte_size) from app_private.asset_upload upload
              where upload.seller_organization_id=p_seller and exists(
                select 1 from app_private.asset_storage_cleanup_job reconcile
                where reconcile.seller_organization_id=upload.seller_organization_id
                  and reconcile.upload_id=upload.id
                  and reconcile.kind='quarantine_reconcile'
                  and reconcile.status<>'completed'
              )),0)+p_size > app_private.seller_open_upload_quota_bytes() then
            raise exception 'seller open asset upload byte quota exceeded' using errcode='P4290';
          end if;"""

_READY_ATE_0046 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga'))"
)
_READY_ATE_0047 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento'))"
)

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)


def _reescreve(alvo: str, de: str, para: str) -> None:
    op.execute(_REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para))


def _estende_o_ready(de: str, para: str) -> None:
    for alvo in _BASES_DO_READY:
        op.execute(_REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para))


def _funcao(nome: str, valor: int, razao: str) -> str:
    """Uma cota como função, com o bloco de permissões que toda função aqui tem."""
    return f"""
create or replace function app_private.{nome}()
returns bigint language sql immutable
set search_path=pg_catalog as $$ select {valor}::bigint $$;
comment on function app_private.{nome}() is '{razao}';
alter function app_private.{nome}() owner to appsell_owner;
revoke all on function app_private.{nome}() from public;
grant execute on function app_private.{nome}() to appsell_runtime;
"""  # noqa: S608 - nome e valor são constantes deste módulo


def upgrade() -> None:
    op.execute(
        _funcao(
            "seller_storage_quota_bytes",
            _COTA_NOVA,
            "Material publicado por vendedor. 20 GiB, cerca de 18 horas de curso.",
        )
    )
    op.execute(
        _funcao(
            "seller_open_upload_quota_bytes",
            _COTA_EM_VOO,
            "Bytes em voo por vendedor, somando os envios abertos.",
        )
    )
    for funcao in _FUNCOES_COM_A_COTA:
        _reescreve(funcao, str(_COTA_ANTIGA), "app_private.seller_storage_quota_bytes()")
    _reescreve(_CRIA_INTENCAO, _ANCORA_EM_VOO, _COM_BYTES_EM_VOO)
    _estende_o_ready(_READY_ATE_0046, _READY_ATE_0047)


def downgrade() -> None:
    # Descer só é seguro se ninguém tiver passado da cota antiga. Voltar com um
    # vendedor guardando 5 GB deixaria a tabela inconsistente com a própria
    # regra, e o material dele irrecuperável pela porta normal.
    #
    # A conta soma as duas tabelas, e não só `asset_blob`, porque é assim que
    # `finalize_asset_verification_v2` mede: material mais páginas rasterizadas.
    # Uma guarda que soma menos do que a regra que ela protege deixa passar
    # exatamente o vendedor que quebraria no próximo PDF.
    op.execute(
        f"""
        do $$
        declare maior bigint;
        begin
          select coalesce(max(coalesce(blob.total,0)+coalesce(pagina.total,0)),0)
            into maior from
              (select seller_organization_id as vendedor, sum(byte_size) as total
                 from public.asset_blob where storage_driver='s3'
                group by seller_organization_id) blob
              full join
              (select seller_organization_id as vendedor, sum(byte_size) as total
                 from public.asset_pdf_page where storage_driver='s3'
                group by seller_organization_id) pagina
              on blob.vendedor = pagina.vendedor;
          if maior > {_COTA_ANTIGA} then
            raise exception
              'não dá para voltar: um vendedor guarda % bytes, acima da cota antiga', maior
              using errcode='55000';
          end if;
        end $$;
        """  # noqa: S608 - o número é constante deste módulo
    )
    _reescreve(_CRIA_INTENCAO, _COM_BYTES_EM_VOO, _ANCORA_EM_VOO)
    for funcao in _FUNCOES_COM_A_COTA:
        _reescreve(funcao, "app_private.seller_storage_quota_bytes()", str(_COTA_ANTIGA))
    op.execute("drop function if exists app_private.seller_open_upload_quota_bytes()")
    op.execute("drop function if exists app_private.seller_storage_quota_bytes()")
    _estende_o_ready(_READY_ATE_0047, _READY_ATE_0046)
