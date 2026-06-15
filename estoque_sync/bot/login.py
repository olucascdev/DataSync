"""Gerenciamento de login no sistema Objetiva Web.

Verifica se já está logado (sessão persistente) ou realiza login.
NUNCA desloga entre execuções para manter a sessão ASP.NET.
"""

import asyncio
import json
from typing import Any

import nodriver as uc

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


async def _clicar_coordenada(page: Any, x: float, y: float) -> None:
    """Dispara um clique real (mousePressed + mouseReleased) via CDP nas coordenadas."""
    await page.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mouseMoved", x=x, y=y,
    ))
    await page.sleep(0.1)
    await page.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mousePressed", x=x, y=y,
        button=uc.cdp.input_.MouseButton.LEFT, click_count=1, buttons=1,
    ))
    await page.send(uc.cdp.input_.dispatch_mouse_event(
        type_="mouseReleased", x=x, y=y,
        button=uc.cdp.input_.MouseButton.LEFT, click_count=1, buttons=1,
    ))


async def _resolver_turnstile(page: Any) -> bool:
    """Resolve o Cloudflare Turnstile clicando no checkbox do widget via CDP.

    O Turnstile interativo só preenche o input cf-turnstile-response após um
    clique real no checkbox. Localiza o iframe do widget, calcula a posição do
    checkbox (lado esquerdo, centralizado verticalmente) e clica. Faz poll do
    token entre tentativas. Retorna True quando o token é obtido.
    """
    for tentativa in range(20):
        estado = await _js(page, """
            const inp = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
            if (inp && inp.value && inp.value.length > 0) return { temToken: true };

            // Localizar o widget visível (iframe do Cloudflare ou container .cf-turnstile)
            let alvo = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (!alvo) {
                const c = document.querySelector('.cf-turnstile');
                if (c) alvo = c.querySelector('iframe') || c;
            }
            if (!alvo && inp) alvo = inp.parentElement;
            if (!alvo) return { temToken: false, rect: null };

            const r = alvo.getBoundingClientRect();
            return { temToken: false, rect: { x: r.x, y: r.y, w: r.width, h: r.height } };
        """)

        if estado.get("temToken"):
            logger.info("turnstile_token_obtido", tentativa=tentativa)
            return True

        rect = estado.get("rect")
        if rect and rect["w"] > 0 and rect["h"] > 0:
            # Checkbox fica ~30px da borda esquerda, centralizado na vertical
            click_x = rect["x"] + 30
            click_y = rect["y"] + rect["h"] / 2
            logger.info("turnstile_clicando_widget", tentativa=tentativa, x=click_x, y=click_y, rect=rect)
            try:
                await _clicar_coordenada(page, click_x, click_y)
            except Exception as exc:
                logger.warning("falha_ao_clicar_turnstile", error=str(exc))
        else:
            logger.info("turnstile_widget_ainda_nao_renderizado", tentativa=tentativa)

        await page.sleep(2)

    logger.warning("turnstile_token_nao_obtido_apos_tentativas")
    return False


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

        # ETAPA 2: resolver o Cloudflare Turnstile clicando no widget via CDP.
        # O Turnstile interativo só preenche o token após um clique real no
        # checkbox; submeter antes resulta em "Captcha inválido!".
        token_obtido = await _resolver_turnstile(page)

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
