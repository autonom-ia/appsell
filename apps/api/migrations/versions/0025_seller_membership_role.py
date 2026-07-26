"""Let the payout routes learn the caller's own role in their own seller.

The payout routes must check MEMBERSHIP: the signed envelope proves the BFF
echoed a seller id, not that the authenticated human belongs to that seller. The
first attempt did that with a direct ``select`` on ``public.seller_membership``
from the runtime — and it always found nothing, because the RLS policy
``membership_self_select`` (0002) admits that read only under a fixed capability
allowlist::

    'audit.read', 'audit.write', 'seller.list', 'seller.read'

``commerce.payout.read`` is not on that list, so the runtime saw zero rows and
every request 403'd, against a seller whose data was correct all along.

Two ways out. Widening the allowlist would work, but it loosens a policy that was
deliberately narrowed, for every capability anyone adds later. The repo already
has the other mechanism: a privileged read goes through a ``security definer``
function, which runs as the owner and is therefore not subject to the runtime
policy — 0018/0019 check membership inside their own functions for exactly this
reason.

So this function answers one question, "what is the role of the CONTEXT's
identity in this seller?", and nothing else:

* it refuses unless the caller is already scoped to that seller, so it can never
  reveal membership of a seller the caller is not operating on;
* it reports only the caller's OWN role, never another member's;
* it needs no capability allowlist, because it discloses nothing the caller does
  not already have — they know who they are and which seller they asked for.

Which role is sufficient stays with the route: reading payout status accepts
owner or admin, binding an account requires owner. Keeping the policy decision in
the route and the privileged read here is what keeps both auditable.
"""

from __future__ import annotations

from alembic import op

revision = "0025_seller_membership_role"
down_revision = "0024_payout_destination"
branch_labels = None
depends_on = None

_SIGNATURE = "app_private.resolve_seller_membership_role(uuid)"

# Both accumulated lists end at 0024 (verified against 0024's own needles before
# writing this: an assumed tail would abort the migration).
_TAIL = "'0024_payout_destination')"
_TAIL_WITH_0025 = "'0024_payout_destination','0025_seller_membership_role')"

_READY_ANCHOR = "select app_private.schema_ready_base_0017()"
_READY_ADDITION = """select app_private.schema_ready_base_0017()
            and to_regprocedure(
              'app_private.resolve_seller_membership_role(uuid)'
            ) is not null"""


def upgrade() -> None:
    statements = [
        # The readiness gate pins the applied revision. Leaving 0025 out makes
        # schema_ready() false the moment this lands, and the API refuses to boot.
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition, $needle${_TAIL}$needle$, $replacement${_TAIL_WITH_0025}$replacement$
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
            definition, $needle${_TAIL}$needle$, $replacement${_TAIL_WITH_0025}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        """
        create function app_private.resolve_seller_membership_role(
          p_seller_organization_id uuid
        )
        returns text language plpgsql stable security definer
        set search_path=pg_catalog,app_private,public,pg_temp as $$
        declare membership_role text;
        begin
          -- Only ever answers about the seller the caller is already scoped to,
          -- and only about the caller's own identity. Anything else is a bug in
          -- the caller, so refuse loudly instead of returning null.
          if p_seller_organization_id is null
            or app_private.context_uuid('identity_user_id') is null
            or app_private.context_uuid('seller_organization_id')
               is distinct from p_seller_organization_id
          then
            raise exception 'seller membership lookup denied' using errcode = '42501';
          end if;
          select membership.role
            into membership_role
            from public.seller_membership membership
            join public.seller_organization organization
              on organization.id = membership.seller_organization_id
            where membership.seller_organization_id = p_seller_organization_id
              and membership.identity_user_id =
                  app_private.context_uuid('identity_user_id')
              and membership.status = 'active'
              and organization.status = 'active';
          -- Null means "not an active member of an active seller". The caller
          -- turns that into 403; it is not an error here.
          return membership_role;
        end $$
        """,
        f"revoke all on function {_SIGNATURE} from public",
        f"grant execute on function {_SIGNATURE} to appsell_runtime",
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed := replace(
            definition,
            $needle${_READY_ANCHOR}$needle$,
            $replacement${_READY_ADDITION}$replacement$
          );
          if changed = definition then
            raise exception 'cannot extend schema readiness for 0025'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    statements = [
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef('app_private.schema_ready()'::regprocedure);
          changed := replace(
            definition,
            $needle${_READY_ADDITION}$needle$,
            $replacement${_READY_ANCHOR}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore schema readiness for 0025'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
        f"drop function {_SIGNATURE}",
        f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0017()'::regprocedure
          );
          changed := replace(
            definition, $needle${_TAIL_WITH_0025}$needle$, $replacement${_TAIL}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0017 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;

          definition := pg_get_functiondef(
            'app_private.schema_ready_base_0016()'::regprocedure
          );
          changed := replace(
            definition, $needle${_TAIL_WITH_0025}$needle$, $replacement${_TAIL}$replacement$
          );
          if changed = definition then
            raise exception 'cannot restore 0016 schema readiness revision'
              using errcode = '55000';
          end if;
          execute changed;
        end $migration$
        """,
    ]
    for statement in statements:
        op.execute(statement)
