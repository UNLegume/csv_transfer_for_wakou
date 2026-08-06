import csv
import io
from datetime import date
from decimal import Decimal

from wakou_transfer.config import AppConfig
from wakou_transfer.csv_export import export_sagawa_csv, export_yamato_csv
from wakou_transfer.domain import Address, Carrier, LineItem, Money, ShippingOrder

SAGAWA_SAMPLE_HEADERS = (
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

YAMATO_SAMPLE_HEADERS = (
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


def config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "sender": {
                "company_name": "発送元株式会社",
                "requester_name": "通販部",
                "postal_code": "0010001",
                "address": "北海道札幌市中央区1-2",
                "phone": "0110000000",
            },
            "sagawa": {"billing_code": "000123456789"},
            "yamato": {"requester_code": "000987654321"},
        }
    )


def order(number: str = "#0000123") -> ShippingOrder:
    return ShippingOrder(
        order_id="gid://shopify/Order/123",
        order_number=number,
        line_items=(
            LineItem(sku="SKU-1", name="商品A", quantity=1, carrier=Carrier.SAGAWA),
            LineItem(sku="SKU-2", name="商品B", quantity=2, carrier=Carrier.YAMATO),
        ),
        shipping_address=Address(
            name='届け先, "様"',
            postal_code="002-0002",
            address1="北海道札幌市北区3-4",
            address2="北館\n101号",
            phone="090-0000-0000",
        ),
        billing_address=Address(
            name="請求先",
            postal_code="003-0003",
            address1="北海道札幌市東区5-6",
            address2="",
            phone="080-0000-0000",
        ),
        total=Money(amount=Decimal("12345678901234567890.50"), currency="JPY"),
        shipping_date=date(2026, 8, 7),
    )


def decoded_rows(payload: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(payload.decode("cp932"), newline="")))


def assert_crlf_only(payload: bytes) -> None:
    assert b"\r\n" in payload
    assert payload.replace(b"\r\n", b"").find(b"\n") == -1


def test_sagawa_matches_sample_headers_and_maps_one_row_per_order() -> None:
    payload = export_sagawa_csv([order(), order("#0000456")], config())
    rows = decoded_rows(payload)

    assert tuple(rows[0]) == SAGAWA_SAMPLE_HEADERS
    assert len(rows[0]) == 50
    assert len(rows) == 3
    assert all(len(row) == 50 for row in rows)
    assert_crlf_only(payload)

    row = dict(zip(rows[0], rows[1], strict=True))
    assert row["伝票no"] == "0000123"
    assert row["受注名"] == "請求先"
    assert row["受注郵便番号"] == "0030003"
    assert row["お届け先名"] == '届け先, "様"'
    assert row["お届け先住所"] == "北海道札幌市北区3-4北館\r\n101号"
    assert row["請求コード"] == "000123456789"
    assert (row["郵便種別"], row["元／着払い種別"], row["代引き種別"]) == ("0", "1", "Yes")


def test_sagawa_uses_shipping_address_when_billing_address_is_missing() -> None:
    without_billing = order().model_copy(update={"billing_address": None})
    rows = decoded_rows(export_sagawa_csv([without_billing], config()))
    row = dict(zip(rows[0], rows[1], strict=True))

    assert row["受注名"] == row["お届け先名"]
    assert row["受注郵便番号"] == row["お届け先郵便番号"]
    assert row["受注者電話番号"] == row["お届け先電話"]


def test_yamato_matches_sample_headers_and_maps_neko_pos_fields() -> None:
    payload = export_yamato_csv([order(), order("#0000456")], config())
    rows = decoded_rows(payload)

    assert tuple(rows[0]) == YAMATO_SAMPLE_HEADERS
    assert len(rows[0]) == 42
    assert len(rows) == 3
    assert all(len(row) == 42 for row in rows)
    assert_crlf_only(payload)

    row = dict(zip(rows[0], rows[1], strict=True))
    assert row["お客様管理番号"] == "0000123"
    assert row["出荷予定日"] == "2026/8/7"
    assert row["届け先電話番号"] == "09000000000"
    assert row["届け先郵便番号"] == "0020002"
    assert row["届け先住所"] == "北海道札幌市北区3-4"
    assert row["届け先建物名（アパートマンション名）"] == "北館\r\n101号"
    assert row["依頼主コード"] == "000987654321"
    assert row["C：品名 購入品"] == "ネットショップ購入品"
    assert (row["コレクト代金引換額（税込）"], row["コレクト内消費税額"]) == ("0", "0")


def test_exports_are_cp932_round_trippable_and_quote_csv_metacharacters() -> None:
    for exporter in (export_sagawa_csv, export_yamato_csv):
        payload = exporter([order()], config())
        decoded = payload.decode("cp932")
        assert decoded.encode("cp932") == payload
        assert '"届け先, ""様"""' in decoded
        assert len(decoded_rows(payload)) == 2
