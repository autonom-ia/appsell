import pytest
from httpx import ASGITransport, AsyncClient

from appsell_api import main


@pytest.mark.asyncio
async def test_liveness_does_not_require_database() -> None:
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
