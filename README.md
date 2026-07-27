# DataSync

Serviço de sincronização automática de estoque para a **Carla Baleeiro**. Faz scraping do relatório de estoque no ERP Objetiva Web, extrai os dados do PDF gerado e realiza upsert no banco de dados PostgreSQL.

---

## Como funciona

```
Objetiva Web (ERP)
       │
       │  Browser automatizado (nodriver/CDP)
       │  └─ Login automático por sessão
       │  └─ Preenche filtros do relatório
       │  └─ Baixa PDF gerado
       ▼
   PDF do relatório
       │
       │  Camelot (extração tabular)
       │  └─ Detecta colunas pelo cabeçalho
       │  └─ Extrai: código, descrição, marca,
       │             peso, altura, largura,
       │             valor e quantidade
       ▼
   Normalização
       │
       │  Converte decimais BR (ex: "1.234,56" → 1234.56)
       │  Padroniza textos (strip + uppercase)
       ▼
   PostgreSQL
       │
       │  Staging table → transação de estoque/preço
       │  Chave de upsert: código fixo do ERP
       ▼
   carla_produtos (tabela atualizada)
       │
       └─ carla_sync_logs (histórico de cada execução)
```

---

## Configuração

Crie um arquivo `.env` dentro de `estoque_sync/`:

```env
# Credenciais do ERP
OBJETIVA_URL=https://carlabaleeiro.objetivaweb.app.br
OBJETIVA_USERNAME=seu_usuario
OBJETIVA_PASSWORD=sua_senha

# Banco de dados
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=carla_db
POSTGRES_USER=carla
POSTGRES_PASSWORD=troque_esta_senha

# Comportamento
SYNC_INTERVAL_SECONDS=3600      # um relatório por hora
SYNC_INTERVAL_JITTER_SECONDS=300
SYNC_TIMEOUT_SECONDS=1800       # inclui extração das páginas do PDF
SYNC_MAX_ATTEMPTS=2             # tentativas por ciclo antes de falhar
SYNC_RETRY_DELAY_SECONDS=300    # cinco minutos entre tentativas
SYNC_FAILURE_THRESHOLD=3
SYNC_FAILURE_COOLDOWN_SECONDS=21600
SYNC_STARTUP_MIN_INTERVAL_SECONDS=1800
SYNC_MIN_PRODUCTS=6000
SYNC_MAX_PRODUCT_DROP_PERCENT=10
PRICE_UPDATE_INTERVAL_HOURS=24
PRICE_MAX_CHANGE_PERCENT=30
CHROME_HEADLESS=false           # true para rodar sem interface
DOWNLOAD_DIR=/app/downloads     # onde os PDFs ficam temporariamente

# Login / Cloudflare Turnstile
TURNSTILE_MAX_CLICKS=3
TURNSTILE_TOKEN_WAIT_SECONDS=15
TURNSTILE_POLL_INTERVAL_SECONDS=1
LOGIN_REDIRECT_TIMEOUT_SECONDS=30
LOGIN_DIAGNOSTICS_DIR=/app/logs

# Logs
LOG_LEVEL=INFO
LOG_FORMAT=console             # console ou json
THIRD_PARTY_LOG_LEVEL=WARNING

# Opcional
PGADMIN_EMAIL=admin@admin.com
PGADMIN_PASSWORD=senha_pgadmin
BROWSER_EXECUTABLE_PATH=/usr/bin/google-chrome-stable
```

---

## Como rodar

### Na VPS (produção)

```bash
# 1. Clonar o repositório
git clone <repo> estoque_sync && cd estoque_sync

# 2. Criar o .env a partir do exemplo
cp estoque_sync/.env.example estoque_sync/.env
nano estoque_sync/.env   # preencher credenciais

# 3. Subir somente o PostgreSQL
docker compose --env-file estoque_sync/.env up -d postgres

# 4. Aplicar a migração obrigatória
docker compose --env-file estoque_sync/.env exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < estoque_sync/database/migrations/001_codigo_erp_e_controle_sync.sql

# 5. Simular e revisar a associação dos códigos existentes
cd estoque_sync
python scripts/backfill_codigos.py \
  --pdf ../RelatorioEstoque_carlabaleeiro_2026_06_12_1326.pdf \
  --report ../docs/backfill-codigos.json

# 6. Aplicar somente as associações exatas e únicas
python scripts/backfill_codigos.py \
  --pdf ../RelatorioEstoque_carlabaleeiro_2026_06_12_1326.pdf \
  --report ../docs/backfill-codigos-aplicado.json \
  --apply
cd ..

# 7. Subir a aplicação
docker compose --env-file estoque_sync/.env up -d --build
```

Logs em tempo real:
```bash
docker compose logs -f estoque-sync
```

PgAdmin fica disponível em `http://localhost:8080` quando `PGADMIN_EMAIL` e `PGADMIN_PASSWORD` estiverem definidos no `.env`.

### Localmente

```bash
cd estoque_sync
cp .env.example .env   # preencher credenciais
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py
```

### Testes

```bash
cd estoque_sync
python -m unittest discover -s tests
```

---

## Relatório configurado no ERP

O bot preenche automaticamente o formulário em `/Relatorio/Estoque` com:

| Campo | Valor |
|---|---|
| Filial | Todas |
| Marca | Todas |
| Modelo | SALDO PRODUTO (A4 PAISAGEM) |
| Tabela de Preço | 1 - PADRAO |
| Coluna 1 | ALTURA |
| Coluna 2 | LARGURA |
| Coluna 3 | PESO |
| Coluna 4 | MARCA |

---

## Decisões técnicas

**Reutilização do browser**: o browser fica aberto entre ciclos de sincronização para reaproveitar o cookie de sessão ASP.NET do ERP. O login só é refeito quando a sessão expira.

**Primeiro sync protegido**: ao iniciar, a aplicação só executa imediatamente quando não houve outra tentativa nos últimos 30 minutos. Isso evita que reinícios em loop gerem acessos repetidos ao ERP.

**Concorrência**: um `asyncio.Lock` protege o processo e um lease em `carla_sync_control` protege contra dois containers acessando o ERP ao mesmo tempo.

**Timeout, retry e circuit breaker**: cada ciclo respeita `SYNC_TIMEOUT_SECONDS`. As tentativas têm pausa longa e, depois do limite de falhas consecutivas, novos acessos são suspensos pelo período configurado.

**Login e Turnstile**: o login verifica sessão existente antes de preencher o formulário. Quando há Cloudflare Turnstile, o bot clica no widget via CDP e aguarda o token antes de submeter. Em falhas de login, salva diagnóstico sanitizado em `LOGIN_DIAGNOSTICS_DIR`.

**Parser de PDF com Camelot**: usa `flavor="stream"` (sem bordas de grade). A estrutura de colunas é detectada dinamicamente pelo cabeçalho de cada página, suportando qualquer combinação de colunas extras selecionadas no ERP. Linhas de sub-filial que aparecem entre os produtos são automaticamente ignoradas.

**Estratégia de persistência**: `codigo_erp` é a chave única. Produto existente atualiza estoque em cada ciclo; preço só atualiza depois de 24 horas e dentro do limite de variação. Produto novo entra completo. Nome, marca e dimensões existentes não são sobrescritos pelo ciclo comum.

**Quarentena de preço**: alterações acima de `PRICE_MAX_CHANGE_PERCENT` preservam o preço atual e são registradas em `carla_preco_divergencias`.

O plano de implantação, rollback e critérios de aceite está em [docs/PLANO.md](docs/PLANO.md).

**Código e nome da marca**: o ERP exporta a marca no formato `"{id} - {nome}"` (ex: `"3 - OCEANE"`). O parser extrai somente o nome (`"OCEANE"`) removendo o prefixo numérico.

**Logs estruturados**: os logs usam `structlog` com `LOG_FORMAT=console` para leitura em VPS ou `LOG_FORMAT=json` para coleta estruturada. Cada ciclo recebe um `sync_id` e também grava o resultado em `carla_sync_logs` quando a tabela está disponível.

**Shutdown gracioso**: `SIGTERM` e `SIGINT` encerram scheduler, navegador e pool PostgreSQL de forma controlada.

---

## Dependências principais

| Pacote | Uso |
|---|---|
| `nodriver` | Automação do browser via CDP |
| `camelot-py[cv]` | Extração de tabelas de PDF |
| `psycopg[binary]` | Conexão PostgreSQL (psycopg3) |
| `psycopg_pool` | Pool de conexões PostgreSQL |
| `apscheduler` | Agendamento do job periódico |
| `pydantic-settings` | Configuração via `.env` |
| `structlog` | Logs em JSON estruturado |
| `pandas` | Manipulação do DataFrame extraído |
| `pyvirtualdisplay` | Suporte a execução do browser em ambiente Linux/headless |
