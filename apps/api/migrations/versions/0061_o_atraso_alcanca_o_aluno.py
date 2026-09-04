"""O atraso do passado alcança quem já tinha comprado.

A `0060` fez o avanço acontecer **ao publicar**. Ela não move ninguém
retroativamente, e por isso quem já estava preso continuou preso.

Medido no host em 04/09/2026, depois da `0060` aplicada: **sete acessos
ativos** parados em publicação anterior à do produto.

| Produto | Publicação atual | Acessos presos |
|---|---|---|
| `condicoes-gerais` | v3 | 2 na v1, 2 na v2 |
| `gerador-de-cotacoes-comparativas` | v2 | 3 na v1 |

Eles sairiam de lá sozinhos no próximo `publish_product` de cada produto. Esta
migration não espera por isso.

## Por que uma função, e não um bloco de backfill

O avanço passa a ter nome: `app_private.advance_entitlements_to`. O
`publish_product` chama a função em vez de carregar o corpo dentro de si, e o
backfill chama **a mesma função**. Uma implementação só.

Escrever o backfill como um segundo bloco de SQL com a mesma guarda copiada
seria criar duas regras que precisam concordar para sempre. Elas não
concordariam: a primeira vez que a guarda mudasse, uma das duas ficaria para
trás em silêncio.

O texto que sai do `publish_product` é lido da própria `0060`, e não copiado
aqui. Se alguém editar aquele arquivo depois de aplicado, o `replace` não acha a
âncora e esta migration falha alto, em vez de reescrever a função errada.

## O que a função garante, e continua garantindo

Só avança quem não perde nada: a publicação de destino precisa conter tudo o que
a anterior tinha, aula por aula e material por material. É a mesma guarda da
`0060`, agora num lugar só.

Autorizado pelo Rodrigo em 04/09/2026.

Revision ID: 0061_o_atraso_alcanca_o_aluno
Revises: 0060_atualizacao_chega_ao_aluno
"""

from __future__ import annotations

from importlib import util as importlib_util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0061_o_atraso_alcanca_o_aluno"
down_revision = "0060_atualizacao_chega_ao_aluno"
branch_labels = None
depends_on = None

_READY_ATE_0060 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno'))"
)
_READY_ATE_0061 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)
_BASE_DO_ACESSO = "app_private.schema_ready_base_0016()"
_AVANCA = "app_private.advance_entitlements_to(uuid,uuid,uuid,uuid,uuid,text)"
_READY_FUNCAO_DE = "            and to_regclass('public.demo_purchase_acesso_key') is not null\n"
_READY_FUNCAO_PARA = (
    _READY_FUNCAO_DE + f"            and to_regprocedure('{_AVANCA}') is not null\n"
)

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


def _migration_0060() -> ModuleType:
    """Lê o texto que a `0060` colocou dentro do `publish_product`.

    Copiar o bloco para cá deixaria duas cópias que precisam continuar iguais.
    Lendo do arquivo original, uma divergência vira erro do `replace`, não um
    `publish_product` reescrito pela metade.

    O caminho sai de `down_revision`: o nome literal envelhece na próxima
    migration e vira um arquivo fixado numa versão que já passou.
    """
    caminho = Path(__file__).with_name(f"{down_revision}.py")
    spec = importlib_util.spec_from_file_location("appsell_migration_0060", caminho)
    if spec is None or spec.loader is None:  # pragma: no cover - caminho fixo no repo
        raise RuntimeError("a migration 0060 nao pode ser lida")
    modulo = importlib_util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_FUNCAO = r"""
create function app_private.advance_entitlements_to(
  p_seller_organization_id uuid, p_catalog_product_id uuid, p_publication_id uuid,
  p_actor_identity_user_id uuid, p_request_id uuid, p_reason text
) returns integer language plpgsql security definer
set search_path=pg_catalog,app_private,public as $$
declare avancando uuid[]; avancados integer:=0;
  progresso_salvo jsonb; pedidos_salvos jsonb;
begin
  if p_seller_organization_id is null or p_catalog_product_id is null
     or p_publication_id is null or p_request_id is null
     or p_reason not in ('publication','backfill') then
    raise exception 'invalid entitlement advance request' using errcode='22023';
  end if;
  -- Só avança quem não perde nada: a publicação de destino precisa conter tudo
  -- o que a anterior tinha, aula por aula e material por material. Era isso que
  -- o congelamento protegia, e continua protegido. Se ela remover alguma coisa,
  -- ninguém avança, e a decisão passa a ser do aluno numa tela própria.
  select coalesce(array_agg(anterior.id),'{}')
    into avancando
    from public.buyer_entitlement anterior
   where anterior.seller_organization_id=p_seller_organization_id
     and anterior.catalog_product_id=p_catalog_product_id
     and anterior.status='active'
     and anterior.publication_id is distinct from p_publication_id
     and not exists(
       select 1 from public.publication_lesson aula
        where aula.seller_organization_id=p_seller_organization_id
          and aula.catalog_product_id=p_catalog_product_id
          and aula.publication_id=anterior.publication_id
          and not exists(
            select 1 from public.publication_lesson nova
             where nova.seller_organization_id=p_seller_organization_id
               and nova.catalog_product_id=p_catalog_product_id
               and nova.publication_id=p_publication_id
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
               and novo.publication_id=p_publication_id
               and novo.lesson_id=material.lesson_id
               and novo.asset_id=material.asset_id));
  if array_length(avancando,1) is null then return 0; end if;
  -- O progresso é guardado por aula, que é como o aluno pensa nele, e não por
  -- publicação, que é detalhe nosso.
  select coalesce(jsonb_agg(jsonb_build_object(
           'entitlement_id',feito.entitlement_id,'course_id',feito.course_id,
           'lesson_id',feito.lesson_id,'progress_percent',feito.progress_percent,
           'started_at',feito.started_at,'completed_at',feito.completed_at,
           'revision',feito.revision)),'[]'::jsonb)
    into progresso_salvo
    from public.lesson_progress feito
   where feito.seller_organization_id=p_seller_organization_id
     and feito.entitlement_id=any(avancando);
  -- `learning_progress_request` guarda o recibo de cada pedido de progresso, e
  -- aponta para o progresso pela matrícula. A matrícula é recriada com
  -- identidade nova, então o recibo precisa ser repontado junto. Sem isto o
  -- `delete` abaixo bate na chave estrangeira e a publicação inteira falha.
  --
  -- Os testes não pegavam: a tabela nasce vazia em banco de teste, e só o
  -- ensaio contra a cópia da produção revelou (04/09/2026, seis recibos).
  select coalesce(jsonb_agg(jsonb_build_object(
           'buyer_identity_user_id',recibo.buyer_identity_user_id,
           'idempotency_key',recibo.idempotency_key,
           'entitlement_id',recibo.entitlement_id,'lesson_id',recibo.lesson_id,
           'body_sha256',recibo.body_sha256,'result',recibo.result,
           'created_at',recibo.created_at)),'[]'::jsonb)
    into pedidos_salvos
    from app_private.learning_progress_request recibo
   where recibo.seller_organization_id=p_seller_organization_id
     and recibo.entitlement_id=any(avancando);
  delete from app_private.learning_progress_request alvo
   where alvo.seller_organization_id=p_seller_organization_id
     and alvo.entitlement_id=any(avancando);
  -- A árvore é refeita em vez de movida: as chaves estrangeiras carregam o
  -- publication_id e nenhuma é adiável, então mover na ordem errada quebra e
  -- não existe ordem certa.
  delete from public.lesson_progress alvo
   where alvo.seller_organization_id=p_seller_organization_id
     and alvo.entitlement_id=any(avancando);
  delete from public.course_learning_state alvo
   where alvo.seller_organization_id=p_seller_organization_id
     and alvo.entitlement_id=any(avancando);
  delete from public.learning_enrollment alvo
   where alvo.seller_organization_id=p_seller_organization_id
     and alvo.entitlement_id=any(avancando);
  -- `buyer_entitlement_learning_after_write` recria matrícula e estado do curso.
  update public.buyer_entitlement acesso
     set publication_id=p_publication_id,updated_at=clock_timestamp()
   where acesso.seller_organization_id=p_seller_organization_id
     and acesso.id=any(avancando);
  get diagnostics avancados = row_count;
  -- O progresso volta pela aula, e só pela aula que continua existindo.
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
   and matricula.publication_id=p_publication_id
  join public.publication_lesson aula
    on aula.seller_organization_id=p_seller_organization_id
   and aula.catalog_product_id=p_catalog_product_id
   and aula.publication_id=p_publication_id
   and aula.course_id=salvo.course_id
   and aula.lesson_id=salvo.lesson_id;
  -- O recibo volta apontando para a matrícula nova. O join com o progresso já
  -- recriado é o que garante a chave estrangeira: recibo de aula que saiu não
  -- tem para onde voltar, pelo mesmo motivo que o progresso dela não tem.
  insert into app_private.learning_progress_request(
    buyer_identity_user_id,idempotency_key,seller_organization_id,enrollment_id,
    entitlement_id,lesson_id,body_sha256,result,created_at)
  select salvo.buyer_identity_user_id,salvo.idempotency_key,p_seller_organization_id,
    feito.enrollment_id,salvo.entitlement_id,salvo.lesson_id,salvo.body_sha256,
    salvo.result,salvo.created_at
  from jsonb_to_recordset(pedidos_salvos) as salvo(
    buyer_identity_user_id uuid,idempotency_key uuid,entitlement_id uuid,
    lesson_id uuid,body_sha256 text,result jsonb,created_at timestamptz)
  join public.lesson_progress feito
    on feito.seller_organization_id=p_seller_organization_id
   and feito.entitlement_id=salvo.entitlement_id
   and feito.lesson_id=salvo.lesson_id
   and feito.publication_id=p_publication_id;
  -- O total de aulas mudou, então o percentual é refeito. A conta é a mesma de
  -- `record_lesson_progress`, de propósito: aula concluída é aula em 100%.
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
  insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,
    target_type,target_id,result,request_id,metadata)
  values(p_seller_organization_id,p_actor_identity_user_id,'catalog.entitlements_advanced',
    'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
    jsonb_build_object('publication_id',p_publication_id,
      'advanced_entitlements',avancados,'reason',p_reason));
  return avancados;
end $$
"""

#: O bloco inteiro da `0060` sai do `publish_product` e vira uma chamada.
_CHAMADA = """
          perform app_private.advance_entitlements_to(
            p_seller_organization_id,p_catalog_product_id,next_publication_id,
            p_identity_user_id,p_request_id,'publication');"""

#: As variáveis do bloco antigo passaram a viver dentro da função.
_DECLARE_DE = "          avancando uuid[]; avancados integer:=0; progresso_salvo jsonb;\n"

_BACKFILL = """
do $migration$
declare produto record; alcancados integer; total integer:=0;
begin
  for produto in
    select alvo.seller_organization_id, alvo.id, alvo.published_snapshot_id
      from public.catalog_product alvo
     where alvo.published_snapshot_id is not null
     order by alvo.seller_organization_id, alvo.id
  loop
    alcancados := app_private.advance_entitlements_to(
      produto.seller_organization_id, produto.id, produto.published_snapshot_id,
      null, gen_random_uuid(), 'backfill'
    );
    total := total + alcancados;
  end loop;
  raise notice 'acessos alcancados pelo backfill: %', total;
end $migration$
"""


def upgrade() -> None:
    op.execute("set local role appsell_owner")
    op.execute(_FUNCAO)
    op.execute(
        "revoke all on function app_private.advance_entitlements_to("
        "uuid,uuid,uuid,uuid,uuid,text) from public"
    )
    migration_0060 = _migration_0060()
    op.execute(
        _REESCREVE.replace("ALVO", _PUBLICAR)
        .replace("DE", migration_0060._AVANCO)
        .replace("PARA", migration_0060._ANCORA + _CHAMADA)
    )
    op.execute(_REESCREVE.replace("ALVO", _PUBLICAR).replace("DE", _DECLARE_DE).replace("PARA", ""))
    op.execute(_BACKFILL)
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0060)
            .replace("PARA", _READY_ATE_0061)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _BASE_DO_ACESSO)
        .replace("DE", _READY_FUNCAO_DE)
        .replace("PARA", _READY_FUNCAO_PARA)
    )


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
