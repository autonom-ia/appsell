"""Add private MP4 media while preserving publication-pinned authorization.

Revision ID: 0013_private_media
Revises: 0012_checkout_webhook_ingress
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_private_media"
down_revision: str | None = "0012_checkout_webhook_ingress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    statements = [
        """
alter table public.learning_asset
  drop constraint learning_asset_file_name_check,
  drop constraint learning_asset_media_type_check,
  drop constraint learning_asset_byte_size_check
        """,
        """
alter table public.learning_asset add constraint learning_asset_media_contract_check check(
  (media_type='application/pdf' and file_name~'^[a-z0-9][a-z0-9-]+[.]pdf$'
    and byte_size between 1 and 10485760)
  or (media_type='video/mp4' and file_name~'^[a-z0-9][a-z0-9-]+[.]mp4$'
    and byte_size between 1 and 104857600)
)
        """,
        """
alter table public.asset_blob
  drop constraint asset_blob_media_type_check,
  drop constraint asset_blob_byte_size_check,
  drop constraint asset_blob_magic_type_check
        """,
        """
alter table public.asset_blob add constraint asset_blob_media_contract_check check(
  (media_type='application/pdf' and magic_type='application/pdf' and byte_size between 1 and 10485760)
  or (media_type='video/mp4' and magic_type='video/mp4' and byte_size between 1 and 104857600)
)
        """,
        """
alter table app_private.asset_upload
  drop constraint asset_upload_expected_media_type_check,
  drop constraint asset_upload_expected_byte_size_check
        """,
        """
alter table app_private.asset_upload add constraint asset_upload_media_contract_check check(
  (expected_media_type='application/pdf' and lower(file_name)~'[.]pdf$'
    and expected_byte_size between 1 and 10485760)
  or (expected_media_type='video/mp4' and lower(file_name)~'[.]mp4$'
    and expected_byte_size between 1 and 104857600)
)
        """,
        """
alter table app_private.asset_storage_cleanup_job
  drop constraint asset_storage_cleanup_job_byte_size_check,
  drop constraint asset_storage_cleanup_job_media_type_check
        """,
        """
alter table app_private.asset_storage_cleanup_job
  add constraint asset_storage_cleanup_job_byte_size_check
    check(byte_size is null or byte_size between 1 and 104857600),
  add constraint asset_storage_cleanup_job_media_type_check
    check(media_type is null or media_type in ('application/pdf','video/mp4'))
        """,
        """
create or replace function app_private.create_asset_upload_intent(
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
             or strpos(p_file_name,'/')>0 or strpos(p_file_name,chr(92))>0
             or p_file_name~'[[:cntrl:]]' or not (
               (p_media_type='application/pdf' and lower(p_file_name)~'[.]pdf$'
                 and p_size between 1 and 10485760)
               or (p_media_type='video/mp4' and lower(p_file_name)~'[.]mp4$'
                 and p_size between 1 and 104857600)
             )
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
        """
create or replace function app_private.confirm_asset_upload(
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
             or p_observed_size not between 1 and 104857600 or p_idempotency is null
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
        """
create or replace function app_private.register_asset_ready_candidate(
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
             or p_ready_size not between 1 and 104857600
             or p_ready_sha is null or p_ready_sha!~'^[0-9a-f]{64}$'
             or p_ready_media not in ('application/pdf','video/mp4')
             or p_magic<>p_ready_media
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
        """
create or replace function app_private.finalize_asset_verification(
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
             or p_source_size not between 1 and 104857600
             or p_ready_size not between 1 and 104857600 then
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
            and p_magic=candidate.expected_media_type and p_scanner_result='clean'
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
        """
create or replace function app_private.schema_ready() returns boolean
        language sql stable security definer set search_path=pg_catalog,app_private as $$
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
            and to_regclass('public.course_module') is not null
            and to_regclass('public.lesson') is not null
            and to_regclass('public.buyer_entitlement') is not null
            and to_regclass('public.learning_asset') is not null
            and to_regclass('public.publication_course') is not null
            and to_regclass('public.publication_lesson') is not null
            and to_regclass('public.learning_enrollment') is not null
            and to_regclass('public.lesson_progress') is not null
            and to_regclass('public.course_learning_state') is not null
            and to_regclass('public.asset_record') is not null
            and to_regclass('public.asset_blob') is not null
            and to_regclass('public.asset_version') is not null
            and to_regclass('public.lesson_asset_draft') is not null
            and to_regclass('public.publication_asset') is not null
            and to_regclass('public.checkout_integration') is not null
            and to_regclass('public.webhook_endpoint') is not null
            and to_regclass('public.external_product_mapping') is not null
            and to_regclass('public.webhook_event') is not null
            and to_regclass('app_private.outbox_event') is not null
            and to_regclass('app_private.bff_request_replay') is not null
            and to_regclass('app_private.seller_onboarding_request') is not null
            and to_regclass('app_private.product_draft_request') is not null
            and to_regclass('app_private.catalog_mutation_request') is not null
            and to_regclass('app_private.curriculum_mutation_request') is not null
            and to_regclass('app_private.demo_tenant_registry') is not null
            and to_regclass('app_private.learning_progress_request') is not null
            and to_regclass('app_private.asset_upload') is not null
            and to_regclass('app_private.asset_verification_job') is not null
            and to_regclass('app_private.asset_storage_cleanup_job') is not null
            and to_regclass('app_private.asset_mutation_request') is not null
            and to_regclass('app_private.asset_worker_identity') is not null
            and to_regclass('app_private.demo_inspection_ticket') is not null
            and to_regprocedure('app_private.read_buyer_products(uuid)') is not null
            and to_regprocedure('app_private.read_buyer_seller_storefront(uuid,uuid)') is not null
            and to_regprocedure('app_private.start_demo_inspection(uuid,text,text,integer,text,uuid)') is not null
            and to_regprocedure('app_private.read_demo_inspection(uuid,text,uuid,text,uuid)') is not null
            and to_regprocedure('app_private.revoke_demo_inspection(uuid,text,uuid,text,text,uuid)') is not null
            and to_regprocedure('app_private.read_public_storefront(text)') is not null
            and to_regprocedure('app_private.resolve_webhook_endpoint(text)') is not null
            and to_regprocedure('app_private.accept_checkout_webhook(uuid,uuid,text,text,bigint,text,smallint,text,integer,text,text,smallint,text,smallint,timestamptz,bigint,uuid)') is not null
            and exists(select 1 from pg_catalog.pg_trigger
              where tgrelid='public.webhook_event'::regclass
                and tgname='webhook_event_immutable' and not tgisinternal
                and tgenabled='O')
            and not exists(
              select 1 from unnest(array[
                'public.checkout_integration'::regclass,
                'public.webhook_endpoint'::regclass,
                'public.external_product_mapping'::regclass,
                'public.webhook_event'::regclass
              ]) relation(oid)
              join pg_catalog.pg_class class on class.oid=relation.oid
              where not class.relrowsecurity or not class.relforcerowsecurity
            )
            and exists(select 1 from public.alembic_version
              where version_num='0013_private_media')
            and exists(select 1 from app_private.context_signing_key where singleton)
            and exists(select 1 from app_private.bootstrap_trust_policy where singleton)
        $$
        """
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError("AppSell migrations are forward-only")
