"""Gerenciamento de login no sistema Objetiva Web.

Verifica se já está logado (sessão persistente) ou realiza login.
NUNCA desloga entre execuções para manter a sessão ASP.NET.
"""

import asyncio
import json
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from time import monotonic
from typing import Any

import nodriver as uc

from config.settings import settings
from app.logging_config import get_logger

logger = get_logger("bot.login")


class LoginError(RuntimeError):
    """Erro controlado durante a autenticação no Objetiva Web."""


class TurnstileTokenError(LoginError):
    """O Cloudflare Turnstile não forneceu um token válido."""


class LoginRejectedError(LoginError):
    """O formulário foi enviado, mas o sistema não autenticou a sessão."""


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
    checkbox (lado esquerdo, centralizado verticalmente) e clica. Após cada
    clique, aguarda o ciclo de validação terminar antes de tentar novamente.
    """
    inicio = monotonic()
    max_cliques = max(1, settings.turnstile_max_clicks)
    intervalo_poll = max(0.1, settings.turnstile_poll_interval_seconds)
    espera_token = max(intervalo_poll, settings.turnstile_token_wait_seconds)
    polls_por_clique = max(1, ceil(espera_token / intervalo_poll))
    cliques_enviados = 0

    logger.info(
        "turnstile_verificacao_iniciada",
        max_cliques=max_cliques,
        espera_token_segundos=espera_token,
        intervalo_poll_segundos=intervalo_poll,
    )

    async def obter_estado() -> dict[str, Any]:
        return await _js(page, """
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

    estado = await obter_estado()
    if estado.get("temToken"):
        logger.info(
            "turnstile_token_ja_disponivel",
            duracao_segundos=round(monotonic() - inicio, 1),
        )
        return True

    for indice_clique in range(max_cliques):
        numero_clique = indice_clique + 1
        rect = estado.get("rect")

        if rect and rect["w"] > 0 and rect["h"] > 0:
            logger.info(
                "turnstile_widget_encontrado",
                clique=numero_clique,
                max_cliques=max_cliques,
            )

            # Checkbox fica ~30px da borda esquerda, centralizado na vertical.
            click_x = rect["x"] + 30
            click_y = rect["y"] + rect["h"] / 2
            try:
                await _clicar_coordenada(page, click_x, click_y)
                cliques_enviados += 1
                logger.info(
                    "turnstile_clique_enviado",
                    clique=numero_clique,
                    max_cliques=max_cliques,
                    x=round(click_x, 1),
                    y=round(click_y, 1),
                )
            except Exception as exc:
                logger.warning(
                    "turnstile_falha_ao_clicar",
                    clique=numero_clique,
                    max_cliques=max_cliques,
                    error=str(exc),
                )
        else:
            logger.warning(
                "turnstile_widget_nao_encontrado",
                rodada=numero_clique,
                max_rodadas=max_cliques,
            )

        logger.info(
            "turnstile_aguardando_token",
            clique=numero_clique,
            max_cliques=max_cliques,
            limite_segundos=espera_token,
            widget_visivel=bool(rect),
        )

        inicio_espera = monotonic()
        for numero_poll in range(1, polls_por_clique + 1):
            tempo_restante = espera_token - (numero_poll - 1) * intervalo_poll
            await page.sleep(min(intervalo_poll, tempo_restante))
            estado = await obter_estado()

            if estado.get("temToken"):
                logger.info(
                    "turnstile_token_obtido",
                    clique=numero_clique,
                    poll=numero_poll,
                    espera_apos_clique_segundos=round(monotonic() - inicio_espera, 1),
                    duracao_segundos=round(monotonic() - inicio, 1),
                )
                return True

        logger.warning(
            "turnstile_rodada_sem_token",
            clique=numero_clique,
            max_cliques=max_cliques,
            espera_segundos=espera_token,
        )

    logger.warning(
        "turnstile_token_nao_obtido",
        cliques_enviados=cliques_enviados,
        max_cliques=max_cliques,
        duracao_segundos=round(monotonic() - inicio, 1),
    )
    return False


async def _salvar_diagnostico_login(page: Any, motivo: str) -> dict[str, Any]:
    """Salva evidências da tela sem persistir credenciais ou tokens."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    diretorio = Path(settings.login_diagnostics_dir)
    prefixo = diretorio / f"login_{motivo}_{timestamp}"
    resultado: dict[str, Any] = {
        "motivo": motivo,
        "timestamp": timestamp,
        "screenshot": None,
        "html": None,
        "metadata": None,
        "erros": [],
    }

    try:
        diretorio.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("falha_ao_criar_diretorio_diagnostico_login", error=str(exc))
        resultado["erros"].append(f"diretorio: {exc}")
        return resultado

    try:
        metadata = await _js(page, """
            const seletores = [
                '.validation-summary-errors', '.field-validation-error',
                '.text-danger', '.alert', '.alert-danger',
                '[role="alert"]', '.toast-message'
            ];
            const mensagens = [];
            for (const seletor of seletores) {
                document.querySelectorAll(seletor).forEach((el) => {
                    const texto = (el.textContent || '').trim();
                    if (texto) mensagens.push(texto);
                });
            }
            const token = document.querySelector(
                'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
            );
            return {
                url: location.href,
                titulo: document.title,
                readyState: document.readyState,
                mensagens: [...new Set(mensagens)].slice(0, 10),
                temCamposLogin: !!(
                    document.querySelector('input[name="Login"]')
                    && document.querySelector('input[type="password"]')
                ),
                tamanhoTokenTurnstile: token && token.value ? token.value.length : 0,
            };
        """)
        metadata_path = prefixo.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        resultado["metadata"] = str(metadata_path)
    except Exception as exc:
        resultado["erros"].append(f"metadata: {exc}")

    try:
        await _js(page, """
            document.querySelectorAll(
                'input[type="password"], input[name="Login"], '
                + 'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], '
                + 'input[name="g-recaptcha-response"], textarea[name="g-recaptcha-response"], '
                + 'input[name="__RequestVerificationToken"]'
            ).forEach((el) => {
                el.value = '';
                el.removeAttribute('value');
                el.textContent = '';
                el.setAttribute('data-redacted', 'true');
            });
            return true;
        """)
    except Exception as exc:
        resultado["erros"].append(f"sanitizacao: {exc}")

    try:
        screenshot_path = prefixo.with_suffix(".png")
        await page.save_screenshot(
            filename=str(screenshot_path),
            format="png",
            full_page=True,
        )
        resultado["screenshot"] = str(screenshot_path)
    except Exception as exc:
        resultado["erros"].append(f"screenshot: {exc}")

    try:
        html_sanitizado = await _js(page, """
            const raiz = document.documentElement.cloneNode(true);
            raiz.querySelectorAll(
                'input[type="password"], input[name="Login"], '
                + 'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], '
                + 'input[name="g-recaptcha-response"], textarea[name="g-recaptcha-response"], '
                + 'input[name="__RequestVerificationToken"]'
            ).forEach((el) => {
                el.removeAttribute('value');
                el.textContent = '';
                el.setAttribute('data-redacted', 'true');
            });
            return '<!DOCTYPE html>\\n' + raiz.outerHTML;
        """)
        html_path = prefixo.with_suffix(".html")
        html_path.write_text(html_sanitizado or "", encoding="utf-8")
        resultado["html"] = str(html_path)
    except Exception as exc:
        resultado["erros"].append(f"html: {exc}")

    logger.warning("diagnostico_login_salvo", **resultado)
    return resultado


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
            raise LoginError("Credenciais do Objetiva Web não configuradas")

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
        logger.debug("diagnostico_form_login", diag=diag)

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
        if not preenchido.get("ok"):
            await _salvar_diagnostico_login(page, "campos_nao_preenchidos")
            raise LoginError(
                f"Não foi possível preencher as credenciais: {preenchido.get('motivo')}"
            )

        # ETAPA 2: resolver o Cloudflare Turnstile clicando no widget via CDP.
        # O Turnstile interativo só preenche o token após um clique real no
        # checkbox; submeter antes resulta em "Captcha inválido!".
        token_obtido = await _resolver_turnstile(page)
        if not token_obtido:
            await _salvar_diagnostico_login(page, "turnstile_sem_token")
            raise TurnstileTokenError(
                "Turnstile não gerou token; formulário de login não foi enviado"
            )

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
        if not resultado.get("ok"):
            await _salvar_diagnostico_login(page, "formulario_nao_submetido")
            raise LoginError(
                f"Não foi possível submeter o formulário: {resultado.get('motivo')}"
            )

        # Aguardar redirect: verifica a cada 2s se saiu da página de login (até 30s)
        tentativas_redirect = max(1, int(settings.login_redirect_timeout_seconds / 2))
        for _ in range(tentativas_redirect):
            await page.sleep(2)
            url_atual = page.url or ""
            if url_atual and "Account/Entrar" not in url_atual and "login" not in url_atual.lower():
                logger.info("redirect_pos_login_detectado", url=url_atual)
                return page

        diagnostico = await _salvar_diagnostico_login(page, "login_nao_redirecionou")
        logger.warning(
            "login_nao_redirecionou",
            screenshot=diagnostico.get("screenshot"),
            metadata=diagnostico.get("metadata"),
            html=diagnostico.get("html"),
        )
        raise LoginRejectedError(
            "Login não foi confirmado após o envio do formulário"
        )

    except asyncio.CancelledError:
        raise
    except LoginError as exc:
        logger.error("login_falhou", tipo=type(exc).__name__, error=str(exc))
        raise
    except Exception as exc:
        diagnostico = await _salvar_diagnostico_login(page, "erro_inesperado")
        logger.error(
            "erro_ao_verificar_ou_logar",
            error=str(exc),
            diagnostico=diagnostico,
            exc_info=True,
        )
        raise LoginError(f"Erro inesperado durante o login: {exc}") from exc
