"""環境ごとに与える固定値と配送会社別制約。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
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
    amount: str = "0"
    amount_unit: str = "0円"
    include_delivery_date: bool = True
    service_type: str = "1"
    option1: str = "必着"
    option2: str = "ご不在の際は不在票を入れて下さい"
    option3: str = "送り主："
    option4: str = "配達指定"
    option5: str = "【"
    option6: str = "】"
    postal_type: str = "0"
    payment_type: str = "1"
    cod_type: str = "Yes"


class YamatoConfig(ConfigModel):
    requester_code: str = Field(min_length=1)
    label_type: str = "A"
    item_name: str = "ネットショップ購入品"
    collect_amount: str = "0"
    collect_tax: str = "0"
    billing_classification_code: str = ""
    freight_management_number: str = ""


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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    sender: SenderConfig
    sagawa: SagawaConfig
    yamato: YamatoConfig
    auth_username: str = Field(min_length=1)
    auth_password: SecretStr
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

