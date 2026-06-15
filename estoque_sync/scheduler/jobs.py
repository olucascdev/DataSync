"""Jobs do scheduler de sincronização de estoque.

Configura e executa o job periódico de sincronização usando APScheduler.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config.settings import settings
from app.logging_config import get_logger, log_sync_to_db
from database.postgres import get_pool, get_connection
from database.repositories import EstoqueRepository
from database.upsert import upsert_estoque
from parser.pdf_parser import extrair_produtos_pdf
from parser.normalizador import normalizar_df
from bot.navegador import iniciar_navegador, fechar_navegador
from bot.relatorio import gerar_e_baixar_relatorio, RELATORIO_URL
from bot.login import verificar_ou_logar

logger = get_logger("scheduler.jobs")

# Browser global para reutilizar entre execuções
# Mantém a sessão ASP.NET ativa e evita overhead de reiniciar
_browser: Any = None
_sync_lock: asyncio.Lock | None = None


async def sincronizar_estoque() -> None:
    """Executa a sincronização impedindo concorrência no mesmo navegador."""
    global _sync_lock

    if _sync_lock is None:
        _sync_lock = asyncio.Lock()

    if _sync_lock.locked():
        logger.warning("sync_ignorado_porque_ja_existe_execucao_em_andamento")
        return

    async with _sync_lock:
        await _sincronizar_estoque_impl()


async def _sincronizar_estoque_impl() -> None:
    """Job principal de sincronização de estoque.

    Fluxo:
    1. Iniciar navegador (ou reutilizar existente)
    2. Baixar PDF do relatório de estoque
    3. Extrair produtos do PDF
    4. Normalizar dados
    5. UPSERT no banco
    6. Log do sync
    7. Limpar PDF temporário
    """
    global _browser

    sync_start = datetime.now()
    origem = "pdf_estoque"
    status = "error"
    total_recebidos = 0
    total_atualizados = 0
    total_criados = 0
    detalhes = ""
    caminho_pdf = ""

    try:
        logger.info("sync_start", timestamp=sync_start.isoformat())

        # -------------------------------------------------------
        # 1. Iniciar ou reutilizar navegador
        # -------------------------------------------------------
        if _browser is None:
            logger.info("iniciando_novo_navegador")
            _browser = await iniciar_navegador()
        else:
            logger.info("reutilizando_navegador_existente")

        # -------------------------------------------------------
        # 1.1 Verificar sessão / fazer login
        # -------------------------------------------------------
        # Se o browser já existe e tem abas abertas, verificar se já está
        # na página do relatório ou em uma página logada.
        # Só navegar para a URL base se for a primeira execução ou se
        # detectar que está deslogado.
        page_login = None
        precisa_navegar = True

        if _browser is not None:
            try:
                abas_existentes = _browser.tabs
                if len(abas_existentes) > 0:
                    # Reutilizar a última aba ativa
                    page_login = abas_existentes[-1]
                    logger.info(
                        "reutilizando_aba_existente",
                        url=page_login.url,
                        total_abas=len(abas_existentes),
                    )
                    # Se já está na página do relatório ou em uma URL do sistema,
                    # não precisa navegar para a URL base
                    if (
                        RELATORIO_URL.split("/Relatorio")[0] in page_login.url
                        or settings.objetiva_url in page_login.url
                    ):
                        precisa_navegar = False
                        logger.info("ja_esta_em_pagina_do_sistema_pulando_navegacao")
            except Exception as exc:
                logger.warning("erro_ao_verificar_abas_existentes", error=str(exc))

        if precisa_navegar:
            logger.info("navegando_para_url_base")
            if page_login is None:
                page_login = await _browser.get(settings.objetiva_url)
            else:
                page_login = await _browser.get(settings.objetiva_url)
            await page_login.sleep(2)

        await verificar_ou_logar(_browser, page_login)
        logger.info("login_verificado")

        # -------------------------------------------------------
        # 2. Baixar PDF
        # -------------------------------------------------------
        logger.info("gerando_relatorio")
        caminho_pdf = await gerar_e_baixar_relatorio(_browser)
        logger.info("pdf_downloaded", caminho=caminho_pdf)

        # -------------------------------------------------------
        # 3. Extrair produtos do PDF
        # -------------------------------------------------------
        logger.info("extraindo_produtos_do_pdf")
        df_raw = await asyncio.to_thread(extrair_produtos_pdf, caminho_pdf)
        total_recebidos = len(df_raw)
        logger.info("pdf_parsed", total_produtos=total_recebidos)

        if df_raw.empty:
            detalhes = "Nenhum produto extraído do PDF"
            logger.warning("nenhum_produto_extraido")
            status = "error"
            return

        # -------------------------------------------------------
        # 4. Normalizar dados
        # -------------------------------------------------------
        logger.info("normalizando_dados")
        df_norm = await asyncio.to_thread(normalizar_df, df_raw)

        # -------------------------------------------------------
        # 5. UPSERT no banco
        # -------------------------------------------------------
        logger.info("realizando_upsert")
        resultado = await asyncio.to_thread(_upsert_sync, df_norm)
        total_atualizados = resultado["atualizados"]
        total_criados = resultado["inseridos"]

        logger.info(
            "upsert_finished",
            atualizados=total_atualizados,
            criados=total_criados,
        )

        status = "success"
        detalhes = f"Sync concluído em {(datetime.now() - sync_start).total_seconds():.1f}s"

    except Exception as exc:
        logger.error("sync_error", error=str(exc), exc_info=True)
        status = "error"
        detalhes = f"Erro: {str(exc)}"

    finally:
        # -------------------------------------------------------
        # 6. Log do sync no banco
        # -------------------------------------------------------
        try:
            await asyncio.to_thread(
                _log_sync_sync,
                origem,
                status,
                total_recebidos,
                total_atualizados,
                total_criados,
                detalhes,
            )
        except Exception as exc:
            logger.warning("falha_ao_logar_sync", error=str(exc))

        # -------------------------------------------------------
        # 7. Limpar PDF temporário
        # -------------------------------------------------------
        if caminho_pdf and os.path.exists(caminho_pdf):
            try:
                os.remove(caminho_pdf)
                logger.info("pdf_temporario_removido", caminho=caminho_pdf)
            except Exception as exc:
                logger.warning("falha_ao_remover_pdf", caminho=caminho_pdf, error=str(exc))

        duracao = (datetime.now() - sync_start).total_seconds()
        logger.info(
            "sync_concluido",
            status=status,
            duracao_segundos=duracao,
            recebidos=total_recebidos,
            atualizados=total_atualizados,
            criados=total_criados,
        )


def configurar_scheduler() -> AsyncIOScheduler:
    """Configura o APScheduler com o job de sincronização.

    Returns:
        Instância do scheduler configurado.
    """
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        sincronizar_estoque,
        "interval",
        seconds=settings.sync_interval_seconds,
        id="sync_estoque",
        max_instances=1,
        replace_existing=True,
        name="Sincronização de Estoque",
    )

    logger.info(
        "scheduler_configurado",
        intervalo_segundos=settings.sync_interval_seconds,
    )

    return scheduler


async def encerrar_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Encerra o scheduler e fecha o navegador global.

    Args:
        scheduler: Instância do scheduler.
    """
    global _browser

    logger.info("encerrando_scheduler")

    # Parar scheduler
    if scheduler.running:
        scheduler.shutdown(wait=False)

    # Fechar navegador
    if _browser is not None:
        try:
            await fechar_navegador(_browser)
        except Exception as exc:
            logger.warning("erro_ao_fechar_navegador_no_shutdown", error=str(exc))
        _browser = None

    logger.info("scheduler_encerrado")


# ---------------------------------------------------------------------------
# Funções auxiliares síncronas para asyncio.to_thread()
# ---------------------------------------------------------------------------

def _upsert_sync(df_norm):
    """Wrapper síncrono para upsert de estoque (executado via to_thread)."""
    with get_connection() as conn:
        repo = EstoqueRepository(conn)
        return repo.upsert_batch(df_norm)


def _log_sync_sync(origem, status, total_recebidos, total_atualizados, total_criados, detalhes):
    """Wrapper síncrono para log de sync (executado via to_thread)."""
    with get_connection() as conn:
        log_sync_to_db(
            conn=conn,
            origem=origem,
            status=status,
            total_recebidos=total_recebidos,
            total_atualizados=total_atualizados,
            total_criados=total_criados,
            detalhes=detalhes,
        )
