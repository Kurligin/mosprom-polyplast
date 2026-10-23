"""Настройки приложения из переменных окружения (.env передаётся в контейнер через env_file)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # «МАКС»
    max_bot_token: str = ""
    max_bot_name: str = ""
    max_api_base_url: str = "https://platform-api2.max.ru"
    miniapp_url: str = ""

    # GigaChat
    gigachat_client_id: str = ""
    gigachat_credentials: str = ""
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2"

    # Инфраструктура
    database_url: str = "postgresql+asyncpg://korsik:korsik@postgres:5432/korsik"
    public_base_url: str = ""
    tz_default: str = "Europe/Moscow"
    ca_bundle_file: str = "certs/russian_trusted_ca_bundle.pem"

    # Выключить long polling бота (например, у второго разработчика)
    bot_enabled: bool = True


settings = Settings()
