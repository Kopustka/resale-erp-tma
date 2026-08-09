"""Конфигурация приложения из переменных окружения."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Telegram
    bot_token: str = Field(..., alias="BOT_TOKEN")
    webapp_url: str = Field("https://example.com", alias="WEBAPP_URL")
    # TTL валидности initData (защита от replay), секунды
    init_data_ttl: int = Field(86400, alias="INIT_DATA_TTL")

    # База данных
    database_url: str = Field(
        "postgresql+asyncpg://postgres:postgres@localhost:5432/resale",
        alias="DATABASE_URL",
    )
    # Redis (idempotency + кэш BI)
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    # Домен
    app_currency: str = Field("Br", alias="APP_CURRENCY")  # белорусский рубль, без конвертаций
    default_timezone: str = Field("Europe/Minsk", alias="DEFAULT_TIMEZONE")
    stale_days_threshold: int = Field(60, alias="STALE_DAYS_THRESHOLD")

    # Инфраструктура
    cors_origins: str = Field("*", alias="CORS_ORIGINS")
    media_cache_ttl: int = Field(86400, alias="MEDIA_CACHE_TTL")
    # Загрузка фото из галереи (локальное хранилище)
    media_dir: str = Field("/opt/resale-erp/backend/media", alias="MEDIA_DIR")
    max_upload_mb: int = Field(12, alias="MAX_UPLOAD_MB")

    # AI-генерация названия/описания по фото (Gemini, бесплатный ключ AI Studio)
    gemini_api_key: str = Field("", alias="GEMINI_API_KEY")
    gemini_model: str = Field("gemini-2.5-flash", alias="GEMINI_MODEL")

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
