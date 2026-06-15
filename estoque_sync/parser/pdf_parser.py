"""Parser de PDF de relatório de estoque usando Camelot.

Extrai produtos de PDFs gerados pelo sistema Objetiva Web.

Camelot (flavor="stream") retorna uma tabela por página. A estrutura das
colunas varia conforme as "Colunas à Imprimir" selecionadas no ERP, então
o parser detecta os índices dinamicamente a partir da linha de cabeçalho.

Estrutura atual (9 cols, com Peso/Altura/Largura/Marca configurados):
    col[0]=vazio  col[1]=seq+desc  col[2]=Peso  col[3]=Altura  col[4]=Largura
    col[5]=Marca("{id} - {nome}")  col[6]=Nenhum  col[7]=Valor  col[8]=Quantidade

Entre cada produto aparecem sub-linhas de filial:
    col[1]=vazio  col[5]="Filial" ou "1 - C R DA SILVEIRA BALEEIRO"  col[6]="Grade"/"U"
Essas linhas são identificadas pelo col[idx_desc] vazio.
"""

import re
from pathlib import Path

import camelot
import pandas as pd

from app.logging_config import get_logger

logger = get_logger("parser.pdf_parser")

# Strings que indicam célula vazia/sem valor
_CELULA_VAZIA = {"NENHUM", "NAN", "NONE", "-", ""}


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
    # CÓDIGO não é mapeado aqui: quando colunas são separadas, CÓDIGO é a coluna
    # vazia (seq está embutido em DESCRIÇÃO). Só entra como fallback se DESCRIÇÃO
    # não existir (PDFs antigos com Código+Descrição fundidos).
    _MAP = {
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


def _parse_seq_desc(valor: str) -> tuple[str | None, str | None]:
    """Separa seq e descrição de '{seq} {descricao}'.

    Retorna (seq, descricao) ou (None, None) se não for linha de produto.
    """
    s = valor.strip()
    if not s:
        return None, None
    partes = s.split(maxsplit=1)
    if len(partes) < 2 or not partes[0].isdigit():
        return None, None
    return partes[0], partes[1].strip()


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
        idx_marca  = struct.get("marca")
        idx_valor  = struct.get("valor")
        idx_qtd    = struct.get("qtd")
        idx_peso   = struct.get("peso")
        idx_altura = struct.get("altura")
        idx_largura= struct.get("largura")

        for _, row in df.iterrows():
            col_desc_raw = str(row.iloc[idx_desc] if idx_desc < len(row) else "").strip()
            seq, descricao = _parse_seq_desc(col_desc_raw)

            if seq is not None:
                # Nova linha de produto — salvar o anterior
                if produto_atual is not None:
                    produtos.append(produto_atual)

                produto_atual = {
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
                tem_desc = bool(col_desc_raw)

                if produto_atual["valor"] is None:
                    produto_atual["valor"] = _cell(row, idx_valor)
                if produto_atual["quantidade"] is None:
                    produto_atual["quantidade"] = _cell(row, idx_qtd)

                # Só atualizar marca se for continuação de produto (não filial)
                if produto_atual["marca"] is None and tem_desc and idx_marca is not None:
                    produto_atual["marca"] = _extrair_marca(_cell(row, idx_marca))

                # Concatenar continuação de descrição
                if tem_desc and produto_atual["valor"] is None:
                    produto_atual["descricao"] += " " + col_desc_raw

    if produto_atual is not None:
        produtos.append(produto_atual)

    return produtos


def extrair_produtos_pdf(caminho_pdf: str) -> pd.DataFrame:
    """Extrai produtos de um PDF de relatório de estoque usando Camelot.

    Returns:
        DataFrame com colunas: descricao, marca, valor, quantidade, altura, largura, peso
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
            columns=["descricao", "marca", "valor", "quantidade", "altura", "largura", "peso"]
        )

    produtos = _processar_tabelas(tables)

    logger.info("extracao_concluida", total_produtos=len(produtos))

    if not produtos:
        logger.warning("nenhum_produto_extraido_do_pdf")

    return pd.DataFrame(
        produtos,
        columns=["descricao", "marca", "valor", "quantidade", "altura", "largura", "peso"],
    )
