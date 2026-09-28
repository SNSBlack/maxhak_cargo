"""Конфигурация сервиса. Все значения читаются из .env / переменных окружения."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # MAX
    max_bot_token: str = ""
    max_bot_username: str = "t459_hakaton_max_bot"
    max_api_base: str = "https://botapi.max.ru"
    public_webapp_url: str = ""
    # MAX принимает кнопку open_app только для мини-приложения, которое
    # организаторы привязали к боту. С непривязанным адресом API отклоняет
    # сообщение целиком (404 "Link not found"). Ставим 1 после подтверждения.
    miniapp_registered: int = 0

    # Авторизация мини-приложения
    auth_mode: str = "dev"  # strict | lenient | dev
    session_ttl_hours: int = 24

    # Источники данных
    data_source: str = "demo"  # demo | odata
    # Какой файл читает демо-источник: маленький набор для проверки формул
    # или сгенерированный масштабный (tools/generate_dataset.py).
    demo_data_file: str = "data/demo_1c.json"
    onec_base_url: str = ""
    onec_user: str = ""
    onec_password: str = ""
    onec_mapping: str = "ut_11"

    edo_provider: str = "demo"
    edo_api_base: str = ""
    edo_api_key: str = ""

    routing_engine: str = "demo"
    yandex_routing_api_key: str = ""

    # Инфраструктура
    enable_bot: int = 1
    host: str = "127.0.0.1"
    port: int = 8099
    db_path: str = "data/app.sqlite3"

    @property
    def db_file(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else ROOT / p

    @property
    def deeplink(self) -> str:
        return f"https://max.ru/{self.max_bot_username}"


settings = Settings()
