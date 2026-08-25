from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal
from urllib.parse import quote
from uuid import uuid4

import boto3  # type: ignore[import-untyped]
import httpx
from botocore import UNSIGNED  # type: ignore[import-untyped]
from botocore.client import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from .asset_media import (
    PAGE_IMAGE_MEDIA_TYPE,
    SUPPORTED_MEDIA,
    extension_matches,
    is_document,
)
from .asset_media import max_bytes as asset_max_bytes
from .config import Settings
from .image_verifier import COVER_EXTENSION, COVER_MAX_BYTES, is_cover_media


class StorageError(RuntimeError):
    pass


class StorageCapabilityError(StorageError):
    pass


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    version_id: str
    size: int
    content_type: str
    checksum_sha256_b64: str
    etag: str
    appsell_upload_id: str


@dataclass(frozen=True)
class PresignedRequest:
    url: str
    headers: dict[str, str]
    expires_in_seconds: int
    required_content_length: int | None = None


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        if not settings.storage_configured:
            raise StorageError("S3 storage is not configured")
        common: dict[str, Any] = {
            "service_name": "s3",
            "region_name": settings.storage_region,
            "aws_access_key_id": settings.storage_control_access_key,
            "aws_secret_access_key": settings.storage_control_secret_key,
            "config": Config(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.storage_path_style else "virtual"},
                connect_timeout=settings.storage_connect_timeout_seconds,
                read_timeout=settings.storage_read_timeout_seconds,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        }
        self._control: Any = boto3.client(endpoint_url=settings.storage_internal_endpoint, **common)
        self._public: Any = boto3.client(endpoint_url=settings.storage_public_endpoint, **common)
        anonymous_config = Config(
            signature_version=UNSIGNED,
            s3={"addressing_style": "path" if settings.storage_path_style else "virtual"},
            connect_timeout=settings.storage_connect_timeout_seconds,
            read_timeout=settings.storage_read_timeout_seconds,
            retries={"max_attempts": 1, "mode": "standard"},
        )
        self._anonymous: Any = boto3.client(
            "s3",
            # Exercise the same buckets over the API-reachable path. The public
            # endpoint exists to mint browser-facing URLs and can legitimately be
            # a host/LB address without container hairpin routing.
            endpoint_url=settings.storage_internal_endpoint,
            region_name=settings.storage_region,
            config=anonymous_config,
        )
        self._anonymous_public: Any = boto3.client(
            "s3",
            # This client is intentionally not used by the API-container startup
            # path: a host/LB endpoint may not support hairpin routing. The
            # storage_public_audit entrypoint executes it from outside the app
            # network and therefore proves the browser-facing edge itself.
            endpoint_url=settings.storage_public_endpoint,
            region_name=settings.storage_region,
            config=anonymous_config,
        )
        self.public_endpoint = settings.storage_public_endpoint.rstrip("/")
        self.quarantine_bucket = settings.storage_quarantine_bucket
        self.ready_bucket = settings.storage_ready_bucket
        self.put_ttl = settings.storage_presign_put_ttl_seconds
        self.get_ttl = settings.storage_presign_get_ttl_seconds

    def presign_put(
        self,
        *,
        bucket: str,
        key: str,
        content_type: str,
        sha256_hex: str,
        byte_size: int,
        upload_request_id: str,
    ) -> PresignedRequest:
        maximum = asset_max_bytes(content_type)
        if byte_size < 1 or byte_size > maximum:
            raise ValueError("upload byte_size is outside the allowed range")
        if not upload_request_id or len(upload_request_id) > 128:
            raise ValueError("upload_request_id is invalid")
        checksum = _checksum_b64(sha256_hex)
        params = {
            "Bucket": bucket,
            "Key": key,
            "ContentType": content_type,
            "ChecksumSHA256": checksum,
            "ContentLength": byte_size,
            "Metadata": {"appsell-upload-id": upload_request_id},
        }
        return PresignedRequest(
            url=str(
                self._public.generate_presigned_url(
                    "put_object", Params=params, ExpiresIn=self.put_ttl, HttpMethod="PUT"
                )
            ),
            headers={
                "content-type": content_type,
                "x-amz-checksum-sha256": checksum,
                "x-amz-meta-appsell-upload-id": upload_request_id,
            },
            expires_in_seconds=self.put_ttl,
            required_content_length=byte_size,
        )

    def presign_cover_put(
        self,
        *,
        key: str,
        content_type: str,
        sha256_hex: str,
        byte_size: int,
        upload_request_id: str,
    ) -> PresignedRequest:
        """Assina o envio de uma capa direto do navegador para a quarentena.

        Separado de ``presign_put`` de proposito. Aquele metodo tira o teto de
        ``SUPPORTED_MEDIA``, e ensinar aquele catalogo a conhecer JPEG e WebP
        faria uma capa poder ser assinada como material de aula — outro caminho,
        com outra politica de download. Sao duas politicas; sao dois metodos.

        Tudo o que a capa declara fica preso na assinatura: tipo, tamanho exato,
        digest e a identidade do upload. O S3 recusa o PUT que discordar de
        qualquer um deles, entao o objeto que chega a quarentena e exatamente o
        que foi pedido — o que nao diz nada sobre ele ser mesmo uma imagem, e
        por isso o verificador ainda abre os bytes depois.
        """
        if not is_cover_media(content_type):
            raise StorageCapabilityError("cover media type is not storable")
        if byte_size < 1 or byte_size > COVER_MAX_BYTES:
            raise ValueError("cover byte_size is outside the allowed range")
        if not upload_request_id or len(upload_request_id) > 128:
            raise ValueError("upload_request_id is invalid")
        checksum = _checksum_b64(sha256_hex)
        params = {
            "Bucket": self.quarantine_bucket,
            "Key": key,
            "ContentType": content_type,
            "ChecksumSHA256": checksum,
            "ContentLength": byte_size,
            "Metadata": {"appsell-upload-id": upload_request_id},
        }
        return PresignedRequest(
            url=str(
                self._public.generate_presigned_url(
                    "put_object", Params=params, ExpiresIn=self.put_ttl, HttpMethod="PUT"
                )
            ),
            headers={
                "content-type": content_type,
                "x-amz-checksum-sha256": checksum,
                "x-amz-meta-appsell-upload-id": upload_request_id,
            },
            expires_in_seconds=self.put_ttl,
            required_content_length=byte_size,
        )

    def put_cover(
        self,
        *,
        destination_key: str,
        source_path: Path,
        byte_size: int,
        sha256_hex: str,
        content_type: str,
    ) -> StoredObject:
        """Guarda a capa já saneada de um produto.

        Não passa pelo caminho dos materiais de aula de propósito. Aquele
        caminho valida contra ``SUPPORTED_MEDIA``, e alargar aquele catálogo
        para caber JPEG e WebP faria uma capa poder ser anexada como material —
        outro caminho, com outra política de download. A capa é uma imagem para
        exibir, então a disposição é sempre ``inline`` e o nome do arquivo nunca
        vem do vendedor: é derivado do digest.
        """
        if not is_cover_media(content_type):
            raise StorageCapabilityError("cover media type is not storable")
        extension = COVER_EXTENSION[content_type]
        response = self._control.put_object(
            Bucket=self.ready_bucket,
            Key=destination_key,
            Body=source_path.read_bytes(),
            ContentLength=byte_size,
            ContentType=content_type,
            ContentDisposition=f"inline; filename*=UTF-8''capa-{sha256_hex[:16]}.{extension}",
            CacheControl="private, no-store, max-age=0",
            ChecksumSHA256=_checksum_b64(sha256_hex),
        )
        version_id = str(response.get("VersionId") or "")
        if not version_id:
            raise StorageCapabilityError("cover PUT did not return VersionId")
        stored = self.head(bucket=self.ready_bucket, key=destination_key, version_id=version_id)
        if stored.size != byte_size or not object_sha256_matches(stored, sha256_hex):
            self.delete_version(
                bucket=self.ready_bucket, key=destination_key, version_id=version_id
            )
            raise StorageCapabilityError("stored cover does not match what was verified")
        return stored

    def presign_cover(self, *, key: str, version_id: str, media_type: str) -> PresignedRequest:
        """Endereço temporário para exibir a capa. Sempre inline, nunca download."""
        if not version_id:
            raise StorageCapabilityError("cover display requires an immutable VersionId")
        url = self._public.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.ready_bucket,
                "Key": key,
                "VersionId": version_id,
                "ResponseCacheControl": "private, max-age=300",
                "ResponseContentType": media_type,
            },
            ExpiresIn=self.get_ttl,
            HttpMethod="GET",
        )
        return PresignedRequest(url=str(url), headers={}, expires_in_seconds=self.get_ttl)

    def presign_get(
        self,
        *,
        bucket: str,
        key: str,
        version_id: str,
        file_name: str,
        media_type: str = "application/pdf",
        disposition: Literal["inline", "attachment"] = "inline",
    ) -> PresignedRequest:
        if not version_id:
            raise StorageCapabilityError("download requires an immutable VersionId")
        content_disposition = _asset_content_disposition(file_name, media_type, disposition)
        url = self._public.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": key,
                "VersionId": version_id,
                "ResponseContentDisposition": content_disposition,
                "ResponseCacheControl": "private, no-store, max-age=0",
                "ResponseContentType": media_type,
            },
            ExpiresIn=self.get_ttl,
            HttpMethod="GET",
        )
        return PresignedRequest(url=str(url), headers={}, expires_in_seconds=self.get_ttl)

    def head(self, *, bucket: str, key: str, version_id: str | None = None) -> StoredObject:
        params: dict[str, Any] = {"Bucket": bucket, "Key": key, "ChecksumMode": "ENABLED"}
        if version_id:
            params["VersionId"] = version_id
        response = self._control.head_object(**params)
        metadata = {
            str(name).lower(): str(value)
            for name, value in (response.get("Metadata") or {}).items()
        }
        actual_version = str(response.get("VersionId") or "")
        checksum = str(response.get("ChecksumSHA256") or "")
        if not actual_version:
            raise StorageCapabilityError("storage did not return VersionId")
        if not checksum:
            raise StorageCapabilityError("storage did not return SHA-256 checksum")
        return StoredObject(
            bucket=bucket,
            key=key,
            version_id=actual_version,
            size=int(response["ContentLength"]),
            content_type=str(response.get("ContentType") or "application/octet-stream"),
            checksum_sha256_b64=checksum,
            etag=str(response.get("ETag") or "").strip('"'),
            appsell_upload_id=metadata.get("appsell-upload-id", ""),
        )

    def object_exists(self, *, bucket: str, key: str) -> bool:
        try:
            self._control.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status == 404:
                return False
            raise

    @contextmanager
    def open_download(self, *, bucket: str, key: str, version_id: str) -> Iterator[BinaryIO]:
        try:
            response = self._control.get_object(Bucket=bucket, Key=key, VersionId=version_id)
            body: BinaryIO = response["Body"]
            try:
                yield body
            finally:
                body.close()
        except (BotoCoreError, ClientError, KeyError) as error:
            raise StorageError("S3 object download failed") from error

    def copy_verified(
        self, *, source: StoredObject, destination_bucket: str, destination_key: str
    ) -> StoredObject:
        response = self._control.copy_object(
            Bucket=destination_bucket,
            Key=destination_key,
            CopySource={
                "Bucket": source.bucket,
                "Key": source.key,
                "VersionId": source.version_id,
            },
            ContentType=source.content_type,
            MetadataDirective="REPLACE",
            Metadata={"appsell-upload-id": source.appsell_upload_id},
            ChecksumAlgorithm="SHA256",
        )
        version_id = str(response.get("VersionId") or "")
        if not version_id:
            raise StorageCapabilityError("copy did not return VersionId")
        try:
            copied = self.head(
                bucket=destination_bucket, key=destination_key, version_id=version_id
            )
            if (
                copied.size != source.size
                or copied.content_type != source.content_type
                or copied.checksum_sha256_b64 != source.checksum_sha256_b64
                or copied.appsell_upload_id != source.appsell_upload_id
            ):
                raise StorageError("copied object does not match verified source")
        except Exception:
            self.delete_version(
                bucket=destination_bucket, key=destination_key, version_id=version_id
            )
            raise
        return copied

    def put_verified(
        self,
        *,
        destination_bucket: str,
        destination_key: str,
        source_path: Path,
        byte_size: int,
        sha256_hex: str,
        content_type: str,
        upload_id: str,
        file_name: str,
    ) -> StoredObject:
        checksum = _checksum_b64(sha256_hex)
        # A document is stored to be downloaded. Asking for inline here is what
        # the delivery guard refuses, so promoting one would crash rather than
        # store.
        disposition = _asset_content_disposition(
            file_name, content_type, "attachment" if is_document(content_type) else "inline"
        )
        version_id = ""
        try:
            with source_path.open("rb") as source:
                response = self._control.put_object(
                    Bucket=destination_bucket,
                    Key=destination_key,
                    Body=source,
                    ContentLength=byte_size,
                    ContentType=content_type,
                    ContentDisposition=disposition,
                    CacheControl="private, no-store, max-age=0",
                    ChecksumSHA256=checksum,
                    Metadata={"appsell-upload-id": upload_id},
                )
            version_id = str(response.get("VersionId") or "")
            if not version_id:
                raise StorageCapabilityError("verified PUT did not return VersionId")
            ready = self.head(bucket=destination_bucket, key=destination_key, version_id=version_id)
            if (
                ready.size != byte_size
                or ready.content_type != content_type
                or ready.checksum_sha256_b64 != checksum
                or ready.appsell_upload_id != upload_id
            ):
                raise StorageError("ready object does not match verified asset")
            return ready
        except Exception:
            if version_id:
                self.delete_version(
                    bucket=destination_bucket, key=destination_key, version_id=version_id
                )
            raise

    def delete_version(self, *, bucket: str, key: str, version_id: str) -> None:
        if not version_id:
            raise StorageCapabilityError("delete requires exact VersionId")
        self._control.delete_object(Bucket=bucket, Key=key, VersionId=version_id)

    def delete_all_versions(self, *, bucket: str, key: str) -> int:
        deleted = 0
        paginator = self._control.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket, Prefix=key):
            objects = [
                {"Key": entry["Key"], "VersionId": entry["VersionId"]}
                for collection in (page.get("Versions", []), page.get("DeleteMarkers", []))
                for entry in collection
                if entry.get("Key") == key and entry.get("VersionId")
            ]
            if objects:
                self._control.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )
                deleted += len(objects)
        return deleted

    def delete_all_versions_under_prefix(self, *, bucket: str, prefix: str) -> int:
        if not prefix or not prefix.endswith("/"):
            raise StorageCapabilityError("prefix delete requires a directory prefix")
        deleted = 0
        while True:
            page = self._control.list_object_versions(
                Bucket=bucket,
                Prefix=prefix,
                MaxKeys=1_000,
            )
            objects = [
                {"Key": entry["Key"], "VersionId": entry["VersionId"]}
                for collection in (page.get("Versions", []), page.get("DeleteMarkers", []))
                for entry in collection
                if str(entry.get("Key") or "").startswith(prefix) and entry.get("VersionId")
            ]
            if not objects:
                return deleted
            response = self._control.delete_objects(
                Bucket=bucket,
                Delete={"Objects": objects, "Quiet": True},
            )
            if response.get("Errors"):
                raise StorageError("storage prefix version cleanup was incomplete")
            deleted += len(objects)

    def ensure_local_ci_controls(self, *, allowed_origin: str) -> None:
        for bucket in (self.quarantine_bucket, self.ready_bucket):
            try:
                self._control.head_bucket(Bucket=bucket)
            except ClientError as error:
                if error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                    raise
                self._control.create_bucket(Bucket=bucket)
            self._control.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
            methods = ["HEAD", "PUT"] if bucket == self.quarantine_bucket else ["GET", "HEAD"]
            allowed_headers = (
                ["content-type", "x-amz-checksum-sha256", "x-amz-meta-appsell-upload-id"]
                if bucket == self.quarantine_bucket
                else []
            )
            rule: dict[str, Any] = {
                "AllowedOrigins": [allowed_origin],
                "AllowedMethods": methods,
                "ExposeHeaders": ["etag", "x-amz-version-id", "x-amz-checksum-sha256"],
                "MaxAgeSeconds": 300,
            }
            if allowed_headers:
                rule["AllowedHeaders"] = allowed_headers
            self._control.put_bucket_cors(
                Bucket=bucket,
                CORSConfiguration={"CORSRules": [rule]},
            )

    def probe_availability(self) -> None:
        for bucket in (self.quarantine_bucket, self.ready_bucket):
            self._control.head_bucket(Bucket=bucket)

    def assert_runtime_controls(
        self,
        *,
        allowed_origin: str,
        require_tls: bool,
        cors_mode: Literal["s3", "edge"] = "s3",
    ) -> None:
        if require_tls and not self.public_endpoint.startswith("https://"):
            raise StorageCapabilityError("public storage endpoint must use TLS")
        if cors_mode not in {"s3", "edge"}:
            raise StorageCapabilityError("storage CORS mode is invalid")
        for bucket in (self.quarantine_bucket, self.ready_bucket):
            versioning = self._control.get_bucket_versioning(Bucket=bucket)
            if versioning.get("Status") != "Enabled":
                raise StorageCapabilityError(f"versioning is not enabled for {bucket}")
            if cors_mode == "s3":
                cors = self._control.get_bucket_cors(Bucket=bucket).get("CORSRules", [])
                expected_methods = (
                    {"HEAD", "PUT"} if bucket == self.quarantine_bucket else {"GET", "HEAD"}
                )
                expected_headers = (
                    {"content-type", "x-amz-checksum-sha256", "x-amz-meta-appsell-upload-id"}
                    if bucket == self.quarantine_bucket
                    else set()
                )
                if len(cors) != 1:
                    raise StorageCapabilityError(f"unexpected CORS rule count for {bucket}")
                rule = cors[0]
                actual_headers = {item.lower() for item in rule.get("AllowedHeaders", [])}
                if (
                    rule.get("AllowedOrigins") != [allowed_origin]
                    or set(rule.get("AllowedMethods", [])) != expected_methods
                    or actual_headers != expected_headers
                ):
                    raise StorageCapabilityError(f"CORS policy is not exact for {bucket}")
            self._assert_anonymous_denied(bucket)

    def assert_public_edge_controls(
        self,
        *,
        require_tls: bool,
        cors_mode: Literal["s3", "edge"] = "s3",
        allowed_origin: str = "",
    ) -> None:
        """Prove anonymous denial over the browser-facing endpoint.

        Run this from a host/runner that can reach ``storage_public_endpoint``;
        the API process deliberately uses the internal endpoint for its startup
        and periodic control audit to avoid depending on container hairpin NAT.
        """
        if require_tls and not self.public_endpoint.startswith("https://"):
            raise StorageCapabilityError("public storage endpoint must use TLS")
        if cors_mode not in {"s3", "edge"}:
            raise StorageCapabilityError("storage CORS mode is invalid")
        for bucket in (self.quarantine_bucket, self.ready_bucket):
            self._assert_anonymous_denied(
                bucket,
                anonymous_client=self._anonymous_public,
                cleanup_client=self._public,
            )
        if cors_mode == "edge":
            self._assert_edge_cors(allowed_origin)

    def _assert_edge_cors(self, allowed_origin: str) -> None:
        if not allowed_origin:
            raise StorageCapabilityError("edge CORS mode requires an allowed origin")
        request_headers = "content-type,x-amz-checksum-sha256,x-amz-meta-appsell-upload-id"
        with httpx.Client(
            timeout=httpx.Timeout(5.0, connect=2.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            allowed = client.options(
                f"{self.public_endpoint}/",
                headers={
                    "Origin": allowed_origin,
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": request_headers,
                },
            )
            if allowed.status_code not in {200, 204}:
                raise StorageCapabilityError(
                    f"public storage CORS preflight returned HTTP {allowed.status_code}"
                )
            if allowed.headers.get("access-control-allow-origin") != allowed_origin:
                raise StorageCapabilityError("public storage CORS origin is not exact")
            if allowed.headers.get("access-control-allow-credentials") == "true":
                raise StorageCapabilityError("public storage CORS must not allow credentials")
            actual_methods = {
                method.strip().upper()
                for method in allowed.headers.get("access-control-allow-methods", "").split(",")
                if method.strip()
            }
            if not {"PUT", "OPTIONS"}.issubset(actual_methods):
                raise StorageCapabilityError("public storage CORS methods are incomplete")
            actual_headers = {
                header.strip().lower()
                for header in allowed.headers.get("access-control-allow-headers", "").split(",")
                if header.strip()
            }
            if not {
                "content-type",
                "x-amz-checksum-sha256",
                "x-amz-meta-appsell-upload-id",
            }.issubset(actual_headers):
                raise StorageCapabilityError("public storage CORS headers are incomplete")

            denied = client.options(
                f"{self.public_endpoint}/",
                headers={
                    "Origin": "https://appsell-cors-denied.invalid",
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": request_headers,
                },
            )
            if denied.headers.get("access-control-allow-origin"):
                raise StorageCapabilityError("public storage CORS allows an untrusted origin")

    def _assert_anonymous_denied(
        self,
        bucket: str,
        *,
        anonymous_client: Any | None = None,
        cleanup_client: Any | None = None,
    ) -> None:
        anonymous = anonymous_client or self._anonymous
        cleanup = cleanup_client or self._control
        key = f"appsell-anonymous-probe/{uuid4()}"
        payload = b"appsell-anonymous-write-probe"
        checksum = base64.b64encode(hashlib.sha256(payload).digest()).decode("ascii")
        probes = (
            ("list", lambda: anonymous.list_objects_v2(Bucket=bucket, MaxKeys=1)),
            ("get", lambda: anonymous.get_object(Bucket=bucket, Key=key)),
            ("head", lambda: anonymous.head_object(Bucket=bucket, Key=key)),
            ("delete", lambda: anonymous.delete_object(Bucket=bucket, Key=key)),
            (
                "put",
                lambda: anonymous.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=payload,
                    ContentLength=len(payload),
                    ChecksumSHA256=checksum,
                ),
            ),
        )
        for operation, probe in probes:
            try:
                response = probe()  # type: ignore[no-untyped-call]
            except ClientError as error:
                status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status not in {401, 403}:
                    raise StorageCapabilityError(
                        f"anonymous {operation} probe returned unexpected HTTP {status}"
                    ) from error
            except BotoCoreError as error:
                raise StorageCapabilityError(f"anonymous {operation} probe failed") from error
            else:
                body = response.get("Body")
                if body is not None and hasattr(body, "close"):
                    body.close()
                version_id = str(response.get("VersionId") or "")
                try:
                    if version_id:
                        cleanup.delete_object(
                            Bucket=bucket,
                            Key=key,
                            VersionId=version_id,
                        )
                    else:
                        self._delete_all_versions_with_client(
                            cleanup,
                            bucket=bucket,
                            key=key,
                        )
                except Exception as cleanup_error:
                    raise StorageCapabilityError(
                        f"anonymous {operation} is allowed and probe cleanup failed"
                    ) from cleanup_error
                raise StorageCapabilityError(f"anonymous {operation} is allowed for {bucket}")

    @staticmethod
    def _delete_all_versions_with_client(client: Any, *, bucket: str, key: str) -> None:
        paginator = client.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=bucket, Prefix=key):
            objects = [
                {"Key": entry["Key"], "VersionId": entry["VersionId"]}
                for collection in (page.get("Versions", []), page.get("DeleteMarkers", []))
                for entry in collection
                if entry.get("Key") == key and entry.get("VersionId")
            ]
            if objects:
                client.delete_objects(
                    Bucket=bucket,
                    Delete={"Objects": objects, "Quiet": True},
                )


def object_sha256_matches(stored: StoredObject, expected_hex: str) -> bool:
    return stored.checksum_sha256_b64 == _checksum_b64(expected_hex)


def _asset_content_disposition(
    file_name: str,
    media_type: str,
    disposition: Literal["inline", "attachment"],
) -> str:
    if (
        not file_name
        or len(file_name.encode("utf-8")) > 160
        or "/" in file_name
        or "\\" in file_name
        or any(ord(character) < 32 or ord(character) == 127 for character in file_name)
        or (media_type != PAGE_IMAGE_MEDIA_TYPE and not extension_matches(media_type, file_name))
        or (media_type == PAGE_IMAGE_MEDIA_TYPE and not file_name.lower().endswith(".png"))
        or (media_type not in SUPPORTED_MEDIA and media_type != PAGE_IMAGE_MEDIA_TYPE)
        or disposition not in {"inline", "attachment"}
        # A document is never opened inside the page. Asking for it inline is a
        # programming mistake, and answering it would be the vulnerability.
        or (is_document(media_type) and disposition != "attachment")
    ):
        raise ValueError("asset file_name or media_type is invalid")
    return f"{disposition}; filename*=UTF-8''{quote(file_name, safe='')}"


def _checksum_b64(sha256_hex: str) -> str:
    if len(sha256_hex) != 64:
        raise ValueError("SHA-256 must contain 64 hexadecimal characters")
    try:
        raw = bytes.fromhex(sha256_hex)
    except ValueError as error:
        raise ValueError("SHA-256 must be hexadecimal") from error
    return base64.b64encode(raw).decode("ascii")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
