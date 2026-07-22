from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .config import Settings

_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")


class WebhookSecretConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WebhookSecretResolver:
    _secrets: Mapping[str, bytes]

    @classmethod
    def from_settings(cls, settings: Settings) -> WebhookSecretResolver:
        serialized = settings.webhook_local_keyring_b64_json
        if not serialized:
            return cls(MappingProxyType({}))
        if settings.environment not in {"local", "ci"} and not settings.webhook_env_keyring_allowed:
            raise WebhookSecretConfigurationError(
                "local webhook secrets are forbidden outside local/CI"
            )
        try:
            parsed: Any = json.loads(serialized)
        except json.JSONDecodeError as error:
            raise WebhookSecretConfigurationError("webhook keyring must be valid JSON") from error
        if not isinstance(parsed, list) or len(parsed) > 32:
            raise WebhookSecretConfigurationError("webhook keyring must contain at most 32 keys")
        if not parsed:
            return cls(MappingProxyType({}))

        forbidden = _forbidden_keys(settings)
        forbidden_hashes = _forbidden_hashes(settings)
        secrets: dict[str, bytes] = {}
        for entry in parsed:
            if not isinstance(entry, dict) or set(entry) != {"reference", "key"}:
                raise WebhookSecretConfigurationError("webhook key entry is invalid")
            reference = entry["reference"]
            encoded = entry["key"]
            if (
                not isinstance(reference, str)
                or _REFERENCE.fullmatch(reference) is None
                or not isinstance(encoded, str)
            ):
                raise WebhookSecretConfigurationError("webhook key entry is invalid")
            if reference in secrets:
                raise WebhookSecretConfigurationError("webhook secret references must be unique")
            key = _decode_key(encoded, "webhook secret")
            if any(hmac.compare_digest(key, value) for value in forbidden):
                raise WebhookSecretConfigurationError("webhook secrets must be independent")
            if hashlib.sha256(key).hexdigest() in forbidden_hashes:
                raise WebhookSecretConfigurationError("webhook secrets must be independent")
            if any(hmac.compare_digest(key, value) for value in secrets.values()):
                raise WebhookSecretConfigurationError("webhook secret material must be unique")
            secrets[reference] = key
        return cls(MappingProxyType(secrets))

    def resolve(self, reference: str) -> bytes | None:
        return self._secrets.get(reference)


def _forbidden_keys(settings: Settings) -> list[bytes]:
    values = [
        settings.context_hmac_key_b64,
        settings.service_credential_b64,
    ]
    try:
        internal = json.loads(settings.internal_hmac_keyring_b64_json or "[]")
    except json.JSONDecodeError as error:
        raise WebhookSecretConfigurationError("internal keyring must be valid JSON") from error
    if isinstance(internal, list):
        values.extend(entry.get("key", "") for entry in internal if isinstance(entry, dict))
    return [_decode_key(value, "forbidden secret") for value in values if value]


def _forbidden_hashes(settings: Settings) -> frozenset[str]:
    try:
        values: Any = json.loads(settings.webhook_forbidden_key_sha256_json)
    except json.JSONDecodeError as error:
        raise WebhookSecretConfigurationError("forbidden key hashes must be valid JSON") from error
    if (
        not isinstance(values, list)
        or len(values) > 16
        or any(
            not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in values
        )
        or len(set(values)) != len(values)
    ):
        raise WebhookSecretConfigurationError("forbidden key hashes are invalid")
    return frozenset(values)


def _decode_key(value: str, label: str) -> bytes:
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise WebhookSecretConfigurationError(f"{label} must be canonical base64") from error
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
        raise WebhookSecretConfigurationError(f"{label} must contain 32 bytes")
    return decoded
