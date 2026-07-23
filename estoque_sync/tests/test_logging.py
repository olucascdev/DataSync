"""Testes do formato de logs usado na VPS."""

import unittest

from app.logging_config import _console_renderer, _format_console_value


class LoggingTests(unittest.TestCase):
    def test_console_renderer_destaca_contexto_principal(self) -> None:
        linha = _console_renderer(
            None,
            "info",
            {
                "timestamp": "2026-07-23T13:30:00Z",
                "level": "info",
                "component": "scheduler.jobs",
                "event": "sync_ciclo_iniciado",
                "sync_id": "a1b2c3d4",
                "tentativa": 1,
            },
        )

        self.assertIn("INFO", linha)
        self.assertIn("scheduler.jobs", linha)
        self.assertIn("SYNC CICLO INICIADO", linha)
        self.assertIn("sync_id=a1b2c3d4", linha)
        self.assertIn("tentativa=1", linha)

    def test_console_limita_campos_muito_grandes(self) -> None:
        valor = _format_console_value("x" * 1000)

        self.assertLessEqual(len(valor), 800)
        self.assertTrue(valor.endswith("..."))


if __name__ == "__main__":
    unittest.main()
