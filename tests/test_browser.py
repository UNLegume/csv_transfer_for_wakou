from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import Page, Route, expect, sync_playwright
from uvicorn import Config, Server

from wakou_transfer.app import OrderSource, create_app
from wakou_transfer.config import AppConfig
from wakou_transfer.domain import Address, Carrier, LineItem, Money, ShippingOrder
from wakou_transfer.history import ExportHistory


class BrowserOrderSource(OrderSource):
    def __init__(self, orders: tuple[ShippingOrder, ...]) -> None:
        self.orders = orders

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
        return tuple(
            ShippingOrder(
                order_id=order.order_id,
                order_number=order.order_number,
                line_items=order.line_items,
                shipping_address=order.shipping_address,
                billing_address=order.billing_address,
                total=order.total,
                shipping_date=shipping_date,
            )
            for order in self.orders
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


def order(number: int, carrier: Carrier | None) -> ShippingOrder:
    address = Address(
        name="テスト 太郎",
        postal_code="1000001",
        address1="東京都千代田区テスト町1-1",
        address2="デモビル101",
        phone="0312345678",
    )
    return ShippingOrder(
        order_id=f"gid://shopify/Order/{number}",
        order_number=f"#BROWSER{number:03d}",
        line_items=(
            LineItem(
                sku=f"SKU{number}",
                name=f"テスト商品{number}",
                quantity=1,
                carrier=carrier,
            ),
        ),
        shipping_address=address,
        billing_address=address,
        total=Money(amount=Decimal("1000"), currency="JPY"),
        shipping_date=date(2026, 8, 7),
    )


@contextmanager
def running_app(tmp_path: Path, *, seed_long_history: bool = False) -> Iterator[str]:
    history = ExportHistory(tmp_path / "browser-history.sqlite3")
    if seed_long_history:
        history.record(
            "oldest-batch",
            "gid://shopify/Order/2",
            "#BROWSER002",
            Carrier.SAGAWA,
        )
        for number in range(4, 108):
            history.record(
                f"batch-{number}",
                f"gid://shopify/Order/{number}",
                f"#ARCHIVE{number:03d}",
                Carrier.SAGAWA,
            )
    app = create_app(
        config(),
        BrowserOrderSource(
            (
                order(1, Carrier.YAMATO),
                order(2, Carrier.SAGAWA),
                order(3, None),
            )
        ),
        history,
    )
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = Server(Config(app, log_level="warning", lifespan="off"))
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("テストサーバーを起動できませんでした")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


def fetch_orders(page: Page, *, expected_rows: int = 3) -> None:
    page.locator("#startDate").fill("2026-08-06")
    page.locator("#endDate").fill("2026-08-06")
    page.locator("#shippingDate").fill("2026-08-07")
    page.get_by_role("button", name="Shopifyから注文取得").click()
    expect(page.locator("#ordersBody tr")).to_have_count(expected_rows)


def test_mixed_export_preserves_selection_and_history_allows_reexport(
    tmp_path: Path,
) -> None:
    with running_app(tmp_path) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            http_credentials={"username": "operator", "password": "test-secret"},
            timezone_id="Asia/Tokyo",
        )
        page = context.new_page()
        page.goto(base_url)

        assert page.evaluate(
            "localDate(new Date('2026-08-06T00:30:00+09:00'))"
        ) == "2026-08-06"
        fetch_orders(page)

        page.locator("#selectAll").check()
        expect(page.locator("#selectionSummary")).to_contain_text(
            "3件選択（ヤマト 1・佐川 1・未指定 1）"
        )
        page.route("**/api/history?*", lambda route: route.abort())
        with page.expect_download():
            page.locator("#sagawaExport").click()

        expect(page.locator("#notice")).to_contain_text(
            "佐川CSV（1件）を生成し、出力済み一覧へ移動しました。"
        )
        page.unroute("**/api/history?*")

        expect(page.locator("#selectionSummary")).to_contain_text(
            "2件選択（ヤマト 1・佐川 0・未指定 1）"
        )
        expect(page.locator("#yamatoExport")).to_be_enabled()

        with page.expect_response("**/api/history?*"):
            page.locator("#historyTab").click()
        expect(page.locator("#historyCount")).to_have_text("1 / 1件")
        expect(page.locator("#historyBody")).to_contain_text(
            "先にこの注文を含む対象期間の注文を取得"
        )

        page.locator("#pendingTab").click()
        fetch_orders(page, expected_rows=2)
        with page.expect_response("**/api/history?*"):
            page.locator("#historyTab").click()
        reason = page.get_by_label("#BROWSER002の再出力理由")
        reason.fill("取込み失敗のため")
        expect(reason).to_have_value("取込み失敗のため")
        with page.expect_download():
            page.get_by_role("button", name="再出力", exact=True).click()

        expect(page.locator("#historyCount")).to_have_text("2 / 2件")
        expect(page.locator("#historyBody")).to_contain_text("取込み失敗のため")
        expect(page.locator("#historyBody")).to_contain_text(
            "先にこの注文を含む対象期間の注文を取得"
        )
        context.close()
        browser.close()


def test_export_buttons_are_locked_while_request_is_in_flight(tmp_path: Path) -> None:
    with running_app(tmp_path) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            http_credentials={"username": "operator", "password": "test-secret"},
        )
        page = context.new_page()
        page.goto(base_url)
        fetch_orders(page)
        page.locator("#selectYamato").click()

        def delay_export(route: Route) -> None:
            time.sleep(0.3)
            route.continue_()

        page.route("**/api/export/yamato", delay_export)
        page.locator("#yamatoExport").click(no_wait_after=True)
        expect(page.locator("#yamatoExport")).to_be_disabled()
        expect(page.locator("#sagawaExport")).to_be_disabled()
        expect(page.locator("#notice")).to_contain_text("出力済み一覧へ移動", timeout=5_000)
        assert page.evaluate("state.exporting") is False
        context.close()
        browser.close()


def test_unassigned_order_can_be_selected_without_change_reason(tmp_path: Path) -> None:
    with running_app(tmp_path) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            http_credentials={"username": "operator", "password": "test-secret"},
        )
        page = context.new_page()
        page.goto(base_url)
        fetch_orders(page)

        row = page.locator("#ordersBody tr").filter(has_text="#BROWSER003")
        row.locator("label").filter(has_text="ヤマト").click()
        row.locator('input[type="checkbox"]').check()
        expect(page.locator("#overrideReason")).to_have_count(0)

        with page.expect_download():
            page.locator("#yamatoExport").click()
        expect(page.locator("#notice")).to_contain_text(
            "ヤマトCSV（1件）を生成し、出力済み一覧へ移動しました。"
        )
        context.close()
        browser.close()


def test_automatically_assigned_carrier_can_be_changed_without_reason(tmp_path: Path) -> None:
    with running_app(tmp_path) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            accept_downloads=True,
            http_credentials={"username": "operator", "password": "test-secret"},
        )
        page = context.new_page()
        page.goto(base_url)
        fetch_orders(page)

        row = page.locator("#ordersBody tr").filter(has_text="#BROWSER001")
        row.locator("label").filter(has_text="佐川").click()
        row.locator('input[type="checkbox"]').check()

        with page.expect_download():
            page.locator("#sagawaExport").click()
        expect(page.locator("#notice")).to_contain_text(
            "佐川CSV（1件）を生成し、出力済み一覧へ移動しました。"
        )
        context.close()
        browser.close()


def test_history_search_reaches_an_export_older_than_first_page(tmp_path: Path) -> None:
    with running_app(tmp_path, seed_long_history=True) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            http_credentials={"username": "operator", "password": "test-secret"},
        )
        page = context.new_page()
        page.goto(base_url)
        fetch_orders(page, expected_rows=2)

        with page.expect_response("**/api/history?*"):
            page.locator("#historyTab").click()
        expect(page.locator("#historyCount")).to_have_text("100 / 105件")
        expect(page.locator("#historyBody")).not_to_contain_text("#BROWSER002")

        page.get_by_label("出力履歴の注文番号検索").fill("#BROWSER002")
        with page.expect_response("**/api/history?*"):
            page.locator("#historySearchButton").click()
        expect(page.locator("#historyCount")).to_have_text("1 / 1件")
        expect(page.locator("#historyBody")).to_contain_text("#BROWSER002")
        expect(page.get_by_role("button", name="再出力", exact=True)).to_be_enabled()
        context.close()
        browser.close()
