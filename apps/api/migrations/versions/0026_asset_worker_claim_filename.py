"""Return the upload filename in durable asset verification claims.

Revision ID: 0026_asset_worker_claim_filename
Revises: 0025_seller_membership_role
"""

from __future__ import annotations

from alembic import op

revision = "0026_asset_worker_claim_filename"
down_revision = "0025_seller_membership_role"
branch_labels = None
depends_on = None

_CLAIM_SIGNATURE = "app_private.claim_asset_verification_jobs(uuid,text,integer,integer)"

_CLAIM_FUNCTION = """
create or replace function app_private.claim_asset_verification_jobs(
  p_actor uuid,p_worker text,p_limit integer,p_lease_seconds integer
) returns jsonb language plpgsql security definer
set search_path=pg_catalog,app_private,public as $function$
declare candidate record; claimed jsonb:='[]'::jsonb; lease_until timestamptz;
  locked_attempts integer;
begin
  if not app_private.asset_worker_context_allowed(p_actor) then
    raise exception 'asset worker context is not authorized' using errcode='28000';
  end if;
  if p_worker is null or p_worker<>btrim(p_worker)
     or octet_length(p_worker) not between 3 and 120 or p_worker~'[[:cntrl:]]'
     or p_limit not between 1 and 20 or p_lease_seconds not between 30 and 600 then
    raise exception 'invalid asset verification claim' using errcode='22023';
  end if;
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
        )
      )
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
end $function$
"""


def upgrade() -> None:
    statements = [
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$'0025_seller_membership_role')$needle$,
            $replacement$'0025_seller_membership_role','0026_asset_worker_claim_filename')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0016 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$'0025_seller_membership_role')$needle$,
            $replacement$'0025_seller_membership_role','0026_asset_worker_claim_filename')$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        _CLAIM_FUNCTION,
        f"revoke all on function {_CLAIM_SIGNATURE} from public",
        f"grant execute on function {_CLAIM_SIGNATURE} to appsell_runtime",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$'0025_seller_membership_role','0026_asset_worker_claim_filename')$needle$,
            $replacement$'0025_seller_membership_role')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0016 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition,
            $needle$'0025_seller_membership_role','0026_asset_worker_claim_filename')$needle$,
            $replacement$'0025_seller_membership_role')$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0017 schema readiness revision'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.claim_asset_verification_jobs(uuid,text,integer,integer)'::regprocedure
          );
          changed := replace(
            definition,
            $needle$upload.expected_media_type,
              upload.file_name$needle$,
            $replacement$upload.expected_media_type$replacement$
          );
          changed := replace(
            changed,
            $needle$'expected_media_type',candidate.expected_media_type,
              'file_name',candidate.file_name$needle$,
            $replacement$'expected_media_type',candidate.expected_media_type$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore asset worker claim payload'
              using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)
