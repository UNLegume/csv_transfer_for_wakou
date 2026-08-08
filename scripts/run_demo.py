"""Run the operator UI with fictional orders and no Shopify writes."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import uvicorn

from wakou_transfer.app import create_app
from wakou_transfer.config import AppConfig
from wakou_transfer.domain import Address, Carrier, LineItem, Money, ShippingOrder
from wakou_transfer.history import ExportHistory


class DemoOrderSource:
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
        del start_date, end_date, financial_status, fulfillment_status, include_cancelled
        return tuple(
            order.model_copy(update={"shipping_date": shipping_date})
            for order in self.orders
        )


def demo_order(
    number: int,
    carrier: Carrier | None,
    item_name: str,
    *,
    quantity: int = 1,
    max_quantity: int | None = None,
) -> ShippingOrder:
    address = Address(
        name="テスト 太郎",
        postal_code="1000001",
        address1="東京都千代田区テスト町1-1",
        address2="デモビル101",
        phone="0312345678",
    )
    return ShippingOrder(
        order_id=f"gid://shopify/Order/demo-{number}",
        order_number=f"#DEMO{number:03d}",
        line_items=(
            LineItem(
                sku=f"DEMO-{number}",
                name=item_name,
                quantity=quantity,
                carrier=carrier,
                yamato_max_quantity=max_quantity,
            ),
        ),
        shipping_address=address,
        billing_address=address,
        total=Money(amount=Decimal("3000"), currency="JPY"),
        shipping_date=date.today(),
    )


def build_app():
    config = AppConfig.model_validate(
        {
            "auth_username": "demo",
            "auth_password": "demo-only",
            "yamato_quantity_limit": 3,
        }
    )
    orders = (
        demo_order(1, Carrier.YAMATO, "デモ小型商品", max_quantity=2),
        demo_order(2, Carrier.SAGAWA, "デモ大型商品"),
        demo_order(3, None, "配送会社未設定のデモ商品"),
    )
    history_path = Path("/tmp/wakou-demo-history.sqlite3")
    history_path.unlink(missing_ok=True)
    return create_app(config, DemoOrderSource(orders), ExportHistory(history_path))


app = build_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
