from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.security import register_database_url_secrets, register_secret


class Settings(BaseSettings):
    app_env: str = "local"
    market_data_mode: Literal["live", "demo"] = "live"
    app_timezone: str = "Asia/Tokyo"
    database_url: str = Field(
        default="postgresql+psycopg://market@localhost:5432/market_signal_lab",
        min_length=1,
        repr=False,
    )
    fred_api_key: str = Field(default="", repr=False)
    fred_base_url: str = "https://api.stlouisfed.org/fred"
    jquants_api_key: str = Field(default="", repr=False)
    jquants_base_url: str = "https://api.jquants.com"
    sec_base_url: str = "https://data.sec.gov"
    sec_user_agent: str = ""
    jquants_min_request_interval_seconds: int = Field(default=15, ge=0)
    api_timeout_seconds: int = 20
    data_stale_after_days: int = Field(default=7, ge=1)
    backup_dir: str = "/backups"
    backup_retention_days: int = Field(default=14, ge=1)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def model_post_init(self, __context: object) -> None:
        register_database_url_secrets(self.database_url)
        register_secret(self.fred_api_key)
        register_secret(self.jquants_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
