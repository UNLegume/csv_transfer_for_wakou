import sqlite3
from pathlib import Path

from wakou_transfer.domain import Carrier
from wakou_transfer.history import ExportHistory


def test_records_only_non_personal_export_metadata(tmp_path: Path) -> None:
    history = ExportHistory(tmp_path / "history.sqlite3")

    history.record(
        batch_id="batch-1",
        order_id="gid://shopify/Order/1",
        order_number="#1673",
        carrier=Carrier.YAMATO,
    )
    assert history.list_recent()[0].order_id == "gid://shopify/Order/1"

    assert history.exported_order_ids(["gid://shopify/Order/1", "missing"]) == {
        "gid://shopify/Order/1"
    }
    with sqlite3.connect(tmp_path / "history.sqlite3") as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(exports)")}
    assert columns == {
        "id",
        "batch_id",
        "order_id",
        "order_number",
        "carrier",
        "exported_at",
        "reexport_reason",
    }
    assert not {"name", "address", "phone"} & columns


def test_reexport_reason_is_required_for_an_existing_order(tmp_path: Path) -> None:
    history = ExportHistory(tmp_path / "history.sqlite3")
    history.record("batch-1", "order-1", "#1", Carrier.SAGAWA)

    try:
        history.record("batch-2", "order-1", "#1", Carrier.SAGAWA)
    except ValueError as exc:
        assert "再出力理由" in str(exc)
    else:
        raise AssertionError("重複出力が許可された")

    history.record(
        "batch-2",
        "order-1",
        "#1",
        Carrier.SAGAWA,
        reexport_reason="CSVを紛失したため",
    )
    assert len(history.list_recent()) == 2


def test_batch_duplicate_check_is_atomic(tmp_path: Path) -> None:
    history = ExportHistory(tmp_path / "history.sqlite3")
    history.record("old", "order-existing", "#1", Carrier.SAGAWA)

    try:
        history.record_batch(
            "new-batch",
            [
                ("order-new", "#2", Carrier.SAGAWA),
                ("order-existing", "#1", Carrier.SAGAWA),
            ],
        )
    except ValueError as exc:
        assert "再出力理由" in str(exc)
    else:
        raise AssertionError("重複を含むバッチが記録された")

    assert history.exported_order_ids(["order-new"]) == set()
    assert len(history.list_recent()) == 1


def test_history_can_page_to_old_exports_and_search_order_number(tmp_path: Path) -> None:
    history = ExportHistory(tmp_path / "history.sqlite3")
    history.record_batch(
        "batch-many",
        [(f"order-{number}", f"#{number:04d}", Carrier.YAMATO) for number in range(1, 106)],
    )

    oldest_page = history.list_recent(limit=10, offset=100)

    assert [record.order_number for record in oldest_page] == [
        "#0005",
        "#0004",
        "#0003",
        "#0002",
        "#0001",
    ]
    assert history.count() == 105
    assert [record.order_number for record in history.list_recent(order_number="0001")] == ["#0001"]
    assert history.count(order_number="0001") == 1
