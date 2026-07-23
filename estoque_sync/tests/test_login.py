"""Testes do contrato de autenticação."""

import unittest
from unittest.mock import AsyncMock, patch

from bot.login import (
    LoginRejectedError,
    TurnstileTokenError,
    _resolver_turnstile,
    verificar_ou_logar,
)
from config.settings import settings


class FakePage:
    def __init__(self, redirect_on_sleep: bool = False) -> None:
        self.url = "https://example.test/Account/Entrar"
        self.redirect_on_sleep = redirect_on_sleep
        self.sleep_calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        if self.redirect_on_sleep:
            self.url = "https://example.test/"


class LoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.username_patch = patch.object(
            settings,
            "objetiva_username",
            "usuario_teste",
        )
        self.password_patch = patch.object(
            settings,
            "objetiva_password",
            "senha_teste",
        )
        self.username_patch.start()
        self.password_patch.start()
        self.addCleanup(self.username_patch.stop)
        self.addCleanup(self.password_patch.stop)

    async def test_turnstile_nao_clica_quando_token_ja_existe(self) -> None:
        page = FakePage()

        with (
            patch(
                "bot.login._js",
                new=AsyncMock(return_value={"temToken": True}),
            ),
            patch(
                "bot.login._clicar_coordenada",
                new=AsyncMock(),
            ) as clicar_mock,
        ):
            resultado = await _resolver_turnstile(page)

        self.assertTrue(resultado)
        clicar_mock.assert_not_awaited()
        self.assertEqual(page.sleep_calls, [])

    async def test_turnstile_aguarda_token_sem_repetir_clique(self) -> None:
        page = FakePage()
        widget = {
            "temToken": False,
            "rect": {"x": 100, "y": 200, "w": 300, "h": 70},
        }

        with (
            patch.object(settings, "turnstile_max_clicks", 3),
            patch.object(settings, "turnstile_token_wait_seconds", 3),
            patch.object(settings, "turnstile_poll_interval_seconds", 1),
            patch(
                "bot.login._js",
                new=AsyncMock(
                    side_effect=[
                        widget,
                        widget,
                        widget,
                        {"temToken": True},
                    ]
                ),
            ),
            patch(
                "bot.login._clicar_coordenada",
                new=AsyncMock(),
            ) as clicar_mock,
        ):
            resultado = await _resolver_turnstile(page)

        self.assertTrue(resultado)
        clicar_mock.assert_awaited_once_with(page, 130, 235)
        self.assertEqual(page.sleep_calls, [1, 1, 1])

    async def test_turnstile_limita_cliques_quando_token_nao_aparece(self) -> None:
        page = FakePage()
        widget = {
            "temToken": False,
            "rect": {"x": 100, "y": 200, "w": 300, "h": 70},
        }

        with (
            patch.object(settings, "turnstile_max_clicks", 3),
            patch.object(settings, "turnstile_token_wait_seconds", 2),
            patch.object(settings, "turnstile_poll_interval_seconds", 1),
            patch(
                "bot.login._js",
                new=AsyncMock(side_effect=[widget] * 7),
            ),
            patch(
                "bot.login._clicar_coordenada",
                new=AsyncMock(),
            ) as clicar_mock,
        ):
            resultado = await _resolver_turnstile(page)

        self.assertFalse(resultado)
        self.assertEqual(clicar_mock.await_count, 3)
        self.assertEqual(page.sleep_calls, [1, 1, 1, 1, 1, 1])

    async def test_turnstile_falha_sem_clicar_quando_widget_nao_aparece(self) -> None:
        page = FakePage()
        sem_widget = {"temToken": False, "rect": None}

        with (
            patch.object(settings, "turnstile_max_clicks", 2),
            patch.object(settings, "turnstile_token_wait_seconds", 1),
            patch.object(settings, "turnstile_poll_interval_seconds", 1),
            patch(
                "bot.login._js",
                new=AsyncMock(side_effect=[sem_widget] * 3),
            ),
            patch(
                "bot.login._clicar_coordenada",
                new=AsyncMock(),
            ) as clicar_mock,
        ):
            resultado = await _resolver_turnstile(page)

        self.assertFalse(resultado)
        clicar_mock.assert_not_awaited()
        self.assertEqual(page.sleep_calls, [1, 1])

    async def test_nao_submete_formulario_sem_token_turnstile(self) -> None:
        page = FakePage()

        with (
            patch(
                "bot.login._js",
                new=AsyncMock(
                    side_effect=[
                        True,
                        {"totalForms": 1},
                        {"ok": True},
                    ]
                ),
            ) as js_mock,
            patch(
                "bot.login._resolver_turnstile",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "bot.login._salvar_diagnostico_login",
                new=AsyncMock(return_value={"screenshot": "falha.png"}),
            ) as diagnostico_mock,
        ):
            with self.assertRaises(TurnstileTokenError):
                await verificar_ou_logar(object(), page)

        self.assertEqual(js_mock.await_count, 3)
        diagnostico_mock.assert_awaited_once_with(page, "turnstile_sem_token")

    async def test_retorna_somente_depois_do_redirect_confirmado(self) -> None:
        page = FakePage(redirect_on_sleep=True)

        with (
            patch(
                "bot.login._js",
                new=AsyncMock(
                    side_effect=[
                        True,
                        {"totalForms": 1},
                        {"ok": True},
                        {"ok": True, "metodo": "button_click"},
                    ]
                ),
            ),
            patch(
                "bot.login._resolver_turnstile",
                new=AsyncMock(return_value=True),
            ),
        ):
            resultado = await verificar_ou_logar(object(), page)

        self.assertIs(resultado, page)
        self.assertEqual(page.url, "https://example.test/")

    async def test_falha_quando_formulario_nao_redireciona(self) -> None:
        page = FakePage()

        with (
            patch.object(settings, "login_redirect_timeout_seconds", 2),
            patch(
                "bot.login._js",
                new=AsyncMock(
                    side_effect=[
                        True,
                        {"totalForms": 1},
                        {"ok": True},
                        {"ok": True, "metodo": "button_click"},
                    ]
                ),
            ),
            patch(
                "bot.login._resolver_turnstile",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.login._salvar_diagnostico_login",
                new=AsyncMock(return_value={"screenshot": "falha.png"}),
            ) as diagnostico_mock,
        ):
            with self.assertRaises(LoginRejectedError):
                await verificar_ou_logar(object(), page)

        diagnostico_mock.assert_awaited_once_with(page, "login_nao_redirecionou")


if __name__ == "__main__":
    unittest.main()
