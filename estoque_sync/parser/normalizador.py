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


def _valores_divergentes(series: pd.Series) -> bool:
    """Retorna True quando uma coluna possui mais de um valor real no grupo."""
    return series.dropna().nunique() > 1


def _consolidar_codigos_duplicados(df: pd.DataFrame) -> pd.DataFrame:
    """Consolida repeticoes do mesmo codigo ERP vindas do relatorio.

    Repeticoes identicas sao descartadas. Quando o mesmo produto aparece em
    mais de uma linha com saldos diferentes, os saldos sao somados.
    """
    duplicados = df["codigo_erp"].duplicated(keep=False)
    if not duplicados.any():
        return df

    total_linhas_duplicadas = int(duplicados.sum())
    codigos = sorted(df.loc[duplicados, "codigo_erp"].unique().tolist())

    df_sem_repeticoes = df.drop_duplicates().copy()
    duplicados = df_sem_repeticoes["codigo_erp"].duplicated(keep=False)
    if not duplicados.any():
        logger.warning(
            "codigos_erp_duplicados_identicos_descartados",
            total_linhas_duplicadas=total_linhas_duplicadas,
            total_codigos=len(codigos),
            amostra=codigos[:10],
        )
        return df_sem_repeticoes

    colunas_consistentes = [
        "descricao",
        "marca",
        "valor_venda",
        "altura_cm",
        "largura_cm",
        "peso_kg",
    ]
    divergentes: dict[str, list[str]] = {}
    for codigo, grupo in df_sem_repeticoes.loc[duplicados].groupby("codigo_erp"):
        if any(_valores_divergentes(grupo[coluna]) for coluna in colunas_consistentes):
            divergentes.setdefault("codigos", []).append(str(codigo))

    if divergentes:
        amostra = sorted(divergentes["codigos"])[:10]
        raise ValueError(f"Codigos ERP duplicados com dados divergentes no relatorio: {amostra}")

    logger.warning(
        "codigos_erp_duplicados_consolidados",
        total_linhas_duplicadas=total_linhas_duplicadas,
        total_codigos=len(codigos),
        amostra=codigos[:10],
    )

    return (
        df_sem_repeticoes.groupby("codigo_erp", as_index=False, sort=False)
        .agg(
            {
                "descricao": "first",
                "marca": "first",
                "saldo_fisico": "sum",
                "valor_venda": "first",
                "altura_cm": "first",
                "largura_cm": "first",
                "peso_kg": "first",
            }
        )
    )


def normalizar_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza o DataFrame extraído do PDF.

    Transformações:
    - codigo_erp: chave numerica preservada como texto
    - descricao: strip() e upper()
    - valor -> valor_venda: decimal BR
    - quantidade -> saldo_fisico: decimal BR
    - altura -> altura_cm: decimal BR (opcional, pode ser None)
    - largura -> largura_cm: decimal BR (opcional, pode ser None)
    - peso -> peso_kg: decimal BR (opcional, pode ser None)

    Returns:
        DataFrame com colunas: codigo_erp, descricao, saldo_fisico,
        valor_venda, altura_cm, largura_cm, peso_kg
    """
    if df.empty:
        logger.warning("normalizar_df_dataframe_vazio")
        return pd.DataFrame(
            columns=[
                "codigo_erp", "descricao", "marca", "saldo_fisico",
                "valor_venda", "altura_cm", "largura_cm", "peso_kg",
            ]
        )

    logger.info("normalizando_dataframe", total_registros=len(df))

    df_norm = df.copy()

    colunas_obrigatorias = {"codigo_erp", "descricao", "valor", "quantidade"}
    ausentes = colunas_obrigatorias.difference(df_norm.columns)
    if ausentes:
        raise ValueError(f"Colunas obrigatorias ausentes: {sorted(ausentes)}")

    df_norm["codigo_erp"] = df_norm["codigo_erp"].astype("string").str.strip()
    codigos_invalidos = ~df_norm["codigo_erp"].str.fullmatch(r"\d+", na=False)
    if codigos_invalidos.any():
        amostra = df_norm.loc[codigos_invalidos, "codigo_erp"].head(10).tolist()
        raise ValueError(f"Codigos ERP invalidos no relatorio: {amostra}")

    descricoes_invalidas = (
        df_norm["descricao"].isna()
        | df_norm["descricao"].astype("string").str.strip().eq("")
    )
    if descricoes_invalidas.any():
        amostra = df_norm.loc[descricoes_invalidas, "codigo_erp"].head(10).tolist()
        raise ValueError(f"Descricoes vazias para os codigos: {amostra}")

    valores_invalidos = (
        df_norm["valor"].apply(_parse_decimal_brasileiro).isna()
        | df_norm["quantidade"].apply(_parse_decimal_brasileiro).isna()
    )
    if valores_invalidos.any():
        amostra = df_norm.loc[valores_invalidos, "codigo_erp"].head(10).tolist()
        detalhes = df_norm.loc[
            valores_invalidos,
            ["codigo_erp", "descricao", "valor", "quantidade"],
        ].head(10).to_dict("records")
        logger.error("valores_invalidos_no_relatorio", amostra=detalhes)
        raise ValueError(f"Preco ou estoque invalidos para os codigos: {amostra}")

    df_norm["descricao"] = df_norm["descricao"].str.strip().str.upper()
    df_norm["marca"] = df_norm["marca"].str.strip().str.upper() if "marca" in df_norm.columns else None
    df_norm["valor_venda"] = df_norm["valor"].apply(_parse_decimal_obrigatorio)
    df_norm["saldo_fisico"] = df_norm["quantidade"].apply(_parse_decimal_obrigatorio)
    precos_invalidos = df_norm["valor_venda"] <= 0
    if precos_invalidos.any():
        amostra = df_norm.loc[precos_invalidos, "codigo_erp"].head(10).tolist()
        raise ValueError(f"Precos menores ou iguais a zero para os codigos: {amostra}")

    # Colunas extras (podem ser None se o PDF não as contiver)
    for col_pdf, col_db in [("altura", "altura_cm"), ("largura", "largura_cm"), ("peso", "peso_kg")]:
        if col_pdf in df_norm.columns:
            df_norm[col_db] = df_norm[col_pdf].apply(
                lambda v: _parse_decimal_brasileiro(v) if pd.notna(v) else None
            )
        else:
            df_norm[col_db] = None

    df_norm = df_norm[
        [
            "codigo_erp", "descricao", "marca", "saldo_fisico",
            "valor_venda", "altura_cm", "largura_cm", "peso_kg",
        ]
    ]
    df_norm = _consolidar_codigos_duplicados(df_norm)

    logger.info("normalizacao_concluida", total_registros=len(df_norm))
    return df_norm
