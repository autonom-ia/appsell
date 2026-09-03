"""A capa do produto aparece na biblioteca do aluno (#387).

A área do aluno mostrava só texto: nome da loja, título, descrição e
progresso. O produto tem capa desde a 0037, e ela já aparece no cartão da
loja, na lista do painel e na página de vendas — faltava aqui, que é onde o
comprador volta toda vez que retoma o curso.

A capa vem da tabela, não do conteúdo publicado: o snapshot guarda o que foi
publicado do produto, e a imagem é atualizável sem republicar. O bloco é
condicional, como na vitrine: produto sem capa não ganha a chave, e o cartão
desenha a arte gerada a partir do nome.

Ordem: o web precisa subir ANTES desta migration. O parser do navegador
confere as chaves exatas do item, e uma chave nova derruba a biblioteca de
quem ainda estiver com a versão antiga.

Revision ID: 0058_capa_na_biblioteca
Revises: 0057_banner_na_vitrine
"""

from __future__ import annotations

from alembic import op

revision = "0058_capa_na_biblioteca"
down_revision = "0057_banner_na_vitrine"
branch_labels = None
depends_on = None


# As duas definições ficam congeladas aqui, e não recortadas da 0007 em
# tempo de execução: a migration precisa produzir sempre o mesmo SQL, hoje
# e num banco criado do zero daqui a um ano, mesmo que alguém edite a 0007.
# O downgrade devolve exatamente o texto que existia antes deste upgrade.

_COM_CAPA = r"""
        create or replace function app_private.read_buyer_library(p_identity_user_id uuid)
        returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare result jsonb;
        begin
          if not app_private.library_context_allowed(p_identity_user_id,'library.read') then
            raise exception 'library context is not authorized' using errcode='28000';
          end if;
          select coalesce(jsonb_agg(jsonb_build_object(
            'entitlement_id',entitlement.id,
            'seller',jsonb_build_object('id',organization.id,'slug',organization.slug,'name',organization.display_name),
            'product',(publication.snapshot->'product') || jsonb_build_object(
              'publication_id',publication.id,'publication_version',publication.version
            ) || case when product.cover_object_key is null then '{}'::jsonb
                 else jsonb_build_object('cover',jsonb_build_object(
                   'object_key',product.cover_object_key,
                   'storage_version_id',product.cover_storage_version_id,
                   'media_type',product.cover_media_type,
                   'width',product.cover_width,'height',product.cover_height)) end,
            'progress_percent',coalesce(summary.progress_percent,0),
            'resume_lesson_id',coalesce(
              latest_partial.lesson_id,first_incomplete.lesson_id,first_lesson.lesson_id
            )
          ) order by entitlement.created_at,entitlement.id),'[]'::jsonb) into result
          from buyer_entitlement entitlement
          join seller_organization organization on organization.id=entitlement.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          join publication
            on publication.seller_organization_id=entitlement.seller_organization_id
           and publication.catalog_product_id=entitlement.catalog_product_id
           and publication.id=entitlement.publication_id
          left join lateral (
            select case when sum(state.total_lessons)=0 then 0
              else floor(sum(state.completed_lessons)*100.0/sum(state.total_lessons))::smallint end progress_percent
            from course_learning_state state
            where state.seller_organization_id=entitlement.seller_organization_id
              and state.entitlement_id=entitlement.id
          ) summary on true
          left join lateral (
            select progress.lesson_id from lesson_progress progress
            where progress.seller_organization_id=entitlement.seller_organization_id
              and progress.entitlement_id=entitlement.id
              and progress.progress_percent between 1 and 99
            order by progress.updated_at desc,progress.lesson_id limit 1
          ) latest_partial on true
          left join lateral (
            select projected.lesson_id from publication_lesson projected
            where projected.seller_organization_id=entitlement.seller_organization_id
              and projected.publication_id=entitlement.publication_id
              and not exists(
                select 1 from lesson_progress progress
                where progress.seller_organization_id=entitlement.seller_organization_id
                  and progress.entitlement_id=entitlement.id
                  and progress.lesson_id=projected.lesson_id
                  and progress.progress_percent=100
              )
            order by projected.course_position,projected.module_position,
              projected.lesson_position,projected.lesson_id limit 1
          ) first_incomplete on true
          left join lateral (
            select projected.lesson_id from publication_lesson projected
            where projected.seller_organization_id=entitlement.seller_organization_id
              and projected.publication_id=entitlement.publication_id
            order by projected.course_position,projected.module_position,
              projected.lesson_position,projected.lesson_id limit 1
          ) first_lesson on true
          where entitlement.buyer_identity_user_id=p_identity_user_id
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          return result;
        end $$
"""

_SEM_CAPA = r"""
        create or replace function app_private.read_buyer_library(p_identity_user_id uuid)
        returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare result jsonb;
        begin
          if not app_private.library_context_allowed(p_identity_user_id,'library.read') then
            raise exception 'library context is not authorized' using errcode='28000';
          end if;
          select coalesce(jsonb_agg(jsonb_build_object(
            'entitlement_id',entitlement.id,
            'seller',jsonb_build_object('id',organization.id,'slug',organization.slug,'name',organization.display_name),
            'product',(publication.snapshot->'product') || jsonb_build_object(
              'publication_id',publication.id,'publication_version',publication.version
            ),
            'progress_percent',coalesce(summary.progress_percent,0),
            'resume_lesson_id',coalesce(
              latest_partial.lesson_id,first_incomplete.lesson_id,first_lesson.lesson_id
            )
          ) order by entitlement.created_at,entitlement.id),'[]'::jsonb) into result
          from buyer_entitlement entitlement
          join seller_organization organization on organization.id=entitlement.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          join publication
            on publication.seller_organization_id=entitlement.seller_organization_id
           and publication.catalog_product_id=entitlement.catalog_product_id
           and publication.id=entitlement.publication_id
          left join lateral (
            select case when sum(state.total_lessons)=0 then 0
              else floor(sum(state.completed_lessons)*100.0/sum(state.total_lessons))::smallint end progress_percent
            from course_learning_state state
            where state.seller_organization_id=entitlement.seller_organization_id
              and state.entitlement_id=entitlement.id
          ) summary on true
          left join lateral (
            select progress.lesson_id from lesson_progress progress
            where progress.seller_organization_id=entitlement.seller_organization_id
              and progress.entitlement_id=entitlement.id
              and progress.progress_percent between 1 and 99
            order by progress.updated_at desc,progress.lesson_id limit 1
          ) latest_partial on true
          left join lateral (
            select projected.lesson_id from publication_lesson projected
            where projected.seller_organization_id=entitlement.seller_organization_id
              and projected.publication_id=entitlement.publication_id
              and not exists(
                select 1 from lesson_progress progress
                where progress.seller_organization_id=entitlement.seller_organization_id
                  and progress.entitlement_id=entitlement.id
                  and progress.lesson_id=projected.lesson_id
                  and progress.progress_percent=100
              )
            order by projected.course_position,projected.module_position,
              projected.lesson_position,projected.lesson_id limit 1
          ) first_incomplete on true
          left join lateral (
            select projected.lesson_id from publication_lesson projected
            where projected.seller_organization_id=entitlement.seller_organization_id
              and projected.publication_id=entitlement.publication_id
            order by projected.course_position,projected.module_position,
              projected.lesson_position,projected.lesson_id limit 1
          ) first_lesson on true
          where entitlement.buyer_identity_user_id=p_identity_user_id
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          return result;
        end $$
"""


def _biblioteca(com_capa: bool) -> str:
    """A função da biblioteca, com ou sem a capa do produto."""
    return _COM_CAPA if com_capa else _SEM_CAPA


_READY = "app_private.schema_ready()"
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_READY_ATE_0057 = "'0056_banner_do_produto','0057_banner_na_vitrine'))"
_READY_ATE_0058 = "'0056_banner_do_produto','0057_banner_na_vitrine','0058_capa_na_biblioteca'))"

_REESCREVE = """
do $migration$
declare
  definicao text;
  reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao) - length(replace(definicao, $de$DE$de$, ''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO não continha a âncora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  execute reescrita;
end $migration$
"""


def upgrade() -> None:
    op.execute(_biblioteca(com_capa=True))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0057)
            .replace("PARA", _READY_ATE_0058)
        )


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0058)
            .replace("PARA", _READY_ATE_0057)
        )
    op.execute(_biblioteca(com_capa=False))
