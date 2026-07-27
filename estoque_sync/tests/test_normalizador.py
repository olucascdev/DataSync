"""Testes das barreiras de seguranca do relatorio."""

import unittest
from decimal import Decimal

import pandas as pd

from parser.normalizador import normalizar_df


def _produto(**overrides):
    produto = {
        "codigo_erp": "6706",
        "descricao": "Fame In Love",
        "marca": "Paco",
        "valor": "1.119,90",
        "quantidade": "5,00",
        "altura": "10,00",
        "largura": "2,50",
        "peso": "0,300",
    }
    produto.update(overrides)
    return produto


class NormalizadorTests(unittest.TestCase):
    def test_preserva_codigo_e_converte_valores_brasileiros(self) -> None:
        resultado = normalizar_df(pd.DataFrame([_produto()]))

        self.assertEqual(resultado.iloc[0]["codigo_erp"], "6706")
        self.assertEqual(resultado.iloc[0]["descricao"], "FAME IN LOVE")
        self.assertEqual(resultado.iloc[0]["valor_venda"], Decimal("1119.90"))
        self.assertEqual(resultado.iloc[0]["saldo_fisico"], Decimal("5.00"))

    def test_descarta_linha_duplicada_identica(self) -> None:
        df = pd.DataFrame(
            [
                _produto(),
                _produto(),
            ]
        )

        resultado = normalizar_df(df)
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["saldo_fisico"], Decimal("5.00"))

    def test_consolida_codigo_duplicado_somando_estoque(self) -> None:
        df = pd.DataFrame(
            [
                _produto(quantidade="5,00"),
                _produto(quantidade="2,50"),
            ]
        )

        resultado = normalizar_df(df)
        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado.iloc[0]["saldo_fisico"], Decimal("7.50"))

    def test_rejeita_codigo_duplicado_com_dados_divergentes(self) -> None:
        df = pd.DataFrame(
            [
                _produto(),
                _produto(descricao="Outro produto", quantidade="2,00"),
            ]
        )

        with self.assertRaisesRegex(ValueError, "dados divergentes"):
            normalizar_df(df)

    def test_rejeita_preco_invalido_sem_atualizacao_parcial(self) -> None:
        df = pd.DataFrame([_produto(valor="VALOR")])

        with self.assertRaisesRegex(ValueError, "Preco ou estoque invalidos"):
            normalizar_df(df)

    def test_permite_mesma_descricao_com_codigos_diferentes(self) -> None:
        df = pd.DataFrame(
            [
                _produto(codigo_erp="1"),
                _produto(codigo_erp="2"),
            ]
        )

        resultado = normalizar_df(df)
        self.assertEqual(resultado["codigo_erp"].tolist(), ["1", "2"])


if __name__ == "__main__":
    unittest.main()
