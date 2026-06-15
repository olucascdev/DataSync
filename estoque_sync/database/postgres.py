"""Gerenciamento de conexão com PostgreSQL usando psycopg3 pool."""

import time
from contextlib import contextmanager
from typing import Generator

import psycopg
from psycopg_pool import ConnectionPool

from config.settings import settings
from app.logging_config import get_logger

logger = get_logger("database.postgres")

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Retorna o pool singleton de conexões PostgreSQL.

    Cria o pool na primeira chamada e reutiliza nas subsequentes.
    Inclui retry com backoff exponencial em caso de falha na criação.
    """
    global _pool

    if _pool is not None:
        return _pool

    max_retries = 5
    base_delay = 2.0

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                "criando_pool_postgres",
                host=settings.postgres_host,
                port=settings.postgres_port,
                db=settings.postgres_db,
                attempt=attempt,
            )

            _pool = ConnectionPool(
                conninfo=settings.postgres_dsn,
                min_size=2,
                max_size=10,
                open=True,
                check=ConnectionPool.check_connection,
            )

            logger.info("pool_postgres_criado_com_sucesso")
            return _pool

        except psycopg.Error as exc:
            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "falha_ao_criar_pool",
                attempt=attempt,
                max_retries=max_retries,
                delay=delay,
                error=str(exc),
            )
            if attempt < max_retries:
                time.sleep(delay)
            else:
                logger.error("falha_maxima_ao_criar_pool", error=str(exc))
                raise

    raise RuntimeError("Não foi possível criar o pool de conexões")


@contextmanager
def get_connection() -> Generator[psycopg.Connection, None, None]:
    """Context manager que obtém uma conexão do pool.

    Usage:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    """
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool() -> None:
    """Fecha o pool de conexões de forma graciosa."""
    global _pool
    if _pool is not None:
        logger.info("fechando_pool_postgres")
        _pool.close()
        _pool = None
