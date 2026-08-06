"""送り状CSV生成用FastAPIアプリケーション。"""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from secrets import compare_digest
from time import monotonic
from typing import Annotated, Protocol
from urllib.parse import quote
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from .config import AppConfig, CarrierFieldLimits
from .csv_export import export_sagawa_csv, export_yamato_csv
from .domain import Carrier, ShippingOrder
from .history import ExportHistory
from .routing import RoutingDecision, route_order
from .shopify import ShopifyClient
from .validation import Severity, ValidationIssue, validate_address


class OrderSource(Protocol):
    async def fetch_orders(
        self,
        start_date: date,
        end_date: date,
        *,
        shipping_date: date,
        financial_status: str = "paid",
        fulfillment_status: str | None = None,
        include_cancelled: bool = False,
    ) -> tuple[ShippingOrder, ...]: ...


class PreviewRequest(BaseModel):
    start_date: date
    end_date: date
    shipping_date: date


class ExportRequest(BaseModel):
    preview_id: str
    order_ids: list[str] = Field(min_length=1)
    overrides: dict[str, Carrier] = Field(default_factory=dict)
    override_reasons: dict[str, str] = Field(default_factory=dict)
    reexport_reason: str | None = None


class PreviewStore:
    def __init__(
        self,
        *,
        max_batches: int = 20,
        ttl_seconds: float = 900,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._batches: dict[str, tuple[float, tuple[ShippingOrder, ...]]] = {}
        self._max_batches = max_batches
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def _purge_expired(self) -> None:
        now = self._clock()
        expired = [
            preview_id
            for preview_id, (created_at, _) in self._batches.items()
            if now - created_at >= self._ttl_seconds
        ]
        for preview_id in expired:
            self._batches.pop(preview_id, None)

    def put(self, orders: tuple[ShippingOrder, ...]) -> str:
        self._purge_expired()
        while len(self._batches) >= self._max_batches:
            oldest = next(iter(self._batches))
            self._batches.pop(oldest)
        preview_id = uuid4().hex
        self._batches[preview_id] = (self._clock(), orders)
        return preview_id

    def get(self, preview_id: str) -> tuple[ShippingOrder, ...] | None:
        self._purge_expired()
        batch = self._batches.get(preview_id)
        return batch[1] if batch else None

    def remove_orders(self, preview_id: str, order_ids: set[str]) -> None:
        batch = self._batches.get(preview_id)
        if batch is None:
            return
        remaining = tuple(order for order in batch[1] if order.order_id not in order_ids)
        if remaining:
            self._batches[preview_id] = (batch[0], remaining)
        else:
            self._batches.pop(preview_id, None)


def _limits(config: AppConfig, carrier: Carrier) -> CarrierFieldLimits:
    return config.limits.yamato if carrier is Carrier.YAMATO else config.limits.sagawa


def _decision(order: ShippingOrder, config: AppConfig) -> RoutingDecision:
    return route_order(order.line_items, yamato_quantity_limit=config.yamato_quantity_limit)


def _preview_order(
    order: ShippingOrder,
    config: AppConfig,
    already_exported: set[str],
) -> dict[str, object]:
    decision = _decision(order, config)
    target = decision.carrier
    issues: tuple[ValidationIssue, ...] = ()
    if target is not Carrier.NEEDS_REVIEW:
        issues = validate_address(
            order.order_number,
            order.shipping_address,
            target,
            _limits(config, target),
        )
    return {
        "order_id": order.order_id,
        "order_number": order.order_number,
        "recipient_name": order.shipping_address.name,
        "postal_code": order.shipping_address.postal_code,
        "address": f"{order.shipping_address.address1}{order.shipping_address.address2}",
        "phone": order.shipping_address.phone,
        "items": [
            {
                "sku": item.sku,
                "name": item.name,
                "quantity": item.quantity,
                "carrier": item.carrier.value if item.carrier else None,
            }
            for item in order.line_items
        ],
        "suggested_carrier": target.value,
        "reason": decision.explanation,
        "errors": [
            {"field": issue.field, "code": issue.code, "reason": issue.reason}
            for issue in issues
            if issue.severity is Severity.ERROR
        ],
        "already_exported": order.order_id in already_exported,
    }


def create_app(
    config: AppConfig | None = None,
    shopify_client: OrderSource | None = None,
    history: ExportHistory | None = None,
) -> FastAPI:
    resolved_config = config or AppConfig()  # type: ignore[call-arg]
    source = shopify_client or ShopifyClient.from_env()
    export_history = history or ExportHistory(Path("data/export-history.sqlite3"))
    previews = PreviewStore()
    app = FastAPI(title="ワコウ送り状CSV変換", version="0.1.0")
    basic = HTTPBasic()

    def require_auth(
        credentials: Annotated[HTTPBasicCredentials, Depends(basic)],
    ) -> str:
        valid_user = compare_digest(credentials.username, resolved_config.auth_username)
        valid_password = compare_digest(
            credentials.password,
            resolved_config.auth_password.get_secret_value(),
        )
        if not (valid_user and valid_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="認証に失敗しました",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials.username

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
    async def index() -> HTMLResponse:
        template = Path(__file__).with_name("templates").joinpath("index.html")
        return HTMLResponse(template.read_text(encoding="utf-8"))

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/history", dependencies=[Depends(require_auth)])
    async def history_list() -> dict[str, object]:
        return {
            "records": [
                {
                    "batch_id": record.batch_id,
                    "order_number": record.order_number,
                    "carrier": record.carrier.value,
                    "exported_at": record.exported_at,
                    "reexport_reason": record.reexport_reason,
                }
                for record in export_history.list_recent()
            ]
        }

    @app.post("/api/preview", dependencies=[Depends(require_auth)])
    async def preview(request: PreviewRequest) -> dict[str, object]:
        if request.start_date > request.end_date:
            raise HTTPException(422, "開始日は終了日以前にしてください")
        try:
            orders = await source.fetch_orders(
                request.start_date,
                request.end_date,
                shipping_date=request.shipping_date,
                financial_status="paid",
                fulfillment_status="unfulfilled",
                include_cancelled=False,
            )
        except Exception as exc:
            raise HTTPException(502, "Shopifyから注文を取得できませんでした") from exc
        preview_id = previews.put(orders)
        exported = export_history.exported_order_ids([order.order_id for order in orders])
        return {
            "preview_id": preview_id,
            "orders": [_preview_order(order, resolved_config, exported) for order in orders],
        }

    @app.post("/api/export/{carrier}", dependencies=[Depends(require_auth)])
    async def export(carrier: Carrier, request: ExportRequest) -> Response:
        if carrier is Carrier.NEEDS_REVIEW:
            raise HTTPException(422, "出力先はヤマトまたは佐川を指定してください")
        batch = previews.get(request.preview_id)
        if batch is None:
            raise HTTPException(404, "プレビューが見つかりません。注文を再取得してください")
        requested = set(request.order_ids)
        selected = [order for order in batch if order.order_id in requested]
        if len(selected) != len(requested):
            raise HTTPException(422, "プレビューに存在しない注文が含まれています")

        output_orders: list[ShippingOrder] = []
        for order in selected:
            decision = _decision(order, resolved_config)
            override = request.overrides.get(order.order_id)
            if override is not None:
                override_reason = request.override_reasons.get(order.order_id, "")
                try:
                    decision = decision.override(override, override_reason)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            if decision.carrier is Carrier.NEEDS_REVIEW:
                raise HTTPException(422, f"{order.order_number}: 配送会社の確認が必要です")
            if decision.carrier is not carrier:
                raise HTTPException(422, f"{order.order_number}: 選択した配送会社と一致しません")
            issues = validate_address(
                order.order_number,
                order.shipping_address,
                carrier,
                _limits(resolved_config, carrier),
            )
            errors = [issue for issue in issues if issue.severity is Severity.ERROR]
            if errors:
                raise HTTPException(422, f"{order.order_number}: {errors[0].reason}")
            output_orders.append(order)

        already = export_history.exported_order_ids([order.order_id for order in output_orders])
        reexport_reason = request.reexport_reason.strip() if request.reexport_reason else None
        if already and not reexport_reason:
            raise HTTPException(409, "出力済み注文です。再出力理由を入力してください")

        if carrier is Carrier.YAMATO:
            body = export_yamato_csv(output_orders, resolved_config)
            label = "ヤマト"
        else:
            body = export_sagawa_csv(output_orders, resolved_config)
            label = "佐川"
        batch_id = uuid4().hex
        try:
            export_history.record_batch(
                batch_id,
                [
                    (order.order_id, order.order_number, carrier)
                    for order in output_orders
                ],
                reexport_reason=reexport_reason,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        previews.remove_orders(request.preview_id, requested)
        filename = f"送り状_{label}_{output_orders[0].shipping_date:%Y%m%d}.csv"
        return Response(
            body,
            media_type="text/csv; charset=shift_jis",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    return app
