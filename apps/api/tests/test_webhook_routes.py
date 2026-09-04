from appsell_api.webhook_routes import _endpoint_resolution


def test_v3_endpoint_resolution_is_scoped_by_seller() -> None:
    seller_id = "11111111-1111-4111-8111-111111111111"

    statement, parameters = _endpoint_resolution("a" * 64, seller_id)

    assert "cast(:seller_id as uuid)" in str(statement)
    assert parameters == {"endpoint_hash": "a" * 64, "seller_id": seller_id}


def test_legacy_endpoint_resolution_remains_available() -> None:
    statement, parameters = _endpoint_resolution("b" * 64, None)

    assert "seller_id" not in str(statement)
    assert parameters == {"endpoint_hash": "b" * 64}
