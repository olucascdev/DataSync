"""Normalizador de DataFrame de estoque.

Converte strings de valores monetários brasileiros para Decimal
e padroniza descrições. Suporta as colunas extras (altura, largura, peso)
extraídas do relatório.
"""

import pandas as pd
from decimal import Decimal, InvalidOperation

from app.logging_config import get_logger

logger = get_logger("parser.normalizador")


def _parse_decimal_brasileiro(valor: str | None) -> Decimal | None:
    """Converte string de decimal brasileiro para Decimal.

    Retorna None se o valor for None, vazio ou não parseável.

    Exemplos:
        "35.549,00" -> 35549.00
        "1,00"      -> 1.00
        "0,000"     -> 0.000
        None        -> None
    """
    if not valor or not isinstance(valor, str):
        return None

    valor = valor.strip()
    if not valor:
        return None

    try:
        return Decimal(valor.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _parse_decimal_obrigatorio(valor: str) -> Decimal:
    """Converte decimal brasileiro, levantando erro se inválido."""
    result = _parse_decimal_brasileiro(valor)
    if result is None:
        raise ValueError(f"Valor inválido para conversão decimal: {valor!r}")
    return result


def normalizar_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza o DataFrame extraído do PDF.

    Transformações:
    - descricao: strip() e upper()
    - valor -> valor_venda: decimal BR
    - quantidade -> saldo_fisico: decimal BR
    - altura -> altura_cm: decimal BR (opcional, pode ser None)
    - largura -> largura_cm: decimal BR (opcional, pode ser None)
    - peso -> peso_kg: decimal BR (opcional, pode ser None)

    Returns:
        DataFrame com colunas: descricao, saldo_fisico, valor_venda,
                                altura_cm, largura_cm, peso_kg
    """
    if df.empty:
        logger.warning("normalizar_df_dataframe_vazio")
        return pd.DataFrame(
            columns=["descricao", "marca", "saldo_fisico", "valor_venda", "altura_cm", "largura_cm", "peso_kg"]
        )

    logger.info("normalizando_dataframe", total_registros=len(df))

    df_norm = df.copy()

    # Remover linhas sem valor ou quantidade (produtos com extração incompleta)
    antes = len(df_norm)
    df_norm = df_norm.dropna(subset=["valor", "quantidade"])
    df_norm = df_norm[df_norm["valor"].str.strip().ne("") & df_norm["quantidade"].str.strip().ne("")]

    # Descartar linhas onde valor/quantidade não são decimais válidos.
    # Captura cabeçalhos de página que vazam para os dados (ex: "QUANTIDADE",
    # "VALOR") sem precisar manter uma blacklist de palavras.
    valido = df_norm["valor"].apply(lambda v: _parse_decimal_brasileiro(v) is not None) & \
             df_norm["quantidade"].apply(lambda v: _parse_decimal_brasileiro(v) is not None)
    df_norm = df_norm[valido]

    descartados = antes - len(df_norm)
    if descartados:
        logger.warning("linhas_sem_valor_descartadas", total=descartados)

    df_norm["descricao"] = df_norm["descricao"].str.strip().str.upper()
    df_norm["marca"] = df_norm["marca"].str.strip().str.upper() if "marca" in df_norm.columns else None
    df_norm["valor_venda"] = df_norm["valor"].apply(_parse_decimal_obrigatorio)
    df_norm["saldo_fisico"] = df_norm["quantidade"].apply(_parse_decimal_obrigatorio)

    # Colunas extras (podem ser None se o PDF não as contiver)
    for col_pdf, col_db in [("altura", "altura_cm"), ("largura", "largura_cm"), ("peso", "peso_kg")]:
        if col_pdf in df_norm.columns:
            df_norm[col_db] = df_norm[col_pdf].apply(
                lambda v: _parse_decimal_brasileiro(v) if pd.notna(v) else None
            )
        else:
            df_norm[col_db] = None

    df_norm = df_norm[["descricao", "marca", "saldo_fisico", "valor_venda", "altura_cm", "largura_cm", "peso_kg"]]

    # Remover duplicatas por descrição (manter última ocorrência)
    antes = len(df_norm)
    df_norm = df_norm.drop_duplicates(subset=["descricao"], keep="last")
    depois = len(df_norm)
    if antes != depois:
        logger.info("duplicatas_removidas", antes=antes, depois=depois, removidas=antes - depois)

    logger.info("normalizacao_concluida", total_registros=len(df_norm))
    return df_norm
