"""Make publication decide product access, and add seat-limited access links.

Two invariants motivate this migration.

First, publication and sale activation used to be two transactions. The seller
published a catalog, the offer stayed in ``draft``, and a second call had to flip
it to ``active``. Between the two calls the product was published with a draft
offer, and ``app_private.grant_public_product_access`` treats exactly that shape
as free access. A product meant to be sold was therefore enrollable for free by
any visitor holding the public link. ``publish_product_with_access`` closes the
window by deciding the access mode inside the publishing transaction: a paid
publication activates the offer before the transaction commits, or nothing is
published at all.

Second, free distribution had no way to be bounded. An access link carries a
seat budget, an expiry and a revocation, and the seat is consumed per person
rather than per click: the redemption table is keyed by
``(seller_organization_id, access_link_id, buyer_identity_user_id)``, so the same
buyer reopening the same link never burns a second seat. The raw link token
never reaches this database. The API stores only its SHA-256 digest and an
eight-character prefix used for display, which keeps a database dump from
handing out access.
"""

from __future__ import annotations

from alembic import op

revision = "0034_publish_access_links"
down_revision = "0033_pdf_study_state"
branch_labels = None
depends_on = None

_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access','0032_platform_superadmin_catalog',"
    "'0033_pdf_study_state')"
)
_READY_TAIL_WITH_0034 = _READY_TAIL[:-1] + ",'0034_publish_access_links')"

_PUBLISH_SIGNATURE = (
    "app_private.publish_product_with_access"
    "(uuid,uuid,uuid,uuid,bigint,bigint,jsonb,text,uuid,text,uuid)"
)
_CREATE_LINK_SIGNATURE = (
    "app_private.create_product_access_link"
    "(uuid,uuid,uuid,text,text,text,integer,timestamptz,uuid,text,uuid)"
)
_LIST_LINKS_SIGNATURE = "app_private.list_product_access_links(uuid,uuid,uuid)"
_REVOKE_LINK_SIGNATURE = (
    "app_private.revoke_product_access_link(uuid,uuid,uuid,uuid,uuid,text,uuid)"
)
_ROTATE_LINK_SIGNATURE = (
    "app_private.rotate_product_access_link(uuid,uuid,uuid,uuid,text,text,uuid,text,uuid)"
)
_LIST_REDEMPTIONS_SIGNATURE = (
    "app_private.list_product_access_link_redemptions(uuid,uuid,uuid,uuid)"
)
_REDEEM_SIGNATURE = "app_private.redeem_product_access_link(uuid,text,uuid,text,uuid)"

_READY_ANCHOR = (
    "and to_regprocedure('app_private.mutate_pdf_study_state"
    "(uuid,uuid,uuid,bigint,uuid,text,uuid,jsonb)') is not null"
)
_READY_ADDITION = f"""{_READY_ANCHOR}
            and to_regclass('public.product_access_link') is not null
            and to_regclass('public.product_access_link_redemption') is not null
            and to_regprocedure('{_PUBLISH_SIGNATURE}') is not null
            and to_regprocedure('{_CREATE_LINK_SIGNATURE}') is not null
            and to_regprocedure('{_REDEEM_SIGNATURE}') is not null"""

_ACCESS_LINK_TABLE = r"""
create table public.product_access_link (
  seller_organization_id uuid not null,
  id uuid not null default gen_random_uuid(),
  catalog_product_id uuid not null,
  label text not null check (
    label = btrim(label)
    and length(label) between 1 and 80
    and label !~ '[[:cntrl:]]'
  ),
  token_sha256 text not null check (token_sha256 ~ '^[0-9a-f]{64}$'),
  token_prefix text not null check (token_prefix ~ '^[0-9a-f]{8}$'),
  max_uses integer check (max_uses is null or max_uses between 1 and 100000),
  redeemed_count integer not null default 0 check (redeemed_count >= 0),
  expires_at timestamptz,
  revoked_at timestamptz,
  rotated_at timestamptz,
  created_by_identity_user_id uuid not null references public.identity_user(id),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (seller_organization_id, id),
  constraint product_access_link_public_id_key unique (id),
  constraint product_access_link_token_key unique (token_sha256),
  constraint product_access_link_product_fkey
    foreign key (seller_organization_id, catalog_product_id)
    references public.catalog_product(seller_organization_id, id),
  constraint product_access_link_seat_check
    check (max_uses is null or redeemed_count <= max_uses)
)
"""

_REDEMPTION_TABLE = """
create table public.product_access_link_redemption (
  seller_organization_id uuid not null,
  access_link_id uuid not null,
  buyer_identity_user_id uuid not null references public.identity_user(id),
  entitlement_id uuid not null references public.buyer_entitlement(id),
  redeemed_at timestamptz not null default clock_timestamp(),
  primary key (seller_organization_id, access_link_id, buyer_identity_user_id),
  constraint product_access_link_redemption_link_fkey
    foreign key (seller_organization_id, access_link_id)
    references public.product_access_link(seller_organization_id, id)
)
"""

# The paid pre-flight runs BEFORE the inner publish call so a seller without an
# active payout account never ends up with a published catalog as the side
# effect of a rejected sale.
_PUBLISH_WITH_ACCESS = r"""
create function app_private.publish_product_with_access(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_offer_id uuid,
  p_expected_product_version bigint,
  p_expected_offer_version bigint,
  p_expected_course_versions jsonb,
  p_access_mode text,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table(
  publication_id uuid,
  publication_version bigint,
  product_version bigint,
  source_sha256 text,
  offer_status text,
  offer_version bigint,
  created boolean
)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  published record;
  locked_offer public.offer%rowtype;
  final_status text;
  final_version bigint;
begin
  if p_access_mode is null or p_access_mode not in ('free','paid') then
    raise exception 'publication access mode is invalid' using errcode='22023';
  end if;
  if app_private.context_text('capability') is distinct from 'catalog.publication.create'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id')
        is distinct from p_seller_organization_id then
    raise exception 'publication access context is not authorized' using errcode='28000';
  end if;

  if p_access_mode='paid' then
    select * into locked_offer
    from public.offer offer
    where offer.seller_organization_id=p_seller_organization_id
      and offer.catalog_product_id=p_catalog_product_id
      and offer.id=p_offer_id
    for update;
    if not found then
      raise exception 'catalog offer is unavailable' using errcode='28000';
    end if;
    if locked_offer.unit_amount_minor <= 0 then
      raise exception 'paid access requires a positive price' using errcode='P4222';
    end if;
    if not exists (
      select 1
      from public.seller_payout_account account
      where account.seller_organization_id=p_seller_organization_id
        and account.charges_enabled
        and account.payouts_enabled
        and account.disabled_reason is null
    ) then
      raise exception 'paid access requires an active payout account' using errcode='P4223';
    end if;
  end if;

  select * into published
  from app_private.publish_product(
    p_identity_user_id,
    p_seller_organization_id,
    p_catalog_product_id,
    p_offer_id,
    p_expected_product_version,
    p_expected_offer_version,
    p_expected_course_versions,
    p_idempotency_key,
    p_body_sha256,
    p_request_id
  );

  if p_access_mode='paid' and published.created then
    update public.offer offer
    set status='active',
        version=offer.version+1,
        updated_at=clock_timestamp()
    where offer.seller_organization_id=p_seller_organization_id
      and offer.catalog_product_id=p_catalog_product_id
      and offer.id=p_offer_id
    returning offer.status, offer.version into final_status, final_version;
    if final_status is null then
      raise exception 'catalog offer is unavailable' using errcode='28000';
    end if;
    insert into public.audit_event(
      seller_organization_id,actor_identity_user_id,action,target_type,target_id,
      result,request_id,metadata
    ) values(
      p_seller_organization_id,p_identity_user_id,'catalog.offer_activated',
      'offer',p_offer_id,'succeeded',p_request_id,
      jsonb_build_object(
        'catalog_product_id',p_catalog_product_id,
        'publication_id',published.publication_id,
        'access_mode',p_access_mode
      )
    );
  else
    select offer.status, offer.version into final_status, final_version
    from public.offer offer
    where offer.seller_organization_id=p_seller_organization_id
      and offer.catalog_product_id=p_catalog_product_id
      and offer.id=p_offer_id;
    if final_status is null then
      raise exception 'catalog offer is unavailable' using errcode='28000';
    end if;
  end if;

  return query select
    published.publication_id,
    published.publication_version,
    published.product_version,
    published.source_sha256,
    final_status,
    final_version,
    published.created;
end
$$
"""

_CREATE_LINK = r"""
create function app_private.create_product_access_link(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_label text,
  p_token_sha256 text,
  p_token_prefix text,
  p_max_uses integer,
  p_expires_at timestamptz,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table(
  access_link_id uuid,
  token_prefix text,
  label text,
  max_uses integer,
  redeemed_count integer,
  expires_at timestamptz,
  created_at timestamptz
)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  created_link public.product_access_link%rowtype;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$'
     or p_token_sha256 is null or p_token_sha256 !~ '^[0-9a-f]{64}$'
     or p_token_prefix is null or p_token_prefix !~ '^[0-9a-f]{8}$'
     or p_label is null or p_label <> btrim(p_label)
     or length(p_label) not between 1 and 80 or p_label ~ '[[:cntrl:]]'
     or (p_max_uses is not null and p_max_uses not between 1 and 100000)
     or (p_expires_at is not null and p_expires_at <= clock_timestamp()) then
    raise exception 'access link request is invalid' using errcode='22023';
  end if;
  if app_private.context_text('capability') is distinct from 'catalog.access_link.create'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id')
        is distinct from p_seller_organization_id then
    raise exception 'access link context is not authorized' using errcode='28000';
  end if;

  perform 1
  from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id=organization.id
  where organization.id=p_seller_organization_id
    and organization.status='active'
    and membership.identity_user_id=p_identity_user_id
    and membership.role='seller_owner'
    and membership.status='active'
  for key share of organization, membership;
  if not found then
    raise exception 'access link owner is not authorized' using errcode='28000';
  end if;

  perform 1
  from public.catalog_product product
  where product.seller_organization_id=p_seller_organization_id
    and product.id=p_catalog_product_id
    and product.status <> 'archived'
  for share;
  if not found then
    raise exception 'access link product is unavailable' using errcode='28000';
  end if;

  -- Redemption grants free access, which a sold product refuses. Minting a link
  -- for one would hand the seller an invitation that can never work, so the
  -- refusal belongs here rather than in the recipient's face.
  if exists (
    select 1
    from public.offer offer
    where offer.seller_organization_id=p_seller_organization_id
      and offer.catalog_product_id=p_catalog_product_id
      and offer.status='active'
  ) then
    raise exception 'access link requires a free product' using errcode='P4226';
  end if;

  insert into public.product_access_link(
    seller_organization_id,catalog_product_id,label,token_sha256,token_prefix,
    max_uses,expires_at,created_by_identity_user_id
  ) values(
    p_seller_organization_id,p_catalog_product_id,p_label,p_token_sha256,p_token_prefix,
    p_max_uses,p_expires_at,p_identity_user_id
  )
  returning * into created_link;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.access_link_created',
    'product_access_link',created_link.id,'succeeded',p_request_id,
    jsonb_build_object(
      'catalog_product_id',p_catalog_product_id,
      'token_prefix',p_token_prefix,
      'max_uses',p_max_uses,
      'idempotency_key',p_idempotency_key
    )
  );

  return query select
    created_link.id,created_link.token_prefix,created_link.label,created_link.max_uses,
    created_link.redeemed_count,created_link.expires_at,created_link.created_at;
end
$$
"""

_LIST_LINKS = """
create function app_private.list_product_access_links(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid
) returns table(
  access_link_id uuid,
  token_prefix text,
  label text,
  max_uses integer,
  redeemed_count integer,
  expires_at timestamptz,
  revoked_at timestamptz,
  rotated_at timestamptz,
  created_at timestamptz
)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null then
    raise exception 'access link listing is invalid' using errcode='22023';
  end if;
  if app_private.context_text('capability') is distinct from 'catalog.access_link.read'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id')
        is distinct from p_seller_organization_id then
    raise exception 'access link context is not authorized' using errcode='28000';
  end if;

  perform 1
  from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id=organization.id
  where organization.id=p_seller_organization_id
    and organization.status='active'
    and membership.identity_user_id=p_identity_user_id
    and membership.role='seller_owner'
    and membership.status='active';
  if not found then
    raise exception 'access link owner is not authorized' using errcode='28000';
  end if;

  return query
  select link.id,link.token_prefix,link.label,link.max_uses,link.redeemed_count,
         link.expires_at,link.revoked_at,link.rotated_at,link.created_at
  from public.product_access_link link
  where link.seller_organization_id=p_seller_organization_id
    and link.catalog_product_id=p_catalog_product_id
  order by link.created_at desc, link.id desc;
end
$$
"""

_LIST_REDEMPTIONS = """
create function app_private.list_product_access_link_redemptions(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_access_link_id uuid
) returns table(
  buyer_identity_user_id uuid,
  entitlement_id uuid,
  redeemed_at timestamptz
)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_access_link_id is null then
    raise exception 'access link redemption listing is invalid' using errcode='22023';
  end if;
  if app_private.context_text('capability') is distinct from 'catalog.access_link.read'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id')
        is distinct from p_seller_organization_id then
    raise exception 'access link context is not authorized' using errcode='28000';
  end if;

  perform 1
  from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id=organization.id
  where organization.id=p_seller_organization_id
    and organization.status='active'
    and membership.identity_user_id=p_identity_user_id
    and membership.role='seller_owner'
    and membership.status='active';
  if not found then
    raise exception 'access link owner is not authorized' using errcode='28000';
  end if;

  return query
  select redemption.buyer_identity_user_id,redemption.entitlement_id,redemption.redeemed_at
  from public.product_access_link_redemption redemption
  join public.product_access_link link
    on link.seller_organization_id=redemption.seller_organization_id
   and link.id=redemption.access_link_id
  where redemption.seller_organization_id=p_seller_organization_id
    and redemption.access_link_id=p_access_link_id
    and link.catalog_product_id=p_catalog_product_id
  order by redemption.redeemed_at desc;
end
$$
"""

_REVOKE_LINK = r"""
create function app_private.revoke_product_access_link(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_access_link_id uuid,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table(access_link_id uuid, revoked_at timestamptz, changed boolean)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  locked_link public.product_access_link%rowtype;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_access_link_id is null
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'access link revocation is invalid' using errcode='22023';
  end if;
  if app_private.context_text('capability') is distinct from 'catalog.access_link.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id')
        is distinct from p_seller_organization_id then
    raise exception 'access link context is not authorized' using errcode='28000';
  end if;

  perform 1
  from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id=organization.id
  where organization.id=p_seller_organization_id
    and organization.status='active'
    and membership.identity_user_id=p_identity_user_id
    and membership.role='seller_owner'
    and membership.status='active'
  for key share of organization, membership;
  if not found then
    raise exception 'access link owner is not authorized' using errcode='28000';
  end if;

  select * into locked_link
  from public.product_access_link link
  where link.seller_organization_id=p_seller_organization_id
    and link.catalog_product_id=p_catalog_product_id
    and link.id=p_access_link_id
  for update;
  if not found then
    raise exception 'access link is unavailable' using errcode='P4225';
  end if;

  if locked_link.revoked_at is not null then
    return query select locked_link.id,locked_link.revoked_at,false;
    return;
  end if;

  update public.product_access_link link
  set revoked_at=clock_timestamp(),updated_at=clock_timestamp()
  where link.seller_organization_id=p_seller_organization_id
    and link.id=p_access_link_id
  returning * into locked_link;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.access_link_revoked',
    'product_access_link',locked_link.id,'succeeded',p_request_id,
    jsonb_build_object(
      'catalog_product_id',p_catalog_product_id,
      'token_prefix',locked_link.token_prefix,
      'redeemed_count',locked_link.redeemed_count,
      'idempotency_key',p_idempotency_key
    )
  );

  return query select locked_link.id,locked_link.revoked_at,true;
end
$$
"""

# Rotation keeps the seat budget and the label. The previous digest stops
# resolving the moment this commits, which is what makes "I lost the link" safe
# to answer without handing the seller a way to read the old one back.
_ROTATE_LINK = r"""
create function app_private.rotate_product_access_link(
  p_identity_user_id uuid,
  p_seller_organization_id uuid,
  p_catalog_product_id uuid,
  p_access_link_id uuid,
  p_token_sha256 text,
  p_token_prefix text,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table(
  access_link_id uuid,
  token_prefix text,
  redeemed_count integer,
  max_uses integer,
  rotated_at timestamptz
)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  locked_link public.product_access_link%rowtype;
begin
  if p_identity_user_id is null or p_seller_organization_id is null
     or p_catalog_product_id is null or p_access_link_id is null
     or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$'
     or p_token_sha256 is null or p_token_sha256 !~ '^[0-9a-f]{64}$'
     or p_token_prefix is null or p_token_prefix !~ '^[0-9a-f]{8}$' then
    raise exception 'access link rotation is invalid' using errcode='22023';
  end if;
  if app_private.context_text('capability') is distinct from 'catalog.access_link.update'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id')
        is distinct from p_seller_organization_id then
    raise exception 'access link context is not authorized' using errcode='28000';
  end if;

  perform 1
  from public.seller_organization organization
  join public.seller_membership membership
    on membership.seller_organization_id=organization.id
  where organization.id=p_seller_organization_id
    and organization.status='active'
    and membership.identity_user_id=p_identity_user_id
    and membership.role='seller_owner'
    and membership.status='active'
  for key share of organization, membership;
  if not found then
    raise exception 'access link owner is not authorized' using errcode='28000';
  end if;

  select * into locked_link
  from public.product_access_link link
  where link.seller_organization_id=p_seller_organization_id
    and link.catalog_product_id=p_catalog_product_id
    and link.id=p_access_link_id
  for update;
  if not found or locked_link.revoked_at is not null then
    raise exception 'access link is unavailable' using errcode='P4225';
  end if;

  update public.product_access_link link
  set token_sha256=p_token_sha256,
      token_prefix=p_token_prefix,
      rotated_at=clock_timestamp(),
      updated_at=clock_timestamp()
  where link.seller_organization_id=p_seller_organization_id
    and link.id=p_access_link_id
  returning * into locked_link;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller_organization_id,p_identity_user_id,'catalog.access_link_rotated',
    'product_access_link',locked_link.id,'succeeded',p_request_id,
    jsonb_build_object(
      'catalog_product_id',p_catalog_product_id,
      'token_prefix',p_token_prefix,
      'redeemed_count',locked_link.redeemed_count,
      'idempotency_key',p_idempotency_key
    )
  );

  return query select
    locked_link.id,locked_link.token_prefix,locked_link.redeemed_count,
    locked_link.max_uses,locked_link.rotated_at;
end
$$
"""

# The row lock on the link is what serialises the seat budget: concurrent
# redeemers queue on it, so a thirty seat link admits exactly thirty people no
# matter how many arrive at once. Re-entry by an already admitted buyer takes the
# early branch and never touches the counter.
_REDEEM_LINK = r"""
create function app_private.redeem_product_access_link(
  p_identity_user_id uuid,
  p_token_sha256 text,
  p_idempotency_key uuid,
  p_body_sha256 text,
  p_request_id uuid
) returns table(
  entitlement_id uuid,
  seller_organization_id uuid,
  catalog_product_id uuid,
  created boolean,
  seat_consumed boolean,
  seats_remaining integer
)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  locked_link public.product_access_link%rowtype;
  prior_redemption public.product_access_link_redemption%rowtype;
  granted record;
  consumed boolean;
  remaining integer;
begin
  if p_identity_user_id is null or p_idempotency_key is null or p_request_id is null
     or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$'
     or p_token_sha256 is null or p_token_sha256 !~ '^[0-9a-f]{64}$' then
    raise exception 'access link redemption is invalid' using errcode='22023';
  end if;
  if app_private.context_text('capability') is distinct from 'library.enroll'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is not null then
    raise exception 'access link redemption context is not authorized' using errcode='42501';
  end if;

  select * into locked_link
  from public.product_access_link link
  where link.token_sha256=p_token_sha256
  for update;
  if not found
     or locked_link.revoked_at is not null
     or (locked_link.expires_at is not null and locked_link.expires_at <= clock_timestamp()) then
    raise exception 'access link is unavailable' using errcode='P4225';
  end if;

  select * into prior_redemption
  from public.product_access_link_redemption redemption
  where redemption.seller_organization_id=locked_link.seller_organization_id
    and redemption.access_link_id=locked_link.id
    and redemption.buyer_identity_user_id=p_identity_user_id
  for update;

  if prior_redemption.access_link_id is null
     and locked_link.max_uses is not null
     and locked_link.redeemed_count >= locked_link.max_uses then
    raise exception 'access link has no seats left' using errcode='P4224';
  end if;

  select * into granted
  from app_private.grant_public_product_access(
    p_identity_user_id,
    locked_link.seller_organization_id,
    locked_link.catalog_product_id,
    p_idempotency_key,
    p_body_sha256,
    p_request_id
  );

  if prior_redemption.access_link_id is null then
    insert into public.product_access_link_redemption(
      seller_organization_id,access_link_id,buyer_identity_user_id,entitlement_id
    ) values(
      locked_link.seller_organization_id,locked_link.id,p_identity_user_id,
      granted.entitlement_id
    );
    update public.product_access_link link
    set redeemed_count=link.redeemed_count+1,updated_at=clock_timestamp()
    where link.seller_organization_id=locked_link.seller_organization_id
      and link.id=locked_link.id
    returning * into locked_link;
    consumed:=true;
  else
    consumed:=false;
  end if;

  remaining:=case
    when locked_link.max_uses is null then null
    else greatest(locked_link.max_uses-locked_link.redeemed_count,0)
  end;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    locked_link.seller_organization_id,p_identity_user_id,'commerce.access_link_redeemed',
    'buyer_entitlement',granted.entitlement_id,'succeeded',p_request_id,
    jsonb_build_object(
      'access_link_id',locked_link.id,
      'token_prefix',locked_link.token_prefix,
      'catalog_product_id',locked_link.catalog_product_id,
      'seat_consumed',consumed
    )
  );

  return query select
    granted.entitlement_id,
    locked_link.seller_organization_id,
    locked_link.catalog_product_id,
    granted.created,
    consumed,
    remaining;
end
$$
"""


def upgrade() -> None:
    statements = [
        _ACCESS_LINK_TABLE,
        "alter table public.product_access_link enable row level security",
        "alter table public.product_access_link force row level security",
        """
        create policy product_access_link_owner_all on public.product_access_link
          to appsell_owner using (true) with check (true)
        """,
        """
        create index product_access_link_product_idx
          on public.product_access_link(seller_organization_id,catalog_product_id,created_at desc)
        """,
        _REDEMPTION_TABLE,
        "alter table public.product_access_link_redemption enable row level security",
        "alter table public.product_access_link_redemption force row level security",
        """
        create policy product_access_link_redemption_owner_all
          on public.product_access_link_redemption
          to appsell_owner using (true) with check (true)
        """,
        _PUBLISH_WITH_ACCESS,
        _CREATE_LINK,
        _LIST_LINKS,
        _LIST_REDEMPTIONS,
        _REVOKE_LINK,
        _ROTATE_LINK,
        _REDEEM_LINK,
        f"revoke all on function {_PUBLISH_SIGNATURE} from public",
        f"grant execute on function {_PUBLISH_SIGNATURE} to appsell_runtime",
        f"revoke all on function {_CREATE_LINK_SIGNATURE} from public",
        f"grant execute on function {_CREATE_LINK_SIGNATURE} to appsell_runtime",
        f"revoke all on function {_LIST_LINKS_SIGNATURE} from public",
        f"grant execute on function {_LIST_LINKS_SIGNATURE} to appsell_runtime",
        f"revoke all on function {_LIST_REDEMPTIONS_SIGNATURE} from public",
        f"grant execute on function {_LIST_REDEMPTIONS_SIGNATURE} to appsell_runtime",
        f"revoke all on function {_REVOKE_LINK_SIGNATURE} from public",
        f"grant execute on function {_REVOKE_LINK_SIGNATURE} to appsell_runtime",
        f"revoke all on function {_ROTATE_LINK_SIGNATURE} from public",
        f"grant execute on function {_ROTATE_LINK_SIGNATURE} to appsell_runtime",
        f"revoke all on function {_REDEEM_SIGNATURE} from public",
        f"grant execute on function {_REDEEM_SIGNATURE} to appsell_runtime",
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0034}$new$);
          if changed=definition then
            raise exception 'cannot extend 0016 readiness for 0034' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0034}$new$);
          if changed=definition then
            raise exception 'cannot extend 0017 readiness for 0034' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ANCHOR}$old$,$new${_READY_ADDITION}$new$);
          if changed=definition then
            raise exception 'cannot extend schema readiness for 0034' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """
        do $migration$
        begin
          if exists (select 1 from public.product_access_link_redemption) then
            raise exception 'cannot drop redeemed access links' using errcode='55000';
          end if;
        end $migration$
        """,
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ADDITION}$old$,$new${_READY_ANCHOR}$new$);
          if changed=definition then
            raise exception 'cannot restore readiness from 0034' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0034}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0017 readiness from 0034' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0034}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0016 readiness from 0034' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"drop function {_REDEEM_SIGNATURE}",
        f"drop function {_ROTATE_LINK_SIGNATURE}",
        f"drop function {_REVOKE_LINK_SIGNATURE}",
        f"drop function {_LIST_REDEMPTIONS_SIGNATURE}",
        f"drop function {_LIST_LINKS_SIGNATURE}",
        f"drop function {_CREATE_LINK_SIGNATURE}",
        f"drop function {_PUBLISH_SIGNATURE}",
        "drop table public.product_access_link_redemption",
        "drop table public.product_access_link",
    ]
    for statement in statements:
        op.execute(statement)
