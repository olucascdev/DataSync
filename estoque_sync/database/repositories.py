"""Repositórios de acesso a dados."""

from typing import Any

import pandas as pd
import psycopg

from config.settings import settings
from database.upsert import upsert_estoque
from app.logging_config import get_logger

logger = get_logger("database.repositories")


class EstoqueRepository:
    """Repositório para operações de estoque."""

    def __init__(self, conn: psycopg.Connection):
        """Inicializa com uma conexão ativa.

        Args:
            conn: Conexão psycopg3 ativa.
        """
        self.conn = conn

    def upsert_batch(self, df: pd.DataFrame) -> dict[str, int]:
        """Sincroniza estoque e aplica a politica diaria de preco.

        Args:
            df: DataFrame normalizado, identificado por codigo_erp.

        Returns:
            Contadores de estoque, insercoes e precos.
        """
        return upsert_estoque(
            self.conn,
            df,
            price_update_interval_hours=settings.price_update_interval_hours,
            price_max_change_percent=settings.price_max_change_percent,
            min_products=settings.sync_min_products,
            max_product_drop_percent=settings.sync_max_product_drop_percent,
        )

    def contar_registros(self) -> int:
        """Retorna o total de registros na tabela carla_produtos.

        Returns:
            Número total de produtos.
        """
        try:
            with self.conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM carla_produtos")
                row = cur.fetchone()
                return row[0] if row else 0
        except psycopg.Error as exc:
            logger.error("erro_ao_contar_registros", error=str(exc))
            raise
