"""Configurações da aplicação usando pydantic-settings."""

from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações carregadas do .env e variáveis de ambiente."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "carla_db"
    postgres_user: str = "carla"
    postgres_password: str = "senha_segura_aqui"

    # Objetiva Web
    objetiva_url: str = "https://carlabaleeiro.objetivaweb.app.br"
    objetiva_username: str = ""
    objetiva_password: str = ""

    # Chrome / nodriver
    chrome_headless: bool = True
    chrome_profile_dir: str = "./data/chrome-profile"
    chrome_args: List[str] = []
    browser_executable_path: str = ""  # Caminho absoluto do executável do navegador (ex: /usr/bin/google-chrome-stable). Vazio = auto-detectar.

    # Scheduler
    sync_interval_seconds: int = 3600
    sync_interval_jitter_seconds: int = 300
    sync_timeout_seconds: int = 1800
    sync_max_attempts: int = 2
    sync_retry_delay_seconds: float = 300.0
    sync_failure_threshold: int = 3
    sync_failure_cooldown_seconds: int = 21600
    sync_startup_min_interval_seconds: int = 1800
    sync_min_products: int = 6000
    sync_max_product_drop_percent: float = 10.0

    # Politica de preco
    price_update_interval_hours: int = 24
    price_max_change_percent: float = 30.0

    # Login / Turnstile
    turnstile_max_clicks: int = 3
    turnstile_token_wait_seconds: float = 15.0
    turnstile_poll_interval_seconds: float = 1.0
    login_redirect_timeout_seconds: int = 30
    login_diagnostics_dir: str = "./logs"

    # Logging
    log_level: str = "INFO"
    log_format: str = "console"
    third_party_log_level: str = "WARNING"

    # Downloads
    download_dir: str = "./downloads"

    @property
    def postgres_dsn(self) -> str:
        """Retorna a DSN do PostgreSQL."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
