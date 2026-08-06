"""ShopifyやCSVの形式に依存しない配送ドメインモデル。"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)


class Carrier(StrEnum):
    YAMATO = "yamato"
    SAGAWA = "sagawa"
    NEEDS_REVIEW = "needs_review"


class Address(DomainModel):
    name: str
    postal_code: str
    address1: str
    address2: str = ""
    phone: str


class LineItem(DomainModel):
    sku: str
    name: str
    quantity: int = Field(gt=0)
    carrier: Carrier | None = None


class Money(DomainModel):
    amount: Decimal = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class ShippingOrder(DomainModel):
    order_id: str
    order_number: str
    line_items: tuple[LineItem, ...] = Field(min_length=1)
    shipping_address: Address
    billing_address: Address | None
    total: Money
    shipping_date: date

