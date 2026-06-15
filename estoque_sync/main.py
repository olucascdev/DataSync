"""Ponto de entrada da aplicação Estoque Sync.

Carrega configurações, inicializa logging, pool de conexões,
scheduler e mantém o programa rodando com graceful shutdown.
"""

import asyncio
import signal
import sys
from typing import Any

from config.settings import settings
from app.logging_config import get_logger
from database.postgres import get_pool, close_pool
from scheduler.jobs import configurar_scheduler, encerrar_scheduler

logger = get_logger("main")

# Flag para controle de shutdown
_shutdown_event: asyncio.Event | None = None


def _setup_signal_handlers(scheduler: Any) -> None:
    """Configura handlers para SIGTERM e SIGINT para graceful shutdown.

    Args:
        scheduler: Instância do APScheduler.
    """
    loop = asyncio.get_event_loop()

    def _signal_handler() -> None:
        logger.info("sinal_de_shutdown_recebido_iniciando_encerramento_gracioso")
        if _shutdown_event:
            _shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("signal_handlers_configurados", signals=["SIGTERM", "SIGINT"])


async def main() -> None:
    """Função principal da aplicação."""
    global _shutdown_event

    logger.info(
        "estoque_sync_iniciando",
        log_level=settings.log_level,
        sync_interval=settings.sync_interval_seconds,
        postgres_host=settings.postgres_host,
        chrome_headless=settings.chrome_headless,
    )

    # -------------------------------------------------------
    # Inicializar pool de conexões
    # -------------------------------------------------------
    logger.info("inicializando_pool_postgres")
    try:
        pool = get_pool()
        logger.info("pool_postgres_inicializado")
    except Exception as exc:
        logger.error("falha_ao_inicializar_pool_postgres", error=str(exc))
        sys.exit(1)

    # -------------------------------------------------------
    # Configurar scheduler
    # -------------------------------------------------------
    logger.info("configurando_scheduler")
    scheduler = configurar_scheduler()

    # Configurar signal handlers
    _setup_signal_handlers(scheduler)

    # -------------------------------------------------------
    # Iniciar scheduler
    # -------------------------------------------------------
    logger.info("iniciando_scheduler")
    scheduler.start()

    # Executar o primeiro job imediatamente
    from scheduler.jobs import sincronizar_estoque

    logger.info("executando_primeiro_sync_imediato")
    try:
        await sincronizar_estoque()
    except Exception as exc:
        logger.error("erro_no_primeiro_sync", error=str(exc))

    # -------------------------------------------------------
    # Manter programa rodando
    # -------------------------------------------------------
    _shutdown_event = asyncio.Event()

    logger.info(
        "estoque_sync_em_execucao",
        dica="Pressione Ctrl+C para encerrar",
    )

    try:
        await _shutdown_event.wait()
    except asyncio.CancelledError:
        logger.info("loop_principal_cancelado")

    # -------------------------------------------------------
    # Graceful shutdown
    # -------------------------------------------------------
    logger.info("iniciando_shutdown_gracioso")

    # Encerrar scheduler e navegador
    await encerrar_scheduler(scheduler)

    # Fechar pool de conexões
    close_pool()

    logger.info("estoque_sync_encerrado")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("interrupted_by_user")
    except Exception as exc:
        logger.error("erro_fatal", error=str(exc), exc_info=True)
        sys.exit(1)
