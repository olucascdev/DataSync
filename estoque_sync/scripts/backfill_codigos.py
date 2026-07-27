"""Associa codigo_erp aos produtos existentes por descricao exata e unica."""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from database.postgres import close_pool, get_connection  # noqa: E402
from parser.normalizador import normalizar_df  # noqa: E402
from parser.pdf_parser import extrair_produtos_pdf  # noqa: E402


def _normalizar_descricao(valor: str) -> str:
    return " ".join(valor.strip().upper().split())


def _agrupar_por_descricao(registros: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grupos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for registro in registros:
        grupos[_normalizar_descricao(registro["descricao"])].append(registro)
    return grupos


def gerar_plano(caminho_pdf: str) -> dict[str, Any]:
    """Compara PDF e banco sem realizar alteracoes."""
    df = normalizar_df(extrair_produtos_pdf(caminho_pdf))
    origem = df[["codigo_erp", "descricao"]].to_dict("records")

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, descricao, codigo_erp
                FROM public.carla_produtos
                ORDER BY descricao, id
                """
            )
            banco = [
                {"id": row[0], "descricao": row[1], "codigo_erp": row[2]}
                for row in cur.fetchall()
            ]

    origem_por_desc = _agrupar_por_descricao(origem)
    banco_sem_codigo = _agrupar_por_descricao(
        [produto for produto in banco if produto["codigo_erp"] is None]
    )
    codigos_existentes = {
        str(produto["codigo_erp"])
        for produto in banco
        if produto["codigo_erp"] is not None
    }

    associacoes: list[dict[str, str]] = []
    ambiguidades: list[dict[str, Any]] = []
    codigos_ja_associados: list[str] = []

    for descricao, produtos_pdf in origem_por_desc.items():
        produtos_banco = banco_sem_codigo.get(descricao, [])
        if len(produtos_pdf) == 1 and len(produtos_banco) == 1:
            codigo = str(produtos_pdf[0]["codigo_erp"])
            if codigo in codigos_existentes:
                codigos_ja_associados.append(codigo)
                continue
            associacoes.append(
                {
                    "id": produtos_banco[0]["id"],
                    "codigo_erp": codigo,
                    "descricao": produtos_banco[0]["descricao"],
                }
            )
        elif produtos_banco and (len(produtos_pdf) > 1 or len(produtos_banco) > 1):
            ambiguidades.append(
                {
                    "descricao_normalizada": descricao,
                    "codigos_pdf": [str(item["codigo_erp"]) for item in produtos_pdf],
                    "ids_banco": [item["id"] for item in produtos_banco],
                }
            )

    ids_associados = {item["id"] for item in associacoes}
    codigos_associados = {item["codigo_erp"] for item in associacoes}

    return {
        "resumo": {
            "produtos_pdf": len(origem),
            "produtos_banco": len(banco),
            "associacoes_seguras": len(associacoes),
            "ambiguidades": len(ambiguidades),
            "sem_correspondencia_no_banco": sum(
                1
                for item in origem
                if str(item["codigo_erp"]) not in codigos_associados
                and str(item["codigo_erp"]) not in codigos_existentes
            ),
            "produtos_banco_sem_correspondencia": sum(
                1
                for item in banco
                if item["codigo_erp"] is None and item["id"] not in ids_associados
            ),
        },
        "associacoes": associacoes,
        "ambiguidades": ambiguidades,
        "codigos_ja_associados": sorted(set(codigos_ja_associados)),
    }


def aplicar_plano(plano: dict[str, Any]) -> int:
    """Aplica somente as associacoes seguras presentes no plano."""
    associacoes = plano["associacoes"]
    if not associacoes:
        return 0

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                UPDATE public.carla_produtos
                SET codigo_erp = %s
                WHERE id = %s::uuid
                  AND codigo_erp IS NULL
                """,
                [
                    (item["codigo_erp"], item["id"])
                    for item in associacoes
                ],
            )
    return len(associacoes)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Associa codigo_erp por descricao exata e unica.",
    )
    parser.add_argument("--pdf", required=True, help="Caminho do PDF de estoque")
    parser.add_argument("--report", required=True, help="Arquivo JSON de saida")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica as associacoes seguras; sem esta flag, apenas simula",
    )
    args = parser.parse_args()

    try:
        plano = gerar_plano(args.pdf)
        aplicados = aplicar_plano(plano) if args.apply else 0
        plano["modo"] = "aplicado" if args.apply else "simulacao"
        plano["registros_aplicados"] = aplicados

        caminho_relatorio = Path(args.report)
        caminho_relatorio.parent.mkdir(parents=True, exist_ok=True)
        caminho_relatorio.write_text(
            json.dumps(plano, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        print(json.dumps(plano["resumo"], ensure_ascii=False, indent=2))
        print(f"Modo: {plano['modo']}")
        print(f"Relatorio: {caminho_relatorio.resolve()}")
        return 0
    finally:
        close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
