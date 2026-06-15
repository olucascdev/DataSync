# 🤖 Plano de Automação — Relatório de Estoque

**URL Alvo:** `https://carlabaleeiro.objetivaweb.app.br/Relatorio/Estoque`

---

## Passo 1 — Navegação Inicial

1. Aguardar o login completo e o carregamento total do DOM.
2. Navegar para a URL alvo.
3. **Validação:** Aguardar até que o elemento `#form-relatorio` esteja presente no DOM.  
   > Nota: A página executa `carregarFiltro(...)` no `$(document).ready`, portanto aguardar o fim do carregamento é essencial antes de interagir com os campos.

---

## Passo 2 — Aba "Principal"

> **Seletor da aba:** `a[href="#principal"][role="tab"]`  
> (Aba já vem ativa por padrão, mas garantir que a classe `.active` esteja presente.)

| Campo | Seletor (ID) | Tipo no HTML | Valor a Selecionar | Observação Técnica |
|-------|-------------|--------------|-------------------|-------------------|
| **Filial** | `#FiliaisId` | Multi-select (`multiple`) | Selecionar **todas** as opções disponíveis. | Clicar no botão do bootstrap-select para abrir o dropdown, depois clicar na opção **"MARCAR/DESMARCAR TODAS"** (primeiro item da lista). |
| **Tabela de Preço** | `#TabelaPreco` | Single-select (⚠️ **não** é `multiple`) | `1` — **1 - PADRAO** | **Atenção:** Este campo não aceita múltipla seleção. Não existe opção "TODOS". O valor padrão é o placeholder `SELECIONE A TABELA DE PREÇO`. Deve-se selecionar obrigatoriamente `1 - PADRAO`. |
| **Modelo** | `#ModeloRelatorioId` | Single-select | `102` — **SALDO PRODUTO (A4 PAISAGEM)** | Este valor é obrigatório para habilitar a seleção das **Colunas 4 e 5** posteriormente. |

**Campos obrigatórios já pré-preenchidos** (garantir que permaneçam com estes valores):

| Campo | ID | Valor Default |
|-------|-----|---------------|
| Tipo Valor | `#TipoValor` | `0` (VR VENDA) |
| Acumular Saldo | `#AcumularSaldo` | `0` (FILIAL SELECIONADA) |
| Tipo Estoque | `#TipoEstoque` | `0` (SALDO ATUAL) |
| Agrupamento | `#Agrupamento` | `0` (NENHUM) |
| Ordenar | `#Ordenar` | `0` (NENHUM) |

---

## Passo 3 — Aba "Colunas à Imprimir"

> **Seletor da aba:** `a[href="#colunasImprimir"][role="tab"]`

**Pré-condição:** O campo **Modelo** na aba Principal deve estar configurado como `102` (SALDO PRODUTO A4 PAISAGEM).  
> Sem este modelo, as colunas 4 e 5 não devem ser selecionáveis ou o relatório poderá falhar.

| Campo | Seletor (ID) | Valor a Selecionar | Texto da Opção |
|-------|-------------|-------------------|----------------|
| **Coluna 1** | `#Coluna1` | `30` | MARCA |
| **Coluna 2** | `#Coluna2` | `25` | ALTURA |
| **Coluna 3** | `#Coluna3` | `26` | LARGURA |
| **Coluna 4** | `#Coluna4` | `23` | PESO |
| **Coluna 5** | `#Coluna5` | `27` | PROFUNDIDADE |

**Mapeamento completo das opções utilizadas:**

| Valor | Texto |
|-------|-------|
| `23` | PESO |
| `25` | ALTURA |
| `26` | LARGURA |
| `27` | PROFUNDIDADE |
| `30` | MARCA |

---

## Passo 4 — Ação Final

- **Seletor do botão:** `#btnVisualizar`
- **Ação:** Clicar no botão.
- **Resultado esperado:** Submissão do formulário `#form-relatorio` (`method="post"`, `target="_blank"`), abrindo o relatório gerado em uma nova aba/janela do navegador.

---

## ⚠️ Pontos de Atenção / Validações

1. **Regra de Negócio — Formato Paisagem:**  
   O HTML contém o aviso: *"Para selecionar colunas 4 e 5, devem ser selecionados modelos em formato PAISAGEM."* Como o plano exige o uso das colunas 4 e 5, o modelo **obrigatoriamente** deve ser `102` (SALDO PRODUTO A4 PAISAGEM) ou outro modelo paisagem (`103`, `104`, `105`, `107`, `108`). Este plano utiliza `102`.

2. **Tabela de Preço é Single-Select:**  
   O campo `#TabelaPreco` no HTML é um `<select>` comum (sem o atributo `multiple="multiple"`). Portanto, não é possível selecionar "todos" como no campo Filial. A única opção válida além do placeholder é `1 - PADRAO`. O bot deve selecionar essa opção explicitamente.

3. **Interação com Bootstrap Select:**  
   Todos os selects da página utilizam o plugin `bootstrap-select`. O `<select>` original fica com `tabindex="-98"` e o plugin renderiza um botão dropdown e uma lista `<ul>`/`<li>`. O bot de automação deve:
   - Clicar no botão `.dropdown-toggle` (ex: `button[data-id="FiliaisId"]`) para abrir o menu.
   - Clicar nas opções `li > a` dentro do dropdown para marcar/desmarcar itens.

4. **Filial pré-selecionada:**  
   O campo Filial já vem pré-selecionado com a filial logada (`1 - C R DA SILVEIRA BALEEIRO`). Se a intenção for realmente "todas", o bot deve clicar em **"MARCAR/DESMARCAR TODAS"** dentro do dropdown de `#FiliaisId`.

---

## ✅ Checklist de Execução (Resumo para o Bot)

```markdown
□ Navegar para /Relatorio/Estoque
□ Aguardar carregamento completo do formulário (#form-relatorio)
□ Aba Principal:
  □ Filial: marcar todas via dropdown bootstrap-select
  □ TabelaPreco: selecionar "1 - PADRAO"
  □ ModeloRelatorioId: selecionar "102" (SALDO PRODUTO A4 PAISAGEM)
□ Aba Colunas à Imprimir:
  □ Coluna1: "30" (MARCA)
  □ Coluna2: "25" (ALTURA)
  □ Coluna3: "26" (LARGURA)
  □ Coluna4: "23" (PESO)
  □ Coluna5: "27" (PROFUNDIDADE)
□ Clicar em #btnVisualizar
```

---

## 📎 Referência de Seletores Úteis

```javascript
// Tabs
const tabPrincipal = 'a[href="#principal"]';
const tabOpcoes = 'a[href="#opcoes"]';
const tabColunas = 'a[href="#colunasImprimir"]';

// Selects principais (interagir via bootstrap-select dropdown)
const filial = '#FiliaisId';
const tabelaPreco = '#TabelaPreco';
const modeloRelatorio = '#ModeloRelatorioId';
const coluna1 = '#Coluna1';
const coluna2 = '#Coluna2';
const coluna3 = '#Coluna3';
const coluna4 = '#Coluna4';
const coluna5 = '#Coluna5';

// Botões
const btnVisualizar = '#btnVisualizar';

// Formulário
const formRelatorio = '#form-relatorio';
```
