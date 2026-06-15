"""Configuração de logging com structlog e JSON."""

import logging
import sys
from typing import Any

import structlog
from structlog.types import Processor

from config.settings import settings


def _setup_logging() -> None:
    """Configura structlog com processadores JSON e logging padrão."""

    # Processadores do structlog
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Configurar logging padrão para usar structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )

    structlog.configure(
        processors=shared_processors
        + [
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Retorna um logger structlog configurado."""
    return structlog.get_logger(name)


def log_sync_to_db(
    conn: Any,
    origem: str,
    status: str,
    total_recebidos: int,
    total_atualizados: int,
    total_criados: int,
    detalhes: str = "",
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
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO carla_sync_logs
                    (origem, status, total_recebidos, total_atualizados, total_criados, detalhes, started_at, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (origem, status, total_recebidos, total_atualizados, total_criados, detalhes),
            )
        # O commit/rollback é gerenciado pelo context manager que fornece a conexão
    except Exception as exc:
        # Se a tabela não existir ou houver erro, logamos mas não falhamos o sync
        logger = get_logger("logging_config")
        logger.warning("falha_ao_logar_sync_no_db", error=str(exc))


# Inicializar logging ao importar o módulo
_setup_logging()
