import pytest
from pydantic import ValidationError

from wakou_transfer.config import AppConfig, CarrierFieldLimits


def valid_values() -> dict[str, object]:
    return {
        "auth_username": "operator",
        "auth_password": "test-secret",
    }


def test_config_does_not_require_sender_or_carrier_contract_codes() -> None:
    config = AppConfig.model_validate(valid_values())

    assert "sender" not in type(config).model_fields
    assert "billing_code" not in type(config.sagawa).model_fields
    assert "requester_code" not in type(config.yamato).model_fields
    assert "billing_classification_code" not in type(config.yamato).model_fields
    assert "freight_management_number" not in type(config.yamato).model_fields


def test_config_accepts_environment_specific_operational_values() -> None:
    values = valid_values()
    values["sagawa"] = {"service_type": "2"}
    values["yamato"] = {"label_type": "B", "item_name": "雑貨"}

    config = AppConfig.model_validate(values)

    assert config.sagawa.service_type == "2"
    assert config.yamato.label_type == "B"
    assert config.yamato.item_name == "雑貨"
    assert config.limits.yamato.name == 20


def test_config_requires_authentication_values() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({})


def test_carrier_limits_are_configurable_and_positive() -> None:
    values = valid_values()
    values["limits"] = {"yamato": CarrierFieldLimits(name=10, address=30, phone=15)}
    assert AppConfig.model_validate(values).limits.yamato.name == 10

    with pytest.raises(ValidationError):
        CarrierFieldLimits(name=0, address=30, phone=15)
