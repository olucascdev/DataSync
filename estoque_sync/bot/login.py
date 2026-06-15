"""Gerenciamento de login no sistema Objetiva Web.

Verifica se já está logado (sessão persistente) ou realiza login.
NUNCA desloga entre execuções para manter a sessão ASP.NET.
"""

import asyncio
import json
from typing import Any

from config.settings import settings
from app.logging_config import get_logger

logger = get_logger("bot.login")


async def _js(page: Any, script: str) -> Any:
    """Executa JS e retorna resultado via JSON.stringify para evitar RemoteObject."""
    wrapped = f"(() => {{ const __r = (() => {{ {script} }})(); return JSON.stringify(__r); }})()"
    raw = await page.evaluate(wrapped)
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


async def verificar_ou_logar(browser: Any, page: Any) -> Any:
    """Verifica se o usuário está logado ou realiza login via JavaScript.

    Usa JS direto para preencher e submeter o formulário — mais confiável
    em modo headless do que send_keys + click via CDP.
    """
    logger.info("verificando_estado_de_login")

    try:
        # Detectar se está na página de login verificando campos no DOM via JS
        tem_login = await _js(page, """
            const u = document.getElementById('Login') || document.querySelector('input[name="Login"]');
            const s = document.getElementById('Senha') || document.querySelector('input[name="Senha"]') || document.querySelector('input[type="password"]');
            return !!(u && s);
        """)

        if not tem_login:
            logger.info("usuario_ja_esta_logado_sessao_persistente")
            return page

        logger.info("campos_de_login_encontrados_tentando_autenticar")

        if not (settings.objetiva_username and settings.objetiva_password):
            logger.warning("sem_credenciais_configuradas")
            return page

        logger.info("realizando_login_automatico")

        # Preencher e submeter via JS — evita problemas de foco/eventos em headless
        resultado = await _js(page, f"""
            const usuario = document.getElementById('Login') || document.querySelector('input[name="Login"]');
            const senha = document.getElementById('Senha') || document.querySelector('input[name="Senha"]') || document.querySelector('input[type="password"]');
            const form = document.querySelector('form');

            if (!usuario || !senha) return {{ ok: false, motivo: 'campos_nao_encontrados' }};

            // Preencher valores via property setter (dispara validação do framework)
            const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            nativeInputValueSetter.call(usuario, {json.dumps(settings.objetiva_username)});
            usuario.dispatchEvent(new Event('input', {{ bubbles: true }}));
            usuario.dispatchEvent(new Event('change', {{ bubbles: true }}));

            nativeInputValueSetter.call(senha, {json.dumps(settings.objetiva_password)});
            senha.dispatchEvent(new Event('input', {{ bubbles: true }}));
            senha.dispatchEvent(new Event('change', {{ bubbles: true }}));

            if (form) {{
                form.submit();
                return {{ ok: true, metodo: 'form_submit' }};
            }}

            const btn = document.querySelector('button[type="submit"]') || document.querySelector('input[type="submit"]');
            if (btn) {{
                btn.click();
                return {{ ok: true, metodo: 'button_click' }};
            }}

            return {{ ok: false, motivo: 'form_e_botao_nao_encontrados' }};
        """)

        logger.info("login_automatico_realizado_aguardando_redirecionamento", resultado=resultado)

        # Aguardar redirect: verifica a cada 2s se saiu da página de login (até 30s)
        for _ in range(15):
            await page.sleep(2)
            url_atual = page.url or ""
            if "Account/Entrar" not in url_atual and "login" not in url_atual.lower():
                logger.info("redirect_pos_login_detectado", url=url_atual)
                return page

        logger.warning("timeout_aguardando_redirect_pos_login", url=page.url)
        return page

    except Exception as exc:
        logger.error("erro_ao_verificar_ou_logar", error=str(exc))
        return page
