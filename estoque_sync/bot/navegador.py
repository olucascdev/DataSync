"""Gerenciamento do navegador usando nodriver.

nodriver é um driver de automação baseado em CDP (Chrome DevTools Protocol)
que não requer chromedriver separado.
"""

import os
import json
import shutil
from pathlib import Path
from typing import Any, Optional

import nodriver as uc

from config.settings import settings
from app.logging_config import get_logger

logger = get_logger("bot.navegador")

# Caminhos comuns do Brave Browser para auto-detecção
BRAVE_PATHS = [
    "/usr/bin/brave",
    "/usr/bin/brave-browser",
    "/usr/local/bin/brave",
    "/usr/local/bin/brave-browser",
    "/opt/brave.com/brave/brave",
]


def _configurar_preferencias_download() -> None:
    """Configura o perfil persistente para baixar PDF sem prompt do sistema."""
    profile_dir = Path(settings.chrome_profile_dir)
    default_dir = profile_dir / "Default"
    preferences_path = default_dir / "Preferences"
    download_dir = Path(settings.download_dir).resolve()

    try:
        default_dir.mkdir(parents=True, exist_ok=True)
        download_dir.mkdir(parents=True, exist_ok=True)

        preferences = {}
        if preferences_path.exists():
            try:
                preferences = json.loads(preferences_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                preferences = {}

        preferences.setdefault("download", {})
        preferences["download"].update(
            {
                "default_directory": str(download_dir),
                "directory_upgrade": True,
                "prompt_for_download": False,
            }
        )

        preferences.setdefault("plugins", {})
        preferences["plugins"]["always_open_pdf_externally"] = True

        preferences_path.write_text(json.dumps(preferences), encoding="utf-8")
        logger.info("preferencias_download_configuradas", download_dir=str(download_dir))
    except Exception as exc:
        logger.warning("falha_ao_configurar_preferencias_download", error=str(exc))


def _detectar_navegador() -> Optional[str]:
    """Detecta o caminho do executável do navegador.

    Prioridade:
    1. Caminho explícito em settings.browser_executable_path
    2. Auto-detecção do Brave nos caminhos comuns
    3. Auto-detecção do Brave via PATH (shutil.which)
    4. None (fallback para Chrome padrão do nodriver)

    Returns:
        Caminho do executável ou None para usar o padrão do nodriver.
    """
    # 1. Caminho explícito configurado
    if settings.browser_executable_path:
        caminho = settings.browser_executable_path
        if os.path.isfile(caminho):
            logger.info("navegador_detectado", fonte="config", caminho=caminho)
            return caminho
        logger.warning(
            "caminho_executavel_nao_existe",
            caminho=caminho,
            msg="Caminho configurado não existe, tentando auto-detectar",
        )

    # 2. Auto-detecção do Brave em caminhos conhecidos
    for caminho in BRAVE_PATHS:
        if os.path.isfile(caminho):
            logger.info("navegador_detectado", fonte="auto-detect-caminhos", caminho=caminho)
            return caminho

    # 3. Auto-detecção do Brave via PATH
    for nome in ("brave", "brave-browser"):
        caminho = shutil.which(nome)
        if caminho:
            logger.info("navegador_detectado", fonte="auto-detect-path", caminho=caminho)
            return caminho

    # 4. Fallback: Chrome padrão do nodriver
    logger.info("navegador_nao_detectado", msg="Brave não encontrado, usando Chrome padrão do nodriver")
    return None


async def iniciar_navegador() -> Any:
    """Inicia o navegador Chrome/Brave usando nodriver.

    Usa perfil persistente para manter sessões ASP.NET entre execuções.
    Detecta automaticamente o Brave Browser se disponível.

    Returns:
        Instância do browser nodriver.
    """
    logger.info(
        "iniciando_navegador",
        headless=settings.chrome_headless,
        profile_dir=settings.chrome_profile_dir,
    )

    browser_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-pdf-extension",
    ]

    # Adicionar argumentos customizados do settings
    if settings.chrome_args:
        browser_args.extend(settings.chrome_args)

    # Detectar executável do navegador
    executable_path = _detectar_navegador()
    _configurar_preferencias_download()

    try:
        kwargs: dict[str, Any] = {
            "headless": settings.chrome_headless,
            "user_data_dir": settings.chrome_profile_dir,
            "browser_args": browser_args,
            "no_sandbox": True,
        }
        if executable_path:
            kwargs["browser_executable_path"] = executable_path
            logger.info("caminho_executavel", caminho=executable_path)

        browser = await uc.start(**kwargs)

        logger.info("navegador_iniciado_com_sucesso")
        return browser

    except Exception as exc:
        logger.error("falha_ao_iniciar_navegador", error=str(exc))
        raise


async def fechar_navegador(browser: Any) -> None:
    """Fecha o navegador de forma graciosa.

    Args:
        browser: Instância do browser nodriver.
    """
    try:
        logger.info("fechando_navegador")
        browser.stop()
        logger.info("navegador_fechado")
    except Exception as exc:
        logger.warning("erro_ao_fechar_navegador", error=str(exc))
