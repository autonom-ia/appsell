"""Quem entra pelo link da turma também recebe o "acesso liberado".

A 0049 fez a compra avisar quem comprou. O outro caminho de entrada — o link
de turma, resgatado em `redeem_product_access_link` — continuava mudo: a
pessoa clicava, ganhava o acesso e ninguém dizia onde ele estava. Esta
migration insere a mesma intenção de enviar, na mesma transação, e só no
ramo em que a vaga é consumida pela primeira vez — quem repete o link não
recebe de novo, e a chave por (link, pessoa) garante isso mesmo com retry.

A função é reescrita pelo padrão da 0048/0049 (`pg_get_functiondef` +
`replace` ancorado, âncora exatamente uma vez), com a assinatura preservada.
O `check` de `kind` passa a aceitar o tipo novo; o worker que sabe montar
esse e-mail sobe junto, e uma linha que ele não conhecesse cairia em
`dead_letter` com `kind_unknown` — visível, sem tocar no acesso.
"""

from __future__ import annotations

from alembic import op

revision = "0050_recibo_do_link_de_turma"
down_revision = "0049_caixa_de_saida_de_email"
branch_labels = None
depends_on = None

_REDEEM = "app_private.redeem_product_access_link(uuid,text,uuid,text,uuid)"

_READY_ATE_0049 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email'))"
)
_READY_ATE_0050 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email','0050_recibo_do_link_de_turma'))"
)

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)

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
end $migration$;
"""

# Âncora conferida contra o texto real da função: ocorre uma vez, dentro do
# ramo "primeira vez desta pessoa neste link", depois de contar a vaga.
_ANCORA = """    returning * into locked_link;
    consumed:=true;
"""

_TRECHO_INSERIDO = """    returning * into locked_link;
    insert into app_private.email_outbox(
      seller_organization_id,kind,dedupe_key,recipient_identity_user_id,
      recipient_email,payload
    ) select locked_link.seller_organization_id,'access_link_redeemed',
      'access_link:'||locked_link.id::text||':'||p_identity_user_id::text,
      p_identity_user_id,buyer.email_normalized,
      jsonb_build_object('schema_version',1,
        'entitlement_id',granted.entitlement_id,
        'catalog_product_id',locked_link.catalog_product_id,
        'access_link_id',locked_link.id,
        'product_title',catalog.title,
        'seller_display_name',seller.display_name,
        'seller_slug',seller.slug)
    from public.catalog_product catalog
    join public.seller_organization seller
      on seller.id=locked_link.seller_organization_id
    join public.identity_user buyer on buyer.id=p_identity_user_id
    where catalog.seller_organization_id=locked_link.seller_organization_id
      and catalog.id=locked_link.catalog_product_id
    on conflict on constraint email_outbox_dedupe_key do nothing;
    consumed:=true;
"""

_KIND_ANTES = "check (kind in ('purchase_confirmed'))"
_KIND_DEPOIS = "check (kind in ('purchase_confirmed','access_link_redeemed'))"


def upgrade() -> None:
    op.execute("alter table app_private.email_outbox drop constraint email_outbox_kind_check")
    op.execute(
        f"alter table app_private.email_outbox add constraint email_outbox_kind_check {_KIND_DEPOIS}"
    )
    op.execute(
        _REESCREVE.replace("ALVO", _REDEEM).replace("DE", _ANCORA).replace("PARA", _TRECHO_INSERIDO)
    )
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0049)
            .replace("PARA", _READY_ATE_0050)
        )


def downgrade() -> None:
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0050)
            .replace("PARA", _READY_ATE_0049)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _REDEEM).replace("DE", _TRECHO_INSERIDO).replace("PARA", _ANCORA)
    )
    # Linhas do tipo novo ainda pendentes impediriam o check antigo: elas
    # deixam de existir junto com a capacidade de enviá-las.
    op.execute("delete from app_private.email_outbox where kind='access_link_redeemed'")
    op.execute("alter table app_private.email_outbox drop constraint email_outbox_kind_check")
    op.execute(
        f"alter table app_private.email_outbox add constraint email_outbox_kind_check {_KIND_ANTES}"
    )
