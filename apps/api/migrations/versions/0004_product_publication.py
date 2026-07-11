"""Create versioned offer drafts and immutable product publications.

Revision ID: 0004_product_publication
Revises: 0003_product_course_drafts
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_product_publication"
down_revision: str | None = "0003_product_course_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        alter table seller_membership
          add constraint seller_membership_authorization_state_key
          unique (seller_organization_id, identity_user_id, role, status)
        """,
        """
        create table offer (
          seller_organization_id uuid not null references seller_organization(id),
          id uuid not null default gen_random_uuid(),
          catalog_product_id uuid not null,
          title text not null check (
            title = btrim(title)
            and octet_length(title) between 2 and 120
            and title !~ '[[:cntrl:]]'
          ),
          summary text not null default '' check (
            octet_length(summary) <= 500
            and summary !~ '[[:cntrl:]]'
          ),
          status text not null default 'draft' check (status = 'draft'),
          version bigint not null default 1 check (version > 0),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, id),
          constraint offer_product_fkey
            foreign key (seller_organization_id, catalog_product_id)
            references catalog_product (seller_organization_id, id),
          constraint offer_seller_product_key
            unique (seller_organization_id, catalog_product_id),
          constraint offer_seller_product_id_key
            unique (seller_organization_id, catalog_product_id, id)
        )
        """,
        """
        create index offer_seller_product_created_idx
          on offer (seller_organization_id, catalog_product_id, created_at desc, id desc)
        """,
        """
        create table publication (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          catalog_product_id uuid not null,
          version bigint not null check (version > 0),
          schema_version smallint not null default 1 check (schema_version = 1),
          source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
          snapshot jsonb not null check (
            jsonb_typeof(snapshot) = 'object'
            and octet_length(snapshot::text) <= 1048576
          ),
          actor_identity_user_id uuid not null references identity_user(id),
          published_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, id),
          constraint publication_product_fkey
            foreign key (seller_organization_id, catalog_product_id)
            references catalog_product (seller_organization_id, id),
          constraint publication_product_version_key
            unique (seller_organization_id, catalog_product_id, version),
          constraint publication_seller_product_id_key
            unique (seller_organization_id, catalog_product_id, id)
        )
        """,
        """
        create index publication_product_time_idx
          on publication (
            seller_organization_id, catalog_product_id, published_at desc, id desc
          )
        """,
        """
        alter table catalog_product add column published_snapshot_id uuid
        """,
        """
        alter table catalog_product add constraint catalog_product_published_snapshot_fkey
          foreign key (seller_organization_id, id, published_snapshot_id)
          references publication (seller_organization_id, catalog_product_id, id)
        """,
        """
        alter table catalog_product add constraint catalog_product_publication_state_check
          check (
            (status <> 'published' or published_snapshot_id is not null)
            and (status <> 'draft' or published_snapshot_id is null)
          )
        """,
        """
        create table app_private.catalog_mutation_request (
          seller_organization_id uuid not null,
          operation text not null check (
            operation in ('draft.update', 'product.publish')
          ),
          idempotency_key uuid not null,
          identity_user_id uuid not null references public.identity_user(id),
          body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
          catalog_product_id uuid not null,
          offer_id uuid not null,
          publication_id uuid,
          result jsonb not null check (jsonb_typeof(result) = 'object'),
          created_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, operation, idempotency_key),
          constraint catalog_mutation_request_product_fkey
            foreign key (seller_organization_id, catalog_product_id)
            references public.catalog_product (seller_organization_id, id),
          constraint catalog_mutation_request_offer_fkey
            foreign key (seller_organization_id, catalog_product_id, offer_id)
            references public.offer (seller_organization_id, catalog_product_id, id),
          constraint catalog_mutation_request_publication_fkey
            foreign key (seller_organization_id, catalog_product_id, publication_id)
            references public.publication (seller_organization_id, catalog_product_id, id)
        )
        """,
        """
        create index catalog_mutation_request_actor_idx
          on app_private.catalog_mutation_request (
            identity_user_id, seller_organization_id, created_at desc
          )
        """,
        """
        create index catalog_mutation_request_product_idx
          on app_private.catalog_mutation_request (
            seller_organization_id, catalog_product_id, operation, created_at desc
          )
        """,
        """
        revoke all on table app_private.catalog_mutation_request
          from public, appsell_runtime
        """,
        "alter table offer enable row level security",
        "alter table offer force row level security",
        "alter table publication enable row level security",
        "alter table publication force row level security",
        """
        create or replace function app_private.catalog_draft_read_allowed(
          p_seller_organization_id uuid
        ) returns boolean
        language sql stable security definer
        set search_path = pg_catalog, app_private, public
        as $$
          select p_seller_organization_id is not null
            and app_private.context_text('capability') in (
              'catalog.draft.read', 'catalog.draft.preview'
            )
            and app_private.context_uuid('seller_organization_id') =
                p_seller_organization_id
            and exists (
              select 1
              from public.seller_organization organization
              join public.seller_membership membership
                on membership.seller_organization_id = organization.id
              where organization.id = p_seller_organization_id
                and organization.status = 'active'
                and membership.identity_user_id =
                    app_private.context_uuid('identity_user_id')
                and membership.role = 'seller_owner'
                and membership.status = 'active'
            )
        $$
        """,
        """
        create function app_private.catalog_owner_action_allowed(
          p_seller_organization_id uuid,
          p_capability text
        ) returns boolean
        language sql stable security definer
        set search_path = pg_catalog, app_private, public
        as $$
          select p_seller_organization_id is not null
            and p_capability is not null
            and app_private.context_text('capability') = p_capability
            and app_private.context_uuid('seller_organization_id') =
                p_seller_organization_id
            and exists (
              select 1
              from public.seller_organization organization
              join public.seller_membership membership
                on membership.seller_organization_id = organization.id
              where organization.id = p_seller_organization_id
                and organization.status = 'active'
                and membership.identity_user_id =
                    app_private.context_uuid('identity_user_id')
                and membership.role = 'seller_owner'
                and membership.status = 'active'
            )
        $$
        """,
        """
        revoke all on function app_private.catalog_owner_action_allowed(uuid, text)
          from public
        """,
        """
        grant execute on function app_private.catalog_owner_action_allowed(uuid, text)
          to appsell_runtime
        """,
        """
        create policy offer_owner_all on offer to appsell_owner
          using (true) with check (true)
        """,
        """
        create policy publication_owner_all on publication to appsell_owner
          using (true) with check (true)
        """,
        """
        create policy offer_owner_read on offer for select to appsell_runtime
          using (
            seller_organization_id =
              app_private.context_uuid('seller_organization_id')
            and app_private.catalog_draft_read_allowed(seller_organization_id)
          )
        """,
        """
        create policy publication_owner_read on publication for select to appsell_runtime
          using (
            seller_organization_id =
              app_private.context_uuid('seller_organization_id')
            and (
              app_private.catalog_owner_action_allowed(
                seller_organization_id, 'catalog.publication.read'
              )
              or app_private.catalog_draft_read_allowed(seller_organization_id)
            )
          )
        """,
        """
        create function app_private.reject_publication_mutation()
        returns trigger language plpgsql
        set search_path = pg_catalog
        as $$
        begin
          raise exception 'publications are append-only' using errcode = '55000';
        end
        $$
        """,
        """
        create trigger publication_immutable
          before update or delete on publication
          for each row execute function app_private.reject_publication_mutation()
        """,
        "revoke all on function app_private.reject_publication_mutation() from public",
        """
        create function app_private.update_product_course_offer_draft(
          p_identity_user_id uuid,
          p_seller_organization_id uuid,
          p_catalog_product_id uuid,
          p_product_slug text,
          p_course_id uuid,
          p_offer_id uuid,
          p_product_title text,
          p_product_description text,
          p_course_title text,
          p_offer_title text,
          p_offer_summary text,
          p_expected_product_version bigint,
          p_expected_course_version bigint,
          p_expected_offer_version bigint,
          p_idempotency_key uuid,
          p_body_sha256 text,
          p_request_id uuid
        ) returns table (
          offer_id uuid,
          product_version bigint,
          course_version bigint,
          offer_version bigint,
          created boolean
        )
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          existing_request app_private.catalog_mutation_request%rowtype;
          locked_product public.catalog_product%rowtype;
          locked_course public.course%rowtype;
          locked_offer public.offer%rowtype;
          next_product_version bigint;
          next_course_version bigint;
          next_offer_version bigint;
          response jsonb;
        begin
          if app_private.context_text('capability') <> 'catalog.draft.update'
             or app_private.context_uuid('identity_user_id') is distinct from
                p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is distinct from
                p_seller_organization_id then
            raise exception 'catalog draft update context is not authorized'
              using errcode = '28000';
          end if;
          if p_identity_user_id is null or p_seller_organization_id is null
             or p_catalog_product_id is null or p_course_id is null
             or p_offer_id is null or p_idempotency_key is null
             or p_request_id is null
             or p_product_slug is null
             or p_product_slug !~ '^[a-z0-9]+(?:-[a-z0-9]+)*$'
             or octet_length(p_product_slug) not between 3 and 63
             or p_product_title is null or p_product_title <> btrim(p_product_title)
             or octet_length(p_product_title) not between 2 and 160
             or p_product_title ~ '[[:cntrl:]]'
             or p_product_description is null
             or octet_length(p_product_description) > 2000
             or p_product_description ~ '[[:cntrl:]]'
             or p_course_title is null or p_course_title <> btrim(p_course_title)
             or octet_length(p_course_title) not between 2 and 160
             or p_course_title ~ '[[:cntrl:]]'
             or p_offer_title is null or p_offer_title <> btrim(p_offer_title)
             or octet_length(p_offer_title) not between 2 and 120
             or p_offer_title ~ '[[:cntrl:]]'
             or p_offer_summary is null or octet_length(p_offer_summary) > 500
             or p_offer_summary ~ '[[:cntrl:]]'
             or p_expected_product_version is null or p_expected_product_version < 1
             or p_expected_course_version is null or p_expected_course_version < 1
             or p_expected_offer_version is null or p_expected_offer_version < 0
             or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
            raise exception 'invalid catalog draft update request' using errcode = '22023';
          end if;

          perform 1
          from public.seller_organization organization
          join public.seller_membership membership
            on membership.seller_organization_id = organization.id
          where organization.id = p_seller_organization_id
            and organization.status = 'active'
            and membership.identity_user_id = p_identity_user_id
            and membership.role = 'seller_owner'
            and membership.status = 'active'
          for key share of organization, membership;
          if not found then
            raise exception 'catalog draft owner is not authorized'
              using errcode = '28000';
          end if;

          perform pg_advisory_xact_lock(
            hashtextextended(
              'appsell.catalog.mutation:' || p_seller_organization_id::text ||
              '/draft.update/' || p_idempotency_key::text,
              0
            )
          );
          select * into existing_request
          from app_private.catalog_mutation_request request
          where request.seller_organization_id = p_seller_organization_id
            and request.operation = 'draft.update'
            and request.idempotency_key = p_idempotency_key;
          if found then
            if existing_request.identity_user_id = p_identity_user_id
               and existing_request.body_sha256 = p_body_sha256
               and existing_request.catalog_product_id = p_catalog_product_id
               and existing_request.offer_id = p_offer_id then
              return query select existing_request.offer_id,
                (existing_request.result ->> 'product_version')::bigint,
                (existing_request.result ->> 'course_version')::bigint,
                (existing_request.result ->> 'offer_version')::bigint,
                false;
              return;
            end if;
            raise exception 'catalog draft update idempotency conflict'
              using errcode = '23505';
          end if;

          select * into locked_product
          from public.catalog_product product
          where product.seller_organization_id = p_seller_organization_id
            and product.id = p_catalog_product_id
          for update;
          if not found then
            raise exception 'catalog product is unavailable' using errcode = '28000';
          end if;
          if locked_product.status = 'archived' then
            raise exception 'archived catalog product is immutable' using errcode = '55000';
          end if;
          if locked_product.version <> p_expected_product_version then
            raise exception 'catalog product version conflict' using errcode = '40001';
          end if;

          select course.* into locked_course
          from public.product_course linked
          join public.course course
            on course.seller_organization_id = linked.seller_organization_id
           and course.id = linked.course_id
          where linked.seller_organization_id = p_seller_organization_id
            and linked.catalog_product_id = p_catalog_product_id
            and linked.course_id = p_course_id
          for no key update of linked, course;
          if not found then
            raise exception 'catalog course is unavailable' using errcode = '28000';
          end if;
          if locked_course.status = 'archived' then
            raise exception 'archived course is immutable' using errcode = '55000';
          end if;
          if locked_course.draft_version <> p_expected_course_version then
            raise exception 'catalog course version conflict' using errcode = '40001';
          end if;

          select * into locked_offer
          from public.offer candidate
          where candidate.seller_organization_id = p_seller_organization_id
            and candidate.id = p_offer_id
          for update;
          if p_expected_offer_version = 0 then
            if found then
              raise exception 'catalog offer already exists' using errcode = '23505';
            end if;
            insert into public.offer (
              seller_organization_id, id, catalog_product_id, title, summary, status
            ) values (
              p_seller_organization_id, p_offer_id, p_catalog_product_id,
              p_offer_title, p_offer_summary, 'draft'
            );
            next_offer_version := 1;
          else
            if not found or locked_offer.catalog_product_id <> p_catalog_product_id then
              raise exception 'catalog offer is unavailable' using errcode = '28000';
            end if;
            if locked_offer.status <> 'draft' then
              raise exception 'catalog offer is not editable' using errcode = '55000';
            end if;
            if locked_offer.version <> p_expected_offer_version then
              raise exception 'catalog offer version conflict' using errcode = '40001';
            end if;
            update public.offer
            set title = p_offer_title,
                summary = p_offer_summary,
                version = version + 1,
                updated_at = clock_timestamp()
            where seller_organization_id = p_seller_organization_id
              and id = p_offer_id
            returning version into next_offer_version;
          end if;

          update public.catalog_product
          set slug = p_product_slug,
              title = p_product_title,
              description = p_product_description,
              version = version + 1,
              updated_at = clock_timestamp()
          where seller_organization_id = p_seller_organization_id
            and id = p_catalog_product_id
          returning version into next_product_version;
          update public.course
          set title = p_course_title,
              draft_version = draft_version + 1,
              updated_at = clock_timestamp()
          where seller_organization_id = p_seller_organization_id
            and id = p_course_id
          returning draft_version into next_course_version;

          response := jsonb_build_object(
            'product_version', next_product_version,
            'course_version', next_course_version,
            'offer_version', next_offer_version
          );
          insert into app_private.catalog_mutation_request (
            seller_organization_id, operation, idempotency_key,
            identity_user_id, body_sha256, catalog_product_id, offer_id, result
          ) values (
            p_seller_organization_id, 'draft.update', p_idempotency_key,
            p_identity_user_id, p_body_sha256, p_catalog_product_id, p_offer_id,
            response
          );
          insert into public.audit_event (
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          ) values (
            p_seller_organization_id, p_identity_user_id,
            'catalog.product_draft_updated', 'catalog_product', p_catalog_product_id,
            'succeeded', p_request_id,
            jsonb_build_object(
              'course_id', p_course_id,
              'offer_id', p_offer_id,
              'product_version', next_product_version,
              'course_version', next_course_version,
              'offer_version', next_offer_version
            )
          );
          return query select p_offer_id, next_product_version,
                              next_course_version, next_offer_version, true;
        end
        $$
        """,
        """
        create function app_private.publish_product(
          p_identity_user_id uuid,
          p_seller_organization_id uuid,
          p_catalog_product_id uuid,
          p_offer_id uuid,
          p_expected_product_version bigint,
          p_expected_offer_version bigint,
          p_expected_course_versions jsonb,
          p_idempotency_key uuid,
          p_body_sha256 text,
          p_request_id uuid
        ) returns table (
          publication_id uuid,
          publication_version bigint,
          product_version bigint,
          source_sha256 text,
          created boolean
        )
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare
          existing_request app_private.catalog_mutation_request%rowtype;
          locked_product public.catalog_product%rowtype;
          locked_offer public.offer%rowtype;
          expected_key text;
          expected_value text;
          linked_course_count integer;
          expected_course_count integer;
          next_publication_id uuid := gen_random_uuid();
          next_publication_version bigint;
          next_product_version bigint;
          generated_snapshot jsonb;
          generated_source_sha256 text;
          response jsonb;
        begin
          if app_private.context_text('capability') <> 'catalog.publication.create'
             or app_private.context_uuid('identity_user_id') is distinct from
                p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is distinct from
                p_seller_organization_id then
            raise exception 'catalog publication context is not authorized'
              using errcode = '28000';
          end if;
          if p_identity_user_id is null or p_seller_organization_id is null
             or p_catalog_product_id is null or p_offer_id is null
             or p_expected_product_version is null or p_expected_product_version < 1
             or p_expected_offer_version is null or p_expected_offer_version < 1
             or p_expected_course_versions is null
             or jsonb_typeof(p_expected_course_versions) <> 'object'
             or p_expected_course_versions = '{}'::jsonb
             or p_idempotency_key is null or p_request_id is null
             or p_body_sha256 is null or p_body_sha256 !~ '^[0-9a-f]{64}$' then
            raise exception 'invalid catalog publication request' using errcode = '22023';
          end if;
          for expected_key, expected_value in
            select key, value from jsonb_each_text(p_expected_course_versions)
          loop
            if expected_key !~
                 '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               or expected_value !~ '^[1-9][0-9]{0,18}$' then
              raise exception 'invalid expected course versions' using errcode = '22023';
            end if;
          end loop;
          select count(*) into expected_course_count
          from jsonb_object_keys(p_expected_course_versions);

          perform 1
          from public.seller_organization organization
          join public.seller_membership membership
            on membership.seller_organization_id = organization.id
          where organization.id = p_seller_organization_id
            and organization.status = 'active'
            and membership.identity_user_id = p_identity_user_id
            and membership.role = 'seller_owner'
            and membership.status = 'active'
          for key share of organization, membership;
          if not found then
            raise exception 'catalog publication owner is not authorized'
              using errcode = '28000';
          end if;

          perform pg_advisory_xact_lock(
            hashtextextended(
              'appsell.catalog.mutation:' || p_seller_organization_id::text ||
              '/product.publish/' || p_idempotency_key::text,
              0
            )
          );
          select * into existing_request
          from app_private.catalog_mutation_request request
          where request.seller_organization_id = p_seller_organization_id
            and request.operation = 'product.publish'
            and request.idempotency_key = p_idempotency_key;
          if found then
            if existing_request.identity_user_id = p_identity_user_id
               and existing_request.body_sha256 = p_body_sha256
               and existing_request.catalog_product_id = p_catalog_product_id
               and existing_request.offer_id = p_offer_id then
              return query select existing_request.publication_id,
                (existing_request.result ->> 'publication_version')::bigint,
                (existing_request.result ->> 'product_version')::bigint,
                existing_request.result ->> 'source_sha256', false;
              return;
            end if;
            raise exception 'catalog publication idempotency conflict'
              using errcode = '23505';
          end if;

          select * into locked_product
          from public.catalog_product product
          where product.seller_organization_id = p_seller_organization_id
            and product.id = p_catalog_product_id
          for update;
          if not found then
            raise exception 'catalog product is unavailable' using errcode = '28000';
          end if;
          if locked_product.status = 'archived' then
            raise exception 'archived catalog product cannot be published'
              using errcode = '55000';
          end if;
          if locked_product.version <> p_expected_product_version then
            raise exception 'catalog product version conflict' using errcode = '40001';
          end if;

          select * into locked_offer
          from public.offer candidate
          where candidate.seller_organization_id = p_seller_organization_id
            and candidate.catalog_product_id = p_catalog_product_id
            and candidate.id = p_offer_id
          for no key update;
          if not found then
            raise exception 'catalog offer is unavailable' using errcode = '28000';
          end if;
          if locked_offer.status <> 'draft' then
            raise exception 'catalog offer is not publishable' using errcode = '55000';
          end if;
          if locked_offer.version <> p_expected_offer_version then
            raise exception 'catalog offer version conflict' using errcode = '40001';
          end if;

          perform 1
          from public.product_course linked
          join public.course course
            on course.seller_organization_id = linked.seller_organization_id
           and course.id = linked.course_id
          where linked.seller_organization_id = p_seller_organization_id
            and linked.catalog_product_id = p_catalog_product_id
          order by linked.position, course.id
          for no key update of linked, course;
          get diagnostics linked_course_count = row_count;
          if linked_course_count = 0
             or linked_course_count <> expected_course_count
             or exists (
               select 1
               from public.product_course linked
               join public.course course
                 on course.seller_organization_id = linked.seller_organization_id
                and course.id = linked.course_id
               where linked.seller_organization_id = p_seller_organization_id
                 and linked.catalog_product_id = p_catalog_product_id
                 and (
                   course.status = 'archived'
                   or p_expected_course_versions ->> course.id::text is null
                   or (p_expected_course_versions ->> course.id::text)::numeric
                        <> course.draft_version
                 )
             ) then
            raise exception 'catalog course version conflict' using errcode = '40001';
          end if;

          generated_snapshot := jsonb_build_object(
            'schema_version', 1,
            'seller_organization_id', p_seller_organization_id,
            'product', jsonb_build_object(
              'id', locked_product.id,
              'slug', locked_product.slug,
              'title', locked_product.title,
              'description', locked_product.description
            ),
            'offer', jsonb_build_object(
              'id', locked_offer.id,
              'title', locked_offer.title,
              'summary', locked_offer.summary,
              'billing_authority', 'external_unconfigured'
            ),
            'courses', (
              select jsonb_agg(
                jsonb_build_object(
                  'id', course.id,
                  'title', course.title,
                  'description', course.description,
                  'position', linked.position
                ) order by linked.position, course.id
              )
              from public.product_course linked
              join public.course course
                on course.seller_organization_id = linked.seller_organization_id
               and course.id = linked.course_id
              where linked.seller_organization_id = p_seller_organization_id
                and linked.catalog_product_id = p_catalog_product_id
            )
          );
          generated_source_sha256 := encode(
            public.digest(convert_to(generated_snapshot::text, 'UTF8'), 'sha256'),
            'hex'
          );
          if locked_product.published_snapshot_id is not null
             and exists (
               select 1
               from public.publication current_publication
               where current_publication.seller_organization_id =
                       p_seller_organization_id
                 and current_publication.catalog_product_id = p_catalog_product_id
                 and current_publication.id = locked_product.published_snapshot_id
                 and current_publication.snapshot = generated_snapshot
             ) then
            raise exception 'no unpublished changes' using errcode = 'P4091';
          end if;
          select coalesce(max(existing.version), 0) + 1
            into next_publication_version
          from public.publication existing
          where existing.seller_organization_id = p_seller_organization_id
            and existing.catalog_product_id = p_catalog_product_id;

          insert into public.publication (
            seller_organization_id, id, catalog_product_id, version,
            schema_version, source_sha256, snapshot, actor_identity_user_id
          ) values (
            p_seller_organization_id, next_publication_id, p_catalog_product_id,
            next_publication_version, 1, generated_source_sha256,
            generated_snapshot, p_identity_user_id
          );
          update public.catalog_product
          set status = 'published',
              published_snapshot_id = next_publication_id,
              version = version + 1,
              updated_at = clock_timestamp()
          where seller_organization_id = p_seller_organization_id
            and id = p_catalog_product_id
          returning version into next_product_version;

          response := jsonb_build_object(
            'publication_version', next_publication_version,
            'product_version', next_product_version,
            'source_sha256', generated_source_sha256
          );
          insert into app_private.catalog_mutation_request (
            seller_organization_id, operation, idempotency_key,
            identity_user_id, body_sha256, catalog_product_id, offer_id,
            publication_id, result
          ) values (
            p_seller_organization_id, 'product.publish', p_idempotency_key,
            p_identity_user_id, p_body_sha256, p_catalog_product_id, p_offer_id,
            next_publication_id, response
          );
          insert into public.audit_event (
            seller_organization_id, actor_identity_user_id, action, target_type,
            target_id, result, request_id, metadata
          ) values (
            p_seller_organization_id, p_identity_user_id,
            'catalog.product_published', 'catalog_product', p_catalog_product_id,
            'succeeded', p_request_id,
            jsonb_build_object(
              'publication_id', next_publication_id,
              'publication_version', next_publication_version,
              'source_sha256', generated_source_sha256,
              'offer_id', p_offer_id
            )
          );
          return query select next_publication_id, next_publication_version,
                              next_product_version, generated_source_sha256, true;
        end
        $$
        """,
        """
        revoke all on function app_private.update_product_course_offer_draft(
          uuid, uuid, uuid, text, uuid, uuid, text, text, text, text, text,
          bigint, bigint, bigint, uuid, text, uuid
        ) from public
        """,
        """
        grant execute on function app_private.update_product_course_offer_draft(
          uuid, uuid, uuid, text, uuid, uuid, text, text, text, text, text,
          bigint, bigint, bigint, uuid, text, uuid
        ) to appsell_runtime
        """,
        """
        revoke all on function app_private.publish_product(
          uuid, uuid, uuid, uuid, bigint, bigint, jsonb, uuid, text, uuid
        ) from public
        """,
        """
        grant execute on function app_private.publish_product(
          uuid, uuid, uuid, uuid, bigint, bigint, jsonb, uuid, text, uuid
        ) to appsell_runtime
        """,
        "grant select on offer, publication to appsell_runtime",
        "revoke insert, update, delete on offer, publication from appsell_runtime",
        """
        create or replace function app_private.schema_ready()
        returns boolean language sql stable security definer
        set search_path = pg_catalog, app_private
        as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.platform_grant') is not null
            and to_regclass('public.superadmin_bootstrap') is not null
            and to_regclass('public.seller_organization') is not null
            and to_regclass('public.seller_membership') is not null
            and to_regclass('public.audit_event') is not null
            and to_regclass('public.catalog_product') is not null
            and to_regclass('public.course') is not null
            and to_regclass('public.product_course') is not null
            and to_regclass('public.offer') is not null
            and to_regclass('public.publication') is not null
            and to_regclass('app_private.bff_request_replay') is not null
            and to_regclass('app_private.seller_onboarding_request') is not null
            and to_regclass('app_private.product_draft_request') is not null
            and to_regclass('app_private.catalog_mutation_request') is not null
            and exists (
              select 1 from public.alembic_version
              where version_num = '0004_product_publication'
            )
            and exists (select 1 from app_private.context_signing_key where singleton)
            and exists (select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
