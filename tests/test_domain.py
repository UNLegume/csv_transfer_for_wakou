from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from wakou_transfer.domain import Address, Carrier, LineItem, Money, ShippingOrder


def test_shipping_order_preserves_identifiers_as_strings() -> None:
    order = ShippingOrder(
        order_id="gid://shopify/Order/1",
        order_number="0000123",
        line_items=(LineItem(sku="SKU-1", name="商品", quantity=1, carrier=Carrier.YAMATO),),
        shipping_address=Address(
            name="山田 太郎", postal_code="001-0001", address1="札幌市", phone="090-0000-0000"
        ),
        billing_address=None,
        total=Money(amount=Decimal("1200.50"), currency="JPY"),
        shipping_date=date(2026, 8, 7),
    )

    assert order.order_number == "0000123"
    assert order.shipping_address.postal_code == "001-0001"
    assert order.shipping_address.phone == "090-0000-0000"


@pytest.mark.parametrize("quantity", [0, -1])
def test_line_item_rejects_non_positive_quantity(quantity: int) -> None:
    with pytest.raises(ValidationError):
        LineItem(sku="SKU", name="商品", quantity=quantity)


def test_money_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        Money(amount=Decimal("-0.01"), currency="JPY")

