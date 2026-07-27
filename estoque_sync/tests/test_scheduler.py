"""Testes de recuperação do job de sincronização."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from scheduler import jobs


async def _to_thread_direto(func, *args, **kwargs):
    """Evita deixar executor de threads aberto nos testes assincronos."""
    return func(*args, **kwargs)


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.to_thread_patch = patch.object(
            jobs.asyncio,
            "to_thread",
            new=_to_thread_direto,
        )
        self.to_thread_patch.start()
        jobs._browser = None
        jobs._sync_lock = None
        jobs._falhas_consecutivas = 0
        jobs._circuito_aberto_ate = 0.0

    async def asyncTearDown(self) -> None:
        self.to_thread_patch.stop()
        jobs._browser = None
        jobs._sync_lock = None
        jobs._falhas_consecutivas = 0
        jobs._circuito_aberto_ate = 0.0

    async def test_reinicia_navegador_e_repete_uma_vez(self) -> None:
        with (
            patch.object(jobs.settings, "sync_max_attempts", 2),
            patch.object(jobs.settings, "sync_retry_delay_seconds", 0),
            patch.object(
                jobs,
                "_sincronizar_estoque_impl",
                new=AsyncMock(side_effect=[RuntimeError("falha"), None]),
            ) as sync_mock,
            patch.object(
                jobs,
                "_reiniciar_navegador",
                new=AsyncMock(),
            ) as reiniciar_mock,
        ):
            await jobs._executar_sync_com_retry()

        self.assertEqual(sync_mock.await_count, 2)
        reiniciar_mock.assert_awaited_once()

    async def test_timeout_libera_job_e_reinicia_navegador(self) -> None:
        async def sync_lento() -> None:
            await asyncio.sleep(1)

        with (
            patch.object(jobs.settings, "sync_timeout_seconds", 0.01),
            patch.object(jobs, "_adquirir_lease_sync", return_value=True),
            patch.object(jobs, "_liberar_lease_sync"),
            patch.object(
                jobs,
                "_executar_sync_com_retry",
                new=AsyncMock(side_effect=sync_lento),
            ),
            patch.object(
                jobs,
                "_reiniciar_navegador",
                new=AsyncMock(),
            ) as reiniciar_mock,
        ):
            await jobs.sincronizar_estoque()

        reiniciar_mock.assert_awaited_once_with(motivo="sync_timeout")
        self.assertIsNotNone(jobs._sync_lock)
        self.assertFalse(jobs._sync_lock.locked())
        self.assertEqual(jobs._falhas_consecutivas, 1)

    async def test_lease_ocupado_nao_executa_sync(self) -> None:
        with (
            patch.object(jobs, "_adquirir_lease_sync", return_value=False),
            patch.object(
                jobs,
                "_executar_sync_com_retry",
                new=AsyncMock(),
            ) as executar_mock,
        ):
            await jobs.sincronizar_estoque()

        executar_mock.assert_not_awaited()

    async def test_circuit_breaker_interrompe_novos_acessos(self) -> None:
        with (
            patch.object(jobs.settings, "sync_failure_threshold", 1),
            patch.object(jobs.settings, "sync_failure_cooldown_seconds", 60),
            patch.object(jobs, "_adquirir_lease_sync", return_value=True),
            patch.object(jobs, "_liberar_lease_sync"),
            patch.object(
                jobs,
                "_executar_sync_com_retry",
                new=AsyncMock(side_effect=RuntimeError("falha")),
            ) as executar_mock,
        ):
            await jobs.sincronizar_estoque()
            await jobs.sincronizar_estoque()

        executar_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
