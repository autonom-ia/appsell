"""A caixa de saída de e-mail nasce na mesma transação que libera o acesso.

Até aqui a compra terminava em silêncio: `apply_checkout_fulfillment` gravava
o `buyer_entitlement` e ninguém contava ao comprador. O e-mail de confirmação
não pode ser um passo depois do commit — um SMTP fora do ar no instante do
pagamento não pode impedir o acesso, e um processo que morre entre o commit e
o envio não pode perder o e-mail. Por isso a intenção de enviar é uma linha
em `app_private.email_outbox`, inserida dentro da própria função que concede
o acesso, e o envio acontece depois, por um worker com lease e fence — o mesmo
desenho da fila de fulfillment (0020), que já está provado em produção.

Idempotência vem da chave da compra: `(seller, 'purchase:<integração>:<sha
da compra>')` é única, então um webhook reprocessado, que já não cria segundo
grant, também não cria segundo e-mail.

A função de fulfillment é reescrita pelo padrão da 0048 (`pg_get_functiondef`
+ `replace` ancorado): a inserção entra logo após o `returning` do
`buyer_entitlement`, e só nesse ramo — reembolso e revogação não geram e-mail.
A função continua com a mesma assinatura, então a API em produção segue
funcionando entre a migration e o deploy; ela apenas passa a deixar linhas
pendentes que o worker novo consome.

O registro guarda o necessário para auditar a entrega — destinatário, tipo,
momento, resultado, `Message-ID` — e não guarda o corpo. Políticas em
docs/architecture/EMAIL_POLICY.md.
"""

from __future__ import annotations

from alembic import op

revision = "0049_caixa_de_saida_de_email"
down_revision = "0048_nome_de_quem_entrou"
branch_labels = None
depends_on = None

_CAPACIDADE = "notification.email.worker"
_APPLY = "app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text)"

_READY_ATE_0048 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou'))"
)
_READY_ATE_0049 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou',"
    "'0049_caixa_de_saida_de_email'))"
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
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  if reescrita = definicao then
    raise exception 'ALVO não continha o texto esperado' using errcode='55000';
  end if;
  execute reescrita;
end $migration$;
"""

# Âncora conferida contra o texto real da função em banco: ocorre uma vez, no
# ramo de purchase.approved, logo depois do upsert de buyer_entitlement.
_ANCORA = """              expires_at=null,updated_at=clock_timestamp()
            returning id into v_entitlement_id;
"""

_TRECHO_INSERIDO = """            insert into app_private.email_outbox(
              seller_organization_id,kind,dedupe_key,recipient_identity_user_id,
              recipient_email,payload
            ) select p_seller,'purchase_confirmed',
              'purchase:'||event.checkout_integration_id::text||':'
                ||job.external_purchase_id_sha256,
              buyer_id,buyer.email_normalized,
              jsonb_build_object('schema_version',1,
                'entitlement_id',v_entitlement_id,
                'catalog_product_id',mapped_product_id,
                'product_title',catalog.title,
                'seller_display_name',seller.display_name,
                'seller_slug',seller.slug)
            from public.catalog_product catalog
            join public.seller_organization seller on seller.id=p_seller
            join public.identity_user buyer on buyer.id=buyer_id
            where catalog.seller_organization_id=p_seller
              and catalog.id=mapped_product_id
            on conflict on constraint email_outbox_dedupe_key do nothing;
"""
_INSERCAO = _ANCORA + _TRECHO_INSERIDO

_TABELAS = (
    """
    create table app_private.email_worker_identity (
      identity_user_id uuid primary key references public.identity_user(id),
      status text not null check (status in ('active','revoked')),
      label text not null check (
        label=btrim(label) and octet_length(label) between 3 and 120
        and label!~'[[:cntrl:]]'
      ),
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp()
    )
    """,
    """
    create table app_private.email_outbox (
      seller_organization_id uuid not null references public.seller_organization(id),
      id uuid not null default gen_random_uuid(),
      kind text not null check (kind in ('purchase_confirmed')),
      dedupe_key text not null check (
        dedupe_key ~ '^[a-z_]+:[A-Za-z0-9._:-]{8,200}$'
      ),
      recipient_identity_user_id uuid not null references public.identity_user(id),
      recipient_email text not null check (
        recipient_email = lower(btrim(recipient_email))
        and position('@' in recipient_email) > 1
        and octet_length(recipient_email) between 6 and 320
        and recipient_email !~ '[[:cntrl:][:space:]]'
      ),
      payload jsonb not null check (
        jsonb_typeof(payload)='object' and octet_length(payload::text) <= 4096
      ),
      status text not null default 'pending' check (
        status in ('pending','retry','leased','sent','dead_letter')
      ),
      attempt_count integer not null default 0 check (attempt_count between 0 and 32),
      lease_owner text check (
        lease_owner is null or lease_owner ~ '^[a-z0-9][a-z0-9._-]{2,63}$'
      ),
      lease_fence bigint not null default 0,
      lease_expires_at timestamptz,
      error_code text check (
        error_code is null or error_code ~ '^[a-z_]{3,40}$'
      ),
      message_id text check (
        message_id is null or (
          octet_length(message_id) between 3 and 255 and message_id !~ '[[:cntrl:][:space:]]'
        )
      ),
      sent_at timestamptz,
      available_at timestamptz not null default clock_timestamp(),
      created_at timestamptz not null default clock_timestamp(),
      updated_at timestamptz not null default clock_timestamp(),
      primary key (seller_organization_id,id),
      constraint email_outbox_dedupe_key unique (seller_organization_id,dedupe_key)
    )
    """,
    """
    create index email_outbox_pending_idx
      on app_private.email_outbox(available_at,created_at,id)
      where status in ('pending','retry','leased')
    """,
)

# Molde sem f-string: a capacidade entra por replace, e o ruff não vê SQL
# montado por interpolação (S608) onde só há uma constante deste arquivo.
_FUNCOES_MOLDE = (
    """
    create function app_private.ensure_email_worker(
      p_worker uuid,p_issuer text,p_subject text,p_email text,p_label text
    ) returns void language plpgsql security definer
    set search_path=pg_catalog,app_private,public as $$
    begin
      if app_private.context_text('capability') is distinct from '__CAPACIDADE__'
         or app_private.context_uuid('identity_user_id') is distinct from p_worker
         or app_private.context_uuid('seller_organization_id') is not null
         or p_worker is null
         or p_issuer is null or length(p_issuer) not between 3 and 255
         or p_subject is null or length(p_subject) not between 3 and 255
         or p_email is null or p_email <> lower(btrim(p_email))
         or position('@' in p_email) <= 1
         or p_label is null or p_label !~ '^[a-z0-9][a-z0-9._-]{2,63}$' then
        raise exception 'email worker registration is not authorized' using errcode='28000';
      end if;
      insert into public.identity_user(
        id,identity_issuer,identity_sub,email_normalized,email_verified
      ) values (p_worker,p_issuer,p_subject,p_email,true)
      on conflict (identity_issuer,identity_sub) do update set
        email_normalized=excluded.email_normalized,email_verified=true,
        updated_at=clock_timestamp()
      where identity_user.id=excluded.id;
      insert into app_private.email_worker_identity(identity_user_id,status,label)
      values (p_worker,'active',p_label)
      on conflict (identity_user_id) do update set
        label=excluded.label,updated_at=clock_timestamp();
    end $$
    """,
    # A faixa de lease [5,300] é a mesma da fila de fulfillment. O worker tem
    # um teste que lê esta faixa da própria função e compara com a constante
    # dele — a rede que faltou na #284, quando o worker passou a ser recusado
    # em silêncio por um lease fora da faixa.
    """
    create function app_private.claim_email_outbox(
      p_worker uuid,p_label text,p_limit integer,p_lease_seconds integer
    ) returns jsonb language plpgsql security definer
    set search_path=pg_catalog,app_private,public as $$
    declare claimed jsonb;
    begin
      if app_private.context_text('capability') is distinct from '__CAPACIDADE__'
         or app_private.context_uuid('identity_user_id') is distinct from p_worker
         or app_private.context_uuid('seller_organization_id') is not null
         or p_label is null or p_label!~'^[a-z0-9][a-z0-9._-]{2,63}$'
         or p_limit not between 1 and 8 or p_lease_seconds not between 5 and 300
         or not exists(select 1 from app_private.email_worker_identity worker
           where worker.identity_user_id=p_worker and worker.status='active') then
        raise exception 'email worker is not authorized' using errcode='28000';
      end if;
      update app_private.email_outbox queued set status='dead_letter',
        lease_owner=null,lease_expires_at=null,error_code='send_unexpected',
        updated_at=clock_timestamp()
      where queued.status='leased' and queued.lease_expires_at<=clock_timestamp()
        and queued.attempt_count>=10;
      with candidates as (
        select queued.seller_organization_id,queued.id
        from app_private.email_outbox queued
        where queued.attempt_count<10 and (
          (queued.status in ('pending','retry') and queued.available_at<=clock_timestamp())
          or (queued.status='leased' and queued.lease_expires_at<=clock_timestamp())
        )
        order by queued.available_at,queued.created_at,queued.id
        for update skip locked limit p_limit
      ), leased as (
        update app_private.email_outbox queued set status='leased',
          attempt_count=queued.attempt_count+1,lease_owner=p_label,
          lease_fence=queued.lease_fence+1,
          lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
          error_code=null,updated_at=clock_timestamp()
        from candidates where queued.seller_organization_id=candidates.seller_organization_id
          and queued.id=candidates.id
        returning queued.seller_organization_id,queued.id,queued.kind,queued.lease_fence,
          queued.attempt_count,queued.recipient_email,queued.recipient_identity_user_id,
          queued.payload
      ) select coalesce(jsonb_agg(
          to_jsonb(leased) || jsonb_build_object(
            'recipient_name',(select display_name from public.identity_user person
              where person.id=leased.recipient_identity_user_id)
          ) order by leased.id),'[]'::jsonb)
        into claimed from leased;
      return claimed;
    end $$
    """,
    """
    create function app_private.complete_email_outbox(
      p_worker uuid,p_label text,p_seller uuid,p_outbox uuid,p_fence bigint,
      p_message_id text,p_request uuid
    ) returns void language plpgsql security definer
    set search_path=pg_catalog,app_private,public as $$
    declare row_kind text; row_attempt integer;
    begin
      if app_private.context_text('capability') is distinct from '__CAPACIDADE__'
         or app_private.context_uuid('identity_user_id') is distinct from p_worker
         or app_private.context_uuid('seller_organization_id') is distinct from p_seller
         or p_message_id is null or octet_length(p_message_id) not between 3 and 255
         or p_message_id ~ '[[:cntrl:][:space:]]' or p_request is null
         or not exists(select 1 from app_private.email_worker_identity worker
           where worker.identity_user_id=p_worker and worker.status='active') then
        raise exception 'email completion is not authorized' using errcode='28000';
      end if;
      update app_private.email_outbox queued set status='sent',
        lease_owner=null,lease_expires_at=null,error_code=null,
        message_id=p_message_id,sent_at=clock_timestamp(),updated_at=clock_timestamp()
      where queued.seller_organization_id=p_seller and queued.id=p_outbox
        and queued.status='leased' and queued.lease_owner=p_label
        and queued.lease_fence=p_fence and queued.lease_expires_at>clock_timestamp()
      returning queued.kind,queued.attempt_count into row_kind,row_attempt;
      if not found then
        raise exception 'email lease is unavailable' using errcode='40001';
      end if;
      insert into public.audit_event(seller_organization_id,actor_identity_user_id,
        action,target_type,target_id,result,request_id,metadata)
      values(p_seller,p_worker,'notification.email_sent','email_outbox',p_outbox,
        'succeeded',p_request,jsonb_build_object('kind',row_kind,
          'attempt',row_attempt,'message_id',p_message_id));
    end $$
    """,
    # Falha permanente (destinatário recusado, corpo impossível de montar) vai
    # direto para dead_letter; temporária volta para retry com o atraso pedido,
    # e vira dead_letter na décima tentativa.
    """
    create function app_private.fail_email_outbox(
      p_worker uuid,p_label text,p_seller uuid,p_outbox uuid,p_fence bigint,
      p_error text,p_delay_seconds integer,p_permanent boolean,p_request uuid
    ) returns text language plpgsql security definer
    set search_path=pg_catalog,app_private,public as $$
    declare new_status text; row_kind text; row_attempt integer;
    begin
      if app_private.context_text('capability') is distinct from '__CAPACIDADE__'
         or app_private.context_uuid('identity_user_id') is distinct from p_worker
         or app_private.context_uuid('seller_organization_id') is distinct from p_seller
         or p_error is null or p_error !~ '^[a-z_]{3,40}$'
         or p_delay_seconds not between 1 and 3600 or p_permanent is null
         or p_request is null
         or not exists(select 1 from app_private.email_worker_identity worker
           where worker.identity_user_id=p_worker and worker.status='active') then
        raise exception 'email failure record is not authorized' using errcode='28000';
      end if;
      update app_private.email_outbox queued set
        status=case when p_permanent or queued.attempt_count>=10
          then 'dead_letter' else 'retry' end,
        available_at=clock_timestamp()+make_interval(secs=>p_delay_seconds),
        lease_owner=null,lease_expires_at=null,error_code=p_error,
        updated_at=clock_timestamp()
      where queued.seller_organization_id=p_seller and queued.id=p_outbox
        and queued.status='leased' and queued.lease_owner=p_label
        and queued.lease_fence=p_fence and queued.lease_expires_at>clock_timestamp()
      returning queued.status,queued.kind,queued.attempt_count
        into new_status,row_kind,row_attempt;
      if not found then
        raise exception 'email lease is unavailable' using errcode='40001';
      end if;
      insert into public.audit_event(seller_organization_id,actor_identity_user_id,
        action,target_type,target_id,result,request_id,metadata)
      values(p_seller,p_worker,'notification.email_failed','email_outbox',p_outbox,
        'failed',p_request,jsonb_build_object('kind',row_kind,'attempt',row_attempt,
          'error_code',p_error,'status',new_status));
      return new_status;
    end $$
    """,
)

_FUNCOES = tuple(molde.replace("__CAPACIDADE__", _CAPACIDADE) for molde in _FUNCOES_MOLDE)

_ASSINATURAS = (
    "app_private.ensure_email_worker(uuid,text,text,text,text)",
    "app_private.claim_email_outbox(uuid,text,integer,integer)",
    "app_private.complete_email_outbox(uuid,text,uuid,uuid,bigint,text,uuid)",
    "app_private.fail_email_outbox(uuid,text,uuid,uuid,bigint,text,integer,boolean,uuid)",
)


def upgrade() -> None:
    for sql in _TABELAS:
        op.execute(sql)
    op.execute("alter table app_private.email_worker_identity owner to appsell_owner")
    op.execute("alter table app_private.email_outbox owner to appsell_owner")
    op.execute(
        "revoke all on table app_private.email_outbox,app_private.email_worker_identity "
        "from public,appsell_runtime"
    )
    for sql in _FUNCOES:
        op.execute(sql)
    for assinatura in _ASSINATURAS:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")
    op.execute(_REESCREVE.replace("ALVO", _APPLY).replace("DE", _ANCORA).replace("PARA", _INSERCAO))
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0048)
            .replace("PARA", _READY_ATE_0049)
        )


def downgrade() -> None:
    # Descer descarta e-mails ainda não enviados. É aceitável só porque o
    # rollback combinado é "parar o worker" — as linhas ficam pendentes — e o
    # downgrade fica para depois de ter certeza de que nada precisa sair.
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0049)
            .replace("PARA", _READY_ATE_0048)
        )
    op.execute(_REESCREVE.replace("ALVO", _APPLY).replace("DE", _INSERCAO).replace("PARA", _ANCORA))
    for assinatura in reversed(_ASSINATURAS):
        op.execute(f"drop function if exists {assinatura}")
    op.execute("drop table if exists app_private.email_outbox")
    op.execute("drop table if exists app_private.email_worker_identity")
