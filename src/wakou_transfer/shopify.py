"""Read-only Shopify Admin GraphQL client."""

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

import httpx

from .domain import Address, Carrier, LineItem, Money, ShippingOrder

DEFAULT_API_VERSION: Final = "2026-07"
METAFIELD_NAMESPACE: Final = "wakou"
CARRIER_METAFIELD_KEY: Final = "carrier"
MAX_QUANTITY_METAFIELD_KEY: Final = "max_quantity"

_STATUS_PATTERN = re.compile(r"^[a-z_]+$")


class ShopifyError(RuntimeError):
    """Base class for Shopify communication failures."""


class ShopifyHTTPError(ShopifyError):
    """The Shopify endpoint returned a non-success HTTP response."""


class ShopifyGraphQLError(ShopifyError):
    """Shopify returned GraphQL, user, or malformed-data errors."""


@dataclass(frozen=True)
class ResolvedDeliveryMetafields:
    carrier: Carrier | None
    max_quantity: int | None


def _normalise_status(value: str, field: str) -> str:
    normalised = value.strip().lower()
    if not _STATUS_PATTERN.fullmatch(normalised):
        raise ValueError(f"Invalid Shopify {field}: {value!r}")
    return normalised


def build_order_search_query(
    start_date: date,
    end_date: date,
    *,
    financial_status: str = "paid",
    fulfillment_status: str | None = None,
    include_cancelled: bool = False,
) -> str:
    """Build Shopify's order-search expression without accepting query syntax."""
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")
    parts = [f"created_at:>={start_date.isoformat()}", f"created_at:<={end_date.isoformat()}"]
    parts.append(f"financial_status:{_normalise_status(financial_status, 'financial status')}")
    if fulfillment_status is not None:
        parts.append(
            f"fulfillment_status:{_normalise_status(fulfillment_status, 'fulfillment status')}"
        )
    if not include_cancelled:
        parts.append("status:not_cancelled")
    return " ".join(parts)


def _parse_carrier(value: str | None) -> Carrier | None:
    if value is None or not value.strip():
        return None
    try:
        return Carrier(value.strip().lower())
    except ValueError:
        return Carrier.NEEDS_REVIEW


def _parse_max_quantity(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        quantity = int(value)
    except ValueError:
        return None
    return quantity if quantity > 0 else None


def resolve_delivery_metafields(
    *,
    variant_carrier: str | None,
    product_carrier: str | None,
    variant_max_quantity: str | None = None,
    product_max_quantity: str | None = None,
) -> ResolvedDeliveryMetafields:
    """Resolve variant values before product values; never infer a carrier."""
    carrier_value = variant_carrier if variant_carrier is not None else product_carrier
    quantity_value = (
        variant_max_quantity if variant_max_quantity is not None else product_max_quantity
    )
    return ResolvedDeliveryMetafields(
        carrier=_parse_carrier(carrier_value),
        max_quantity=_parse_max_quantity(quantity_value),
    )


_LINE_ITEM_FIELDS = f"""
  nodes {{
    id name quantity sku
    variant {{
      carrier: metafield(
        namespace: \"{METAFIELD_NAMESPACE}\", key: \"{CARRIER_METAFIELD_KEY}\"
      ) {{ value }}
      maxQuantity: metafield(
        namespace: \"{METAFIELD_NAMESPACE}\", key: \"{MAX_QUANTITY_METAFIELD_KEY}\"
      ) {{ value }}
      product {{
        carrier: metafield(
          namespace: \"{METAFIELD_NAMESPACE}\", key: \"{CARRIER_METAFIELD_KEY}\"
        ) {{ value }}
        maxQuantity: metafield(
          namespace: \"{METAFIELD_NAMESPACE}\", key: \"{MAX_QUANTITY_METAFIELD_KEY}\"
        ) {{ value }}
      }}
    }}
  }}
  pageInfo {{ hasNextPage endCursor }}
"""

_ORDERS_QUERY = f"""
query Orders($query: String!, $after: String) {{
  orders(first: 100, after: $after, query: $query, sortKey: CREATED_AT) {{
    nodes {{
      id name createdAt cancelledAt displayFinancialStatus displayFulfillmentStatus
      shippingAddress {{ name zip address1 address2 phone }}
      billingAddress {{ name zip address1 address2 phone }}
      currentTotalPriceSet {{ shopMoney {{ amount currencyCode }} }}
      lineItems(first: 100) {{ {_LINE_ITEM_FIELDS} }}
    }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_MORE_LINE_ITEMS_QUERY = f"""
query OrderLineItems($id: ID!, $after: String!) {{
  order(id: $id) {{
    lineItems(first: 100, after: $after) {{ {_LINE_ITEM_FIELDS} }}
  }}
}}
"""


class ShopifyClient:
    """Minimal asynchronous, read-only Admin GraphQL client."""

    def __init__(
        self,
        store_domain: str,
        access_token: str,
        *,
        api_version: str = DEFAULT_API_VERSION,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        domain = store_domain.strip().removeprefix("https://").rstrip("/")
        if not domain or "/" in domain:
            raise ValueError("store_domain must be a Shopify host name")
        if not access_token.strip():
            raise ValueError("access_token must not be empty")
        if not re.fullmatch(r"\d{4}-\d{2}", api_version):
            raise ValueError("api_version must use YYYY-MM format")
        self._url = f"https://{domain}/admin/api/{api_version}/graphql.json"
        self._access_token = access_token
        self._transport = transport
        self._timeout = timeout

    @classmethod
    def from_env(
        cls,
        *,
        api_version: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "ShopifyClient":
        """Create a client from the documented Shopify environment variables."""
        try:
            domain = os.environ["SHOPIFY_STORE_DOMAIN"]
            token = os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"]
        except KeyError as exc:
            raise ValueError(f"Missing environment variable: {exc.args[0]}") from exc
        version = api_version or os.getenv("SHOPIFY_API_VERSION", DEFAULT_API_VERSION)
        if version is None:  # pragma: no cover - os.getenv default makes this defensive only
            version = DEFAULT_API_VERSION
        return cls(domain, token, api_version=version, transport=transport)

    async def fetch_orders(
        self,
        start_date: date,
        end_date: date,
        *,
        financial_status: str = "paid",
        fulfillment_status: str | None = None,
        include_cancelled: bool = False,
    ) -> tuple[ShippingOrder, ...]:
        search_query = build_order_search_query(
            start_date,
            end_date,
            financial_status=financial_status,
            fulfillment_status=fulfillment_status,
            include_cancelled=include_cancelled,
        )
        headers = {
            "X-Shopify-Access-Token": self._access_token,
            "Content-Type": "application/json",
        }
        orders: list[ShippingOrder] = []
        cursor: str | None = None
        async with httpx.AsyncClient(
            headers=headers, transport=self._transport, timeout=self._timeout
        ) as http:
            while True:
                data = await self._execute(
                    http, _ORDERS_QUERY, {"query": search_query, "after": cursor}
                )
                connection = _mapping(data.get("orders"), "data.orders")
                nodes = _sequence(connection.get("nodes"), "data.orders.nodes")
                for raw_order in nodes:
                    order = _mapping(raw_order, "order")
                    items_connection = _mapping(order.get("lineItems"), "order.lineItems")
                    raw_items = list(_sequence(items_connection.get("nodes"), "lineItems.nodes"))
                    page_info = _mapping(items_connection.get("pageInfo"), "lineItems.pageInfo")
                    line_cursor = _cursor(page_info)
                    while bool(page_info.get("hasNextPage")):
                        if line_cursor is None:
                            raise ShopifyGraphQLError("lineItems page has no endCursor")
                        line_data = await self._execute(
                            http,
                            _MORE_LINE_ITEMS_QUERY,
                            {"id": _string(order.get("id"), "order.id"), "after": line_cursor},
                        )
                        line_order = _mapping(line_data.get("order"), "data.order")
                        next_connection = _mapping(
                            line_order.get("lineItems"), "data.order.lineItems"
                        )
                        raw_items.extend(
                            _sequence(next_connection.get("nodes"), "lineItems.nodes")
                        )
                        page_info = _mapping(
                            next_connection.get("pageInfo"), "lineItems.pageInfo"
                        )
                        line_cursor = _cursor(page_info)
                    orders.append(_to_shipping_order(order, raw_items))
                page_info = _mapping(connection.get("pageInfo"), "orders.pageInfo")
                if not bool(page_info.get("hasNextPage")):
                    break
                cursor = _cursor(page_info)
                if cursor is None:
                    raise ShopifyGraphQLError("orders page has no endCursor")
        return tuple(orders)

    async def _execute(
        self, http: httpx.AsyncClient, query: str, variables: Mapping[str, object]
    ) -> Mapping[str, Any]:
        try:
            response = await http.post(self._url, json={"query": query, "variables": variables})
        except httpx.HTTPError as exc:
            raise ShopifyHTTPError(f"Shopify request failed: {exc}") from exc
        if not response.is_success:
            retry = response.headers.get("Retry-After")
            detail = f" (retry after {retry}s)" if retry else ""
            raise ShopifyHTTPError(f"Shopify HTTP {response.status_code}{detail}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ShopifyGraphQLError("Shopify returned invalid JSON") from exc
        root = _mapping(payload, "response")
        messages = _error_messages(root)
        if messages:
            raise ShopifyGraphQLError("; ".join(messages))
        return _mapping(root.get("data"), "response.data")


def _error_messages(value: object) -> list[str]:
    messages: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key in {"errors", "userErrors"} and isinstance(child, Sequence):
                for error in child:
                    if isinstance(error, Mapping):
                        message = error.get("message")
                        messages.append(str(message) if message is not None else str(error))
            else:
                messages.extend(_error_messages(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for child in value:
            messages.extend(_error_messages(child))
    return messages


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ShopifyGraphQLError(f"Missing or invalid {path}")
    return value


def _sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ShopifyGraphQLError(f"Missing or invalid {path}")
    return value


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ShopifyGraphQLError(f"Missing or invalid {path}")
    return value


def _integer(value: object, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ShopifyGraphQLError(f"Missing or invalid {path}")
    return value


def _optional_metafield(parent: Mapping[str, Any], key: str) -> str | None:
    value = parent.get(key)
    if value is None:
        return None
    metafield = _mapping(value, key)
    raw = metafield.get("value")
    return raw if isinstance(raw, str) else None


def _cursor(page_info: Mapping[str, Any]) -> str | None:
    value = page_info.get("endCursor")
    return value if isinstance(value, str) and value else None


def _to_address(value: object, path: str) -> Address:
    raw = _mapping(value, path)
    return Address(
        name=_string(raw.get("name"), f"{path}.name"),
        postal_code=_string(raw.get("zip"), f"{path}.zip"),
        address1=_string(raw.get("address1"), f"{path}.address1"),
        address2=str(raw.get("address2") or ""),
        phone=_string(raw.get("phone"), f"{path}.phone"),
    )


def _to_line_item(value: object) -> LineItem:
    raw = _mapping(value, "lineItem")
    variant_value = raw.get("variant")
    variant = _mapping(variant_value, "lineItem.variant") if variant_value is not None else {}
    product_value = variant.get("product")
    product = _mapping(product_value, "lineItem.variant.product") if product_value else {}
    resolved = resolve_delivery_metafields(
        variant_carrier=_optional_metafield(variant, "carrier"),
        product_carrier=_optional_metafield(product, "carrier"),
        variant_max_quantity=_optional_metafield(variant, "maxQuantity"),
        product_max_quantity=_optional_metafield(product, "maxQuantity"),
    )
    sku = raw.get("sku")
    return LineItem(
        sku=sku if isinstance(sku, str) else "",
        name=_string(raw.get("name"), "lineItem.name"),
        quantity=_integer(raw.get("quantity"), "lineItem.quantity"),
        carrier=resolved.carrier,
    )


def _to_shipping_order(raw: Mapping[str, Any], raw_items: Sequence[object]) -> ShippingOrder:
    price_set = _mapping(raw.get("currentTotalPriceSet"), "order.currentTotalPriceSet")
    money = _mapping(price_set.get("shopMoney"), "order.currentTotalPriceSet.shopMoney")
    created_at = _string(raw.get("createdAt"), "order.createdAt")
    try:
        shipping_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
        amount = Decimal(_string(money.get("amount"), "order.total.amount"))
    except (ValueError, ArithmeticError) as exc:
        raise ShopifyGraphQLError("Invalid order date or total amount") from exc
    billing = raw.get("billingAddress")
    return ShippingOrder(
        order_id=_string(raw.get("id"), "order.id"),
        order_number=_string(raw.get("name"), "order.name"),
        line_items=tuple(_to_line_item(item) for item in raw_items),
        shipping_address=_to_address(raw.get("shippingAddress"), "order.shippingAddress"),
        billing_address=(
            _to_address(billing, "order.billingAddress") if billing is not None else None
        ),
        total=Money(
            amount=amount,
            currency=_string(money.get("currencyCode"), "order.total.currencyCode"),
        ),
        shipping_date=shipping_date,
    )
