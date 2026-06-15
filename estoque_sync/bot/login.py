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

        # DIAGNÓSTICO: estrutura da página de login antes de submeter
        diag = await _js(page, """
            const senha = document.getElementById('Senha') || document.querySelector('input[name="Senha"]') || document.querySelector('input[type="password"]');
            const form = senha ? senha.closest('form') : document.querySelector('form');
            const inputs = form ? Array.from(form.querySelectorAll('input')).map(i => ({ name: i.name, type: i.type, id: i.id })) : [];
            const token = form ? form.querySelector('input[name="__RequestVerificationToken"]') : null;
            const btn = form ? form.querySelector('button[type="submit"], input[type="submit"], button') : null;
            return {
                totalForms: document.forms.length,
                formAction: form ? form.getAttribute('action') : null,
                formMethod: form ? form.getAttribute('method') : null,
                temAntiForgeryToken: !!token,
                inputs: inputs,
                botaoTexto: btn ? (btn.textContent || btn.value || '').trim() : null,
            };
        """)
        logger.info("diagnostico_form_login", diag=diag)

        logger.info("realizando_login_automatico")

        # ETAPA 1: preencher as credenciais (SEM submeter ainda)
        preenchido = await _js(page, f"""
            const usuario = document.getElementById('Login') || document.querySelector('input[name="Login"]');
            const senha = document.getElementById('Senha') || document.querySelector('input[name="Senha"]') || document.querySelector('input[type="password"]');
            if (!usuario || !senha) return {{ ok: false, motivo: 'campos_nao_encontrados' }};

            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
            setter.call(usuario, {json.dumps(settings.objetiva_username)});
            usuario.dispatchEvent(new Event('input', {{ bubbles: true }}));
            usuario.dispatchEvent(new Event('change', {{ bubbles: true }}));
            setter.call(senha, {json.dumps(settings.objetiva_password)});
            senha.dispatchEvent(new Event('input', {{ bubbles: true }}));
            senha.dispatchEvent(new Event('change', {{ bubbles: true }}));

            return {{ ok: usuario.value.length > 0 && senha.value.length > 0 }};
        """)
        logger.info("credenciais_preenchidas", preenchido=preenchido)

        # ETAPA 2: aguardar o Cloudflare Turnstile resolver e preencher o token.
        # O Turnstile gera o token de forma assíncrona; submeter antes disso
        # resulta em "Captcha inválido!". Poll até 60s pelo token não-vazio.
        token_obtido = False
        for tentativa in range(30):
            estado_token = await _js(page, """
                const t = document.querySelector('input[name="cf-turnstile-response"]')
                       || document.querySelector('textarea[name="cf-turnstile-response"]')
                       || document.querySelector('input[name="g-recaptcha-response"]');
                return { temToken: !!(t && t.value && t.value.length > 0), tamanho: t ? t.value.length : -1 };
            """)
            if estado_token.get("temToken"):
                token_obtido = True
                logger.info("turnstile_token_obtido", tentativa=tentativa, tamanho=estado_token.get("tamanho"))
                break
            await page.sleep(2)

        if not token_obtido:
            logger.warning("turnstile_token_nao_obtido_submetendo_mesmo_assim")

        # ETAPA 3: clicar no botão para submeter (com o token já preenchido)
        resultado = await _js(page, """
            const senha = document.getElementById('Senha') || document.querySelector('input[name="Senha"]') || document.querySelector('input[type="password"]');
            const form = senha ? (senha.closest('form') || document.querySelector('form')) : document.querySelector('form');
            const btn = form ? form.querySelector('button[type="submit"], input[type="submit"], button') : null;
            if (btn) { btn.click(); return { ok: true, metodo: 'button_click' }; }
            if (form) { form.submit(); return { ok: true, metodo: 'form_submit' }; }
            return { ok: false, motivo: 'form_e_botao_nao_encontrados' };
        """)

        logger.info("login_submetido_aguardando_redirecionamento", resultado=resultado, token_obtido=token_obtido)

        # Aguardar redirect: verifica a cada 2s se saiu da página de login (até 30s)
        for _ in range(15):
            await page.sleep(2)
            url_atual = page.url or ""
            if url_atual and "Account/Entrar" not in url_atual and "login" not in url_atual.lower():
                logger.info("redirect_pos_login_detectado", url=url_atual)
                return page

        # DIAGNÓSTICO: login falhou — capturar mensagens de erro/validação da página
        erro_pagina = await _js(page, """
            const sel = ['.validation-summary-errors', '.field-validation-error', '.text-danger',
                         '.alert', '.alert-danger', '[role="alert"]', '.toast-message'];
            const msgs = [];
            for (const s of sel) {
                document.querySelectorAll(s).forEach(el => {
                    const t = (el.textContent || '').trim();
                    if (t) msgs.push(t);
                });
            }
            return { url: location.href, titulo: document.title, mensagens: msgs.slice(0, 10) };
        """)
        logger.warning("login_falhou_diagnostico", erro_pagina=erro_pagina)
        return page

    except Exception as exc:
        logger.error("erro_ao_verificar_ou_logar", error=str(exc))
        return page
