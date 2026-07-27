"""Parser de PDF de relatório de estoque usando Camelot.

Extrai produtos de PDFs gerados pelo sistema Objetiva Web.

Camelot (flavor="stream") retorna uma tabela por página. A estrutura das
colunas varia conforme as "Colunas à Imprimir" selecionadas no ERP, então
o parser detecta os índices dinamicamente a partir da linha de cabeçalho.

No mesmo PDF, o Camelot pode devolver codigo e descricao em colunas separadas
ou fundidos na primeira celula. O parser suporta os dois formatos e elimina
repeticoes identicas causadas por tabelas sobrepostas.

Entre cada produto aparecem sub-linhas de filial:
    descricao vazia, marca="Filial" ou nome da filial, grade="U".
Essas linhas nao criam produtos nem sobrescrevem a descricao atual.
"""

import re
from pathlib import Path

import camelot
import pandas as pd

from app.logging_config import get_logger

logger = get_logger("parser.pdf_parser")

# Strings que indicam célula vazia/sem valor
_CELULA_VAZIA = {"NENHUM", "NAN", "NONE", "-", ""}
_DECIMAL_BR_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*(?:,\d+)?$|^\d+(?:,\d+)?$")


def _cell(row: pd.Series, i: int | None) -> str | None:
    """Retorna célula como string limpa, ou None se vazia/sem valor."""
    if i is None or i < 0 or i >= len(row):
        return None
    s = str(row.iloc[i]).strip()
    return None if s.upper() in _CELULA_VAZIA else s


def _extrair_marca(valor: str | None) -> str | None:
    """Extrai nome da marca removendo prefixo numérico do ERP.

    "3 - OCEANE"  → "OCEANE"
    "64 - REVLON" → "REVLON"
    "-"           → None
    """
    if not valor:
        return None
    nome = re.sub(r'^\d+\s*-\s*', '', valor).strip()
    return nome if nome else None


def _detectar_estrutura(df: pd.DataFrame) -> dict[str, int]:
    """Lê o cabeçalho da tabela e retorna mapa nome→índice das colunas.

    Procura a linha que contenha "VALOR" e "QUANTIDADE" e mapeia cada
    coluna conhecida pelo seu texto normalizado.
    """
    # O codigo pode vir separado ou fundido com a descricao, dependendo da
    # tabela detectada pelo Camelot.
    _MAP = {
        "CÓDIGO":    "codigo", "CODIGO": "codigo",
        "DESCRIÇÃO": "desc", "DESCRICAO": "desc",
        "MARCA":     "marca",
        "VALOR":     "valor",
        "QUANTIDADE":"qtd",
        "PESO":      "peso",
        "ALTURA":    "altura",
        "LARGURA":   "largura",
    }

    def _norm(t: str) -> str:
        return (t.strip()
                 .upper()
                 .replace("Ç", "C")
                 .replace("Ã", "A")
                 .replace("Â", "A")
                 .replace("É", "E")
                 .replace("Ê", "E"))

    for _, row in df.iterrows():
        textos = [_norm(str(v)) for v in row]
        if "VALOR" not in textos or "QUANTIDADE" not in textos:
            continue

        struct: dict[str, int] = {}
        for i, t in enumerate(textos):
            chave = _MAP.get(t)
            if chave and chave not in struct:
                struct[chave] = i

        # Fallback: CÓDIGO+DESCRIÇÃO fundidos em uma célula (PDFs sem colunas extras)
        if "desc" not in struct:
            for i, t in enumerate(textos):
                if "DESCRI" in t or "CODIGO" in t or "CÓDIGO" in t:
                    struct["desc"] = i
                    break

        if "valor" in struct and "qtd" in struct:
            return struct

    return {}


def _parse_codigo_desc(valor: str) -> tuple[str | None, str | None]:
    """Separa codigo e descricao de '{codigo} {descricao}'.

    Retorna (codigo, descricao) ou (None, None) se nao for linha de produto.
    """
    s = valor.strip()
    if not s:
        return None, None
    partes = s.split(maxsplit=1)
    if len(partes) < 2 or not partes[0].isdigit():
        return None, None
    return partes[0], partes[1].strip()


def _limpar_descricao_extraida(valor: str) -> str:
    """Remove artefatos de cabeçalho/rodapé colados à descrição pelo Camelot."""
    descricao = " ".join(str(valor).split())
    descricao = re.sub(r"\s*\(\+\)Informações sobre os filtros.*$", "", descricao, flags=re.I)
    descricao = re.sub(r"\s*Código\s+Descrição.*$", "", descricao, flags=re.I)
    descricao = re.sub(r"\s*Altura\s+(?:\d+(?:[,.]\d+)?\s*)+.*$", "", descricao, flags=re.I)
    return descricao.strip()


def _parece_decimal_brasileiro(valor: str | None) -> bool:
    """Indica se uma célula parece um número decimal brasileiro."""
    return bool(valor and _DECIMAL_BR_RE.fullmatch(str(valor).strip()))


def _produto_tem_preco_e_estoque(produto: dict) -> bool:
    """Produto extraído com valor e quantidade preenchidos corretamente."""
    return _parece_decimal_brasileiro(produto.get("valor")) and _parece_decimal_brasileiro(
        produto.get("quantidade")
    )


def _extrair_codigo_descricao(
    row: pd.Series,
    idx_codigo: int | None,
    idx_desc: int,
) -> tuple[str | None, str | None]:
    """Le codigo/descricao nos layouts separados ou fundidos do Camelot."""
    codigo_raw = _cell(row, idx_codigo)
    descricao_raw = _cell(row, idx_desc)

    if codigo_raw and codigo_raw.isdigit():
        return codigo_raw, descricao_raw

    if codigo_raw:
        codigo, descricao_codigo = _parse_codigo_desc(codigo_raw)
        if codigo:
            partes = [parte for parte in (descricao_codigo, descricao_raw) if parte]
            return codigo, " ".join(partes)

    if descricao_raw:
        return _parse_codigo_desc(descricao_raw)

    return None, None


def _ler_marca(row: pd.Series, idx_marca: int | None) -> str | None:
    """Lê a marca combinando col[idx_marca] e col[idx_marca+1] se necessário.

    Camelot às vezes quebra "65 - YVES SAINT LAURENT" em duas células:
    col[idx_marca]="65 - YVES SAINT" e col[idx_marca+1]="LAURENT".
    A coluna seguinte normalmente é "Nenhum" (vazia nos dados) — se tiver
    texto não-numérico, é overflow do nome da marca.
    """
    if idx_marca is None:
        return None
    parte1 = _cell(row, idx_marca)
    if parte1 is None:
        return None
    parte2 = _cell(row, idx_marca + 1)
    if parte2 and not parte2.replace(".", "").replace(",", "").isdigit():
        return parte1 + " " + parte2
    return parte1


def _processar_tabelas(tables: camelot.core.TableList) -> list[dict]:
    """Percorre todas as tabelas e extrai produtos usando índices dinâmicos."""
    produtos: list[dict] = []
    produto_atual: dict | None = None
    struct: dict[str, int] = {}

    for table in tables:
        df = table.df

        # Detectar estrutura no cabeçalho desta tabela (pode variar entre páginas)
        novo_struct = _detectar_estrutura(df)
        if novo_struct:
            struct = novo_struct

        if not struct:
            logger.warning("estrutura_nao_detectada_pulando_tabela")
            continue

        idx_desc   = struct.get("desc", 0)
        idx_codigo = struct.get("codigo")
        idx_marca  = struct.get("marca")
        idx_valor  = struct.get("valor")
        idx_qtd    = struct.get("qtd")
        idx_peso   = struct.get("peso")
        idx_altura = struct.get("altura")
        idx_largura= struct.get("largura")

        for _, row in df.iterrows():
            col_desc_raw = _cell(row, idx_desc) or ""
            col_codigo_raw = _cell(row, idx_codigo) or ""
            codigo_upper = col_codigo_raw.upper()
            descricao_upper = col_desc_raw.upper()
            if (
                codigo_upper in {"CÓDIGO", "CODIGO"}
                or descricao_upper in {"DESCRIÇÃO", "DESCRICAO"}
                or "VALOR TOTAL:" in codigo_upper
                or "VALOR TOTAL:" in descricao_upper
                or "TOTAL GERAL" in codigo_upper
                or "TOTAL GERAL" in descricao_upper
            ):
                continue

            codigo_erp, descricao = _extrair_codigo_descricao(
                row,
                idx_codigo,
                idx_desc,
            )
            if (
                descricao
                and descricao.upper().lstrip().startswith(
                    "- C R DA SILVEIRA BALEEIRO"
                )
            ):
                continue

            if codigo_erp is not None and descricao:
                # Nova linha de produto — salvar o anterior
                if produto_atual is not None:
                    produtos.append(produto_atual)

                produto_atual = {
                    "codigo_erp": codigo_erp,
                    "descricao": descricao,
                    "marca":     _extrair_marca(_ler_marca(row, idx_marca)),
                    "peso":      _cell(row, idx_peso),
                    "altura":    _cell(row, idx_altura),
                    "largura":   _cell(row, idx_largura),
                    "valor":     _cell(row, idx_valor),
                    "quantidade":_cell(row, idx_qtd),
                }

            elif produto_atual is not None:
                # Linha de continuação ou sub-linha de filial.
                # Filial: col[idx_desc] está vazio → NÃO atualizar marca.
                # Continuação de produto: col[idx_desc] tem texto.
                texto_continuacao = col_desc_raw
                if not texto_continuacao and col_codigo_raw:
                    texto_continuacao = col_codigo_raw
                tem_desc = bool(texto_continuacao)

                if produto_atual["valor"] is None:
                    produto_atual["valor"] = _cell(row, idx_valor)
                if produto_atual["quantidade"] is None:
                    produto_atual["quantidade"] = _cell(row, idx_qtd)

                # Só atualizar marca se for continuação de produto (não filial)
                if produto_atual["marca"] is None and tem_desc and idx_marca is not None:
                    produto_atual["marca"] = _extrair_marca(_cell(row, idx_marca))

                # Concatenar continuação de descrição
                if tem_desc:
                    produto_atual["descricao"] += " " + texto_continuacao

                # Algumas marcas longas ocupam uma segunda linha exclusiva.
                marca_continuacao = _cell(row, idx_marca)
                proxima_celula = _cell(row, idx_marca + 1) if idx_marca is not None else None
                if (
                    not tem_desc
                    and marca_continuacao
                    and not proxima_celula
                    and _cell(row, idx_valor) is None
                    and _cell(row, idx_qtd) is None
                    and marca_continuacao.upper() != "FILIAL"
                ):
                    if produto_atual["marca"]:
                        produto_atual["marca"] += " " + marca_continuacao
                    else:
                        produto_atual["marca"] = _extrair_marca(marca_continuacao)

    if produto_atual is not None:
        produtos.append(produto_atual)

    return produtos


def _deduplicar_tabelas_sobrepostas(produtos: list[dict]) -> list[dict]:
    """Remove repeticoes incompletas/identicas geradas por tabelas sobrepostas."""
    resultado: list[dict] = []
    indice_por_chave_completa: dict[tuple[str, str, str, str], int] = {}
    indices_incompletos_por_produto: dict[tuple[str, str], list[int]] = {}
    indices_incompletos_por_codigo: dict[str, list[int]] = {}
    produtos_completos: set[tuple[str, str]] = set()
    codigos_completos: set[str] = set()
    incompletos_removidos = 0
    identicos_removidos = 0

    for produto in produtos:
        produto = produto.copy()
        produto["descricao"] = _limpar_descricao_extraida(str(produto["descricao"]))
        codigo = str(produto["codigo_erp"])
        chave_produto = (
            codigo,
            " ".join(str(produto["descricao"]).upper().split()),
        )
        chave_completa = (
            *chave_produto,
            str(produto["valor"]),
            str(produto["quantidade"]),
        )

        produto_completo = _produto_tem_preco_e_estoque(produto)
        if produto_completo:
            produtos_completos.add(chave_produto)
            codigos_completos.add(codigo)
            for indice in indices_incompletos_por_produto.pop(chave_produto, []):
                if resultado[indice] is not None:
                    resultado[indice] = None
                    incompletos_removidos += 1
            for indice in indices_incompletos_por_codigo.pop(codigo, []):
                if resultado[indice] is not None:
                    resultado[indice] = None
                    incompletos_removidos += 1
        elif chave_produto in produtos_completos or codigo in codigos_completos:
            incompletos_removidos += 1
            continue

        indice = indice_por_chave_completa.get(chave_completa)
        if indice is None:
            indice_por_chave_completa[chave_completa] = len(resultado)
            if not produto_completo:
                indices_incompletos_por_produto.setdefault(chave_produto, []).append(len(resultado))
                indices_incompletos_por_codigo.setdefault(codigo, []).append(len(resultado))
            resultado.append(produto)
            continue

        atual = resultado[indice]
        if atual is None:
            resultado[indice] = produto
            continue

        preenchidos_atual = sum(valor is not None for valor in atual.values())
        preenchidos_novo = sum(valor is not None for valor in produto.values())
        if preenchidos_novo > preenchidos_atual:
            resultado[indice] = produto
        else:
            identicos_removidos += 1

    if incompletos_removidos or identicos_removidos:
        logger.warning(
            "produtos_duplicados_do_pdf_descartados",
            incompletos=incompletos_removidos,
            identicos=identicos_removidos,
        )

    return [produto for produto in resultado if produto is not None]


def extrair_produtos_pdf(caminho_pdf: str) -> pd.DataFrame:
    """Extrai produtos de um PDF de relatório de estoque usando Camelot.

    Returns:
        DataFrame com colunas: codigo_erp, descricao, marca, valor,
        quantidade, altura, largura, peso
    """
    caminho = Path(caminho_pdf)
    if not caminho.exists():
        raise FileNotFoundError(f"PDF não encontrado: {caminho}")

    logger.info("abrindo_pdf_para_extracao", caminho=str(caminho))

    tables = camelot.read_pdf(
        str(caminho),
        pages="all",
        flavor="stream",
        edge_tol=500,
        row_tol=5,
    )

    logger.info("tabelas_detectadas", total=len(tables))

    if not tables:
        logger.warning("nenhuma_tabela_detectada_no_pdf")
        return pd.DataFrame(
            columns=[
                "codigo_erp", "descricao", "marca", "valor", "quantidade",
                "altura", "largura", "peso",
            ]
        )

    produtos = _deduplicar_tabelas_sobrepostas(_processar_tabelas(tables))

    logger.info("extracao_concluida", total_produtos=len(produtos))

    if not produtos:
        logger.warning("nenhum_produto_extraido_do_pdf")

    return pd.DataFrame(
        produtos,
        columns=[
            "codigo_erp", "descricao", "marca", "valor", "quantidade",
            "altura", "largura", "peso",
        ],
    )
