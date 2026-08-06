"""環境ごとに与える固定値と配送会社別制約。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)


class SenderConfig(ConfigModel):
    company_name: str = Field(min_length=1)
    requester_name: str = Field(min_length=1)
    postal_code: str = Field(pattern=r"^\d{7}$")
    address: str = Field(min_length=1)
    phone: str = Field(pattern=r"^\d+$")


class SagawaConfig(ConfigModel):
    billing_code: str = Field(min_length=1)


class YamatoConfig(ConfigModel):
    requester_code: str = Field(min_length=1)


class CarrierFieldLimits(ConfigModel):
    name: int = Field(default=20, gt=0)
    address: int = Field(default=50, gt=0)
    phone: int = Field(default=15, gt=0)


class FieldLimits(ConfigModel):
    yamato: CarrierFieldLimits = Field(default_factory=CarrierFieldLimits)
    sagawa: CarrierFieldLimits = Field(default_factory=CarrierFieldLimits)


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        frozen=True,
        str_strip_whitespace=True,
        env_prefix="WAKOU_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    sender: SenderConfig
    sagawa: SagawaConfig
    yamato: YamatoConfig
    limits: FieldLimits = Field(default_factory=FieldLimits)
    yamato_quantity_limit: int | None = Field(default=None, gt=0)

    @model_validator(mode="before")
    @classmethod
    def report_missing_sections(cls, value: Any) -> Any:
        if isinstance(value, dict):
            labels = {
                "sender": "出荷元情報",
                "sagawa": "佐川設定",
                "yamato": "ヤマト設定",
            }
            missing = [label for key, label in labels.items() if not value.get(key)]
            if missing:
                raise ValueError(f"{'・'.join(missing)}が設定されていません")
        return value

