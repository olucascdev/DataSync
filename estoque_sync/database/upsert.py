"""Persistencia atomica de produtos identificados pelo codigo do ERP."""

from decimal import Decimal

import pandas as pd
import psycopg

from app.logging_config import get_logger

logger = get_logger("database.upsert")


class RelatorioIncompletoError(RuntimeError):
    """Indica que o lote parece incompleto e nao deve alterar o banco."""


class MigracaoPendenteError(RuntimeError):
    """Indica produtos legados ainda sem codigo do ERP."""


def _valor_opcional(row: pd.Series, coluna: str):
    valor = row.get(coluna)
    return None if valor is None or pd.isna(valor) else valor


def upsert_estoque(
    conn: psycopg.Connection,
    df: pd.DataFrame,
    *,
    price_update_interval_hours: int = 24,
    price_max_change_percent: Decimal | float = Decimal("30"),
    min_products: int = 1,
    max_product_drop_percent: Decimal | float = Decimal("10"),
) -> dict[str, int]:
    """Sincroniza produtos sem sobrescrever preco em todo ciclo.

    Produtos existentes sao encontrados exclusivamente por ``codigo_erp``.
    O estoque e atualizado em todo lote valido. O preco so e atualizado quando
    venceu o intervalo e sua variacao esta dentro do limite. Produtos novos
    entram com todos os campos.
    """
    if df.empty:
        raise RelatorioIncompletoError("O relatorio normalizado esta vazio")

    required = {
        "codigo_erp",
        "descricao",
        "marca",
        "saldo_fisico",
        "valor_venda",
        "altura_cm",
        "largura_cm",
        "peso_kg",
    }
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Colunas ausentes no lote normalizado: {sorted(missing)}")

    if len(df) < min_products:
        raise RelatorioIncompletoError(
            f"Relatorio possui {len(df)} produtos; minimo configurado: {min_products}"
        )

    price_limit = Decimal(str(price_max_change_percent))
    drop_limit = Decimal(str(max_product_drop_percent))

    logger.info(
        "sync_produtos_inicio",
        total_registros=len(df),
        intervalo_preco_horas=price_update_interval_hours,
        limite_variacao_preco=str(price_limit),
    )

    records = [
        (
            str(row["codigo_erp"]),
            row["descricao"],
            _valor_opcional(row, "marca"),
            row["saldo_fisico"],
            row["valor_venda"],
            _valor_opcional(row, "altura_cm"),
            _valor_opcional(row, "largura_cm"),
            _valor_opcional(row, "peso_kg"),
        )
        for _, row in df.iterrows()
    ]

    try:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE codigo_erp IS NOT NULL),
                        COUNT(*) FILTER (WHERE codigo_erp IS NULL)
                    FROM public.carla_produtos
                    """
                )
                row = cur.fetchone()
                total_cadastrados = row[0] if row else 0
                total_sem_codigo = row[1] if row else 0

                if total_sem_codigo:
                    logger.warning(
                        "produtos_legados_sem_codigo_erp_ignorados_no_sync",
                        total=total_sem_codigo,
                    )

                if total_cadastrados:
                    minimo_aceitavel = (
                        Decimal(total_cadastrados)
                        * (Decimal("100") - drop_limit)
                        / Decimal("100")
                    )
                    if Decimal(len(df)) < minimo_aceitavel:
                        raise RelatorioIncompletoError(
                            "Queda anormal na quantidade de produtos: "
                            f"recebidos={len(df)}, cadastrados={total_cadastrados}, "
                            f"limite_percentual={drop_limit}"
                        )

                cur.execute(
                    """
                    CREATE TEMP TABLE staging_estoque (
                        codigo_erp   TEXT PRIMARY KEY,
                        descricao    TEXT NOT NULL,
                        marca        TEXT,
                        saldo_fisico NUMERIC(12, 4) NOT NULL,
                        valor_venda  NUMERIC(10, 2) NOT NULL,
                        altura_cm    NUMERIC(10, 2),
                        largura_cm   NUMERIC(10, 2),
                        peso_kg      NUMERIC(10, 3)
                    ) ON COMMIT DROP
                    """
                )
                cur.executemany(
                    """
                    INSERT INTO staging_estoque
                        (codigo_erp, descricao, marca, saldo_fisico,
                         valor_venda, altura_cm, largura_cm, peso_kg)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    records,
                )

                cur.execute(
                    """
                    UPDATE public.carla_produtos AS p
                    SET saldo_fisico = d.saldo_fisico,
                        updated_at = NOW()
                    FROM staging_estoque AS d
                    WHERE p.codigo_erp = d.codigo_erp
                    RETURNING p.codigo_erp
                    """
                )
                total_estoque = len(cur.fetchall())

                cur.execute(
                    """
                    INSERT INTO public.carla_preco_divergencias
                        (produto_id, codigo_erp, valor_atual, valor_recebido,
                         variacao_percentual)
                    SELECT
                        p.id,
                        p.codigo_erp,
                        p.valor_venda,
                        d.valor_venda,
                        ABS((d.valor_venda - p.valor_venda)
                            / p.valor_venda * 100)
                    FROM public.carla_produtos AS p
                    JOIN staging_estoque AS d
                      ON d.codigo_erp = p.codigo_erp
                    WHERE (
                            p.preco_atualizado_em IS NULL
                            OR p.preco_atualizado_em
                               <= NOW() - make_interval(hours => %s)
                          )
                      AND p.valor_venda > 0
                      AND ABS((d.valor_venda - p.valor_venda)
                              / p.valor_venda * 100) > %s
                    ON CONFLICT DO NOTHING
                    RETURNING codigo_erp
                    """,
                    (price_update_interval_hours, price_limit),
                )
                total_precos_bloqueados = len(cur.fetchall())

                cur.execute(
                    """
                    UPDATE public.carla_produtos AS p
                    SET valor_venda = d.valor_venda,
                        preco_atualizado_em = NOW(),
                        updated_at = NOW()
                    FROM staging_estoque AS d
                    WHERE p.codigo_erp = d.codigo_erp
                      AND (
                            p.preco_atualizado_em IS NULL
                            OR p.preco_atualizado_em
                               <= NOW() - make_interval(hours => %s)
                          )
                      AND (
                            p.valor_venda IS NULL
                            OR p.valor_venda <= 0
                            OR ABS((d.valor_venda - p.valor_venda)
                                   / p.valor_venda * 100) <= %s
                          )
                    RETURNING p.codigo_erp
                    """,
                    (price_update_interval_hours, price_limit),
                )
                total_precos = len(cur.fetchall())

                cur.execute(
                    """
                    INSERT INTO public.carla_produtos
                        (codigo_erp, descricao, marca, saldo_fisico,
                         valor_venda, preco_atualizado_em, altura_cm,
                         largura_cm, peso_kg, updated_at)
                    SELECT
                        d.codigo_erp, d.descricao, d.marca, d.saldo_fisico,
                        d.valor_venda, NOW(), d.altura_cm, d.largura_cm,
                        d.peso_kg, NOW()
                    FROM staging_estoque AS d
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM public.carla_produtos AS p
                        WHERE p.codigo_erp = d.codigo_erp
                    )
                    RETURNING codigo_erp
                    """
                )
                total_inseridos = len(cur.fetchall())

        resultado = {
            "atualizados": total_estoque,
            "inseridos": total_inseridos,
            "precos_atualizados": total_precos,
            "precos_bloqueados": total_precos_bloqueados,
        }
        logger.info("sync_produtos_concluido", **resultado)
        return resultado

    except psycopg.Error as exc:
        logger.error("sync_produtos_erro_banco", error=str(exc))
        raise
    except Exception as exc:
        logger.error("sync_produtos_erro", error=str(exc))
        raise
