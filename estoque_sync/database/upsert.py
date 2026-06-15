"""Upsert de estoque usando CTE e tabela temporária.

Implementa o padrão staging -> UPSERT via CTE para evitar
dependência de UNIQUE constraint na coluna descricao.
"""

import pandas as pd
import psycopg

from app.logging_config import get_logger

logger = get_logger("database.upsert")


def upsert_estoque(conn: psycopg.Connection, df: pd.DataFrame) -> dict[str, int]:
    """Realiza UPSERT de produtos de estoque no banco.

    Estratégia:
    1. Cria tabela temporária staging_estoque
    2. Copia os dados do DataFrame para a staging via executemany
    3. CTE: UPDATE registros existentes + INSERT registros novos

    O DataFrame pode conter as colunas opcionais altura_cm, largura_cm, peso_kg.
    Se presentes, serão atualizadas no banco (somente quando o valor não for None).

    Returns:
        Dict com chaves "atualizados" e "inseridos".
    """
    if df.empty:
        logger.warning("upsert_estoque_dataframe_vazio")
        return {"atualizados": 0, "inseridos": 0}

    logger.info("upsert_estoque_inicio", total_registros=len(df))

    def _opt(row, col):
        v = row.get(col)
        return None if v is None or (hasattr(v, '__class__') and str(v) == 'nan') or pd.isna(v) else v

    try:
        with conn.cursor() as cur:
            # 1. Criar tabela temporária com todas as colunas
            cur.execute("DROP TABLE IF EXISTS staging_estoque")
            cur.execute(
                """
                CREATE TEMP TABLE staging_estoque (
                    descricao    TEXT,
                    marca        TEXT,
                    saldo_fisico NUMERIC(12, 4),
                    valor_venda  NUMERIC(10, 2),
                    altura_cm    NUMERIC(10, 2),
                    largura_cm   NUMERIC(10, 2),
                    peso_kg      NUMERIC(10, 3)
                )
                """
            )

            records = [
                (
                    row["descricao"],
                    _opt(row, "marca"),
                    row["saldo_fisico"],
                    row["valor_venda"],
                    _opt(row, "altura_cm"),
                    _opt(row, "largura_cm"),
                    _opt(row, "peso_kg"),
                )
                for _, row in df.iterrows()
            ]
            cur.executemany(
                """
                INSERT INTO staging_estoque
                    (descricao, marca, saldo_fisico, valor_venda, altura_cm, largura_cm, peso_kg)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                records,
            )

            logger.info("staging_preenchida", registros=len(records))

            # 2. Executar CTE de UPSERT
            cur.execute(
                """
                WITH dados AS (
                    SELECT descricao, marca, saldo_fisico, valor_venda,
                           altura_cm, largura_cm, peso_kg
                    FROM staging_estoque
                ),
                atualizados AS (
                    UPDATE carla_produtos p
                    SET
                        marca        = COALESCE(d.marca,       p.marca),
                        saldo_fisico = d.saldo_fisico,
                        valor_venda  = d.valor_venda,
                        altura_cm    = COALESCE(d.altura_cm,   p.altura_cm),
                        largura_cm   = COALESCE(d.largura_cm,  p.largura_cm),
                        peso_kg      = COALESCE(d.peso_kg,     p.peso_kg),
                        updated_at   = NOW()
                    FROM dados d
                    WHERE p.descricao = d.descricao
                    RETURNING p.descricao
                ),
                inseridos AS (
                    INSERT INTO carla_produtos
                        (descricao, marca, saldo_fisico, valor_venda,
                         altura_cm, largura_cm, peso_kg, updated_at)
                    SELECT descricao, marca, saldo_fisico, valor_venda,
                           altura_cm, largura_cm, peso_kg, NOW()
                    FROM dados d
                    WHERE d.descricao NOT IN (SELECT descricao FROM atualizados)
                    RETURNING descricao
                )
                SELECT
                    (SELECT COUNT(*) FROM atualizados) AS total_atualizados,
                    (SELECT COUNT(*) FROM inseridos)   AS total_inseridos
                """
            )

            row = cur.fetchone()
            total_atualizados = row[0] if row else 0
            total_inseridos = row[1] if row else 0

            cur.execute("DROP TABLE IF EXISTS staging_estoque")

        logger.info(
            "upsert_estoque_concluido",
            atualizados=total_atualizados,
            inseridos=total_inseridos,
        )
        return {"atualizados": total_atualizados, "inseridos": total_inseridos}

    except psycopg.Error as exc:
        logger.error("upsert_estoque_erro", error=str(exc))
        conn.rollback()
        raise
    except Exception as exc:
        logger.error("upsert_estoque_erro_inesperado", error=str(exc))
        conn.rollback()
        raise
