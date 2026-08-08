"""ワコウ指定形式の送り状CSVを生成する。"""

import csv
import io
from collections.abc import Callable, Iterable, Sequence

from .config import AppConfig
from .domain import Address, ShippingOrder
from .validation import normalize_phone, normalize_postal_code

SAGAWA_HEADERS = (
    "伝票no",
    "受注no",
    "合計金額",
    "合計金額（単位）",
    "shitei_bi_a",
    "shitei_bi_b",
    "shitei_bi_c",
    "時間指定",
    "便種",
    "シールコード１（佐川専用）",
    "シールコード２（佐川専用）",
    "シール３区分コード",
    "シールコード４（佐川専用）",
    "営業所止め",
    "営業所名",
    "送り状区分（ヤマト専用）",
    "受注名",
    "受注郵便番号",
    "受注者住所",
    "受注者電話番号",
    "お届け先名",
    "お届け先郵便番号",
    "お届け先住所",
    "お届け先電話",
    "option1",
    "option2",
    "option3",
    "option4",
    "option5",
    "option6",
    "C：出荷元企業名",
    "C：出荷元郵便番号",
    "C：出荷元住所",
    "C:出荷元電話番号",
    "CM：ご依頼主電話番号",
    "CM：ご依頼主郵便番号",
    "CM：ご依頼主住所１",
    "CM：ご依頼主名称１",
    "取扱商品",
    "郵便種別",
    "元／着払い種別",
    "代引き種別",
    "発送備考欄",
    "品名１",
    "○○様ご依頼品",
    "品名２",
    "品名３",
    "品名４",
    "品名５",
    "請求コード",
)

YAMATO_HEADERS = (
    "お客様管理番号",
    "送り状種別",
    "温度区分",
    "予備4",
    "出荷予定日",
    "配達指定日",
    "時間指定コード",
    "届け先コード",
    "届け先電話番号",
    "届け先電話番号（枝番）",
    "届け先郵便番号",
    "届け先住所",
    "届け先建物名（アパートマンション名）",
    "会社・部門名１",
    "会社・部門名２",
    "届け先名（漢字）",
    "届け先名（カナ）",
    "敬称",
    "依頼主コード",
    "CM：ご依頼主電話番号",
    "依頼主電話番号（枝番）",
    "CM：ご依頼主郵便番号",
    "CM：ご依頼主住所",
    "CM：ご依頼主住所２",
    "CM：ご依頼主名称１",
    "依頼主名（カナ）",
    "品名コード１",
    "C：品名 購入品",
    "品名コード２",
    "空を出力",
    "荷扱い１",
    "荷扱い２",
    "○○様ご依頼品(ネコポス用)",
    "コレクト代金引換額（税込）",
    "コレクト内消費税額",
    "営業所止置き",
    "止め置き営業所コード",
    "ネコポス発行枚数",
    "ネコポス個数口枠の印字",
    "請求先分類コード",
    "運賃管理番号",
    "空を出力2",
)


def _order_number(order: ShippingOrder) -> str:
    return order.order_number.replace("#", "")


def _joined_address(address: Address) -> str:
    return f"{address.address1}{address.address2}"


def _sagawa_row(order: ShippingOrder, config: AppConfig) -> list[str]:
    shipping = order.shipping_address
    billing = order.billing_address or shipping
    shipping_day = f"{order.shipping_date.month}月{order.shipping_date.day}日"
    shipping_compact = order.shipping_date.strftime("%Y%m%d")
    sagawa = config.sagawa
    delivery_day = shipping_day if sagawa.include_delivery_date else ""
    delivery_compact = shipping_compact if sagawa.include_delivery_date else ""
    return [
        _order_number(order),
        "",
        sagawa.amount,
        sagawa.amount_unit,
        delivery_day,
        delivery_day,
        delivery_compact,
        "",
        sagawa.service_type,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        billing.name,
        normalize_postal_code(billing.postal_code),
        _joined_address(billing),
        normalize_phone(billing.phone),
        shipping.name,
        normalize_postal_code(shipping.postal_code),
        _joined_address(shipping),
        normalize_phone(shipping.phone),
        sagawa.option1,
        sagawa.option2,
        sagawa.option3,
        sagawa.option4,
        sagawa.option5,
        sagawa.option6,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        sagawa.postal_type,
        sagawa.payment_type,
        sagawa.cod_type,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]


def _yamato_row(order: ShippingOrder, config: AppConfig) -> list[str]:
    shipping = order.shipping_address
    yamato = config.yamato
    shipping_date = order.shipping_date
    return [
        _order_number(order),
        yamato.label_type,
        "",
        "",
        f"{shipping_date.year}/{shipping_date.month}/{shipping_date.day}",
        "",
        "",
        "",
        normalize_phone(shipping.phone),
        "",
        normalize_postal_code(shipping.postal_code),
        shipping.address1,
        shipping.address2,
        "",
        "",
        shipping.name,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        yamato.item_name,
        "",
        "",
        "",
        "",
        "",
        yamato.collect_amount,
        yamato.collect_tax,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]


def _export_csv(
    orders: Iterable[ShippingOrder],
    config: AppConfig,
    headers: Sequence[str],
    row_factory: Callable[[ShippingOrder, AppConfig], list[str]],
) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=",", lineterminator="\r\n")
    writer.writerow(headers)
    for order in orders:
        row = (
            value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
            for value in row_factory(order, config)
        )
        writer.writerow(row)
    return output.getvalue().encode("cp932")


def export_sagawa_csv(orders: Iterable[ShippingOrder], config: AppConfig) -> bytes:
    """注文ごとに1行の佐川送り状CSVをCP932で返す。"""
    return _export_csv(orders, config, SAGAWA_HEADERS, _sagawa_row)


def export_yamato_csv(orders: Iterable[ShippingOrder], config: AppConfig) -> bytes:
    """注文ごとに1行のヤマト・ネコポス送り状CSVをCP932で返す。"""
    return _export_csv(orders, config, YAMATO_HEADERS, _yamato_row)
