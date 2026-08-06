import pytest

from wakou_transfer.config import CarrierFieldLimits
from wakou_transfer.domain import Address, Carrier
from wakou_transfer.validation import (
    Severity,
    normalize_phone,
    normalize_postal_code,
    validate_address,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("001-0001", "0010001"), ("００１ー０００１", "0010001")],
)
def test_postal_code_normalization_preserves_leading_zero(raw: str, expected: str) -> None:
    assert normalize_postal_code(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("090-1234-5678", "09012345678"), ("＋８１ ９０－１２３４－５６７８", "09012345678")],
)
def test_phone_normalization(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


def test_validation_returns_order_field_reason_and_severity() -> None:
    address = Address(name="", postal_code="123", address1="", phone="abc")
    issues = validate_address("00001", address, Carrier.YAMATO, CarrierFieldLimits())
    assert {issue.field for issue in issues} >= {"name", "postal_code", "address", "phone"}
    assert all(issue.order_number == "00001" and issue.reason for issue in issues)
    assert all(issue.severity is Severity.ERROR for issue in issues)


def test_validation_detects_length_and_cp932_errors() -> None:
    limits = CarrierFieldLimits(name=3, address=5, phone=15)
    address = Address(
        name="長すぎる名前", postal_code="0010001", address1="北海道札幌市😀", phone="09012345678"
    )
    issues = validate_address("1", address, Carrier.SAGAWA, limits)
    assert {(issue.field, issue.code) for issue in issues} >= {
        ("name", "too_long"),
        ("address", "too_long"),
        ("address", "not_cp932"),
    }


def test_valid_address_has_no_errors() -> None:
    address = Address(
        name="山田太郎", postal_code="001-0001", address1="北海道札幌市", phone="+81-90-1234-5678"
    )
    assert validate_address("1", address, Carrier.YAMATO, CarrierFieldLimits()) == ()
