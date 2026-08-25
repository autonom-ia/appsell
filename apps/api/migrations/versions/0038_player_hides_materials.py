"""Hide the material links of lessons the student has not opened.

The player already blanks `content` for every lesson except the selected one.
`0036` added `materials` to the publication snapshot, and because the function
forwards the whole lesson object, those links reached the student for every
lesson in the course — including the ones never opened.

This is not an authorization hole. Whoever receives that answer is entitled to
the whole course and can open any lesson through ordinary navigation, so
nothing is escalated. It is an inconsistency: the reason for blanking `content`
— whatever it is, payload size or progressive delivery — applies just as well
to a link, which *is* the content of an external material.

`create_missing` is deliberately false. Publications made before `0036` carry no
`materials` key at all, and adding an empty one would rewrite the shape of rows
that are supposed to be immutable. A lesson that has the key gets an empty list;
a lesson that never had it stays exactly as it was.

Only `read_learning_player` is patched: `_v2` delegates to it and `_v3`
delegates to `_v2`, so the three contracts share this one expression.

The anchor comes from the live definition, not from the migration that first
created the function — a later migration had already rewritten it to drop
`assets`, and patching against the original text would have failed silently.
"""

from __future__ import annotations

from alembic import op

revision = "0038_player_hides_materials"
down_revision = "0037_product_cover"
branch_labels = None
depends_on = None

_ANCHOR = (
    "case when lesson_item->>'id'=selected_lesson::text then lesson_item-'assets'"
    "\n                    else jsonb_set(lesson_item-'assets','{content}','\"\"'::jsonb,true) end"
)

_REPLACEMENT = (
    "case when lesson_item->>'id'=selected_lesson::text then lesson_item-'assets'"
    "\n                    else jsonb_set(jsonb_set(lesson_item-'assets',"
    "'{content}','\"\"'::jsonb,true),'{materials}','[]'::jsonb,false) end"
)

_READY_TAIL = (
    "'0025_seller_membership_role','0026_asset_worker_claim_filename',"
    "'0027_product_course_draft_update','0028_offer_activation_guard',"
    "'0029_product_draft_asset_access','0030_product_pending_assets',"
    "'0031_public_free_access','0032_platform_superadmin_catalog',"
    "'0033_pdf_study_state','0034_publish_access_links',"
    "'0035_document_support_material','0036_lesson_material_links',"
    "'0037_product_cover')"
)
_READY_TAIL_WITH_0038 = _READY_TAIL[:-1] + ",'0038_player_hides_materials')"


def _patch(old: str, new: str, label: str) -> str:
    return f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef(
            'app_private.read_learning_player(uuid,uuid,uuid)'::regprocedure
          );
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label}' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """


def _ready(old: str, new: str, label: str) -> str:
    return f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label} 0016' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label} 0017' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """


def upgrade() -> None:
    op.execute(_patch(_ANCHOR, _REPLACEMENT, "cannot hide unopened lesson materials"))
    op.execute(_ready(_READY_TAIL, _READY_TAIL_WITH_0038, "cannot extend readiness for 0038 in"))


def downgrade() -> None:
    op.execute(_ready(_READY_TAIL_WITH_0038, _READY_TAIL, "cannot restore readiness from 0038 in"))
    op.execute(_patch(_REPLACEMENT, _ANCHOR, "cannot restore unopened lesson materials"))
