# Plano de sincronizacao por codigo

## Objetivo

Reduzir o risco de preco incorreto sem deixar o estoque desatualizado. O
relatorio do ERP continua sendo baixado uma unica vez por ciclo. Depois da
extracao, cada campo segue uma politica de escrita diferente:

```text
ERP / PDF (a cada 60 minutos)
        |
        v
Parser e validacao por codigo_erp
        |
        +-- produto novo: cadastra nome, marca, preco e estoque
        +-- produto existente: atualiza somente estoque
        `-- preco vencido ha 24h: atualiza se passar nas validacoes
```

Nao serao criados dois robos acessando o ERP. A separacao acontece na camada
de persistencia, evitando downloads e logins duplicados.

## Decisoes

| Assunto | Decisao |
|---|---|
| Identidade do produto | `codigo_erp`, preservado como `TEXT` |
| Frequencia do relatorio | 60 minutos, com jitter configuravel |
| Produto novo | Insere todos os campos disponiveis |
| Produto existente | Atualiza `saldo_fisico` em cada relatorio valido |
| Preco existente | Atualiza quando `preco_atualizado_em` completar 24 horas |
| Nome, marca e dimensoes existentes | Nao sao sobrescritos automaticamente |
| Preco com variacao alta | Mantem preco atual e envia para quarentena |
| Produto ausente do PDF | Nao e removido nem zerado |
| Concorrencia | Lease persistente no PostgreSQL e lock local |
| Falhas repetidas | Circuit breaker suspende novos acessos temporariamente |

## Fases de implantacao

### 1. Migracao do banco

Aplicar `estoque_sync/database/migrations/001_codigo_erp_e_controle_sync.sql`.
A migracao:

- adiciona `codigo_erp` e `preco_atualizado_em` a `carla_produtos`;
- cria indice unico parcial para codigos preenchidos;
- cria `carla_preco_divergencias` para auditoria de precos bloqueados;
- cria `carla_sync_control` para lease e controle de reinicios.

Antes da aplicacao, fazer backup de `carla_produtos`.

### 2. Associacao dos produtos existentes

Executar primeiro em modo de simulacao:

```bash
cd estoque_sync
python scripts/backfill_codigos.py \
  --pdf ../RelatorioEstoque_carlabaleeiro_2026_06_12_1326.pdf \
  --report ../docs/backfill-codigos.json
```

O script associa somente descricoes exatas e unicas. Casos ambiguos ficam no
relatorio para revisao. Depois de revisar:

```bash
python scripts/backfill_codigos.py \
  --pdf ../RelatorioEstoque_carlabaleeiro_2026_06_12_1326.pdf \
  --report ../docs/backfill-codigos-aplicado.json \
  --apply
```

O servico bloqueia a sincronizacao enquanto existir qualquer produto legado
com `codigo_erp` vazio. Isso impede que uma associacao nao resolvida seja
inserida novamente como produto novo.

### 3. Validacao do relatorio

Um ciclo inteiro falha sem alterar o banco quando ocorrer qualquer uma destas
condicoes:

- codigo vazio, nao numerico ou duplicado;
- descricao vazia;
- preco ou estoque nao numerico;
- preco menor ou igual a zero;
- quantidade de produtos abaixo de `SYNC_MIN_PRODUCTS`;
- queda maior que `SYNC_MAX_PRODUCT_DROP_PERCENT` em relacao ao cadastro com
  codigo.

Essa politica e intencionalmente "fail closed": um PDF suspeito nunca produz
uma atualizacao parcial.

### 4. Politica de escrita

A gravacao ocorre em uma unica transacao:

1. Atualiza estoque de produtos encontrados por `codigo_erp`.
2. Identifica precos que ja podem ser atualizados.
3. Bloqueia e registra variacoes acima de `PRICE_MAX_CHANGE_PERCENT`.
4. Atualiza os demais precos vencidos.
5. Insere produtos novos com todos os campos.

Reprocessar o mesmo PDF e idempotente: nao cria produtos duplicados e nao
antecipa a proxima atualizacao de preco.

### 5. Protecao do ERP

Configuracao inicial recomendada:

```env
SYNC_INTERVAL_SECONDS=3600
SYNC_INTERVAL_JITTER_SECONDS=300
SYNC_TIMEOUT_SECONDS=1800
SYNC_MAX_ATTEMPTS=2
SYNC_RETRY_DELAY_SECONDS=300
SYNC_FAILURE_THRESHOLD=3
SYNC_FAILURE_COOLDOWN_SECONDS=21600
SYNC_STARTUP_MIN_INTERVAL_SECONDS=1800
PRICE_UPDATE_INTERVAL_HOURS=24
PRICE_MAX_CHANGE_PERCENT=30
SYNC_MIN_PRODUCTS=6000
SYNC_MAX_PRODUCT_DROP_PERCENT=10
```

O sync imediato da inicializacao e ignorado quando houve outra tentativa
recente. Depois de tres ciclos com falha, o circuit breaker espera seis horas.
Erros nao disparam tentativas em sequencia com intervalo de poucos segundos.

## Implantacao segura

1. Fazer backup e aplicar a migracao.
2. Rodar o backfill sem `--apply`.
3. Revisar ambiguidades e salvar o relatorio gerado.
4. Aplicar o backfill.
5. Executar os testes automatizados.
6. Subir o servico com o preco observado nos logs.
7. Conferir dois ou tres ciclos de estoque.
8. Auditar diariamente `carla_preco_divergencias`.

## Rollback

Em caso de problema, parar o servico novo e voltar a imagem anterior. As
colunas e tabelas adicionadas podem permanecer no banco porque sao
retrocompativeis. Nao remover `codigo_erp` antes de exportar o mapeamento feito
pelo backfill.

## Criterios de aceite

- o parser retorna codigo, nome, preco e estoque da mesma linha;
- o PDF de referencia produz 6.578 produtos e 6.578 codigos unicos;
- nenhum produto existente tem o preco alterado no ciclo comum;
- produto novo entra completo;
- preco elegivel e atualizado somente depois do intervalo configurado;
- variacao suspeita nao altera o produto;
- dois processos nao geram o relatorio ao mesmo tempo;
- reiniciar o container nao causa uma sequencia de acessos ao ERP;
- PDF invalido nao altera nenhuma linha.
