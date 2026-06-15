# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**estoque_sync** is a Python automation service that periodically scrapes inventory reports from the Objetiva Web ERP (via browser automation with nodriver/CDP), parses the downloaded PDF, and upserts data into a PostgreSQL database. All code lives in `estoque_sync/`.

## Running the App

```bash
cd estoque_sync
python main.py                  # local dev (requires .env)
docker compose up --build       # includes Postgres
```

All imports are relative to `estoque_sync/` — run everything from that directory.

## Configuration (`.env`)

| Variable | Purpose |
|---|---|
| `OBJETIVA_USERNAME` / `OBJETIVA_PASSWORD` | ERP credentials (auto-login) |
| `POSTGRES_*` | DB connection |
| `SYNC_INTERVAL_SECONDS` | Sync cadence (default 60s) |
| `CHROME_HEADLESS` | Set `false` to watch the browser |
| `BROWSER_EXECUTABLE_PATH` | Override binary (auto-detects Brave) |
| `DOWNLOAD_DIR` | Where PDFs land temporarily |

## Architecture

```
main.py                 → asyncio entrypoint, signal handling, graceful shutdown
scheduler/jobs.py       → APScheduler interval job + global browser state (_browser global)
bot/
  navegador.py          → nodriver browser init, download-path prefs, Brave auto-detect
  login.py              → session detection + auto-login (form fill)
  relatorio.py          → ERP form fill + PDF download (main complexity lives here)
parser/
  pdf_parser.py         → PyMuPDF text extraction, token-from-right parsing
  normalizador.py       → BR decimal parsing, DataFrame column mapping
database/
  postgres.py           → psycopg3 ConnectionPool singleton (get_connection context manager)
  repositories.py       → EstoqueRepository.upsert_batch()
  upsert.py             → staging temp table + CTE UPDATE+INSERT
app/logging_config.py   → structlog JSON setup + log_sync_to_db()
config/settings.py      → pydantic-settings, all env vars
```

## Key Design Decisions

**Browser reuse**: `_browser` global in `jobs.py` keeps a single nodriver instance across sync cycles to reuse the ASP.NET session cookie. Login is only triggered when session is lost.

**Concurrency guard**: `asyncio.Lock` (`_sync_lock`) — if a job is already running when the scheduler fires, the new invocation is silently skipped.

**Thread isolation**: psycopg3 and PyMuPDF are synchronous; they run inside `asyncio.to_thread()`.

**UPSERT strategy**: `carla_produtos.descricao` has no `UNIQUE` constraint → staging temp table + CTE (`UPDATE` returning matched rows, then `INSERT` where not matched). See `database/upsert.py`.

**`_js()` wrapper in relatorio.py**: `page.evaluate(..., return_by_value=True)` in this nodriver version returns a `RemoteObject` (not a Python dict) for complex JS objects. The wrapper wraps the JS return in `JSON.stringify()` so nodriver returns a plain string, then `json.loads()` in Python. **Never call `page.evaluate()` directly with complex return types — always go through `_js()`.**

## Database Schema

```sql
-- Main table (upsert key: descricao)
CREATE TABLE carla_produtos (
    id uuid DEFAULT gen_random_uuid(),
    marca text,
    descricao text NOT NULL,
    saldo_fisico numeric(12,4) DEFAULT 0,
    valor_venda numeric(10,2),
    peso_kg numeric(10,3),
    altura_cm numeric(10,2),
    largura_cm numeric(10,2),
    comprimento_cm numeric(10,2),
    updated_at timestamptz DEFAULT now()
);

-- Sync audit log
CREATE TABLE carla_sync_logs (
    id uuid DEFAULT gen_random_uuid(),
    origem text NOT NULL,
    status text DEFAULT 'iniciado',
    total_recebidos integer DEFAULT 0,
    total_criados integer DEFAULT 0,
    total_atualizados integer DEFAULT 0,
    total_erros integer DEFAULT 0,
    detalhes jsonb DEFAULT '{}',
    started_at timestamptz DEFAULT now(),   -- NOT created_at
    finished_at timestamptz
);
```

**`carla_sync_logs` gotcha**: the table uses `started_at`/`finished_at`, not `created_at`. The INSERT in `app/logging_config.py:log_sync_to_db` must use `started_at`.

## PDF Format (Critical)

The report downloaded from `/Relatorio/Estoque` uses model **SALDO PRODUTO (A4 PAISAGEM)** with extra columns **ALTURA, LARGURA, PESO** added in the "Colunas à Imprimir" tab.

**Column order in the PDF** (confirmed from page header `CÓDIGO DESCRIÇÃO ALTURA LARGURA PESO VALOR QUANTIDADE`):
```
<seq>  <descricao>  <altura>  <largura>  <peso>  <valor_venda>  <saldo_fisico>
2659   EUDORA GLAM REFIL BASE LIQ...   0   0   0,000   69,90   2,00
```

The parser in `pdf_parser.py` uses a token-from-right approach: takes the last 5 (or 2) whitespace-separated tokens that are all valid BR decimals, and maps them in order:
```python
# With 5 columns: [altura, largura, peso, valor, saldo]
candidate[0] → altura
candidate[1] → largura
candidate[2] → peso
candidate[3] → valor   (valor_venda)
candidate[4] → quantidade  (saldo_fisico)
```

**If the PDF only has 2 numeric columns** (no extra cols selected), it falls back to `[valor, saldo]`.

BR decimal format: `35.549,00` → `35549.00` (dots = thousand separator, comma = decimal).

## ERP Form Automation (relatorio.py)

The form at `/Relatorio/Estoque` uses bootstrap-select dropdowns. All selects are manipulated via JS (not UI clicks) through `_selecionar_select_por_texto()` and `_selecionar_todos_no_select_multi()`.

**Fill order matters**: set `ModeloRelatorioId` before `TabelaPreco` — the ERP's `onChange` on Modelo can reset TabelaPreco.

**Select IDs and expected values**:
| Field | ID | Value |
|---|---|---|
| Filial | `FiliaisId` | All (multi) |
| Marca | `MarcasId` | All (multi) |
| Modelo | `ModeloRelatorioId` | `"SALDO PRODUTO (A4 PAISAGEM)"` exact |
| Tabela de Preço | `TabelaPreco` | `"1 - PADRAO"` exact |
| Coluna 1 | `Coluna1` | `"ALTURA"` (value=25) |
| Coluna 2 | `Coluna2` | `"LARGURA"` (value=26) |
| Coluna 3 | `Coluna3` | `"PESO"` (value=23) |

Always use `texto_exato=` for single-selects to avoid partial-match ambiguity (e.g. RETRATO vs PAISAGEM share the same prefix).

## Known Issues / Active Work

1. **PDF parser column order bug**: `pdf_parser.py` may currently assign `candidate[0]` as `valor` instead of `altura`. Correct mapping: `[0]=altura, [1]=largura, [2]=peso, [3]=valor, [4]=saldo`. Verify against the PDF header line `CÓDIGO DESCRIÇÃO ALTURA LARGURA PESO VALOR QUANTIDADE`.

2. **`carla_sync_logs` column mismatch**: `log_sync_to_db()` inserts into `created_at` but schema has `started_at`. Fix the INSERT in `app/logging_config.py`.

3. **PDF download flow**: After clicking Visualizar, the ERP opens a new tab (`target="_blank"`). The download relies on Chrome profile preferences (`always_open_pdf_externally: true`) + `page.set_download_path()` CDP call. If it doesn't download, the fallback is `_baixar_pdf_via_http()` which uses `browser.cookies.get_all()` (CDP) to get HttpOnly session cookies.

## Sample PDF Lines (for parser debugging)

```
2663 BATOM STICK MATTE VIVA VIDA 0 0 0,000 19,90 37,00
                                ↑ ↑  ↑     ↑     ↑
                             alt larg peso  val   qty

80/200 12/06/2026 11:45:40 ALIF CÓDIGO DESCRIÇÃO ALTURA LARGURA PESO VALOR QUANTIDADE
                                 ← page header, must be filtered out →
```

Page headers (`NN/200 HH:MM:SS ALIF CÓDIGO DESCRIÇÃO ...`) appear mid-file and must be caught by `_deve_ignorar()`. Add `"CÓDIGO DESCRIÇÃO"` and `"ALIF"` to the `_IGNORAR` list if they leak through.
