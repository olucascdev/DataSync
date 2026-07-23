"""Configuração central de logging para console e JSON."""

import json
import logging
import sys
import time
from typing import Any

import structlog
from structlog.types import Processor

from config.settings import settings


def _format_console_value(value: Any) -> str:
    """Formata campos sem perder estrutura nem produzir linhas gigantes."""
    if isinstance(value, str):
        rendered = value if value and not any(char.isspace() for char in value) else json.dumps(
            value,
            ensure_ascii=False,
        )
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            rendered = repr(value)

    if len(rendered) > 800:
        return f"{rendered[:797]}..."
    return rendered


def _console_renderer(
    _logger: Any,
    _method_name: str,
    event_dict: dict[str, Any],
) -> str:
    """Renderiza uma linha estável e fácil de ler no docker logs."""
    timestamp = event_dict.pop("timestamp", "-")
    level = str(event_dict.pop("level", "info")).upper()
    component = str(event_dict.pop("component", "app"))
    event = str(event_dict.pop("event", "log")).replace("_", " ").upper()
    exception = event_dict.pop("exception", None)
    stack = event_dict.pop("stack", None)

    campos = " ".join(
        f"{key}={_format_console_value(value)}"
        for key, value in event_dict.items()
        if value is not None
    )
    linha = f"{timestamp} | {level:<7} | {component:<20} | {event}"
    if campos:
        linha = f"{linha} | {campos}"
    if exception:
        linha = f"{linha}\n{exception}"
    if stack:
        linha = f"{linha}\n{stack}"
    return linha


def _setup_logging() -> None:
    """Configura structlog e bibliotecas de terceiros."""
    app_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    third_party_level = getattr(
        logging,
        settings.third_party_log_level.upper(),
        logging.WARNING,
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    logging.basicConfig(
        format="%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
        level=app_level,
        force=True,
    )
    logging.Formatter.converter = time.gmtime
    for logger_name in ("apscheduler", "nodriver", "websockets", "httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(third_party_level)

    renderer: Processor
    if settings.log_format.lower() == "json":
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = _console_renderer

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(app_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Retorna um logger structlog configurado."""
    return structlog.get_logger().bind(component=name)


def log_sync_to_db(
    conn: Any,
    origem: str,
    status: str,
    total_recebidos: int,
    total_atualizados: int,
    total_criados: int,
    detalhes: dict | str = "",
) -> None:
    """Registra um log de sincronização na tabela carla_sync_logs.

    Args:
        conn: Conexão psycopg3 ativa.
        origem: Identificador da origem (ex: "pdf_estoque").
        status: "success" ou "error".
        total_recebidos: Total de registros recebidos do PDF.
        total_atualizados: Total de registros atualizados no UPSERT.
        total_criados: Total de registros inseridos no UPSERT.
        detalhes: Detalhes adicionais em JSON ou texto livre.
    """
    if isinstance(detalhes, dict):
        detalhes_json = json.dumps(detalhes, ensure_ascii=False)
    elif detalhes:
        detalhes_json = json.dumps({"msg": detalhes}, ensure_ascii=False)
    else:
        detalhes_json = "{}"

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO carla_sync_logs
                    (origem, status, total_recebidos, total_atualizados, total_criados, detalhes, started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (origem, status, total_recebidos, total_atualizados, total_criados, detalhes_json),
            )
        # O commit/rollback é gerenciado pelo context manager que fornece a conexão
    except Exception as exc:
        # Se a tabela não existir ou houver erro, logamos mas não falhamos o sync
        logger = get_logger("logging_config")
        logger.warning("falha_ao_logar_sync_no_db", error=str(exc))


# Inicializar logging ao importar o módulo
_setup_logging()
