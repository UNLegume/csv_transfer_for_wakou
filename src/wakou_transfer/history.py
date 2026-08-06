"""個人情報を保存せず、注文IDでプレビューと対応付けられるCSV出力履歴。"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from .domain import Carrier

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    order_number TEXT NOT NULL,
    carrier TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    reexport_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_exports_order_id ON exports(order_id);
"""


@dataclass(frozen=True)
class ExportRecord:
    batch_id: str
    order_id: str
    order_number: str
    carrier: Carrier
    exported_at: str
    reexport_reason: str | None


class ExportHistory:
    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def exported_order_ids(self, order_ids: list[str] | tuple[str, ...]) -> set[str]:
        if not order_ids:
            return set()
        placeholders = ",".join("?" for _ in order_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT DISTINCT order_id FROM exports WHERE order_id IN ({placeholders})",  # noqa: S608
                tuple(order_ids),
            )
            return {str(row["order_id"]) for row in rows}

    def record(
        self,
        batch_id: str,
        order_id: str,
        order_number: str,
        carrier: Carrier,
        *,
        reexport_reason: str | None = None,
    ) -> None:
        self.record_batch(
            batch_id,
            [(order_id, order_number, carrier)],
            reexport_reason=reexport_reason,
        )

    def record_batch(
        self,
        batch_id: str,
        orders: Sequence[tuple[str, str, Carrier]],
        *,
        reexport_reason: str | None = None,
    ) -> None:
        if not orders:
            return
        reason = reexport_reason.strip() if reexport_reason else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order_ids = [order_id for order_id, _, _ in orders]
            placeholders = ",".join("?" for _ in order_ids)
            existing = {
                str(row["order_id"])
                for row in connection.execute(
                    f"SELECT DISTINCT order_id FROM exports WHERE order_id IN ({placeholders})",  # noqa: S608
                    tuple(order_ids),
                )
            }
            if existing and not reason:
                raise ValueError("出力済み注文の再出力理由が必要です")
            exported_at = datetime.now(UTC).isoformat()
            connection.executemany(
                """INSERT INTO exports
                (batch_id, order_id, order_number, carrier, exported_at, reexport_reason)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        batch_id,
                        order_id,
                        order_number,
                        carrier.value,
                        exported_at,
                        reason if order_id in existing else None,
                    )
                    for order_id, order_number, carrier in orders
                ],
            )

    @staticmethod
    def _order_number_pattern(order_number: str) -> str:
        escaped = order_number.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def list_recent(
        self,
        limit: int = 100,
        offset: int = 0,
        order_number: str | None = None,
    ) -> tuple[ExportRecord, ...]:
        search = order_number.strip() if order_number else None
        where_clause = " WHERE order_number LIKE ? ESCAPE '\\'" if search else ""
        parameters: tuple[object, ...] = (
            (self._order_number_pattern(search), limit, offset) if search else (limit, offset)
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT batch_id, order_id, order_number, carrier, exported_at,
                reexport_reason FROM exports{where_clause}
                ORDER BY id DESC LIMIT ? OFFSET ?""",  # noqa: S608
                parameters,
            )
            return tuple(
                ExportRecord(
                    batch_id=str(row["batch_id"]),
                    order_id=str(row["order_id"]),
                    order_number=str(row["order_number"]),
                    carrier=Carrier(str(row["carrier"])),
                    exported_at=str(row["exported_at"]),
                    reexport_reason=(
                        str(row["reexport_reason"]) if row["reexport_reason"] else None
                    ),
                )
                for row in rows
            )

    def count(self, order_number: str | None = None) -> int:
        search = order_number.strip() if order_number else None
        with self._connect() as connection:
            if search:
                row = connection.execute(
                    "SELECT COUNT(*) AS total FROM exports WHERE order_number LIKE ? ESCAPE '\\'",
                    (self._order_number_pattern(search),),
                ).fetchone()
            else:
                row = connection.execute("SELECT COUNT(*) AS total FROM exports").fetchone()
        return int(row["total"])
