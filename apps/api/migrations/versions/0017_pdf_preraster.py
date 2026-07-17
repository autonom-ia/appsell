"""Add publication-pinned pre-rasterized PDF pages.

Revision ID: 0017_pdf_preraster
Revises: 0016_pdf_access_policy
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017_pdf_preraster"
down_revision: str | None = "0016_pdf_access_policy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
        create table asset_pdf_raster (
          seller_organization_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null check(asset_version>0),
          blob_id uuid not null,
          raster_profile text not null default 'png-1600-v1' check(
            raster_profile='png-1600-v1'
          ),
          page_count integer not null check(page_count between 1 and 100),
          total_byte_size bigint not null check(total_byte_size between 1 and 134217728),
          manifest_sha256 text not null check(manifest_sha256~'^[0-9a-f]{64}$'),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,asset_id,asset_version),
          constraint asset_pdf_raster_version_fkey foreign key(
            seller_organization_id,asset_id,asset_version,blob_id
          ) references asset_version(seller_organization_id,asset_id,version,blob_id),
          constraint asset_pdf_raster_blob_key unique(seller_organization_id,blob_id)
        )
        """,
        """
        create table asset_pdf_page (
          seller_organization_id uuid not null,
          asset_id uuid not null,
          asset_version integer not null check(asset_version>0),
          page_number integer not null check(page_number between 1 and 100),
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
          storage_version_id text check(
            storage_version_id is null
            or octet_length(storage_version_id) between 1 and 512
          ),
          storage_etag text check(
            storage_etag is null or octet_length(storage_etag) between 1 and 256
          ),
          byte_size bigint not null check(byte_size between 1 and 8388608),
          sha256 text not null check(sha256~'^[0-9a-f]{64}$'),
          pixel_width integer not null check(pixel_width between 1 and 1600),
          pixel_height integer not null check(pixel_height between 1 and 1600),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,asset_id,asset_version,page_number),
          constraint asset_pdf_page_raster_fkey foreign key(
            seller_organization_id,asset_id,asset_version
          ) references asset_pdf_raster(seller_organization_id,asset_id,asset_version),
          constraint asset_pdf_page_pixel_area_check check(
            pixel_width::bigint*pixel_height::bigint<=2560000
          ),
          constraint asset_pdf_page_tenant_object_key_check check(
            (storage_driver='s3' and
              object_key='tenants/'||seller_organization_id::text||'/assets/'||
                asset_id::text||'/v'||asset_version::text||'/pages/'||
                page_number::text||'.png')
            or (storage_driver='legacy_bundle'
              and seller_organization_id='90718777-93de-5e7a-aaa4-45b25051582c'::uuid
              and storage_bucket='codex-demo'
              and object_key~('^[a-z0-9][a-z0-9-]{0,80}/pages/'||
                page_number::text||'[.]png$'))
          ),
          constraint asset_pdf_page_storage_check check(
            (storage_driver='s3' and storage_version_id is not null
              and storage_etag is not null)
            or (storage_driver='legacy_bundle' and storage_version_id is null
              and storage_etag is null)
          )
        )
        """,
        """
        create unique index asset_pdf_page_storage_key on asset_pdf_page(
          storage_driver,storage_bucket,object_key,coalesce(storage_version_id,'')
        )
        """,
        """
        create index asset_pdf_page_lookup_idx on asset_pdf_page(
          seller_organization_id,asset_id,asset_version,page_number,sha256
        )
        """,
        """
        create table app_private.asset_pdf_raster_candidate (
          seller_organization_id uuid not null,
          upload_id uuid not null,
          page_count integer not null check(page_count between 1 and 100),
          total_byte_size bigint not null check(total_byte_size between 1 and 134217728),
          manifest_sha256 text not null check(manifest_sha256~'^[0-9a-f]{64}$'),
          created_at timestamptz not null default clock_timestamp(),
          primary key(seller_organization_id,upload_id),
          constraint asset_pdf_raster_candidate_upload_fkey foreign key(
            seller_organization_id,upload_id
          ) references app_private.asset_upload(seller_organization_id,id)
        )
        """,
        """
        create table app_private.asset_pdf_page_candidate (
          seller_organization_id uuid not null,
          upload_id uuid not null,
          page_number integer not null check(page_number between 1 and 100),
          storage_driver text not null check(storage_driver='s3'),
          storage_bucket text not null,
          object_key text not null,
          storage_version_id text not null check(
            octet_length(storage_version_id) between 1 and 512
          ),
          storage_etag text not null check(octet_length(storage_etag) between 1 and 256),
          byte_size bigint not null check(byte_size between 1 and 8388608),
          sha256 text not null check(sha256~'^[0-9a-f]{64}$'),
          pixel_width integer not null check(pixel_width between 1 and 1600),
          pixel_height integer not null check(pixel_height between 1 and 1600),
          primary key(seller_organization_id,upload_id,page_number),
          constraint asset_pdf_page_candidate_raster_fkey foreign key(
            seller_organization_id,upload_id
          ) references app_private.asset_pdf_raster_candidate(
            seller_organization_id,upload_id
          ),
          constraint asset_pdf_page_candidate_pixel_area_check check(
            pixel_width::bigint*pixel_height::bigint<=2560000
          )
        )
        """,
        """
        create index asset_pdf_page_candidate_upload_idx
          on app_private.asset_pdf_page_candidate(seller_organization_id,upload_id,page_number)
        """,
        """
        revoke all on app_private.asset_pdf_raster_candidate,
          app_private.asset_pdf_page_candidate from public,appsell_runtime
        """,
        "alter table asset_pdf_raster enable row level security",
        "alter table asset_pdf_raster force row level security",
        "alter table asset_pdf_page enable row level security",
        "alter table asset_pdf_page force row level security",
        "create policy asset_pdf_raster_owner_all on asset_pdf_raster to appsell_owner using(true) with check(true)",
        "create policy asset_pdf_page_owner_all on asset_pdf_page to appsell_owner using(true) with check(true)",
        "revoke all on asset_pdf_raster,asset_pdf_page from public,appsell_runtime",
        """
        create function app_private.enforce_asset_pdf_raster_source()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,public as $$
        begin
          if not exists(select 1 from asset_blob blob
            where blob.seller_organization_id=new.seller_organization_id
              and blob.id=new.blob_id and blob.media_type='application/pdf'
              and blob.magic_type='application/pdf') then
            raise exception 'PDF raster requires a verified PDF source' using errcode='23514';
          end if;
          return new;
        end $$
        """,
        "revoke all on function app_private.enforce_asset_pdf_raster_source() from public,appsell_runtime",
        """
        create trigger asset_pdf_raster_source_valid before insert on asset_pdf_raster
        for each row execute function app_private.enforce_asset_pdf_raster_source()
        """,
        """
        create function app_private.enforce_asset_pdf_page_limits()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,public as $$
        declare raster asset_pdf_raster%rowtype; existing_count integer; existing_bytes bigint;
        begin
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.pdf-raster/'||new.seller_organization_id::text||'/'||
              new.asset_id::text||'/'||new.asset_version::text,0
          ));
          select * into raster from asset_pdf_raster source
          where source.seller_organization_id=new.seller_organization_id
            and source.asset_id=new.asset_id and source.asset_version=new.asset_version
          for key share;
          if not found or new.page_number>raster.page_count then
            raise exception 'PDF raster page is outside its manifest' using errcode='23514';
          end if;
          select count(*),coalesce(sum(byte_size),0) into existing_count,existing_bytes
          from asset_pdf_page page where page.seller_organization_id=new.seller_organization_id
            and page.asset_id=new.asset_id and page.asset_version=new.asset_version;
          if existing_count+1>raster.page_count
             or existing_bytes+new.byte_size>raster.total_byte_size
             or existing_bytes+new.byte_size>134217728 then
            raise exception 'PDF raster page totals exceed its manifest' using errcode='23514';
          end if;
          return new;
        end $$
        """,
        "revoke all on function app_private.enforce_asset_pdf_page_limits() from public,appsell_runtime",
        """
        create trigger asset_pdf_page_limits_valid before insert on asset_pdf_page
        for each row execute function app_private.enforce_asset_pdf_page_limits()
        """,
        """
        create function app_private.enforce_s3_publication_pdf_raster()
        returns trigger language plpgsql security definer
        set search_path=pg_catalog,public as $$
        begin
          if new.media_type='application/pdf' and new.storage_driver='s3'
             and not exists(
               select 1 from asset_pdf_raster raster
               where raster.seller_organization_id=new.seller_organization_id
                 and raster.asset_id=new.asset_id and raster.asset_version=new.asset_version
                 and raster.blob_id=new.blob_id
                 and (select count(*)=raster.page_count
                       and min(page.page_number)=1
                       and max(page.page_number)=raster.page_count
                       and sum(page.page_number)=raster.page_count*(raster.page_count+1)/2
                       and sum(page.byte_size)=raster.total_byte_size
                     from asset_pdf_page page
                     where page.seller_organization_id=raster.seller_organization_id
                       and page.asset_id=raster.asset_id
                       and page.asset_version=raster.asset_version)
             ) then
            raise exception 'S3 PDF publication requires complete pre-rasterized pages'
              using errcode='23514';
          end if;
          return new;
        end $$
        """,
        "revoke all on function app_private.enforce_s3_publication_pdf_raster() from public,appsell_runtime",
        """
        create trigger publication_s3_pdf_raster_valid before insert or update of
          media_type,storage_driver,asset_id,asset_version,blob_id
        on publication_asset for each row
        execute function app_private.enforce_s3_publication_pdf_raster()
        """,
        """
        create trigger asset_pdf_raster_append_only before update or delete
        on asset_pdf_raster for each row
        execute function app_private.reject_asset_immutable_mutation()
        """,
        """
        create trigger asset_pdf_page_append_only before update or delete
        on asset_pdf_page for each row
        execute function app_private.reject_asset_immutable_mutation()
        """,
        """
        alter table app_private.asset_storage_cleanup_job
          drop constraint asset_storage_cleanup_job_kind_check,
          drop constraint asset_cleanup_locator_check,
          drop constraint asset_storage_cleanup_job_byte_size_check,
          drop constraint asset_storage_cleanup_job_media_type_check
        """,
        """
        alter table app_private.asset_storage_cleanup_job
          add constraint asset_storage_cleanup_job_kind_check check(kind in (
            'quarantine','quarantine_reconcile','ready_reconcile','ready_orphan',
            'pdf_page_orphan'
          )),
          add constraint asset_cleanup_locator_check check(
            (kind in ('quarantine_reconcile','ready_reconcile')
              and storage_version_id is null)
            or (kind in ('quarantine','ready_orphan','pdf_page_orphan')
              and storage_version_id is not null)
          ),
          add constraint asset_storage_cleanup_job_byte_size_check check(
            byte_size is null or byte_size between 1 and 104857600
          ),
          add constraint asset_storage_cleanup_job_media_type_check check(
            media_type is null or media_type in ('application/pdf','video/mp4','image/png')
          ),
          add constraint asset_storage_cleanup_pdf_page_check check(
            kind<>'pdf_page_orphan' or (
              media_type='image/png' and byte_size between 1 and 8388608
              and sha256~'^[0-9a-f]{64}$'
            )
          )
        """,
        """
        alter function app_private.register_asset_ready_candidate(
          uuid,uuid,uuid,text,text,text,bigint,text,text,text,text
        ) rename to register_asset_ready_candidate_v1_legacy
        """,
        """
        revoke all on function app_private.register_asset_ready_candidate_v1_legacy(
          uuid,uuid,uuid,text,text,text,bigint,text,text,text,text
        ) from public,appsell_runtime
        """,
        """
        create function app_private.register_asset_ready_candidate(
          p_actor uuid,p_seller uuid,p_upload uuid,p_worker text,p_ready_version_id text,
          p_ready_etag text,p_ready_size bigint,p_ready_sha text,p_ready_media text,
          p_magic text,p_scanner_result text
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare expected_media text;
        begin
          select expected_media_type into expected_media from app_private.asset_upload
          where seller_organization_id=p_seller and id=p_upload;
          if expected_media='application/pdf' then
            raise exception 'PDF ready candidate requires a complete raster manifest'
              using errcode='22023';
          end if;
          return app_private.register_asset_ready_candidate_v1_legacy(
            p_actor,p_seller,p_upload,p_worker,p_ready_version_id,p_ready_etag,
            p_ready_size,p_ready_sha,p_ready_media,p_magic,p_scanner_result
          );
        end $$
        """,
        """
        create function app_private.register_asset_ready_candidate_v2(
          p_actor uuid,p_seller uuid,p_upload uuid,p_worker text,p_ready_version_id text,
          p_ready_etag text,p_ready_size bigint,p_ready_sha text,p_ready_media text,
          p_magic text,p_scanner_result text,p_pdf_pages jsonb
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare upload app_private.asset_upload%rowtype; item jsonb; page_total bigint;
          page_count integer; expected_page integer:=1; manifest_sha text; result jsonb;
          canonical jsonb;
        begin
          select * into upload from app_private.asset_upload
          where seller_organization_id=p_seller and id=p_upload;
          if not found then
            raise exception 'asset upload is unavailable' using errcode='28000';
          end if;
          if upload.expected_media_type='video/mp4' then
            if p_pdf_pages is not null and p_pdf_pages<>'[]'::jsonb then
              raise exception 'video ready candidate cannot include PDF pages'
                using errcode='22023';
            end if;
            return app_private.register_asset_ready_candidate_v1_legacy(
              p_actor,p_seller,p_upload,p_worker,p_ready_version_id,p_ready_etag,
              p_ready_size,p_ready_sha,p_ready_media,p_magic,p_scanner_result
            );
          end if;
          if p_pdf_pages is null or jsonb_typeof(p_pdf_pages)<>'array'
             or jsonb_array_length(p_pdf_pages) not between 1 and 100 then
            raise exception 'invalid PDF raster manifest' using errcode='22023';
          end if;
          result:=app_private.register_asset_ready_candidate_v1_legacy(
            p_actor,p_seller,p_upload,p_worker,p_ready_version_id,p_ready_etag,
            p_ready_size,p_ready_sha,p_ready_media,p_magic,p_scanner_result
          );
          if coalesce((result->>'reconciling')::boolean,false) then
            raise exception 'asset verification lease is unavailable' using errcode='P4091';
          end if;
          delete from app_private.asset_pdf_page_candidate
          where seller_organization_id=p_seller and upload_id=p_upload;
          delete from app_private.asset_pdf_raster_candidate
          where seller_organization_id=p_seller and upload_id=p_upload;
          for item in select value from jsonb_array_elements(p_pdf_pages) value
            order by (value->>'page_number')::integer
          loop
            if jsonb_typeof(item)<>'object'
               or (item->>'page_number')::integer<>expected_page
               or item->>'storage_driver'<>'s3'
               or item->>'storage_bucket'<>upload.ready_bucket
               or item->>'object_key'<>(
                 'tenants/'||p_seller::text||'/assets/'||upload.asset_id::text||
                 '/v'||upload.reserved_version::text||'/pages/'||expected_page::text||'.png'
               )
               or octet_length(item->>'storage_version_id') not between 1 and 512
               or octet_length(item->>'storage_etag') not between 1 and 256
               or (item->>'byte_size')::bigint not between 1 and 8388608
               or item->>'sha256'!~'^[0-9a-f]{64}$'
               or (item->>'pixel_width')::integer not between 1 and 1600
               or (item->>'pixel_height')::integer not between 1 and 1600
               or (item->>'pixel_width')::bigint*(item->>'pixel_height')::bigint>2560000 then
              raise exception 'invalid PDF raster page' using errcode='22023';
            end if;
            expected_page:=expected_page+1;
          end loop;
          select jsonb_agg(jsonb_build_object(
            'page_number',(value->>'page_number')::integer,
            'storage_driver',value->>'storage_driver',
            'storage_bucket',value->>'storage_bucket',
            'object_key',value->>'object_key',
            'storage_version_id',value->>'storage_version_id',
            'storage_etag',value->>'storage_etag',
            'byte_size',(value->>'byte_size')::bigint,
            'sha256',value->>'sha256',
            'pixel_width',(value->>'pixel_width')::integer,
            'pixel_height',(value->>'pixel_height')::integer
          ) order by (value->>'page_number')::integer),
          count(*)::integer,sum((value->>'byte_size')::bigint)
          into canonical,page_count,page_total
          from jsonb_array_elements(p_pdf_pages) value;
          if page_count<>expected_page-1 or page_total not between 1 and 134217728 then
            raise exception 'invalid PDF raster totals' using errcode='22023';
          end if;
          manifest_sha:=encode(public.digest(convert_to(canonical::text,'UTF8'),'sha256'),'hex');
          insert into app_private.asset_pdf_raster_candidate(
            seller_organization_id,upload_id,page_count,total_byte_size,manifest_sha256
          ) values(p_seller,p_upload,page_count,page_total,manifest_sha);
          insert into app_private.asset_pdf_page_candidate(
            seller_organization_id,upload_id,page_number,storage_driver,storage_bucket,
            object_key,storage_version_id,storage_etag,byte_size,sha256,pixel_width,pixel_height
          ) select p_seller,p_upload,(value->>'page_number')::integer,
            value->>'storage_driver',value->>'storage_bucket',value->>'object_key',
            value->>'storage_version_id',value->>'storage_etag',
            (value->>'byte_size')::bigint,value->>'sha256',
            (value->>'pixel_width')::integer,(value->>'pixel_height')::integer
          from jsonb_array_elements(canonical) value;
          insert into app_private.asset_storage_cleanup_job(
            seller_organization_id,upload_id,kind,storage_driver,bucket,object_key,
            storage_version_id,storage_etag,byte_size,sha256,media_type,status,available_at
          ) select p_seller,p_upload,'pdf_page_orphan',page.storage_driver,
            page.storage_bucket,page.object_key,page.storage_version_id,page.storage_etag,
            page.byte_size,page.sha256,'image/png','queued',clock_timestamp()+interval '5 minutes'
          from app_private.asset_pdf_page_candidate page
          where page.seller_organization_id=p_seller and page.upload_id=p_upload
          on conflict do nothing;
          if (select count(*) from app_private.asset_storage_cleanup_job cleanup
              join app_private.asset_pdf_page_candidate page
                on page.seller_organization_id=cleanup.seller_organization_id
               and page.upload_id=cleanup.upload_id and page.object_key=cleanup.object_key
               and page.storage_version_id=cleanup.storage_version_id
              where cleanup.seller_organization_id=p_seller and cleanup.upload_id=p_upload
                and cleanup.kind='pdf_page_orphan')<>page_count then
            raise exception 'PDF raster cleanup registration conflicts' using errcode='23505';
          end if;
          return result||jsonb_build_object('pdf_page_count',page_count,
            'pdf_total_byte_size',page_total,'pdf_manifest_sha256',manifest_sha);
        end $$
        """,
        """
        revoke all on function app_private.register_asset_ready_candidate(
          uuid,uuid,uuid,text,text,text,bigint,text,text,text,text
        ) from public
        """,
        """
        grant execute on function app_private.register_asset_ready_candidate(
          uuid,uuid,uuid,text,text,text,bigint,text,text,text,text
        ) to appsell_runtime
        """,
        """
        revoke all on function app_private.register_asset_ready_candidate_v2(
          uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb
        ) from public
        """,
        """
        grant execute on function app_private.register_asset_ready_candidate_v2(
          uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb
        ) to appsell_runtime
        """,
        """
        alter function app_private.finalize_asset_verification(
          uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,
          uuid,text,uuid
        ) rename to finalize_asset_verification_v1_legacy
        """,
        """
        revoke all on function app_private.finalize_asset_verification_v1_legacy(
          uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,
          uuid,text,uuid
        ) from public,appsell_runtime
        """,
        """
        create function app_private.finalize_asset_verification(
          p_actor uuid,p_seller uuid,p_upload uuid,p_worker text,
          p_source_size bigint,p_source_sha text,p_ready_size bigint,p_ready_sha text,
          p_ready_media text,p_magic text,p_scanner_result text,p_ready_version_id text,
          p_ready_etag text,p_verdict text,p_idempotency uuid,p_body text,p_request uuid
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare expected_media text;
        begin
          select expected_media_type into expected_media from app_private.asset_upload
          where seller_organization_id=p_seller and id=p_upload;
          if expected_media='application/pdf' and p_verdict='ready' then
            raise exception 'PDF finalize requires a complete raster manifest'
              using errcode='22023';
          end if;
          return app_private.finalize_asset_verification_v1_legacy(
            p_actor,p_seller,p_upload,p_worker,p_source_size,p_source_sha,p_ready_size,
            p_ready_sha,p_ready_media,p_magic,p_scanner_result,p_ready_version_id,
            p_ready_etag,p_verdict,p_idempotency,p_body,p_request
          );
        end $$
        """,
        """
        create function app_private.finalize_asset_verification_v2(
          p_actor uuid,p_seller uuid,p_upload uuid,p_worker text,
          p_source_size bigint,p_source_sha text,p_ready_size bigint,p_ready_sha text,
          p_ready_media text,p_magic text,p_scanner_result text,p_ready_version_id text,
          p_ready_etag text,p_verdict text,p_idempotency uuid,p_body text,p_request uuid,
          p_pdf_manifest_sha256 text
        ) returns jsonb language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        declare upload app_private.asset_upload%rowtype;
          raster app_private.asset_pdf_raster_candidate%rowtype;
          result jsonb; stored_bytes bigint;
        begin
          select * into upload from app_private.asset_upload
          where seller_organization_id=p_seller and id=p_upload for update;
          if not found then raise exception 'asset upload is unavailable' using errcode='28000'; end if;
          if upload.expected_media_type='video/mp4' then
            if p_pdf_manifest_sha256 is not null then
              raise exception 'video finalize cannot include a PDF manifest'
                using errcode='22023';
            end if;
            return app_private.finalize_asset_verification_v1_legacy(
              p_actor,p_seller,p_upload,p_worker,p_source_size,p_source_sha,p_ready_size,
              p_ready_sha,p_ready_media,p_magic,p_scanner_result,p_ready_version_id,
              p_ready_etag,p_verdict,p_idempotency,p_body,p_request
            );
          end if;
          if p_verdict='rejected' then
            return app_private.finalize_asset_verification_v1_legacy(
              p_actor,p_seller,p_upload,p_worker,p_source_size,p_source_sha,p_ready_size,
              p_ready_sha,p_ready_media,p_magic,p_scanner_result,p_ready_version_id,
              p_ready_etag,p_verdict,p_idempotency,p_body,p_request
            );
          end if;
          select * into raster from app_private.asset_pdf_raster_candidate
          where seller_organization_id=p_seller and upload_id=p_upload for share;
          if not found or p_pdf_manifest_sha256 is null
             or p_pdf_manifest_sha256<>raster.manifest_sha256
             or (select count(*) from app_private.asset_pdf_page_candidate page
                 where page.seller_organization_id=p_seller and page.upload_id=p_upload)
                <>raster.page_count
             or (select coalesce(sum(page.byte_size),0)
                 from app_private.asset_pdf_page_candidate page
                 where page.seller_organization_id=p_seller and page.upload_id=p_upload)
                <>raster.total_byte_size then
            raise exception 'PDF raster manifest is incomplete or changed' using errcode='22023';
          end if;
          if exists(select 1 from asset_pdf_raster saved
            where saved.seller_organization_id=p_seller and saved.asset_id=upload.asset_id
              and saved.asset_version=upload.reserved_version and saved.blob_id=upload.blob_id
              and saved.page_count=raster.page_count
              and saved.total_byte_size=raster.total_byte_size
              and saved.manifest_sha256=raster.manifest_sha256) then
            result:=app_private.finalize_asset_verification_v1_legacy(
              p_actor,p_seller,p_upload,p_worker,p_source_size,p_source_sha,p_ready_size,
              p_ready_sha,p_ready_media,p_magic,p_scanner_result,p_ready_version_id,
              p_ready_etag,p_verdict,p_idempotency,p_body,p_request
            );
            return result||jsonb_build_object(
              'pdf_manifest_sha256',raster.manifest_sha256,
              'pdf_page_count',raster.page_count,
              'pdf_total_byte_size',raster.total_byte_size
            );
          end if;
          perform pg_advisory_xact_lock(hashtextextended(
            'appsell.asset/quota/'||p_seller::text,0
          ));
          select coalesce((select sum(byte_size) from asset_blob
            where seller_organization_id=p_seller and storage_driver='s3'),0)
            +coalesce((select sum(byte_size) from asset_pdf_page
            where seller_organization_id=p_seller and storage_driver='s3'),0)
          into stored_bytes;
          if stored_bytes+p_ready_size+raster.total_byte_size>1073741824 then
            raise exception 'seller ready asset byte quota exceeded' using errcode='P4290';
          end if;
          result:=app_private.finalize_asset_verification_v1_legacy(
            p_actor,p_seller,p_upload,p_worker,p_source_size,p_source_sha,p_ready_size,
            p_ready_sha,p_ready_media,p_magic,p_scanner_result,p_ready_version_id,
            p_ready_etag,p_verdict,p_idempotency,p_body,p_request
          );
          if coalesce((result->>'ready')::boolean,false) then
            insert into asset_pdf_raster(
              seller_organization_id,asset_id,asset_version,blob_id,page_count,
              total_byte_size,manifest_sha256
            ) values(p_seller,upload.asset_id,upload.reserved_version,upload.blob_id,
              raster.page_count,raster.total_byte_size,raster.manifest_sha256)
            on conflict do nothing;
            if not exists(select 1 from asset_pdf_raster saved
              where saved.seller_organization_id=p_seller and saved.asset_id=upload.asset_id
                and saved.asset_version=upload.reserved_version and saved.blob_id=upload.blob_id
                and saved.page_count=raster.page_count
                and saved.total_byte_size=raster.total_byte_size
                and saved.manifest_sha256=raster.manifest_sha256) then
              raise exception 'PDF raster history conflicts' using errcode='23505';
            end if;
            insert into asset_pdf_page(
              seller_organization_id,asset_id,asset_version,page_number,storage_driver,
              storage_bucket,object_key,storage_version_id,storage_etag,byte_size,sha256,
              pixel_width,pixel_height
            ) select p_seller,upload.asset_id,upload.reserved_version,page.page_number,
              page.storage_driver,page.storage_bucket,page.object_key,page.storage_version_id,
              page.storage_etag,page.byte_size,page.sha256,page.pixel_width,page.pixel_height
            from app_private.asset_pdf_page_candidate page
            where page.seller_organization_id=p_seller and page.upload_id=p_upload
            on conflict do nothing;
            if (select count(*) from asset_pdf_page saved
                where saved.seller_organization_id=p_seller and saved.asset_id=upload.asset_id
                  and saved.asset_version=upload.reserved_version)<>raster.page_count then
              raise exception 'PDF raster pages conflict' using errcode='23505';
            end if;
            update app_private.asset_storage_cleanup_job cleanup set status='protected',
              lease_expires_at=null,updated_at=clock_timestamp()
            from app_private.asset_pdf_page_candidate page
            where page.seller_organization_id=p_seller and page.upload_id=p_upload
              and cleanup.seller_organization_id=page.seller_organization_id
              and cleanup.upload_id=page.upload_id and cleanup.kind='pdf_page_orphan'
              and cleanup.object_key=page.object_key
              and cleanup.storage_version_id=page.storage_version_id;
            if (select count(*) from app_private.asset_storage_cleanup_job cleanup
                where cleanup.seller_organization_id=p_seller and cleanup.upload_id=p_upload
                  and cleanup.kind='pdf_page_orphan' and cleanup.status='protected')
               <>raster.page_count then
              raise exception 'PDF raster cleanup protection is incomplete' using errcode='55000';
            end if;
          end if;
          return result||jsonb_build_object('pdf_manifest_sha256',raster.manifest_sha256,
            'pdf_page_count',raster.page_count,'pdf_total_byte_size',raster.total_byte_size);
        end $$
        """,
        """
        revoke all on function app_private.finalize_asset_verification(
          uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,
          uuid,text,uuid
        ) from public
        """,
        """
        grant execute on function app_private.finalize_asset_verification(
          uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,
          uuid,text,uuid
        ) to appsell_runtime
        """,
        """
        revoke all on function app_private.finalize_asset_verification_v2(
          uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,
          uuid,text,uuid,text
        ) from public
        """,
        """
        grant execute on function app_private.finalize_asset_verification_v2(
          uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,
          uuid,text,uuid,text
        ) to appsell_runtime
        """,
        """
        create function app_private.authorize_entitled_pdf_manifest(
          p_identity_user_id uuid,p_entitlement_id uuid,p_asset_id uuid
        ) returns table(seller_organization_id uuid,asset_id uuid,asset_version integer,
          blob_id uuid,raster_profile text,page_count integer,total_byte_size bigint,
          manifest_sha256 text,pages jsonb)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if not app_private.library_context_allowed(p_identity_user_id,'library.asset.read') then
            raise exception 'PDF manifest context is not authorized' using errcode='28000';
          end if;
          return query select raster.seller_organization_id,raster.asset_id,
            raster.asset_version,raster.blob_id,raster.raster_profile,raster.page_count,
            raster.total_byte_size,raster.manifest_sha256,
            jsonb_agg(jsonb_build_object('page_number',page.page_number,
              'byte_size',page.byte_size,'sha256',page.sha256,
              'pixel_width',page.pixel_width,'pixel_height',page.pixel_height)
              order by page.page_number)
          from publication_asset published
          join buyer_entitlement entitlement
            on entitlement.seller_organization_id=published.seller_organization_id
           and entitlement.catalog_product_id=published.catalog_product_id
           and entitlement.publication_id=published.publication_id
           and entitlement.buyer_identity_user_id=p_identity_user_id
          join seller_organization organization on organization.id=published.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=published.seller_organization_id
           and product.id=published.catalog_product_id
          join asset_pdf_raster raster
            on raster.seller_organization_id=published.seller_organization_id
           and raster.asset_id=published.asset_id
           and raster.asset_version=published.asset_version
           and raster.blob_id=published.blob_id
          join asset_pdf_page page
            on page.seller_organization_id=raster.seller_organization_id
           and page.asset_id=raster.asset_id and page.asset_version=raster.asset_version
          where entitlement.id=p_entitlement_id and published.asset_id=p_asset_id
            and published.media_type='application/pdf' and entitlement.status='active'
            and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published'
          group by raster.seller_organization_id,raster.asset_id,raster.asset_version,
            raster.blob_id,raster.raster_profile,raster.page_count,raster.total_byte_size,
            raster.manifest_sha256
          having count(*)=raster.page_count and min(page.page_number)=1
            and max(page.page_number)=raster.page_count
            and sum(page.page_number)=raster.page_count*(raster.page_count+1)/2
            and sum(page.byte_size)=raster.total_byte_size;
          if not found then
            raise exception 'entitled PDF manifest is unavailable' using errcode='28000';
          end if;
        end $$
        """,
        """
        create function app_private.authorize_entitled_pdf_page(
          p_identity_user_id uuid,p_entitlement_id uuid,p_asset_id uuid,p_page_number integer
        ) returns table(seller_organization_id uuid,asset_id uuid,asset_version integer,
          blob_id uuid,raster_profile text,page_number integer,storage_driver text,
          storage_bucket text,object_key text,storage_version_id text,storage_etag text,
          byte_size bigint,sha256 text,pixel_width integer,pixel_height integer)
        language plpgsql security definer
        set search_path=pg_catalog,app_private,public as $$
        begin
          if p_page_number not between 1 and 100
             or not app_private.library_context_allowed(
               p_identity_user_id,'library.asset.read'
             ) then
            raise exception 'PDF page context is not authorized' using errcode='28000';
          end if;
          return query select raster.seller_organization_id,raster.asset_id,
            raster.asset_version,raster.blob_id,raster.raster_profile,page.page_number,
            page.storage_driver,page.storage_bucket,page.object_key,page.storage_version_id,
            page.storage_etag,page.byte_size,page.sha256,page.pixel_width,page.pixel_height
          from publication_asset published
          join buyer_entitlement entitlement
            on entitlement.seller_organization_id=published.seller_organization_id
           and entitlement.catalog_product_id=published.catalog_product_id
           and entitlement.publication_id=published.publication_id
           and entitlement.buyer_identity_user_id=p_identity_user_id
          join seller_organization organization on organization.id=published.seller_organization_id
          join catalog_product product
            on product.seller_organization_id=published.seller_organization_id
           and product.id=published.catalog_product_id
          join asset_pdf_raster raster
            on raster.seller_organization_id=published.seller_organization_id
           and raster.asset_id=published.asset_id
           and raster.asset_version=published.asset_version
           and raster.blob_id=published.blob_id
          join asset_pdf_page page
            on page.seller_organization_id=raster.seller_organization_id
           and page.asset_id=raster.asset_id and page.asset_version=raster.asset_version
           and page.page_number=p_page_number
          where entitlement.id=p_entitlement_id and published.asset_id=p_asset_id
            and published.media_type='application/pdf' and entitlement.status='active'
            and entitlement.starts_at<=clock_timestamp()
            and (entitlement.expires_at is null or entitlement.expires_at>clock_timestamp())
            and organization.status='active' and product.status='published'
            and (select count(*)=raster.page_count
                and min(complete.page_number)=1
                and max(complete.page_number)=raster.page_count
                and sum(complete.page_number)=raster.page_count*(raster.page_count+1)/2
                and sum(complete.byte_size)=raster.total_byte_size
              from asset_pdf_page complete
              where complete.seller_organization_id=raster.seller_organization_id
                and complete.asset_id=raster.asset_id
                and complete.asset_version=raster.asset_version);
          if not found then
            raise exception 'entitled PDF page is unavailable' using errcode='28000';
          end if;
        end $$
        """,
        "revoke all on function app_private.authorize_entitled_pdf_manifest(uuid,uuid,uuid) from public",
        "grant execute on function app_private.authorize_entitled_pdf_manifest(uuid,uuid,uuid) to appsell_runtime",
        "revoke all on function app_private.authorize_entitled_pdf_page(uuid,uuid,uuid,integer) from public",
        "grant execute on function app_private.authorize_entitled_pdf_page(uuid,uuid,uuid,integer) to appsell_runtime",
        r"""
        do $migration$
        declare definition text; changed text;
        begin
          alter function app_private.schema_ready() rename to schema_ready_base_0016;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$needle$version_num='0016_pdf_access_policy'$needle$,
            $replacement$version_num in ('0016_pdf_access_policy','0017_pdf_preraster')$replacement$);
          if changed=definition then
            raise exception 'cannot safely extend schema readiness revision' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        "revoke all on function app_private.schema_ready_base_0016() from public,appsell_runtime",
        """
        create function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
          select app_private.schema_ready_base_0016()
            and to_regclass('public.asset_pdf_raster') is not null
            and to_regclass('public.asset_pdf_page') is not null
            and to_regclass('app_private.asset_pdf_raster_candidate') is not null
            and to_regclass('app_private.asset_pdf_page_candidate') is not null
            and to_regprocedure(
              'app_private.register_asset_ready_candidate_v2(uuid,uuid,uuid,text,text,text,bigint,text,text,text,text,jsonb)'
            ) is not null
            and to_regprocedure(
              'app_private.finalize_asset_verification_v2(uuid,uuid,uuid,text,bigint,text,bigint,text,text,text,text,text,text,text,uuid,text,uuid,text)'
            ) is not null
            and to_regprocedure(
              'app_private.authorize_entitled_pdf_manifest(uuid,uuid,uuid)'
            ) is not null
            and to_regprocedure(
              'app_private.authorize_entitled_pdf_page(uuid,uuid,uuid,integer)'
            ) is not null
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.asset_pdf_raster'::regclass
                and tgname='asset_pdf_raster_append_only' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.asset_pdf_raster'::regclass
                and tgname='asset_pdf_raster_source_valid' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.asset_pdf_page'::regclass
                and tgname='asset_pdf_page_append_only' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.asset_pdf_page'::regclass
                and tgname='asset_pdf_page_limits_valid' and not tgisinternal
                and tgenabled='O')
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.publication_asset'::regclass
                and tgname='publication_s3_pdf_raster_valid' and not tgisinternal
                and tgenabled='O')
            and not exists(select 1 from unnest(array[
              'public.asset_pdf_raster'::regclass,'public.asset_pdf_page'::regclass
            ]) relation(oid)
              join pg_catalog.pg_class class on class.oid=relation.oid
              where not class.relrowsecurity or not class.relforcerowsecurity)
            and exists(select 1 from public.alembic_version
              where version_num='0017_pdf_preraster')
        $$
        """,
        "revoke all on function app_private.schema_ready() from public",
        "grant execute on function app_private.schema_ready() to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("0017_pdf_preraster is forward-only; restore the prior release digest")
