import pytest

from wakou_transfer.domain import Carrier, LineItem
from wakou_transfer.routing import ReasonCode, RoutingDecision, route_order


def item(carrier: Carrier | None, quantity: int = 1) -> LineItem:
    return LineItem(sku="SKU", name="商品", quantity=quantity, carrier=carrier)


@pytest.mark.parametrize(
    ("items", "limit", "expected", "reason"),
    [
        ([item(Carrier.YAMATO)], 2, Carrier.YAMATO, ReasonCode.YAMATO_ONLY),
        ([item(Carrier.SAGAWA)], 2, Carrier.SAGAWA, ReasonCode.SAGAWA_INCLUDED),
        (
            [item(Carrier.YAMATO), item(Carrier.SAGAWA)],
            3,
            Carrier.SAGAWA,
            ReasonCode.SAGAWA_INCLUDED,
        ),
        ([item(None)], 2, Carrier.NEEDS_REVIEW, ReasonCode.UNCONFIGURED_ITEM),
        ([item(Carrier.NEEDS_REVIEW)], 2, Carrier.NEEDS_REVIEW, ReasonCode.INVALID_ITEM),
        ([item(Carrier.YAMATO, 2)], 2, Carrier.YAMATO, ReasonCode.YAMATO_ONLY),
        ([item(Carrier.YAMATO, 3)], 2, Carrier.SAGAWA, ReasonCode.YAMATO_QUANTITY_EXCEEDED),
    ],
)
def test_routing_table(
    items: list[LineItem], limit: int, expected: Carrier, reason: ReasonCode
) -> None:
    decision = route_order(items, yamato_quantity_limit=limit)
    assert (decision.carrier, decision.reason_code) == (expected, reason)
    assert decision.explanation


def test_routing_is_independent_of_input_order() -> None:
    items = [item(Carrier.YAMATO), item(Carrier.SAGAWA)]
    assert route_order(items, yamato_quantity_limit=5) == route_order(
        list(reversed(items)), yamato_quantity_limit=5
    )


def test_manual_override_requires_and_preserves_reason() -> None:
    automatic = route_order([item(Carrier.YAMATO)], yamato_quantity_limit=2)
    overridden = automatic.override(Carrier.SAGAWA, "梱包サイズ超過")
    assert overridden == RoutingDecision(
        carrier=Carrier.SAGAWA,
        reason_code=ReasonCode.MANUAL_OVERRIDE,
        explanation="手動変更: 梱包サイズ超過",
        override_reason="梱包サイズ超過",
    )
    with pytest.raises(ValueError, match="理由"):
        automatic.override(Carrier.SAGAWA, " ")
