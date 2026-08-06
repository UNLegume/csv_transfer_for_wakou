"""注文内の全明細を使う、順序に依存しない配送会社判定。"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from .domain import Carrier, LineItem


class ReasonCode(StrEnum):
    YAMATO_ONLY = "yamato_only"
    SAGAWA_INCLUDED = "sagawa_included"
    UNCONFIGURED_ITEM = "unconfigured_item"
    INVALID_ITEM = "invalid_item"
    YAMATO_QUANTITY_EXCEEDED = "yamato_quantity_exceeded"
    MANUAL_ASSIGNMENT = "manual_assignment"
    MANUAL_OVERRIDE = "manual_override"


@dataclass(frozen=True)
class RoutingDecision:
    carrier: Carrier
    reason_code: ReasonCode
    explanation: str
    override_reason: str | None = None

    def override(self, carrier: Carrier, reason: str) -> "RoutingDecision":
        if carrier is Carrier.NEEDS_REVIEW:
            raise ValueError("手動変更先はヤマトまたは佐川を指定してください")
        if carrier is self.carrier:
            return self
        reason = reason.strip()
        if self.carrier is Carrier.NEEDS_REVIEW:
            return RoutingDecision(
                carrier=carrier,
                reason_code=ReasonCode.MANUAL_ASSIGNMENT,
                explanation="手動指定",
            )
        if not reason:
            raise ValueError("手動変更には理由が必要です")
        return RoutingDecision(
            carrier=carrier,
            reason_code=ReasonCode.MANUAL_OVERRIDE,
            explanation=f"手動変更: {reason}",
            override_reason=reason,
        )


def route_order(
    line_items: Iterable[LineItem], *, yamato_quantity_limit: int | None
) -> RoutingDecision:
    items = tuple(line_items)
    if not items or any(item.carrier is None for item in items):
        return RoutingDecision(
            Carrier.NEEDS_REVIEW, ReasonCode.UNCONFIGURED_ITEM, "配送会社が未設定の商品があります"
        )
    if any(item.carrier is Carrier.NEEDS_REVIEW for item in items):
        return RoutingDecision(
            Carrier.NEEDS_REVIEW, ReasonCode.INVALID_ITEM, "配送会社の設定が不正な商品があります"
        )
    if any(item.carrier is Carrier.SAGAWA for item in items):
        return RoutingDecision(
            Carrier.SAGAWA, ReasonCode.SAGAWA_INCLUDED, "佐川指定の商品が含まれています"
        )

    exceeded_line = next(
        (
            item
            for item in items
            if item.yamato_max_quantity is not None
            and item.quantity > item.yamato_max_quantity
        ),
        None,
    )
    if exceeded_line is not None:
        return RoutingDecision(
            Carrier.SAGAWA,
            ReasonCode.YAMATO_QUANTITY_EXCEEDED,
            (
                f"{exceeded_line.name}の数量{exceeded_line.quantity}が"
                f"ヤマト上限{exceeded_line.yamato_max_quantity}を超えています"
            ),
        )

    quantity = sum(item.quantity for item in items)
    if yamato_quantity_limit is not None and quantity > yamato_quantity_limit:
        return RoutingDecision(
            Carrier.SAGAWA,
            ReasonCode.YAMATO_QUANTITY_EXCEEDED,
            f"ヤマト商品の合計数量{quantity}が上限{yamato_quantity_limit}を超えています",
        )
    return RoutingDecision(Carrier.YAMATO, ReasonCode.YAMATO_ONLY, "全商品がヤマト指定です")

