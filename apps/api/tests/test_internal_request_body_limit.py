from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from appsell_api.internal_requests import _read_limited_body


def _request(chunks: list[bytes], headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    messages = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> dict[str, Any]:
        return (
            messages.pop(0)
            if messages
            else {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }
        )

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/webhooks/checkouts/test",
        "query_string": b"",
        "headers": headers or [],
    }
    return Request(scope, receive=receive)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_private_api_streams_exact_limit_without_unbounded_body_buffer() -> None:
    body = await _read_limited_body(_request([b"a" * 32_768, b"b" * 32_768]))
    assert len(body) == 65_536
    with pytest.raises(HTTPException) as error:
        await _read_limited_body(_request([b"a" * 32_768, b"b" * 32_769]))
    assert error.value.status_code == 413


@pytest.mark.asyncio
async def test_private_api_rejects_oversized_content_length_before_reading() -> None:
    called = False

    async def receive() -> dict[str, Any]:
        nonlocal called
        called = True
        return {"type": "http.request", "body": b"", "more_body": False}

    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/v1/webhooks/checkouts/test",
        "query_string": b"",
        "headers": [(b"content-length", b"65537")],
    }
    with pytest.raises(HTTPException) as error:
        await _read_limited_body(Request(scope, receive=receive))  # type: ignore[arg-type]
    assert error.value.status_code == 413
    assert called is False
