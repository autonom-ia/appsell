"""Quem já comprou passa a receber as atualizações do produto.

O acesso do comprador guarda um `publication_id`, e a leitura do aluno usa
exatamente aquela publicação. Na prática a compra ficava **congelada na versão
que existia no momento em que aconteceu**: material acrescentado depois nunca
chegava a quem tinha pagado.

Medido em produção em 04/09/2026: o produto tinha publicação na versão 3, com o
material novo, e **nenhum comprador nela** — dois na versão 1 e dois na versão 2.

Hotmart e Kiwify não têm esse problema porque não congelam nada: a área de
membros deles é conteúdo vivo e o aluno sempre lê o curso de hoje. O que eles
controlam é *turma* e *liberação por data*, não versão de compra.

## O que muda

Ao publicar, os acessos ativos daquele produto avançam para a publicação nova.

**Só quando a publicação nova contém tudo o que a antiga tinha**, aula por aula e
material por material. Era isso que o congelamento protegia, e continua
protegido: se a versão nova tirar alguma coisa, o aluno fica onde está em vez de
perder o que comprou. Esse caso vira uma escolha dele, numa tela própria, e não
um efeito colateral de o vendedor publicar.

## Por que a árvore é refeita, e não movida

Matrícula, estado do curso e progresso carregam o `publication_id` dentro das
próprias chaves estrangeiras, e nenhuma delas é adiável. Mover o pai quebra o
filho, mover o filho quebra o pai: não existe ordem que passe.

Refazer não tem esse problema. O progresso é guardado por *aula*, a árvore
inteira é apagada, o acesso avança, e o gatilho
`buyer_entitlement_learning_after_write` (que já existia, disparado justamente
em `update of publication_id`) recria matrícula e estado na publicação nova. O
progresso volta pela aula, e aula que saiu simplesmente não tem para onde
voltar.

## A regra que sai do banco

`buyer_entitlement_demo_purchase_fkey` incluía o `publication_id`. Ela dizia, na
prática, *"o acesso tem que continuar apontando para a mesma publicação que está
no recibo"*. Era a causa raiz: o congelamento não era esquecimento de código,
estava escrito no schema.

A chave continua garantindo compra, comprador e produto. O recibo continua
guardando em `demo_purchase.publication_id` o que foi vendido. Ele só deixa de
exigir que o aluno fique preso ali. O que foi vendido e o que o aluno pode ver
hoje são duas coisas, e estavam na mesma.

Autorizado pelo Rodrigo em 04/09/2026.

Revision ID: 0060_atualizacao_chega_ao_aluno
Revises: 0059_canal_financial_global
"""

from __future__ import annotations

from alembic import op

revision = "0060_atualizacao_chega_ao_aluno"
down_revision = "0059_canal_financial_global"
branch_labels = None
depends_on = None

_READY_ATE_0059 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global'))"
)
_READY_ATE_0060 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
#: `schema_ready_base_0017()` chama a `0016`, então ensinar a `0016` basta para
#: o healthcheck do contêiner enxergar a chave nova.
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
_READY_CHAVE_DE = (
    "            and exists(select 1 from app_private.context_signing_key where singleton)\n"
)
_READY_CHAVE_PARA = (
    "            and to_regclass('public.demo_purchase_acesso_key') is not null\n"
) + _READY_CHAVE_DE

_PUBLICAR = "app_private.publish_product(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid)"

_REESCREVE = """
do $migration$
declare definicao text; reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao)-length(replace(definicao,$de$DE$de$,''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO nao continha a ancora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao,$de$DE$de$,$para$PARA$para$);
  execute reescrita;
end $migration$
"""

#: `avancando` guarda quais acessos é seguro levar para a publicação nova.
_DECLARE_DE = "generated_snapshot jsonb; generated_source_sha256 text; response jsonb;"
_DECLARE_PARA = (
    "generated_snapshot jsonb; generated_source_sha256 text; response jsonb;\n"
    "          avancando uuid[]; avancados integer:=0; progresso_salvo jsonb;"
)

#: O ponto de encaixe: logo depois de a oferta ser apontada para a publicação
#: nova, que já é onde o resto do mundo passa a olhar para ela.
_ANCORA = """            and configured.publication_id is distinct from next_publication_id;"""

_AVANCO = (
    _ANCORA  # noqa: S608 - texto literal deste módulo, sem valor vindo de fora
    + """
          -- Quem ja comprou avanca para a publicacao nova.
          --
          -- So avanca quem nao perde nada: a publicacao nova precisa conter
          -- tudo o que a anterior tinha, aula por aula e material por material.
          -- Era isso que o congelamento protegia, e continua protegido. Se a
          -- publicacao nova remover alguma coisa, ninguem avanca, e a decisao
          -- passa a ser do aluno numa tela propria.
          select coalesce(array_agg(anterior.id),'{}')
            into avancando
            from public.buyer_entitlement anterior
           where anterior.seller_organization_id=p_seller_organization_id
             and anterior.catalog_product_id=p_catalog_product_id
             and anterior.status='active'
             and anterior.publication_id is distinct from next_publication_id
             and not exists(
               select 1 from public.publication_lesson aula
                where aula.seller_organization_id=p_seller_organization_id
                  and aula.catalog_product_id=p_catalog_product_id
                  and aula.publication_id=anterior.publication_id
                  and not exists(
                    select 1 from public.publication_lesson nova
                     where nova.seller_organization_id=p_seller_organization_id
                       and nova.catalog_product_id=p_catalog_product_id
                       and nova.publication_id=next_publication_id
                       and nova.course_id=aula.course_id
                       and nova.lesson_id=aula.lesson_id))
             and not exists(
               select 1 from public.publication_asset material
                where material.seller_organization_id=p_seller_organization_id
                  and material.catalog_product_id=p_catalog_product_id
                  and material.publication_id=anterior.publication_id
                  and not exists(
                    select 1 from public.publication_asset novo
                     where novo.seller_organization_id=p_seller_organization_id
                       and novo.catalog_product_id=p_catalog_product_id
                       and novo.publication_id=next_publication_id
                       and novo.lesson_id=material.lesson_id
                       and novo.asset_id=material.asset_id));
          if array_length(avancando,1) is not null then
            -- O progresso e guardado por aula, que e como o aluno pensa nele, e
            -- nao por publicacao, que e detalhe nosso.
            select coalesce(jsonb_agg(jsonb_build_object(
                     'entitlement_id',feito.entitlement_id,'course_id',feito.course_id,
                     'lesson_id',feito.lesson_id,'progress_percent',feito.progress_percent,
                     'started_at',feito.started_at,'completed_at',feito.completed_at,
                     'revision',feito.revision)),'[]'::jsonb)
              into progresso_salvo
              from public.lesson_progress feito
             where feito.seller_organization_id=p_seller_organization_id
               and feito.entitlement_id=any(avancando);
            -- A arvore e refeita em vez de movida: as chaves estrangeiras
            -- carregam o publication_id e nenhuma e adiavel, entao mover na
            -- ordem errada quebra e nao existe ordem certa.
            delete from public.lesson_progress alvo
             where alvo.seller_organization_id=p_seller_organization_id
               and alvo.entitlement_id=any(avancando);
            delete from public.course_learning_state alvo
             where alvo.seller_organization_id=p_seller_organization_id
               and alvo.entitlement_id=any(avancando);
            delete from public.learning_enrollment alvo
             where alvo.seller_organization_id=p_seller_organization_id
               and alvo.entitlement_id=any(avancando);
            -- `buyer_entitlement_learning_after_write` recria matricula e
            -- estado do curso na publicacao nova.
            update public.buyer_entitlement acesso
               set publication_id=next_publication_id,updated_at=clock_timestamp()
             where acesso.seller_organization_id=p_seller_organization_id
               and acesso.id=any(avancando);
            get diagnostics avancados = row_count;
            -- O progresso volta pela aula, e so pela aula que continua
            -- existindo na publicacao nova.
            insert into public.lesson_progress(
              seller_organization_id,enrollment_id,buyer_identity_user_id,entitlement_id,
              catalog_product_id,publication_id,course_id,lesson_id,progress_percent,
              started_at,completed_at,revision)
            select matricula.seller_organization_id,matricula.id,
              matricula.buyer_identity_user_id,matricula.entitlement_id,
              matricula.catalog_product_id,matricula.publication_id,matricula.course_id,
              salvo.lesson_id,salvo.progress_percent,salvo.started_at,
              salvo.completed_at,salvo.revision
            from jsonb_to_recordset(progresso_salvo) as salvo(
              entitlement_id uuid,course_id uuid,lesson_id uuid,progress_percent smallint,
              started_at timestamptz,completed_at timestamptz,revision bigint)
            join public.learning_enrollment matricula
              on matricula.seller_organization_id=p_seller_organization_id
             and matricula.entitlement_id=salvo.entitlement_id
             and matricula.course_id=salvo.course_id
             and matricula.publication_id=next_publication_id
            join public.publication_lesson aula
              on aula.seller_organization_id=p_seller_organization_id
             and aula.catalog_product_id=p_catalog_product_id
             and aula.publication_id=next_publication_id
             and aula.course_id=salvo.course_id
             and aula.lesson_id=salvo.lesson_id;
            -- O total de aulas mudou, entao o percentual e refeito. A conta e a
            -- mesma de `record_lesson_progress`, de proposito: aula concluida e
            -- aula em 100%.
            update public.course_learning_state estado
               set completed_lessons=refeito.concluidas,
                   progress_percent=case when estado.total_lessons=0 then 0
                     else floor(refeito.concluidas*100.0/estado.total_lessons)::smallint end,
                   state_version=estado.state_version+1,
                   updated_at=clock_timestamp()
              from (select feito.enrollment_id,
                      count(*) filter (where feito.progress_percent=100) as concluidas
                     from public.lesson_progress feito
                    where feito.seller_organization_id=p_seller_organization_id
                      and feito.entitlement_id=any(avancando)
                    group by feito.enrollment_id) refeito
             where estado.seller_organization_id=p_seller_organization_id
               and estado.enrollment_id=refeito.enrollment_id;
            update public.buyer_entitlement acesso
               set progress_percent=resumo.percentual,updated_at=clock_timestamp()
              from (select estado.entitlement_id,
                      case when sum(estado.total_lessons)=0 then 0
                        else floor(sum(estado.completed_lessons)*100.0
                             /sum(estado.total_lessons))::smallint end as percentual
                     from public.course_learning_state estado
                    where estado.seller_organization_id=p_seller_organization_id
                      and estado.entitlement_id=any(avancando)
                    group by estado.entitlement_id) resumo
             where acesso.seller_organization_id=p_seller_organization_id
               and acesso.id=resumo.entitlement_id;
            insert into audit_event(seller_organization_id,actor_identity_user_id,action,
              target_type,target_id,result,request_id,metadata)
            values(p_seller_organization_id,p_identity_user_id,'catalog.entitlements_advanced',
              'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
              jsonb_build_object('publication_id',next_publication_id,
                'advanced_entitlements',avancados));
          end if;"""
)


def upgrade() -> None:
    op.execute("reset role")
    # A chave nova precisa de um alvo sem o publication_id do outro lado.
    op.execute(
        "alter table public.demo_purchase add constraint demo_purchase_acesso_key "
        "unique(seller_organization_id,id,buyer_identity_user_id,catalog_product_id)"
    )
    op.execute(
        "alter table public.buyer_entitlement "
        "drop constraint buyer_entitlement_demo_purchase_fkey, "
        "add constraint buyer_entitlement_demo_purchase_fkey "
        "foreign key(seller_organization_id,commerce_purchase_id,"
        "buyer_identity_user_id,catalog_product_id) "
        "references public.demo_purchase("
        "seller_organization_id,id,buyer_identity_user_id,catalog_product_id)"
    )
    op.execute("set local role appsell_owner")
    op.execute(
        _REESCREVE.replace("ALVO", _PUBLICAR)
        .replace("DE", _DECLARE_DE)
        .replace("PARA", _DECLARE_PARA)
    )
    op.execute(
        _REESCREVE.replace("ALVO", _PUBLICAR).replace("DE", _ANCORA).replace("PARA", _AVANCO)
    )
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0059)
            .replace("PARA", _READY_ATE_0060)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _BASE_DO_ACESSO)
        .replace("DE", _READY_CHAVE_DE)
        .replace("PARA", _READY_CHAVE_PARA)
    )


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
