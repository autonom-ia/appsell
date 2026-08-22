"""Add immutable, publication-pinned private asset storage.

Revision ID: 0008_private_asset_storage
Revises: 0007_learning_progress
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_private_asset_storage"
down_revision: str | None = "0007_learning_progress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        create table asset_record (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          catalog_product_id uuid not null,
          title text not null check (
            title=btrim(title) and octet_length(title) between 2 and 160
            and title!~'[[:cntrl:]]'
          ),
          status text not null default 'active' check(status in ('active','archived')),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          constraint asset_record_product_fkey foreign key(
            seller_organization_id,catalog_product_id
          ) references catalog_product(seller_organization_id,id),
          constraint asset_record_product_key unique(
            seller_organization_id,catalog_product_id,id
          )
        )
        """,
        """
        create index asset_record_product_status_idx on asset_record(
          seller_organization_id,catalog_product_id,status,id
        )
        """,
        """
        create table asset_blob (
          seller_organization_id uuid not null,
          id uuid not null,
          storage_driver text not null check(storage_driver in ('s3','legacy_bundle')),
          storage_bucket text not null check(
            storage_bucket=btrim(storage_bucket)
            and octet_length(storage_bucket) between 3 and 255
            and storage_bucket~'^[a-z0-9][a-z0-9.-]+$'
          ),
          object_key text not null check(
            object_key=btrim(object_key) and octet_length(object_key) between 1 and 1024
            and object_key!~'(^/|//|[[:cntrl:]]|(^|/)[.][.](/|$))'
          ),
          storage_version_id text,
          storage_etag text,
          file_name text not null check(
            file_name=btrim(file_name) and octet_length(file_name) between 1 and 160
            and file_name!~'[/\\\\[:cntrl:]]'
          ),
          media_type text not null check(media_type='application/pdf'),
          byte_size bigint not null check(byte_size between 1 and 10485760),
          sha256 text not null check(sha256~'^[0-9a-f]{64}$'),
          magic_type text not null check(magic_type='application/pdf'),
          scanner_result text not null check(scanner_result in ('clean','legacy_trusted')),
          verified_at timestamptz not null,
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          constraint asset_blob_location_key unique(
            storage_driver,storage_bucket,object_key,storage_version_id
          ),
          constraint asset_blob_s3_version_check check(
            (storage_driver='s3' and storage_version_id is not null
              and storage_etag is not null and scanner_result='clean')
            or (storage_driver='legacy_bundle' and storage_version_id is null
              and scanner_result='legacy_trusted')
          )
        )
        """,
        """
        create index asset_blob_tenant_hash_idx on asset_blob(
          seller_organization_id,sha256,byte_size,id
        )
        """,
        """
        create table asset_version (
          seller_organization_id uuid not null,
          asset_id uuid not null,
          version integer not null check(version>0),
          blob_id uuid not null,
          actor_identity_user_id uuid not null references identity_user(id),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,asset_id,version),
          constraint asset_version_asset_fkey foreign key(seller_organization_id,asset_id)
            references asset_record(seller_organization_id,id),
          constraint asset_version_blob_fkey foreign key(seller_organization_id,blob_id)
            references asset_blob(seller_organization_id,id),
          constraint asset_version_blob_key unique(seller_organization_id,blob_id),
          constraint asset_version_full_key unique(
            seller_organization_id,asset_id,version,blob_id
          )
        )
        """,
        """
        create table lesson_asset_draft (
          seller_organization_id uuid not null,
          catalog_product_id uuid not null,
          course_id uuid not null,
          module_id uuid not null,
          lesson_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null,
          blob_id uuid not null,
          position integer not null check(position>=0),
          status text not null default 'active' check(status in ('active','archived')),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,lesson_id,asset_id),
          constraint lesson_asset_draft_lesson_fkey foreign key(
            seller_organization_id,course_id,module_id,lesson_id
          ) references lesson(seller_organization_id,course_id,module_id,id),
          constraint lesson_asset_draft_product_course_fkey foreign key(
            seller_organization_id,catalog_product_id,course_id
          ) references product_course(seller_organization_id,catalog_product_id,course_id),
          constraint lesson_asset_draft_asset_fkey foreign key(
            seller_organization_id,catalog_product_id,asset_id
          ) references asset_record(seller_organization_id,catalog_product_id,id),
          constraint lesson_asset_draft_version_fkey foreign key(
            seller_organization_id,asset_id,asset_version,blob_id
          ) references asset_version(seller_organization_id,asset_id,version,blob_id)
        )
        """,
        """
        create unique index lesson_asset_draft_active_position_key
          on lesson_asset_draft(seller_organization_id,lesson_id,position)
          where status='active'
        """,
        """
        create index lesson_asset_draft_course_idx on lesson_asset_draft(
          seller_organization_id,catalog_product_id,course_id,status,module_id,lesson_id,position
        )
        """,
        """
        create table publication_asset (
          seller_organization_id uuid not null,
          catalog_product_id uuid not null,
          publication_id uuid not null,
          course_id uuid not null,
          module_id uuid not null,
          lesson_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null,
          blob_id uuid not null,
          position integer not null check(position>=0),
          title text not null,
          file_name text not null,
          media_type text not null,
          byte_size bigint not null,
          sha256 text not null,
          storage_driver text not null,
          storage_bucket text not null,
          object_key text not null,
          storage_version_id text,
          storage_etag text,
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,publication_id,asset_id),
          constraint publication_asset_lesson_fkey foreign key(
            seller_organization_id,catalog_product_id,publication_id,course_id,lesson_id
          ) references publication_lesson(
            seller_organization_id,catalog_product_id,publication_id,course_id,lesson_id
          ),
          constraint publication_asset_version_fkey foreign key(
            seller_organization_id,asset_id,asset_version,blob_id
          ) references asset_version(seller_organization_id,asset_id,version,blob_id),
          constraint publication_asset_position_key unique(
            seller_organization_id,publication_id,lesson_id,position
          )
        )
        """,
        """
        create index publication_asset_authorization_idx on publication_asset(
          seller_organization_id,catalog_product_id,publication_id,asset_id,lesson_id
        )
        """,
        """
        create table app_private.asset_upload (
          seller_organization_id uuid not null,
          id uuid not null,
          identity_user_id uuid not null references public.identity_user(id),
          catalog_product_id uuid not null,
          course_id uuid not null,
          module_id uuid not null,
          lesson_id uuid not null,
          asset_id uuid not null,
          blob_id uuid not null,
          reserved_version integer not null check(reserved_version>0),
          title text not null,
          file_name text not null,
          expected_media_type text not null check(expected_media_type='application/pdf'),
          expected_byte_size bigint not null check(expected_byte_size between 1 and 10485760),
          expected_sha256 text not null check(expected_sha256~'^[0-9a-f]{64}$'),
          storage_driver text not null check(storage_driver='s3'),
          quarantine_bucket text not null check(
            quarantine_bucket=btrim(quarantine_bucket)
            and octet_length(quarantine_bucket) between 3 and 255
            and quarantine_bucket~'^[a-z0-9][a-z0-9.-]+$'
          ),
          ready_bucket text not null check(
            ready_bucket=btrim(ready_bucket)
            and octet_length(ready_bucket) between 3 and 255
            and ready_bucket~'^[a-z0-9][a-z0-9.-]+$'
          ),
          quarantine_key text not null,
          ready_key text not null,
          observed_version_id text,
          observed_etag text,
          status text not null check(status in (
            'pending','uploaded','verifying','ready','rejected','cancelled','expired'
          )),
          failure_code text,
          expires_at timestamptz not null,
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          constraint asset_upload_product_course_fkey foreign key(
            seller_organization_id,catalog_product_id,course_id
          ) references public.product_course(
            seller_organization_id,catalog_product_id,course_id
          ),
          constraint asset_upload_lesson_fkey foreign key(
            seller_organization_id,course_id,module_id,lesson_id
          ) references public.lesson(seller_organization_id,course_id,module_id,id),
          constraint asset_upload_asset_fkey foreign key(
            seller_organization_id,catalog_product_id,asset_id
          ) references public.asset_record(seller_organization_id,catalog_product_id,id),
          constraint asset_upload_reserved_key unique(
            seller_organization_id,asset_id,reserved_version
          ),
          constraint asset_upload_quarantine_key unique(
            storage_driver,quarantine_bucket,quarantine_key
          ),
          constraint asset_upload_ready_key unique(
            storage_driver,ready_bucket,ready_key
          ),
          constraint asset_upload_bucket_separation check(quarantine_bucket<>ready_bucket),
          constraint asset_upload_expiry_check check(expires_at>created_at)
        )
        """,
        """
        create index asset_upload_product_status_idx on app_private.asset_upload(
          seller_organization_id,catalog_product_id,status,expires_at,id
        )
        """,
        """
        create table app_private.asset_verification_job (
          seller_organization_id uuid not null,
          upload_id uuid not null,
          status text not null check(status in ('queued','leased','completed','failed')),
          attempts integer not null default 0 check(attempts between 0 and 10),
          worker_id text,
          lease_expires_at timestamptz,
          available_at timestamptz not null default clock_timestamp(),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,upload_id),
          constraint asset_verification_job_upload_fkey foreign key(
            seller_organization_id,upload_id
          ) references app_private.asset_upload(seller_organization_id,id)
        )
        """,
        """
        create index asset_verification_job_claim_idx on app_private.asset_verification_job(
          status,available_at,lease_expires_at,created_at,upload_id
        ) where status in ('queued','leased')
        """,
        """
        create table app_private.asset_storage_cleanup_job (
          seller_organization_id uuid not null,
          id uuid not null default gen_random_uuid(),
          upload_id uuid not null,
          kind text not null check(kind in (
            'quarantine','quarantine_reconcile','ready_reconcile','ready_orphan'
          )),
          storage_driver text not null check(storage_driver='s3'),
          bucket text not null,
          object_key text not null,
          storage_version_id text check(
            storage_version_id is null or octet_length(storage_version_id) between 1 and 512
          ),
          storage_etag text,
          byte_size bigint check(byte_size is null or byte_size between 1 and 10485760),
          sha256 text check(sha256 is null or sha256~'^[0-9a-f]{64}$'),
          media_type text check(media_type is null or media_type='application/pdf'),
          status text not null check(status in (
            'queued','leased','protected','completed','dead'
          )),
          attempts integer not null default 0 check(attempts between 0 and 10),
          worker_id text,
          lease_expires_at timestamptz,
          available_at timestamptz not null default clock_timestamp(),
          failure_code text,
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,id),
          constraint asset_cleanup_upload_fkey foreign key(seller_organization_id,upload_id)
            references app_private.asset_upload(seller_organization_id,id),
          constraint asset_cleanup_locator_check check(
            (kind in ('quarantine_reconcile','ready_reconcile')
              and storage_version_id is null)
            or (kind in ('quarantine','ready_orphan') and storage_version_id is not null)
          )
        )
        """,
        """
        create index asset_storage_cleanup_claim_idx on app_private.asset_storage_cleanup_job(
          status,available_at,lease_expires_at,created_at,id
        ) where status in ('queued','leased')
        """,
        """
        create unique index asset_storage_cleanup_locator_key
          on app_private.asset_storage_cleanup_job(
            kind,storage_driver,bucket,object_key,coalesce(storage_version_id,'')
          )
        """,
        """
        create table app_private.asset_mutation_request (
          seller_organization_id uuid not null,
          operation text not null check(operation in (
            'upload.create','upload.confirm','upload.cancel','upload.finalize','asset.archive'
          )),
          idempotency_key uuid not null,
          identity_user_id uuid not null references public.identity_user(id),
          body_sha256 text not null check(body_sha256~'^[0-9a-f]{64}$'),
          catalog_product_id uuid not null,
          course_id uuid not null,
          module_id uuid not null,
          lesson_id uuid not null,
          asset_id uuid,
          upload_id uuid,
          result jsonb not null check(jsonb_typeof(result)='object'),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,operation,idempotency_key)
        )
        """,
        """
        create table app_private.asset_worker_identity (
          identity_user_id uuid primary key references public.identity_user(id),
          status text not null check(status in ('active','revoked')),
          label text not null check(
            label=btrim(label) and octet_length(label) between 3 and 120
            and label!~'[[:cntrl:]]'
          ),
          created_at timestamptz not null default clock_timestamp(),
          updated_at timestamptz not null default clock_timestamp()
        )
        """,
        """
        create index asset_mutation_actor_idx on app_private.asset_mutation_request(
          identity_user_id,seller_organization_id,created_at desc
        )
        """,
        "revoke all on app_private.asset_upload,app_private.asset_verification_job,app_private.asset_storage_cleanup_job,app_private.asset_mutation_request,app_private.asset_worker_identity from public,appsell_runtime",
        "alter table asset_record enable row level security",
        "alter table asset_record force row level security",
        "alter table asset_blob enable row level security",
        "alter table asset_blob force row level security",
        "alter table asset_version enable row level security",
        "alter table asset_version force row level security",
        "alter table lesson_asset_draft enable row level security",
        "alter table lesson_asset_draft force row level security",
        "alter table publication_asset enable row level security",
        "alter table publication_asset force row level security",
        "create policy asset_record_owner_all on asset_record to appsell_owner using(true) with check(true)",
        "create policy asset_blob_owner_all on asset_blob to appsell_owner using(true) with check(true)",
        "create policy asset_version_owner_all on asset_version to appsell_owner using(true) with check(true)",
        "create policy lesson_asset_draft_owner_all on lesson_asset_draft to appsell_owner using(true) with check(true)",
        "create policy publication_asset_owner_all on publication_asset to appsell_owner using(true) with check(true)",
        "revoke all on asset_record,asset_blob,asset_version,lesson_asset_draft,publication_asset from public,appsell_runtime",
        """
        create function app_private.reject_asset_immutable_mutation()
        returns trigger language plpgsql set search_path=pg_catalog as $$
        declare database_marker text;
        begin
          if tg_op='DELETE'
             and old.seller_organization_id='90718777-93de-5e7a-aaa4-45b25051582c'::uuid
             and current_setting('appsell.demo_reset_key',true)='autonomia-demo-v1' then
            select shobj_description(oid,'pg_database') into database_marker
            from pg_database where datname=current_database();
            if database_marker='appsell-demo-reset-target-v1' then return old; end if;
          end if;
          raise exception 'asset history is append-only' using errcode='55000';
        end $$
        """,
        "revoke all on function app_private.reject_asset_immutable_mutation() from public,appsell_runtime",
        "create trigger asset_blob_append_only before update or delete on asset_blob for each row execute function app_private.reject_asset_immutable_mutation()",
        "create trigger asset_version_append_only before update or delete on asset_version for each row execute function app_private.reject_asset_immutable_mutation()",
        "create trigger publication_asset_append_only before update or delete on publication_asset for each row execute function app_private.reject_asset_immutable_mutation()",
        """
        create function app_private.asset_mutation_replay(
          p_seller uuid,p_operation text,p_key uuid,p_actor uuid,p_body text,
          p_product uuid,p_course uuid,p_module uuid,p_lesson uuid,
          p_asset uuid,p_upload uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        declare receipt app_private.asset_mutation_request%rowtype;
        begin
          select * into receipt from app_private.asset_mutation_request request
          where request.seller_organization_id=p_seller
            and request.operation=p_operation and request.idempotency_key=p_key;
          if not found then return null; end if;
          if receipt.identity_user_id=p_actor and receipt.body_sha256=p_body
             and receipt.catalog_product_id=p_product and receipt.course_id=p_course
             and receipt.module_id=p_module and receipt.lesson_id=p_lesson
             and (p_operation='upload.create' or (
               receipt.asset_id is not distinct from p_asset
               and receipt.upload_id is not distinct from p_upload
             )) then
            return receipt.result;
          end if;
          raise exception 'asset idempotency conflict' using errcode='23505';
        end $$
        """,
        "revoke all on function app_private.asset_mutation_replay(uuid,text,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid) from public,appsell_runtime",
        """
        create function app_private.record_asset_mutation(
          p_seller uuid,p_operation text,p_key uuid,p_actor uuid,p_body text,
          p_product uuid,p_course uuid,p_module uuid,p_lesson uuid,
          p_asset uuid,p_upload uuid,p_result jsonb
        ) returns void language sql security definer
        set search_path=pg_catalog,app_private as $$
          insert into app_private.asset_mutation_request(
            seller_organization_id,operation,idempotency_key,identity_user_id,
            body_sha256,catalog_product_id,course_id,module_id,lesson_id,
            asset_id,upload_id,result
          ) values(p_seller,p_operation,p_key,p_actor,p_body,p_product,p_course,
            p_module,p_lesson,p_asset,p_upload,p_result)
        $$
        """,
        "revoke all on function app_private.record_asset_mutation(uuid,text,uuid,uuid,text,uuid,uuid,uuid,uuid,uuid,uuid,jsonb) from public,appsell_runtime",
        """
        create function app_private.enqueue_asset_storage_cleanup(
          p_seller uuid,p_upload uuid,p_kind text,p_driver text,p_bucket text,
          p_object_key text,p_version_id text
        ) returns void language sql security definer
        set search_path=pg_catalog,app_private as $$
          insert into app_private.asset_storage_cleanup_job(
            seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
            storage_version_id,status
          ) values(p_seller,p_upload,p_kind,p_driver,p_bucket,p_object_key,p_version_id,'queued')
          on conflict do nothing
        $$
        """,
        "revoke all on function app_private.enqueue_asset_storage_cleanup(uuid,uuid,text,text,text,text,text) from public,appsell_runtime",
        """
        create function app_private.asset_worker_context_allowed(p_actor uuid)
        returns boolean language sql stable security definer
        set search_path=pg_catalog,app_private as $$
          select coalesce(p_actor is not null
            and app_private.context_uuid('identity_user_id')=p_actor
            and app_private.context_uuid('seller_organization_id') is null
            and app_private.context_text('capability')='catalog.asset.verify'
            and exists(
              select 1 from app_private.asset_worker_identity worker
              where worker.identity_user_id=p_actor and worker.status='active'
            ),false)
        $$
        """,
        "revoke all on function app_private.asset_worker_context_allowed(uuid) from public,appsell_runtime",
        """
        create function app_private.create_asset_upload_intent(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_existing_asset uuid,p_expected_course_version bigint,
          p_title text,p_file_name text,p_media_type text,p_size bigint,p_sha text,
          p_storage_driver text,p_quarantine_bucket text,p_ready_bucket text,
          p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; upload_id uuid:=gen_random_uuid();
          target_asset_id uuid:=coalesce(p_existing_asset,gen_random_uuid()); blob_id uuid:=gen_random_uuid();
          reserved integer; quarantine text; ready text; result jsonb;
        begin
          if p_idempotency is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' or p_title is null or p_title<>btrim(p_title)
             or octet_length(p_title) not between 2 and 160 or p_title~'[[:cntrl:]]'
             or p_file_name is null or p_file_name<>btrim(p_file_name)
             or octet_length(p_file_name) not between 1 and 160
             or p_file_name~'[/\\\\[:cntrl:]]' or lower(p_file_name)!~'[.]pdf$'
             or p_media_type<>'application/pdf' or p_size not between 1 and 10485760
             or p_sha is null or p_sha!~'^[0-9a-f]{64}$'
             or p_storage_driver<>'s3' or p_quarantine_bucket is null
             or p_ready_bucket is null or p_quarantine_bucket=p_ready_bucket
             or p_quarantine_bucket!~'^[a-z0-9][a-z0-9.-]{1,253}[a-z0-9]$'
             or p_ready_bucket!~'^[a-z0-9][a-z0-9.-]{1,253}[a-z0-9]$' then
            raise exception 'invalid asset upload intent' using errcode='22023';
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/reset/'||p_seller::text,0
          ));
          perform app_private.lock_curriculum_access(
            p_actor,p_seller,p_product,p_course,'catalog.asset.create'
          );
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/upload.create/'||p_idempotency::text,0
          ));
          replay:=app_private.asset_mutation_replay(
            p_seller,'upload.create',p_idempotency,p_actor,p_body,p_product,p_course,
            p_module,p_lesson,p_existing_asset,null
          );
          if replay is not null then return jsonb_set(replay,'{created}','false'::jsonb,true); end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/quota/'||p_seller::text,0
          ));
          update app_private.asset_upload set status='expired',failure_code='intent_expired',
            updated_at=clock_timestamp()
          where seller_organization_id=p_seller and status='pending'
            and expires_at<=clock_timestamp();
          if (select count(*) from app_private.asset_upload upload
              where upload.seller_organization_id=p_seller and exists(
                select 1 from app_private.asset_storage_cleanup_job reconcile
                where reconcile.seller_organization_id=upload.seller_organization_id
                  and reconcile.upload_id=upload.id
                  and reconcile.kind='quarantine_reconcile'
                  and reconcile.status<>'completed'
              ))>=100 then
            raise exception 'seller open asset upload limit exceeded' using errcode='P4290';
          end if;
          if coalesce((select sum(byte_size) from asset_blob
              where seller_organization_id=p_seller and storage_driver='s3'),0)+p_size
              >1073741824 then
            raise exception 'seller ready asset byte quota exceeded' using errcode='P4290';
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/open/'||p_seller::text||'/'||p_actor::text,0
          ));
          update app_private.asset_upload set status='expired',failure_code='intent_expired',
            updated_at=clock_timestamp()
          where seller_organization_id=p_seller and identity_user_id=p_actor
            and status='pending' and expires_at<=clock_timestamp();
          if (select count(*) from app_private.asset_upload upload
              where upload.seller_organization_id=p_seller
                and upload.identity_user_id=p_actor and exists(
                  select 1 from app_private.asset_storage_cleanup_job reconcile
                  where reconcile.seller_organization_id=upload.seller_organization_id
                    and reconcile.upload_id=upload.id
                    and reconcile.kind='quarantine_reconcile'
                    and reconcile.status<>'completed'
                ))>=10 then
            raise exception 'open asset upload limit exceeded' using errcode='P4290';
          end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_course_version);
          perform 1 from lesson where seller_organization_id=p_seller and course_id=p_course
            and module_id=p_module and id=p_lesson and status='active' for key share;
          if not found then raise exception 'asset lesson is unavailable' using errcode='28000'; end if;
          if p_existing_asset is null then
            insert into asset_record(seller_organization_id,id,catalog_product_id,title)
              values(p_seller,target_asset_id,p_product,p_title);
          else
            perform 1 from asset_record where seller_organization_id=p_seller
              and catalog_product_id=p_product and id=p_existing_asset and status='active'
              for no key update;
            if not found then raise exception 'asset is unavailable' using errcode='28000'; end if;
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/'||target_asset_id::text,0
          ));
          select coalesce(max(candidate),0)+1 into reserved from (
            select version candidate from asset_version
              where seller_organization_id=p_seller and asset_version.asset_id=target_asset_id
            union all
            select reserved_version from app_private.asset_upload
              where seller_organization_id=p_seller and asset_upload.asset_id=target_asset_id
          ) versions;
          quarantine:='tenants/'||p_seller::text||'/quarantine/'||upload_id::text;
          ready:='tenants/'||p_seller::text||'/assets/'||target_asset_id::text||
            '/v'||reserved::text||'/'||blob_id::text;
          insert into app_private.asset_upload(
            seller_organization_id,id,identity_user_id,catalog_product_id,course_id,
            module_id,lesson_id,asset_id,blob_id,reserved_version,title,file_name,
            expected_media_type,expected_byte_size,expected_sha256,storage_driver,
            quarantine_bucket,ready_bucket,quarantine_key,ready_key,status,expires_at
          ) values(p_seller,upload_id,p_actor,p_product,p_course,p_module,p_lesson,
            target_asset_id,blob_id,reserved,p_title,p_file_name,p_media_type,p_size,p_sha,
            p_storage_driver,p_quarantine_bucket,p_ready_bucket,quarantine,ready,'pending',
            clock_timestamp()+interval '15 minutes');
          insert into app_private.asset_storage_cleanup_job(
            seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
            storage_version_id,status,available_at
          ) select p_seller,upload_id,'quarantine_reconcile',p_storage_driver,
            p_quarantine_bucket,quarantine,null,'queued',expires_at+interval '5 minutes'
          from app_private.asset_upload
          where seller_organization_id=p_seller and id=upload_id;
          insert into app_private.asset_storage_cleanup_job(
            seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
            storage_version_id,status,available_at
          ) select p_seller,upload_id,'ready_reconcile',p_storage_driver,
            p_ready_bucket,ready,null,'queued',expires_at+interval '5 minutes'
          from app_private.asset_upload
          where seller_organization_id=p_seller and id=upload_id;
          result:=jsonb_build_object('upload_id',upload_id,'asset_id',target_asset_id,
            'blob_id',blob_id,'reserved_version',reserved,'course_version',
            p_expected_course_version,'expires_at',(select expires_at from app_private.asset_upload
              where seller_organization_id=p_seller and id=upload_id),'created',true);
          perform app_private.record_asset_mutation(p_seller,'upload.create',p_idempotency,
            p_actor,p_body,p_product,p_course,p_module,p_lesson,
            target_asset_id,upload_id,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_upload_created','asset',target_asset_id,
            'succeeded',p_request,jsonb_build_object('upload_id',upload_id,
              'reserved_version',reserved,'expected_byte_size',p_size));
          return result;
        end $$
        """,
        "revoke all on function app_private.create_asset_upload_intent(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,text,text,text,bigint,text,text,text,text,uuid,text,uuid) from public",
        "grant execute on function app_private.create_asset_upload_intent(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,text,text,text,bigint,text,text,text,text,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.read_asset_upload_for_signing(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype;
        begin
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,candidate.catalog_product_id,
            candidate.course_id,'catalog.asset.upload.sign');
          if candidate.identity_user_id<>p_actor or candidate.status<>'pending'
             or candidate.expires_at<=clock_timestamp() then
            raise exception 'asset upload is unavailable' using errcode='28000';
          end if;
          return jsonb_build_object('upload_id',candidate.id,'storage_driver',candidate.storage_driver,
            'bucket',candidate.quarantine_bucket,'object_key',candidate.quarantine_key,
            'content_type',candidate.expected_media_type,'byte_size',candidate.expected_byte_size,
            'sha256',candidate.expected_sha256,'expires_at',candidate.expires_at);
        end $$
        """,
        "revoke all on function app_private.read_asset_upload_for_signing(uuid,uuid,uuid,uuid,uuid,uuid,uuid) from public",
        "grant execute on function app_private.read_asset_upload_for_signing(uuid,uuid,uuid,uuid,uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.replay_asset_upload_confirmation(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid,p_idempotency uuid,p_body text
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype;
        begin
          if p_idempotency is null or p_body is null or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid asset confirmation replay' using errcode='22023'; end if;
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,p_product,p_course,
            'catalog.asset.upload.confirm');
          return app_private.asset_mutation_replay(p_seller,'upload.confirm',p_idempotency,
            p_actor,p_body,p_product,p_course,p_module,p_lesson,candidate.asset_id,p_upload);
        end $$
        """,
        "revoke all on function app_private.replay_asset_upload_confirmation(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text) from public",
        "grant execute on function app_private.replay_asset_upload_confirmation(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text) to appsell_runtime",
        """
        create function app_private.confirm_asset_upload(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid,p_observed_version_id text,
          p_observed_size bigint,p_observed_checksum text,p_observed_etag text,
          p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype; replay jsonb; result jsonb;
        begin
          if p_observed_version_id is null or octet_length(p_observed_version_id)>512
             or p_observed_etag is null or octet_length(p_observed_etag)>256
             or p_observed_checksum is null or p_observed_checksum!~'^[0-9a-f]{64}$'
             or p_observed_size not between 1 and 10485760 or p_idempotency is null
             or p_request is null or p_body is null or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid asset upload confirmation' using errcode='22023';
          end if;
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,candidate.catalog_product_id,
            candidate.course_id,'catalog.asset.upload.confirm');
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/upload.confirm/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'upload.confirm',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,candidate.asset_id,p_upload);
          if replay is not null then return jsonb_set(replay,'{confirmed}','false'::jsonb,true); end if;
          if candidate.identity_user_id<>p_actor or candidate.status<>'pending'
             or candidate.expires_at<=clock_timestamp() then
            raise exception 'asset upload is unavailable' using errcode='28000';
          end if;
          if p_observed_size<>candidate.expected_byte_size
             or p_observed_checksum<>candidate.expected_sha256 then
            update app_private.asset_upload set status='rejected',failure_code='head_mismatch',
              updated_at=clock_timestamp() where seller_organization_id=p_seller and id=p_upload;
            perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
              candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
              p_observed_version_id);
            result:=jsonb_build_object('upload_id',p_upload,'status','rejected',
              'safe_reason','upload_mismatch','confirmed',false);
            perform app_private.record_asset_mutation(p_seller,'upload.confirm',p_idempotency,
              p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
              candidate.module_id,candidate.lesson_id,
              candidate.asset_id,p_upload,result);
            insert into audit_event(seller_organization_id,actor_identity_user_id,action,
              target_type,target_id,result,request_id,metadata)
            values(p_seller,p_actor,'catalog.asset_upload_rejected','asset',candidate.asset_id,
              'failed',p_request,jsonb_build_object('upload_id',p_upload,
                'safe_reason','upload_mismatch'));
            return result;
          end if;
          update app_private.asset_upload set status='uploaded',
            observed_version_id=p_observed_version_id,observed_etag=p_observed_etag,
            updated_at=clock_timestamp() where seller_organization_id=p_seller and id=p_upload;
          insert into app_private.asset_verification_job(seller_organization_id,upload_id,status)
            values(p_seller,p_upload,'queued');
          result:=jsonb_build_object('upload_id',p_upload,'status','uploaded','confirmed',true);
          perform app_private.record_asset_mutation(p_seller,'upload.confirm',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,
            candidate.asset_id,p_upload,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_upload_confirmed','asset',candidate.asset_id,
            'succeeded',p_request,jsonb_build_object('upload_id',p_upload));
          return result;
        end $$
        """,
        "revoke all on function app_private.confirm_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,uuid,text,uuid) from public",
        "grant execute on function app_private.confirm_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,bigint,text,text,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.begin_asset_verification(
          p_actor uuid,p_seller uuid,p_upload uuid,p_worker text,p_lease_seconds integer
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype; claimed app_private.asset_verification_job%rowtype;
        begin
          if p_worker is null or p_worker<>btrim(p_worker) or octet_length(p_worker) not between 3 and 120
             or p_worker~'[[:cntrl:]]' or p_lease_seconds not between 30 and 600 then
            raise exception 'invalid asset verification lease' using errcode='22023'; end if;
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          if not app_private.asset_worker_context_allowed(p_actor) then
            raise exception 'asset worker context is not authorized' using errcode='28000';
          end if;
          if exists(
            select 1 from app_private.asset_verification_job job
            where job.seller_organization_id=p_seller and job.upload_id=p_upload
              and job.status='leased' and job.attempts>=10
              and job.lease_expires_at<=clock_timestamp()
          ) then
            update app_private.asset_verification_job set status='failed',
              lease_expires_at=null,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and upload_id=p_upload;
            update app_private.asset_upload set status='rejected',
              failure_code='verification_attempts_exhausted',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_upload and status='verifying';
            perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
              candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
              candidate.observed_version_id);
            return null;
          end if;
          update app_private.asset_verification_job job set status='leased',worker_id=p_worker,
            lease_expires_at=clock_timestamp()+make_interval(secs=>p_lease_seconds),
            attempts=attempts+1,updated_at=clock_timestamp()
          where job.seller_organization_id=p_seller and job.upload_id=p_upload
            and job.attempts<10 and (
              job.status='queued' or (job.status='leased' and job.lease_expires_at<=clock_timestamp())
            ) returning * into claimed;
          if not found then raise exception 'asset verification job is unavailable' using errcode='P4091'; end if;
          update app_private.asset_upload set status='verifying',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_upload and status in ('uploaded','verifying');
          return jsonb_build_object('upload_id',p_upload,'worker_id',claimed.worker_id,
            'lease_expires_at',claimed.lease_expires_at,'attempt',claimed.attempts,
            'quarantine_bucket',candidate.quarantine_bucket,
            'ready_bucket',candidate.ready_bucket,
            'quarantine_key',candidate.quarantine_key,'ready_key',candidate.ready_key,
            'source_version_id',candidate.observed_version_id,
            'expected_byte_size',candidate.expected_byte_size,
            'expected_sha256',candidate.expected_sha256,
            'expected_media_type',candidate.expected_media_type,
            'file_name',candidate.file_name);
        end $$
        """,
        "revoke all on function app_private.begin_asset_verification(uuid,uuid,uuid,text,integer) from public",
        "grant execute on function app_private.begin_asset_verification(uuid,uuid,uuid,text,integer) to appsell_runtime",
        """
        create function app_private.claim_asset_verification_jobs(
          p_actor uuid,p_worker text,p_limit integer,p_lease_seconds integer
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate record; claimed jsonb:='[]'::jsonb; lease_until timestamptz;
          locked_attempts integer;
        begin
          if not app_private.asset_worker_context_allowed(p_actor) then
            raise exception 'asset worker context is not authorized' using errcode='28000';
          end if;
          if p_worker is null or p_worker<>btrim(p_worker)
             or octet_length(p_worker) not between 3 and 120 or p_worker~'[[:cntrl:]]'
             or p_limit not between 1 and 20 or p_lease_seconds not between 30 and 600 then
            raise exception 'invalid asset verification claim' using errcode='22023'; end if;
          for candidate in
            select job.seller_organization_id,job.upload_id,upload.storage_driver,
              upload.quarantine_bucket,upload.quarantine_key,upload.observed_version_id
            from app_private.asset_verification_job job
            join app_private.asset_upload upload
              on upload.seller_organization_id=job.seller_organization_id
             and upload.id=job.upload_id
            where job.status='leased' and job.attempts>=10
              and job.lease_expires_at<=clock_timestamp() and upload.status='verifying'
            order by job.lease_expires_at,job.seller_organization_id,job.upload_id
            for update of upload skip locked limit p_limit
          loop
            perform 1 from app_private.asset_verification_job job
            where job.seller_organization_id=candidate.seller_organization_id
              and job.upload_id=candidate.upload_id and job.status='leased'
              and job.attempts>=10 and job.lease_expires_at<=clock_timestamp()
            for update;
            if not found then continue; end if;
            update app_private.asset_verification_job set status='failed',
              lease_expires_at=null,updated_at=clock_timestamp()
            where seller_organization_id=candidate.seller_organization_id
              and upload_id=candidate.upload_id;
            update app_private.asset_upload set status='rejected',
              failure_code='verification_attempts_exhausted',updated_at=clock_timestamp()
            where seller_organization_id=candidate.seller_organization_id
              and id=candidate.upload_id;
            perform app_private.enqueue_asset_storage_cleanup(candidate.seller_organization_id,
              candidate.upload_id,'quarantine',candidate.storage_driver,
              candidate.quarantine_bucket,candidate.quarantine_key,
              candidate.observed_version_id);
          end loop;
          for candidate in
            select job.seller_organization_id,job.upload_id,job.attempts,
              upload.storage_driver,upload.quarantine_bucket,upload.ready_bucket,
              upload.quarantine_key,upload.ready_key,upload.observed_version_id,
              upload.expected_byte_size,upload.expected_sha256,upload.expected_media_type,
              upload.file_name
            from app_private.asset_verification_job job
            join app_private.asset_upload upload
              on upload.seller_organization_id=job.seller_organization_id
             and upload.id=job.upload_id
            where job.attempts<10 and job.available_at<=clock_timestamp()
              and (
                (job.status='queued' and upload.status='uploaded')
                or (job.status='leased' and job.lease_expires_at<=clock_timestamp()
                  and upload.status='verifying')
              )
            order by job.available_at,job.created_at,job.seller_organization_id,job.upload_id
            for update of upload skip locked limit p_limit
          loop
            select job.attempts into locked_attempts
            from app_private.asset_verification_job job
            where job.seller_organization_id=candidate.seller_organization_id
              and job.upload_id=candidate.upload_id and job.attempts<10
              and job.available_at<=clock_timestamp() and (
                job.status='queued' or (
                  job.status='leased' and job.lease_expires_at<=clock_timestamp()
                ))
            for update;
            if not found then continue; end if;
            lease_until:=clock_timestamp()+make_interval(secs=>p_lease_seconds);
            update app_private.asset_verification_job set status='leased',worker_id=p_worker,
              lease_expires_at=lease_until,attempts=attempts+1,updated_at=clock_timestamp()
            where seller_organization_id=candidate.seller_organization_id
              and upload_id=candidate.upload_id;
            update app_private.asset_upload set status='verifying',updated_at=clock_timestamp()
            where seller_organization_id=candidate.seller_organization_id
              and id=candidate.upload_id and status in ('uploaded','verifying');
            claimed:=claimed||jsonb_build_array(jsonb_build_object(
              'seller_organization_id',candidate.seller_organization_id,
              'upload_id',candidate.upload_id,'worker_id',p_worker,
              'lease_expires_at',lease_until,'attempt',locked_attempts+1,
              'storage_driver',candidate.storage_driver,
              'quarantine_bucket',candidate.quarantine_bucket,
              'ready_bucket',candidate.ready_bucket,
              'quarantine_key',candidate.quarantine_key,'ready_key',candidate.ready_key,
              'source_version_id',candidate.observed_version_id,
              'expected_byte_size',candidate.expected_byte_size,
              'expected_sha256',candidate.expected_sha256,
              'expected_media_type',candidate.expected_media_type,
              'file_name',candidate.file_name
            ));
          end loop;
          return claimed;
        end $$
        """,
        "revoke all on function app_private.claim_asset_verification_jobs(uuid,text,integer,integer) from public",
        "grant execute on function app_private.claim_asset_verification_jobs(uuid,text,integer,integer) to appsell_runtime",
        """
        create function app_private.claim_asset_storage_cleanup_jobs(
          p_actor uuid,p_worker text,p_limit integer,p_lease_seconds integer
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        declare candidate record; claimed jsonb:='[]'::jsonb; lease_until timestamptz;
        begin
          if not app_private.asset_worker_context_allowed(p_actor) then
            raise exception 'asset worker context is not authorized' using errcode='28000'; end if;
          if p_worker is null or p_worker<>btrim(p_worker)
             or octet_length(p_worker) not between 3 and 120 or p_worker~'[[:cntrl:]]'
             or p_limit not between 1 and 20 or p_lease_seconds not between 30 and 600 then
            raise exception 'invalid asset cleanup claim' using errcode='22023'; end if;
          with exhausted as (
            select seller_organization_id,id
            from app_private.asset_storage_cleanup_job
            where status='leased' and attempts>=10
              and lease_expires_at<=clock_timestamp()
            order by lease_expires_at,seller_organization_id,id
            for update skip locked limit p_limit
          )
          update app_private.asset_storage_cleanup_job cleanup
          set status='dead',failure_code='attempts_exhausted',lease_expires_at=null,
            updated_at=clock_timestamp()
          from exhausted
          where cleanup.seller_organization_id=exhausted.seller_organization_id
            and cleanup.id=exhausted.id;
          for candidate in
            select cleanup.* from app_private.asset_storage_cleanup_job cleanup
            join app_private.asset_upload upload
              on upload.seller_organization_id=cleanup.seller_organization_id
             and upload.id=cleanup.upload_id
            where cleanup.attempts<10 and cleanup.available_at<=clock_timestamp()
              and (cleanup.status='queued' or (
                cleanup.status='leased' and cleanup.lease_expires_at<=clock_timestamp()
              ))
              and (upload.status in ('ready','rejected','cancelled','expired')
                or (upload.status='pending' and upload.expires_at<=clock_timestamp()))
            order by cleanup.available_at,cleanup.created_at,
              cleanup.seller_organization_id,cleanup.id
            for update of upload skip locked limit p_limit
          loop
            perform 1 from app_private.asset_storage_cleanup_job cleanup
            where cleanup.seller_organization_id=candidate.seller_organization_id
              and cleanup.id=candidate.id and cleanup.attempts<10
              and cleanup.available_at<=clock_timestamp()
              and (cleanup.status='queued' or (
                cleanup.status='leased' and cleanup.lease_expires_at<=clock_timestamp()
              )) for update;
            if not found then continue; end if;
            lease_until:=clock_timestamp()+make_interval(secs=>p_lease_seconds);
            update app_private.asset_storage_cleanup_job set status='leased',worker_id=p_worker,
              lease_expires_at=lease_until,attempts=attempts+1,updated_at=clock_timestamp()
            where seller_organization_id=candidate.seller_organization_id and id=candidate.id;
            claimed:=claimed||jsonb_build_array(jsonb_build_object(
              'id',candidate.id,'seller_organization_id',candidate.seller_organization_id,
              'upload_id',candidate.upload_id,'kind',candidate.kind,
              'delete_mode',case when candidate.kind in (
                'quarantine_reconcile','ready_reconcile'
              )
                then 'delete_all_versions' else 'delete_version' end,
              'storage_driver',candidate.storage_driver,'bucket',candidate.bucket,
              'object_key',candidate.object_key,'storage_version_id',candidate.storage_version_id,
              'worker_id',p_worker,'lease_expires_at',lease_until,
              'attempt',candidate.attempts+1
            ));
          end loop;
          return claimed;
        end $$
        """,
        "revoke all on function app_private.claim_asset_storage_cleanup_jobs(uuid,text,integer,integer) from public",
        "grant execute on function app_private.claim_asset_storage_cleanup_jobs(uuid,text,integer,integer) to appsell_runtime",
        """
        create function app_private.complete_asset_storage_cleanup(
          p_actor uuid,p_worker text,p_seller uuid,p_job uuid
        ) returns boolean language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        begin
          if not app_private.asset_worker_context_allowed(p_actor) then
            raise exception 'asset worker context is not authorized' using errcode='28000'; end if;
          update app_private.asset_storage_cleanup_job set status='completed',
            lease_expires_at=null,failure_code=null,updated_at=clock_timestamp()
          where seller_organization_id=p_seller and id=p_job and status='leased'
            and worker_id=p_worker and lease_expires_at>clock_timestamp();
          if not found then raise exception 'asset cleanup lease is unavailable' using errcode='P4091'; end if;
          return true;
        end $$
        """,
        "revoke all on function app_private.complete_asset_storage_cleanup(uuid,text,uuid,uuid) from public",
        "grant execute on function app_private.complete_asset_storage_cleanup(uuid,text,uuid,uuid) to appsell_runtime",
        """
        create function app_private.retry_asset_storage_cleanup(
          p_actor uuid,p_worker text,p_seller uuid,p_job uuid,p_delay_seconds integer,
          p_failure_code text
        ) returns text language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        declare next_status text;
        begin
          if not app_private.asset_worker_context_allowed(p_actor) then
            raise exception 'asset worker context is not authorized' using errcode='28000'; end if;
          if p_delay_seconds not between 1 and 3600 or p_failure_code is null
             or p_failure_code!~'^[a-z0-9_]{3,64}$' then
            raise exception 'invalid asset cleanup retry' using errcode='22023'; end if;
          select case when attempts>=10 then 'dead' else 'queued' end into next_status
          from app_private.asset_storage_cleanup_job
          where seller_organization_id=p_seller and id=p_job and status='leased'
            and worker_id=p_worker and lease_expires_at>clock_timestamp() for update;
          if not found then raise exception 'asset cleanup lease is unavailable' using errcode='P4091'; end if;
          update app_private.asset_storage_cleanup_job set status=next_status,
            available_at=case when next_status='queued' then
              clock_timestamp()+make_interval(secs=>p_delay_seconds) else available_at end,
            failure_code=p_failure_code,lease_expires_at=null,updated_at=clock_timestamp()
          where seller_organization_id=p_seller and id=p_job;
          return next_status;
        end $$
        """,
        "revoke all on function app_private.retry_asset_storage_cleanup(uuid,text,uuid,uuid,integer,text) from public",
        "grant execute on function app_private.retry_asset_storage_cleanup(uuid,text,uuid,uuid,integer,text) to appsell_runtime",
        """
        create function app_private.register_asset_ready_candidate(
          p_actor uuid,p_seller uuid,p_upload uuid,p_worker text,p_ready_version_id text,
          p_ready_etag text,p_ready_size bigint,p_ready_sha text,p_ready_media text,
          p_magic text,p_scanner_result text
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare upload app_private.asset_upload%rowtype;
          job app_private.asset_verification_job%rowtype; cleanup_id uuid;
          lease_valid boolean;
        begin
          if not app_private.asset_worker_context_allowed(p_actor) then
            raise exception 'asset worker context is not authorized' using errcode='28000'; end if;
          if p_ready_version_id is null or octet_length(p_ready_version_id) not between 1 and 512
             or p_ready_etag is null or octet_length(p_ready_etag) not between 1 and 256
             or p_ready_size not between 1 and 10485760
             or p_ready_sha is null or p_ready_sha!~'^[0-9a-f]{64}$'
             or p_ready_media<>'application/pdf' or p_magic<>'application/pdf'
             or p_scanner_result<>'clean' then
            raise exception 'invalid ready asset candidate' using errcode='22023'; end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/reset/'||p_seller::text,0
          ));
          select * into upload from app_private.asset_upload
            where seller_organization_id=p_seller and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          select * into job from app_private.asset_verification_job
            where seller_organization_id=p_seller and upload_id=p_upload for update;
          lease_valid:=found and job.status='leased' and job.worker_id=p_worker
            and job.lease_expires_at>clock_timestamp() and upload.status='verifying';
          insert into app_private.asset_storage_cleanup_job(
            seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
            storage_version_id,storage_etag,byte_size,sha256,media_type,status,available_at
          ) values(p_seller,p_upload,'ready_orphan',upload.storage_driver,
            upload.ready_bucket,upload.ready_key,p_ready_version_id,p_ready_etag,
            p_ready_size,p_ready_sha,p_ready_media,'queued',case when lease_valid
              then clock_timestamp()+interval '5 minutes' else clock_timestamp() end)
          on conflict do nothing;
          select id into cleanup_id from app_private.asset_storage_cleanup_job cleanup
            where cleanup.seller_organization_id=p_seller and cleanup.upload_id=p_upload
              and cleanup.kind='ready_orphan' and cleanup.bucket=upload.ready_bucket
              and cleanup.object_key=upload.ready_key
              and cleanup.storage_version_id=p_ready_version_id
              and cleanup.storage_etag=p_ready_etag and cleanup.byte_size=p_ready_size
              and cleanup.sha256=p_ready_sha and cleanup.media_type=p_ready_media
            for update;
          if not found then raise exception 'ready asset candidate conflicts' using errcode='23505'; end if;
          if not lease_valid then
            update app_private.asset_storage_cleanup_job set
              available_at=least(available_at,clock_timestamp()),updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=cleanup_id and status='queued';
          end if;
          return jsonb_build_object('cleanup_job_id',cleanup_id,'upload_id',p_upload,
            'ready_version_id',p_ready_version_id,'registered',true,
            'reconciling',not lease_valid);
        end $$
        """,
        "revoke all on function app_private.register_asset_ready_candidate(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text) from public",
        "grant execute on function app_private.register_asset_ready_candidate(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text) to appsell_runtime",
        """
        create function app_private.finalize_asset_verification(
          p_actor uuid,p_seller uuid,p_upload uuid,p_worker text,
          p_source_size bigint,p_source_sha text,p_ready_size bigint,p_ready_sha text,
          p_ready_media text,p_magic text,
          p_scanner_result text,p_ready_version_id text,p_ready_etag text,p_verdict text,
          p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype; job app_private.asset_verification_job%rowtype;
          replay jsonb; next_position integer; next_course_version bigint; result jsonb;
          accepted boolean; quota_exceeded boolean:=false; ready_bytes bigint;
          ready_cleanup_id uuid;
        begin
          if p_worker is null or p_idempotency is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' or p_verdict not in ('ready','rejected')
             or p_source_sha is null or p_source_sha!~'^[0-9a-f]{64}$'
             or p_ready_sha is null or p_ready_sha!~'^[0-9a-f]{64}$'
             or p_source_size not between 1 and 10485760
             or p_ready_size not between 1 and 10485760 then
            raise exception 'invalid asset verification result' using errcode='22023'; end if;
          if not app_private.asset_worker_context_allowed(p_actor) then
            raise exception 'asset worker context is not authorized' using errcode='28000';
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/reset/'||p_seller::text,0
          ));
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/upload.finalize/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'upload.finalize',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,candidate.asset_id,p_upload);
          if replay is not null then return jsonb_set(replay,'{applied}','false'::jsonb,true); end if;
          select * into job from app_private.asset_verification_job
            where seller_organization_id=p_seller and upload_id=p_upload for update;
          if not found or job.status<>'leased' or job.worker_id<>p_worker
             or job.lease_expires_at<=clock_timestamp() or candidate.status<>'verifying' then
            raise exception 'asset verification lease is unavailable' using errcode='P4091';
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/quota/'||p_seller::text,0
          ));
          select coalesce(sum(byte_size),0) into ready_bytes from asset_blob
            where seller_organization_id=p_seller and storage_driver='s3';
          quota_exceeded:=ready_bytes+p_ready_size>1073741824;
          select cleanup.id into ready_cleanup_id
          from app_private.asset_storage_cleanup_job cleanup
          where cleanup.seller_organization_id=p_seller and cleanup.upload_id=p_upload
            and cleanup.kind='ready_orphan' and cleanup.bucket=candidate.ready_bucket
            and cleanup.object_key=candidate.ready_key
            and cleanup.storage_version_id=p_ready_version_id
            and cleanup.storage_etag=p_ready_etag and cleanup.byte_size=p_ready_size
            and cleanup.sha256=p_ready_sha and cleanup.media_type=p_ready_media
            and cleanup.status='queued' for update;
          accepted:=p_verdict='ready' and p_source_size=candidate.expected_byte_size
            and p_source_sha=candidate.expected_sha256
            and p_ready_media=candidate.expected_media_type
            and p_magic='application/pdf' and p_scanner_result='clean'
            and p_ready_version_id is not null and p_ready_etag is not null
            and ready_cleanup_id is not null and not quota_exceeded;
          if not accepted then
            update app_private.asset_upload set status='rejected',failure_code=case
              when p_source_size<>candidate.expected_byte_size
                or p_source_sha<>candidate.expected_sha256 then 'source_mismatch'
              when quota_exceeded then 'ready_quota_exceeded'
              when p_verdict='rejected' then 'scanner_rejected'
              else 'ready_verification_mismatch' end,
              updated_at=clock_timestamp() where seller_organization_id=p_seller and id=p_upload;
            update app_private.asset_verification_job set status='failed',lease_expires_at=null,
              updated_at=clock_timestamp() where seller_organization_id=p_seller and upload_id=p_upload;
            perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
              candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
              candidate.observed_version_id);
            if p_ready_version_id is not null and p_ready_version_id<>'' then
              perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'ready_orphan',
                candidate.storage_driver,candidate.ready_bucket,candidate.ready_key,
                p_ready_version_id);
            end if;
            result:=jsonb_build_object('upload_id',p_upload,'asset_id',candidate.asset_id,
              'status','rejected','ready',false,'applied',true);
            perform app_private.record_asset_mutation(p_seller,'upload.finalize',p_idempotency,
              p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
              candidate.module_id,candidate.lesson_id,
              candidate.asset_id,p_upload,result);
            return result;
          end if;
          perform 1 from product_course linked
          join course on course.seller_organization_id=linked.seller_organization_id
            and course.id=linked.course_id
          join course_module module on module.seller_organization_id=course.seller_organization_id
            and module.course_id=course.id and module.id=candidate.module_id
          join lesson on lesson.seller_organization_id=course.seller_organization_id
            and lesson.course_id=course.id and lesson.module_id=module.id
            and lesson.id=candidate.lesson_id
          where linked.seller_organization_id=p_seller
            and linked.catalog_product_id=candidate.catalog_product_id
            and linked.course_id=candidate.course_id and course.status<>'archived'
            and module.status='active' and lesson.status='active'
          for no key update of course,module,lesson;
          if not found then raise exception 'asset lesson is unavailable' using errcode='28000'; end if;
          insert into asset_blob(seller_organization_id,id,storage_driver,storage_bucket,
            object_key,storage_version_id,storage_etag,file_name,media_type,byte_size,
            sha256,magic_type,scanner_result,verified_at)
          values(p_seller,candidate.blob_id,candidate.storage_driver,candidate.ready_bucket,
            candidate.ready_key,p_ready_version_id,p_ready_etag,candidate.file_name,
            p_ready_media,p_ready_size,p_ready_sha,p_magic,p_scanner_result,
            clock_timestamp());
          insert into asset_version(seller_organization_id,asset_id,version,blob_id,
            actor_identity_user_id) values(p_seller,candidate.asset_id,
            candidate.reserved_version,candidate.blob_id,p_actor);
          select coalesce(max(position),-1)+1 into next_position from lesson_asset_draft
            where seller_organization_id=p_seller and lesson_id=candidate.lesson_id and status='active';
          insert into lesson_asset_draft(seller_organization_id,catalog_product_id,course_id,
            module_id,lesson_id,asset_id,asset_version,blob_id,position,status)
          values(p_seller,candidate.catalog_product_id,candidate.course_id,candidate.module_id,
            candidate.lesson_id,candidate.asset_id,candidate.reserved_version,
            candidate.blob_id,next_position,'active')
          on conflict(seller_organization_id,lesson_id,asset_id) do update set
            asset_version=excluded.asset_version,blob_id=excluded.blob_id,status='active',
            updated_at=clock_timestamp();
          update asset_record set title=candidate.title,status='active',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=candidate.asset_id;
          update course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=candidate.course_id
            returning draft_version into next_course_version;
          update app_private.asset_upload set status='ready',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_upload;
          update app_private.asset_verification_job set status='completed',lease_expires_at=null,
            updated_at=clock_timestamp() where seller_organization_id=p_seller and upload_id=p_upload;
          perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
            candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
            candidate.observed_version_id);
          update app_private.asset_storage_cleanup_job set status='protected',
            lease_expires_at=null,updated_at=clock_timestamp()
          where seller_organization_id=p_seller and id=ready_cleanup_id;
          update app_private.asset_storage_cleanup_job set status='protected',
            lease_expires_at=null,updated_at=clock_timestamp()
          where seller_organization_id=p_seller and upload_id=p_upload
            and kind='ready_reconcile';
          result:=jsonb_build_object('upload_id',p_upload,'asset_id',candidate.asset_id,
            'asset_version',candidate.reserved_version,'blob_id',candidate.blob_id,
            'course_version',next_course_version,'status','ready','ready',true,'applied',true);
          perform app_private.record_asset_mutation(p_seller,'upload.finalize',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,
            candidate.asset_id,p_upload,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_verified','asset',candidate.asset_id,
            'succeeded',p_request,jsonb_build_object('upload_id',p_upload,
              'asset_version',candidate.reserved_version,'course_version',next_course_version));
          return result;
        end $$
        """,
        "revoke all on function app_private.finalize_asset_verification(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid) from public",
        "grant execute on function app_private.finalize_asset_verification(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.list_lesson_asset_drafts(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare result jsonb;
        begin
          perform app_private.lock_curriculum_access(
            p_actor,p_seller,p_product,p_course,'catalog.asset.read'
          );
          perform 1 from lesson where seller_organization_id=p_seller
            and course_id=p_course and module_id=p_module and id=p_lesson and status='active';
          if not found then raise exception 'asset lesson is unavailable' using errcode='28000'; end if;
          select coalesce(jsonb_agg(jsonb_build_object(
            'asset_id',draft.asset_id,'asset_version',draft.asset_version,
            'title',asset.title,'file_name',blob.file_name,'media_type',blob.media_type,
            'byte_size',blob.byte_size,'sha256',blob.sha256,'position',draft.position,
            'status',draft.status
          ) order by draft.position,draft.asset_id),'[]'::jsonb) into result
          from lesson_asset_draft draft
          join asset_record asset on asset.seller_organization_id=draft.seller_organization_id
            and asset.id=draft.asset_id
          join asset_blob blob on blob.seller_organization_id=draft.seller_organization_id
            and blob.id=draft.blob_id
          where draft.seller_organization_id=p_seller and draft.catalog_product_id=p_product
            and draft.course_id=p_course and draft.module_id=p_module
            and draft.lesson_id=p_lesson;
          return result;
        end $$
        """,
        "revoke all on function app_private.list_lesson_asset_drafts(uuid,uuid,uuid,uuid,uuid,uuid) from public",
        "grant execute on function app_private.list_lesson_asset_drafts(uuid,uuid,uuid,uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.read_lesson_asset_uploads(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,p_lesson uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare result jsonb;
        begin
          perform app_private.lock_curriculum_access(
            p_actor,p_seller,p_product,p_course,'catalog.asset.read'
          );
          perform 1 from lesson where seller_organization_id=p_seller
            and course_id=p_course and module_id=p_module and id=p_lesson and status='active';
          if not found then raise exception 'asset lesson is unavailable' using errcode='28000'; end if;
          select coalesce(jsonb_agg(jsonb_build_object(
            'upload_id',upload.id,'asset_id',upload.asset_id,
            'reserved_version',upload.reserved_version,'status',upload.status,
            'title',upload.title,'file_name',upload.file_name,
            'byte_size',upload.expected_byte_size,'safe_reason',case upload.failure_code
              when 'intent_expired' then 'expired'
              when 'head_mismatch' then 'upload_mismatch'
              when 'source_mismatch' then 'upload_mismatch'
              when 'scanner_rejected' then 'verification_failed'
              when 'ready_verification_mismatch' then 'verification_failed'
              when 'verification_attempts_exhausted' then 'verification_failed'
              when 'ready_quota_exceeded' then 'quota_exceeded'
              else case when upload.status='cancelled' then 'cancelled' end end,
            'course_version',course.draft_version,'created_at',upload.created_at,
            'updated_at',upload.updated_at,'expires_at',upload.expires_at,
            'ready_asset',case when upload.status='ready' then jsonb_build_object(
              'asset_id',upload.asset_id,'asset_version',upload.reserved_version,
              'title',asset.title,'file_name',blob.file_name,'media_type',blob.media_type,
              'byte_size',blob.byte_size,'sha256',blob.sha256
            ) end
          ) order by upload.created_at desc,upload.id desc),'[]'::jsonb) into result
          from (
            select * from app_private.asset_upload candidate
            where candidate.seller_organization_id=p_seller
              and candidate.catalog_product_id=p_product and candidate.course_id=p_course
              and candidate.module_id=p_module and candidate.lesson_id=p_lesson
            order by candidate.created_at desc,candidate.id desc limit 100
          ) upload
          join course on course.seller_organization_id=upload.seller_organization_id
            and course.id=upload.course_id
          left join asset_record asset
            on asset.seller_organization_id=upload.seller_organization_id
           and asset.id=upload.asset_id
          left join asset_blob blob
            on blob.seller_organization_id=upload.seller_organization_id
           and blob.id=upload.blob_id;
          return result;
        end $$
        """,
        "revoke all on function app_private.read_lesson_asset_uploads(uuid,uuid,uuid,uuid,uuid,uuid) from public",
        "grant execute on function app_private.read_lesson_asset_uploads(uuid,uuid,uuid,uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.read_asset_upload_status(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare upload app_private.asset_upload%rowtype; result jsonb;
        begin
          select * into upload from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,upload.catalog_product_id,
            upload.course_id,'catalog.asset.read');
          select jsonb_build_object(
            'upload_id',upload.id,'asset_id',upload.asset_id,
            'reserved_version',upload.reserved_version,'status',upload.status,
            'title',upload.title,'file_name',upload.file_name,
            'byte_size',upload.expected_byte_size,'safe_reason',case upload.failure_code
              when 'intent_expired' then 'expired'
              when 'head_mismatch' then 'upload_mismatch'
              when 'source_mismatch' then 'upload_mismatch'
              when 'scanner_rejected' then 'verification_failed'
              when 'ready_verification_mismatch' then 'verification_failed'
              when 'verification_attempts_exhausted' then 'verification_failed'
              when 'ready_quota_exceeded' then 'quota_exceeded'
              else case when upload.status='cancelled' then 'cancelled' end end,
            'course_version',course.draft_version,'created_at',upload.created_at,
            'updated_at',upload.updated_at,'expires_at',upload.expires_at,
            'ready_asset',case when upload.status='ready' then jsonb_build_object(
              'asset_id',upload.asset_id,'asset_version',upload.reserved_version,
              'title',asset.title,'file_name',blob.file_name,'media_type',blob.media_type,
              'byte_size',blob.byte_size,'sha256',blob.sha256
            ) end
          ) into result
          from course
          left join asset_record asset
            on asset.seller_organization_id=upload.seller_organization_id
           and asset.id=upload.asset_id
          left join asset_blob blob
            on blob.seller_organization_id=upload.seller_organization_id
           and blob.id=upload.blob_id
          where course.seller_organization_id=upload.seller_organization_id
            and course.id=upload.course_id;
          return result;
        end $$
        """,
        "revoke all on function app_private.read_asset_upload_status(uuid,uuid,uuid,uuid,uuid,uuid,uuid) from public",
        "grant execute on function app_private.read_asset_upload_status(uuid,uuid,uuid,uuid,uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.cancel_asset_upload(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_upload uuid,p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare candidate app_private.asset_upload%rowtype; replay jsonb; result jsonb;
        begin
          if p_idempotency is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid asset upload cancellation' using errcode='22023'; end if;
          select * into candidate from app_private.asset_upload
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and module_id=p_module and lesson_id=p_lesson
              and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          perform app_private.lock_curriculum_access(p_actor,p_seller,candidate.catalog_product_id,
            candidate.course_id,'catalog.asset.upload.cancel');
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/upload.cancel/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'upload.cancel',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,candidate.asset_id,p_upload);
          if replay is not null then return jsonb_set(replay,'{cancelled}','false'::jsonb,true); end if;
          if candidate.identity_user_id<>p_actor
             or candidate.status not in ('pending','uploaded','verifying') then
            raise exception 'asset upload cannot be cancelled' using errcode='P4091'; end if;
          update app_private.asset_upload set status='cancelled',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_upload;
          update app_private.asset_verification_job set status='failed',
            lease_expires_at=null,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and upload_id=p_upload
              and status in ('queued','leased');
          if candidate.observed_version_id is not null then
            perform app_private.enqueue_asset_storage_cleanup(p_seller,p_upload,'quarantine',
              candidate.storage_driver,candidate.quarantine_bucket,candidate.quarantine_key,
              candidate.observed_version_id);
          end if;
          result:=jsonb_build_object('upload_id',p_upload,'status','cancelled','cancelled',true);
          perform app_private.record_asset_mutation(p_seller,'upload.cancel',p_idempotency,
            p_actor,p_body,candidate.catalog_product_id,candidate.course_id,
            candidate.module_id,candidate.lesson_id,
            candidate.asset_id,p_upload,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_upload_cancelled','asset',candidate.asset_id,
            'succeeded',p_request,jsonb_build_object('upload_id',p_upload));
          return result;
        end $$
        """,
        "revoke all on function app_private.cancel_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,uuid) from public",
        "grant execute on function app_private.cancel_asset_upload(uuid,uuid,uuid,uuid,uuid,uuid,uuid,uuid,text,uuid) to appsell_runtime",
        """
        create function app_private.archive_lesson_asset(
          p_actor uuid,p_seller uuid,p_product uuid,p_course uuid,p_module uuid,
          p_lesson uuid,p_asset uuid,
          p_expected_course_version bigint,p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare replay jsonb; next_version bigint; result jsonb;
        begin
          if p_asset is null or p_idempotency is null or p_request is null or p_body is null
             or p_body!~'^[0-9a-f]{64}$' then
            raise exception 'invalid asset archive request' using errcode='22023'; end if;
          perform app_private.lock_curriculum_access(
            p_actor,p_seller,p_product,p_course,'catalog.asset.archive'
          );
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/'||p_seller::text||'/asset.archive/'||p_idempotency::text,0));
          replay:=app_private.asset_mutation_replay(p_seller,'asset.archive',p_idempotency,
            p_actor,p_body,p_product,p_course,p_module,p_lesson,p_asset,null);
          if replay is not null then return jsonb_set(replay,'{archived}','false'::jsonb,true); end if;
          perform app_private.lock_curriculum_course(p_seller,p_course,p_expected_course_version);
          update lesson_asset_draft set status='archived',updated_at=clock_timestamp()
            where seller_organization_id=p_seller and catalog_product_id=p_product
              and course_id=p_course and lesson_id=p_lesson and asset_id=p_asset
              and module_id=p_module
              and status='active';
          if not found then raise exception 'asset draft is unavailable' using errcode='28000'; end if;
          update course set draft_version=draft_version+1,updated_at=clock_timestamp()
            where seller_organization_id=p_seller and id=p_course
            returning draft_version into next_version;
          result:=jsonb_build_object('asset_id',p_asset,'course_version',next_version,
            'status','archived','archived',true);
          perform app_private.record_asset_mutation(p_seller,'asset.archive',p_idempotency,
            p_actor,p_body,p_product,p_course,p_module,p_lesson,p_asset,null,result);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller,p_actor,'catalog.asset_archived','asset',p_asset,'succeeded',p_request,
            jsonb_build_object('lesson_id',p_lesson,'course_version',next_version));
          return result;
        end $$
        """,
        "revoke all on function app_private.archive_lesson_asset(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid) from public",
        "grant execute on function app_private.archive_lesson_asset(uuid,uuid,uuid,uuid,uuid,uuid,uuid,bigint,uuid,text,uuid) to appsell_runtime",
        """
        do $$
        begin
          if exists(
            select 1 from learning_asset legacy
            left join product_course linked
              on linked.seller_organization_id=legacy.seller_organization_id
             and linked.catalog_product_id=legacy.catalog_product_id
             and linked.course_id=legacy.course_id
            left join lesson source
              on source.seller_organization_id=legacy.seller_organization_id
             and source.course_id=legacy.course_id and source.module_id=legacy.module_id
             and source.id=legacy.lesson_id
            where linked.course_id is null or source.id is null
              or legacy.media_type<>'application/pdf' or legacy.byte_size not between 1 and 10485760
              or legacy.sha256!~'^[0-9a-f]{64}$'
          ) then
            raise exception using errcode='23514',
              message='0008_private_asset_storage cannot safely pin legacy assets',
              hint='repair or remove invalid learning_asset rows before upgrading';
          end if;
        end $$
        """,
        """
        insert into asset_record(seller_organization_id,id,catalog_product_id,title,status,
          created_at,updated_at)
        select seller_organization_id,id,catalog_product_id,title,status,created_at,updated_at
        from learning_asset
        """,
        """
        create function app_private.legacy_asset_blob_id(p_asset uuid)
        returns uuid language sql immutable strict security definer
        set search_path=pg_catalog as $$
          select (substr(md5(p_asset::text||'appsell-legacy-blob-v1'),1,8)||'-'||
            substr(md5(p_asset::text||'appsell-legacy-blob-v1'),9,4)||'-5'||
            substr(md5(p_asset::text||'appsell-legacy-blob-v1'),14,3)||'-a'||
            substr(md5(p_asset::text||'appsell-legacy-blob-v1'),18,3)||'-'||
            substr(md5(p_asset::text||'appsell-legacy-blob-v1'),21,12))::uuid
        $$
        """,
        "revoke all on function app_private.legacy_asset_blob_id(uuid) from public,appsell_runtime",
        """
        insert into asset_blob(seller_organization_id,id,storage_driver,storage_bucket,
          object_key,storage_version_id,storage_etag,file_name,media_type,byte_size,sha256,
          magic_type,scanner_result,verified_at,created_at)
        select seller_organization_id,app_private.legacy_asset_blob_id(id),
          'legacy_bundle','codex-demo',file_name,null,sha256,file_name,media_type,
          byte_size,sha256,'application/pdf','legacy_trusted',created_at,created_at
        from learning_asset
        """,
        """
        insert into asset_version(seller_organization_id,asset_id,version,blob_id,
          actor_identity_user_id,created_at)
        select legacy.seller_organization_id,legacy.id,1,
          app_private.legacy_asset_blob_id(legacy.id),coalesce(
            (select registry.admin_identity_user_id from app_private.demo_tenant_registry registry
              where registry.seller_organization_id=legacy.seller_organization_id),
            (select membership.identity_user_id from seller_membership membership
              where membership.seller_organization_id=legacy.seller_organization_id
                and membership.status='active' order by
                  case membership.role when 'seller_owner' then 0 else 1 end,
                  membership.identity_user_id limit 1)
          ),legacy.created_at
        from learning_asset legacy
        """,
        """
        do $$ begin
          if exists(select 1 from asset_version where actor_identity_user_id is null) then
            raise exception using errcode='23514',
              message='0008_private_asset_storage cannot attribute a legacy asset version',
              hint='add an active seller owner/admin before upgrading';
          end if;
        end $$
        """,
        """
        insert into lesson_asset_draft(seller_organization_id,catalog_product_id,course_id,
          module_id,lesson_id,asset_id,asset_version,blob_id,position,status,created_at,updated_at)
        select seller_organization_id,catalog_product_id,course_id,module_id,lesson_id,id,1,
          app_private.legacy_asset_blob_id(id),position,status,created_at,updated_at
        from learning_asset
        """,
        """
        insert into publication_asset(seller_organization_id,catalog_product_id,publication_id,
          course_id,module_id,lesson_id,asset_id,asset_version,blob_id,position,title,file_name,
          media_type,byte_size,sha256,storage_driver,storage_bucket,object_key,
          storage_version_id,storage_etag,created_at)
        select projected.seller_organization_id,projected.catalog_product_id,
          projected.publication_id,projected.course_id,projected.module_id,projected.lesson_id,
          legacy.id,1,blob.id,legacy.position,legacy.title,blob.file_name,blob.media_type,
          blob.byte_size,blob.sha256,blob.storage_driver,blob.storage_bucket,blob.object_key,
          blob.storage_version_id,blob.storage_etag,blob.created_at
        from publication_lesson projected
        join learning_asset legacy
          on legacy.seller_organization_id=projected.seller_organization_id
         and legacy.catalog_product_id=projected.catalog_product_id
         and legacy.course_id=projected.course_id and legacy.module_id=projected.module_id
         and legacy.lesson_id=projected.lesson_id and legacy.status='active'
        join asset_blob blob on blob.seller_organization_id=legacy.seller_organization_id
          and blob.id=app_private.legacy_asset_blob_id(legacy.id)
        """,
        "alter table publication drop constraint publication_schema_version_check",
        "alter table publication add constraint publication_schema_version_check check(schema_version in (1,2,3))",
        """
        create or replace function app_private.curriculum_courses_snapshot(p_seller uuid,p_product uuid)
        returns jsonb language sql stable security definer
        set search_path=pg_catalog,public as $$
          select coalesce(jsonb_agg(jsonb_build_object(
            'id',course.id,'title',course.title,'description',course.description,
            'position',linked.position,
            'modules',coalesce((
              select jsonb_agg(jsonb_build_object(
                'id',module.id,'title',module.title,'status','active','position',module.position,
                'lessons',coalesce((
                  select jsonb_agg(jsonb_build_object(
                    'id',lesson.id,'title',lesson.title,'kind','text',
                    'content',lesson.body_text,'status','active','position',lesson.position,
                    'assets',coalesce((
                      select jsonb_agg(jsonb_build_object(
                        'id',draft.asset_id,'version',draft.asset_version,
                        'title',asset.title,'file_name',blob.file_name,
                        'media_type',blob.media_type,'byte_size',blob.byte_size,
                        'sha256',blob.sha256,'position',draft.position
                      ) order by draft.position,draft.asset_id)
                      from lesson_asset_draft draft
                      join asset_record asset
                        on asset.seller_organization_id=draft.seller_organization_id
                       and asset.id=draft.asset_id and asset.status='active'
                      join asset_blob blob
                        on blob.seller_organization_id=draft.seller_organization_id
                       and blob.id=draft.blob_id
                      where draft.seller_organization_id=p_seller
                        and draft.catalog_product_id=p_product
                        and draft.course_id=course.id and draft.module_id=module.id
                        and draft.lesson_id=lesson.id and draft.status='active'
                    ),'[]'::jsonb)
                  ) order by lesson.position,lesson.id)
                  from lesson where lesson.seller_organization_id=p_seller
                    and lesson.course_id=course.id and lesson.module_id=module.id
                    and lesson.status='active'
                ),'[]'::jsonb)
              ) order by module.position,module.id)
              from course_module module where module.seller_organization_id=p_seller
                and module.course_id=course.id and module.status='active'
            ),'[]'::jsonb)
          ) order by linked.position,course.id),'[]'::jsonb)
          from product_course linked join course
            on course.seller_organization_id=linked.seller_organization_id
           and course.id=linked.course_id
          where linked.seller_organization_id=p_seller
            and linked.catalog_product_id=p_product
        $$
        """,
        "revoke all on function app_private.curriculum_courses_snapshot(uuid,uuid) from public",
        """
        create or replace function app_private.project_publication(
          p_seller uuid,p_product uuid,p_publication uuid,p_snapshot jsonb
        ) returns void language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare course_item jsonb; module_item jsonb; lesson_item jsonb; asset_item jsonb;
          resolved record;
        begin
          if p_snapshot->>'schema_version' not in ('2','3') then return; end if;
          if p_snapshot->>'seller_organization_id'<>p_seller::text
             or p_snapshot->'product'->>'id'<>p_product::text
             or jsonb_typeof(p_snapshot->'courses')<>'array' then
            raise exception 'invalid publication projection' using errcode='22023';
          end if;
          for course_item in select value from jsonb_array_elements(p_snapshot->'courses') loop
            insert into publication_course(seller_organization_id,catalog_product_id,
              publication_id,course_id,position,lesson_count)
            values(p_seller,p_product,p_publication,(course_item->>'id')::uuid,
              (course_item->>'position')::integer,
              (select count(*) from jsonb_array_elements(course_item->'modules') module_value,
                jsonb_array_elements(module_value->'lessons') lesson_value));
            for module_item in select value from jsonb_array_elements(course_item->'modules') loop
              for lesson_item in select value from jsonb_array_elements(module_item->'lessons') loop
                insert into publication_lesson(seller_organization_id,catalog_product_id,
                  publication_id,course_id,module_id,lesson_id,course_position,
                  module_position,lesson_position)
                values(p_seller,p_product,p_publication,(course_item->>'id')::uuid,
                  (module_item->>'id')::uuid,(lesson_item->>'id')::uuid,
                  (course_item->>'position')::integer,(module_item->>'position')::integer,
                  (lesson_item->>'position')::integer);
                if p_snapshot->>'schema_version'='3' then
                  if jsonb_typeof(lesson_item->'assets')<>'array' then
                    raise exception 'invalid publication asset projection' using errcode='22023';
                  end if;
                  for asset_item in select value from jsonb_array_elements(lesson_item->'assets') loop
                    select draft.asset_id,draft.asset_version,draft.blob_id,draft.position,
                      asset.title,blob.file_name,blob.media_type,blob.byte_size,blob.sha256,
                      blob.storage_driver,blob.storage_bucket,blob.object_key,
                      blob.storage_version_id,blob.storage_etag
                    into resolved
                    from lesson_asset_draft draft
                    join asset_record asset
                      on asset.seller_organization_id=draft.seller_organization_id
                     and asset.id=draft.asset_id and asset.status='active'
                    join asset_blob blob
                      on blob.seller_organization_id=draft.seller_organization_id
                     and blob.id=draft.blob_id
                    where draft.seller_organization_id=p_seller
                      and draft.catalog_product_id=p_product
                      and draft.course_id=(course_item->>'id')::uuid
                      and draft.module_id=(module_item->>'id')::uuid
                      and draft.lesson_id=(lesson_item->>'id')::uuid
                      and draft.asset_id=(asset_item->>'id')::uuid
                      and draft.asset_version=(asset_item->>'version')::integer
                      and draft.status='active';
                    if not found or asset_item->>'title'<>resolved.title
                       or asset_item->>'file_name'<>resolved.file_name
                       or asset_item->>'media_type'<>resolved.media_type
                       or (asset_item->>'byte_size')::bigint<>resolved.byte_size
                       or asset_item->>'sha256'<>resolved.sha256
                       or (asset_item->>'position')::integer<>resolved.position then
                      raise exception 'publication asset changed during projection' using errcode='40001';
                    end if;
                    insert into publication_asset(seller_organization_id,catalog_product_id,
                      publication_id,course_id,module_id,lesson_id,asset_id,asset_version,
                      blob_id,position,title,file_name,media_type,byte_size,sha256,
                      storage_driver,storage_bucket,object_key,storage_version_id,storage_etag)
                    values(p_seller,p_product,p_publication,(course_item->>'id')::uuid,
                      (module_item->>'id')::uuid,(lesson_item->>'id')::uuid,
                      resolved.asset_id,resolved.asset_version,resolved.blob_id,resolved.position,
                      resolved.title,resolved.file_name,resolved.media_type,resolved.byte_size,
                      resolved.sha256,resolved.storage_driver,resolved.storage_bucket,
                      resolved.object_key,resolved.storage_version_id,resolved.storage_etag);
                  end loop;
                end if;
              end loop;
            end loop;
          end loop;
        end $$
        """,
        "revoke all on function app_private.project_publication(uuid,uuid,uuid,jsonb) from public,appsell_runtime",
        """
        create or replace function app_private.publish_product(
          p_identity_user_id uuid,p_seller_organization_id uuid,p_catalog_product_id uuid,
          p_offer_id uuid,p_expected_product_version bigint,p_expected_offer_version bigint,
          p_expected_course_versions jsonb,p_idempotency_key uuid,p_body_sha256 text,p_request_id uuid
        ) returns table(publication_id uuid,publication_version bigint,product_version bigint,
                        source_sha256 text,created boolean)
        language plpgsql security definer set search_path=pg_catalog,app_private,public as $$
        declare existing_request app_private.catalog_mutation_request%rowtype;
          locked_product catalog_product%rowtype; locked_offer offer%rowtype;
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
             or p_expected_course_versions is null
             or jsonb_typeof(p_expected_course_versions)<>'object'
             or p_expected_course_versions='{}'::jsonb or p_idempotency_key is null
             or p_request_id is null or p_body_sha256 is null
             or p_body_sha256!~'^[0-9a-f]{64}$' then
            raise exception 'invalid catalog publication request' using errcode='22023'; end if;
          for expected_key,expected_value in
            select key,value from jsonb_each_text(p_expected_course_versions)
          loop
            if expected_key!~'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
               or expected_value!~'^[1-9][0-9]{0,18}$' then
              raise exception 'invalid expected course versions' using errcode='22023'; end if;
          end loop;
          select count(*) into expected_course_count from jsonb_object_keys(p_expected_course_versions);
          perform 1 from seller_organization organization join seller_membership membership
            on membership.seller_organization_id=organization.id
          where organization.id=p_seller_organization_id and organization.status='active'
            and membership.identity_user_id=p_identity_user_id
            and membership.role='seller_owner' and membership.status='active'
          for share of organization,membership;
          if not found then raise exception 'catalog publication actor is not authorized' using errcode='28000'; end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.catalog.mutation:'||p_seller_organization_id::text||
            '/product.publish/'||p_idempotency_key::text,0));
          select * into existing_request from app_private.catalog_mutation_request request
          where request.seller_organization_id=p_seller_organization_id
            and request.operation='product.publish' and request.idempotency_key=p_idempotency_key;
          if found then
            if existing_request.identity_user_id=p_identity_user_id
               and existing_request.body_sha256=p_body_sha256
               and existing_request.catalog_product_id=p_catalog_product_id
               and existing_request.offer_id=p_offer_id then
              return query select existing_request.publication_id,
                (existing_request.result->>'publication_version')::bigint,
                (existing_request.result->>'product_version')::bigint,
                existing_request.result->>'source_sha256',false; return;
            end if;
            raise exception 'catalog publication idempotency conflict' using errcode='23505';
          end if;
          select * into locked_product from catalog_product product
            where product.seller_organization_id=p_seller_organization_id
              and product.id=p_catalog_product_id for update;
          if not found then raise exception 'catalog product is unavailable' using errcode='28000'; end if;
          if locked_product.status='archived' then
            raise exception 'archived catalog product cannot be published' using errcode='55000'; end if;
          if locked_product.version<>p_expected_product_version then
            raise exception 'catalog product version conflict' using errcode='40001'; end if;
          select * into locked_offer from offer candidate
            where candidate.seller_organization_id=p_seller_organization_id
              and candidate.catalog_product_id=p_catalog_product_id and candidate.id=p_offer_id
            for no key update;
          if not found then raise exception 'catalog offer is unavailable' using errcode='28000'; end if;
          if locked_offer.status<>'draft' then
            raise exception 'catalog offer is not publishable' using errcode='55000'; end if;
          if locked_offer.version<>p_expected_offer_version then
            raise exception 'catalog offer version conflict' using errcode='40001'; end if;
          if exists(
            select 1 from app_private.asset_upload pending
            where pending.seller_organization_id=p_seller_organization_id
              and pending.catalog_product_id=p_catalog_product_id
              and (pending.status in ('uploaded','verifying') or (
                pending.status='pending' and pending.expires_at>clock_timestamp()
              ))
          ) then raise exception 'asset upload is not terminal' using errcode='P4091'; end if;
          perform 1 from product_course linked join course
            on course.seller_organization_id=linked.seller_organization_id
           and course.id=linked.course_id
          where linked.seller_organization_id=p_seller_organization_id
            and linked.catalog_product_id=p_catalog_product_id
          order by course.id,linked.position for no key update of linked,course;
          get diagnostics linked_course_count=row_count;
          if linked_course_count=0 or linked_course_count<>expected_course_count or exists(
            select 1 from product_course linked join course
              on course.seller_organization_id=linked.seller_organization_id
             and course.id=linked.course_id
            where linked.seller_organization_id=p_seller_organization_id
              and linked.catalog_product_id=p_catalog_product_id and (
                course.status='archived'
                or p_expected_course_versions->>course.id::text is null
                or (p_expected_course_versions->>course.id::text)::numeric<>course.draft_version
              )
          ) then raise exception 'catalog course version conflict' using errcode='40001'; end if;
          if exists(
            select 1 from lesson_asset_draft draft
            left join asset_record asset
              on asset.seller_organization_id=draft.seller_organization_id
             and asset.id=draft.asset_id and asset.status='active'
            left join asset_version version
              on version.seller_organization_id=draft.seller_organization_id
             and version.asset_id=draft.asset_id and version.version=draft.asset_version
             and version.blob_id=draft.blob_id
            left join asset_blob blob
              on blob.seller_organization_id=draft.seller_organization_id
             and blob.id=draft.blob_id
            where draft.seller_organization_id=p_seller_organization_id
              and draft.catalog_product_id=p_catalog_product_id and draft.status='active'
              and (asset.id is null or version.asset_id is null or blob.id is null)
          ) then raise exception 'asset draft is not publishable' using errcode='55000'; end if;
          generated_snapshot:=jsonb_build_object(
            'schema_version',3,'seller_organization_id',p_seller_organization_id,
            'product',jsonb_build_object('id',locked_product.id,'slug',locked_product.slug,
              'title',locked_product.title,'description',locked_product.description),
            'offer',jsonb_build_object('id',locked_offer.id,'title',locked_offer.title,
              'summary',locked_offer.summary,'billing_authority','external_unconfigured'),
            'courses',app_private.curriculum_courses_snapshot(
              p_seller_organization_id,p_catalog_product_id
            )
          );
          if octet_length(generated_snapshot::text)>1048576 then
            raise exception 'publication snapshot exceeds one mebibyte' using errcode='P4221'; end if;
          generated_source_sha256:=encode(
            digest(convert_to(generated_snapshot::text,'UTF8'),'sha256'),'hex'
          );
          if locked_product.published_snapshot_id is not null and exists(
            select 1 from publication current_publication
            where current_publication.seller_organization_id=p_seller_organization_id
              and current_publication.catalog_product_id=p_catalog_product_id
              and current_publication.id=locked_product.published_snapshot_id
              and current_publication.snapshot=generated_snapshot
          ) then raise exception 'no unpublished changes' using errcode='P4091'; end if;
          select coalesce(max(existing.version),0)+1 into next_publication_version
            from publication existing
            where existing.seller_organization_id=p_seller_organization_id
              and existing.catalog_product_id=p_catalog_product_id;
          insert into publication(seller_organization_id,id,catalog_product_id,version,
            schema_version,source_sha256,snapshot,actor_identity_user_id)
          values(p_seller_organization_id,next_publication_id,p_catalog_product_id,
            next_publication_version,3,generated_source_sha256,generated_snapshot,p_identity_user_id);
          update catalog_product set status='published',published_snapshot_id=next_publication_id,
            version=version+1,updated_at=clock_timestamp()
          where seller_organization_id=p_seller_organization_id and id=p_catalog_product_id
          returning version into next_product_version;
          response:=jsonb_build_object('publication_version',next_publication_version,
            'product_version',next_product_version,'source_sha256',generated_source_sha256);
          insert into app_private.catalog_mutation_request(seller_organization_id,operation,
            idempotency_key,identity_user_id,body_sha256,catalog_product_id,offer_id,
            publication_id,result)
          values(p_seller_organization_id,'product.publish',p_idempotency_key,
            p_identity_user_id,p_body_sha256,p_catalog_product_id,p_offer_id,
            next_publication_id,response);
          insert into audit_event(seller_organization_id,actor_identity_user_id,action,
            target_type,target_id,result,request_id,metadata)
          values(p_seller_organization_id,p_identity_user_id,'catalog.product_published',
            'catalog_product',p_catalog_product_id,'succeeded',p_request_id,
            jsonb_build_object('publication_id',next_publication_id,
              'publication_version',next_publication_version,
              'source_sha256',generated_source_sha256,'offer_id',p_offer_id,'schema_version',3));
          return query select next_publication_id,next_publication_version,
            next_product_version,generated_source_sha256,true;
        end $$
        """,
        """
        do $migration$
        declare definition text; old_fragment text; new_fragment text;
          old_course_fragment text; new_course_fragment text;
        begin
          definition:=pg_get_functiondef(
            'app_private.read_learning_player(uuid,uuid,uuid)'::regprocedure
          );
          old_fragment:=$old$
          select jsonb_build_object('id',asset.id,'title',asset.title,
            'media_type',asset.media_type) into asset_payload
          from learning_asset asset
          where asset.seller_organization_id=selected.seller_organization_id
            and asset.catalog_product_id=selected.catalog_product_id
            and asset.lesson_id=selected_lesson and asset.status='active'
          order by asset.position,asset.id limit 1;$old$;
          new_fragment:=$new$
          select jsonb_build_object('id',asset.asset_id,
            'title',asset.title,'media_type',asset.media_type) into asset_payload
          from publication_asset asset
          where asset.seller_organization_id=selected.seller_organization_id
            and asset.catalog_product_id=selected.catalog_product_id
            and asset.publication_id=selected.publication_id
            and asset.lesson_id=selected_lesson
          order by asset.position,asset.asset_id limit 1;$new$;
          if position(old_fragment in definition)=0 then
            raise exception 'cannot safely replace learning player asset lookup' using errcode='55000';
          end if;
          definition:=replace(definition,old_fragment,new_fragment);
          old_course_fragment:=$oldcourse$case when lesson_item->>'id'=selected_lesson::text then lesson_item
                    else jsonb_set(lesson_item,'{content}','""'::jsonb,true) end$oldcourse$;
          new_course_fragment:=$newcourse$case when lesson_item->>'id'=selected_lesson::text then lesson_item-'assets'
                    else jsonb_set(lesson_item-'assets','{content}','""'::jsonb,true) end$newcourse$;
          if position(old_course_fragment in definition)=0 then
            raise exception 'cannot safely strip private asset descriptors from player course'
              using errcode='55000';
          end if;
          execute replace(definition,old_course_fragment,new_course_fragment);
        end $migration$
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
            raise exception 'asset context is not authorized' using errcode='28000'; end if;
          return query select asset.asset_id,
            case when asset.storage_driver='legacy_bundle'
              then regexp_replace(asset.file_name,'[.]pdf$','') end,
            asset.file_name,asset.title,asset.media_type,asset.byte_size::integer,asset.sha256
          from publication_asset asset
          join seller_organization organization on organization.id=asset.seller_organization_id
          join buyer_entitlement entitlement
            on entitlement.seller_organization_id=asset.seller_organization_id
           and entitlement.catalog_product_id=asset.catalog_product_id
           and entitlement.publication_id=asset.publication_id
           and entitlement.buyer_identity_user_id=p_identity_user_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          where entitlement.id=p_entitlement_id and asset.asset_id=p_asset_id
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          if not found then raise exception 'learning asset is unavailable' using errcode='28000'; end if;
        end $$
        """,
        "revoke all on function app_private.authorize_learning_asset(uuid,uuid,uuid) from public",
        "grant execute on function app_private.authorize_learning_asset(uuid,uuid,uuid) to appsell_runtime",
        """
        create function app_private.authorize_publication_asset(
          p_identity_user_id uuid,p_entitlement_id uuid,p_asset_id uuid
        ) returns table(asset_id uuid,asset_version integer,file_name text,title text,
          media_type text,byte_size bigint,sha256 text,storage_driver text,
          storage_bucket text,object_key text,storage_version_id text,storage_etag text)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if not app_private.library_context_allowed(p_identity_user_id,'library.asset.read') then
            raise exception 'asset context is not authorized' using errcode='28000'; end if;
          return query select asset.asset_id,asset.asset_version,asset.file_name,asset.title,
            asset.media_type,asset.byte_size,asset.sha256,asset.storage_driver,
            asset.storage_bucket,asset.object_key,asset.storage_version_id,asset.storage_etag
          from publication_asset asset
          join seller_organization organization on organization.id=asset.seller_organization_id
          join buyer_entitlement entitlement
            on entitlement.seller_organization_id=asset.seller_organization_id
           and entitlement.catalog_product_id=asset.catalog_product_id
           and entitlement.publication_id=asset.publication_id
           and entitlement.buyer_identity_user_id=p_identity_user_id
          join catalog_product product
            on product.seller_organization_id=entitlement.seller_organization_id
           and product.id=entitlement.catalog_product_id
          where entitlement.id=p_entitlement_id and asset.asset_id=p_asset_id
            and entitlement.status='active' and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published';
          if not found then raise exception 'learning asset is unavailable' using errcode='28000'; end if;
        end $$
        """,
        "revoke all on function app_private.authorize_publication_asset(uuid,uuid,uuid) from public",
        "grant execute on function app_private.authorize_publication_asset(uuid,uuid,uuid) to appsell_runtime",
        """
        create or replace function app_private.consume_bff_request(
          request_id uuid,expires_at_epoch bigint
        ) returns void language plpgsql security definer
        set search_path=pg_catalog,app_private as $$
        declare now_epoch bigint:=extract(epoch from clock_timestamp())::bigint;
        begin
          if request_id is null or expires_at_epoch is null
             or expires_at_epoch<now_epoch or expires_at_epoch>now_epoch+120 then
            raise exception 'invalid or expired BFF request' using errcode='28000'; end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.bff.request/'||request_id::text,0
          ));
          with expired as (
            select replay.request_id from app_private.bff_request_replay replay
            where replay.expires_at<clock_timestamp()-interval '5 minutes'
            order by replay.expires_at for update skip locked limit 100
          )
          delete from app_private.bff_request_replay replay
          using expired where replay.request_id=expired.request_id;
          insert into app_private.bff_request_replay(request_id,expires_at)
            values(request_id,to_timestamp(expires_at_epoch));
        end $$
        """,
        "revoke all on function app_private.consume_bff_request(uuid,bigint) from public",
        "grant execute on function app_private.consume_bff_request(uuid,bigint) to appsell_runtime",
        """
        create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select to_regclass('public.identity_user') is not null
            and to_regclass('public.asset_record') is not null
            and to_regclass('public.asset_blob') is not null
            and to_regclass('public.asset_version') is not null
            and to_regclass('public.lesson_asset_draft') is not null
            and to_regclass('public.publication_asset') is not null
            and to_regclass('app_private.asset_upload') is not null
            and to_regclass('app_private.asset_verification_job') is not null
            and to_regclass('app_private.asset_storage_cleanup_job') is not null
            and to_regclass('app_private.asset_mutation_request') is not null
            and to_regclass('app_private.asset_worker_identity') is not null
            and exists(select 1 from public.alembic_version
              where version_num='0008_private_asset_storage')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
