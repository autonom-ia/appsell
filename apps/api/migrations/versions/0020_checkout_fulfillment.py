"""Checkout fulfillment: buyer-aware webhook ingress v2 and entitlement worker queue.

The 0012 ingress persists only SHA-256 digests of the external references and no
buyer identity, so nothing consuming ``webhook_event`` can grant access. This
migration adds the missing fulfillment substrate:

* ``app_private.fulfillment_job`` — durable queue row created when a signed
  webhook carries the buyer (payload v2). Stores the buyer e-mail in the
  private schema (revoked from the runtime role) plus the digests already
  recorded on the event; product resolution reuses ``external_product_mapping``
  by digest, so no plaintext external reference is ever persisted. On grant the
  e-mail also lands in ``public.identity_user.email_normalized`` — the same
  place every buyer's e-mail lives (pre-existing), protected by FORCE RLS and
  self-read only; it is not confined to ``app_private`` beyond that.
* ``app_private.fulfillment_worker_identity`` — allowlist for the production
  fulfillment worker (asset/demo worker pattern), self-registered via
  ``ensure_fulfillment_worker`` under the worker capability; revoking the row
  stops the worker without key rotation.
* ``accept_checkout_webhook`` gains two trailing parameters
  (``p_buyer_email``, ``p_buyer_identity_sub``). Payload v1 (no buyer) keeps
  the exact previous behaviour: event + outbox only, no job.
* ``app_private.entitlement_grant`` — the per-purchase grant ledger (root-cause
  ownership model). One row per ``(seller, integration, purchase)`` with status
  ``active``/``refunded``. A ``buyer_entitlement`` is active iff at least one
  ``active`` grant exists for its ``(buyer, product)``. This removes every
  ownership race a single ``reference_key`` had: concurrent unrefunded
  purchases each hold an active grant, so refunding one keeps access while
  another justifies it; a refund of a superseded/old purchase only flips that
  purchase's grant.
* ``claim_fulfillment_jobs`` / ``apply_checkout_fulfillment`` /
  ``fail_fulfillment_job`` — lease/fence worker protocol cloned from the demo
  checkout outbox. Under one advisory lock per ``(seller, buyer, product)``,
  ``apply`` resolves the product by digest mapping, resolves or creates the
  canonical buyer ``identity_user`` (``app_private.identity_user_id`` — the same
  uuid5 the Cognito login yields), then drives the ledger:

  - ``purchase.approved`` upserts an ``active`` grant (a grant already
    ``refunded`` stays refunded — refund wins, even out-of-order), then the
    ``buyer_entitlement`` is (re)activated iff an active grant remains;
  - ``purchase.refunded``/``subscription.cancelled`` upsert a ``refunded``
    grant (creating it if the refund arrives first) and revoke the entitlement
    only when no active grant remains; idempotent when nothing changes.

  ``buyer_entitlement.reference_key`` is a stable ``billing:<buyer>:<product>``
  (one row per pair) — the ledger, not the reference, decides access.

Emitter responsibilities (trust boundary — this schema does NOT re-verify):
``buyer_identity_sub`` may only be populated from a value the emitter itself
validated against the authenticated buyer at the moment of the verified payment
call — never echoed from client-supplied checkout parameters. The
``registration:`` namespace is reserved for the worker-derived e-mail identity
and rejected in explicit subs. Flood protection for ``fulfillment_job`` is
delegated to the emitter's own rate limiting; replay cannot duplicate jobs
(unique per webhook event) but job creation rate is otherwise unbounded here.

Downgrade is refused while fulfillment jobs exist (queue data must be
preserved) or a revoked worker exists (revocation must survive); otherwise it
restores the 0012 ``accept_checkout_webhook`` and the 0019 ``schema_ready``
verbatim and drops the fulfillment objects.
"""

from alembic import op

revision = "0020_checkout_fulfillment"
down_revision = "0019_offer_status"
branch_labels = None
depends_on = None

_ACCEPT_V1_SIGNATURE = (
    "app_private.accept_checkout_webhook("
    "uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,"
    "text,smallint,timestamptz,bigint,uuid)"
)
_ACCEPT_V2_SIGNATURE = (
    "app_private.accept_checkout_webhook("
    "uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,"
    "text,smallint,timestamptz,bigint,uuid,text,text)"
)


def upgrade() -> None:
    statements = [
        """
        create table app_private.fulfillment_worker_identity (
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
        create table app_private.fulfillment_job (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          webhook_event_id uuid not null,
          event_type text not null check (
            event_type in (
              'purchase.approved','purchase.refunded','subscription.cancelled'
            )
          ),
          external_product_id_sha256 text not null check (
            external_product_id_sha256 ~ '^[0-9a-f]{64}$'
          ),
          external_purchase_id_sha256 text not null check (
            external_purchase_id_sha256 ~ '^[0-9a-f]{64}$'
          ),
          buyer_email_normalized text not null check (
            buyer_email_normalized = lower(btrim(buyer_email_normalized))
            and position('@' in buyer_email_normalized) > 1
            and octet_length(buyer_email_normalized) between 6 and 242
            and buyer_email_normalized !~ '[[:cntrl:][:space:]]'
          ),
          buyer_identity_sub text check (
            buyer_identity_sub is null or (
              buyer_identity_sub = btrim(buyer_identity_sub)
              and length(buyer_identity_sub) between 3 and 255
              and buyer_identity_sub !~ '[[:cntrl:]]'
              and buyer_identity_sub not like 'registration:%'
            )
          ),
          status text not null default 'pending' check (
            status in ('pending','retry','leased','completed','dead_letter')
          ),
          attempt_count integer not null default 0 check (attempt_count between 0 and 32),
          lease_owner text check (
            lease_owner is null or lease_owner ~ '^[a-z0-9][a-z0-9._-]{2,63}$'
          ),
          lease_fence bigint not null default 0,
          lease_expires_at timestamptz,
          error_code text,
          available_at timestamptz not null default clock_timestamp(),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,id),
          constraint fulfillment_job_event_key unique (seller_organization_id,webhook_event_id),
          foreign key (seller_organization_id,webhook_event_id)
            references public.webhook_event(seller_organization_id,id)
        )
        """,
        """
        create index fulfillment_job_pending_idx
          on app_private.fulfillment_job(available_at,created_at,id)
          where status in ('pending','retry')
        """,
        """
        create index fulfillment_job_purchase_idx
          on app_private.fulfillment_job(
            seller_organization_id,external_purchase_id_sha256
          )
        """,
        """
        create table app_private.entitlement_grant (
          seller_organization_id uuid not null,
          checkout_integration_id uuid not null,
          external_purchase_id_sha256 text not null check (
            external_purchase_id_sha256 ~ '^[0-9a-f]{64}$'
          ),
          buyer_identity_user_id uuid not null references public.identity_user(id),
          catalog_product_id uuid not null,
          status text not null check (status in ('active','refunded')),
          occurred_at timestamptz not null,
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (
            seller_organization_id,checkout_integration_id,external_purchase_id_sha256
          ),
          foreign key (seller_organization_id,catalog_product_id)
            references public.catalog_product(seller_organization_id,id)
        )
        """,
        """
        create index entitlement_grant_active_idx
          on app_private.entitlement_grant(
            seller_organization_id,buyer_identity_user_id,catalog_product_id
          ) where status='active'
        """,
        """
        create function app_private.ensure_fulfillment_worker(
          p_worker uuid,p_issuer text,p_subject text,p_email text,p_label text
        ) returns void language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if app_private.context_text('capability')
               is distinct from 'commerce.fulfillment.worker'
             or app_private.context_uuid('identity_user_id') is distinct from p_worker
             or app_private.context_uuid('seller_organization_id') is not null
             or p_worker is null
             or p_issuer is null or length(p_issuer) not between 3 and 255
             or p_subject is null or length(p_subject) not between 3 and 255
             or p_email is null or p_email <> lower(btrim(p_email))
             or position('@' in p_email) <= 1
             or p_label is null or p_label !~ '^[a-z0-9][a-z0-9._-]{2,63}$' then
            raise exception 'fulfillment worker registration is not authorized'
              using errcode='28000';
          end if;
          insert into public.identity_user(
            id,identity_issuer,identity_sub,email_normalized,email_verified
          ) values (p_worker,p_issuer,p_subject,p_email,true)
          on conflict (identity_issuer,identity_sub) do update set
            email_normalized=excluded.email_normalized,email_verified=true,
            updated_at=clock_timestamp()
          where identity_user.id=excluded.id;
          insert into app_private.fulfillment_worker_identity(
            identity_user_id,status,label
          ) values (p_worker,'active',p_label)
          on conflict (identity_user_id) do update set
            label=excluded.label,updated_at=clock_timestamp();
        end $$
        """,
        f"drop function {_ACCEPT_V1_SIGNATURE}",
        """
        create function app_private.accept_checkout_webhook(
          p_seller_organization_id uuid,p_endpoint_id uuid,
          p_endpoint_key_sha256 text,p_secret_reference text,p_endpoint_revision bigint,
          p_external_event_id_sha256 text,p_external_event_id_octets smallint,
          p_payload_sha256 text,p_payload_octets integer,p_event_type text,
          p_external_product_id_sha256 text,p_external_product_id_octets smallint,
          p_external_purchase_id_sha256 text,p_external_purchase_id_octets smallint,
          p_occurred_at timestamptz,p_signed_at_epoch bigint,p_request_id uuid,
          p_buyer_email text,p_buyer_identity_sub text
        ) returns table(webhook_event_id uuid,outbox_event_id uuid,created boolean)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare
          endpoint record;
          previous public.webhook_event%rowtype;
          created_event_id uuid;
          created_outbox_id uuid;
          now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.webhook.ingest'
            or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'webhook context is not authorized' using errcode='28000';
          end if;
          if p_seller_organization_id is null or p_endpoint_id is null
            or p_endpoint_key_sha256 is null
            or p_endpoint_key_sha256 !~ '^[0-9a-f]{64}$'
            or p_secret_reference is null
            or p_secret_reference !~ '^[a-z0-9][a-z0-9._-]{2,63}$'
            or p_endpoint_revision is null
            or p_external_event_id_sha256 is null
            or p_external_event_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_event_id_octets is null
            or p_external_event_id_octets not between 1 and 128
            or p_payload_sha256 is null or p_payload_sha256 !~ '^[0-9a-f]{64}$'
            or p_payload_octets is null or p_payload_octets not between 0 and 65536
            or p_event_type is null or p_event_type not in (
              'purchase.approved','purchase.refunded','subscription.cancelled'
            )
            or p_external_product_id_sha256 is null
            or p_external_product_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_product_id_octets is null
            or p_external_product_id_octets not between 1 and 255
            or p_external_purchase_id_sha256 is null
            or p_external_purchase_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_purchase_id_octets is null
            or p_external_purchase_id_octets not between 1 and 255
            or p_occurred_at is null or p_signed_at_epoch is null
            or p_request_id is null
            or (p_buyer_email is not null and (
              p_buyer_email <> lower(btrim(p_buyer_email))
              or position('@' in p_buyer_email) <= 1
              or octet_length(p_buyer_email) not between 6 and 242
              or p_buyer_email ~ '[[:cntrl:][:space:]]'
            ))
            or (p_buyer_identity_sub is not null and (
              p_buyer_email is null
              or p_buyer_identity_sub <> btrim(p_buyer_identity_sub)
              or length(p_buyer_identity_sub) not between 3 and 255
              or p_buyer_identity_sub ~ '[[:cntrl:]]'
              -- 'registration:' e namespace derivado do e-mail: reservado ao
              -- worker; um emissor nao pode se ligar a identidade derivada de
              -- OUTRO e-mail.
              or p_buyer_identity_sub like 'registration:%'
            )) then
            raise exception 'invalid webhook event' using errcode='22023';
          end if;

          select endpoint_row.seller_organization_id,endpoint_row.id,
            endpoint_row.checkout_integration_id,endpoint_row.revision,
            endpoint_row.max_clock_skew_seconds into endpoint
          from public.webhook_endpoint endpoint_row
          join public.checkout_integration integration
            on integration.seller_organization_id=endpoint_row.seller_organization_id
           and integration.id=endpoint_row.checkout_integration_id
          join public.seller_organization seller
            on seller.id=endpoint_row.seller_organization_id
          where endpoint_row.seller_organization_id=p_seller_organization_id
            and endpoint_row.id=p_endpoint_id
            and endpoint_row.endpoint_key_sha256=p_endpoint_key_sha256
            and endpoint_row.secret_reference=p_secret_reference
            and endpoint_row.status='active'
            and endpoint_row.revision=p_endpoint_revision
            and integration.status='active' and seller.status='active'
          for share of endpoint_row,integration,seller;
          if not found or abs(now_epoch-p_signed_at_epoch)>endpoint.max_clock_skew_seconds then
            raise exception 'webhook endpoint is unavailable' using errcode='28000';
          end if;

          perform pg_advisory_xact_lock(hashtextextended(
            endpoint.seller_organization_id::text || ':' || endpoint.id::text ||
              ':' || p_external_event_id_sha256,0
          ));
          select * into previous from public.webhook_event event
          where event.seller_organization_id=endpoint.seller_organization_id
            and event.webhook_endpoint_id=endpoint.id
            and event.external_event_id_sha256=p_external_event_id_sha256;
          if found then
            if previous.payload_sha256<>p_payload_sha256 then
              raise exception 'webhook event id conflict' using errcode='23505';
            end if;
            select outbox.id into strict created_outbox_id
            from app_private.outbox_event outbox
            where outbox.seller_organization_id=previous.seller_organization_id
              and outbox.aggregate_type='webhook_event'
              and outbox.aggregate_id=previous.id
              and outbox.event_type='commerce.webhook.received';
            if p_buyer_email is not null then
              insert into app_private.fulfillment_job(
                seller_organization_id,webhook_event_id,event_type,
                external_product_id_sha256,external_purchase_id_sha256,
                buyer_email_normalized,buyer_identity_sub
              ) values (
                previous.seller_organization_id,previous.id,previous.event_type,
                previous.external_product_id_sha256,
                previous.external_purchase_id_sha256,
                p_buyer_email,p_buyer_identity_sub
              ) on conflict on constraint fulfillment_job_event_key do nothing;
            end if;
            return query select previous.id,created_outbox_id,false;
            return;
          end if;

          insert into public.webhook_event(
            seller_organization_id,checkout_integration_id,webhook_endpoint_id,
            external_event_id_sha256,external_event_id_octets,payload_sha256,
            payload_octets,event_type,external_product_id_sha256,
            external_product_id_octets,external_purchase_id_sha256,
            external_purchase_id_octets,occurred_at,signed_at
          ) values(
            endpoint.seller_organization_id,endpoint.checkout_integration_id,endpoint.id,
            p_external_event_id_sha256,p_external_event_id_octets,p_payload_sha256,
            p_payload_octets,p_event_type,p_external_product_id_sha256,
            p_external_product_id_octets,p_external_purchase_id_sha256,
            p_external_purchase_id_octets,p_occurred_at,to_timestamp(p_signed_at_epoch)
          ) returning id into created_event_id;
          insert into app_private.outbox_event(
            seller_organization_id,aggregate_type,aggregate_id,event_type,
            idempotency_key,payload
          ) values(
            endpoint.seller_organization_id,'webhook_event',created_event_id,
            'commerce.webhook.received','webhook:' || created_event_id::text,
            jsonb_build_object(
              'schema_version',1,'webhook_event_id',created_event_id,
              'seller_organization_id',endpoint.seller_organization_id,
              'checkout_integration_id',endpoint.checkout_integration_id,
              'webhook_endpoint_id',endpoint.id,'event_type',p_event_type,
              'payload_sha256',p_payload_sha256
            )
          ) returning id into created_outbox_id;
          if p_buyer_email is not null then
            insert into app_private.fulfillment_job(
              seller_organization_id,webhook_event_id,event_type,
              external_product_id_sha256,external_purchase_id_sha256,
              buyer_email_normalized,buyer_identity_sub
            ) values (
              endpoint.seller_organization_id,created_event_id,p_event_type,
              p_external_product_id_sha256,p_external_purchase_id_sha256,
              p_buyer_email,p_buyer_identity_sub
            );
          end if;
          insert into public.audit_event(
            seller_organization_id,actor_identity_user_id,action,target_type,
            target_id,result,request_id,metadata
          ) values(
            endpoint.seller_organization_id,null,'commerce.webhook_received',
            'webhook_event',created_event_id,'succeeded',p_request_id,
            jsonb_build_object(
              'adapter','generic_hmac_v1','webhook_endpoint_id',endpoint.id,
              'external_event_id_sha256',p_external_event_id_sha256,
              'payload_sha256',p_payload_sha256,
              'fulfillment_job',p_buyer_email is not null
            )
          );
          return query select created_event_id,created_outbox_id,true;
        end $$
        """,
        """
        create function app_private.claim_fulfillment_jobs(
          p_worker uuid,p_label text,p_limit integer,p_lease_seconds integer
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare jobs jsonb;
        begin
          if app_private.context_text('capability')
               is distinct from 'commerce.fulfillment.worker'
             or app_private.context_uuid('identity_user_id') is distinct from p_worker
             or app_private.context_uuid('seller_organization_id') is not null
             or p_label is null or p_label!~'^[a-z0-9][a-z0-9._-]{2,63}$'
             or p_limit not between 1 and 8 or p_lease_seconds not between 5 and 300
             or not exists(select 1 from app_private.fulfillment_worker_identity worker
               where worker.identity_user_id=p_worker and worker.status='active') then
            raise exception 'fulfillment worker is not authorized' using errcode='28000';
          end if;
          update app_private.fulfillment_job queued set status='dead_letter',
            lease_owner=null,lease_expires_at=null,error_code='apply_unexpected',
            updated_at=clock_timestamp()
          where queued.status='leased' and queued.lease_expires_at<=clock_timestamp()
            and queued.attempt_count>=10;
          with candidates as (
            select queued.seller_organization_id,queued.id
            from app_private.fulfillment_job queued
            where queued.attempt_count<10 and (
              (queued.status in ('pending','retry') and queued.available_at<=clock_timestamp())
              or (queued.status='leased' and queued.lease_expires_at<=clock_timestamp())
            )
            order by queued.available_at,queued.created_at,queued.id
            for update skip locked limit p_limit
          ), leased as (
            update app_private.fulfillment_job queued set status='leased',
              attempt_count=queued.attempt_count+1,lease_owner=p_label,
              lease_fence=queued.lease_fence+1,
              lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
              error_code=null,updated_at=clock_timestamp()
            from candidates where queued.seller_organization_id=candidates.seller_organization_id
              and queued.id=candidates.id
            returning queued.seller_organization_id,queued.id,queued.webhook_event_id,
              queued.event_type,queued.lease_fence,queued.attempt_count
          ) select coalesce(jsonb_agg(to_jsonb(leased) order by leased.id),'[]'::jsonb)
            into jobs from leased;
          return jobs;
        end $$
        """,
        """
        create function app_private.apply_checkout_fulfillment(
          p_worker uuid,p_label text,p_seller uuid,p_job uuid,p_fence bigint,
          p_request uuid,p_issuer text
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare
          job record; event record; mapped_product_id uuid; product record;
          buyer_id uuid; buyer_sub text; v_entitlement_id uuid;
          reference text; has_active_grant boolean;
        begin
          if app_private.context_text('capability')
               is distinct from 'commerce.fulfillment.worker'
             or app_private.context_uuid('identity_user_id') is distinct from p_worker
             or app_private.context_uuid('seller_organization_id') is distinct from p_seller
             or p_label is null or p_job is null or p_fence is null or p_request is null
             or p_issuer is null or length(p_issuer) not between 3 and 255
             or not exists(select 1 from app_private.fulfillment_worker_identity worker
               where worker.identity_user_id=p_worker and worker.status='active') then
            raise exception 'fulfillment apply is not authorized' using errcode='28000';
          end if;
          select queued.* into job from app_private.fulfillment_job queued
          where queued.seller_organization_id=p_seller and queued.id=p_job
            and queued.status='leased' and queued.lease_owner=p_label
            and queued.lease_fence=p_fence and queued.lease_expires_at>clock_timestamp()
          for update;
          if not found then
            raise exception 'fulfillment lease is unavailable' using errcode='40001';
          end if;
          select * into strict event from public.webhook_event stored
          where stored.seller_organization_id=p_seller and stored.id=job.webhook_event_id;

          select mapping.catalog_product_id into mapped_product_id
          from public.external_product_mapping mapping
          where mapping.seller_organization_id=p_seller
            and mapping.checkout_integration_id=event.checkout_integration_id
            and mapping.external_product_id_sha256=job.external_product_id_sha256;
          if mapped_product_id is null then
            update app_private.fulfillment_job set status='dead_letter',
              lease_owner=null,lease_expires_at=null,error_code='product_mapping_missing',
              updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_job;
            insert into public.audit_event(seller_organization_id,actor_identity_user_id,
              action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_worker,'commerce.fulfillment_dead_letter','fulfillment_job',
              p_job,'failed',p_request,jsonb_build_object(
                'error_code','product_mapping_missing',
                'webhook_event_id',job.webhook_event_id));
            return jsonb_build_object('status','dead_letter',
              'error_code','product_mapping_missing');
          end if;

          -- Identidade canônica do comprador: mesmo uuid5 que o login Cognito
          -- produz (app_private.identity_user_id), senão o entitlement ficaria
          -- ligado a uma identidade inalcançavel. Determinístico: serve de chave
          -- de serialização mesmo antes de a linha existir.
          buyer_sub:=coalesce(job.buyer_identity_sub,
            'registration:'||job.buyer_email_normalized);
          buyer_id:=app_private.identity_user_id(p_issuer,buyer_sub);

          -- Serializa approval e refund do MESMO (seller,buyer,produto) em uma
          -- unica secao critica: a maquina de estados nao pode interlear (a
          -- fence protege so a posse do job).
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.fulfillment:'||p_seller::text||':'||buyer_id::text||':'||
              mapped_product_id::text,0));

          -- Ambos os ramos precisam da identidade (FK do grant). O grant so vira
          -- 'active' se ainda nao estiver 'refunded' (refund vence approval).
          insert into public.identity_user(
            id,identity_issuer,identity_sub,email_normalized,email_verified
          ) values (buyer_id,p_issuer,buyer_sub,job.buyer_email_normalized,false)
          on conflict on constraint identity_user_issuer_sub_key do update set
            updated_at=clock_timestamp()
          where public.identity_user.id=excluded.id
          returning id into buyer_id;
          if buyer_id is null then
            raise exception 'buyer identity binding conflict' using errcode='40001';
          end if;

          -- reference_key ESTAVEL por (buyer,produto): a entitlement e uma linha
          -- unica por par; quem determina acesso e o ledger de grants por compra,
          -- nao a reference_key. Isso encerra as races de ownership entre compras.
          reference:='billing:'||buyer_id::text||':'||mapped_product_id::text;

          if job.event_type='purchase.approved' then
            select catalog.id,catalog.published_snapshot_id into product
            from public.catalog_product catalog
            where catalog.seller_organization_id=p_seller and catalog.id=mapped_product_id
            for share;
            if not found or product.published_snapshot_id is null then
              update app_private.fulfillment_job set
                status=case when attempt_count>=10 then 'dead_letter' else 'retry' end,
                available_at=clock_timestamp()+make_interval(secs=>60),
                lease_owner=null,lease_expires_at=null,error_code='product_not_published',
                updated_at=clock_timestamp()
              where seller_organization_id=p_seller and id=p_job;
              insert into public.audit_event(seller_organization_id,actor_identity_user_id,
                action,target_type,target_id,result,request_id,metadata)
              values(p_seller,p_worker,'commerce.fulfillment_retry','fulfillment_job',
                p_job,'failed',p_request,jsonb_build_object(
                  'error_code','product_not_published',
                  'catalog_product_id',mapped_product_id));
              return jsonb_build_object('status','retry',
                'error_code','product_not_published');
            end if;

            insert into app_private.entitlement_grant(
              seller_organization_id,checkout_integration_id,external_purchase_id_sha256,
              buyer_identity_user_id,catalog_product_id,status,occurred_at
            ) values (
              p_seller,event.checkout_integration_id,job.external_purchase_id_sha256,
              buyer_id,mapped_product_id,'active',event.occurred_at
            )
            on conflict (seller_organization_id,checkout_integration_id,
              external_purchase_id_sha256) do update set
              status=case when app_private.entitlement_grant.status='refunded'
                then 'refunded' else 'active' end,
              occurred_at=greatest(app_private.entitlement_grant.occurred_at,
                excluded.occurred_at),
              updated_at=clock_timestamp();

            has_active_grant:=exists(
              select 1 from app_private.entitlement_grant ledger
              where ledger.seller_organization_id=p_seller
                and ledger.buyer_identity_user_id=buyer_id
                and ledger.catalog_product_id=mapped_product_id
                and ledger.status='active');
            if not has_active_grant then
              -- Esta compra ja fora reembolsada (out-of-order) e nenhuma outra
              -- justifica acesso: nao conceder.
              update app_private.fulfillment_job set status='completed',
                lease_owner=null,lease_expires_at=null,error_code=null,
                updated_at=clock_timestamp()
              where seller_organization_id=p_seller and id=p_job;
              insert into public.audit_event(seller_organization_id,actor_identity_user_id,
                action,target_type,target_id,result,request_id,metadata)
              values(p_seller,p_worker,'commerce.fulfillment_skipped','fulfillment_job',
                p_job,'succeeded',p_request,jsonb_build_object(
                  'reason','purchase_refunded',
                  'external_purchase_id_sha256',job.external_purchase_id_sha256));
              return jsonb_build_object('status','completed','applied',false,
                'reason','purchase_refunded');
            end if;

            insert into public.buyer_entitlement(
              seller_organization_id,id,buyer_identity_user_id,catalog_product_id,
              publication_id,reference_key,source,status,progress_percent
            ) values (
              p_seller,gen_random_uuid(),buyer_id,mapped_product_id,
              product.published_snapshot_id,reference,'billing','active',0
            )
            on conflict on constraint buyer_entitlement_subject_product_key do update set
              status='active',publication_id=excluded.publication_id,
              starts_at=case when public.buyer_entitlement.status='active'
                then public.buyer_entitlement.starts_at else clock_timestamp() end,
              expires_at=null,updated_at=clock_timestamp()
            returning id into v_entitlement_id;
            update app_private.fulfillment_job set status='completed',
              lease_owner=null,lease_expires_at=null,error_code=null,
              updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_job;
            insert into public.audit_event(seller_organization_id,actor_identity_user_id,
              action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_worker,'commerce.fulfillment_entitlement_granted',
              'buyer_entitlement',v_entitlement_id,'succeeded',p_request,
              jsonb_build_object('webhook_event_id',job.webhook_event_id,
                'catalog_product_id',mapped_product_id,
                'buyer_identity_user_id',buyer_id,
                'external_purchase_id_sha256',job.external_purchase_id_sha256));
            return jsonb_build_object('status','completed','applied',true,
              'entitlement_id',v_entitlement_id,'buyer_identity_user_id',buyer_id);
          end if;

          -- purchase.refunded / subscription.cancelled: marca o grant desta
          -- compra como reembolsado (refund vence, mesmo out-of-order). Revoga a
          -- entitlement so quando NENHUM grant ativo restar para (buyer,produto).
          insert into app_private.entitlement_grant(
            seller_organization_id,checkout_integration_id,external_purchase_id_sha256,
            buyer_identity_user_id,catalog_product_id,status,occurred_at
          ) values (
            p_seller,event.checkout_integration_id,job.external_purchase_id_sha256,
            buyer_id,mapped_product_id,'refunded',event.occurred_at
          )
          on conflict (seller_organization_id,checkout_integration_id,
            external_purchase_id_sha256) do update set
            status='refunded',updated_at=clock_timestamp();

          has_active_grant:=exists(
            select 1 from app_private.entitlement_grant ledger
            where ledger.seller_organization_id=p_seller
              and ledger.buyer_identity_user_id=buyer_id
              and ledger.catalog_product_id=mapped_product_id
              and ledger.status='active');
          if not has_active_grant then
            update public.buyer_entitlement set status='revoked',
              updated_at=clock_timestamp()
            where seller_organization_id=p_seller
              and buyer_identity_user_id=buyer_id
              and catalog_product_id=mapped_product_id
              and status='active'
            returning id into v_entitlement_id;
            if v_entitlement_id is not null then
              update app_private.fulfillment_job set status='completed',
                lease_owner=null,lease_expires_at=null,error_code=null,
                updated_at=clock_timestamp()
              where seller_organization_id=p_seller and id=p_job;
              insert into public.audit_event(seller_organization_id,actor_identity_user_id,
                action,target_type,target_id,result,request_id,metadata)
              values(p_seller,p_worker,'commerce.fulfillment_entitlement_revoked',
                'buyer_entitlement',v_entitlement_id,'succeeded',p_request,
                jsonb_build_object('webhook_event_id',job.webhook_event_id,
                  'event_type',job.event_type,
                  'external_purchase_id_sha256',job.external_purchase_id_sha256));
              return jsonb_build_object('status','completed','applied',true,
                'entitlement_id',v_entitlement_id,'revoked',true);
            end if;
          end if;
          update app_private.fulfillment_job set status='completed',
            lease_owner=null,lease_expires_at=null,error_code=null,
            updated_at=clock_timestamp()
          where seller_organization_id=p_seller and id=p_job;
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,
            action,target_type,target_id,result,request_id,metadata)
          values(p_seller,p_worker,'commerce.fulfillment_skipped','fulfillment_job',
            p_job,'succeeded',p_request,jsonb_build_object(
              'reason','nothing_to_revoke','event_type',job.event_type,
              'external_purchase_id_sha256',job.external_purchase_id_sha256));
          return jsonb_build_object('status','completed','applied',false,
            'reason','nothing_to_revoke');
        end $$
        """,
        """
        create function app_private.fail_fulfillment_job(
          p_worker uuid,p_label text,p_seller uuid,p_job uuid,p_fence bigint,
          p_error text,p_delay_seconds integer
        ) returns void language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        begin
          if app_private.context_text('capability')
               is distinct from 'commerce.fulfillment.worker'
             or app_private.context_uuid('identity_user_id') is distinct from p_worker
             or app_private.context_uuid('seller_organization_id') is distinct from p_seller
             or p_error not in ('apply_retryable','apply_unexpected')
             or p_delay_seconds not between 1 and 300
             or not exists(select 1 from app_private.fulfillment_worker_identity worker
               where worker.identity_user_id=p_worker and worker.status='active') then
            raise exception 'fulfillment failure record is not authorized'
              using errcode='28000';
          end if;
          update app_private.fulfillment_job queued set
            status=case when queued.attempt_count>=10 then 'dead_letter' else 'retry' end,
            available_at=clock_timestamp()+make_interval(secs=>p_delay_seconds),
            lease_owner=null,lease_expires_at=null,error_code=p_error,
            updated_at=clock_timestamp()
          where queued.seller_organization_id=p_seller and queued.id=p_job
            and queued.status='leased' and queued.lease_owner=p_label
            and queued.lease_fence=p_fence and queued.lease_expires_at>clock_timestamp();
          if not found then
            raise exception 'fulfillment lease is unavailable' using errcode='40001';
          end if;
        end $$
        """,
        """
        create function app_private.accept_checkout_webhook(
          p_seller_organization_id uuid,p_endpoint_id uuid,
          p_endpoint_key_sha256 text,p_secret_reference text,p_endpoint_revision bigint,
          p_external_event_id_sha256 text,p_external_event_id_octets smallint,
          p_payload_sha256 text,p_payload_octets integer,p_event_type text,
          p_external_product_id_sha256 text,p_external_product_id_octets smallint,
          p_external_purchase_id_sha256 text,p_external_purchase_id_octets smallint,
          p_occurred_at timestamptz,p_signed_at_epoch bigint,p_request_id uuid
        ) returns table(webhook_event_id uuid,outbox_event_id uuid,created boolean)
        language sql security definer
        set search_path=pg_catalog,app_private,public as $$
          -- Payload-v1 compatibility wrapper: same contract as before 0020
          -- (event + outbox, no fulfillment job). Keeps schema_ready_base_0016
          -- and any legacy caller intact.
          select * from app_private.accept_checkout_webhook(
            $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
            null::text,null::text
          )
        $$
        """,
        "revoke all on table app_private.fulfillment_job,app_private.fulfillment_worker_identity,app_private.entitlement_grant from public,appsell_runtime",
        f"revoke all on function {_ACCEPT_V1_SIGNATURE} from public",
        f"grant execute on function {_ACCEPT_V1_SIGNATURE} to appsell_runtime",
        "revoke all on function app_private.ensure_fulfillment_worker(uuid,text,text,text,text) from public",
        f"revoke all on function {_ACCEPT_V2_SIGNATURE} from public",
        "revoke all on function app_private.claim_fulfillment_jobs(uuid,text,integer,integer) from public",
        "revoke all on function app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text) from public",
        "revoke all on function app_private.fail_fulfillment_job(uuid,text,uuid,uuid,bigint,text,integer) from public",
        "grant execute on function app_private.ensure_fulfillment_worker(uuid,text,text,text,text) to appsell_runtime",
        f"grant execute on function {_ACCEPT_V2_SIGNATURE} to appsell_runtime",
        "grant execute on function app_private.claim_fulfillment_jobs(uuid,text,integer,integer) to appsell_runtime",
        "grant execute on function app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text) to appsell_runtime",
        "grant execute on function app_private.fail_fulfillment_job(uuid,text,uuid,uuid,bigint,text,integer) to appsell_runtime",
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0016 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select app_private.schema_ready_base_0017()
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='unit_amount_minor' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='currency' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='billing_period' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_constraint
              where conrelid='public.offer'::regclass
                and conname='offer_status_check' and convalidated)
            and to_regprocedure(
              'app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)'
            ) is not null
            and to_regclass('app_private.fulfillment_job') is not null
            and to_regclass('app_private.fulfillment_worker_identity') is not null
            and to_regclass('app_private.entitlement_grant') is not null
            and to_regprocedure(
              'app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamptz,bigint,uuid,text,text)'
            ) is not null
            and to_regprocedure(
              'app_private.claim_fulfillment_jobs(uuid,text,integer,integer)'
            ) is not null
            and to_regprocedure(
              'app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text)'
            ) is not null
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """
        do $migration$
        begin
          if exists (select 1 from app_private.fulfillment_job) then
            raise exception
              'cannot downgrade 0020_checkout_fulfillment: fulfillment jobs exist; queue data must be preserved'
              using errcode = '55000';
          end if;
          -- Uma revogacao de worker e sinal de seguranca: derrubar a tabela e
          -- deixar o auto-registro recriar o worker como ativo apagaria a
          -- revogacao. Recusa o downgrade enquanto houver worker revogado.
          if exists (
            select 1 from app_private.fulfillment_worker_identity
            where status = 'revoked'
          ) then
            raise exception
              'cannot downgrade 0020_checkout_fulfillment: a revoked fulfillment worker exists; revocation must be preserved'
              using errcode = '55000';
          end if;
        end $migration$
        """,
        "drop function app_private.fail_fulfillment_job(uuid,text,uuid,uuid,bigint,text,integer)",
        "drop function app_private.apply_checkout_fulfillment(uuid,text,uuid,uuid,bigint,uuid,text)",
        "drop function app_private.claim_fulfillment_jobs(uuid,text,integer,integer)",
        f"drop function if exists {_ACCEPT_V1_SIGNATURE}",
        f"drop function {_ACCEPT_V2_SIGNATURE}",
        "drop function app_private.ensure_fulfillment_worker(uuid,text,text,text,text)",
        "drop table app_private.entitlement_grant",
        "drop table app_private.fulfillment_job",
        "drop table app_private.fulfillment_worker_identity",
        """
        create function app_private.accept_checkout_webhook(
          p_seller_organization_id uuid,p_endpoint_id uuid,
          p_endpoint_key_sha256 text,p_secret_reference text,p_endpoint_revision bigint,
          p_external_event_id_sha256 text,p_external_event_id_octets smallint,
          p_payload_sha256 text,p_payload_octets integer,p_event_type text,
          p_external_product_id_sha256 text,p_external_product_id_octets smallint,
          p_external_purchase_id_sha256 text,p_external_purchase_id_octets smallint,
          p_occurred_at timestamptz,p_signed_at_epoch bigint,p_request_id uuid
        ) returns table(webhook_event_id uuid,outbox_event_id uuid,created boolean)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare
          endpoint record;
          previous public.webhook_event%rowtype;
          created_event_id uuid;
          created_outbox_id uuid;
          now_epoch bigint := extract(epoch from clock_timestamp())::bigint;
        begin
          if app_private.context_text('capability') is distinct from 'commerce.webhook.ingest'
            or app_private.context_uuid('seller_organization_id') is not null then
            raise exception 'webhook context is not authorized' using errcode='28000';
          end if;
          if p_seller_organization_id is null or p_endpoint_id is null
            or p_endpoint_key_sha256 is null
            or p_endpoint_key_sha256 !~ '^[0-9a-f]{64}$'
            or p_secret_reference is null
            or p_secret_reference !~ '^[a-z0-9][a-z0-9._-]{2,63}$'
            or p_endpoint_revision is null
            or p_external_event_id_sha256 is null
            or p_external_event_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_event_id_octets is null
            or p_external_event_id_octets not between 1 and 128
            or p_payload_sha256 is null or p_payload_sha256 !~ '^[0-9a-f]{64}$'
            or p_payload_octets is null or p_payload_octets not between 0 and 65536
            or p_event_type is null or p_event_type not in (
              'purchase.approved','purchase.refunded','subscription.cancelled'
            )
            or p_external_product_id_sha256 is null
            or p_external_product_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_product_id_octets is null
            or p_external_product_id_octets not between 1 and 255
            or p_external_purchase_id_sha256 is null
            or p_external_purchase_id_sha256 !~ '^[0-9a-f]{64}$'
            or p_external_purchase_id_octets is null
            or p_external_purchase_id_octets not between 1 and 255
            or p_occurred_at is null or p_signed_at_epoch is null
            or p_request_id is null then
            raise exception 'invalid webhook event' using errcode='22023';
          end if;

          select endpoint_row.seller_organization_id,endpoint_row.id,
            endpoint_row.checkout_integration_id,endpoint_row.revision,
            endpoint_row.max_clock_skew_seconds into endpoint
          from public.webhook_endpoint endpoint_row
          join public.checkout_integration integration
            on integration.seller_organization_id=endpoint_row.seller_organization_id
           and integration.id=endpoint_row.checkout_integration_id
          join public.seller_organization seller
            on seller.id=endpoint_row.seller_organization_id
          where endpoint_row.seller_organization_id=p_seller_organization_id
            and endpoint_row.id=p_endpoint_id
            and endpoint_row.endpoint_key_sha256=p_endpoint_key_sha256
            and endpoint_row.secret_reference=p_secret_reference
            and endpoint_row.status='active'
            and endpoint_row.revision=p_endpoint_revision
            and integration.status='active' and seller.status='active'
          for share of endpoint_row,integration,seller;
          if not found or abs(now_epoch-p_signed_at_epoch)>endpoint.max_clock_skew_seconds then
            raise exception 'webhook endpoint is unavailable' using errcode='28000';
          end if;

          perform pg_advisory_xact_lock(hashtextextended(
            endpoint.seller_organization_id::text || ':' || endpoint.id::text ||
              ':' || p_external_event_id_sha256,0
          ));
          select * into previous from public.webhook_event event
          where event.seller_organization_id=endpoint.seller_organization_id
            and event.webhook_endpoint_id=endpoint.id
            and event.external_event_id_sha256=p_external_event_id_sha256;
          if found then
            if previous.payload_sha256<>p_payload_sha256 then
              raise exception 'webhook event id conflict' using errcode='23505';
            end if;
            select outbox.id into strict created_outbox_id
            from app_private.outbox_event outbox
            where outbox.seller_organization_id=previous.seller_organization_id
              and outbox.aggregate_type='webhook_event'
              and outbox.aggregate_id=previous.id
              and outbox.event_type='commerce.webhook.received';
            return query select previous.id,created_outbox_id,false;
            return;
          end if;

          insert into public.webhook_event(
            seller_organization_id,checkout_integration_id,webhook_endpoint_id,
            external_event_id_sha256,external_event_id_octets,payload_sha256,
            payload_octets,event_type,external_product_id_sha256,
            external_product_id_octets,external_purchase_id_sha256,
            external_purchase_id_octets,occurred_at,signed_at
          ) values(
            endpoint.seller_organization_id,endpoint.checkout_integration_id,endpoint.id,
            p_external_event_id_sha256,p_external_event_id_octets,p_payload_sha256,
            p_payload_octets,p_event_type,p_external_product_id_sha256,
            p_external_product_id_octets,p_external_purchase_id_sha256,
            p_external_purchase_id_octets,p_occurred_at,to_timestamp(p_signed_at_epoch)
          ) returning id into created_event_id;
          insert into app_private.outbox_event(
            seller_organization_id,aggregate_type,aggregate_id,event_type,
            idempotency_key,payload
          ) values(
            endpoint.seller_organization_id,'webhook_event',created_event_id,
            'commerce.webhook.received','webhook:' || created_event_id::text,
            jsonb_build_object(
              'schema_version',1,'webhook_event_id',created_event_id,
              'seller_organization_id',endpoint.seller_organization_id,
              'checkout_integration_id',endpoint.checkout_integration_id,
              'webhook_endpoint_id',endpoint.id,'event_type',p_event_type,
              'payload_sha256',p_payload_sha256
            )
          ) returning id into created_outbox_id;
          insert into public.audit_event(
            seller_organization_id,actor_identity_user_id,action,target_type,
            target_id,result,request_id,metadata
          ) values(
            endpoint.seller_organization_id,null,'commerce.webhook_received',
            'webhook_event',created_event_id,'succeeded',p_request_id,
            jsonb_build_object(
              'adapter','generic_hmac_v1','webhook_endpoint_id',endpoint.id,
              'external_event_id_sha256',p_external_event_id_sha256,
              'payload_sha256',p_payload_sha256
            )
          );
          return query select created_event_id,created_outbox_id,true;
        end $$
        """,
        f"revoke all on function {_ACCEPT_V1_SIGNATURE} from public",
        f"grant execute on function {_ACCEPT_V1_SIGNATURE} to appsell_runtime",
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0016 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status','0020_checkout_fulfillment')$needle$,
            $replacement$version_num in ('0017_pdf_preraster','0018_offer_pricing','0019_offer_status')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select app_private.schema_ready_base_0017()
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='unit_amount_minor' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='currency' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_attribute
              where attrelid='public.offer'::regclass
                and attname='billing_period' and not attisdropped)
            and exists(select 1 from pg_catalog.pg_constraint
              where conrelid='public.offer'::regclass
                and conname='offer_status_check' and convalidated)
            and to_regprocedure(
              'app_private.transition_offer_status(uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid)'
            ) is not null
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)
