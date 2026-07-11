"""Add publication-pinned learning enrollment and progress.

Revision ID: 0007_learning_progress
Revises: 0006_demo_learning
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_learning_progress"
down_revision: str | None = "0006_demo_learning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        create table publication_course (
          seller_organization_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          course_id uuid not null,
          position integer not null check (position >= 0),
          lesson_count integer not null check (lesson_count >= 0),
          primary key (seller_organization_id,publication_id,course_id),
          constraint publication_course_publication_fkey
            foreign key (seller_organization_id,catalog_product_id,publication_id)
            references publication(seller_organization_id,catalog_product_id,id),
          constraint publication_course_course_fkey
            foreign key (seller_organization_id,course_id)
            references course(seller_organization_id,id),
          constraint publication_course_full_key unique (
            seller_organization_id,catalog_product_id,publication_id,course_id
          ),
          constraint publication_course_position_key unique (
            seller_organization_id,publication_id,position
          )
        )
        """,
        """
        create table publication_lesson (
          seller_organization_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          course_id uuid not null,
          module_id uuid not null,
          lesson_id uuid not null,
          course_position integer not null check (course_position >= 0),
          module_position integer not null check (module_position >= 0),
          lesson_position integer not null check (lesson_position >= 0),
          primary key (seller_organization_id,publication_id,lesson_id),
          constraint publication_lesson_course_fkey foreign key (
            seller_organization_id,catalog_product_id,publication_id,course_id
          ) references publication_course(
            seller_organization_id,catalog_product_id,publication_id,course_id
          ),
          constraint publication_lesson_source_fkey foreign key (
            seller_organization_id,course_id,module_id,lesson_id
          ) references lesson(seller_organization_id,course_id,module_id,id),
          constraint publication_lesson_full_key unique (
            seller_organization_id,catalog_product_id,publication_id,course_id,lesson_id
          ),
          constraint publication_lesson_order_key unique (
            seller_organization_id,publication_id,course_position,module_position,lesson_position
          )
        )
        """,
        "alter table publication_course enable row level security",
        "alter table publication_course force row level security",
        "alter table publication_lesson enable row level security",
        "alter table publication_lesson force row level security",
        "create policy publication_course_owner_all on publication_course to appsell_owner using (true) with check (true)",
        "create policy publication_lesson_owner_all on publication_lesson to appsell_owner using (true) with check (true)",
        """
        create function app_private.project_publication(
          p_seller uuid,p_product uuid,p_publication uuid,p_snapshot jsonb
        ) returns void language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare course_item jsonb; module_item jsonb; lesson_item jsonb;
        begin
          if p_snapshot->>'schema_version'<>'2' then return; end if;
          if p_snapshot->>'seller_organization_id'<>p_seller::text
             or p_snapshot->'product'->>'id'<>p_product::text
             or jsonb_typeof(p_snapshot->'courses')<>'array' then
            raise exception 'invalid publication projection' using errcode='22023';
          end if;
          for course_item in select value from jsonb_array_elements(p_snapshot->'courses') loop
            insert into public.publication_course(
              seller_organization_id,catalog_product_id,publication_id,course_id,
              position,lesson_count
            ) values(
              p_seller,p_product,p_publication,(course_item->>'id')::uuid,
              (course_item->>'position')::integer,
              (select count(*) from jsonb_array_elements(course_item->'modules') module_value,
                jsonb_array_elements(module_value->'lessons') lesson_value)
            );
            for module_item in select value from jsonb_array_elements(course_item->'modules') loop
              for lesson_item in select value from jsonb_array_elements(module_item->'lessons') loop
                insert into public.publication_lesson(
                  seller_organization_id,catalog_product_id,publication_id,course_id,
                  module_id,lesson_id,course_position,module_position,lesson_position
                ) values(
                  p_seller,p_product,p_publication,(course_item->>'id')::uuid,
                  (module_item->>'id')::uuid,(lesson_item->>'id')::uuid,
                  (course_item->>'position')::integer,
                  (module_item->>'position')::integer,
                  (lesson_item->>'position')::integer
                );
              end loop;
            end loop;
          end loop;
        end $$
        """,
        "revoke all on function app_private.project_publication(uuid,uuid,uuid,jsonb) from public,appsell_runtime",
        """
        create function app_private.project_publication_trigger()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          perform app_private.project_publication(
            new.seller_organization_id,new.catalog_product_id,new.id,new.snapshot
          );
          return new;
        end $$
        """,
        "revoke all on function app_private.project_publication_trigger() from public,appsell_runtime",
        """
        create trigger publication_projection_after_insert
          after insert on publication for each row
          execute function app_private.project_publication_trigger()
        """,
        """
        select app_private.project_publication(
          seller_organization_id,catalog_product_id,id,snapshot
        ) from publication where schema_version=2
        """,
        """
        create trigger publication_course_append_only
          before update or delete on publication_course for each row
          execute function app_private.reject_publication_mutation()
        """,
        """
        create trigger publication_lesson_append_only
          before update or delete on publication_lesson for each row
          execute function app_private.reject_publication_mutation()
        """,
        "alter table buyer_entitlement add column publication_id uuid",
        """
        do $$
        begin
          if exists(
            select 1 from buyer_entitlement entitlement
            join catalog_product product
              on product.seller_organization_id=entitlement.seller_organization_id
             and product.id=entitlement.catalog_product_id
            left join publication source
              on source.seller_organization_id=product.seller_organization_id
             and source.catalog_product_id=product.id
             and source.id=product.published_snapshot_id
            where product.status<>'published' or product.published_snapshot_id is null
              or source.id is null or source.schema_version<>2
              or not exists(
                select 1 from publication_lesson projected
                where projected.seller_organization_id=source.seller_organization_id
                  and projected.publication_id=source.id
              )
          ) then
            raise exception using
              errcode='23514',
              message='0007_learning_progress cannot pin an entitlement to a playable v2 publication',
              hint='publish a v2 snapshot with at least one lesson or revoke/remove the entitlement before upgrading';
          end if;
        end $$
        """,
        """
        update buyer_entitlement entitlement set publication_id=product.published_snapshot_id
        from catalog_product product
        where product.seller_organization_id=entitlement.seller_organization_id
          and product.id=entitlement.catalog_product_id
          and product.status='published' and product.published_snapshot_id is not null
        """,
        "alter table buyer_entitlement alter column publication_id set not null",
        """
        alter table buyer_entitlement add constraint buyer_entitlement_publication_fkey
          foreign key (seller_organization_id,catalog_product_id,publication_id)
          references publication(seller_organization_id,catalog_product_id,id)
        """,
        """
        alter table buyer_entitlement add constraint buyer_entitlement_learning_key
          unique (seller_organization_id,id,buyer_identity_user_id,catalog_product_id,publication_id)
        """,
        """
        do $$
        begin
          if exists(
            select 1 from buyer_entitlement group by id having count(*)>1
          ) then
            raise exception using
              errcode='23505',
              message='0007_learning_progress requires globally unique entitlement ids',
              hint='replace colliding legacy entitlement ids before upgrading';
          end if;
        end $$
        """,
        """
        alter table buyer_entitlement add constraint buyer_entitlement_public_id_key
          unique (id)
        """,
        "drop policy buyer_entitlement_subject_read on buyer_entitlement",
        """
        create policy buyer_entitlement_subject_read on buyer_entitlement
          for select to appsell_runtime using (
            buyer_identity_user_id=app_private.context_uuid('identity_user_id')
            and app_private.context_text('capability')='library.read'
            and status='active' and starts_at<=clock_timestamp()
            and (expires_at is null or expires_at>clock_timestamp())
          )
        """,
        "drop policy learning_asset_subject_read on learning_asset",
        """
        create function app_private.active_learning_entitlement(
          p_actor uuid,p_seller uuid,p_entitlement uuid,p_product uuid,p_publication uuid
        ) returns boolean language sql stable security definer
        set search_path=pg_catalog,app_private,public as $$
          select exists(
            select 1
            from public.buyer_entitlement entitlement
            join public.seller_organization organization
              on organization.id=entitlement.seller_organization_id
            join public.catalog_product product
              on product.seller_organization_id=entitlement.seller_organization_id
             and product.id=entitlement.catalog_product_id
            where entitlement.buyer_identity_user_id=p_actor
              and entitlement.seller_organization_id=p_seller
              and entitlement.id=p_entitlement
              and entitlement.catalog_product_id=p_product
              and entitlement.publication_id=p_publication
              and entitlement.status='active'
              and entitlement.starts_at<=clock_timestamp()
              and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
              and organization.status='active' and product.status='published'
          )
        $$
        """,
        "revoke all on function app_private.active_learning_entitlement(uuid,uuid,uuid,uuid,uuid) from public,appsell_runtime",
        """
        create function app_private.active_learning_asset(
          p_actor uuid,p_seller uuid,p_product uuid,p_lesson uuid
        ) returns boolean language sql stable security definer
        set search_path=pg_catalog,app_private,public as $$
          select exists(
            select 1
            from public.buyer_entitlement entitlement
            join public.seller_organization organization
              on organization.id=entitlement.seller_organization_id
            join public.catalog_product product
              on product.seller_organization_id=entitlement.seller_organization_id
             and product.id=entitlement.catalog_product_id
            join public.publication_lesson projected
              on projected.seller_organization_id=entitlement.seller_organization_id
             and projected.catalog_product_id=entitlement.catalog_product_id
             and projected.publication_id=entitlement.publication_id
             and projected.lesson_id=p_lesson
            where entitlement.buyer_identity_user_id=p_actor
              and entitlement.seller_organization_id=p_seller
              and entitlement.catalog_product_id=p_product
              and entitlement.status='active'
              and entitlement.starts_at<=clock_timestamp()
              and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
              and organization.status='active' and product.status='published'
          )
        $$
        """,
        "revoke all on function app_private.active_learning_asset(uuid,uuid,uuid,uuid) from public,appsell_runtime",
        """
        create policy learning_asset_subject_read on learning_asset
          for select to appsell_runtime using (
            app_private.context_text('capability') in ('library.read','library.asset.read')
            and status='active'
            and app_private.active_learning_asset(
              app_private.context_uuid('identity_user_id'),seller_organization_id,
              catalog_product_id,lesson_id
            )
          )
        """,
        """
        create table learning_enrollment (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          buyer_identity_user_id uuid not null,
          entitlement_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          course_id uuid not null,
          created_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,id),
          constraint learning_enrollment_entitlement_fkey foreign key (
            seller_organization_id,entitlement_id,buyer_identity_user_id,
            catalog_product_id,publication_id
          ) references buyer_entitlement(
            seller_organization_id,id,buyer_identity_user_id,catalog_product_id,publication_id
          ),
          constraint learning_enrollment_course_fkey foreign key (
            seller_organization_id,catalog_product_id,publication_id,course_id
          ) references publication_course(
            seller_organization_id,catalog_product_id,publication_id,course_id
          ),
          constraint learning_enrollment_entitlement_course_key unique (
            seller_organization_id,entitlement_id,course_id
          ),
          constraint learning_enrollment_full_key unique (
            seller_organization_id,id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id
          )
        )
        """,
        """
        create table lesson_progress (
          seller_organization_id uuid not null,
          enrollment_id uuid not null,
          buyer_identity_user_id uuid not null,
          entitlement_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          course_id uuid not null,
          lesson_id uuid not null,
          progress_percent smallint not null check (progress_percent between 0 and 100),
          started_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          completed_at timestamptz,
          revision bigint not null default 1 check (revision > 0),
          primary key (seller_organization_id,enrollment_id,lesson_id),
          constraint lesson_progress_enrollment_fkey foreign key (
            seller_organization_id,enrollment_id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id
          ) references learning_enrollment(
            seller_organization_id,id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id
          ),
          constraint lesson_progress_publication_lesson_fkey foreign key (
            seller_organization_id,catalog_product_id,publication_id,course_id,lesson_id
          ) references publication_lesson(
            seller_organization_id,catalog_product_id,publication_id,course_id,lesson_id
          ),
          constraint lesson_progress_completion_check check (
            (progress_percent=100 and completed_at is not null)
            or (progress_percent<100 and completed_at is null)
          )
        )
        """,
        """
        create table course_learning_state (
          seller_organization_id uuid not null,
          enrollment_id uuid not null,
          buyer_identity_user_id uuid not null,
          entitlement_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          course_id uuid not null,
          last_lesson_id uuid,
          completed_lessons integer not null default 0 check (completed_lessons >= 0),
          total_lessons integer not null check (total_lessons >= 0),
          progress_percent smallint not null default 0 check (progress_percent between 0 and 100),
          state_version bigint not null default 0 check (state_version >= 0),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id,enrollment_id),
          constraint course_learning_state_enrollment_fkey foreign key (
            seller_organization_id,enrollment_id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id
          ) references learning_enrollment(
            seller_organization_id,id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id
          ),
          constraint course_learning_state_last_lesson_fkey foreign key (
            seller_organization_id,catalog_product_id,publication_id,course_id,last_lesson_id
          ) references publication_lesson(
            seller_organization_id,catalog_product_id,publication_id,course_id,lesson_id
          ),
          constraint course_learning_state_count_check check (
            completed_lessons <= total_lessons
            and progress_percent = case when total_lessons=0 then 0
              else floor(completed_lessons*100.0/total_lessons)::smallint end
          )
        )
        """,
        """
        create index learning_enrollment_buyer_entitlement_idx
          on learning_enrollment(buyer_identity_user_id,entitlement_id,course_id)
        """,
        """
        create index lesson_progress_buyer_entitlement_idx
          on lesson_progress(buyer_identity_user_id,entitlement_id,updated_at desc)
        """,
        """
        create index course_learning_state_entitlement_idx
          on course_learning_state(
            seller_organization_id,entitlement_id,updated_at desc,enrollment_id
          )
        """,
        """
        create table app_private.learning_progress_request (
          buyer_identity_user_id uuid not null references public.identity_user(id),
          idempotency_key uuid not null,
          seller_organization_id uuid not null,
          enrollment_id uuid not null,
          entitlement_id uuid not null,
          lesson_id uuid not null,
          body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
          result jsonb not null check (jsonb_typeof(result)='object'),
          created_at timestamptz not null default clock_timestamp(),
          primary key (buyer_identity_user_id,idempotency_key),
          constraint learning_progress_request_progress_fkey foreign key (
            seller_organization_id,enrollment_id,lesson_id
          ) references public.lesson_progress(
            seller_organization_id,enrollment_id,lesson_id
          )
        )
        """,
        "revoke all on table app_private.learning_progress_request from public,appsell_runtime",
        "alter table learning_enrollment enable row level security",
        "alter table learning_enrollment force row level security",
        "alter table lesson_progress enable row level security",
        "alter table lesson_progress force row level security",
        "alter table course_learning_state enable row level security",
        "alter table course_learning_state force row level security",
        "create policy learning_enrollment_owner_all on learning_enrollment to appsell_owner using (true) with check (true)",
        "create policy lesson_progress_owner_all on lesson_progress to appsell_owner using (true) with check (true)",
        "create policy course_learning_state_owner_all on course_learning_state to appsell_owner using (true) with check (true)",
        """
        create function app_private.learning_context_allowed(p_actor uuid,p_capability text)
        returns boolean language sql stable security definer
        set search_path=pg_catalog,app_private as $$
          select coalesce(p_actor is not null
            and p_capability in ('learning.player.read','learning.progress.write')
            and app_private.context_uuid('identity_user_id')=p_actor
            and app_private.context_text('capability')=p_capability
            and app_private.context_uuid('seller_organization_id') is null,false)
        $$
        """,
        "revoke all on function app_private.learning_context_allowed(uuid,text) from public",
        """
        create policy learning_enrollment_buyer_read on learning_enrollment
          for select to appsell_runtime using (
            buyer_identity_user_id=app_private.context_uuid('identity_user_id')
            and app_private.context_text('capability')='learning.player.read'
            and app_private.active_learning_entitlement(
              buyer_identity_user_id,seller_organization_id,entitlement_id,
              catalog_product_id,publication_id
            )
          )
        """,
        """
        create policy lesson_progress_buyer_read on lesson_progress
          for select to appsell_runtime using (
            buyer_identity_user_id=app_private.context_uuid('identity_user_id')
            and app_private.context_text('capability')='learning.player.read'
            and app_private.active_learning_entitlement(
              buyer_identity_user_id,seller_organization_id,entitlement_id,
              catalog_product_id,publication_id
            )
          )
        """,
        """
        create policy course_learning_state_buyer_read on course_learning_state
          for select to appsell_runtime using (
            buyer_identity_user_id=app_private.context_uuid('identity_user_id')
            and app_private.context_text('capability')='learning.player.read'
            and app_private.active_learning_entitlement(
              buyer_identity_user_id,seller_organization_id,entitlement_id,
              catalog_product_id,publication_id
            )
          )
        """,
        """
        create function app_private.sync_learning_enrollments()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          insert into public.learning_enrollment(
            seller_organization_id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id
          ) select new.seller_organization_id,new.buyer_identity_user_id,new.id,
              new.catalog_product_id,new.publication_id,projected.course_id
            from public.publication_course projected
            where projected.seller_organization_id=new.seller_organization_id
              and projected.catalog_product_id=new.catalog_product_id
              and projected.publication_id=new.publication_id
          on conflict(seller_organization_id,entitlement_id,course_id) do nothing;
          insert into public.course_learning_state(
            seller_organization_id,enrollment_id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id,total_lessons
          ) select enrollment.seller_organization_id,enrollment.id,
              enrollment.buyer_identity_user_id,enrollment.entitlement_id,
              enrollment.catalog_product_id,enrollment.publication_id,enrollment.course_id,
              projected.lesson_count
            from public.learning_enrollment enrollment
            join public.publication_course projected
              on projected.seller_organization_id=enrollment.seller_organization_id
             and projected.catalog_product_id=enrollment.catalog_product_id
             and projected.publication_id=enrollment.publication_id
             and projected.course_id=enrollment.course_id
            where enrollment.seller_organization_id=new.seller_organization_id
              and enrollment.entitlement_id=new.id
          on conflict(seller_organization_id,enrollment_id) do nothing;
          return new;
        end $$
        """,
        "revoke all on function app_private.sync_learning_enrollments() from public,appsell_runtime",
        """
        create trigger buyer_entitlement_learning_after_write
          after insert or update of publication_id on buyer_entitlement for each row
          execute function app_private.sync_learning_enrollments()
        """,
        """
        insert into learning_enrollment(
          seller_organization_id,buyer_identity_user_id,entitlement_id,
          catalog_product_id,publication_id,course_id
        ) select entitlement.seller_organization_id,entitlement.buyer_identity_user_id,
            entitlement.id,entitlement.catalog_product_id,entitlement.publication_id,
            projected.course_id
          from buyer_entitlement entitlement
          join publication_course projected
            on projected.seller_organization_id=entitlement.seller_organization_id
           and projected.catalog_product_id=entitlement.catalog_product_id
           and projected.publication_id=entitlement.publication_id
        on conflict(seller_organization_id,entitlement_id,course_id) do nothing
        """,
        """
        insert into course_learning_state(
          seller_organization_id,enrollment_id,buyer_identity_user_id,entitlement_id,
          catalog_product_id,publication_id,course_id,total_lessons
        ) select enrollment.seller_organization_id,enrollment.id,
            enrollment.buyer_identity_user_id,enrollment.entitlement_id,
            enrollment.catalog_product_id,enrollment.publication_id,enrollment.course_id,
            projected.lesson_count
          from learning_enrollment enrollment
          join publication_course projected
            on projected.seller_organization_id=enrollment.seller_organization_id
           and projected.catalog_product_id=enrollment.catalog_product_id
           and projected.publication_id=enrollment.publication_id
           and projected.course_id=enrollment.course_id
        on conflict(seller_organization_id,enrollment_id) do nothing
        """,
        """
        create function app_private.read_learning_player(
          p_actor uuid,p_entitlement uuid,p_lesson uuid default null
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare selected record; selected_lesson uuid; resume_lesson uuid;
          course_payload jsonb; progress_payload jsonb; asset_payload jsonb;
        begin
          if not app_private.learning_context_allowed(p_actor,'learning.player.read')
             or p_entitlement is null then
            raise exception 'learning context is not authorized' using errcode='28000';
          end if;
          select entitlement.seller_organization_id,enrollment.id enrollment_id,
            entitlement.id entitlement_id,entitlement.catalog_product_id,
            entitlement.publication_id,enrollment.course_id,
            organization.id seller_id,product.id product_id,
            publication.snapshot->'product' product_snapshot,
            publication.version publication_version,
            publication.source_sha256,state.last_lesson_id,state.progress_percent,
            state.state_version,state.updated_at
          into selected
          from buyer_entitlement entitlement
          join learning_enrollment enrollment
            on enrollment.seller_organization_id=entitlement.seller_organization_id
           and enrollment.entitlement_id=entitlement.id
          join seller_organization organization on organization.id=entitlement.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          join publication
            on publication.seller_organization_id=entitlement.seller_organization_id
           and publication.catalog_product_id=entitlement.catalog_product_id
           and publication.id=entitlement.publication_id
          join course_learning_state state
            on state.seller_organization_id=enrollment.seller_organization_id
           and state.enrollment_id=enrollment.id
          where entitlement.id=p_entitlement
            and entitlement.buyer_identity_user_id=p_actor
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published'
            and (p_lesson is null or exists(
              select 1 from publication_lesson projected
              where projected.seller_organization_id=enrollment.seller_organization_id
                and projected.catalog_product_id=enrollment.catalog_product_id
                and projected.publication_id=enrollment.publication_id
                and projected.course_id=enrollment.course_id
                and projected.lesson_id=p_lesson
            ))
          order by
            case when p_lesson is null and exists(
              select 1 from publication_lesson candidate
              where candidate.seller_organization_id=enrollment.seller_organization_id
                and candidate.publication_id=enrollment.publication_id
                and candidate.course_id=enrollment.course_id
                and not exists(
                  select 1 from lesson_progress completed
                  where completed.seller_organization_id=enrollment.seller_organization_id
                    and completed.enrollment_id=enrollment.id
                    and completed.lesson_id=candidate.lesson_id
                    and completed.progress_percent=100
                )
            ) then 0 else 1 end,
            case when p_lesson is null and exists(
              select 1 from lesson_progress active_progress
              where active_progress.seller_organization_id=enrollment.seller_organization_id
                and active_progress.enrollment_id=enrollment.id
                and active_progress.progress_percent between 1 and 99
            ) then 0 else 1 end,
            case when p_lesson is null and exists(
              select 1 from lesson_progress active_progress
              where active_progress.seller_organization_id=enrollment.seller_organization_id
                and active_progress.enrollment_id=enrollment.id
                and active_progress.progress_percent between 1 and 99
            ) then state.updated_at end desc nulls last,
            (select position from publication_course projected
              where projected.seller_organization_id=enrollment.seller_organization_id
                and projected.publication_id=enrollment.publication_id
                and projected.course_id=enrollment.course_id),enrollment.id
          limit 1;
          if not found then raise exception 'learning item is unavailable' using errcode='28000'; end if;
          select coalesce(p_lesson,(
            select selected.last_lesson_id
            from lesson_progress progress
            where progress.seller_organization_id=selected.seller_organization_id
              and progress.enrollment_id=selected.enrollment_id
              and progress.lesson_id=selected.last_lesson_id
              and progress.progress_percent<100
          ),(
            select projected.lesson_id from publication_lesson projected
            where projected.seller_organization_id=selected.seller_organization_id
              and projected.publication_id=selected.publication_id
              and projected.course_id=selected.course_id
              and not exists(
                select 1 from lesson_progress completed
                where completed.seller_organization_id=selected.seller_organization_id
                  and completed.enrollment_id=selected.enrollment_id
                  and completed.lesson_id=projected.lesson_id
                  and completed.progress_percent=100
              )
            order by projected.module_position,projected.lesson_position,
              projected.lesson_id limit 1
          ),(
            select projected.lesson_id from publication_lesson projected
            where projected.seller_organization_id=selected.seller_organization_id
              and projected.publication_id=selected.publication_id
              and projected.course_id=selected.course_id
            order by projected.module_position,projected.lesson_position,projected.lesson_id limit 1
          )) into selected_lesson;
          if selected_lesson is null then
            raise exception 'learning course has no lessons' using errcode='28000';
          end if;
          resume_lesson:=selected_lesson;
          select course_item into course_payload
          from publication source,
            lateral jsonb_array_elements(source.snapshot->'courses') course_item
          where source.seller_organization_id=selected.seller_organization_id
            and source.id=selected.publication_id
            and course_item->>'id'=selected.course_id::text;
          select course_payload || jsonb_build_object('modules',coalesce((
            select jsonb_agg(
              module_item || jsonb_build_object('lessons',coalesce((
                select jsonb_agg(
                  case when lesson_item->>'id'=selected_lesson::text then lesson_item
                    else jsonb_set(lesson_item,'{content}','""'::jsonb,true) end
                  order by lesson_ordinality
                )
                from jsonb_array_elements(module_item->'lessons')
                  with ordinality lessons(lesson_item,lesson_ordinality)
              ),'[]'::jsonb))
              order by module_ordinality
            )
            from jsonb_array_elements(course_payload->'modules')
              with ordinality modules(module_item,module_ordinality)
          ),'[]'::jsonb)) into course_payload;
          select coalesce(jsonb_agg(jsonb_build_object(
            'lesson_id',progress.lesson_id,'progress_percent',progress.progress_percent,
            'completed',progress.progress_percent=100,'updated_at',progress.updated_at
          ) order by projected.module_position,projected.lesson_position),'[]'::jsonb)
          into progress_payload
          from lesson_progress progress
          join publication_lesson projected
            on projected.seller_organization_id=progress.seller_organization_id
           and projected.publication_id=progress.publication_id
           and projected.lesson_id=progress.lesson_id
          where progress.seller_organization_id=selected.seller_organization_id
            and progress.enrollment_id=selected.enrollment_id;
          select jsonb_build_object('id',asset.id,'title',asset.title,
            'media_type',asset.media_type) into asset_payload
          from learning_asset asset
          where asset.seller_organization_id=selected.seller_organization_id
            and asset.catalog_product_id=selected.catalog_product_id
            and asset.lesson_id=selected_lesson and asset.status='active'
          order by asset.position,asset.id limit 1;
          return jsonb_build_object(
            'seller',jsonb_build_object('id',selected.seller_id),
            'product',selected.product_snapshot,
            'publication',jsonb_build_object('id',selected.publication_id,
              'version',selected.publication_version,'source_sha256',selected.source_sha256),
            'entitlement_id',selected.entitlement_id,'enrollment_id',selected.enrollment_id,
            'course',course_payload,'selected_lesson_id',selected_lesson,
            'resume_lesson_id',resume_lesson,
            'course_progress_percent',selected.progress_percent,
            'state_version',selected.state_version,'lesson_progress',progress_payload,
            'selected_asset',asset_payload
          );
        end $$
        """,
        "revoke all on function app_private.read_learning_player(uuid,uuid,uuid) from public",
        "grant execute on function app_private.read_learning_player(uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.record_lesson_progress(
          p_actor uuid,p_entitlement uuid,p_lesson uuid,p_progress smallint,
          p_expected_version bigint,p_idempotency uuid,p_body_sha text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare locked record; state_row course_learning_state%rowtype;
          existing app_private.learning_progress_request%rowtype;
          lesson_percent smallint; prior_lesson_percent smallint;
          completed_count integer; course_percent smallint; entitlement_percent smallint;
          response jsonb; resume_lesson uuid;
        begin
          if not app_private.learning_context_allowed(p_actor,'learning.progress.write')
             or p_entitlement is null or p_lesson is null or p_progress is null
             or p_progress not between 0 and 100
             or p_expected_version is null or p_expected_version<0 or p_idempotency is null
             or p_request is null or p_body_sha is null or p_body_sha!~'^[0-9a-f]{64}$' then
            raise exception 'invalid learning progress request' using errcode='22023';
          end if;
          select entitlement.seller_organization_id,enrollment.id enrollment_id,
            entitlement.id entitlement_id,entitlement.catalog_product_id,
            entitlement.publication_id,enrollment.course_id
          into locked
          from buyer_entitlement entitlement
          join learning_enrollment enrollment
            on enrollment.seller_organization_id=entitlement.seller_organization_id
           and enrollment.entitlement_id=entitlement.id
          join seller_organization organization on organization.id=entitlement.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          join publication_lesson projected
            on projected.seller_organization_id=enrollment.seller_organization_id
           and projected.catalog_product_id=enrollment.catalog_product_id
           and projected.publication_id=enrollment.publication_id
           and projected.course_id=enrollment.course_id and projected.lesson_id=p_lesson
          where entitlement.id=p_entitlement and entitlement.buyer_identity_user_id=p_actor
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published'
          for update of entitlement for share of organization,product;
          if not found then raise exception 'learning item is unavailable' using errcode='28000'; end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.learning.progress:'||p_actor::text||':'||p_idempotency::text,0));
          select * into existing from app_private.learning_progress_request request
          where request.buyer_identity_user_id=p_actor and request.idempotency_key=p_idempotency;
          if found then
            if existing.seller_organization_id=locked.seller_organization_id
               and existing.enrollment_id=locked.enrollment_id
               and existing.entitlement_id=p_entitlement and existing.lesson_id=p_lesson
               and existing.body_sha256=p_body_sha then
              return jsonb_set(existing.result,'{applied}','false'::jsonb,true);
            end if;
            raise exception 'learning idempotency conflict' using errcode='23505';
          end if;
          select * into state_row from course_learning_state state
          where state.seller_organization_id=locked.seller_organization_id
            and state.enrollment_id=locked.enrollment_id for update;
          if state_row.state_version<>p_expected_version then
            raise exception 'learning progress version conflict' using errcode='40001';
          end if;
          select progress.progress_percent into prior_lesson_percent
          from lesson_progress progress
          where progress.seller_organization_id=locked.seller_organization_id
            and progress.enrollment_id=locked.enrollment_id
            and progress.lesson_id=p_lesson;
          prior_lesson_percent:=coalesce(prior_lesson_percent,0);
          insert into lesson_progress(
            seller_organization_id,enrollment_id,buyer_identity_user_id,entitlement_id,
            catalog_product_id,publication_id,course_id,lesson_id,progress_percent,completed_at
          ) values(
            locked.seller_organization_id,locked.enrollment_id,p_actor,p_entitlement,
            locked.catalog_product_id,locked.publication_id,locked.course_id,p_lesson,
            p_progress,case when p_progress=100 then clock_timestamp() end
          ) on conflict(seller_organization_id,enrollment_id,lesson_id) do update set
            progress_percent=greatest(lesson_progress.progress_percent,excluded.progress_percent),
            completed_at=case when greatest(lesson_progress.progress_percent,excluded.progress_percent)=100
              then coalesce(lesson_progress.completed_at,clock_timestamp()) end,
            updated_at=clock_timestamp(),revision=lesson_progress.revision+1
          returning progress_percent into lesson_percent;
          select count(*) into completed_count from lesson_progress progress
          where progress.seller_organization_id=locked.seller_organization_id
            and progress.enrollment_id=locked.enrollment_id and progress.progress_percent=100;
          course_percent:=case when state_row.total_lessons=0 then 0
            else floor(completed_count*100.0/state_row.total_lessons)::smallint end;
          update course_learning_state set last_lesson_id=p_lesson,
            completed_lessons=completed_count,progress_percent=course_percent,
            state_version=state_version+1,updated_at=clock_timestamp()
          where seller_organization_id=locked.seller_organization_id
            and enrollment_id=locked.enrollment_id
          returning state_version into p_expected_version;
          select case when sum(state.total_lessons)=0 then 0
            else floor(sum(state.completed_lessons)*100.0/sum(state.total_lessons))::smallint end
          into entitlement_percent
          from course_learning_state state
          where state.seller_organization_id=locked.seller_organization_id
            and state.entitlement_id=p_entitlement;
          update buyer_entitlement set progress_percent=entitlement_percent,
            updated_at=clock_timestamp()
          where seller_organization_id=locked.seller_organization_id and id=p_entitlement;
          if lesson_percent<100 then
            resume_lesson:=p_lesson;
          else
            select projected.lesson_id into resume_lesson
            from publication_lesson projected
            where projected.seller_organization_id=locked.seller_organization_id
              and projected.catalog_product_id=locked.catalog_product_id
              and projected.publication_id=locked.publication_id
              and not exists(
                select 1 from lesson_progress progress
                where progress.seller_organization_id=projected.seller_organization_id
                  and progress.entitlement_id=p_entitlement
                  and progress.lesson_id=projected.lesson_id
                  and progress.progress_percent=100
              )
            order by projected.course_position,projected.module_position,
              projected.lesson_position,projected.lesson_id limit 1;
            resume_lesson:=coalesce(resume_lesson,p_lesson);
          end if;
          update course_learning_state set last_lesson_id=case when exists(
              select 1 from publication_lesson projected
              where projected.seller_organization_id=locked.seller_organization_id
                and projected.publication_id=locked.publication_id
                and projected.course_id=locked.course_id
                and projected.lesson_id=resume_lesson
            ) then resume_lesson end
          where seller_organization_id=locked.seller_organization_id
            and enrollment_id=locked.enrollment_id;
          response:=jsonb_build_object(
            'entitlement_id',p_entitlement,'enrollment_id',locked.enrollment_id,
            'lesson_id',p_lesson,'lesson_progress_percent',lesson_percent,
            'course_progress_percent',course_percent,'resume_lesson_id',resume_lesson,
            'state_version',p_expected_version,'applied',true
          );
          insert into app_private.learning_progress_request(
            buyer_identity_user_id,idempotency_key,seller_organization_id,enrollment_id,
            entitlement_id,lesson_id,body_sha256,result
          ) values(p_actor,p_idempotency,locked.seller_organization_id,locked.enrollment_id,
            p_entitlement,p_lesson,p_body_sha,response);
          if p_progress=100 and prior_lesson_percent<100 then
            insert into audit_event(
              seller_organization_id,actor_identity_user_id,action,target_type,
              target_id,result,request_id,metadata
            ) values(locked.seller_organization_id,p_actor,'learning.lesson_completed',
              'lesson',p_lesson,'succeeded',p_request,
              jsonb_build_object('entitlement_id',p_entitlement,
                'publication_id',locked.publication_id,'course_id',locked.course_id));
          end if;
          return response;
        end $$
        """,
        "revoke all on function app_private.record_lesson_progress(uuid,uuid,uuid,smallint,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.record_lesson_progress(uuid,uuid,uuid,smallint,bigint,uuid,text,uuid) to appsell_runtime",
        """
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
        """,
        """
        create or replace function app_private.library_context_allowed(
          p_identity_user_id uuid,p_capability text
        ) returns boolean
        language sql stable security definer
        set search_path=pg_catalog,app_private,public as $$
          select coalesce(p_identity_user_id is not null
            and p_capability in ('library.read','library.asset.read')
            and app_private.context_uuid('identity_user_id')=p_identity_user_id
            and app_private.context_uuid('seller_organization_id') is null
            and app_private.context_text('capability')=p_capability,false)
        $$
        """,
        "drop function app_private.authorize_learning_asset(uuid,uuid,uuid)",
        """
        create function app_private.authorize_learning_asset(
          p_identity_user_id uuid,p_entitlement_id uuid,p_asset_id uuid
        ) returns table(asset_id uuid,seed_key text,file_name text,title text,
          media_type text,byte_size integer,sha256 text)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if not app_private.library_context_allowed(p_identity_user_id,'library.asset.read') then
            raise exception 'asset context is not authorized' using errcode='28000';
          end if;
          return query select asset.id,asset.seed_key,asset.file_name,asset.title,
            asset.media_type,asset.byte_size,asset.sha256
          from learning_asset asset
          join seller_organization organization on organization.id=asset.seller_organization_id
          join buyer_entitlement entitlement
            on entitlement.seller_organization_id=asset.seller_organization_id
           and entitlement.catalog_product_id=asset.catalog_product_id
           and entitlement.buyer_identity_user_id=p_identity_user_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          join publication_lesson projected
            on projected.seller_organization_id=asset.seller_organization_id
           and projected.publication_id=entitlement.publication_id
           and projected.lesson_id=asset.lesson_id
          where entitlement.id=p_entitlement_id
            and asset.id=p_asset_id and asset.status='active'
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          if not found then raise exception 'learning asset is unavailable' using errcode='28000'; end if;
        end $$
        """,
        "revoke all on function app_private.authorize_learning_asset(uuid,uuid,uuid) from public",
        "grant execute on function app_private.authorize_learning_asset(uuid,uuid,uuid) to appsell_runtime",
        "revoke select on learning_asset from appsell_runtime",
        "revoke select on publication_course,publication_lesson,learning_enrollment,lesson_progress,course_learning_state from appsell_runtime",
        "revoke insert,update,delete on publication_course,publication_lesson,learning_enrollment,lesson_progress,course_learning_state from appsell_runtime",
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.buyer_entitlement') is not null
            and to_regclass('public.publication_course') is not null
            and to_regclass('public.publication_lesson') is not null
            and to_regclass('public.learning_enrollment') is not null
            and to_regclass('public.lesson_progress') is not null
            and to_regclass('public.course_learning_state') is not null
            and to_regclass('app_private.learning_progress_request') is not null
            and exists(select 1 from public.alembic_version where version_num='0007_learning_progress')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
