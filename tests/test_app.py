from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI

from wakou_transfer.app import PreviewStore, create_app
from wakou_transfer.config import AppConfig
from wakou_transfer.domain import Address, Carrier, LineItem, Money, ShippingOrder
from wakou_transfer.history import ExportHistory


class FakeShopifyClient:
    def __init__(self, orders: tuple[ShippingOrder, ...]) -> None:
        self.orders = orders
        self.calls: list[tuple[date, date, date]] = []

    async def fetch_orders(
        self,
        start_date: date,
        end_date: date,
        *,
        shipping_date: date,
        financial_status: str = "paid",
        fulfillment_status: str | None = None,
        include_cancelled: bool = False,
    ) -> tuple[ShippingOrder, ...]:
        self.calls.append((start_date, end_date, shipping_date))
        return self.orders


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        auth=("operator", "test-secret"),
    )


def config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "sender": {
                "company_name": "株式会社テスト",
                "requester_name": "テスト店",
                "postal_code": "1000001",
                "address": "東京都千代田区千代田1-1",
                "phone": "0312345678",
            },
            "sagawa": {"billing_code": "000123456789"},
            "yamato": {"requester_code": "REQ001"},
            "auth_username": "operator",
            "auth_password": "test-secret",
        }
    )


def order(carrier: Carrier | None = Carrier.YAMATO) -> ShippingOrder:
    address = Address(
        name="山田 太郎",
        postal_code="0010001",
        address1="北海道札幌市中央区1-1",
        address2="テストビル101",
        phone="09012345678",
    )
    return ShippingOrder(
        order_id="gid://shopify/Order/1",
        order_number="#1673",
        line_items=(LineItem(sku="SKU1", name="商品", quantity=1, carrier=carrier),),
        shipping_address=address,
        billing_address=address,
        total=Money(amount=Decimal("3030"), currency="JPY"),
        shipping_date=date(2026, 8, 4),
    )


def test_preview_store_expires_and_caps_personal_data() -> None:
    now = [0.0]
    store = PreviewStore(max_batches=2, ttl_seconds=10, clock=lambda: now[0])
    first = store.put((order(),))
    store.put((order(),))
    store.put((order(),))

    assert store.get(first) is None
    expiring = store.put((order(),))
    now[0] = 11.0
    assert store.get(expiring) is None


@pytest.mark.asyncio
async def test_operation_endpoints_require_authentication(tmp_path: Path) -> None:
    app = create_app(
        config(),
        FakeShopifyClient(tuple()),
        ExportHistory(tmp_path / "history.sqlite3"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as anonymous:
        index = await anonymous.get("/")
        preview = await anonymous.post(
            "/api/preview",
            json={
                "start_date": "2026-08-01",
                "end_date": "2026-08-02",
                "shipping_date": "2026-08-04",
            },
        )
        history = await anonymous.get("/api/history")

    assert index.status_code == 401
    assert preview.status_code == 401
    assert history.status_code == 401


@pytest.mark.asyncio
async def test_index_serves_japanese_operation_screen(tmp_path: Path) -> None:
    app = create_app(
        config(),
        FakeShopifyClient(tuple()),
        ExportHistory(tmp_path / "history.sqlite3"),
    )
    async with client_for(app) as client:
        response = await client.get("/")

    assert response.status_code == 200
    assert "ワコウ送り状CSV変換" in response.text
    assert "Shopifyから注文取得" in response.text
    assert "ヤマト候補を選択" in response.text
    assert "佐川候補を選択" in response.text
    assert "未出力の注文" in response.text
    assert "出力済み一覧" in response.text
    assert "loadHistory" in response.text
    assert "selectedIds:new Set()" in response.text
    assert "selectedIdsForCarrier" in response.text
    assert "carrier-radio" in response.text
    assert "document.body.appendChild(link)" in response.text
    assert "setTimeout(()=>URL.revokeObjectURL(objectUrl),1000)" in response.text


@pytest.mark.asyncio
async def test_history_endpoint_lists_exports_without_personal_data(tmp_path: Path) -> None:
    history = ExportHistory(tmp_path / "history.sqlite3")
    history.record(
        "batch-1",
        "gid://shopify/Order/1",
        "#1673",
        Carrier.SAGAWA,
    )
    app = create_app(config(), FakeShopifyClient(tuple()), history)

    async with client_for(app) as client:
        response = await client.get("/api/history")

    assert response.status_code == 200
    assert response.json()["records"] == [
        {
            "batch_id": "batch-1",
            "order_id": "gid://shopify/Order/1",
            "order_number": "#1673",
            "carrier": "sagawa",
            "exported_at": history.list_recent()[0].exported_at,
            "reexport_reason": None,
        }
    ]
    assert "山田" not in response.text
    assert "北海道" not in response.text
    assert "09012345678" not in response.text


@pytest.mark.asyncio
async def test_history_endpoint_pages_to_old_exports_and_reports_total(tmp_path: Path) -> None:
    history = ExportHistory(tmp_path / "history.sqlite3")
    history.record_batch(
        "batch-many",
        [(f"order-{number}", f"#{number:04d}", Carrier.SAGAWA) for number in range(1, 106)],
    )
    app = create_app(config(), FakeShopifyClient(tuple()), history)

    async with client_for(app) as client:
        first_page = await client.get("/api/history")
        response = await client.get("/api/history", params={"limit": 10, "offset": 100})
        searched = await client.get(
            "/api/history",
            params={"order_number": "0001"},
        )

    assert first_page.status_code == 200
    assert len(first_page.json()["records"]) == 100
    assert first_page.json()["total"] == 105
    assert first_page.json()["has_more"] is True
    assert response.status_code == 200
    assert response.json()["total"] == 105
    assert response.json()["has_more"] is False
    assert [record["order_number"] for record in response.json()["records"]] == [
        "#0005",
        "#0004",
        "#0003",
        "#0002",
        "#0001",
    ]
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["has_more"] is False
    assert [record["order_id"] for record in searched.json()["records"]] == ["order-1"]
    assert "recipient_name" not in response.text
    assert "address" not in response.text
    assert "phone" not in response.text


@pytest.mark.asyncio
async def test_history_endpoint_validates_paging_parameters(tmp_path: Path) -> None:
    app = create_app(
        config(),
        FakeShopifyClient(tuple()),
        ExportHistory(tmp_path / "history.sqlite3"),
    )

    async with client_for(app) as client:
        responses = [
            await client.get("/api/history", params={"limit": 0}),
            await client.get("/api/history", params={"limit": 201}),
            await client.get("/api/history", params={"offset": -1}),
        ]

    assert [response.status_code for response in responses] == [422, 422, 422]


@pytest.mark.asyncio
async def test_preview_fetches_orders_and_returns_decision(tmp_path: Path) -> None:
    fake = FakeShopifyClient((order(),))
    app = create_app(config(), fake, ExportHistory(tmp_path / "history.sqlite3"))
    async with client_for(app) as client:
        response = await client.post(
            "/api/preview",
            json={
                "start_date": "2026-08-01",
                "end_date": "2026-08-02",
                "shipping_date": "2026-08-04",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert fake.calls == [(date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 4))]
    assert payload["orders"][0]["order_number"] == "#1673"
    assert payload["orders"][0]["suggested_carrier"] == "yamato"
    assert payload["orders"][0]["errors"] == []
    assert payload["orders"][0]["already_exported"] is False
    assert payload["preview_id"]


@pytest.mark.asyncio
async def test_export_returns_cp932_csv_and_records_history(tmp_path: Path) -> None:
    fake = FakeShopifyClient((order(),))
    history = ExportHistory(tmp_path / "history.sqlite3")
    async with client_for(create_app(config(), fake, history)) as client:
        preview = (
            await client.post(
                "/api/preview",
                json={
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "shipping_date": "2026-08-04",
                },
            )
        ).json()
        response = await client.post(
            "/api/export/yamato",
            json={
                "preview_id": preview["preview_id"],
                "order_ids": ["gid://shopify/Order/1"],
            },
        )
        history_response = await client.get("/api/history")

    assert response.status_code == 200
    assert "送り状_ヤマト_20260804.csv" in unquote(response.headers["content-disposition"])
    decoded = response.content.decode("cp932")
    assert "お客様管理番号" in decoded
    assert "1673" in decoded
    assert history.exported_order_ids(["gid://shopify/Order/1"])
    assert history_response.status_code == 200
    assert history_response.json()["records"][0]["order_id"] == preview["orders"][0]["order_id"]
    assert history_response.json()["records"][0]["order_number"] == "#1673"
    assert history_response.json()["records"][0]["carrier"] == "yamato"


@pytest.mark.asyncio
async def test_duplicate_export_requires_reexport_reason(tmp_path: Path) -> None:
    fake = FakeShopifyClient((order(),))
    history = ExportHistory(tmp_path / "history.sqlite3")
    history.record("old", "gid://shopify/Order/1", "#1673", Carrier.YAMATO)
    async with client_for(create_app(config(), fake, history)) as client:
        preview = (
            await client.post(
                "/api/preview",
                json={
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "shipping_date": "2026-08-04",
                },
            )
        ).json()
        blocked = await client.post(
            "/api/export/yamato",
            json={
                "preview_id": preview["preview_id"],
                "order_ids": ["gid://shopify/Order/1"],
            },
        )
        history_after_block = (await client.get("/api/history")).json()["records"]
        allowed = await client.post(
            "/api/export/yamato",
            json={
                "preview_id": preview["preview_id"],
                "order_ids": ["gid://shopify/Order/1"],
                "reexport_reason": "再送依頼のため",
            },
        )
        history_after_reexport = (await client.get("/api/history")).json()["records"]

    assert blocked.status_code == 409
    assert "再出力理由" in blocked.json()["detail"]
    assert len(history_after_block) == 1
    assert allowed.status_code == 200
    assert len(history_after_reexport) == 2
    assert history_after_reexport[0]["order_id"] == "gid://shopify/Order/1"
    assert history_after_reexport[0]["reexport_reason"] == "再送依頼のため"


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", (Carrier.YAMATO, Carrier.SAGAWA))
async def test_unconfigured_product_can_be_manually_routed_without_reason(
    tmp_path: Path,
    carrier: Carrier,
) -> None:
    fake = FakeShopifyClient((order(None),))
    history = ExportHistory(tmp_path / "history.sqlite3")
    async with client_for(create_app(config(), fake, history)) as client:
        preview = (
            await client.post(
                "/api/preview",
                json={
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "shipping_date": "2026-08-04",
                },
            )
        ).json()
        allowed = await client.post(
            f"/api/export/{carrier.value}",
            json={
                "preview_id": preview["preview_id"],
                "order_ids": ["gid://shopify/Order/1"],
                "overrides": {"gid://shopify/Order/1": carrier.value},
            },
        )

    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_changing_an_automatically_assigned_carrier_still_requires_reason(
    tmp_path: Path,
) -> None:
    fake = FakeShopifyClient((order(Carrier.YAMATO),))
    history = ExportHistory(tmp_path / "history.sqlite3")
    async with client_for(create_app(config(), fake, history)) as client:
        preview = (
            await client.post(
                "/api/preview",
                json={
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "shipping_date": "2026-08-04",
                },
            )
        ).json()
        blocked = await client.post(
            "/api/export/sagawa",
            json={
                "preview_id": preview["preview_id"],
                "order_ids": ["gid://shopify/Order/1"],
                "overrides": {"gid://shopify/Order/1": "sagawa"},
            },
        )

    assert blocked.status_code == 422
    assert "理由" in blocked.json()["detail"]
