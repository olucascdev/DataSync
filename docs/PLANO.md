# 📋 PLANO DEFINITIVO v4.0
## SINCRONIZAÇÃO ESTOQUE OBJETIVA → POSTGRES → N8N

---

## ✅ ESCOPO CONFIRMADO

| Origem (PDF) | Destino (PostgreSQL) | Tipo | Regra |
|--------------|----------------------|------|-------|
| **Descrição** | `descricao` | `TEXT NOT NULL` | Chave natural do UPSERT |
| **Quantidade** | `saldo_fisico` | `NUMERIC(12,4)` | `DEFAULT 0` |
| **Valor** | `valor_venda` | `NUMERIC(10,2)` | Populado pelo PDF |
| — | `marca` | `TEXT` | `NULL` (não vem do PDF) |
| — | `peso_kg` | `NUMERIC(10,3)` | `NULL` (não vem do PDF) |
| — | `altura_cm` | `NUMERIC(10,2)` | `NULL` (não vem do PDF) |
| — | `largura_cm` | `NUMERIC(10,2)` | `NULL` (não vem do PDF) |
| — | `comprimento_cm` | `NUMERIC(10,2)` | `NULL` (não vem do PDF) |
| — | `updated_at` | `TIMESTAMPTZ` | `NOW()` |

> **Decisão:** O relatório padrão já traz tudo que precisamos. **Não é necessário** acessar a aba "Colunas a imprimir". O bot gera o relatório padrão direto.

---

## 🗄️ SCHEMA — RESPEITANDO O DUMP EXISTENTE

Nenhuma alteração no schema. Usar a tabela `carla_produtos` exatamente como está:

```sql
CREATE TABLE public.carla_produtos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    marca text,
    descricao text NOT NULL,
    saldo_fisico numeric(12,4) DEFAULT 0,
    valor_venda numeric(10,2),
    peso_kg numeric(10,3),
    altura_cm numeric(10,2),
    largura_cm numeric(10,2),
    comprimento_cm numeric(10,2),
    updated_at timestamp with time zone DEFAULT now()
);
```

### Estratégia de UPSERT por `descricao`

Como não há `UNIQUE` em `descricao`, usamos **CTE com `UPDATE` + `INSERT`**:

```sql
WITH dados AS (
    SELECT 
        descricao,
        saldo_fisico,
        valor_venda
    FROM staging_temp
),
atualizados AS (
    UPDATE carla_produtos p
    SET 
        saldo_fisico = d.saldo_fisico,
        valor_venda = d.valor_venda,
        updated_at = NOW()
    FROM dados d
    WHERE p.descricao = d.descricao
    RETURNING p.descricao
)
INSERT INTO carla_produtos (descricao, saldo_fisico, valor_venda, updated_at)
SELECT descricao, saldo_fisico, valor_venda, NOW()
FROM dados d
WHERE d.descricao NOT IN (SELECT descricao FROM atualizados);
```

---

## 🤖 FLUXO DO BOT (SIMPLIFICADO)

```
1. INICIAR NODRIVER
   └── Perfil persistente: ./data/chrome-profile
   └── Sessão ASP.NET reaproveitada

2. NAVEGAR
   └── Sidebar: Estoque → Relatório → Relatório de Estoque
   └── URL: https://carlabaleeiro.objetivaweb.app.br/Relatorio/Estoque

3. PREENCHER FILTROS
   ├── Filial: Acumular Saldo = "Todas"
   ├── Tabela de Preço: "1 - PADRAO"
   ├── Modelo: "Saldo Produto"
   └── Formato: "A4 Paisagem"

4. GERAR RELATÓRIO
   └── Clicar: [Visualizar]
   └── Aguardar nova aba (target="_blank")

5. BAIXAR PDF
   └── Aguardar download automático
   └── Salvar: downloads/estoque_{timestamp}.pdf
   └── Validar: tamanho > 0

6. FECHAR ABA PDF
   └── Manter aba principal logada
```

> **NÃO é necessário** acessar a aba "Colunas a imprimir". O relatório padrão já traz Descrição + Valor + Quantidade.

---

## 📄 EXTRAÇÃO PDF

### Parser (`parser/pdf_parser.py`)

**Estrutura do PDF confirmada:**
- 116 páginas, ~6.713 produtos
- Cabeçalho fixo por página (ignorar)
- Rodapé fixo (ignorar)
- Total Geral na última página (ignorar)
- Linha de filtros na página 1 (ignorar)

**Padrão de linha:**
```
2 OCEANE ESPONJA MS FLAT BLEND VINHO        49,90    1,00
1 PRODUTO NAO CONTROLADO                     2,00   35.549,00
```

**Regex:**
```python
^\s*(\d+)\s+(.+?)\s+([\d.,]+)\s+([\d.,]+)\s*$
# Grupo 1: Número sequencial (descartar)
# Grupo 2: Descrição
# Grupo 3: Valor unitário
# Grupo 4: Quantidade (saldo)
```

**Tratamento de descrições longas:**
- Usar coordenadas Y do PyMuPDF para agrupar linhas quebradas
- Ou heuristicamente: se a linha não começa com número + espaço, é continuação da anterior

### Normalizador (`parser/normalizador.py`)

```python
def parse_decimal(valor_str: str) -> Decimal:
    # "35.549,00" → "35549.00"
    # "49,90" → "49.90"
    return Decimal(valor_str.replace(".", "").replace(",", "."))

def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(
        descricao=lambda x: x["descricao"].str.strip().str.upper(),
        saldo_fisico=lambda x: x["quantidade"].apply(parse_decimal),
        valor_venda=lambda x: x["valor"].apply(parse_decimal),
    )[["descricao", "saldo_fisico", "valor_venda"]]
```

---

## 🗄️ CAMADA DE DADOS

### Conexão (`database/postgres.py`)
- `psycopg` com pool (max 5 conexões — respeitar limites do Neon)
- Connection string via `.env`
- Retry automático

### UPSERT (`database/upsert.py`)
- `execute_values` para batch insert em tabela temporária
- CTE `UPDATE` + `INSERT` na `carla_produtos`
- Transação única (atomic)
- Log no `carla_sync_logs`

### Repositório (`database/repositories.py`)
```python
class EstoqueRepository:
    def upsert_batch(self, df: pd.DataFrame) -> dict:
        # Retorna: {"atualizados": N, "inseridos": M}
        pass
    
    def contar_registros(self) -> int:
        pass
```

---

## ⏰ SCHEDULER

### Job (`scheduler/jobs.py`)
```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler.add_job(
    sincronizar_estoque,
    "interval",
    seconds=60,
    id="sync_estoque",
    max_instances=1,  # Evita sobreposição
    replace_existing=True,
)
```

### Fluxo do Job
```
1. Log: sync_start → carla_sync_logs
2. Iniciar navegador (reutilizar sessão)
3. Navegar e gerar PDF
4. Baixar PDF
5. Log: pdf_downloaded
6. Extrair dados (PyMuPDF)
7. Log: pdf_parsed (quantidade)
8. Normalizar DataFrame
9. UPSERT em lote
10. Log: upsert_finished (atualizados, inseridos)
11. Limpar PDF temporário
12. Log: sync_completed
```

---

## 📝 LOGS

### Arquivo JSON (`logs/estoque_sync_YYYYMMDD.jsonl`)
```json
{"evento":"sync_start","timestamp":"2026-06-11T16:30:00Z"}
{"evento":"pdf_downloaded","arquivo":"estoque_2026_06_11_163000.pdf","paginas":116,"tamanho_bytes":1234567}
{"evento":"pdf_parsed","produtos":6713,"colunas":["descricao","valor_venda","saldo_fisico"]}
{"evento":"upsert_finished","atualizados":6668,"inseridos":45,"erros":0}
{"evento":"sync_completed","duracao_segundos":42}
```

### Banco (`carla_sync_logs`)
```sql
INSERT INTO carla_sync_logs (origem, status, total_recebidos, total_atualizados, total_criados, started_at)
VALUES ('objetiva_estoque', 'concluido', 6713, 6668, 45, NOW());
```

---

## 🐳 DOCKER

### `Dockerfile`
```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    wget gnupg fonts-liberation libasound2 libatk-bridge2.0-0 \
    libgtk-3-0 libnss3 libxss1 libxtst6 xdg-utils \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/downloads /app/logs /app/data/chrome-profile

CMD ["python", "main.py"]
```

### `docker-compose.yml`
```yaml
version: '3.8'

services:
  estoque-sync:
    build: .
    environment:
      - POSTGRES_HOST=${POSTGRES_HOST}
      - POSTGRES_PORT=5432
      - POSTGRES_DB=carla_db
      - POSTGRES_USER=${POSTGRES_USER}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - OBJETIVA_URL=https://carlabaleeiro.objetivaweb.app.br
      - CHROME_HEADLESS=true
      - SYNC_INTERVAL_SECONDS=60
      - LOG_LEVEL=INFO
    volumes:
      - ./data/chrome-profile:/app/data/chrome-profile
      - ./downloads:/app/downloads
      - ./logs:/app/logs
    restart: unless-stopped
    cap_add:
      - SYS_ADMIN
    security_opt:
      - seccomp=unconfined
```

> **Nota:** Em produção (VPS), o PostgreSQL é externo (Neon). Não precisa de serviço `postgres` no compose.

---

## 📅 CRONOGRAMA FINAL

| Fase | Tempo | Entregável |
|------|-------|------------|
| 1. Scaffold | 2h | Estrutura de pastas, `requirements.txt`, `.env`, config Pydantic |
| 2. Logging | 1h | Structlog + integração `carla_sync_logs` |
| 3. Postgres | 2h | Conexão, pool, repositório, CTE UPSERT |
| 4. Parser | 3h | PyMuPDF, regex, normalizador, teste com PDF real |
| 5. Nodriver | 4h | Perfil persistente, navegação, filtros, download |
| 6. Scheduler | 1h | APScheduler, job integrado, anti-sobreposição |
| 7. Docker | 2h | Dockerfile, compose, teste local |
| 8. Testes | 2h | Integração completa, validar 6713 produtos |
| 9. Deploy | 2h | VPS, Neon, systemd ou compose |

**Total estimado:** ~19 horas de trabalho concentrado.

---

## 🎯 PRÓXIMO PASSO

Iniciar implementação: criar scaffold do projeto (pastas, configs, requirements, `.env` template) e seguir módulo por módulo.

---
