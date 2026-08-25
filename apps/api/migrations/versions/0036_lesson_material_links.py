"""Let a lesson carry external links and embedded videos.

A seller could attach a file and nothing else, so a reference to an official
page, or a video that already lives on YouTube, had to be pasted into the lesson
text where it is neither checked nor presented as material.

Two shapes share one table because they answer the same question and appear in
the same list. An external link is an address the student opens; the server
stores it and never visits it, which keeps this from becoming a way to make the
server fetch arbitrary hosts. An embedded video is a provider and an identifier,
never markup: the seller cannot paste an iframe, so there is nothing to sanitise
and nothing to escape.

The publication snapshot carries the materials, because a link the student never
sees would be a feature only the editor believes in.
"""

from __future__ import annotations

from alembic import op

revision = "0036_lesson_material_links"
down_revision = "0035_document_support_material"
branch_labels = None
depends_on = None

_TABLE = r"""
create table public.lesson_material_link (
  seller_organization_id uuid not null,
  id uuid not null default gen_random_uuid(),
  course_id uuid not null,
  lesson_id uuid not null,
  kind text not null check (kind in ('external','embed')),
  title text not null check (
    title = btrim(title)
    and octet_length(title) between 2 and 160
    and title !~ '[[:cntrl:]]'
  ),
  url text check (
    url is null or (
      url ~ '^https://[^[:space:][:cntrl:]]{3,2000}$'
      and url !~ '[<>"]'
    )
  ),
  provider text check (provider is null or provider in ('youtube','vimeo')),
  video_id text check (video_id is null or video_id ~ '^[A-Za-z0-9_-]{1,64}$'),
  position integer not null check (position >= 0),
  status text not null default 'active' check (status in ('active','archived')),
  created_at timestamptz not null default clock_timestamp(),
  updated_at timestamptz not null default clock_timestamp(),
  primary key (seller_organization_id, id),
  constraint lesson_material_link_lesson_fkey
    foreign key (seller_organization_id, course_id, lesson_id)
    references public.lesson (seller_organization_id, course_id, id),
  constraint lesson_material_link_shape_check check (
    (kind='external' and url is not null and provider is null and video_id is null)
    or (kind='embed' and url is null and provider is not null and video_id is not null)
  ),
  constraint lesson_material_link_position_key
    unique (seller_organization_id, lesson_id, position)
    deferrable initially immediate
)
"""

_CREATE = r"""
create function app_private.create_lesson_material_link(
  p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid,
  p_kind text,p_title text,p_url text,p_provider text,p_video_id text,
  p_expected_course_version bigint,p_idempotency uuid,p_body text,p_request uuid
) returns table(material_id uuid, course_version bigint, "position" integer)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  locked_course public.course%rowtype;
  next_position integer;
  created public.lesson_material_link%rowtype;
begin
  if p_kind not in ('external','embed')
     or p_title is null or p_title<>btrim(p_title)
     or octet_length(p_title) not between 2 and 160 or p_title~'[[:cntrl:]]'
     or p_idempotency is null or p_request is null
     or p_body is null or p_body!~'^[0-9a-f]{64}$'
     or (p_kind='external' and (p_url is null or p_provider is not null or p_video_id is not null))
     or (p_kind='embed' and (p_url is not null or p_provider is null or p_video_id is null)) then
    raise exception 'lesson material request is invalid' using errcode='22023';
  end if;

  perform app_private.lock_curriculum_access(
    p_actor,p_seller,p_product,p_course,'catalog.curriculum.create');

  select * into locked_course
  from public.course course
  where course.seller_organization_id=p_seller and course.id=p_course
  for update;
  if not found then
    raise exception 'curriculum course is unavailable' using errcode='28000';
  end if;
  if locked_course.draft_version<>p_expected_course_version then
    raise exception 'curriculum version conflict' using errcode='40001';
  end if;

  perform 1
  from public.lesson lesson
  where lesson.seller_organization_id=p_seller
    and lesson.course_id=p_course
    and lesson.module_id=p_module
    and lesson.id=p_lesson
    and lesson.status='active'
  for share;
  if not found then
    raise exception 'curriculum lesson is unavailable' using errcode='28000';
  end if;

  select coalesce(max(link.position)+1,0) into next_position
  from public.lesson_material_link link
  where link.seller_organization_id=p_seller and link.lesson_id=p_lesson;

  insert into public.lesson_material_link(
    seller_organization_id,course_id,lesson_id,kind,title,url,provider,video_id,position
  ) values(
    p_seller,p_course,p_lesson,p_kind,p_title,p_url,p_provider,p_video_id,next_position
  )
  returning * into created;

  update public.course course
  set draft_version=course.draft_version+1,updated_at=clock_timestamp()
  where course.seller_organization_id=p_seller and course.id=p_course
  returning * into locked_course;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller,p_actor,'catalog.lesson_material_created','lesson_material_link',created.id,
    'succeeded',p_request,
    jsonb_build_object('lesson_id',p_lesson,'kind',p_kind,'idempotency_key',p_idempotency)
  );

  return query select created.id,locked_course.draft_version,created.position;
end
$$
"""

_LIST = """
create function app_private.list_lesson_material_links(
  p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid
) returns table(
  material_id uuid, kind text, title text, url text,
  provider text, video_id text, "position" integer
)
language plpgsql stable security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
begin
  perform app_private.lock_curriculum_access(
    p_actor,p_seller,p_product,p_course,'catalog.curriculum.read');
  return query
  select link.id,link.kind,link.title,link.url,link.provider,link.video_id,link.position
  from public.lesson_material_link link
  where link.seller_organization_id=p_seller
    and link.lesson_id=p_lesson
    and link.status='active'
  order by link.position, link.id;
end
$$
"""

_ARCHIVE = r"""
create function app_private.archive_lesson_material_link(
  p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid,
  p_material uuid,p_expected_course_version bigint,p_idempotency uuid,p_body text,p_request uuid
) returns table(material_id uuid, course_version bigint, changed boolean)
language plpgsql security definer
set search_path=pg_catalog,app_private,public,pg_temp as $$
declare
  locked_course public.course%rowtype;
  locked_link public.lesson_material_link%rowtype;
begin
  if p_material is null or p_idempotency is null or p_request is null
     or p_body is null or p_body!~'^[0-9a-f]{64}$' then
    raise exception 'lesson material archive is invalid' using errcode='22023';
  end if;

  perform app_private.lock_curriculum_access(
    p_actor,p_seller,p_product,p_course,'catalog.curriculum.archive');

  select * into locked_course
  from public.course course
  where course.seller_organization_id=p_seller and course.id=p_course
  for update;
  if not found then
    raise exception 'curriculum course is unavailable' using errcode='28000';
  end if;
  if locked_course.draft_version<>p_expected_course_version then
    raise exception 'curriculum version conflict' using errcode='40001';
  end if;

  select * into locked_link
  from public.lesson_material_link link
  where link.seller_organization_id=p_seller
    and link.lesson_id=p_lesson
    and link.id=p_material
  for update;
  if not found then
    raise exception 'lesson material is unavailable' using errcode='28000';
  end if;
  if locked_link.status='archived' then
    return query select locked_link.id,locked_course.draft_version,false;
    return;
  end if;

  update public.lesson_material_link link
  set status='archived',updated_at=clock_timestamp()
  where link.seller_organization_id=p_seller and link.id=p_material;

  update public.course course
  set draft_version=course.draft_version+1,updated_at=clock_timestamp()
  where course.seller_organization_id=p_seller and course.id=p_course
  returning * into locked_course;

  insert into public.audit_event(
    seller_organization_id,actor_identity_user_id,action,target_type,target_id,
    result,request_id,metadata
  ) values(
    p_seller,p_actor,'catalog.lesson_material_archived','lesson_material_link',p_material,
    'succeeded',p_request,
    jsonb_build_object('lesson_id',p_lesson,'idempotency_key',p_idempotency)
  );

  return query select locked_link.id,locked_course.draft_version,true;
end
$$
"""

# The snapshot must carry the materials, or the student never sees them.
_PROJECTION_ANCHOR = "'content',lesson.body_text,'status','active','position',lesson.position,"
_PROJECTION_REPLACEMENT = (
    "'content',lesson.body_text,'status','active','position',lesson.position,"
    "\n                    'materials',coalesce(("
    "\n                      select jsonb_agg(jsonb_build_object("
    "\n                        'id',material.id,'kind',material.kind,'title',material.title,"
    "\n                        'url',material.url,'provider',material.provider,"
    "\n                        'video_id',material.video_id,'position',material.position)"
    "\n                        order by material.position, material.id)"
    "\n                      from public.lesson_material_link material"
    "\n                      where material.seller_organization_id=lesson.seller_organization_id"
    "\n                        and material.lesson_id=lesson.id"
    "\n                        and material.status='active'),'[]'::jsonb),"
)

_SIGNATURES = (
    "app_private.create_lesson_material_link"
    "(uuid,uuid,uuid,uuid,uuid,uuid,text,text,text,text,text,bigint,uuid,text,uuid)",
    "app_private.list_lesson_material_links(uuid,uuid,uuid,uuid,uuid,uuid)",
    "app_private.archive_lesson_material_link"
    "(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid)",
)

_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access','0032_platform_superadmin_catalog',"
    "'0033_pdf_study_state','0034_publish_access_links',"
    "'0035_document_support_material')"
)
_READY_TAIL_WITH_0036 = _READY_TAIL[:-1] + ",'0036_lesson_material_links')"
_READY_ANCHOR = (
    "and to_regprocedure(\n              "
    "'app_private.asset_media_contract_ok(text,text,bigint)'\n            ) is not null"
)
_READY_ADDITION = (
    _READY_ANCHOR + "\n            and to_regclass('public.lesson_material_link') is not null"
)

_WIDEN_TEMPLATE = """
    do $migration$
    declare target oid; definition text; changed text; touched integer := 0;
    begin
      for target in
        select routine.oid
        from pg_proc routine
        join pg_namespace space on space.oid=routine.pronamespace
        where space.nspname='app_private' and routine.proname='__FUNCTION__'
      loop
        definition:=pg_get_functiondef(target);
        changed:=replace(definition,$old$__OLD__$old$,$new$__NEW__$new$);
        if changed<>definition then
          execute changed;
          touched:=touched+1;
        end if;
      end loop;
      if touched=0 then
        raise exception 'cannot carry __LABEL__ into the snapshot' using errcode='55000';
      end if;
    end $migration$
    """


def _widen(function_name: str, old: str, new: str, label: str) -> str:
    return (
        _WIDEN_TEMPLATE.replace("__FUNCTION__", function_name)
        .replace("__OLD__", old)
        .replace("__NEW__", new)
        .replace("__LABEL__", label)
    )


def upgrade() -> None:
    statements = [
        _TABLE,
        "alter table public.lesson_material_link enable row level security",
        "alter table public.lesson_material_link force row level security",
        """
        create policy lesson_material_link_owner_all on public.lesson_material_link
          to appsell_owner using (true) with check (true)
        """,
        """
        create index lesson_material_link_lesson_idx
          on public.lesson_material_link(seller_organization_id,lesson_id,status,position)
        """,
        _CREATE,
        _LIST,
        _ARCHIVE,
    ]
    for signature in _SIGNATURES:
        statements.append(f"revoke all on function {signature} from public")
        statements.append(f"grant execute on function {signature} to appsell_runtime")
    statements.append(
        _widen(
            "curriculum_courses_snapshot",
            _PROJECTION_ANCHOR,
            _PROJECTION_REPLACEMENT,
            "materials",
        )
    )
    statements.append(
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0036}$new$);
          if changed=definition then
            raise exception 'cannot extend 0016 readiness for 0036' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL}$old$,$new${_READY_TAIL_WITH_0036}$new$);
          if changed=definition then
            raise exception 'cannot extend 0017 readiness for 0036' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed:=replace(definition,$old${_READY_ANCHOR}$old$,$new${_READY_ADDITION}$new$);
          if changed=definition then
            raise exception 'cannot extend schema readiness for 0036' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """
        do $migration$
        begin
          if exists (select 1 from public.lesson_material_link) then
            raise exception 'cannot drop lesson materials that exist' using errcode='55000';
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
            raise exception 'cannot restore readiness from 0036' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0036}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0017 readiness from 0036' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${_READY_TAIL_WITH_0036}$old$,$new${_READY_TAIL}$new$);
          if changed=definition then
            raise exception 'cannot restore 0016 readiness from 0036' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        _widen(
            "curriculum_courses_snapshot",
            _PROJECTION_REPLACEMENT,
            _PROJECTION_ANCHOR,
            "materials back out of",
        ),
    ]
    for signature in reversed(_SIGNATURES):
        statements.append(f"drop function {signature}")
    statements.append("drop table public.lesson_material_link")
    for statement in statements:
        op.execute(statement)
