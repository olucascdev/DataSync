"""Gerenciamento de login no sistema Objetiva Web.

Verifica se já está logado (sessão persistente) ou realiza login.
NUNCA desloga entre execuções para manter a sessão ASP.NET.
"""

import asyncio
from typing import Any

from config.settings import settings
from app.logging_config import get_logger

logger = get_logger("bot.login")


async def verificar_ou_logar(browser: Any, page: Any) -> Any:
    """Verifica se o usuário está logado ou realiza login.

    Estratégia:
    1. Verificar se a página atual contém campos de login
    2. Se não encontrar campos de login, assume que já está autenticado
    3. Se encontrar campos de login e houver credenciais no config, preencher
    4. Se não houver credenciais, aguardar login manual (perfil persistente)

    O perfil persistente do Chrome salva cookies e sessões ASP.NET,
    então na maioria das vezes o usuário já estará logado.

    Args:
        browser: Instância do browser nodriver.
        page: Página atual do nodriver.

    Returns:
        Página atual (logada).
    """
    logger.info("verificando_estado_de_login")

    try:
        # Tentar encontrar campos de login
        # O sistema Objetiva geralmente usa inputs com name ou id relacionados a login
        campos_login = [
            'input[id="Login"]',           # ID do Objetiva Web
            'input[name="Login"]',         # Name do Objetiva Web
            'input[id="login"]',           # lowercase fallback
            'input[name="login"]',
            'input[name="username"]',
            'input[name="usuario"]',
            'input[type="email"]',
            'input[id="username"]',
        ]

        campo_usuario_encontrado = None
        for seletor in campos_login:
            try:
                campo = await asyncio.wait_for(page.select(seletor), timeout=3.0)
                if campo:
                    campo_usuario_encontrado = campo
                    break
            except (asyncio.TimeoutError, Exception):
                continue

        if campo_usuario_encontrado is None:
            # Não encontrou campos de login - provavelmente já está logado
            logger.info("usuario_ja_esta_logado_sessao_persistente")
            return page

        # Encontrou campos de login - precisa autenticar
        logger.info("campos_de_login_encontrados_tentando_autenticar")

        if settings.objetiva_username and settings.objetiva_password:
            # Tem credenciais configuradas - realizar login automático
            logger.info("realizando_login_automatico")

            # Preencher usuário
            await campo_usuario_encontrado.click()
            await campo_usuario_encontrado.send_keys(settings.objetiva_username)

            # Encontrar e preencher senha
            campos_senha = [
                'input[id="Senha"]',           # ID do Objetiva Web
                'input[name="Senha"]',         # Name do Objetiva Web
                'input[name="password"]',
                'input[name="senha"]',
                'input[type="password"]',
                'input[id="password"]',
                'input[id="senha"]',
            ]

            campo_senha = None
            for seletor in campos_senha:
                try:
                    campo = await asyncio.wait_for(page.select(seletor), timeout=3.0)
                    if campo:
                        campo_senha = campo
                        break
                except (asyncio.TimeoutError, Exception):
                    continue

            if campo_senha:
                await campo_senha.click()
                await campo_senha.send_keys(settings.objetiva_password)

                # Encontrar e clicar no botão de login
                botoes_login = [
                    'button[type="submit"]',
                    'input[type="submit"]',
                ]

                botao_login = None
                for seletor in botoes_login:
                    try:
                        botao = await asyncio.wait_for(page.select(seletor), timeout=3.0)
                        if botao:
                            botao_login = botao
                            break
                    except (asyncio.TimeoutError, Exception):
                        continue

                # Fallback: iterar todos os botões e verificar texto
                if botao_login is None:
                    try:
                        botoes = await page.select_all("button")
                        for btn in botoes:
                            texto = await btn.get_text()
                            if texto and any(
                                p in texto.lower() for p in ["entrar", "login", "acessar", "enviar"]
                            ):
                                botao_login = btn
                                break
                    except Exception:
                        pass

                if botao_login:
                    await botao_login.click()
                    logger.info("login_automatico_realizado_aguardando_redirecionamento")

                    # Aguardar redirecionamento
                    await page.sleep(5)
                    return page
                else:
                    logger.warning("botao_de_login_nao_encontrado")
            else:
                logger.warning("campo_de_senha_nao_encontrado")
        else:
            # Sem credenciais - aguardar login manual
            logger.warning(
                "sem_credenciais_configuradas_aguardando_login_manual_ou_sessao_persistente"
            )
            logger.info(
                "dica_configure_OBJETIVA_USERNAME_e_OBJETIVA_PASSWORD_no_.env_para_login_automatico"
            )

        # Retornar a página atual (pode estar logada ou não)
        return page

    except Exception as exc:
        logger.error("erro_ao_verificar_ou_logar", error=str(exc))
        # Retornar a página mesmo em caso de erro
        return page
