from __future__ import annotations

import os

import psycopg


def configure_context_key() -> None:
    database_url = os.environ.get("APPSELL_MIGRATOR_DATABASE_URL", "").replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    key_hex = os.environ.get("APPSELL_CONTEXT_SIGNING_KEY_HEX", "")
    bootstrap_key_hex = os.environ.get("APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX", "")
    issuer = os.environ.get("APPSELL_BOOTSTRAP_EXPECTED_ISSUER", "")
    audience = os.environ.get("APPSELL_BOOTSTRAP_EXPECTED_AUDIENCE", "")
    client_id = os.environ.get("APPSELL_BOOTSTRAP_EXPECTED_CLIENT_ID", "")
    bootstrap_enabled_raw = os.environ.get("APPSELL_BOOTSTRAP_ENABLED", "false").lower()
    if bootstrap_enabled_raw not in {"true", "false"}:
        raise RuntimeError("APPSELL_BOOTSTRAP_ENABLED must be true or false")
    bootstrap_enabled = bootstrap_enabled_raw == "true"
    if not database_url:
        raise RuntimeError("APPSELL_MIGRATOR_DATABASE_URL is required")
    if len(key_hex) != 64:
        raise RuntimeError("APPSELL_CONTEXT_SIGNING_KEY_HEX must contain 32 bytes in hex")
    try:
        secret = bytes.fromhex(key_hex)
    except ValueError as error:
        raise RuntimeError("APPSELL_CONTEXT_SIGNING_KEY_HEX must be valid hex") from error
    if len(bootstrap_key_hex) != 64:
        raise RuntimeError("APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX must contain 32 bytes in hex")
    try:
        bootstrap_secret = bytes.fromhex(bootstrap_key_hex)
    except ValueError as error:
        raise RuntimeError("APPSELL_BOOTSTRAP_ATTESTATION_KEY_HEX must be valid hex") from error
    if bootstrap_secret == secret:
        raise RuntimeError("context and bootstrap signing keys must be different")
    if not all((issuer, audience, client_id)):
        raise RuntimeError("bootstrap issuer, audience and client ID are required")

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("set local role appsell_owner")
        existing = connection.execute(
            "select secret from app_private.context_signing_key where singleton for update"
        ).fetchone()
        if existing is None:
            connection.execute(
                "insert into app_private.context_signing_key (secret) values (%s)",
                (secret,),
            )
        elif bytes(existing[0]) != secret:
            raise RuntimeError("context signing key is already configured with a different value")
        trust_policy = connection.execute(
            """
            select issuer, audience, client_id, attestation_secret, enabled
            from app_private.bootstrap_trust_policy where singleton for update
            """
        ).fetchone()
        immutable_policy = (issuer, audience, client_id, bootstrap_secret)
        if trust_policy is None:
            connection.execute(
                """
                insert into app_private.bootstrap_trust_policy (
                  issuer, audience, client_id, attestation_secret, enabled
                ) values (%s, %s, %s, %s, %s)
                """,
                (*immutable_policy, bootstrap_enabled),
            )
        else:
            current_immutable = (*trust_policy[:3], bytes(trust_policy[3]))
            current_enabled = bool(trust_policy[4])
            if current_immutable != immutable_policy:
                raise RuntimeError("bootstrap trust policy is already configured differently")
            if bootstrap_enabled and not current_enabled:
                consumed = connection.execute(
                    """
                    select exists (select 1 from public.superadmin_bootstrap)
                      or exists (
                        select 1 from public.platform_grant
                        where role = 'platform_superadmin' and status = 'active'
                      )
                    """
                ).fetchone()
                if consumed != (False,):
                    raise RuntimeError("consumed superadmin bootstrap cannot be re-enabled")
                connection.execute(
                    "update app_private.bootstrap_trust_policy set enabled = true where singleton"
                )
            elif not bootstrap_enabled and current_enabled:
                connection.execute(
                    "update app_private.bootstrap_trust_policy set enabled = false where singleton"
                )


if __name__ == "__main__":
    configure_context_key()
