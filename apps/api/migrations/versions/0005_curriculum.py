"""Create tenant-safe course curriculum and publication snapshots v2.

Revision ID: 0005_curriculum
Revises: 0004_product_publication
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_curriculum"
down_revision: str | None = "0004_product_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        create table course_module (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          course_id uuid not null,
          title text not null check (
            title = btrim(title) and octet_length(title) between 2 and 160
            and title !~ '[[:cntrl:]]'
          ),
          status text not null default 'active' check (status in ('active','archived')),
          position integer not null check (position >= 0),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, id),
          constraint course_module_course_fkey
            foreign key (seller_organization_id, course_id)
            references course (seller_organization_id, id),
          constraint course_module_seller_course_id_key
            unique (seller_organization_id, course_id, id),
          constraint course_module_position_key
            unique (seller_organization_id, course_id, position)
            deferrable initially immediate
        )
        """,
        """
        create index course_module_course_order_idx
          on course_module (seller_organization_id, course_id, status, position, id)
        """,
        """
        create table lesson (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          course_id uuid not null,
          module_id uuid not null,
          title text not null check (
            title = btrim(title) and octet_length(title) between 2 and 160
            and title !~ '[[:cntrl:]]'
          ),
          body_text text not null default '' check (
            octet_length(body_text) <= 8192
            and replace(replace(replace(body_text, E'\n', ''), E'\r', ''), E'\t', '')
                !~ '[[:cntrl:]]'
          ),
          status text not null default 'active' check (status in ('active','archived')),
          position integer not null check (position >= 0),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, id),
          constraint lesson_module_fkey
            foreign key (seller_organization_id, course_id, module_id)
            references course_module (seller_organization_id, course_id, id),
          constraint lesson_seller_course_id_key
            unique (seller_organization_id, course_id, id),
          constraint lesson_position_key
            unique (seller_organization_id, module_id, position)
            deferrable initially immediate
        )
        """,
        """
        create index lesson_module_order_idx
          on lesson (seller_organization_id, course_id, module_id, status, position, id)
        """,
        """
        create table app_private.curriculum_mutation_request (
          seller_organization_id uuid not null,
          operation text not null check (operation in (
            'module.create','module.update','module.archive','module.reorder',
            'lesson.create','lesson.update','lesson.archive','lesson.reorder'
          )),
          idempotency_key uuid not null,
          identity_user_id uuid not null references public.identity_user(id),
          body_sha256 text not null check (body_sha256 ~ '^[0-9a-f]{64}$'),
          catalog_product_id uuid not null,
          course_id uuid not null,
          resource_key text not null check (octet_length(resource_key) between 1 and 160),
          result jsonb not null check (jsonb_typeof(result) = 'object'),
          created_at timestamptz not null default clock_timestamp(),
          primary key (seller_organization_id, operation, idempotency_key),
          constraint curriculum_request_product_course_fkey
            foreign key (seller_organization_id, catalog_product_id, course_id)
            references public.product_course (
              seller_organization_id, catalog_product_id, course_id
            )
        )
        """,
        """
        create index curriculum_mutation_actor_idx
          on app_private.curriculum_mutation_request (
            identity_user_id, seller_organization_id, course_id, created_at desc
          )
        """,
        """
        create index curriculum_mutation_product_course_idx
          on app_private.curriculum_mutation_request (
            seller_organization_id, catalog_product_id, course_id, created_at desc
          )
        """,
        """
        create index curriculum_mutation_course_retention_idx
          on app_private.curriculum_mutation_request (
            seller_organization_id, course_id, created_at desc, operation, idempotency_key
          )
        """,
        "revoke all on table app_private.curriculum_mutation_request from public, appsell_runtime",
        "alter table course_module enable row level security",
        "alter table course_module force row level security",
        "alter table lesson enable row level security",
        "alter table lesson force row level security",
        """
        create policy course_module_owner_all on course_module to appsell_owner
          using (true) with check (true)
        """,
        """
        create policy lesson_owner_all on lesson to appsell_owner
          using (true) with check (true)
        """,
        """
        create function app_private.curriculum_action_allowed(
          p_seller_organization_id uuid, p_course_id uuid, p_capability text
        ) returns boolean
        language sql stable security definer
        set search_path = pg_catalog, app_private, public
        as $$
          select p_seller_organization_id is not null and p_course_id is not null
            and app_private.context_text('capability') = p_capability
            and app_private.context_uuid('seller_organization_id') = p_seller_organization_id
            and exists (
              select 1 from public.seller_organization organization
              join public.seller_membership membership
                on membership.seller_organization_id = organization.id
              where organization.id = p_seller_organization_id
                and organization.status = 'active'
                and membership.identity_user_id = app_private.context_uuid('identity_user_id')
                and membership.role in ('seller_owner','seller_admin','seller_editor')
                and membership.status = 'active'
            )
            and exists (
              select 1 from public.course candidate
              where candidate.seller_organization_id = p_seller_organization_id
                and candidate.id = p_course_id and candidate.status <> 'archived'
            )
        $$
        """,
        "revoke all on function app_private.curriculum_action_allowed(uuid,uuid,text) from public",
        "grant execute on function app_private.curriculum_action_allowed(uuid,uuid,text) to appsell_runtime",
        """
        create policy course_module_staff_read on course_module for select to appsell_runtime
          using (
            seller_organization_id = app_private.context_uuid('seller_organization_id')
            and app_private.curriculum_action_allowed(
              seller_organization_id, course_id, app_private.context_text('capability')
            )
            and app_private.context_text('capability') in (
              'catalog.curriculum.read','catalog.draft.read','catalog.draft.preview'
            )
          )
        """,
        """
        create policy lesson_staff_read on lesson for select to appsell_runtime
          using (
            seller_organization_id = app_private.context_uuid('seller_organization_id')
            and app_private.curriculum_action_allowed(
              seller_organization_id, course_id, app_private.context_text('capability')
            )
            and app_private.context_text('capability') in (
              'catalog.curriculum.read','catalog.draft.read','catalog.draft.preview'
            )
          )
        """,
        """
        create function app_private.lock_curriculum_access(
          p_identity_user_id uuid, p_seller_organization_id uuid,
          p_catalog_product_id uuid, p_course_id uuid, p_capability text
        ) returns void
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        begin
          if app_private.context_text('capability') <> p_capability
             or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is distinct from
                p_seller_organization_id then
            raise exception 'curriculum context is not authorized' using errcode = '28000';
          end if;
          perform 1 from public.seller_organization organization
          join public.seller_membership membership
            on membership.seller_organization_id = organization.id
          where organization.id = p_seller_organization_id
            and organization.status = 'active'
            and membership.identity_user_id = p_identity_user_id
            and membership.role in ('seller_owner','seller_admin','seller_editor')
            and membership.status = 'active'
          for share of organization, membership;
          if not found then
            raise exception 'curriculum actor is not authorized' using errcode = '28000';
          end if;
          perform 1 from public.product_course linked
          join public.course candidate
            on candidate.seller_organization_id = linked.seller_organization_id
           and candidate.id = linked.course_id
          where linked.seller_organization_id = p_seller_organization_id
            and linked.catalog_product_id = p_catalog_product_id
            and linked.course_id = p_course_id
            and candidate.status <> 'archived'
          for share of linked;
          if not found then
            raise exception 'curriculum course is unavailable' using errcode = '28000';
          end if;
        end
        $$
        """,
        "revoke all on function app_private.lock_curriculum_access(uuid,uuid,uuid,uuid,text) from public",
        """
        create function app_private.curriculum_content_bytes(p_seller uuid,p_course uuid)
        returns bigint language sql stable security definer
        set search_path=pg_catalog,public as $$
          select coalesce(sum(octet_length(body_text)),0)::bigint from public.lesson
          where seller_organization_id=p_seller and course_id=p_course and status='active'
        $$
        """,
        "revoke all on function app_private.curriculum_content_bytes(uuid,uuid) from public",
        """
        create function app_private.lock_curriculum_course(
          p_seller_organization_id uuid, p_course_id uuid, p_expected_version bigint
        ) returns bigint
        language plpgsql security definer
        set search_path = pg_catalog, public
        as $$
        declare current_version bigint;
        begin
          if p_expected_version is null or p_expected_version<1 then
            raise exception 'invalid curriculum version' using errcode='22023'; end if;
          select draft_version into current_version from public.course
          where seller_organization_id = p_seller_organization_id and id = p_course_id
            and status <> 'archived' for no key update;
          if not found then raise exception 'curriculum course is unavailable' using errcode='28000'; end if;
          if current_version <> p_expected_version then
            raise exception 'curriculum version conflict' using errcode='40001';
          end if;
          return current_version;
        end
        $$
        """,
        "revoke all on function app_private.lock_curriculum_course(uuid,uuid,bigint) from public",
        """
        create function app_private.curriculum_replay(
          p_seller uuid, p_operation text, p_key uuid, p_actor uuid,
          p_body_sha256 text, p_product uuid, p_course uuid, p_resource text
        ) returns jsonb
        language plpgsql security definer
        set search_path = pg_catalog, app_private
        as $$
        declare receipt app_private.curriculum_mutation_request%rowtype;
        begin
          select * into receipt from app_private.curriculum_mutation_request request
          where request.seller_organization_id=p_seller and request.operation=p_operation
            and request.idempotency_key=p_key;
          if not found then return null; end if;
          if receipt.identity_user_id=p_actor and receipt.body_sha256=p_body_sha256
             and receipt.catalog_product_id=p_product and receipt.course_id=p_course
             and receipt.resource_key=p_resource then
            return receipt.result;
          end if;
          raise exception 'curriculum idempotency conflict' using errcode='23505';
        end
        $$
        """,
        "revoke all on function app_private.curriculum_replay(uuid,text,uuid,uuid,text,uuid,uuid,text) from public",
        """
        create function app_private.record_curriculum_mutation(
          p_seller uuid, p_operation text, p_key uuid, p_actor uuid,
          p_body_sha256 text, p_product uuid, p_course uuid, p_resource text, p_result jsonb
        ) returns void
        language sql security definer
        set search_path = pg_catalog, app_private
        as $$
          insert into app_private.curriculum_mutation_request (
            seller_organization_id,operation,idempotency_key,identity_user_id,
            body_sha256,catalog_product_id,course_id,resource_key,result
          ) values (p_seller,p_operation,p_key,p_actor,p_body_sha256,p_product,p_course,p_resource,p_result);
          delete from app_private.curriculum_mutation_request request
          where request.seller_organization_id=p_seller and request.course_id=p_course
            and (request.created_at < clock_timestamp() - interval '30 days' or
              (request.operation,request.idempotency_key) in (
                select stale.operation,stale.idempotency_key
                from app_private.curriculum_mutation_request stale
                where stale.seller_organization_id=p_seller and stale.course_id=p_course
                order by stale.created_at desc,stale.operation,stale.idempotency_key
                offset 5000
              ))
        $$
        """,
        "revoke all on function app_private.record_curriculum_mutation(uuid,text,uuid,uuid,text,uuid,uuid,text,jsonb) from public",
        """
        create function app_private.read_course_curriculum(
          p_identity_user_id uuid, p_seller uuid, p_product uuid, p_course uuid
        ) returns table(
          course_version bigint, curriculum jsonb, module_total bigint,
          lesson_total bigint, lesson_totals_by_module jsonb
        )
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        begin
          perform app_private.lock_curriculum_access(
            p_identity_user_id,p_seller,p_product,p_course,'catalog.curriculum.read'
          );
          perform 1 from public.course candidate
          where candidate.seller_organization_id=p_seller and candidate.id=p_course
            and candidate.status<>'archived' for share;
          if not found then
            raise exception 'curriculum course is unavailable' using errcode='28000';
          end if;
          return query select candidate.draft_version,
            coalesce((
              select jsonb_agg(jsonb_build_object(
                'id',module.id,'title',module.title,'status','active',
                'position',module.position,
                'lessons',coalesce((
                  select jsonb_agg(jsonb_build_object(
                    'id',lesson.id,'title',lesson.title,'kind','text',
                    'content',lesson.body_text,'status','active','position',lesson.position
                  ) order by lesson.position,lesson.id)
                  from public.lesson lesson where lesson.seller_organization_id=p_seller
                    and lesson.course_id=p_course and lesson.module_id=module.id
                    and lesson.status='active'
                ),'[]'::jsonb)
              ) order by module.position,module.id)
              from public.course_module module where module.seller_organization_id=p_seller
                and module.course_id=p_course and module.status='active'
            ),'[]'::jsonb),
            (select count(*) from public.course_module usage_module
              where usage_module.seller_organization_id=p_seller
                and usage_module.course_id=p_course),
            (select count(*) from public.lesson usage_lesson
              where usage_lesson.seller_organization_id=p_seller
                and usage_lesson.course_id=p_course),
            coalesce((
              select jsonb_object_agg(usage.module_id,usage.total)
              from (
                select usage_lesson.module_id::text as module_id,count(*) as total
                from public.lesson usage_lesson
                where usage_lesson.seller_organization_id=p_seller
                  and usage_lesson.course_id=p_course
                group by usage_lesson.module_id
              ) usage
            ),'{}'::jsonb)
          from public.course candidate
          where candidate.seller_organization_id=p_seller and candidate.id=p_course;
        end
        $$
        """,
        "revoke all on function app_private.read_course_curriculum(uuid,uuid,uuid,uuid) from public",
        "grant execute on function app_private.read_course_curriculum(uuid,uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.create_course_module(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_title text,
          p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(module_id uuid,course_version bigint,created boolean)
        language plpgsql security definer
        set search_path = pg_catalog, app_private, public
        as $$
        declare replay jsonb; new_id uuid:=gen_random_uuid(); next_version bigint; next_position integer;
        begin
          if p_title is null or p_title<>btrim(p_title) or octet_length(p_title) not between 2 and 160
             or p_title~'[[:cntrl:]]' or p_key is null or p_request is null
             or p_body is null or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid module create request' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.create');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/module.create/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'module.create',p_key,p_actor,p_body,p_product,p_course,'course:'||p_course::text);
          if replay is not null then return query select (replay->>'module_id')::uuid,(replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          select count(*)::integer into next_position from public.course_module
            where seller_organization_id=p_seller and course_id=p_course and status='active';
          if (select count(*) from public.course_module
                where seller_organization_id=p_seller and course_id=p_course)>=50 then
            raise exception 'module limit exceeded' using errcode='22023'; end if;
          insert into public.course_module(seller_organization_id,id,course_id,title,position)
            values(p_seller,new_id,p_course,p_title,next_position);
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'module.create',p_key,p_actor,p_body,p_product,p_course,'course:'||p_course::text,
            jsonb_build_object('module_id',new_id,'course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.module_created','course_module',new_id,'succeeded',p_request,jsonb_build_object('course_id',p_course));
          return query select new_id,next_version,true;
        end $$
        """,
        "revoke all on function app_private.create_course_module(uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.create_course_module(uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.update_course_module(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_title text,
          p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(course_version bigint,updated boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; next_version bigint;
        begin
          if p_module is null or p_title is null or p_title<>btrim(p_title)
             or octet_length(p_title) not between 2 and 160 or p_title~'[[:cntrl:]]'
             or p_key is null or p_request is null or p_body is null or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid module update request' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.update');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/module.update/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'module.update',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text);
          if replay is not null then return query select (replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          update public.course_module set title=p_title,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and course_id=p_course and id=p_module and status='active';
          if not found then raise exception 'module unavailable' using errcode='28000'; end if;
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'module.update',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text,
            jsonb_build_object('module_id',p_module,'course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.module_updated','course_module',p_module,'succeeded',p_request,jsonb_build_object('course_id',p_course));
          return query select next_version,true;
        end $$
        """,
        "revoke all on function app_private.update_course_module(uuid,uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.update_course_module(uuid,uuid,uuid,uuid,uuid,text,bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.create_course_lesson(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_title text,p_text text,
          p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(lesson_id uuid,course_version bigint,created boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; new_id uuid:=gen_random_uuid(); next_version bigint; next_position integer;
        begin
          if p_module is null or p_title is null or p_title<>btrim(p_title)
             or octet_length(p_title) not between 2 and 160 or p_title~'[[:cntrl:]]'
             or p_text is null or octet_length(p_text)>8192
             or replace(replace(replace(p_text,E'\n',''),E'\r',''),E'\t','')~'[[:cntrl:]]'
             or p_key is null or p_request is null
             or p_body is null or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid lesson create request' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.create');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/lesson.create/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'lesson.create',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text);
          if replay is not null then return query select (replay->>'lesson_id')::uuid,(replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          perform 1 from public.course_module where seller_organization_id=p_seller and course_id=p_course
            and id=p_module and status='active' for key share;
          if not found then raise exception 'module unavailable' using errcode='28000'; end if;
          select count(*)::integer into next_position from public.lesson
            where seller_organization_id=p_seller and course_id=p_course and module_id=p_module and status='active';
          if (select count(*) from public.lesson where seller_organization_id=p_seller
                and course_id=p_course and module_id=p_module)>=100
             or (select count(*) from public.lesson where seller_organization_id=p_seller
                  and course_id=p_course)>=500
             or app_private.curriculum_content_bytes(p_seller,p_course)+octet_length(p_text)>800000 then
            raise exception 'lesson limit exceeded' using errcode='22023'; end if;
          insert into public.lesson(seller_organization_id,id,course_id,module_id,title,body_text,position)
            values(p_seller,new_id,p_course,p_module,p_title,p_text,next_position);
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'lesson.create',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text,
            jsonb_build_object('lesson_id',new_id,'course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.lesson_created','lesson',new_id,'succeeded',p_request,jsonb_build_object('course_id',p_course,'module_id',p_module));
          return query select new_id,next_version,true;
        end $$
        """,
        "revoke all on function app_private.create_course_lesson(uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.create_course_lesson(uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.update_course_lesson(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid,
          p_title text,p_text text,p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(course_version bigint,updated boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; next_version bigint;
        begin
          if p_module is null or p_lesson is null or p_title is null or p_title<>btrim(p_title)
             or octet_length(p_title) not between 2 and 160 or p_title~'[[:cntrl:]]'
             or p_text is null or octet_length(p_text)>8192
             or replace(replace(replace(p_text,E'\n',''),E'\r',''),E'\t','')~'[[:cntrl:]]'
             or p_key is null or p_request is null
             or p_body is null or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid lesson update request' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.update');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/lesson.update/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'lesson.update',p_key,p_actor,p_body,p_product,p_course,
            'module:'||p_module::text||'/lesson:'||p_lesson::text);
          if replay is not null then return query select (replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          if app_private.curriculum_content_bytes(p_seller,p_course)
               - coalesce((select octet_length(body_text) from public.lesson
                   where seller_organization_id=p_seller and course_id=p_course
                     and module_id=p_module and id=p_lesson and status='active'),0)
               + octet_length(p_text)>800000 then
            raise exception 'course text limit exceeded' using errcode='22023'; end if;
          update public.lesson set title=p_title,body_text=p_text,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and course_id=p_course and module_id=p_module
              and id=p_lesson and status='active';
          if not found then raise exception 'lesson unavailable' using errcode='28000'; end if;
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'lesson.update',p_key,p_actor,p_body,p_product,p_course,
            'module:'||p_module::text||'/lesson:'||p_lesson::text,
            jsonb_build_object('lesson_id',p_lesson,'course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.lesson_updated','lesson',p_lesson,'succeeded',p_request,jsonb_build_object('course_id',p_course,'module_id',p_module));
          return query select next_version,true;
        end $$
        """,
        "revoke all on function app_private.update_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.update_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,text,text,bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.archive_course_module(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(course_version bigint,archived boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; next_version bigint; archived_position integer;
        begin
          if p_module is null or p_key is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' then raise exception 'invalid module archive request' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.archive');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/module.archive/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'module.archive',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text);
          if replay is not null then return query select (replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          perform 1 from public.course_module where seller_organization_id=p_seller and course_id=p_course
            and id=p_module and status='active' for no key update;
          if not found then raise exception 'module unavailable' using errcode='28000'; end if;
          select coalesce(max(position),-1)+1 into archived_position from public.course_module
            where seller_organization_id=p_seller and course_id=p_course;
          set constraints course_module_position_key deferred;
          update public.course_module set status='archived',position=archived_position,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and course_id=p_course and id=p_module;
          with ordered as (
            select id,row_number() over(order by position,id)-1 as next_position
            from public.course_module where seller_organization_id=p_seller and course_id=p_course and status='active'
          ) update public.course_module target set position=ordered.next_position,updated_at=clock_timestamp()
            from ordered where target.seller_organization_id=p_seller and target.id=ordered.id;
          update public.lesson set status='archived',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and course_id=p_course and module_id=p_module and status='active';
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'module.archive',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text,
            jsonb_build_object('module_id',p_module,'course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.module_archived','course_module',p_module,'succeeded',p_request,jsonb_build_object('course_id',p_course));
          return query select next_version,true;
        end $$
        """,
        "revoke all on function app_private.archive_course_module(uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.archive_course_module(uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.archive_course_lesson(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid,
          p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(course_version bigint,archived boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; next_version bigint; archived_position integer;
        begin
          if p_module is null or p_lesson is null or p_key is null or p_request is null
             or p_body is null or p_body!~'^[0-9a-f]{64}$' then raise exception 'invalid lesson archive request' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.archive');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/lesson.archive/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'lesson.archive',p_key,p_actor,p_body,p_product,p_course,
            'module:'||p_module::text||'/lesson:'||p_lesson::text);
          if replay is not null then return query select (replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          perform 1 from public.lesson where seller_organization_id=p_seller and course_id=p_course
            and module_id=p_module and id=p_lesson and status='active' for no key update;
          if not found then raise exception 'lesson unavailable' using errcode='28000'; end if;
          select coalesce(max(position),-1)+1 into archived_position from public.lesson
            where seller_organization_id=p_seller and module_id=p_module;
          set constraints lesson_position_key deferred;
          update public.lesson set status='archived',position=archived_position,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and course_id=p_course and module_id=p_module and id=p_lesson;
          with ordered as (
            select id,row_number() over(order by position,id)-1 as next_position from public.lesson
            where seller_organization_id=p_seller and course_id=p_course and module_id=p_module and status='active'
          ) update public.lesson target set position=ordered.next_position,updated_at=clock_timestamp()
            from ordered where target.seller_organization_id=p_seller and target.id=ordered.id;
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'lesson.archive',p_key,p_actor,p_body,p_product,p_course,
            'module:'||p_module::text||'/lesson:'||p_lesson::text,
            jsonb_build_object('lesson_id',p_lesson,'course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.lesson_archived','lesson',p_lesson,'succeeded',p_request,jsonb_build_object('course_id',p_course,'module_id',p_module));
          return query select next_version,true;
        end $$
        """,
        "revoke all on function app_private.archive_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.archive_course_lesson(uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.reorder_course_modules(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_order uuid[],
          p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(course_version bigint,reordered boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; next_version bigint; existing_count integer;
        begin
          if p_order is null or cardinality(p_order)>50 or p_key is null or p_request is null
             or p_body is null or p_body!~'^[0-9a-f]{64}$'
             or exists(select 1 from unnest(p_order) item where item is null)
             or cardinality(p_order)<>(select count(distinct item) from unnest(p_order) item) then
            raise exception 'invalid module order' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.reorder');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/module.reorder/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'module.reorder',p_key,p_actor,p_body,p_product,p_course,'course:'||p_course::text);
          if replay is not null then return query select (replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          select count(*) into existing_count from public.course_module where seller_organization_id=p_seller
            and course_id=p_course and status='active';
          if existing_count<>cardinality(p_order) or exists(
            select 1 from unnest(p_order) item left join public.course_module module
              on module.seller_organization_id=p_seller and module.course_id=p_course
             and module.id=item and module.status='active' where module.id is null
          ) then raise exception 'module order must contain the exact active set' using errcode='22023'; end if;
          set constraints course_module_position_key deferred;
          update public.course_module target set position=ordered.position,updated_at=clock_timestamp()
          from (select item,row_number() over(order by ordinality)-1 as position
                from unnest(p_order) with ordinality input(item,ordinality)) ordered
          where target.seller_organization_id=p_seller and target.course_id=p_course and target.id=ordered.item;
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'module.reorder',p_key,p_actor,p_body,p_product,p_course,'course:'||p_course::text,
            jsonb_build_object('course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.modules_reordered','course',p_course,'succeeded',p_request,jsonb_build_object('count',cardinality(p_order)));
          return query select next_version,true;
        end $$
        """,
        "revoke all on function app_private.reorder_course_modules(uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.reorder_course_modules(uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.reorder_module_lessons(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_order uuid[],
          p_expected_version bigint,p_key uuid,p_body text,p_request uuid
        ) returns table(course_version bigint,reordered boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; next_version bigint; existing_count integer;
        begin
          if p_module is null or p_order is null or cardinality(p_order)>100
             or p_key is null or p_request is null
             or p_body is null or p_body!~'^[0-9a-f]{64}$'
             or exists(select 1 from unnest(p_order) item where item is null)
             or cardinality(p_order)<>(select count(distinct item) from unnest(p_order) item) then
            raise exception 'invalid lesson order' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,'catalog.curriculum.reorder');
          perform pg_advisory_xact_lock(hashtextextended('appsell.curriculum/'||p_seller::text||'/lesson.reorder/'||p_key::text,0));
          replay:=app_private.curriculum_replay(p_seller,'lesson.reorder',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text);
          if replay is not null then return query select (replay->>'course_version')::bigint,false; return; end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_version);
          perform 1 from public.course_module where seller_organization_id=p_seller and course_id=p_course
            and id=p_module and status='active' for key share;
          if not found then raise exception 'module unavailable' using errcode='28000'; end if;
          select count(*) into existing_count from public.lesson where seller_organization_id=p_seller
            and course_id=p_course and module_id=p_module and status='active';
          if existing_count<>cardinality(p_order) or exists(
            select 1 from unnest(p_order) item left join public.lesson lesson
              on lesson.seller_organization_id=p_seller and lesson.course_id=p_course
             and lesson.module_id=p_module and lesson.id=item and lesson.status='active' where lesson.id is null
          ) then raise exception 'lesson order must contain the exact active set' using errcode='22023'; end if;
          set constraints lesson_position_key deferred;
          update public.lesson target set position=ordered.position,updated_at=clock_timestamp()
          from (select item,row_number() over(order by ordinality)-1 as position
                from unnest(p_order) with ordinality input(item,ordinality)) ordered
          where target.seller_organization_id=p_seller and target.course_id=p_course
            and target.module_id=p_module and target.id=ordered.item;
          update public.course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course returning draft_version into next_version;
          perform app_private.record_curriculum_mutation(p_seller,'lesson.reorder',p_key,p_actor,p_body,p_product,p_course,'module:'||p_module::text,
            jsonb_build_object('course_version',next_version));
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.lessons_reordered','course_module',p_module,'succeeded',p_request,jsonb_build_object('course_id',p_course,'count',cardinality(p_order)));
          return query select next_version,true;
        end $$
        """,
        "revoke all on function app_private.reorder_module_lessons(uuid,uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.reorder_module_lessons(uuid,uuid,uuid,uuid,uuid,uuid[],bigint,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.curriculum_courses_snapshot(p_seller uuid,p_product uuid)
        returns jsonb language sql stable security definer
        set search_path=pg_catalog,public
        as $$
          select coalesce(jsonb_agg(jsonb_build_object(
            'id',course.id,'title',course.title,'description',course.description,
            'position',linked.position,
            'modules',coalesce((
              select jsonb_agg(jsonb_build_object(
                'id',module.id,'title',module.title,'status','active',
                'position',module.position,
                'lessons',coalesce((
                  select jsonb_agg(jsonb_build_object(
                    'id',lesson.id,'title',lesson.title,'kind','text',
                    'content',lesson.body_text,'status','active','position',lesson.position
                  ) order by lesson.position,lesson.id)
                  from public.lesson lesson where lesson.seller_organization_id=p_seller
                    and lesson.course_id=course.id and lesson.module_id=module.id
                    and lesson.status='active'
                ),'[]'::jsonb)
              ) order by module.position,module.id)
              from public.course_module module where module.seller_organization_id=p_seller
                and module.course_id=course.id and module.status='active'
            ),'[]'::jsonb)
          ) order by linked.position,course.id),'[]'::jsonb)
          from public.product_course linked join public.course course
            on course.seller_organization_id=linked.seller_organization_id and course.id=linked.course_id
          where linked.seller_organization_id=p_seller and linked.catalog_product_id=p_product
        $$
        """,
        "revoke all on function app_private.curriculum_courses_snapshot(uuid,uuid) from public",
        "alter table publication drop constraint publication_schema_version_check",
        "alter table publication add constraint publication_schema_version_check check (schema_version in (1,2))",
        """
        create or replace function app_private.publish_product(
          p_identity_user_id uuid,p_seller_organization_id uuid,p_catalog_product_id uuid,
          p_offer_id uuid,p_expected_product_version bigint,p_expected_offer_version bigint,
          p_expected_course_versions jsonb,p_idempotency_key uuid,p_body_sha256 text,p_request_id uuid
        ) returns table(publication_id uuid,publication_version bigint,product_version bigint,
                        source_sha256 text,created boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare
          existing_request app_private.catalog_mutation_request%rowtype;
          locked_product public.catalog_product%rowtype;
          locked_offer public.offer%rowtype;
          expected_key text; expected_value text; linked_course_count integer;
          expected_course_count integer; next_publication_id uuid:=gen_random_uuid();
          next_publication_version bigint; next_product_version bigint;
          generated_snapshot jsonb; generated_source_sha256 text; response jsonb;
        begin
          if app_private.context_text('capability')<>'catalog.publication.create'
             or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
             or app_private.context_uuid('seller_organization_id') is distinct from p_seller_organization_id then
            raise exception 'catalog publication context is not authorized' using errcode='28000'; end if;
          if p_identity_user_id is null or p_seller_organization_id is null
             or p_catalog_product_id is null or p_offer_id is null
             or p_expected_product_version is null or p_expected_product_version<1
             or p_expected_offer_version is null or p_expected_offer_version<1
             or p_expected_course_versions is null or jsonb_typeof(p_expected_course_versions)<>'object'
             or p_expected_course_versions='{}'::jsonb or p_idempotency_key is null or p_request_id is null
             or p_body_sha256 is null or p_body_sha256!~'^[0-9a-f]{64}$' then
            raise exception 'invalid catalog publication request' using errcode='22023'; end if;
          for expected_key,expected_value in select key,value from jsonb_each_text(p_expected_course_versions) loop
            if expected_key!~'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               or expected_value!~'^[1-9][0-9]{0,18}$' then
              raise exception 'invalid expected course versions' using errcode='22023'; end if;
          end loop;
          select count(*) into expected_course_count from jsonb_object_keys(p_expected_course_versions);
          perform 1 from public.seller_organization organization join public.seller_membership membership
            on membership.seller_organization_id=organization.id
          where organization.id=p_seller_organization_id and organization.status='active'
            and membership.identity_user_id=p_identity_user_id
            and membership.role='seller_owner' and membership.status='active'
          for share of organization,membership;
          if not found then raise exception 'catalog publication actor is not authorized' using errcode='28000'; end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.catalog.mutation:'||p_seller_organization_id::text||'/product.publish/'||p_idempotency_key::text,0));
          select * into existing_request from app_private.catalog_mutation_request request
          where request.seller_organization_id=p_seller_organization_id and request.operation='product.publish'
            and request.idempotency_key=p_idempotency_key;
          if found then
            if existing_request.identity_user_id=p_identity_user_id and existing_request.body_sha256=p_body_sha256
               and existing_request.catalog_product_id=p_catalog_product_id and existing_request.offer_id=p_offer_id then
              return query select existing_request.publication_id,
                (existing_request.result->>'publication_version')::bigint,
                (existing_request.result->>'product_version')::bigint,
                existing_request.result->>'source_sha256',false; return;
            end if;
            raise exception 'catalog publication idempotency conflict' using errcode='23505';
          end if;
          select * into locked_product from public.catalog_product product
            where product.seller_organization_id=p_seller_organization_id and product.id=p_catalog_product_id for update;
          if not found then raise exception 'catalog product is unavailable' using errcode='28000'; end if;
          if locked_product.status='archived' then raise exception 'archived catalog product cannot be published' using errcode='55000'; end if;
          if locked_product.version<>p_expected_product_version then raise exception 'catalog product version conflict' using errcode='40001'; end if;
          select * into locked_offer from public.offer candidate
            where candidate.seller_organization_id=p_seller_organization_id
              and candidate.catalog_product_id=p_catalog_product_id and candidate.id=p_offer_id for no key update;
          if not found then raise exception 'catalog offer is unavailable' using errcode='28000'; end if;
          if locked_offer.status<>'draft' then raise exception 'catalog offer is not publishable' using errcode='55000'; end if;
          if locked_offer.version<>p_expected_offer_version then raise exception 'catalog offer version conflict' using errcode='40001'; end if;
          perform 1 from public.product_course linked join public.course course
            on course.seller_organization_id=linked.seller_organization_id and course.id=linked.course_id
          where linked.seller_organization_id=p_seller_organization_id
            and linked.catalog_product_id=p_catalog_product_id
          order by course.id,linked.position for no key update of linked,course;
          get diagnostics linked_course_count=row_count;
          if linked_course_count=0 or linked_course_count<>expected_course_count or exists(
            select 1 from public.product_course linked join public.course course
              on course.seller_organization_id=linked.seller_organization_id and course.id=linked.course_id
            where linked.seller_organization_id=p_seller_organization_id
              and linked.catalog_product_id=p_catalog_product_id and (
                course.status='archived' or p_expected_course_versions->>course.id::text is null
                or (p_expected_course_versions->>course.id::text)::numeric<>course.draft_version
              )
          ) then raise exception 'catalog course version conflict' using errcode='40001'; end if;
          generated_snapshot:=jsonb_build_object(
            'schema_version',2,'seller_organization_id',p_seller_organization_id,
            'product',jsonb_build_object('id',locked_product.id,'slug',locked_product.slug,
              'title',locked_product.title,'description',locked_product.description),
            'offer',jsonb_build_object('id',locked_offer.id,'title',locked_offer.title,
              'summary',locked_offer.summary,'billing_authority','external_unconfigured'),
            'courses',app_private.curriculum_courses_snapshot(p_seller_organization_id,p_catalog_product_id)
          );
          if octet_length(generated_snapshot::text)>1048576 then
            raise exception 'publication snapshot exceeds one mebibyte' using errcode='P4221';
          end if;
          generated_source_sha256:=encode(public.digest(convert_to(generated_snapshot::text,'UTF8'),'sha256'),'hex');
          if locked_product.published_snapshot_id is not null and exists(
            select 1 from public.publication current_publication
            where current_publication.seller_organization_id=p_seller_organization_id
              and current_publication.catalog_product_id=p_catalog_product_id
              and current_publication.id=locked_product.published_snapshot_id
              and current_publication.snapshot=generated_snapshot
          ) then raise exception 'no unpublished changes' using errcode='P4091'; end if;
          select coalesce(max(existing.version),0)+1 into next_publication_version
            from public.publication existing where existing.seller_organization_id=p_seller_organization_id
              and existing.catalog_product_id=p_catalog_product_id;
          insert into public.publication(seller_organization_id,id,catalog_product_id,version,schema_version,
            source_sha256,snapshot,actor_identity_user_id) values(p_seller_organization_id,next_publication_id,
            p_catalog_product_id,next_publication_version,2,generated_source_sha256,generated_snapshot,p_identity_user_id);
          update public.catalog_product set status='published',published_snapshot_id=next_publication_id,
            version=version+1,updated_at=clock_timestamp()
          where seller_organization_id=p_seller_organization_id and id=p_catalog_product_id
          returning version into next_product_version;
          response:=jsonb_build_object('publication_version',next_publication_version,
            'product_version',next_product_version,'source_sha256',generated_source_sha256);
          insert into app_private.catalog_mutation_request(seller_organization_id,operation,idempotency_key,
            identity_user_id,body_sha256,catalog_product_id,offer_id,publication_id,result)
          values(p_seller_organization_id,'product.publish',p_idempotency_key,p_identity_user_id,p_body_sha256,
            p_catalog_product_id,p_offer_id,next_publication_id,response);
          insert into public.audit_event(seller_organization_id,actor_identity_user_id,action,target_type,target_id,
            result,request_id,metadata) values(p_seller_organization_id,p_identity_user_id,'catalog.product_published',
            'catalog_product',p_catalog_product_id,'succeeded',p_request_id,jsonb_build_object(
              'publication_id',next_publication_id,'publication_version',next_publication_version,
              'source_sha256',generated_source_sha256,'offer_id',p_offer_id,'schema_version',2));
          return query select next_publication_id,next_publication_version,next_product_version,
                              generated_source_sha256,true;
        end $$
        """,
        """
        revoke all on function app_private.publish_product(
          uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid
        ) from public
        """,
        """
        grant execute on function app_private.publish_product(
          uuid,uuid,uuid,uuid,bigint,bigint,jsonb,uuid,text,uuid
        ) to appsell_runtime
        """,
        "grant select on course_module,lesson to appsell_runtime",
        "revoke insert,update,delete on course_module,lesson from appsell_runtime",
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.catalog_product') is not null
            and to_regclass('public.course') is not null
            and to_regclass('public.course_module') is not null
            and to_regclass('public.lesson') is not null
            and to_regclass('public.publication') is not null
            and to_regclass('app_private.curriculum_mutation_request') is not null
            and exists(select 1 from public.alembic_version where version_num='0005_curriculum')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
