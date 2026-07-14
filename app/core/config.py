from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    market_data_mode: Literal["live", "demo"] = "live"
    app_timezone: str = "Asia/Tokyo"
    database_url: str = "postgresql+psycopg://market:market_password@localhost:5432/market_signal_lab"
    fred_api_key: str = ""
    fred_base_url: str = "https://api.stlouisfed.org/fred"
    jquants_api_key: str = ""
    jquants_base_url: str = "https://api.jquants.com"
    sec_base_url: str = "https://data.sec.gov"
    sec_user_agent: str = ""
    jquants_min_request_interval_seconds: int = Field(default=15, ge=0)
    api_timeout_seconds: int = 20
    data_stale_after_days: int = Field(default=7, ge=1)
    backup_dir: str = "/backups"
    backup_retention_days: int = Field(default=14, ge=1)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
