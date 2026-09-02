"""Quem entra pelo link da turma também recebe o "acesso liberado".

A 0049 fez a compra avisar quem comprou. O outro caminho de entrada — o link
de turma, resgatado em `redeem_product_access_link` — continuava mudo: a
pessoa clicava, ganhava o acesso e ninguém dizia onde ele estava. Esta
migration insere a mesma intenção de enviar, na mesma transação, e só no
ramo em que a vaga é consumida pela primeira vez — quem repete o link não
recebe de novo, e a chave por (link, pessoa) garante isso mesmo com retry.

A função é reescrita pelo padrão da 0048/0049 (`pg_get_functiondef` +
`replace` ancorado, âncora exatamente uma vez), com a assinatura preservada.
O `check` de `kind` passa a aceitar o tipo novo, e `fail_email_outbox` passa a
tratar `kind_unknown` como falha do worker, não do item: nunca vira
`dead_letter` e devolve a tentativa. Sem isso, um worker que não conheça o
tipo — o de agora, antes do deploy, ou o anterior num rollback — apagaria
e-mails legítimos de vez.

Ordem de deploy: **a API nova antes desta migration.** O worker novo com o
banco antigo é inofensivo (nenhuma linha do tipo novo existe); na ordem
inversa, e num rollback para a imagem anterior, as linhas ficam esperando o
worker que sabe montá-las.
"""

from __future__ import annotations

from alembic import op

revision = "0050_recibo_do_link_de_turma"
down_revision = "0049_caixa_de_saida_de_email"
branch_labels = None
depends_on = None

_REDEEM = "app_private.redeem_product_access_link(uuid,text,uuid,text,uuid)"
_FAIL = "app_private.fail_email_outbox(uuid,text,uuid,uuid,bigint,text,integer,boolean,uuid)"

# Um tipo que o worker não conhece é worker velho, não item errado — e um
# rollback para a imagem anterior é exatamente isso. No banco, para valer
# mesmo quando quem chama é o binário antigo, que manda p_permanent=true.
_LISTA_DO_CANAL = "p_error in ('smtp_auth','smtp_unreachable','smtp_no_starttls','smtp_no_auth')"
_LISTA_COM_KIND = (
    "p_error in ('smtp_auth','smtp_unreachable','smtp_no_starttls','smtp_no_auth','kind_unknown')"
)
# A lista aparece DUAS vezes na função (no status e no attempt_count): a linha
# seguinte é o que distingue uma da outra, e vai junto na âncora.
_STATUS_ANTES = _LISTA_DO_CANAL + "\n            then 'retry'"
_STATUS_DEPOIS = _LISTA_COM_KIND + "\n            then 'retry'"
_TENTATIVA_ANTES = _LISTA_DO_CANAL + "\n            then greatest("
_TENTATIVA_DEPOIS = _LISTA_COM_KIND + "\n            then greatest("
# p_permanent é avaliado antes da lista, então um chamador antigo (que manda
# permanent=true para um kind que não conhece) mataria a linha. Este ramo entra
# na frente de todos.
_PERMANENTE_ANTES = "when p_permanent then 'dead_letter'"
_PERMANENTE_DEPOIS = (
    "when p_error='kind_unknown' then 'retry'\n          when p_permanent then 'dead_letter'"
)

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

# Variante para as reescritas que o downgrade deixa no lugar de propósito (a
# isenção de kind_unknown): sem isto, um segundo upgrade não acharia mais a
# âncora e a migration deixaria de ser repetível.
_REESCREVE_SE_PRECISAR = """
do $migration$
declare
  definicao text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if position($ja$PARA$ja$ in definicao) > 0 then
    return;
  end if;
  if (length(definicao) - length(replace(definicao, $de$DE$de$, ''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO não continha a âncora exatamente uma vez' using errcode='55000';
  end if;
  execute replace(definicao, $de$DE$de$, $para$PARA$para$);
end $migration$;
"""

# Âncora conferida contra o texto real da função: ocorre uma vez, dentro do
# ramo "primeira vez desta pessoa neste link", depois de contar a vaga.
_ANCORA = """    returning * into locked_link;
    consumed:=true;
"""

# O acesso "virar ativo" é o que justifica o e-mail — e isso não é o mesmo que
# criar a linha: uma entitlement revogada ou expirada que o link reativa também
# libera o curso. O estado anterior tem de ser lido ANTES do grant, porque
# depois dele já é 'active'.
_ANCORA_DECLARE = """  consumed boolean;
  remaining integer;
"""
_DECLARE_COM_ESTADO = """  consumed boolean;
  remaining integer;
  previous_access text;
"""

_ANCORA_ANTES_DO_GRANT = """  select * into granted
  from app_private.grant_public_product_access("""
_LEITURA_ANTES_DO_GRANT = """  select entitlement.status into previous_access
  from public.buyer_entitlement entitlement
  where entitlement.seller_organization_id=locked_link.seller_organization_id
    and entitlement.buyer_identity_user_id=p_identity_user_id
    and entitlement.catalog_product_id=locked_link.catalog_product_id;

  select * into granted
  from app_private.grant_public_product_access("""

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
      -- Só quando o link fez o acesso VIRAR ativo: linha nova, ou uma
      -- entitlement revogada/expirada que ele reativou. Quem já tinha o curso
      -- consome a vaga e não ouve que "o link liberou o curso" — não liberou.
      and previous_access is distinct from 'active'
    on conflict on constraint email_outbox_dedupe_key do nothing;
    consumed:=true;
"""

_KIND_DEPOIS = "check (kind in ('purchase_confirmed','access_link_redeemed'))"


def upgrade() -> None:
    # Idempotente de propósito: um downgrade deixa o check já alargado.
    op.execute(
        "alter table app_private.email_outbox drop constraint if exists email_outbox_kind_check"
    )
    op.execute(
        f"alter table app_private.email_outbox add constraint email_outbox_kind_check {_KIND_DEPOIS}"
    )
    op.execute(
        _REESCREVE_SE_PRECISAR.replace("ALVO", _FAIL)
        .replace("DE", _PERMANENTE_ANTES)
        .replace("PARA", _PERMANENTE_DEPOIS)
    )
    op.execute(
        _REESCREVE_SE_PRECISAR.replace("ALVO", _FAIL)
        .replace("DE", _STATUS_ANTES)
        .replace("PARA", _STATUS_DEPOIS)
    )
    op.execute(
        _REESCREVE_SE_PRECISAR.replace("ALVO", _FAIL)
        .replace("DE", _TENTATIVA_ANTES)
        .replace("PARA", _TENTATIVA_DEPOIS)
    )
    op.execute(
        _REESCREVE.replace("ALVO", _REDEEM)
        .replace("DE", _ANCORA_DECLARE)
        .replace("PARA", _DECLARE_COM_ESTADO)
    )
    op.execute(
        _REESCREVE.replace("ALVO", _REDEEM)
        .replace("DE", _ANCORA_ANTES_DO_GRANT)
        .replace("PARA", _LEITURA_ANTES_DO_GRANT)
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
    # Desfaz a origem dos e-mails (a função) e o ready. O check de kind fica
    # como está, aceitando o tipo novo: restaurá-lo exigiria apagar toda linha
    # desse tipo — inclusive as enviadas, que são registro de entrega — e uma
    # coluna mais permissiva não quebra nada; o worker antigo devolve um kind
    # que não conhece como retry, sem tocar no acesso.
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0050)
            .replace("PARA", _READY_ATE_0049)
        )
    op.execute(
        _REESCREVE.replace("ALVO", _REDEEM).replace("DE", _TRECHO_INSERIDO).replace("PARA", _ANCORA)
    )
    op.execute(
        _REESCREVE.replace("ALVO", _REDEEM)
        .replace("DE", _LEITURA_ANTES_DO_GRANT)
        .replace("PARA", _ANCORA_ANTES_DO_GRANT)
    )
    op.execute(
        _REESCREVE.replace("ALVO", _REDEEM)
        .replace("DE", _DECLARE_COM_ESTADO)
        .replace("PARA", _ANCORA_DECLARE)
    )
    # A isenção de kind_unknown FICA: ela protege justamente o worker antigo
    # que este downgrade acompanha.
