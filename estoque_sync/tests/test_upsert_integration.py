"""Testes de integracao opcionais para a politica de escrita no PostgreSQL."""

import os
import unittest
from decimal import Decimal

import pandas as pd
import psycopg

from database.upsert import (
    RelatorioIncompletoError,
    upsert_estoque,
)
from database.sync_control import (
    adquirir_lease,
    liberar_lease,
    pode_executar_sync_inicial,
)


TEST_DSN = os.getenv("TEST_POSTGRES_DSN")


def _lote(*, estoque: str, preco: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "codigo_erp": "6706",
                "descricao": "PRODUTO TESTE",
                "marca": "MARCA",
                "saldo_fisico": Decimal(estoque),
                "valor_venda": Decimal(preco),
                "altura_cm": None,
                "largura_cm": None,
                "peso_kg": None,
            }
        ]
    )


@unittest.skipUnless(TEST_DSN, "TEST_POSTGRES_DSN nao configurado")
class UpsertIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = psycopg.connect(TEST_DSN)
        with self.conn.cursor() as cur:
            cur.execute("TRUNCATE carla_preco_divergencias, carla_produtos")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _sincronizar(self, *, estoque: str, preco: str, min_products: int = 1):
        return upsert_estoque(
            self.conn,
            _lote(estoque=estoque, preco=preco),
            price_update_interval_hours=24,
            price_max_change_percent=Decimal("30"),
            min_products=min_products,
            max_product_drop_percent=Decimal("10"),
        )

    def _produto(self):
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT saldo_fisico, valor_venda
                FROM carla_produtos
                WHERE codigo_erp = '6706'
                """
            )
            return cur.fetchone()

    def test_novo_completo_estoque_frequente_e_preco_diario(self) -> None:
        primeiro = self._sincronizar(estoque="5", preco="100")
        self.assertEqual(primeiro["inseridos"], 1)
        self.assertEqual(self._produto(), (Decimal("5.0000"), Decimal("100.00")))

        segundo = self._sincronizar(estoque="7", preco="110")
        self.assertEqual(segundo["precos_atualizados"], 0)
        self.assertEqual(self._produto(), (Decimal("7.0000"), Decimal("100.00")))

        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE carla_produtos
                SET preco_atualizado_em = NOW() - INTERVAL '25 hours'
                WHERE codigo_erp = '6706'
                """
            )
        self.conn.commit()

        terceiro = self._sincronizar(estoque="8", preco="120")
        self.assertEqual(terceiro["precos_atualizados"], 1)
        self.assertEqual(self._produto(), (Decimal("8.0000"), Decimal("120.00")))

    def test_preco_suspeito_fica_em_quarentena(self) -> None:
        self._sincronizar(estoque="5", preco="100")
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE carla_produtos
                SET preco_atualizado_em = NOW() - INTERVAL '25 hours'
                WHERE codigo_erp = '6706'
                """
            )
        self.conn.commit()

        resultado = self._sincronizar(estoque="9", preco="200")

        self.assertEqual(resultado["precos_atualizados"], 0)
        self.assertEqual(resultado["precos_bloqueados"], 1)
        self.assertEqual(self._produto(), (Decimal("9.0000"), Decimal("100.00")))
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM carla_preco_divergencias")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_lote_abaixo_do_minimo_nao_altera_banco(self) -> None:
        with self.assertRaises(RelatorioIncompletoError):
            self._sincronizar(estoque="5", preco="100", min_products=2)

        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM carla_produtos")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_produto_legado_sem_codigo_nao_bloqueia_sync_atual(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO carla_produtos (descricao, saldo_fisico, valor_venda)
                VALUES ('LEGADO', 1, 10)
                """
            )
        self.conn.commit()

        resultado = self._sincronizar(estoque="5", preco="100")

        self.assertEqual(resultado["inseridos"], 1)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE codigo_erp IS NULL),
                    COUNT(*) FILTER (WHERE codigo_erp = '6706')
                FROM carla_produtos
                """
            )
            self.assertEqual(cur.fetchone(), (1, 1))


@unittest.skipUnless(TEST_DSN, "TEST_POSTGRES_DSN nao configurado")
class SyncControlIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn1 = psycopg.connect(TEST_DSN)
        self.conn2 = psycopg.connect(TEST_DSN)
        with self.conn1.cursor() as cur:
            cur.execute("TRUNCATE carla_sync_control")
        self.conn1.commit()

    def tearDown(self) -> None:
        self.conn1.close()
        self.conn2.close()

    def test_lease_impede_segunda_instancia_e_pode_ser_liberado(self) -> None:
        self.assertTrue(adquirir_lease(self.conn1, "worker-1", 300))
        self.conn1.commit()

        self.assertFalse(adquirir_lease(self.conn2, "worker-2", 300))
        self.conn2.commit()

        liberar_lease(self.conn1, "worker-1", success=True)
        self.conn1.commit()

        self.assertTrue(adquirir_lease(self.conn2, "worker-2", 300))
        self.conn2.commit()

    def test_execucao_recente_bloqueia_sync_imediato(self) -> None:
        self.assertTrue(adquirir_lease(self.conn1, "worker-1", 300))
        self.conn1.commit()
        liberar_lease(self.conn1, "worker-1", success=True)
        self.conn1.commit()

        self.assertFalse(pode_executar_sync_inicial(self.conn2, 1800))

    def test_circuit_breaker_permanece_apos_liberar_lease(self) -> None:
        self.assertTrue(adquirir_lease(self.conn1, "worker-1", 300))
        self.conn1.commit()
        liberar_lease(
            self.conn1,
            "worker-1",
            success=False,
            failure_threshold=1,
            failure_cooldown_seconds=3600,
        )
        self.conn1.commit()

        self.assertFalse(adquirir_lease(self.conn2, "worker-2", 300))
        self.conn2.commit()
        self.assertFalse(pode_executar_sync_inicial(self.conn2, 0))


if __name__ == "__main__":
    unittest.main()
