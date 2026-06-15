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
    browser_executable_path: str = ""  # Caminho absoluto do executável do navegador (ex: /usr/bin/brave). Vazio = auto-detectar.

    # Scheduler
    sync_interval_seconds: int = 60

    # Logging
    log_level: str = "INFO"

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
