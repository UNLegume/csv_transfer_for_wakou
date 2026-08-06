import pytest
from pydantic import ValidationError

from wakou_transfer.config import AppConfig, CarrierFieldLimits, SagawaConfig, SenderConfig


def valid_values() -> dict[str, object]:
    return {
        "sender": SenderConfig(
            company_name="株式会社ワコウ",
            requester_name="発送担当",
            postal_code="0010001",
            address="北海道札幌市",
            phone="0110000000",
        ),
        "sagawa": SagawaConfig(billing_code="0012345678"),
        "yamato": {"requester_code": "1234567890"},
        "auth_username": "operator",
        "auth_password": "test-secret",
    }


def test_config_accepts_environment_specific_fixed_values() -> None:
    config = AppConfig.model_validate(valid_values())

    assert config.sender.postal_code == "0010001"
    assert config.sagawa.billing_code == "0012345678"
    assert config.limits.yamato.name == 20


def test_config_missing_required_value_has_clear_japanese_error() -> None:
    values = valid_values()
    values.pop("sender")

    with pytest.raises(ValidationError, match="出荷元情報が設定されていません"):
        AppConfig.model_validate(values)


def test_carrier_limits_are_configurable_and_positive() -> None:
    values = valid_values()
    values["limits"] = {"yamato": CarrierFieldLimits(name=10, address=30, phone=15)}
    assert AppConfig.model_validate(values).limits.yamato.name == 10

    with pytest.raises(ValidationError):
        CarrierFieldLimits(name=0, address=30, phone=15)

