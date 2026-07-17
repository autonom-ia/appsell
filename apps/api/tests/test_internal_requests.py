from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import replace
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from fastapi import HTTPException

from appsell_api.internal_requests import InternalRequestAuthenticator
from appsell_api.service_auth import ServiceEnvelope, ServiceIdentity


def _worker_envelope() -> ServiceEnvelope:
    now = int(time.time())
    identity_issuer = "https://identity.test.autonomia.site"
    return ServiceEnvelope(
        version=1,
        kid="api-internal",
        issuer="appsell-bff",
        audience="appsell-api",
        session_ref="asset-worker",
        session_epoch=1,
        identity=ServiceIdentity(
            issuer=identity_issuer,
            sub="asset-worker-v1",
            user_id=uuid5(NAMESPACE_URL, f"{identity_issuer}\0asset-worker-v1"),
        ),
        capability="catalog.asset.verify",
        seller_organization_id=None,
        method="POST",
        path="/v1/internal/asset-worker",
        query_sha256=hashlib.sha256(b"").hexdigest(),
        body_sha256=hashlib.sha256(b"").hexdigest(),
        request_id=uuid4(),
        iat=now,
        exp=now + 60,
    )


def _auth_without_database() -> InternalRequestAuthenticator:
    return InternalRequestAuthenticator(
        verifier=cast(Any, object()),
        service_credential=b"s" * 32,
        context_hmac_key=b"c" * 32,
        session_factory=cast(Any, object()),
        internal_issuer="appsell-bff",
        internal_audience="appsell-api",
        identity_issuer="https://identity.test.autonomia.site",
    )


@pytest.mark.asyncio
async def test_continuation_rejects_request_that_was_not_guarded_in_this_task() -> None:
    auth = _auth_without_database()

    with pytest.raises(HTTPException) as error:
        async with auth.continuation_domain_session(_worker_envelope()):
            pytest.fail("unguarded continuation entered a database session")

    assert error.value.status_code == 401
    assert error.value.detail == "invalid_internal_request"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forged_envelope",
    [
        replace(_worker_envelope(), session_ref="browser-session"),
        replace(_worker_envelope(), capability="catalog.asset.create"),
        replace(_worker_envelope(), seller_organization_id=uuid4()),
        replace(_worker_envelope(), path="/v1/me/sellers"),
        replace(_worker_envelope(), issuer="forged-service"),
        replace(_worker_envelope(), audience="forged-api"),
        replace(
            _worker_envelope(),
            identity=replace(_worker_envelope().identity, user_id=uuid4()),
        ),
        replace(_worker_envelope(), body_sha256=hashlib.sha256(b"forged").hexdigest()),
        replace(_worker_envelope(), exp=1),
    ],
)
async def test_trusted_session_rejects_forged_worker_envelope(
    forged_envelope: ServiceEnvelope,
) -> None:
    auth = _auth_without_database()

    with pytest.raises(HTTPException) as error:
        async with auth.trusted_domain_session(forged_envelope):
            pytest.fail("forged worker envelope entered a database session")

    assert error.value.status_code == 401
    assert error.value.detail == "invalid_internal_request"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"seller_organization_id": uuid4()},
        {"path": "/v1/me/sellers"},
        {"body_sha256": hashlib.sha256(b"forged").hexdigest()},
        {"capability": "catalog.asset.archive"},
    ],
)
async def test_continuation_binds_all_claims_and_allowed_capability_transition(
    mutation: dict[str, Any],
) -> None:
    auth = _auth_without_database()
    original = replace(
        _worker_envelope(),
        capability="catalog.asset.create",
        path="/v1/me/sellers/demo/assets/upload-intents",
    )
    auth._mark_guarded(original)
    changes = {"capability": "catalog.asset.upload.sign", **mutation}
    forged = replace(original, **changes)

    with pytest.raises(HTTPException) as error:
        async with auth.continuation_domain_session(forged):
            pytest.fail("forged continuation entered a database session")

    assert error.value.status_code == 401
    assert error.value.detail == "invalid_internal_request"


@pytest.mark.asyncio
async def test_continuation_is_not_inherited_by_a_child_task() -> None:
    auth = _auth_without_database()
    original = replace(_worker_envelope(), capability="catalog.asset.create")
    auth._mark_guarded(original)
    continuation = replace(original, capability="catalog.asset.upload.sign")

    async def attempt() -> int:
        with pytest.raises(HTTPException) as error:
            async with auth.continuation_domain_session(continuation):
                pytest.fail("child task inherited continuation authority")
        return error.value.status_code

    assert await asyncio.create_task(attempt()) == 401
