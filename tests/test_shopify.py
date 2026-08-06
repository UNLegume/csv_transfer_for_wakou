import json
from collections.abc import Iterator
from datetime import date

import httpx
import pytest

from wakou_transfer.domain import Carrier
from wakou_transfer.shopify import (
    DEFAULT_API_VERSION,
    ShopifyClient,
    ShopifyGraphQLError,
    ShopifyHTTPError,
    build_order_search_query,
    resolve_delivery_metafields,
)


def address(name: str = "山田 太郎") -> dict[str, str]:
    return {
        "name": name,
        "zip": "001-0001",
        "province": "北海道",
        "city": "札幌市中央区",
        "address1": "北1条1-1",
        "address2": "ワコウビル",
        "phone": "090-0000-0000",
    }


def metafield(value: str | None) -> dict[str, str] | None:
    return None if value is None else {"value": value}


def line_item(
    item_id: str,
    *,
    carrier: str | None,
    product_carrier: str | None = None,
    quantity: int = 1,
) -> dict[str, object]:
    return {
        "id": item_id,
        "name": f"商品{item_id}",
        "quantity": quantity,
        "sku": f"SKU-{item_id}",
        "variant": {
            "carrier": metafield(carrier),
            "maxQuantity": metafield("2"),
            "product": {
                "carrier": metafield(product_carrier),
                "maxQuantity": metafield("4"),
            },
        },
    }


def order_node(
    order_id: str,
    items: list[dict[str, object]],
    *,
    line_has_next: bool = False,
    line_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "id": order_id,
        "name": "#0000123",
        "createdAt": "2026-08-02T10:20:30Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "cancelledAt": None,
        "shippingAddress": address(),
        "billingAddress": address("山田 花子"),
        "currentTotalPriceSet": {
            "shopMoney": {"amount": "1200.50", "currencyCode": "JPY"}
        },
        "lineItems": {
            "nodes": items,
            "pageInfo": {"hasNextPage": line_has_next, "endCursor": line_cursor},
        },
    }


class Responses:
    def __init__(self, *payloads: tuple[int, dict[str, object]]) -> None:
        self.payloads: Iterator[tuple[int, dict[str, object]]] = iter(payloads)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, payload = next(self.payloads)
        return httpx.Response(status, json=payload, request=request)


@pytest.mark.asyncio
async def test_from_env_uses_client_credentials_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "example.myshopify.com")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "client-secret")
    monkeypatch.delenv("SHOPIFY_ADMIN_ACCESS_TOKEN", raising=False)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/admin/oauth/access_token":
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(
                200,
                json={"access_token": "temporary-token", "expires_in": 86399},
                request=request,
            )
        assert request.headers["X-Shopify-Access-Token"] == "temporary-token"
        return httpx.Response(
            200,
            json={
                "data": {
                    "orders": {
                        "nodes": [],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
            request=request,
        )

    client = ShopifyClient.from_env(transport=httpx.MockTransport(handler))
    orders = await client.fetch_orders(
        date(2026, 8, 1), date(2026, 8, 2), shipping_date=date(2026, 8, 3)
    )

    assert orders == ()
    assert [request.url.path for request in requests] == [
        "/admin/oauth/access_token",
        f"/admin/api/{DEFAULT_API_VERSION}/graphql.json",
    ]


def test_build_order_search_query_includes_dates_and_statuses() -> None:
    assert build_order_search_query(
        date(2026, 8, 1),
        date(2026, 8, 2),
        financial_status="paid",
        fulfillment_status="unfulfilled",
        include_cancelled=False,
    ) == (
        "created_at:>=2026-08-01 created_at:<=2026-08-02 financial_status:paid "
        "fulfillment_status:unfulfilled status:not_cancelled"
    )


@pytest.mark.parametrize(
    ("variant", "product", "expected"),
    [
        ("yamato", "sagawa", (Carrier.YAMATO, 2)),
        (None, "sagawa", (Carrier.SAGAWA, 4)),
        (None, None, (None, None)),
        ("invalid", "yamato", (Carrier.NEEDS_REVIEW, 2)),
        (None, "invalid", (Carrier.NEEDS_REVIEW, 4)),
    ],
)
def test_resolve_delivery_metafields(
    variant: str | None,
    product: str | None,
    expected: tuple[Carrier | None, int | None],
) -> None:
    resolved = resolve_delivery_metafields(
        variant_carrier=variant,
        product_carrier=product,
        variant_max_quantity="2" if variant is not None else None,
        product_max_quantity="4" if product is not None else None,
    )
    assert (resolved.carrier, resolved.max_quantity) == expected


@pytest.mark.asyncio
async def test_fetch_orders_converts_shopify_data_and_uses_configured_version() -> None:
    responses = Responses(
        (
            200,
            {
                "data": {
                    "orders": {
                        "nodes": [
                            order_node(
                                "gid://shopify/Order/1",
                                [line_item("1", carrier=None, product_carrier="sagawa")],
                            )
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        )
    )
    client = ShopifyClient(
        "example.myshopify.com",
        "secret",
        api_version="2026-07",
        transport=httpx.MockTransport(responses),
    )

    orders = await client.fetch_orders(
        date(2026, 8, 1), date(2026, 8, 2), shipping_date=date(2026, 8, 4)
    )

    assert len(orders) == 1
    order = orders[0]
    assert order.order_id == "gid://shopify/Order/1"
    assert order.order_number == "#0000123"
    assert order.shipping_date == date(2026, 8, 4)
    assert order.shipping_address.address1 == "北海道札幌市中央区北1条1-1"
    assert order.line_items[0].carrier is Carrier.SAGAWA
    assert order.line_items[0].yamato_max_quantity == 2
    assert str(order.total.amount) == "1200.50"
    assert responses.requests[0].url == (
        "https://example.myshopify.com/admin/api/2026-07/graphql.json"
    )
    assert responses.requests[0].headers["X-Shopify-Access-Token"] == "secret"
    query = json.loads(responses.requests[0].content)["query"]
    assert 'namespace: "delivery", key: "carrier"' in query
    assert 'namespace: "delivery", key: "yamato_max_quantity"' in query
    variables = json.loads(responses.requests[0].content)["variables"]
    assert variables["query"] == (
        "created_at:>=2026-08-01 created_at:<=2026-08-02 financial_status:paid "
        "status:not_cancelled"
    )


@pytest.mark.asyncio
async def test_fetch_orders_paginates_orders_and_each_orders_line_items() -> None:
    first = order_node(
        "gid://shopify/Order/1",
        [line_item("1", carrier="yamato")],
        line_has_next=True,
        line_cursor="line-1",
    )
    second = order_node("gid://shopify/Order/2", [line_item("3", carrier="sagawa")])
    responses = Responses(
        (
            200,
            {
                "data": {
                    "orders": {
                        "nodes": [first],
                        "pageInfo": {"hasNextPage": True, "endCursor": "order-1"},
                    }
                }
            },
        ),
        (
            200,
            {
                "data": {
                    "order": {
                        "lineItems": {
                            "nodes": [line_item("2", carrier="yamato")],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            },
        ),
        (
            200,
            {
                "data": {
                    "orders": {
                        "nodes": [second],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        ),
    )
    client = ShopifyClient(
        "example.myshopify.com", "secret", transport=httpx.MockTransport(responses)
    )

    orders = await client.fetch_orders(
        date(2026, 8, 1), date(2026, 8, 2), shipping_date=date(2026, 8, 4)
    )

    assert [len(order.line_items) for order in orders] == [2, 1]
    request_bodies = [json.loads(request.content) for request in responses.requests]
    assert request_bodies[1]["variables"] == {"id": "gid://shopify/Order/1", "after": "line-1"}
    assert request_bodies[2]["variables"]["after"] == "order-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 429, 500])
async def test_fetch_orders_reports_http_failures(status: int) -> None:
    responses = Responses((status, {"error": "failed"}))
    client = ShopifyClient(
        "example.myshopify.com", "secret", transport=httpx.MockTransport(responses)
    )

    with pytest.raises(ShopifyHTTPError, match=str(status)):
        await client.fetch_orders(
            date(2026, 8, 1), date(2026, 8, 2), shipping_date=date(2026, 8, 4)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"errors": [{"message": "Access denied"}]},
        {"data": {"orders": None}, "errors": [{"message": "Throttled"}]},
        {"data": {"orders": {"userErrors": [{"message": "Bad query"}]}}},
    ],
)
async def test_fetch_orders_reports_graphql_and_user_errors(payload: dict[str, object]) -> None:
    responses = Responses((200, payload))
    client = ShopifyClient(
        "example.myshopify.com", "secret", transport=httpx.MockTransport(responses)
    )

    with pytest.raises(ShopifyGraphQLError):
        await client.fetch_orders(
            date(2026, 8, 1), date(2026, 8, 2), shipping_date=date(2026, 8, 4)
        )


def test_default_api_version_is_explicit() -> None:
    assert DEFAULT_API_VERSION == "2026-07"
