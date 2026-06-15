"""Geração e download do relatório de estoque.

Navega até a página de relatório, preenche filtros, visualiza
e baixa o PDF gerado pelo sistema Objetiva Web.
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from config.settings import settings
from app.logging_config import get_logger

logger = get_logger("bot.relatorio")

RELATORIO_URL = f"{settings.objetiva_url}/Relatorio/Estoque"

# JS compartilhado: normaliza texto removendo acentos, espaços extras e uppercasing
_JS_NORMALIZAR = """
const normalizar = (texto) => (texto || '')
    .normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g, '')
    .replace(/[\\t\\n\\r ]+/g, ' ')
    .trim()
    .toUpperCase();
const contemTodos = (texto, tokens) => tokens.every((t) => texto.includes(normalizar(t)));
const contemAlgum = (texto, tokens) => tokens.some((t) => texto.includes(normalizar(t)));
"""


async def _js(page: Any, script: str) -> Any:
    """Executa JavaScript e devolve o resultado desserializado.

    Retorna string/number/boolean via avaliação direta; para objetos
    encapsula o retorno em JSON.stringify para contornar a limitação do
    nodriver ao desserializar RemoteObjects complexos.
    """
    wrapped = f"""
    (() => {{
        {_JS_NORMALIZAR}
        const __result = (() => {{
            {script}
        }})();
        return JSON.stringify(__result);
    }})()
    """
    raw = await page.evaluate(wrapped)
    if isinstance(raw, str):
        return json.loads(raw)
    # nodriver às vezes já converte string primitiva — retornar como está
    return raw


async def _selecionar_select_por_texto(
    page: Any,
    data_id: str,
    incluir: list[str],
    excluir: list[str] | None = None,
    texto_exato: str | None = None,
) -> bool:
    """Seleciona direto no <select> e atualiza o bootstrap-select.

    Quando `texto_exato` é fornecido, faz match exato (normalizado) — mais
    seguro quando opções compartilham prefixo (ex: A4 RETRATO vs A4 PAISAGEM).
    Detecta estado atual via texto do botão e retorna sem interação (jaEstava).
    """
    script = f"""
        const dataId = {json.dumps(data_id)};
        const incluir = {json.dumps(incluir)};
        const excluir = {json.dumps(excluir or [])};
        const textoExato = {json.dumps(texto_exato)};
        const textoExatoNorm = normalizar(textoExato);

        const select = document.getElementById(dataId) || document.querySelector('select[name="' + dataId + '"]');
        const botao = document.querySelector('button[data-id="' + dataId + '"]');
        const textoAtualBotao = normalizar(botao ? botao.textContent : '');

        // Verificar se já está selecionado corretamente (jaEstava)
        if (textoAtualBotao) {{
            if (textoExatoNorm && textoAtualBotao === textoExatoNorm) {{
                return {{ selecionado: true, jaEstava: true, texto: botao.textContent.trim() }};
            }}
            if (!textoExatoNorm && contemTodos(textoAtualBotao, incluir) && !contemAlgum(textoAtualBotao, excluir)) {{
                return {{ selecionado: true, jaEstava: true, texto: botao.textContent.trim() }};
            }}
        }}

        if (!select) {{
            return {{ selecionado: false, motivo: 'select_nao_encontrado' }};
        }}

        const opcoes = Array.from(select.options);

        // Busca por texto exato primeiro
        const opcaoExata = textoExatoNorm
            ? opcoes.find((item) => normalizar(item.textContent) === textoExatoNorm)
            : null;

        // Fallback: match por incluir/excluir em texto+valor
        const opcao = opcaoExata || opcoes.find((item) => {{
            const t = normalizar(item.textContent + ' ' + item.value);
            return contemTodos(t, incluir) && !contemAlgum(t, excluir);
        }});

        if (!opcao) {{
            return {{
                selecionado: false,
                motivo: 'opcao_nao_encontrada',
                opcoes: opcoes.map((item) => item.textContent.trim()).slice(0, 30),
            }};
        }}

        // Aplicar seleção
        select.value = opcao.value;
        Array.from(select.options).forEach((item) => {{ item.selected = item === opcao; }});
        select.dispatchEvent(new Event('change', {{ bubbles: true }}));

        if (window.jQuery) {{
            window.jQuery(select).trigger('change');
            try {{
                if (typeof window.jQuery(select).selectpicker === 'function') {{
                    window.jQuery(select).selectpicker('val', opcao.value);
                    window.jQuery(select).selectpicker('refresh');
                }}
            }} catch (e) {{}}
        }}

        return {{ selecionado: true, jaEstava: false, value: opcao.value, texto: opcao.textContent.trim() }};
    """

    resultado = await _js(page, script)

    if isinstance(resultado, dict) and resultado.get("selecionado"):
        logger.info(
            "bootstrap_select_valor_definido",
            data_id=data_id,
            texto=resultado.get("texto"),
            ja_estava=resultado.get("jaEstava"),
        )
        return True

    logger.warning(
        "bootstrap_select_opcao_nao_encontrada",
        data_id=data_id,
        incluir=incluir,
        excluir=excluir or [],
        texto_exato=texto_exato,
        resultado=resultado,
    )
    return False


async def _abrir_pagina_relatorio(browser: Any) -> Any:
    """Abre a página do relatório e aguarda o formulário com retry contra DOM stale."""
    ultimo_erro = None

    for tentativa in range(1, 4):
        page = await browser.get(RELATORIO_URL)
        # Espera progressiva: dá tempo ao redirect pós-login (mais lento em headless/VPS)
        await page.sleep(2 + tentativa)

        try:
            url_atual = page.url
            titulo = await _js(page, "return document.title")
            logger.info(
                "aguardando_form_relatorio",
                tentativa=tentativa,
                url=url_atual,
                titulo=titulo,
            )

            form = await asyncio.wait_for(page.select("#form-relatorio"), timeout=30.0)
            if form:
                return page

            logger.warning(
                "form_relatorio_nao_encontrado_na_pagina",
                tentativa=tentativa,
                url=url_atual,
                titulo=titulo,
            )
        except Exception as exc:
            ultimo_erro = exc
            logger.warning(
                "falha_ao_aguardar_form_relatorio_tentando_recarregar",
                tentativa=tentativa,
                error=str(exc),
            )
            await page.sleep(1)

    raise RuntimeError(f"Formulário #form-relatorio não carregou: {ultimo_erro}")


async def _selecionar_todos_no_select_multi(page: Any, data_id: str) -> None:
    """Marca todas as opções úteis no <select multiple> e atualiza o bootstrap-select.

    Retorna sem interação se o botão já indica "TODOS/TODAS" ou se todas as
    options já estão selecionadas no DOM.
    """
    script = f"""
        const dataId = {json.dumps(data_id)};
        const botao = document.querySelector('button[data-id="' + dataId + '"]');
        const textoBotao = (botao ? botao.textContent : '').toUpperCase().replace(/[\\t\\n\\r ]+/g, ' ').trim();

        // bootstrap-select exibe "TODOS(AS)" quando tudo está marcado
        if (textoBotao.includes('TODOS') || textoBotao.includes('TODAS')) {{
            return {{ selecionado: true, jaEstava: true, textoBotao: textoBotao }};
        }}

        const select = document.getElementById(dataId) || document.querySelector('select[name="' + dataId + '"]');
        if (!select) {{
            return {{ selecionado: false, motivo: 'select_nao_encontrado' }};
        }}

        // Opções úteis: excluir disabled, value vazio e a opção MARCAR/DESMARCAR
        const opcoes = Array.from(select.options).filter((opcao) => {{
            const t = (opcao.textContent || '').toUpperCase();
            return !opcao.disabled && opcao.value !== '' && !t.includes('MARCAR/DESMARCAR');
        }});

        // Verificar se todas já estão marcadas
        if (opcoes.length > 0 && opcoes.every((o) => o.selected)) {{
            return {{ selecionado: true, jaEstava: true, total: opcoes.length }};
        }}

        // Marcar todas
        opcoes.forEach((o) => {{ o.selected = true; }});
        select.dispatchEvent(new Event('change', {{ bubbles: true }}));

        if (window.jQuery) {{
            window.jQuery(select).trigger('change');
            try {{
                if (typeof window.jQuery(select).selectpicker === 'function') {{
                    window.jQuery(select).selectpicker('selectAll');
                    window.jQuery(select).selectpicker('refresh');
                }}
            }} catch (e) {{}}
        }}

        return {{ selecionado: true, jaEstava: false, total: opcoes.length }};
    """

    resultado = await _js(page, script)

    if isinstance(resultado, dict) and resultado.get("selecionado"):
        logger.info(
            "bootstrap_select_multi_todos_definido",
            data_id=data_id,
            ja_estava=resultado.get("jaEstava"),
            total=resultado.get("total"),
            texto_botao=resultado.get("textoBotao"),
        )
        return

    raise RuntimeError(f"Não foi possível selecionar todas as opções de {data_id}: {resultado}")


async def _aguardar_download_pdf(download_dir: Path, iniciado_em: float, timeout: float = 120.0) -> str | None:
    """Aguarda um PDF real aparecer no diretório de download."""
    fim = time.time() + timeout

    while time.time() < fim:
        candidatos = []
        for arquivo in download_dir.glob("*.pdf"):
            try:
                if arquivo.stat().st_mtime >= iniciado_em and arquivo.stat().st_size > 0:
                    candidatos.append(arquivo)
            except OSError:
                continue

        if candidatos:
            mais_recente = max(candidatos, key=lambda item: item.stat().st_mtime)
            logger.info("pdf_detectado_no_diretorio_de_download", caminho=str(mais_recente))
            return str(mais_recente)

        await asyncio.sleep(1)

    return None


async def gerar_e_baixar_relatorio(browser: Any) -> str:
    """Gera e baixa o relatório de estoque em PDF.

    Fluxo:
    1. Navegar para /Relatorio/Estoque e aguardar #form-relatorio
    2. Aba Principal: Filial (todas), Marca (todas), Modelo (A4 PAISAGEM), Tabela de Preço
    3. Aba "Colunas à Imprimir": Coluna1=ALTURA, Coluna2=LARGURA, Coluna3=PESO
    4. Clicar em "Visualizar" (#btnVisualizar)
    5. Aguardar download automático do PDF

    Returns:
        Caminho absoluto do arquivo PDF salvo.
    """
    logger.info("iniciando_geracao_de_relatorio")

    try:
        # -------------------------------------------------------
        # 1. Navegar e aguardar carregamento do formulário
        # -------------------------------------------------------
        page = await _abrir_pagina_relatorio(browser)

        # Aguardar carregarFiltro() do ERP popular todos os dropdowns
        logger.info("aguardando_javascript_carregar_filtros")
        await page.sleep(5)

        logger.info("pagina_de_relatorio_carregada", url=RELATORIO_URL)

        # Configurar diretório de download via CDP antes de clicar Visualizar
        download_dir = Path(settings.download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        try:
            await page.set_download_path(download_dir.resolve())
            logger.info("diretorio_de_download_configurado", caminho=str(download_dir.resolve()))
        except Exception as exc:
            logger.warning("falha_ao_configurar_diretorio_de_download", error=str(exc))

        # -------------------------------------------------------
        # 2. Aba Principal - Preencher filtros
        # -------------------------------------------------------

        # Filial (#FiliaisId): multi-select — selecionar todas
        logger.info("configurando_filial")
        await _selecionar_todos_no_select_multi(page, "FiliaisId")

        # Marca (#MarcasId): multi-select — selecionar todas as marcas
        logger.info("configurando_marca")
        await _selecionar_todos_no_select_multi(page, "MarcasId")

        # Modelo (#ModeloRelatorioId): texto_exato para evitar match com RETRATO
        logger.info("configurando_modelo")
        if not await _selecionar_select_por_texto(
            page,
            "ModeloRelatorioId",
            incluir=["PAISAGEM"],
            excluir=["RETRATO"],
            texto_exato="SALDO PRODUTO (A4 PAISAGEM)",
        ):
            raise RuntimeError("Modelo 'SALDO PRODUTO (A4 PAISAGEM)' não encontrado")

        # Aguardar ERP processar onChange do Modelo (pode resetar TabelaPreco)
        await page.sleep(1)

        # Tabela de Preço (#TabelaPreco): texto_exato para seleção segura
        logger.info("configurando_tabela_preco")
        if not await _selecionar_select_por_texto(
            page,
            "TabelaPreco",
            incluir=["1", "PADRAO"],
            texto_exato="1 - PADRAO",
        ):
            raise RuntimeError("Tabela de Preço '1 - PADRAO' não encontrada")

        logger.info(
            "filtros_principais_preenchidos",
            filial="TODAS",
            marca="TODAS",
            tabela_preco="1 - PADRAO",
            modelo="SALDO PRODUTO (A4 PAISAGEM)",
        )

        # -------------------------------------------------------
        # 3. Aba "Colunas à Imprimir"
        # -------------------------------------------------------
        logger.info("abrindo_aba_colunas")
        aba_colunas = await asyncio.wait_for(
            page.select('a[href="#colunasImprimir"]'), timeout=10.0
        )
        if not aba_colunas:
            raise RuntimeError("Aba 'Colunas à Imprimir' não encontrada")
        await aba_colunas.click()
        await page.sleep(1)

        # Coluna 1: ALTURA (value=25)
        if not await _selecionar_select_por_texto(
            page, "Coluna1", incluir=["ALTURA"], texto_exato="ALTURA"
        ):
            raise RuntimeError("Coluna1: opção 'ALTURA' não encontrada")

        # Coluna 2: LARGURA (value=26)
        if not await _selecionar_select_por_texto(
            page, "Coluna2", incluir=["LARGURA"], texto_exato="LARGURA"
        ):
            raise RuntimeError("Coluna2: opção 'LARGURA' não encontrada")

        # Coluna 3: PESO (value=23)
        if not await _selecionar_select_por_texto(
            page, "Coluna3", incluir=["PESO"], texto_exato="PESO"
        ):
            raise RuntimeError("Coluna3: opção 'PESO' não encontrada")

        # Coluna 4: MARCA
        if not await _selecionar_select_por_texto(
            page, "Coluna4", incluir=["MARCA"], texto_exato="MARCA"
        ):
            raise RuntimeError("Coluna4: opção 'MARCA' não encontrada")

        logger.info(
            "colunas_a_imprimir_configuradas",
            coluna1="ALTURA",
            coluna2="LARGURA",
            coluna3="PESO",
            coluna4="MARCA",
        )

        # -------------------------------------------------------
        # 4. Clicar em "Visualizar"
        # -------------------------------------------------------
        botao_visualizar = await asyncio.wait_for(
            page.select("#btnVisualizar"), timeout=10.0
        )
        if not botao_visualizar:
            raise RuntimeError("Botão #btnVisualizar não encontrado")

        download_inicio = time.time()
        await botao_visualizar.click()
        logger.info("botao_visualizar_clicado")

        # -------------------------------------------------------
        # 5. Aguardar download automático
        # -------------------------------------------------------
        # Tentativa rápida: download direto configurado pelas Preferences do perfil
        await page.sleep(3)
        caminho_baixado = await _aguardar_download_pdf(download_dir, download_inicio, timeout=15.0)
        if caminho_baixado:
            return caminho_baixado

        # Verificar se abriu nova aba (target="_blank")
        abas = browser.tabs
        if len(abas) > 1:
            page_pdf = abas[-1]
            await page_pdf.activate()
            logger.info("nova_aba_detectada_para_o_pdf")
            try:
                await page_pdf.set_download_path(download_dir.resolve())
            except Exception:
                pass
        else:
            page_pdf = page
            logger.info("pdf_aberto_na_mesma_aba")

        await page_pdf.sleep(5)
        caminho_baixado = await _aguardar_download_pdf(download_dir, download_inicio, timeout=20.0)
        if caminho_baixado:
            return caminho_baixado

        # -------------------------------------------------------
        # 6. Fallback: baixar PDF via HTTP com cookies CDP
        # -------------------------------------------------------
        pdf_url = page_pdf.url
        logger.info("tentando_baixar_pdf_via_http", url=pdf_url)

        if pdf_url and (pdf_url.lower().endswith(".pdf") or "pdf" in pdf_url.lower()):
            return await _baixar_pdf_via_http(browser, pdf_url)

        try:
            embed = await asyncio.wait_for(
                page_pdf.select("embed[type='application/pdf']"), timeout=3.0
            )
            if embed:
                src = await embed.get_property("src")
                if src:
                    return await _baixar_pdf_via_http(browser, src)
        except Exception:
            pass

        try:
            object_tag = await asyncio.wait_for(
                page_pdf.select("object[data]"), timeout=3.0
            )
            if object_tag:
                data = await object_tag.get_property("data")
                if data:
                    return await _baixar_pdf_via_http(browser, data)
        except Exception:
            pass

        # Última espera longa
        caminho_baixado = await _aguardar_download_pdf(download_dir, download_inicio, timeout=120.0)
        if caminho_baixado:
            return caminho_baixado

        raise RuntimeError(
            "PDF não foi baixado automaticamente. "
            "Verifique as preferências de download do navegador e se o relatório foi gerado."
        )

    except Exception as exc:
        logger.error("erro_ao_gerar_relatorio", error=str(exc))
        raise


async def _baixar_pdf_via_http(browser: Any, url: str) -> str:
    """Baixa o PDF via HTTP reutilizando cookies da sessão (via CDP).

    Usa browser.cookies.get_all() para obter todos os cookies incluindo HttpOnly,
    que não são acessíveis via document.cookie e são essenciais para sessão ASP.NET.

    Returns:
        Caminho do arquivo PDF salvo.
    """
    logger.info("baixando_pdf_via_http", url=url)

    cookies: dict[str, str] = {}

    # Obter cookies via CDP (inclui HttpOnly — essenciais para sessão ASP.NET)
    try:
        cdp_cookies = await browser.cookies.get_all()
        for c in cdp_cookies:
            cookies[c.name] = c.value
        logger.info("cookies_obtidos_via_cdp", quantidade=len(cookies))
    except Exception as exc:
        logger.warning("falha_ao_obter_cookies_cdp", error=str(exc))

    async with httpx.AsyncClient(
        cookies=cookies,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/pdf,*/*",
        },
        timeout=60,
        follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()

        download_dir = Path(settings.download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        caminho_pdf = download_dir / f"estoque_{timestamp}.pdf"

        with open(caminho_pdf, "wb") as f:
            f.write(response.content)

    logger.info("pdf_baixado_com_sucesso_via_http", caminho=str(caminho_pdf))
    return str(caminho_pdf)
