from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    EndpointConnectionError,
)

import appsell_api.storage_s3 as storage_s3_module
from appsell_api.config import Settings
from appsell_api.storage_s3 import (
    S3Storage,
    StorageCapabilityError,
    StorageError,
    StoredObject,
    object_sha256_matches,
)


@pytest.mark.parametrize(
    "sdk_error",
    [
        ClientError(
            {
                "Error": {"Code": "AccessDenied", "Message": "denied"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            "GetObject",
        ),
        EndpointConnectionError(endpoint_url="https://storage.example.test"),
    ],
)
def test_open_download_converts_sdk_failures_to_storage_error(
    sdk_error: Exception,
) -> None:
    def fail_get_object(**_kwargs: Any) -> None:
        raise sdk_error

    storage = object.__new__(S3Storage)
    storage._control = SimpleNamespace(get_object=fail_get_object)

    with pytest.raises(StorageError, match="download failed"):
        with storage.open_download(bucket="ready", key="asset", version_id="v1"):
            pass


def test_anonymous_security_probes_use_internal_container_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints: list[str] = []

    def fake_client(*_args: Any, **kwargs: Any) -> SimpleNamespace:
        endpoints.append(kwargs["endpoint_url"])
        return SimpleNamespace()

    monkeypatch.setattr(storage_s3_module.boto3, "client", fake_client)  # type: ignore[attr-defined]
    settings = Settings(
        storage_provider="s3",
        storage_internal_endpoint="http://seaweedfs:8333",
        storage_public_endpoint="http://127.0.0.1:48333",
        storage_quarantine_bucket="quarantine",
        storage_ready_bucket="ready",
        storage_control_access_key="control",
        storage_control_secret_key="test-only-secret",  # noqa: S106
        storage_allowed_origin="http://localhost:3000",
        pdf_sandbox_socket="/run/appsell-pdf/pdf.sock",
    )

    S3Storage(settings)

    assert endpoints == [
        "http://seaweedfs:8333",
        "http://127.0.0.1:48333",
        "http://seaweedfs:8333",
        "http://127.0.0.1:48333",
    ]


def test_public_edge_audit_uses_public_anonymous_and_cleanup_clients() -> None:
    calls: list[tuple[str, str]] = []

    class Denied:
        def __getattr__(self, operation: str) -> Any:
            def deny(**_kwargs: Any) -> None:
                calls.append(("anonymous-public", operation))
                raise ClientError(
                    {
                        "Error": {"Code": "AccessDenied", "Message": "denied"},
                        "ResponseMetadata": {"HTTPStatusCode": 403},
                    },
                    operation,
                )

            return deny

    storage = object.__new__(S3Storage)
    storage.public_endpoint = "https://assets.example.test"
    storage.quarantine_bucket = "quarantine"
    storage.ready_bucket = "ready"
    storage._anonymous_public = Denied()
    storage._public = SimpleNamespace()

    storage.assert_public_edge_controls(require_tls=True)

    assert len(calls) == 10
    assert {client for client, _operation in calls} == {"anonymous-public"}


def test_object_checksum_is_compared_as_sha256_not_etag() -> None:
    stored = StoredObject(
        bucket="ready",
        key="asset",
        version_id="v1",
        size=3,
        content_type="application/pdf",
        checksum_sha256_b64="ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=",
        etag="etag",
        appsell_upload_id="upload-id",
    )
    assert object_sha256_matches(
        stored,
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    )


def test_presigned_download_and_delete_require_exact_version() -> None:
    storage = object.__new__(S3Storage)
    storage.get_ttl = 60
    storage._public = SimpleNamespace()
    storage._control = SimpleNamespace()
    with pytest.raises(StorageCapabilityError):
        storage.presign_get(bucket="ready", key="asset", version_id="", file_name="material.pdf")
    with pytest.raises(StorageCapabilityError):
        storage.delete_version(bucket="ready", key="asset", version_id="")


def test_prefix_reconcile_repeats_until_every_version_and_marker_is_deleted() -> None:
    prefix = "tenants/seller/assets/asset/v1/"
    listed = [
        {
            "Versions": [{"Key": f"{prefix}blob", "VersionId": "v1"}],
            "DeleteMarkers": [{"Key": f"{prefix}pages/1.png", "VersionId": "m1"}],
        },
        {},
    ]
    deleted: list[dict[str, Any]] = []

    def delete_objects(**kwargs: Any) -> dict[str, Any]:
        deleted.append(kwargs)
        return {}

    storage = object.__new__(S3Storage)
    storage._control = SimpleNamespace(
        list_object_versions=lambda **_kwargs: listed.pop(0),
        delete_objects=delete_objects,
    )

    assert storage.delete_all_versions_under_prefix(bucket="ready", prefix=prefix) == 2
    assert deleted[0]["Delete"]["Objects"] == [
        {"Key": f"{prefix}blob", "VersionId": "v1"},
        {"Key": f"{prefix}pages/1.png", "VersionId": "m1"},
    ]


def test_presigned_download_pins_safe_pdf_name_and_disables_caching() -> None:
    observed: dict[str, Any] = {}

    def generate_presigned_url(*_args: Any, **kwargs: Any) -> str:
        observed.update(kwargs)
        return "https://assets.example.test/object?X-Amz-Signature=signed"

    storage = object.__new__(S3Storage)
    storage.get_ttl = 60
    storage._public = SimpleNamespace(generate_presigned_url=generate_presigned_url)

    storage.presign_get(
        bucket="ready",
        key="opaque-key",
        version_id="v1",
        file_name="material seguro.pdf",
    )

    assert observed["Params"]["ResponseContentDisposition"] == (
        "inline; filename*=UTF-8''material%20seguro.pdf"
    )
    assert observed["Params"]["ResponseCacheControl"] == "private, no-store, max-age=0"

    storage.presign_get(
        bucket="ready",
        key="opaque-key",
        version_id="v1",
        file_name="material seguro.pdf",
        disposition="attachment",
    )
    assert observed["Params"]["ResponseContentDisposition"] == (
        "attachment; filename*=UTF-8''material%20seguro.pdf"
    )

    storage.presign_get(
        bucket="ready",
        key="page-key",
        version_id="page-v1",
        file_name="page-1.png",
        media_type="image/png",
    )
    assert observed["Params"]["ResponseContentDisposition"] == (
        "inline; filename*=UTF-8''page-1.png"
    )

    with pytest.raises(ValueError, match="invalid"):
        storage.presign_get(
            bucket="ready",
            key="page-key",
            version_id="page-v1",
            file_name="page-1.pdf",
            media_type="image/png",
        )


def test_head_fails_when_provider_omits_version_or_checksum() -> None:
    storage = object.__new__(S3Storage)
    storage._control = SimpleNamespace(
        head_object=lambda **_kwargs: {
            "ContentLength": 3,
            "ContentType": "application/pdf",
            "ETag": '"etag-is-not-a-checksum"',
        }
    )
    with pytest.raises(StorageCapabilityError, match="VersionId"):
        storage.head(bucket="ready", key="asset")


def test_copy_fails_if_provider_does_not_return_destination_version() -> None:
    storage = object.__new__(S3Storage)
    storage._control = SimpleNamespace(copy_object=lambda **_kwargs: {})
    source: Any = SimpleNamespace(
        bucket="quarantine",
        key="asset",
        version_id="source-version",
        content_type="application/pdf",
        appsell_upload_id="upload-id",
    )
    with pytest.raises(StorageCapabilityError, match="VersionId"):
        storage.copy_verified(source=source, destination_bucket="ready", destination_key="asset")


def test_readiness_rejects_anonymous_put_and_cleans_exact_probe_version() -> None:
    class Anonymous:
        def _denied(self, operation: str) -> None:
            raise ClientError(
                {
                    "Error": {"Code": "AccessDenied", "Message": "denied"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                operation,
            )

        def list_objects_v2(self, **_kwargs: Any) -> None:
            self._denied("ListObjectsV2")

        def get_object(self, **_kwargs: Any) -> None:
            self._denied("GetObject")

        def head_object(self, **_kwargs: Any) -> None:
            self._denied("HeadObject")

        def delete_object(self, **_kwargs: Any) -> None:
            self._denied("DeleteObject")

        def put_object(self, **_kwargs: Any) -> dict[str, str]:
            return {"VersionId": "public-write-v1"}

    deleted: list[dict[str, Any]] = []
    storage = object.__new__(S3Storage)
    storage._anonymous = Anonymous()
    storage._control = SimpleNamespace(delete_object=lambda **kwargs: deleted.append(kwargs))
    with pytest.raises(StorageCapabilityError, match="anonymous put is allowed"):
        storage._assert_anonymous_denied("quarantine")  # noqa: SLF001
    assert len(deleted) == 1
    assert deleted[0]["Bucket"] == "quarantine"
    assert deleted[0]["VersionId"] == "public-write-v1"
    assert deleted[0]["Key"].startswith("appsell-anonymous-probe/")
