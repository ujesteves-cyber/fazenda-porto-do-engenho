# Estoque de Embriões — Design

**Data:** 12/05/2026
**Status:** Aprovado para implementação
**Escopo:** v1 — gestão de estoque de embriões vitrificados FIV-TE, com importação automática da planilha do laboratório, registro de saídas (TE interna, venda, perda) e reconciliação manual.

---

## 1. Contexto

A Fazenda Porto do Engenho mantém estoque de embriões vitrificados (VT) produzidos por FIV-TE em laboratório terceirizado (FIVET, Dr. Thiago Biancardi, CRMV-ES 909). O laboratório envia periodicamente uma planilha PDF com o estoque atual: cada linha representa um **lote** identificado por `(DT OPU, DT Vitrificação, Doadora, Touro, Tipo Sêmen)` com uma quantidade. A fazenda precisa:

1. Importar essa planilha sem trabalho manual.
2. Registrar saídas de embriões entre planilhas: TE em receptoras próprias, vendas para terceiros, perdas/descartes.
3. Saber a qualquer momento quantos embriões existem por lote, por touro, por doadora.
4. Reconciliar o sistema com a planilha do lab quando suspeitar de divergência.

Hoje não existe módulo de embriões no sistema — só Estoque de Touros (`/estoque`). O design abaixo adiciona um módulo paralelo `/embrioes`.

## 2. Decisão arquitetural: estoque transacional

Há tensão natural entre **planilha do lab** ("verdade oficial") e **movimentos da fazenda** (TE/venda/perda entre planilhas). Foram avaliadas três abordagens:

1. **Snapshot puro** — toda importação substitui o estoque; movimentos são só histórico. *Rejeitado:* não suporta requisitos B/C (venda/perda) com decremento em tempo real.
2. **Estoque transacional (escolhido)** — cada lote tem `qtd_inicial` e `qtd_atual`; movimentos decrementam `qtd_atual`; importação adiciona apenas lotes novos sem mexer nos existentes; reconciliação é manual e sob demanda.
3. **Reconciliação automática a cada import** — diff aplicado silenciosamente. *Rejeitado:* mascara erros operacionais (some embrião sem motivo registrado).

A escolha por #2 prioriza **rastreabilidade explícita**: toda saída de embrião precisa ter motivo registrado (TE, venda, perda, ou ajuste de reconciliação assinado).

## 3. Modelo de dados

Três tabelas novas em `schema.sql`.

### 3.1 `embriao_lote`

```sql
CREATE TABLE IF NOT EXISTS embriao_lote (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    dt_opu              TEXT,              -- "04/09/25" (mantido como veio do PDF)
    dt_vitrificacao     TEXT,              -- "12/09/25"
    doadora             TEXT NOT NULL,     -- "R3529", "144/16", "D006"
    doadora_matriz_id   TEXT,              -- FK opcional para matrizes.animal_id
    touro               TEXT NOT NULL,     -- "CIA ROBUSTO JATA" (uppercase canonical)
    tipo_semen          TEXT NOT NULL,     -- 'Sex F' | 'Conv.' (canonical)
    qtd_inicial         INTEGER NOT NULL,  -- qtd vinda da primeira planilha
    qtd_atual           INTEGER NOT NULL,  -- qtd_inicial menos somatório dos movimentos
    obs                 TEXT,
    lab                 TEXT DEFAULT 'FIVET',
    data_import         DATETIME DEFAULT CURRENT_TIMESTAMP,
    arquivo_origem      TEXT,
    UNIQUE(dt_opu, dt_vitrificacao, doadora, touro, tipo_semen)
);
CREATE INDEX IF NOT EXISTS idx_lote_doadora ON embriao_lote(doadora);
CREATE INDEX IF NOT EXISTS idx_lote_touro   ON embriao_lote(touro);
CREATE INDEX IF NOT EXISTS idx_lote_atual   ON embriao_lote(qtd_atual);
```

A constraint `UNIQUE` é o que garante que reimportar a mesma planilha não duplica lotes.

### 3.2 `embriao_movimento`

```sql
CREATE TABLE IF NOT EXISTS embriao_movimento (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id       INTEGER NOT NULL REFERENCES embriao_lote(id) ON DELETE CASCADE,
    tipo          TEXT NOT NULL,    -- 'te_interna' | 'venda' | 'perda' | 'ajuste_lab'
    qtd           INTEGER NOT NULL, -- sempre positivo (saída)
    data          TEXT NOT NULL,    -- "dd/mm/aaaa"
    receptora     TEXT,             -- usado apenas em 'te_interna'
    comprador     TEXT,             -- usado apenas em 'venda'
    valor_unit    REAL,             -- usado apenas em 'venda' (R$ por embrião)
    valor_total   REAL,             -- calculado no backend: qtd * valor_unit
    obs           TEXT,
    created_by    INTEGER REFERENCES usuarios(id),
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mov_lote ON embriao_movimento(lote_id);
CREATE INDEX IF NOT EXISTS idx_mov_tipo ON embriao_movimento(tipo);
```

`qtd_atual` em `embriao_lote` é materializado e mantido em sincronia por código de aplicação (em transação BEGIN/COMMIT). Não usar trigger SQL — explícito é melhor que mágico.

### 3.3 `embriao_import`

```sql
CREATE TABLE IF NOT EXISTS embriao_import (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo           TEXT,
    data_planilha     TEXT,           -- "30/04/2026" (extraído do cabeçalho do PDF)
    n_lotes_novos     INTEGER,
    n_lotes_ignorados INTEGER,
    n_embrioes_total  INTEGER,        -- total declarado no rodapé do PDF
    imported_by       INTEGER REFERENCES usuarios(id),
    imported_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Tabela de auditoria. Permite responder "quando foi a última importação?" e "quem importou?".

## 4. UI — telas e navegação

### 4.1 Sidebar

Adicionar item logo após "Estoque de Touros" em `templates/base.html`:

```html
<a href="/embrioes"><span class="icon">❄</span> <span>Estoque de Embriões</span></a>
```

Ícone `❄` representa vitrificação/congelamento. Se renderizar mal em alguma fonte, alternativa: `◈`.

### 4.2 `/embrioes` — Lista de lotes

**6 KPI cards no topo:**

| Card | SQL |
|------|-----|
| Estoque Total | `SELECT SUM(qtd_atual) FROM embriao_lote` |
| Sex F Disponível | `SELECT SUM(qtd_atual) WHERE tipo_semen='Sex F'` |
| Convencional Disponível | `SELECT SUM(qtd_atual) WHERE tipo_semen='Conv.'` |
| Doadoras Distintas | `SELECT COUNT(DISTINCT doadora) WHERE qtd_atual > 0` |
| Touros Distintos | `SELECT COUNT(DISTINCT touro) WHERE qtd_atual > 0` |
| Receita Total (vendas) | `SELECT SUM(valor_total) FROM embriao_movimento WHERE tipo='venda'` |

**Filtros:**
- Busca livre por doadora ou touro (LIKE)
- Tipo: Todos / Sex F / Conv.
- Touro: dropdown populado pelos distintos
- Checkbox "Esconder lotes zerados" (padrão: marcado)

**Tabela** (ordenação default: `dt_vitrificacao DESC`):

| DT OPU | DT Vitrif | Doadora | Touro | Tipo | Qtd Inicial | Qtd Atual | Ações |

Coluna Doadora: se `doadora_matriz_id IS NOT NULL`, renderiza `R3529 ↗` clicável apontando para `/ficha?id=<doadora_matriz_id>`, com tooltip exibindo `ICIAGen: X · IDESM: Y` da doadora.

Lotes com `qtd_atual=0` aparecem com `opacity:0.5` quando o filtro está desligado.

Ações por linha: `[Ver]` `[TE]` `[Vender]` `[Perda]`.

**Botões superiores:** `[Importar PDF FIVET]` `[Reconciliar]` `[+ Lote manual]`.

### 4.3 `/embrioes/<id>` — Detalhe do lote

- **Header** com info do lote (datas, doadora linkada, touro, tipo, qtd inicial/atual).
- **Painel "Sobre a doadora"** (renderizado apenas se `doadora_matriz_id` linkado): ICIAGen, IDESM, RMat, CEIP, categoria, botão "Ver ficha completa ↗".
- **Painel KPI do lote:** Restante, Total usado em TE, Total vendido, Receita.
- **Tabela de movimentos** ordenada por data:

  | Data | Tipo | Qtd | Detalhes | Usuário | Ações |
  |------|------|-----|----------|---------|-------|

  Detalhes varia por tipo:
  - `te_interna`: "Receptora: <id>"
  - `venda`: "<comprador> · R$ <valor_unit>/un · Total R$ <valor_total>"
  - `perda`: "<obs>"
  - `ajuste_lab`: "Reconciliação com planilha <data>. Sistema=X, planilha=Y."

- **Botões:** `[Registrar TE]` `[Registrar Venda]` `[Registrar Perda]` `[Editar lote]` (master) `[Excluir lote]` (master).

### 4.4 `/embrioes/importar` — Importação de PDF

Fluxo em 3 passos:

1. **Upload** — `<input type="file" accept=".pdf">`.
2. **Preview editável** — parser extrai linhas; cada uma vira uma linha de inputs editáveis. Banner verde se total declarado bate com soma; amarelo se diverge (`Total declarado X ≠ soma Y, diferença Z`).
3. **Confirmar** — submete preview corrigido; backend insere apenas lotes novos (UNIQUE constraint dispara para os já existentes).

Resultado: "X lotes novos importados, Y já existiam (ignorados), Z embriões totais na planilha."

### 4.5 `/embrioes/reconciliar` — Reconciliação manual

1. Upload do PDF mais recente.
2. Sistema parseia e constrói diff contra lotes em DB:
   - **Lotes no PDF mas não em DB**: lote novo (cadastra direto).
   - **Lotes em DB com `qtd_atual>0` que sumiram do PDF**: lote zerado pelo lab; sugere ajuste para 0.
   - **Lotes em ambos com qtd divergente**: mostra diferença.
3. Tabela de divergências com botões `[Aceitar]` / `[Ignorar]` por linha.
4. Aplicar cria movimentos tipo `ajuste_lab` com obs detalhada.

Acessível apenas por `is_master`.

### 4.6 Modais compartilhados

- **Modal TE**: qtd, data, receptora (texto livre opcional), obs.
- **Modal Venda**: qtd, data, comprador, valor unitário, obs. `valor_total` calculado em tempo real na UI; backend recalcula para segurança.
- **Modal Perda**: qtd, data, motivo (obs obrigatória).

Todos validam `qtd ≤ qtd_atual` no frontend e no backend.

## 5. Importação PDF — parser

Usa `pdfplumber` (a adicionar em `requirements.txt`). PDF do FIVET é text-based; `extract_tables()` retorna a estrutura tabular diretamente.

```python
import pdfplumber, re

def parse_fivet_pdf(file_path):
    with pdfplumber.open(file_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text()

        m = re.search(r'Atualizado em (\d{2}/\d{2}/\d{4})', text)
        data_planilha = m.group(1) if m else None

        m = re.search(r'ESTOQUE TOTAL.*?(\d+)', text, re.DOTALL)
        total_declarado = int(m.group(1)) if m else None

        tables = page.extract_tables()
        principal = next((t for t in tables if t and len(t[0]) == 7), None)
        if not principal:
            raise ValueError("Tabela principal não encontrada no PDF")

        linhas = []
        for row in principal[1:]:
            if not row[0] or 'ESTOQUE TOTAL' in (row[0] or ''):
                continue
            try:
                linhas.append({
                    'dt_opu': (row[0] or '').strip(),
                    'dt_vitrificacao': (row[1] or '').strip(),
                    'doadora': (row[2] or '').strip(),
                    'touro': (row[3] or '').strip().upper(),
                    'tipo_semen': normalizar_tipo(row[4]),
                    'qtd': int(row[5]),
                    'obs': (row[6] or '').strip(),
                })
            except (ValueError, TypeError):
                continue  # linha mal-formada vai para preview com flag de erro

        return {
            'data_planilha': data_planilha,
            'total_declarado': total_declarado,
            'linhas': linhas,
        }

def normalizar_tipo(raw):
    s = (raw or '').strip().lower()
    if 'sex' in s: return 'Sex F'
    return 'Conv.'
```

### 5.1 Lookup automático de doadora

```python
def lookup_matriz(doadora):
    db = get_db()
    # Match exato
    m = db.execute("SELECT animal_id FROM matrizes WHERE animal_id=?",
                   (doadora,)).fetchone()
    if m: return m['animal_id']
    # Match tolerante (remove espaços e barras)
    chave = doadora.replace('/', '').replace(' ', '')
    m = db.execute("""SELECT animal_id FROM matrizes
                     WHERE REPLACE(REPLACE(animal_id,' ',''),'/','')=?""",
                   (chave,)).fetchone()
    return m['animal_id'] if m else None
```

Aplicado no momento do INSERT em `embriao_lote`. Se a doadora for cadastrada como matriz depois, função utilitária `relink_doadoras()` atualiza FKs em lote (exposta como endpoint master `POST /api/embrioes/relink`).

## 6. Movimentos — backend transacional

Padrão único para os 3 tipos de saída + ajuste:

```python
@app.route('/api/embrioes/<int:lote_id>/movimento', methods=['POST'])
@api_login_required
def criar_movimento(lote_id):
    data = request.json
    tipo = data['tipo']
    qtd = int(data['qtd'])
    if qtd <= 0:
        return jsonify({'erro': 'Quantidade deve ser positiva'}), 400
    if tipo not in ('te_interna','venda','perda','ajuste_lab'):
        return jsonify({'erro': 'Tipo inválido'}), 400
    if tipo == 'ajuste_lab' and not session.get('is_master'):
        return jsonify({'erro': 'Apenas master pode criar ajustes'}), 403

    db = get_db()
    db.execute('BEGIN')
    try:
        lote = db.execute(
            "SELECT qtd_atual FROM embriao_lote WHERE id=?", (lote_id,)
        ).fetchone()
        if not lote:
            db.rollback()
            return jsonify({'erro': 'Lote não encontrado'}), 404
        if qtd > lote['qtd_atual']:
            db.rollback()
            return jsonify({'erro': f'Saldo insuficiente (disponível: {lote["qtd_atual"]})'}), 400

        valor_unit = data.get('valor_unit')
        valor_total = (valor_unit * qtd) if (tipo == 'venda' and valor_unit) else None

        db.execute("""INSERT INTO embriao_movimento
            (lote_id, tipo, qtd, data, receptora, comprador,
             valor_unit, valor_total, obs, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (lote_id, tipo, qtd, data['data'],
             data.get('receptora'), data.get('comprador'),
             valor_unit, valor_total, data.get('obs'),
             session['user_id']))

        db.execute("UPDATE embriao_lote SET qtd_atual = qtd_atual - ? WHERE id=?",
                   (qtd, lote_id))

        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.rollback()
        return jsonify({'erro': str(e)}), 500
```

### 6.1 Exclusão de movimento

`DELETE /api/embrioes/movimento/<mov_id>` em transação:
1. Lê `lote_id`, `qtd` e `tipo` do movimento.
2. Se `tipo='ajuste_lab'` e usuário não é master → retorna 403. (Mantém simetria: só master cria, só master desfaz reconciliação.)
3. `UPDATE embriao_lote SET qtd_atual = qtd_atual + qtd WHERE id=lote_id`.
4. `DELETE FROM embriao_movimento WHERE id=mov_id`.
5. Commit.

Permite reverter erros operacionais sem perder o registro definitivo (auditoria fica no log da aplicação se necessário no futuro).

## 7. API completa

| Método | Endpoint | Permissão | Descrição |
|--------|----------|-----------|-----------|
| GET | `/api/embrioes` | login | Lista de lotes com filtros (`q`, `tipo`, `touro`, `zerados`) |
| GET | `/api/embrioes/kpis` | login | KPIs da tela principal |
| GET | `/api/embrioes/<id>` | login | Detalhe de 1 lote + movimentos |
| POST | `/api/embrioes` | login | Cria lote manual |
| PUT | `/api/embrioes/<id>` | master | Edita lote (campos descritivos; não permite alterar qtd direto) |
| DELETE | `/api/embrioes/<id>` | master | Exclui lote (cascade nos movimentos) |
| POST | `/api/embrioes/<id>/movimento` | login | Cria movimento (TE/venda/perda); `ajuste_lab` exige master |
| DELETE | `/api/embrioes/movimento/<id>` | login | Exclui movimento (reverte qtd) |
| POST | `/api/embrioes/importar/preview` | login | Upload PDF, retorna linhas parseadas |
| POST | `/api/embrioes/importar/confirmar` | login | Confirma import com linhas (possivelmente editadas) |
| POST | `/api/embrioes/reconciliar/preview` | master | Upload PDF, retorna diff |
| POST | `/api/embrioes/reconciliar/aplicar` | master | Aplica ajustes selecionados |
| GET | `/api/embrioes/doadora-info/<doadora>` | login | Retorna ICIAGen/IDESM da doadora se for matriz |
| POST | `/api/embrioes/relink` | master | Reroda lookup de matriz em todos os lotes (utilitário) |

## 8. Permissões

| Operação | Login comum | Master |
|----------|-------------|--------|
| Ver lista/detalhe/KPIs | ✅ | ✅ |
| Importar PDF (lotes novos) | ✅ | ✅ |
| Cadastrar lote manual | ✅ | ✅ |
| Registrar TE/venda/perda | ✅ | ✅ |
| Excluir movimento (TE/venda/perda) | ✅ | ✅ |
| Excluir movimento `ajuste_lab` | ❌ | ✅ |
| Editar lote | ❌ | ✅ |
| Excluir lote | ❌ | ✅ |
| Reconciliar (criar `ajuste_lab`) | ❌ | ✅ |
| Relink doadoras | ❌ | ✅ |

## 9. Casos extremos

- **PDF sem texto extraível (scan)**: parser falha → mostra erro orientando reimportar com PDF de texto ou cadastrar manualmente.
- **PDF com layout diferente do exemplo**: parser pode pegar tabela errada; preview editável permite correção; usuário pode reportar para ajuste do parser.
- **Total do PDF ≠ soma das linhas**: banner amarelo no preview; usuário decide se prossegue.
- **Doadora com matching ambíguo**: lookup conservador faz match exato primeiro, depois normalizado. Se houver ambiguidade real (improvável), pega o primeiro resultado e usuário pode corrigir pela tela de edição (master).
- **Saldo insuficiente em movimento**: backend rejeita com 400; UI mostra erro claro.
- **Excluir lote com movimentos**: `ON DELETE CASCADE` remove movimentos juntos. Apenas master. Mensagem de confirmação explícita: "Vai apagar X movimentos junto."

## 10. Fora do escopo (v2 ou nunca)

- Catálogo de venda em PDF (foi avaliado; decidido por não incluir em v1).
- Rastreamento de prenhez (receptora → resultado → produto nascido). Receptora hoje é texto livre.
- Suporte a múltiplos laboratórios (campo `lab` fica preparado, mas UI assume FIVET).
- Importação de Excel/XLS (lab só envia PDF).
- Foto/imagem dos embriões.
- Estimativa de ICIAGen esperado pela média pai × mãe (Opção C da pergunta 4, recusada).

## 11. Mudanças em arquivos existentes

| Arquivo | Mudança |
|---------|---------|
| `schema.sql` | Adicionar 3 tabelas + índices da seção 3 |
| `requirements.txt` | Adicionar `pdfplumber>=0.10` |
| `app.py` | Adicionar rotas das seções 4 e 7; helpers de parser e lookup |
| `templates/base.html` | Adicionar item de sidebar |
| `templates/embrioes.html` | Criar (tela 4.2) |
| `templates/embrioes_detalhe.html` | Criar (tela 4.3) |
| `templates/embrioes_importar.html` | Criar (tela 4.4) |
| `templates/embrioes_reconciliar.html` | Criar (tela 4.5) |
| `static/css/main.css` | Possíveis ajustes pontuais para modais — reusar `.modal-overlay`, `.kpi-grid`, `.data-table` já existentes |

## 12. Critérios de aceitação

1. Importar o PDF de exemplo (estoque atualizado em 30/04/2026, total 136 VT) resulta em ~40 lotes criados, total `SUM(qtd_atual)` = 136.
2. Reimportar o mesmo PDF não duplica lotes (todos ignorados).
3. Registrar TE de 2 embriões em um lote de 6 deixa `qtd_atual=4` e o movimento aparece no detalhe.
4. Excluir esse movimento devolve `qtd_atual=6`.
5. Doadoras que coincidem com `matrizes.animal_id` aparecem como link clicável; as que não, como texto simples.
6. Usuário comum não consegue acessar reconciliação (403).
7. Tentar registrar saída maior que `qtd_atual` retorna 400 sem alterar estado.
8. KPIs da tela principal batem com SUM correspondente no banco.
