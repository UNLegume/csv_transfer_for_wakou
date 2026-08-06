"""送り状生成前の宛先正規化・検証。"""

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from .config import CarrierFieldLimits
from .domain import Address, Carrier


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    order_number: str
    field: str
    reason: str
    code: str
    severity: Severity


def normalize_postal_code(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"[-ー\s]", "", normalized)


def normalize_phone(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    compact = re.sub(r"[-ー()（）\s]", "", normalized)
    if compact.startswith("+81"):
        compact = "0" + compact[3:]
    return compact


def validate_address(
    order_number: str,
    address: Address,
    carrier: Carrier,
    limits: CarrierFieldLimits,
) -> tuple[ValidationIssue, ...]:
    del carrier  # limits are selected by the caller for the target carrier.
    issues: list[ValidationIssue] = []

    def error(field: str, code: str, reason: str) -> None:
        issues.append(ValidationIssue(order_number, field, reason, code, Severity.ERROR))

    address_text = f"{address.address1}{address.address2}"
    required = {"name": address.name, "address": address_text}
    for field, value in required.items():
        if not value:
            error(field, "required", "必須項目が未入力です")

    postal_code = normalize_postal_code(address.postal_code)
    if not re.fullmatch(r"\d{7}", postal_code):
        error("postal_code", "invalid_format", "郵便番号は7桁で入力してください")

    phone = normalize_phone(address.phone)
    if not re.fullmatch(r"0\d{9,10}", phone):
        error("phone", "invalid_format", "電話番号を国内形式の10桁または11桁で入力してください")

    for field, value, maximum in (
        ("name", address.name, limits.name),
        ("address", address_text, limits.address),
        ("phone", phone, limits.phone),
    ):
        if len(value) > maximum:
            error(field, "too_long", f"{maximum}文字以内で入力してください")

    for field, value in (
        ("name", address.name),
        ("address", address_text),
    ):
        try:
            value.encode("cp932")
        except UnicodeEncodeError:
            error(field, "not_cp932", "CP932で表現できない文字が含まれています")

    return tuple(issues)


def has_errors(issues: tuple[ValidationIssue, ...]) -> bool:
    """CSV出力対象から除外すべきエラーがあるかを返す。"""
    return any(issue.severity is Severity.ERROR for issue in issues)
