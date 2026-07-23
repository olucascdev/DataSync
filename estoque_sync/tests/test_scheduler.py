"""Testes de recuperação do job de sincronização."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from scheduler import jobs


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        jobs._browser = None
        jobs._sync_lock = None
        jobs._falhas_consecutivas = 0

    async def asyncTearDown(self) -> None:
        jobs._browser = None
        jobs._sync_lock = None
        jobs._falhas_consecutivas = 0

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


if __name__ == "__main__":
    unittest.main()
