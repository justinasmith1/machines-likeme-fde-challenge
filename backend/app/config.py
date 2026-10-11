from decimal import Decimal
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Azure OpenAI is optional for now so the app starts without it.
    # The key is a SecretStr: repr/str show '**********', never the value.
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: SecretStr | None = None
    azure_openai_deployment: str = "JustinaSmith-RCS2"

    price_tolerance_pct: Decimal = Decimal("0.02")
    analyst_approval_limit: Decimal = Decimal("5000")

    reference_data_dir: str = "data/reference"
    database_url: str = "sqlite:///./contoso.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
