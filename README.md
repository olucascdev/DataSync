# DataSync

Serviço de sincronização automática de estoque para a **Cliente**. Faz scraping do relatório de estoque no ERP Objetiva Web, extrai os dados do PDF gerado e realiza upsert no banco de dados PostgreSQL.

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
       │  Staging table → CTE UPDATE + INSERT
       │  Chave de upsert: descrição do produto
       ▼
   cliente_produtos (tabela atualizada)
```

---

## Configuração

Crie um arquivo `.env` dentro de `estoque_sync/`:

```env
# Credenciais do ERP
OBJETIVA_USERNAME=seu_usuario
OBJETIVA_PASSWORD=sua_senha

# Banco de dados
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=nome_do_banco
POSTGRES_USER=usuario_postgres
POSTGRES_PASSWORD=senha_postgres

# Comportamento
SYNC_INTERVAL_SECONDS=3600      # intervalo entre sincronizações (padrão: 1h)
CHROME_HEADLESS=true            # false para ver o browser em ação
DOWNLOAD_DIR=/tmp/estoque_pdfs  # onde os PDFs ficam temporariamente

# Opcional
BROWSER_EXECUTABLE_PATH=/usr/bin/brave-browser  # auto-detectado se omitido
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

# 3. Subir
docker compose up -d --build
```

Logs em tempo real:
```bash
docker compose logs -f estoque-sync
```

### Localmente

```bash
cd estoque_sync
cp .env.example .env   # preencher credenciais
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py
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

**Concorrência**: um `asyncio.Lock` garante que apenas um job roda por vez. Se o scheduler disparar enquanto o anterior ainda está em execução, o novo disparo é ignorado silenciosamente.

**Parser de PDF com Camelot**: usa `flavor="stream"` (sem bordas de grade). A estrutura de colunas é detectada dinamicamente pelo cabeçalho de cada página, suportando qualquer combinação de colunas extras selecionadas no ERP. Linhas de sub-filial que aparecem entre os produtos são automaticamente ignoradas.

**Estratégia de upsert**: a tabela `carla_produtos` não tem `UNIQUE` constraint em `descricao`, então o upsert usa tabela temporária de staging + CTE com `UPDATE` retornando as linhas atualizadas e `INSERT` somente nas não encontradas.

**Código e nome da marca**: o ERP exporta a marca no formato `"{id} - {nome}"` (ex: `"3 - OCEANE"`). O parser extrai somente o nome (`"OCEANE"`) removendo o prefixo numérico.

---

## Dependências principais

| Pacote | Uso |
|---|---|
| `nodriver` | Automação do browser via CDP |
| `camelot-py[cv]` | Extração de tabelas de PDF |
| `psycopg[binary]` | Conexão PostgreSQL (psycopg3) |
| `apscheduler` | Agendamento do job periódico |
| `pydantic-settings` | Configuração via `.env` |
| `structlog` | Logs em JSON estruturado |
| `pandas` | Manipulação do DataFrame extraído |
