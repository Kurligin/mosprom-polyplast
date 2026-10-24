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

    # LLM: gigachat (облако) или openai — любой OpenAI-совместимый сервер в закрытом контуре (vLLM, Ollama)
    llm_provider: str = "gigachat"
    llm_base_url: str = ""      # например http://ollama:11434/v1 или http://vllm:8000/v1
    llm_model: str = ""         # имя модели на этом сервере
    llm_api_key: str = ""       # если сервер требует ключ

    # Инфраструктура
    database_url: str = "postgresql+asyncpg://korsik:korsik@postgres:5432/korsik"
    public_base_url: str = ""
    tz_default: str = "Europe/Moscow"
    ca_bundle_file: str = "certs/russian_trusted_ca_bundle.pem"

    # Выключить long polling бота (например, у второго разработчика)
    bot_enabled: bool = True
    # Фоновые задачи (тик сценария, напоминания, сводка)
    jobs_enabled: bool = True
    # Mini App вне «МАКС» (браузер, разработка): пускать без initData как HR. В проде — false.
    # Действует только для запросов с этой машины, не через прокси и туннели (app/api/auth.py)
    dev_allow_no_init_data: bool = False
    # Демо-кнопки панели: машина времени (только тестовые и синтетические новички) и «Сформировать сводку».
    # В docker-compose.prod.yml по умолчанию false
    demo_enabled: bool = True
    # «Путь новичка»: вехи по опросам, карточка пути картинкой, мерч-сюрприз в конце (app/path)
    path_enabled: bool = True

    # База знаний
    embedding_model: str = "BAAI/bge-m3"

    # Голосовые сообщения: распознавание только локально (faster-whisper на CPU), аудио наружу не уходит.
    # Модель кэшируется в томе /models (HF_HOME), как bge-m3. Замер на сервере (8 vCPU Broadwell, int8,
    # 4 потока, фраза 7 с): small ≈ 5–6 с, medium ≈ 13 с, large-v3-turbo ≈ 20 с (точнее всех).
    # Нужна точность важнее скорости — VOICE_MODEL=large-v3-turbo.
    voice_enabled: bool = True
    voice_model: str = "small"
    voice_device: str = "cpu"
    voice_threads: int = 4
    voice_beam_size: int = 5
    voice_max_seconds: int = 60       # дольше — просим записать покороче
    voice_max_bytes: int = 10 * 1024 * 1024
    voice_timeout: float = 90.0       # скачивание + распознавание, секунд


settings = Settings()
