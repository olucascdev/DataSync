"""Repositórios de acesso a dados."""

from typing import Any

import pandas as pd
import psycopg

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
        """Realiza UPSERT em lote de produtos de estoque.

        Args:
            df: DataFrame com colunas: descricao, saldo_fisico, valor_venda

        Returns:
            Dict com chaves "atualizados" e "inseridos".
        """
        return upsert_estoque(self.conn, df)

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
