"""Testes do contrato de autenticação."""

import unittest
from unittest.mock import AsyncMock, patch

from bot.login import LoginRejectedError, TurnstileTokenError, verificar_ou_logar
from config.settings import settings


class FakePage:
    def __init__(self, redirect_on_sleep: bool = False) -> None:
        self.url = "https://example.test/Account/Entrar"
        self.redirect_on_sleep = redirect_on_sleep

    async def sleep(self, _seconds: float) -> None:
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
