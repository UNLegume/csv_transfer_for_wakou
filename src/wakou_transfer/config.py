"""環境ごとに与える固定値と配送会社別制約。"""

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)


class SagawaConfig(ConfigModel):
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
    label_type: str = "A"
    item_name: str = "ネットショップ購入品"
    collect_amount: str = "0"
    collect_tax: str = "0"


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

    sagawa: SagawaConfig = Field(default_factory=SagawaConfig)
    yamato: YamatoConfig = Field(default_factory=YamatoConfig)
    auth_username: str = Field(min_length=1)
    auth_password: SecretStr
    limits: FieldLimits = Field(default_factory=FieldLimits)
    yamato_quantity_limit: int | None = Field(default=None, gt=0)
