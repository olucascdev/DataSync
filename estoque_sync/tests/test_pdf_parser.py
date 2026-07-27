"""Testes unitarios do vinculo entre codigo e descricao."""

import unittest
from types import SimpleNamespace

import pandas as pd

from parser.pdf_parser import (
    _deduplicar_tabelas_sobrepostas,
    _limpar_descricao_extraida,
    _parse_codigo_desc,
    _processar_tabelas,
)


class PdfParserTests(unittest.TestCase):
    def test_separa_codigo_da_descricao(self) -> None:
        self.assertEqual(
            _parse_codigo_desc("6706 FAME IN LOVE REFILLABLE PARFUM"),
            ("6706", "FAME IN LOVE REFILLABLE PARFUM"),
        )

    def test_ignora_linha_sem_codigo(self) -> None:
        self.assertEqual(_parse_codigo_desc("Total Geral"), (None, None))

    def test_codigo_separado_nao_confunde_nome_iniciado_por_numero(self) -> None:
        tabela = SimpleNamespace(
            df=pd.DataFrame(
                [
                    ["Código", "Descrição", "Marca", "Valor", "Quantidade"],
                    ["1534", "1 MILLION ELIXIR 50ML", "26 - PACO", "689,90", "0,00"],
                ]
            )
        )

        produtos = _processar_tabelas([tabela])

        self.assertEqual(produtos[0]["codigo_erp"], "1534")
        self.assertEqual(produtos[0]["descricao"], "1 MILLION ELIXIR 50ML")

    def test_codigo_e_descricao_fundidos_continuam_suportados(self) -> None:
        tabela = SimpleNamespace(
            df=pd.DataFrame(
                [
                    ["Código", "Descrição", "Marca", "Valor", "Quantidade"],
                    ["1533 POLO SPORT EDT 125ML", "", "1 - DIVERSAS", "379,80", "0,00"],
                ]
            )
        )

        produtos = _processar_tabelas([tabela])

        self.assertEqual(produtos[0]["codigo_erp"], "1533")
        self.assertEqual(produtos[0]["descricao"], "POLO SPORT EDT 125ML")

    def test_descarta_duplicata_incompleta_quando_existe_linha_completa(self) -> None:
        produtos = [
            {
                "codigo_erp": "3994",
                "descricao": "ISSEY MIYAKE A DROP DISSEY FRAICHE EDP 90ML",
                "marca": "ISSEY MIYAKE",
                "valor": "0,00",
                "quantidade": None,
                "altura": "0",
                "largura": "0",
                "peso": "0,000",
            },
            {
                "codigo_erp": "3994",
                "descricao": "ISSEY MIYAKE A DROP DISSEY FRAICHE EDP 90ML",
                "marca": "ISSEY MIYAKE",
                "valor": "799,00",
                "quantidade": "0,00",
                "altura": "0",
                "largura": "0",
                "peso": "0,000",
            },
        ]

        resultado = _deduplicar_tabelas_sobrepostas(produtos)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["valor"], "799,00")
        self.assertEqual(resultado[0]["quantidade"], "0,00")

    def test_descarta_duplicata_incompleta_depois_da_linha_completa(self) -> None:
        produtos = [
            {
                "codigo_erp": "4777",
                "descricao": "BOUCHERON QUATRE ICONIC EDP 50ML",
                "marca": "BOUCHERON PARIS",
                "valor": "685,00",
                "quantidade": "1,00",
                "altura": "0",
                "largura": "0",
                "peso": "0,000",
            },
            {
                "codigo_erp": "4777",
                "descricao": "BOUCHERON QUATRE ICONIC EDP 50ML",
                "marca": "BOUCHERON PARIS",
                "valor": "685,00",
                "quantidade": None,
                "altura": "0",
                "largura": "0",
                "peso": "0,000",
            },
        ]

        resultado = _deduplicar_tabelas_sobrepostas(produtos)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["quantidade"], "1,00")

    def test_descarta_duplicata_incompleta_com_cabecalho_contaminando_descricao(self) -> None:
        produtos = [
            {
                "codigo_erp": "4806",
                "descricao": "GUCCI FLORA GORGEOUS GARDENIA EDP 50ML + TS 1",
                "marca": "GUCCI",
                "valor": "599,90",
                "quantidade": "0,00",
                "altura": "0",
                "largura": "0",
                "peso": "0,000",
            },
            {
                "codigo_erp": "4806",
                "descricao": "GUCCI FLORA GORGEOUS GARDENIA EDP 50ML + TS 1 Código\nDescrição",
                "marca": "GUCCI",
                "valor": "599,90",
                "quantidade": "Quantidade",
                "altura": "0",
                "largura": "0",
                "peso": "0,000",
            },
        ]

        resultado = _deduplicar_tabelas_sobrepostas(produtos)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["descricao"], "GUCCI FLORA GORGEOUS GARDENIA EDP 50ML + TS 1")

    def test_limpa_artefatos_colados_na_descricao(self) -> None:
        self.assertEqual(
            _limpar_descricao_extraida(
                "CH 212 VIP ROSE EDP 80ML (+)Informações sobre os filtros Código\nDescrição"
            ),
            "CH 212 VIP ROSE EDP 80ML",
        )
        self.assertEqual(
            _limpar_descricao_extraida(
                "BOUCHERON QUATRE ICONIC EDP 100ML Altura 0 0 0 0 BOUCHERON PARIS"
            ),
            "BOUCHERON QUATRE ICONIC EDP 100ML",
        )


if __name__ == "__main__":
    unittest.main()
