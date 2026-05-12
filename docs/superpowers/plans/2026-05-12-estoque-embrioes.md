# Estoque de Embriões Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `/embrioes` module that manages FIV-TE embryo stock with PDF import from FIVET lab, transactional movements (TE/sale/loss), and manual reconciliation.

**Architecture:** Single Flask app (`app.py`) following existing pattern; PDF parser extracted to `embriao_pdf.py` for testability; SQLite schema extended with 3 new tables; 4 new Jinja templates; 1 new sidebar link.

**Tech Stack:** Python 3.11, Flask, SQLite, `pdfplumber` (new dep), `pytest` (new dev dep), Jinja2, vanilla JS + Chart.js (existing).

**Spec:** `docs/superpowers/specs/2026-05-12-estoque-embrioes-design.md`

**Testing approach:** This codebase has no existing test suite. We introduce `pytest` and write tests only for pure logic that benefits from automated coverage (PDF parser, normalizers, lookup function, movement validation). Routes are smoke-tested with Flask's test client. UI templates are verified manually in the browser at the end.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `schema.sql` | Modify | Append 3 new tables + indexes |
| `requirements.txt` | Modify | Add `pdfplumber>=0.10`, `pytest>=8.0` |
| `app.py` | Modify | Add ~15 route handlers + helpers (`lookup_matriz`) |
| `embriao_pdf.py` | Create | PDF parser, normalizers (pure functions, testable) |
| `tests/__init__.py` | Create | Make `tests` a package |
| `tests/test_embriao_pdf.py` | Create | Tests for parser/normalizers |
| `tests/test_embriao_routes.py` | Create | Tests for movement validation + import |
| `tests/fixtures/fivet_exemplo.pdf` | Create | Test fixture (sample PDF) |
| `tests/conftest.py` | Create | Pytest fixtures (test db, test client) |
| `templates/base.html` | Modify | Add sidebar link for `/embrioes` |
| `templates/embrioes.html` | Create | Lista de lotes + KPIs |
| `templates/embrioes_detalhe.html` | Create | Detalhe do lote + movimentos |
| `templates/embrioes_importar.html` | Create | Importação com preview editável |
| `templates/embrioes_reconciliar.html` | Create | Reconciliação manual |

---

## Task 1: Schema + dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `schema.sql`
- Modify: `app.py` (the `init_db()` runs `schema.sql`; no code change needed there if init_db reads schema.sql at startup — verify in step 2)

- [ ] **Step 1: Add new dependencies to `requirements.txt`**

Append at end of file:

```
pdfplumber>=0.10
pytest>=8.0
```

- [ ] **Step 2: Verify how `app.py` initializes the schema**

Run:
```bash
grep -n "init_db\|schema.sql" app.py
```
Expected: see `init_db()` reading `schema.sql` (line ~125 based on earlier exploration). Confirm that adding tables to `schema.sql` is sufficient — no need to register them manually.

- [ ] **Step 3: Append the 3 new tables + indexes to `schema.sql`**

Add at the end of `schema.sql`:

```sql
-- =========================================================
-- Estoque de Embriões (FIV-TE)
-- =========================================================

CREATE TABLE IF NOT EXISTS embriao_lote (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    dt_opu              TEXT,
    dt_vitrificacao     TEXT,
    doadora             TEXT NOT NULL,
    doadora_matriz_id   TEXT,
    touro               TEXT NOT NULL,
    tipo_semen          TEXT NOT NULL,
    qtd_inicial         INTEGER NOT NULL,
    qtd_atual           INTEGER NOT NULL,
    obs                 TEXT,
    lab                 TEXT DEFAULT 'FIVET',
    data_import         DATETIME DEFAULT CURRENT_TIMESTAMP,
    arquivo_origem      TEXT,
    UNIQUE(dt_opu, dt_vitrificacao, doadora, touro, tipo_semen)
);
CREATE INDEX IF NOT EXISTS idx_lote_doadora ON embriao_lote(doadora);
CREATE INDEX IF NOT EXISTS idx_lote_touro   ON embriao_lote(touro);
CREATE INDEX IF NOT EXISTS idx_lote_atual   ON embriao_lote(qtd_atual);

CREATE TABLE IF NOT EXISTS embriao_movimento (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    lote_id       INTEGER NOT NULL REFERENCES embriao_lote(id) ON DELETE CASCADE,
    tipo          TEXT NOT NULL,
    qtd           INTEGER NOT NULL,
    data          TEXT NOT NULL,
    receptora     TEXT,
    comprador     TEXT,
    valor_unit    REAL,
    valor_total   REAL,
    obs           TEXT,
    created_by    INTEGER REFERENCES usuarios(id),
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mov_lote ON embriao_movimento(lote_id);
CREATE INDEX IF NOT EXISTS idx_mov_tipo ON embriao_movimento(tipo);

CREATE TABLE IF NOT EXISTS embriao_import (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo           TEXT,
    data_planilha     TEXT,
    n_lotes_novos     INTEGER,
    n_lotes_ignorados INTEGER,
    n_embrioes_total  INTEGER,
    imported_by       INTEGER REFERENCES usuarios(id),
    imported_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 4: Install new dependencies**

Run:
```bash
pip install -r requirements.txt
```
Expected: `pdfplumber` and `pytest` installed successfully.

- [ ] **Step 5: Apply schema to dev DB**

Run:
```bash
python -c "from app import init_db, app; ctx = app.app_context(); ctx.push(); init_db()"
```
Then verify:
```bash
python -c "import sqlite3; c = sqlite3.connect('data/fazenda167.db'); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'embriao_%'\").fetchall()])"
```
Expected: `['embriao_lote', 'embriao_movimento', 'embriao_import']`

- [ ] **Step 6: Commit**

```bash
git add requirements.txt schema.sql
git commit -m "feat(embrioes): add schema + dependencies for embryo stock module"
```

---

## Task 2: PDF parser module + tests

**Files:**
- Create: `embriao_pdf.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_embriao_pdf.py`
- Create: `tests/fixtures/fivet_exemplo.pdf` (manual: user provides a sample PDF; if absent, generate synthetic via reportlab in conftest)

- [ ] **Step 1: Create test fixture directory and conftest**

Create `tests/__init__.py` with single line:
```python
# Test package
```

Create `tests/conftest.py`:
```python
import os
import sys
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 2: Write the failing test for `normalizar_tipo`**

Create `tests/test_embriao_pdf.py`:
```python
from embriao_pdf import normalizar_tipo


def test_normalizar_tipo_sex_f_canonical():
    assert normalizar_tipo("Sex F") == "Sex F"


def test_normalizar_tipo_sex_uppercase():
    assert normalizar_tipo("SEX F") == "Sex F"


def test_normalizar_tipo_sexada():
    assert normalizar_tipo("Sexada") == "Sex F"


def test_normalizar_tipo_conv_with_period():
    assert normalizar_tipo("Conv.") == "Conv."


def test_normalizar_tipo_conv_without_period():
    assert normalizar_tipo("Conv") == "Conv."


def test_normalizar_tipo_convencional():
    assert normalizar_tipo("Convencional") == "Conv."


def test_normalizar_tipo_empty():
    assert normalizar_tipo("") == "Conv."


def test_normalizar_tipo_none():
    assert normalizar_tipo(None) == "Conv."
```

- [ ] **Step 3: Run tests and verify they fail**

Run:
```bash
pytest tests/test_embriao_pdf.py -v
```
Expected: 8 failures with `ModuleNotFoundError: No module named 'embriao_pdf'`.

- [ ] **Step 4: Create `embriao_pdf.py` with `normalizar_tipo`**

Create `embriao_pdf.py`:
```python
"""PDF parser for FIVET embryo stock spreadsheets.

Pure-function module isolated from Flask for easy unit testing.
"""
import re
from typing import Optional


def normalizar_tipo(raw: Optional[str]) -> str:
    """Canonicalize tipo_semen to 'Sex F' or 'Conv.'.

    Anything containing 'sex' (case-insensitive) -> 'Sex F'.
    Everything else (including empty/None) -> 'Conv.'.
    """
    s = (raw or "").strip().lower()
    if "sex" in s:
        return "Sex F"
    return "Conv."
```

- [ ] **Step 5: Run tests and verify they pass**

Run:
```bash
pytest tests/test_embriao_pdf.py -v
```
Expected: 8 passed.

- [ ] **Step 6: Add `parse_fivet_pdf` and its tests**

Add to `tests/test_embriao_pdf.py`:
```python
import pytest
from pathlib import Path
from embriao_pdf import parse_fivet_pdf

FIXTURE = Path(__file__).parent / "fixtures" / "fivet_exemplo.pdf"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_extracts_header_date():
    result = parse_fivet_pdf(str(FIXTURE))
    assert result["data_planilha"] == "30/04/2026"


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_extracts_total():
    result = parse_fivet_pdf(str(FIXTURE))
    assert result["total_declarado"] == 136


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_extracts_all_rows():
    result = parse_fivet_pdf(str(FIXTURE))
    # Sum of Quant.VT column should equal declared total
    soma = sum(r["qtd"] for r in result["linhas"])
    assert soma == result["total_declarado"]


@pytest.mark.skipif(not FIXTURE.exists(), reason="fixture PDF not present")
def test_parse_fivet_pdf_first_row_shape():
    result = parse_fivet_pdf(str(FIXTURE))
    first = result["linhas"][0]
    assert set(first.keys()) >= {"dt_opu", "dt_vitrificacao", "doadora",
                                  "touro", "tipo_semen", "qtd", "obs"}
    assert first["dt_opu"] == "04/09/25"
    assert first["doadora"] == "R3529"
    assert first["touro"] == "CIA ROBUSTO JATA"
    assert first["tipo_semen"] == "Sex F"
    assert first["qtd"] == 6


def test_parse_fivet_pdf_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        parse_fivet_pdf("/nonexistent/path.pdf")
```

- [ ] **Step 7: Run tests and verify them**

Run:
```bash
pytest tests/test_embriao_pdf.py -v
```
Expected (without fixture PDF): 8 passed (normalizar_tipo), 4 skipped (fixture tests), 1 failed (missing-file test, because function doesn't exist yet).

- [ ] **Step 8: Implement `parse_fivet_pdf` in `embriao_pdf.py`**

Append to `embriao_pdf.py`:
```python
import pdfplumber


def parse_fivet_pdf(file_path: str) -> dict:
    """Parse a FIVET stock spreadsheet PDF.

    Returns:
        {
            'data_planilha': str | None,    # e.g. "30/04/2026"
            'total_declarado': int | None,  # e.g. 136
            'linhas': list[dict],           # each row of the main table
        }
    Each `linha` has keys: dt_opu, dt_vitrificacao, doadora, touro,
    tipo_semen, qtd, obs.

    Raises:
        FileNotFoundError: if `file_path` does not exist.
        ValueError: if the main table cannot be located in the PDF.
    """
    with pdfplumber.open(file_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""

        m = re.search(r"Atualizado em (\d{2}/\d{2}/\d{4})", text)
        data_planilha = m.group(1) if m else None

        m = re.search(r"ESTOQUE TOTAL.*?(\d+)", text, re.DOTALL)
        total_declarado = int(m.group(1)) if m else None

        tables = page.extract_tables()
        principal = None
        for t in tables:
            if t and len(t[0]) == 7:
                principal = t
                break
        if not principal:
            raise ValueError(
                "Tabela principal de 7 colunas não foi encontrada no PDF. "
                "Verifique se o layout do laboratório mudou."
            )

        linhas = []
        for row in principal[1:]:
            if not row[0] or "ESTOQUE TOTAL" in (row[0] or ""):
                continue
            try:
                linhas.append({
                    "dt_opu": (row[0] or "").strip(),
                    "dt_vitrificacao": (row[1] or "").strip(),
                    "doadora": (row[2] or "").strip(),
                    "touro": (row[3] or "").strip().upper(),
                    "tipo_semen": normalizar_tipo(row[4]),
                    "qtd": int(row[5]),
                    "obs": (row[6] or "").strip(),
                })
            except (ValueError, TypeError):
                # Malformed line — skip silently; user catches in preview
                continue

        return {
            "data_planilha": data_planilha,
            "total_declarado": total_declarado,
            "linhas": linhas,
        }
```

- [ ] **Step 9: Re-run tests**

Run:
```bash
pytest tests/test_embriao_pdf.py -v
```
Expected: 9 passed, 4 skipped (or 13 passed if fixture exists).

- [ ] **Step 10: (Optional) Save the sample PDF as fixture**

Ask user to drop the FIVET sample PDF at `tests/fixtures/fivet_exemplo.pdf`. If they don't, the parser tests stay skipped — acceptable for first iteration.

```bash
mkdir -p tests/fixtures
# user manually copies sample PDF here
```

Re-run `pytest tests/test_embriao_pdf.py -v` — 4 previously-skipped tests should now pass.

- [ ] **Step 11: Commit**

```bash
git add embriao_pdf.py tests/__init__.py tests/conftest.py tests/test_embriao_pdf.py
git commit -m "feat(embrioes): add PDF parser module with pytest coverage"
```

---

## Task 3: Helpers + page routes + sidebar

**Files:**
- Modify: `app.py` (add `lookup_matriz` helper, 4 page routes, import `embriao_pdf`)
- Modify: `templates/base.html` (add sidebar link)

- [ ] **Step 1: Add `lookup_matriz` helper to `app.py`**

Locate a spot near `get_ultima_rodada` (around line 290) and add:

```python
def lookup_matriz(doadora):
    """Try to match a doadora ID (free text) against matrizes.animal_id.

    Returns the matched animal_id or None.
    Tries exact match first, then a normalized match (strips spaces/slashes).
    """
    if not doadora:
        return None
    db = get_db()
    m = db.execute(
        "SELECT animal_id FROM matrizes WHERE animal_id=?",
        (doadora,)
    ).fetchone()
    if m:
        return m["animal_id"]
    chave = doadora.replace("/", "").replace(" ", "")
    m = db.execute(
        "SELECT animal_id FROM matrizes "
        "WHERE REPLACE(REPLACE(animal_id,' ',''),'/','')=?",
        (chave,)
    ).fetchone()
    return m["animal_id"] if m else None
```

- [ ] **Step 2: Add page routes to `app.py`**

After the existing `/touros` page route block, add:

```python
@app.route('/embrioes')
@login_required
def embrioes_page():
    return render_template('embrioes.html')


@app.route('/embrioes/<int:lote_id>')
@login_required
def embrioes_detalhe_page(lote_id):
    return render_template('embrioes_detalhe.html', lote_id=lote_id)


@app.route('/embrioes/importar')
@login_required
def embrioes_importar_page():
    return render_template('embrioes_importar.html')


@app.route('/embrioes/reconciliar')
@master_required
def embrioes_reconciliar_page():
    return render_template('embrioes_reconciliar.html')
```

- [ ] **Step 3: Add sidebar link to `templates/base.html`**

Find the line containing `Estoque de Touros` and insert a new `<a>` right after it:

Locate:
```html
<a href="/estoque"><span class="icon">$</span> <span>Estoque de Touros</span></a>
```

Add immediately below:
```html
<a href="/embrioes"><span class="icon">&#x2744;</span> <span>Estoque de Embriões</span></a>
```

Using HTML entity `&#x2744;` (❄) avoids any encoding issue with the source file.

- [ ] **Step 4: Smoke test — start the app and verify nav**

Run:
```bash
python app.py
```
Open http://localhost:5000/, log in, verify:
- Sidebar shows new "Estoque de Embriões" link with ❄ icon
- Clicking it goes to `/embrioes` and renders without 500 (will be blank — template doesn't exist yet; expect "TemplateNotFound" — that's the next task)

Stop the server (Ctrl+C).

- [ ] **Step 5: Commit**

```bash
git add app.py templates/base.html
git commit -m "feat(embrioes): add lookup_matriz helper, page routes, sidebar entry"
```

---

## Task 4: Lista API endpoint with filters + KPIs

**Files:**
- Modify: `app.py` (add `/api/embrioes` and `/api/embrioes/kpis`)
- Create: `tests/test_embriao_routes.py`

- [ ] **Step 1: Create `tests/test_embriao_routes.py` with test client fixture and a list endpoint test**

Create file with:
```python
import os
import sqlite3
import tempfile
import pytest
from pathlib import Path

import app as flask_app_module


@pytest.fixture
def client():
    """Flask test client with an isolated temp DB."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    flask_app_module.app.config["TESTING"] = True
    flask_app_module.app.config["DATABASE"] = db_path
    # Apply schema
    conn = sqlite3.connect(db_path)
    schema = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    # Seed a usuario for login
    conn.execute(
        "INSERT INTO usuarios (nome, email, senha_hash, papel) "
        "VALUES (?, ?, ?, ?)",
        ("Tester", "test@ex.com", "x", "master")
    )
    conn.commit()
    conn.close()

    # Monkey-patch get_db to use our temp db
    original_get_db = flask_app_module.get_db

    def get_db_test():
        from flask import g
        if "db" not in g:
            g.db = sqlite3.connect(db_path)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    flask_app_module.get_db = get_db_test

    with flask_app_module.app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = 1
            sess["user_nome"] = "Tester"
            sess["papel"] = "master"
        yield c

    flask_app_module.get_db = original_get_db
    os.close(db_fd)
    os.unlink(db_path)


def test_embrioes_lista_empty(client):
    r = client.get("/api/embrioes")
    assert r.status_code == 200
    assert r.json == []
```

- [ ] **Step 2: Run the failing test**

Run:
```bash
pytest tests/test_embriao_routes.py -v
```
Expected: 1 failed (`AssertionError` from `r.status_code` being 404 — endpoint doesn't exist).

- [ ] **Step 3: Implement `GET /api/embrioes`**

In `app.py`, add (near other `/api/*` route definitions):

```python
@app.route('/api/embrioes', methods=['GET'])
@api_login_required
def api_embrioes_lista():
    db = get_db()
    q = (request.args.get('q') or '').strip()
    tipo = request.args.get('tipo') or ''
    touro = (request.args.get('touro') or '').strip()
    zerados = request.args.get('zerados') == '1'

    sql = "SELECT * FROM embriao_lote WHERE 1=1"
    params = []
    if q:
        sql += " AND (doadora LIKE ? OR touro LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    if tipo in ('Sex F', 'Conv.'):
        sql += " AND tipo_semen = ?"
        params.append(tipo)
    if touro:
        sql += " AND touro = ?"
        params.append(touro)
    if not zerados:
        sql += " AND qtd_atual > 0"
    sql += " ORDER BY dt_vitrificacao DESC, id DESC"

    rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    return jsonify(rows)
```

- [ ] **Step 4: Run the test — it should pass now**

```bash
pytest tests/test_embriao_routes.py::test_embrioes_lista_empty -v
```
Expected: 1 passed.

- [ ] **Step 5: Add filter tests**

Append to `tests/test_embriao_routes.py`:
```python
def _insert_lote(client, **kw):
    """Helper: insert a lote via raw SQL using the same temp DB."""
    from flask import g
    with client.application.app_context():
        db = flask_app_module.get_db()
        cols = ["dt_opu", "dt_vitrificacao", "doadora", "touro",
                "tipo_semen", "qtd_inicial", "qtd_atual"]
        defaults = {
            "dt_opu": "04/09/25", "dt_vitrificacao": "12/09/25",
            "doadora": "R3529", "touro": "CIA ROBUSTO JATA",
            "tipo_semen": "Sex F", "qtd_inicial": 6, "qtd_atual": 6,
        }
        defaults.update(kw)
        db.execute(
            f"INSERT INTO embriao_lote ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            [defaults[c] for c in cols]
        )
        db.commit()


def test_embrioes_lista_filtro_tipo(client):
    _insert_lote(client, doadora="A", tipo_semen="Sex F", qtd_atual=3)
    _insert_lote(client, doadora="B", tipo_semen="Conv.", qtd_atual=5)
    r = client.get("/api/embrioes?tipo=Sex+F")
    assert r.status_code == 200
    assert len(r.json) == 1
    assert r.json[0]["doadora"] == "A"


def test_embrioes_lista_esconde_zerados_por_padrao(client):
    _insert_lote(client, doadora="VIVA", qtd_atual=3)
    _insert_lote(client, doadora="MORTA", dt_opu="01/01/25", qtd_atual=0)
    r = client.get("/api/embrioes")
    assert len(r.json) == 1
    assert r.json[0]["doadora"] == "VIVA"


def test_embrioes_lista_zerados_incluidos_com_flag(client):
    _insert_lote(client, doadora="VIVA", qtd_atual=3)
    _insert_lote(client, doadora="MORTA", dt_opu="01/01/25", qtd_atual=0)
    r = client.get("/api/embrioes?zerados=1")
    assert len(r.json) == 2
```

- [ ] **Step 6: Run new tests**

```bash
pytest tests/test_embriao_routes.py -v
```
Expected: 4 passed.

- [ ] **Step 7: Implement `GET /api/embrioes/kpis`**

In `app.py`, add right after the previous endpoint:

```python
@app.route('/api/embrioes/kpis', methods=['GET'])
@api_login_required
def api_embrioes_kpis():
    db = get_db()
    row = db.execute("""
        SELECT
            COALESCE(SUM(qtd_atual), 0)                                    AS total,
            COALESCE(SUM(CASE WHEN tipo_semen='Sex F' THEN qtd_atual END), 0) AS sex_f,
            COALESCE(SUM(CASE WHEN tipo_semen='Conv.' THEN qtd_atual END), 0) AS conv,
            COUNT(DISTINCT CASE WHEN qtd_atual > 0 THEN doadora END)        AS doadoras,
            COUNT(DISTINCT CASE WHEN qtd_atual > 0 THEN touro END)          AS touros
        FROM embriao_lote
    """).fetchone()
    receita = db.execute(
        "SELECT COALESCE(SUM(valor_total), 0) AS r "
        "FROM embriao_movimento WHERE tipo='venda'"
    ).fetchone()
    return jsonify({
        "total": row["total"],
        "sex_f": row["sex_f"],
        "conv": row["conv"],
        "doadoras": row["doadoras"],
        "touros": row["touros"],
        "receita_total": receita["r"],
    })
```

- [ ] **Step 8: Add KPI test**

Append to `tests/test_embriao_routes.py`:
```python
def test_embrioes_kpis_basico(client):
    _insert_lote(client, doadora="A", tipo_semen="Sex F", qtd_atual=3)
    _insert_lote(client, doadora="B", tipo_semen="Conv.", qtd_atual=5)
    r = client.get("/api/embrioes/kpis")
    assert r.status_code == 200
    assert r.json["total"] == 8
    assert r.json["sex_f"] == 3
    assert r.json["conv"] == 5
    assert r.json["doadoras"] == 2
    assert r.json["touros"] == 1  # both default to "CIA ROBUSTO JATA"
    assert r.json["receita_total"] == 0
```

- [ ] **Step 9: Run all tests**

```bash
pytest tests/ -v
```
Expected: all passed.

- [ ] **Step 10: Commit**

```bash
git add app.py tests/test_embriao_routes.py
git commit -m "feat(embrioes): add list and KPIs endpoints with filter coverage"
```

---

## Task 5: Detalhe endpoint

**Files:**
- Modify: `app.py`
- Modify: `tests/test_embriao_routes.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_embriao_routes.py`:
```python
def test_embrioes_detalhe_lote_inexistente_404(client):
    r = client.get("/api/embrioes/999")
    assert r.status_code == 404


def test_embrioes_detalhe_inclui_lote_e_movimentos_vazios(client):
    _insert_lote(client, doadora="R3529", qtd_atual=6)
    # The id auto-increments from 1
    r = client.get("/api/embrioes/1")
    assert r.status_code == 200
    assert r.json["lote"]["doadora"] == "R3529"
    assert r.json["lote"]["qtd_atual"] == 6
    assert r.json["movimentos"] == []
    assert r.json["kpis"]["restante"] == 6
    assert r.json["kpis"]["usado_te"] == 0
    assert r.json["kpis"]["vendido"] == 0
    assert r.json["kpis"]["receita"] == 0
```

- [ ] **Step 2: Run test — it fails**

```bash
pytest tests/test_embriao_routes.py::test_embrioes_detalhe_lote_inexistente_404 -v
```
Expected: fail (endpoint doesn't exist).

- [ ] **Step 3: Implement endpoint in `app.py`**

```python
@app.route('/api/embrioes/<int:lote_id>', methods=['GET'])
@api_login_required
def api_embrioes_detalhe(lote_id):
    db = get_db()
    lote = db.execute(
        "SELECT * FROM embriao_lote WHERE id=?", (lote_id,)
    ).fetchone()
    if not lote:
        return jsonify({'erro': 'Lote não encontrado'}), 404

    movs = [dict(r) for r in db.execute(
        "SELECT m.*, u.nome AS user_nome FROM embriao_movimento m "
        "LEFT JOIN usuarios u ON u.id=m.created_by "
        "WHERE m.lote_id=? ORDER BY m.data, m.id", (lote_id,)
    ).fetchall()]

    agg = db.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN tipo='te_interna' THEN qtd END), 0) AS usado_te,
            COALESCE(SUM(CASE WHEN tipo='venda'       THEN qtd END), 0) AS vendido,
            COALESCE(SUM(CASE WHEN tipo='venda'       THEN valor_total END), 0) AS receita
        FROM embriao_movimento WHERE lote_id=?
    """, (lote_id,)).fetchone()

    return jsonify({
        'lote': dict(lote),
        'movimentos': movs,
        'kpis': {
            'restante': lote['qtd_atual'],
            'usado_te': agg['usado_te'],
            'vendido': agg['vendido'],
            'receita': agg['receita'],
        },
    })
```

- [ ] **Step 4: Run tests — should pass**

```bash
pytest tests/test_embriao_routes.py -v
```
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_embriao_routes.py
git commit -m "feat(embrioes): add lote detail endpoint with aggregated KPIs"
```

---

## Task 6: Lote CRUD (manual create, edit, delete)

**Files:**
- Modify: `app.py`
- Modify: `tests/test_embriao_routes.py`

- [ ] **Step 1: Failing tests for create**

Append to `tests/test_embriao_routes.py`:
```python
def test_embrioes_criar_manual(client):
    payload = {
        "dt_opu": "01/01/26", "dt_vitrificacao": "10/01/26",
        "doadora": "X100", "touro": "TOURO TESTE",
        "tipo_semen": "Conv.", "qtd": 5, "obs": "manual"
    }
    r = client.post("/api/embrioes", json=payload)
    assert r.status_code == 200
    assert r.json["ok"] is True
    assert r.json["id"] > 0


def test_embrioes_criar_manual_duplicado_409(client):
    payload = {
        "dt_opu": "01/01/26", "dt_vitrificacao": "10/01/26",
        "doadora": "X100", "touro": "TOURO TESTE",
        "tipo_semen": "Conv.", "qtd": 5
    }
    r1 = client.post("/api/embrioes", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/embrioes", json=payload)
    assert r2.status_code == 409
```

- [ ] **Step 2: Run — fail**

```bash
pytest tests/test_embriao_routes.py::test_embrioes_criar_manual -v
```

- [ ] **Step 3: Implement create in `app.py`**

```python
@app.route('/api/embrioes', methods=['POST'])
@api_login_required
def api_embrioes_criar():
    data = request.json or {}
    required = ['doadora', 'touro', 'tipo_semen', 'qtd']
    for k in required:
        if not data.get(k):
            return jsonify({'erro': f'Campo obrigatório: {k}'}), 400
    try:
        qtd = int(data['qtd'])
    except (TypeError, ValueError):
        return jsonify({'erro': 'qtd deve ser inteiro'}), 400
    if qtd <= 0:
        return jsonify({'erro': 'qtd deve ser positivo'}), 400
    tipo = data['tipo_semen']
    if tipo not in ('Sex F', 'Conv.'):
        return jsonify({'erro': 'tipo_semen inválido'}), 400

    doadora = data['doadora'].strip()
    db = get_db()
    try:
        cur = db.execute("""
            INSERT INTO embriao_lote
              (dt_opu, dt_vitrificacao, doadora, doadora_matriz_id,
               touro, tipo_semen, qtd_inicial, qtd_atual, obs, arquivo_origem)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            (data.get('dt_opu') or '').strip(),
            (data.get('dt_vitrificacao') or '').strip(),
            doadora,
            lookup_matriz(doadora),
            data['touro'].strip().upper(),
            tipo,
            qtd, qtd,
            (data.get('obs') or '').strip() or None,
            'manual',
        ))
        db.commit()
        return jsonify({'ok': True, 'id': cur.lastrowid})
    except sqlite3.IntegrityError:
        return jsonify({'erro': 'Lote já existe (mesma chave única)'}), 409
```

- [ ] **Step 4: Run tests — pass**

```bash
pytest tests/test_embriao_routes.py -v
```

- [ ] **Step 5: Failing tests for edit + delete**

Append:
```python
def test_embrioes_editar_requer_master_se_master_only(client):
    # Inserts via helper
    _insert_lote(client, doadora="EDIT", qtd_atual=3)
    r = client.put("/api/embrioes/1", json={"obs": "atualizado"})
    # Logged user is master in fixture, so should succeed
    assert r.status_code == 200
    assert r.json["ok"] is True


def test_embrioes_editar_nao_permite_alterar_qtd_atual(client):
    _insert_lote(client, doadora="EDIT", qtd_atual=3)
    r = client.put("/api/embrioes/1", json={"qtd_atual": 99})
    assert r.status_code == 200
    # Verify qtd_atual unchanged
    r2 = client.get("/api/embrioes/1")
    assert r2.json["lote"]["qtd_atual"] == 3


def test_embrioes_excluir(client):
    _insert_lote(client, doadora="DEL", qtd_atual=2)
    r = client.delete("/api/embrioes/1")
    assert r.status_code == 200
    r2 = client.get("/api/embrioes/1")
    assert r2.status_code == 404
```

- [ ] **Step 6: Run — fail**

- [ ] **Step 7: Implement edit + delete in `app.py`**

```python
@app.route('/api/embrioes/<int:lote_id>', methods=['PUT'])
@api_master_required
def api_embrioes_editar(lote_id):
    data = request.json or {}
    # Whitelist of editable fields (qtd_atual deliberately excluded)
    editable = ['dt_opu', 'dt_vitrificacao', 'doadora', 'touro',
                'tipo_semen', 'obs']
    sets, params = [], []
    for k in editable:
        if k in data:
            v = (data[k] or '').strip() if isinstance(data[k], str) else data[k]
            if k == 'touro' and v:
                v = v.upper()
            if k == 'tipo_semen' and v not in ('Sex F', 'Conv.'):
                return jsonify({'erro': 'tipo_semen inválido'}), 400
            sets.append(f"{k}=?")
            params.append(v if v != '' else None)
    if not sets:
        return jsonify({'ok': True})
    # Re-link doadora if it changed
    if 'doadora' in data:
        sets.append("doadora_matriz_id=?")
        params.append(lookup_matriz(data['doadora']))
    params.append(lote_id)
    db = get_db()
    try:
        db.execute(f"UPDATE embriao_lote SET {','.join(sets)} WHERE id=?", params)
        db.commit()
    except sqlite3.IntegrityError as e:
        return jsonify({'erro': f'Conflito de unicidade: {e}'}), 409
    return jsonify({'ok': True})


@app.route('/api/embrioes/<int:lote_id>', methods=['DELETE'])
@api_master_required
def api_embrioes_excluir(lote_id):
    db = get_db()
    cur = db.execute("DELETE FROM embriao_lote WHERE id=?", (lote_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({'erro': 'Lote não encontrado'}), 404
    return jsonify({'ok': True})
```

- [ ] **Step 8: Run tests — all pass**

```bash
pytest tests/test_embriao_routes.py -v
```

- [ ] **Step 9: Commit**

```bash
git add app.py tests/test_embriao_routes.py
git commit -m "feat(embrioes): add lote CRUD endpoints (create/edit/delete)"
```

---

## Task 7: Movement endpoint (create + delete with reversal)

**Files:**
- Modify: `app.py`
- Modify: `tests/test_embriao_routes.py`

- [ ] **Step 1: Failing tests for create movement**

Append:
```python
def test_movimento_te_decrementa_saldo(client):
    _insert_lote(client, doadora="T1", qtd_atual=6)
    r = client.post("/api/embrioes/1/movimento", json={
        "tipo": "te_interna", "qtd": 2, "data": "15/05/2026",
        "receptora": "045"
    })
    assert r.status_code == 200
    r2 = client.get("/api/embrioes/1")
    assert r2.json["lote"]["qtd_atual"] == 4
    assert len(r2.json["movimentos"]) == 1
    assert r2.json["movimentos"][0]["tipo"] == "te_interna"
    assert r2.json["movimentos"][0]["qtd"] == 2


def test_movimento_venda_calcula_valor_total(client):
    _insert_lote(client, doadora="V1", qtd_atual=10)
    r = client.post("/api/embrioes/1/movimento", json={
        "tipo": "venda", "qtd": 3, "data": "20/05/2026",
        "comprador": "Fazenda X", "valor_unit": 1500.0
    })
    assert r.status_code == 200
    r2 = client.get("/api/embrioes/1")
    assert r2.json["lote"]["qtd_atual"] == 7
    assert r2.json["kpis"]["receita"] == 4500.0


def test_movimento_saldo_insuficiente_400(client):
    _insert_lote(client, doadora="S1", qtd_atual=2)
    r = client.post("/api/embrioes/1/movimento", json={
        "tipo": "perda", "qtd": 5, "data": "15/05/2026"
    })
    assert r.status_code == 400
    r2 = client.get("/api/embrioes/1")
    assert r2.json["lote"]["qtd_atual"] == 2  # unchanged


def test_movimento_tipo_invalido_400(client):
    _insert_lote(client, doadora="X1", qtd_atual=5)
    r = client.post("/api/embrioes/1/movimento", json={
        "tipo": "outro_tipo", "qtd": 1, "data": "15/05/2026"
    })
    assert r.status_code == 400
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement endpoint in `app.py`**

```python
@app.route('/api/embrioes/<int:lote_id>/movimento', methods=['POST'])
@api_login_required
def api_embrioes_criar_movimento(lote_id):
    data = request.json or {}
    tipo = data.get('tipo')
    if tipo not in ('te_interna', 'venda', 'perda', 'ajuste_lab'):
        return jsonify({'erro': 'Tipo inválido'}), 400
    if tipo == 'ajuste_lab' and not is_master(current_user()):
        return jsonify({'erro': 'Apenas master pode criar ajuste de reconciliação'}), 403

    try:
        qtd = int(data.get('qtd', 0))
    except (TypeError, ValueError):
        return jsonify({'erro': 'qtd deve ser inteiro'}), 400
    if qtd <= 0:
        return jsonify({'erro': 'qtd deve ser positivo'}), 400

    data_mov = (data.get('data') or '').strip()
    if not data_mov:
        return jsonify({'erro': 'Campo data é obrigatório'}), 400

    valor_unit = data.get('valor_unit')
    if valor_unit is not None:
        try:
            valor_unit = float(valor_unit)
        except (TypeError, ValueError):
            return jsonify({'erro': 'valor_unit inválido'}), 400

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
            return jsonify({
                'erro': f'Saldo insuficiente (disponível: {lote["qtd_atual"]})'
            }), 400

        valor_total = (valor_unit * qtd) if (tipo == 'venda' and valor_unit) else None

        db.execute("""
            INSERT INTO embriao_movimento
              (lote_id, tipo, qtd, data, receptora, comprador,
               valor_unit, valor_total, obs, created_by)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            lote_id, tipo, qtd, data_mov,
            (data.get('receptora') or '').strip() or None,
            (data.get('comprador') or '').strip() or None,
            valor_unit, valor_total,
            (data.get('obs') or '').strip() or None,
            session.get('user_id'),
        ))
        db.execute(
            "UPDATE embriao_lote SET qtd_atual = qtd_atual - ? WHERE id=?",
            (qtd, lote_id)
        )
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.rollback()
        return jsonify({'erro': str(e)}), 500
```

- [ ] **Step 4: Run tests — pass**

- [ ] **Step 5: Failing test for delete movement (reversal)**

```python
def test_excluir_movimento_devolve_qtd(client):
    _insert_lote(client, doadora="REV", qtd_atual=6)
    r = client.post("/api/embrioes/1/movimento", json={
        "tipo": "te_interna", "qtd": 2, "data": "01/05/2026"
    })
    assert r.status_code == 200
    # Find the movimento id
    r2 = client.get("/api/embrioes/1")
    mov_id = r2.json["movimentos"][0]["id"]
    r3 = client.delete(f"/api/embrioes/movimento/{mov_id}")
    assert r3.status_code == 200
    r4 = client.get("/api/embrioes/1")
    assert r4.json["lote"]["qtd_atual"] == 6
    assert r4.json["movimentos"] == []
```

- [ ] **Step 6: Run — fail**

- [ ] **Step 7: Implement delete movement in `app.py`**

```python
@app.route('/api/embrioes/movimento/<int:mov_id>', methods=['DELETE'])
@api_login_required
def api_embrioes_excluir_movimento(mov_id):
    db = get_db()
    mov = db.execute(
        "SELECT lote_id, qtd, tipo FROM embriao_movimento WHERE id=?",
        (mov_id,)
    ).fetchone()
    if not mov:
        return jsonify({'erro': 'Movimento não encontrado'}), 404
    if mov['tipo'] == 'ajuste_lab' and not is_master(current_user()):
        return jsonify({
            'erro': 'Apenas master pode excluir ajuste de reconciliação'
        }), 403

    db.execute('BEGIN')
    try:
        db.execute(
            "UPDATE embriao_lote SET qtd_atual = qtd_atual + ? WHERE id=?",
            (mov['qtd'], mov['lote_id'])
        )
        db.execute("DELETE FROM embriao_movimento WHERE id=?", (mov_id,))
        db.commit()
        return jsonify({'ok': True})
    except Exception as e:
        db.rollback()
        return jsonify({'erro': str(e)}), 500
```

- [ ] **Step 8: Run all tests**

```bash
pytest tests/ -v
```
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add app.py tests/test_embriao_routes.py
git commit -m "feat(embrioes): add movement create/delete with transactional saldo update"
```

---

## Task 8: Import endpoints (preview + confirm)

**Files:**
- Modify: `app.py`
- Modify: `tests/test_embriao_routes.py`

- [ ] **Step 1: Failing test for `confirmar` (preview is just parsing — we test that indirectly)**

Append:
```python
def test_importar_confirmar_cria_lotes(client):
    linhas = [
        {"dt_opu": "01/01/26", "dt_vitrificacao": "10/01/26",
         "doadora": "AAA", "touro": "TOURO A",
         "tipo_semen": "Sex F", "qtd": 4, "obs": ""},
        {"dt_opu": "01/01/26", "dt_vitrificacao": "10/01/26",
         "doadora": "BBB", "touro": "TOURO B",
         "tipo_semen": "Conv.", "qtd": 2, "obs": ""},
    ]
    r = client.post("/api/embrioes/importar/confirmar", json={
        "arquivo": "fake.pdf",
        "data_planilha": "30/04/2026",
        "linhas": linhas,
    })
    assert r.status_code == 200
    assert r.json["novos"] == 2
    assert r.json["ignorados"] == 0


def test_importar_confirmar_segunda_vez_ignora(client):
    linhas = [{"dt_opu": "01/01/26", "dt_vitrificacao": "10/01/26",
               "doadora": "AAA", "touro": "TOURO A",
               "tipo_semen": "Sex F", "qtd": 4, "obs": ""}]
    r1 = client.post("/api/embrioes/importar/confirmar", json={
        "arquivo": "f.pdf", "data_planilha": "30/04/2026", "linhas": linhas})
    assert r1.json["novos"] == 1
    r2 = client.post("/api/embrioes/importar/confirmar", json={
        "arquivo": "f.pdf", "data_planilha": "30/04/2026", "linhas": linhas})
    assert r2.json["novos"] == 0
    assert r2.json["ignorados"] == 1
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement preview + confirmar endpoints in `app.py`**

Add near top of file (imports section):
```python
from embriao_pdf import parse_fivet_pdf
```

Then add routes:
```python
@app.route('/api/embrioes/importar/preview', methods=['POST'])
@api_login_required
def api_embrioes_importar_preview():
    """Parse uploaded PDF and return the extracted rows without persisting."""
    if 'arquivo' not in request.files:
        return jsonify({'erro': 'arquivo PDF não enviado'}), 400
    f = request.files['arquivo']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'erro': 'apenas arquivos PDF são aceitos'}), 400
    # Persist to a temp path so pdfplumber can open by name
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    try:
        f.save(tmp.name)
        tmp.close()
        result = parse_fivet_pdf(tmp.name)
        result['arquivo'] = f.filename
        return jsonify(result)
    except ValueError as e:
        return jsonify({'erro': str(e)}), 400
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@app.route('/api/embrioes/importar/confirmar', methods=['POST'])
@api_login_required
def api_embrioes_importar_confirmar():
    """Persist the (possibly user-edited) rows. UNIQUE constraint dedupes."""
    payload = request.json or {}
    linhas = payload.get('linhas') or []
    if not linhas:
        return jsonify({'erro': 'Nenhuma linha para importar'}), 400
    arquivo = (payload.get('arquivo') or '').strip()
    data_planilha = (payload.get('data_planilha') or '').strip() or None

    db = get_db()
    novos, ignorados = 0, 0
    db.execute('BEGIN')
    try:
        for row in linhas:
            try:
                qtd = int(row.get('qtd', 0))
            except (TypeError, ValueError):
                continue
            if qtd <= 0:
                continue
            doadora = (row.get('doadora') or '').strip()
            touro = (row.get('touro') or '').strip().upper()
            tipo = row.get('tipo_semen') or 'Conv.'
            if tipo not in ('Sex F', 'Conv.'):
                tipo = 'Conv.'
            if not doadora or not touro:
                continue
            try:
                db.execute("""
                    INSERT INTO embriao_lote
                      (dt_opu, dt_vitrificacao, doadora, doadora_matriz_id,
                       touro, tipo_semen, qtd_inicial, qtd_atual, obs, arquivo_origem)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    (row.get('dt_opu') or '').strip(),
                    (row.get('dt_vitrificacao') or '').strip(),
                    doadora,
                    lookup_matriz(doadora),
                    touro, tipo, qtd, qtd,
                    (row.get('obs') or '').strip() or None,
                    arquivo or None,
                ))
                novos += 1
            except sqlite3.IntegrityError:
                ignorados += 1
        total = sum(int(r.get('qtd', 0)) for r in linhas if str(r.get('qtd', '')).isdigit())
        db.execute("""
            INSERT INTO embriao_import
              (arquivo, data_planilha, n_lotes_novos, n_lotes_ignorados,
               n_embrioes_total, imported_by)
            VALUES (?,?,?,?,?,?)
        """, (arquivo or None, data_planilha, novos, ignorados, total,
              session.get('user_id')))
        db.commit()
        return jsonify({'ok': True, 'novos': novos, 'ignorados': ignorados,
                        'total': total})
    except Exception as e:
        db.rollback()
        return jsonify({'erro': str(e)}), 500
```

- [ ] **Step 4: Run tests — pass**

```bash
pytest tests/test_embriao_routes.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_embriao_routes.py
git commit -m "feat(embrioes): add import preview/confirmar endpoints with dedup"
```

---

## Task 9: Reconciliation endpoints

**Files:**
- Modify: `app.py`
- Modify: `tests/test_embriao_routes.py`

- [ ] **Step 1: Failing test**

Append:
```python
def test_reconciliar_preview_detecta_divergencia(client):
    # Sistema tem 1 lote com qtd 5
    _insert_lote(client, doadora="REC", qtd_atual=5,
                 dt_opu="01/01/26", dt_vitrificacao="10/01/26")
    # Planilha mostra mesmo lote mas com qtd 3
    payload = {
        "linhas": [{
            "dt_opu": "01/01/26", "dt_vitrificacao": "10/01/26",
            "doadora": "REC", "touro": "CIA ROBUSTO JATA",
            "tipo_semen": "Sex F", "qtd": 3, "obs": ""
        }],
        "data_planilha": "30/04/2026",
    }
    r = client.post("/api/embrioes/reconciliar/preview", json=payload)
    assert r.status_code == 200
    diffs = r.json["divergentes"]
    assert len(diffs) == 1
    assert diffs[0]["sistema"] == 5
    assert diffs[0]["planilha"] == 3
    assert diffs[0]["diferenca"] == -2


def test_reconciliar_aplicar_cria_ajuste(client):
    _insert_lote(client, doadora="REC", qtd_atual=5,
                 dt_opu="01/01/26", dt_vitrificacao="10/01/26")
    r = client.post("/api/embrioes/reconciliar/aplicar", json={
        "data_planilha": "30/04/2026",
        "ajustes": [{"lote_id": 1, "diferenca": -2}]
    })
    assert r.status_code == 200
    r2 = client.get("/api/embrioes/1")
    assert r2.json["lote"]["qtd_atual"] == 3
    assert r2.json["movimentos"][0]["tipo"] == "ajuste_lab"
    assert r2.json["movimentos"][0]["qtd"] == 2
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement endpoints**

```python
@app.route('/api/embrioes/reconciliar/preview', methods=['POST'])
@api_master_required
def api_embrioes_reconciliar_preview():
    """Compare DB lotes vs PDF rows. Returns 3 lists: novos, sumidos, divergentes."""
    payload = request.json or {}
    linhas = payload.get('linhas') or []
    db = get_db()
    # Build lookup of planilha rows by unique key
    chave = lambda r: (
        (r.get('dt_opu') or '').strip(),
        (r.get('dt_vitrificacao') or '').strip(),
        (r.get('doadora') or '').strip(),
        (r.get('touro') or '').strip().upper(),
        r.get('tipo_semen') or 'Conv.',
    )
    pdf_map = {chave(r): int(r.get('qtd', 0)) for r in linhas}

    db_rows = db.execute(
        "SELECT id, dt_opu, dt_vitrificacao, doadora, touro, tipo_semen, qtd_atual "
        "FROM embriao_lote"
    ).fetchall()

    novos, sumidos, divergentes = [], [], []
    db_keys = set()
    for r in db_rows:
        k = (r['dt_opu'] or '', r['dt_vitrificacao'] or '',
             r['doadora'], r['touro'], r['tipo_semen'])
        db_keys.add(k)
        if k in pdf_map:
            if pdf_map[k] != r['qtd_atual']:
                divergentes.append({
                    'lote_id': r['id'],
                    'doadora': r['doadora'],
                    'touro': r['touro'],
                    'tipo_semen': r['tipo_semen'],
                    'dt_vitrificacao': r['dt_vitrificacao'],
                    'sistema': r['qtd_atual'],
                    'planilha': pdf_map[k],
                    'diferenca': pdf_map[k] - r['qtd_atual'],
                })
        elif r['qtd_atual'] > 0:
            sumidos.append({
                'lote_id': r['id'],
                'doadora': r['doadora'],
                'touro': r['touro'],
                'tipo_semen': r['tipo_semen'],
                'dt_vitrificacao': r['dt_vitrificacao'],
                'sistema': r['qtd_atual'],
            })

    for k, qtd_pdf in pdf_map.items():
        if k not in db_keys and qtd_pdf > 0:
            novos.append({
                'dt_opu': k[0], 'dt_vitrificacao': k[1],
                'doadora': k[2], 'touro': k[3],
                'tipo_semen': k[4], 'qtd': qtd_pdf,
            })

    return jsonify({'novos': novos, 'sumidos': sumidos, 'divergentes': divergentes})


@app.route('/api/embrioes/reconciliar/aplicar', methods=['POST'])
@api_master_required
def api_embrioes_reconciliar_aplicar():
    """Apply selected adjustments. Creates ajuste_lab movements."""
    payload = request.json or {}
    ajustes = payload.get('ajustes') or []
    data_planilha = (payload.get('data_planilha') or '').strip() or '?'

    db = get_db()
    db.execute('BEGIN')
    aplicados = 0
    try:
        for adj in ajustes:
            lote_id = adj.get('lote_id')
            try:
                diff = int(adj.get('diferenca', 0))
            except (TypeError, ValueError):
                continue
            if diff == 0:
                continue
            if diff > 0:
                # Sistema tinha menos que planilha — devolve embriões
                db.execute(
                    "UPDATE embriao_lote SET qtd_atual = qtd_atual + ? WHERE id=?",
                    (diff, lote_id)
                )
                qtd_mov = diff
                obs = f"Reconciliação com planilha {data_planilha}: sistema +{diff}."
            else:
                qtd_mov = -diff  # positivo
                lote = db.execute(
                    "SELECT qtd_atual FROM embriao_lote WHERE id=?", (lote_id,)
                ).fetchone()
                if not lote or qtd_mov > lote['qtd_atual']:
                    continue  # skip if would go negative
                db.execute(
                    "UPDATE embriao_lote SET qtd_atual = qtd_atual - ? WHERE id=?",
                    (qtd_mov, lote_id)
                )
                obs = f"Reconciliação com planilha {data_planilha}: sistema -{qtd_mov}."
            db.execute("""
                INSERT INTO embriao_movimento
                  (lote_id, tipo, qtd, data, obs, created_by)
                VALUES (?, 'ajuste_lab', ?, ?, ?, ?)
            """, (lote_id, qtd_mov, data_planilha, obs, session.get('user_id')))
            aplicados += 1
        db.commit()
        return jsonify({'ok': True, 'aplicados': aplicados})
    except Exception as e:
        db.rollback()
        return jsonify({'erro': str(e)}), 500
```

- [ ] **Step 4: Run tests — pass**

```bash
pytest tests/test_embriao_routes.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_embriao_routes.py
git commit -m "feat(embrioes): add reconciliation preview/apply endpoints"
```

---

## Task 10: Helper endpoints (doadora-info + relink)

**Files:**
- Modify: `app.py`
- Modify: `tests/test_embriao_routes.py`

- [ ] **Step 1: Failing tests**

Append:
```python
def test_doadora_info_nao_cadastrada(client):
    r = client.get("/api/embrioes/doadora-info/NAOEXISTE")
    assert r.status_code == 404


def test_doadora_info_matriz_existe(client):
    # Insert a matriz via fixture's db
    from flask import g
    with client.application.app_context():
        db = flask_app_module.get_db()
        db.execute(
            "INSERT INTO matrizes (animal_id, categoria) VALUES (?, ?)",
            ("P1234", "M")
        )
        db.commit()
    r = client.get("/api/embrioes/doadora-info/P1234")
    assert r.status_code == 200
    assert r.json["animal_id"] == "P1234"
    assert r.json["categoria"] == "M"
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement endpoints**

```python
@app.route('/api/embrioes/doadora-info/<path:doadora>', methods=['GET'])
@api_login_required
def api_embrioes_doadora_info(doadora):
    matched = lookup_matriz(doadora)
    if not matched:
        return jsonify({'erro': 'doadora não cadastrada como matriz'}), 404
    db = get_db()
    m = db.execute("""
        SELECT m.animal_id, m.categoria, m.ceip, m.precoce,
               a.iciagen, a.deca_icia_g, a.idesm, a.rmat
        FROM matrizes m
        LEFT JOIN avaliacoes a ON a.animal_id = m.animal_id
        WHERE m.animal_id = ?
        ORDER BY a.rodada_id DESC LIMIT 1
    """, (matched,)).fetchone()
    return jsonify(dict(m))


@app.route('/api/embrioes/relink', methods=['POST'])
@api_master_required
def api_embrioes_relink():
    """Re-runs lookup_matriz for every lote and updates doadora_matriz_id."""
    db = get_db()
    rows = db.execute("SELECT id, doadora FROM embriao_lote").fetchall()
    atualizados = 0
    for r in rows:
        novo = lookup_matriz(r['doadora'])
        db.execute(
            "UPDATE embriao_lote SET doadora_matriz_id=? WHERE id=?",
            (novo, r['id'])
        )
        atualizados += 1
    db.commit()
    return jsonify({'ok': True, 'atualizados': atualizados})
```

- [ ] **Step 4: Run tests — pass**

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_embriao_routes.py
git commit -m "feat(embrioes): add doadora-info and relink helper endpoints"
```

---

## Task 11: Template — lista de lotes (`/embrioes`)

**Files:**
- Create: `templates/embrioes.html`

- [ ] **Step 1: Create the template**

Create `templates/embrioes.html` with full content:

```html
{% extends "base.html" %}
{% block title %}Estoque de Embriões – Porto do Engenho{% endblock %}
{% block page_title %}Estoque de Embriões{% endblock %}

{% block content %}
<!-- KPI cards -->
<div class="kpi-grid">
    <div class="kpi-card"><div class="kpi-label">Estoque Total</div><div class="kpi-value" id="kTotal">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Sex F</div><div class="kpi-value" id="kSexF">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Convencional</div><div class="kpi-value" id="kConv">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Doadoras</div><div class="kpi-value" id="kDoadoras">-</div></div>
    <div class="kpi-card"><div class="kpi-label">Touros</div><div class="kpi-value" id="kTouros">-</div></div>
    <div class="kpi-card green"><div class="kpi-label">Receita Total</div><div class="kpi-value" id="kReceita">-</div></div>
</div>

<!-- Filters / actions -->
<div class="filters">
    <input type="text" id="fBusca" placeholder="Buscar doadora ou touro..." style="width:220px">
    <select id="fTipo">
        <option value="">Todos os tipos</option>
        <option value="Sex F">Sex F</option>
        <option value="Conv.">Convencional</option>
    </select>
    <select id="fTouro"><option value="">Todos os touros</option></select>
    <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer">
        <input type="checkbox" id="fZerados"> Mostrar lotes zerados
    </label>
    <button class="btn btn-outline btn-sm" onclick="loadLista()">Filtrar</button>
    <div style="flex:1"></div>
    <a href="/embrioes/importar" class="btn btn-black btn-sm">Importar PDF FIVET</a>
    {% if is_master %}<a href="/embrioes/reconciliar" class="btn btn-outline btn-sm">Reconciliar</a>{% endif %}
    <button class="btn btn-red btn-sm" onclick="abrirNovoLote()">+ Lote manual</button>
</div>

<!-- Table -->
<div class="panel">
    <div class="panel-body" style="overflow-x:auto">
        <table class="data-table">
            <thead>
                <tr>
                    <th>DT OPU</th><th>DT Vitrif.</th><th>Doadora</th><th>Touro</th>
                    <th>Tipo</th><th>Qtd Inicial</th><th>Qtd Atual</th><th>Ações</th>
                </tr>
            </thead>
            <tbody id="lotesBody">
                <tr><td colspan="8" style="text-align:center;color:var(--gray40);padding:40px">Carregando...</td></tr>
            </tbody>
        </table>
    </div>
</div>

<!-- Modal: novo lote manual -->
<div id="novoLoteModal" class="modal-overlay">
    <div class="modal-box" style="width:520px">
        <h3 style="font-family:'Barlow Condensed',sans-serif;font-size:18px;margin-bottom:16px">Novo Lote Manual</h3>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
            <div class="form-field"><label>DT OPU</label><input type="text" id="nDtOpu" placeholder="dd/mm/aa"></div>
            <div class="form-field"><label>DT Vitrificação</label><input type="text" id="nDtVit" placeholder="dd/mm/aa"></div>
            <div class="form-field"><label>Doadora *</label><input type="text" id="nDoadora"></div>
            <div class="form-field"><label>Touro *</label><input type="text" id="nTouro"></div>
            <div class="form-field"><label>Tipo Sêmen *</label>
                <select id="nTipo"><option value="Sex F">Sex F</option><option value="Conv.">Conv.</option></select>
            </div>
            <div class="form-field"><label>Quantidade *</label><input type="number" id="nQtd" min="1"></div>
            <div class="form-field" style="grid-column:1/-1"><label>Observação</label><input type="text" id="nObs"></div>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
            <button class="btn btn-outline btn-sm" onclick="fecharModal('novoLoteModal')">Cancelar</button>
            <button class="btn btn-red btn-sm" onclick="salvarNovoLote()">Criar</button>
        </div>
    </div>
</div>

<style>
.modal-overlay { display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:200;align-items:center;justify-content:center; }
.modal-box { background:var(--white);border-radius:8px;padding:32px;box-shadow:0 8px 32px rgba(0,0,0,0.3); }
.form-field { margin-bottom:12px; }
.form-field label { display:block;font-size:11px;color:var(--gray60);margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px; }
.form-field input, .form-field select { width:100%;padding:8px 12px;border:1px solid var(--gray20);border-radius:6px;font-family:'Barlow',sans-serif;font-size:13px; }
.doadora-link { color:var(--red);text-decoration:none;font-weight:600; }
.doadora-link:hover { text-decoration:underline; }
</style>
{% endblock %}

{% block scripts %}
<script>
function fecharModal(id) { document.getElementById(id).style.display = 'none'; }

async function loadKpis() {
    const k = await API.get('/api/embrioes/kpis');
    if (!k) return;
    document.getElementById('kTotal').textContent = k.total;
    document.getElementById('kSexF').textContent = k.sex_f;
    document.getElementById('kConv').textContent = k.conv;
    document.getElementById('kDoadoras').textContent = k.doadoras;
    document.getElementById('kTouros').textContent = k.touros;
    document.getElementById('kReceita').textContent = k.receita_total
        ? `R$ ${Number(k.receita_total).toLocaleString('pt-BR')}` : 'R$ 0';
}

async function loadLista() {
    const params = new URLSearchParams();
    const q = document.getElementById('fBusca').value.trim();
    if (q) params.set('q', q);
    const tipo = document.getElementById('fTipo').value;
    if (tipo) params.set('tipo', tipo);
    const touro = document.getElementById('fTouro').value;
    if (touro) params.set('touro', touro);
    if (document.getElementById('fZerados').checked) params.set('zerados', '1');

    const data = await API.get(`/api/embrioes?${params}`);
    const tbody = document.getElementById('lotesBody');
    if (!data || data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:var(--gray40);padding:40px">Nenhum lote. Use "+ Lote manual" ou "Importar PDF FIVET".</td></tr>';
        return;
    }
    // Populate touro filter dropdown
    const tourosSet = new Set(data.map(l => l.touro));
    const sel = document.getElementById('fTouro');
    const currentVal = sel.value;
    sel.innerHTML = '<option value="">Todos os touros</option>' +
        [...tourosSet].sort().map(t => `<option value="${t}">${t}</option>`).join('');
    sel.value = currentVal;

    tbody.innerHTML = data.map(l => {
        const zerado = l.qtd_atual === 0;
        const doadora = l.doadora_matriz_id
            ? `<a class="doadora-link" href="/ficha?id=${encodeURIComponent(l.doadora_matriz_id)}">${l.doadora} ↗</a>`
            : l.doadora;
        return `<tr style="${zerado ? 'opacity:0.5' : ''}">
            <td class="mono" style="font-size:12px">${l.dt_opu || '-'}</td>
            <td class="mono" style="font-size:12px">${l.dt_vitrificacao || '-'}</td>
            <td>${doadora}</td>
            <td style="font-size:12px">${l.touro}</td>
            <td>${l.tipo_semen}</td>
            <td class="mono">${l.qtd_inicial}</td>
            <td class="mono"><strong>${l.qtd_atual}</strong></td>
            <td style="white-space:nowrap">
                <a class="btn btn-outline btn-sm" href="/embrioes/${l.id}">Ver</a>
            </td>
        </tr>`;
    }).join('');
}

function abrirNovoLote() {
    ['nDtOpu','nDtVit','nDoadora','nTouro','nQtd','nObs'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('nTipo').value = 'Sex F';
    document.getElementById('novoLoteModal').style.display = 'flex';
}

async function salvarNovoLote() {
    const body = {
        dt_opu: document.getElementById('nDtOpu').value.trim(),
        dt_vitrificacao: document.getElementById('nDtVit').value.trim(),
        doadora: document.getElementById('nDoadora').value.trim(),
        touro: document.getElementById('nTouro').value.trim(),
        tipo_semen: document.getElementById('nTipo').value,
        qtd: parseInt(document.getElementById('nQtd').value || '0'),
        obs: document.getElementById('nObs').value.trim(),
    };
    const res = await fetch('/api/embrioes', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.ok) {
        fecharModal('novoLoteModal');
        loadKpis();
        loadLista();
    } else {
        alert(data.erro || 'Erro ao criar lote');
    }
}

loadKpis();
loadLista();
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke test in browser**

Run:
```bash
python app.py
```
Open http://localhost:5000/embrioes (after login). Expected:
- 6 KPI cards show zero values
- Empty table with "Nenhum lote" message
- Filters + buttons render
- "+ Lote manual" opens modal; creating a lote refreshes list and KPIs

Stop server.

- [ ] **Step 3: Commit**

```bash
git add templates/embrioes.html
git commit -m "feat(embrioes): add main list template with KPIs and filters"
```

---

## Task 12: Template — detalhe do lote (`/embrioes/<id>`)

**Files:**
- Create: `templates/embrioes_detalhe.html`

- [ ] **Step 1: Create the template**

Create `templates/embrioes_detalhe.html`:

```html
{% extends "base.html" %}
{% block title %}Detalhe do Lote – Porto do Engenho{% endblock %}
{% block page_title %}Lote de Embriões{% endblock %}

{% block content %}
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
    <a href="/embrioes" style="color:var(--gray40);text-decoration:none;font-size:13px">Estoque</a>
    <span style="color:var(--gray20)">/</span>
    <h2 id="loteTitulo" style="font-family:'Barlow Condensed',sans-serif;font-size:20px">Carregando...</h2>
</div>

<div style="display:grid;grid-template-columns:2fr 1fr;gap:20px">
    <!-- Coluna principal -->
    <div>
        <!-- Header info -->
        <div class="panel">
            <div class="panel-header" style="border-bottom:3px solid var(--red)">
                <h2 style="font-size:15px">Informações do Lote</h2>
            </div>
            <div class="panel-body" id="loteInfo" style="padding:16px"></div>
        </div>

        <!-- KPIs -->
        <div class="kpi-grid" style="margin-top:20px">
            <div class="kpi-card"><div class="kpi-label">Restante</div><div class="kpi-value" id="kRest">-</div></div>
            <div class="kpi-card"><div class="kpi-label">Usado em TE</div><div class="kpi-value" id="kTE">-</div></div>
            <div class="kpi-card"><div class="kpi-label">Vendido</div><div class="kpi-value" id="kVend">-</div></div>
            <div class="kpi-card green"><div class="kpi-label">Receita</div><div class="kpi-value" id="kRec">-</div></div>
        </div>

        <!-- Movimentos -->
        <div class="panel" style="margin-top:20px">
            <div class="panel-header" style="display:flex;justify-content:space-between;align-items:center">
                <h2 style="font-size:15px">Movimentos</h2>
                <div>
                    <button class="btn btn-outline btn-sm" onclick="abrirMov('te_interna')">+ TE</button>
                    <button class="btn btn-outline btn-sm" onclick="abrirMov('venda')">+ Venda</button>
                    <button class="btn btn-outline btn-sm" onclick="abrirMov('perda')">+ Perda</button>
                </div>
            </div>
            <div class="panel-body" style="padding:0;overflow-x:auto">
                <table class="data-table">
                    <thead><tr><th>Data</th><th>Tipo</th><th>Qtd</th><th>Detalhes</th><th>Usuário</th><th></th></tr></thead>
                    <tbody id="movBody"></tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Coluna lateral: doadora -->
    <aside>
        <div id="doadoraPanel" class="panel" style="display:none">
            <div class="panel-header" style="border-bottom:3px solid var(--red)">
                <h2 style="font-size:15px">Sobre a doadora</h2>
            </div>
            <div class="panel-body" id="doadoraBody" style="padding:16px"></div>
        </div>
        {% if is_master %}
        <div style="margin-top:12px">
            <button class="btn btn-outline btn-sm" onclick="excluirLote()" style="width:100%;color:var(--red)">Excluir lote</button>
        </div>
        {% endif %}
    </aside>
</div>

<!-- Modal de movimento -->
<div id="movModal" class="modal-overlay">
    <div class="modal-box" style="width:420px">
        <h3 id="movTitulo" style="font-family:'Barlow Condensed',sans-serif;font-size:18px;margin-bottom:16px"></h3>
        <div class="form-field"><label>Quantidade *</label><input type="number" id="mQtd" min="1"></div>
        <div class="form-field"><label>Data *</label><input type="text" id="mData" placeholder="dd/mm/aaaa"></div>
        <div class="form-field" id="campoReceptora" style="display:none"><label>Receptora (opcional)</label><input type="text" id="mReceptora"></div>
        <div class="form-field" id="campoComprador" style="display:none"><label>Comprador *</label><input type="text" id="mComprador"></div>
        <div class="form-field" id="campoValor" style="display:none">
            <label>Valor unitário (R$) *</label>
            <input type="number" id="mValor" step="0.01" min="0">
            <div style="font-size:11px;color:var(--gray60);margin-top:4px">Valor total: <span id="mValorTotal">R$ 0,00</span></div>
        </div>
        <div class="form-field"><label>Observação</label><input type="text" id="mObs"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px">
            <button class="btn btn-outline btn-sm" onclick="fecharModal('movModal')">Cancelar</button>
            <button class="btn btn-red btn-sm" onclick="salvarMov()">Registrar</button>
        </div>
    </div>
</div>

<style>
.modal-overlay { display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:200;align-items:center;justify-content:center; }
.modal-box { background:var(--white);border-radius:8px;padding:32px;box-shadow:0 8px 32px rgba(0,0,0,0.3); }
.form-field { margin-bottom:12px; }
.form-field label { display:block;font-size:11px;color:var(--gray60);margin-bottom:4px;font-weight:600;text-transform:uppercase;letter-spacing:0.3px; }
.form-field input { width:100%;padding:8px 12px;border:1px solid var(--gray20);border-radius:6px;font-family:'Barlow',sans-serif;font-size:13px; }
.info-row { display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--gray5); }
.info-row:last-child { border-bottom:none; }
.info-row .lbl { color:var(--gray60);font-size:12px; }
.info-row .val { font-family:'DM Mono',monospace;font-size:13px; }
.badge-mov-te { background:#E3F4EA;color:var(--ok); }
.badge-mov-venda { background:#FFF3DD;color:#A06800; }
.badge-mov-perda { background:#F5E2E3;color:var(--red); }
.badge-mov-ajuste { background:#E3E8F0;color:#2E4D7B; }
</style>
{% endblock %}

{% block scripts %}
<script>
const LOTE_ID = {{ lote_id }};
let currentMovTipo = null;

function fecharModal(id) { document.getElementById(id).style.display = 'none'; }

function fmtMoney(v) {
    return v ? `R$ ${Number(v).toLocaleString('pt-BR', {minimumFractionDigits:2})}` : 'R$ 0,00';
}

async function loadLote() {
    const data = await API.get(`/api/embrioes/${LOTE_ID}`);
    if (!data || data.erro) { alert(data.erro || 'Erro'); return; }
    const l = data.lote;
    document.getElementById('loteTitulo').textContent = `${l.doadora} × ${l.touro} (${l.tipo_semen})`;

    document.getElementById('loteInfo').innerHTML = `
        <div class="info-row"><span class="lbl">DT OPU</span><span class="val">${l.dt_opu || '-'}</span></div>
        <div class="info-row"><span class="lbl">DT Vitrificação</span><span class="val">${l.dt_vitrificacao || '-'}</span></div>
        <div class="info-row"><span class="lbl">Doadora</span><span class="val">${l.doadora}</span></div>
        <div class="info-row"><span class="lbl">Touro</span><span class="val">${l.touro}</span></div>
        <div class="info-row"><span class="lbl">Tipo Sêmen</span><span class="val">${l.tipo_semen}</span></div>
        <div class="info-row"><span class="lbl">Qtd Inicial</span><span class="val">${l.qtd_inicial}</span></div>
        <div class="info-row"><span class="lbl">Qtd Atual</span><span class="val"><strong>${l.qtd_atual}</strong></span></div>
        <div class="info-row"><span class="lbl">Origem</span><span class="val">${l.arquivo_origem || '-'}</span></div>
        ${l.obs ? `<div class="info-row"><span class="lbl">Obs</span><span class="val">${l.obs}</span></div>` : ''}
    `;

    document.getElementById('kRest').textContent = data.kpis.restante;
    document.getElementById('kTE').textContent = data.kpis.usado_te;
    document.getElementById('kVend').textContent = data.kpis.vendido;
    document.getElementById('kRec').textContent = fmtMoney(data.kpis.receita);

    // Movimentos
    const tbody = document.getElementById('movBody');
    if (!data.movimentos.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--gray40);padding:30px">Nenhum movimento registrado</td></tr>';
    } else {
        tbody.innerHTML = data.movimentos.map(m => {
            let detalhes = '';
            let badgeClass = '';
            if (m.tipo === 'te_interna') {
                detalhes = m.receptora ? `Receptora: ${m.receptora}` : '(sem receptora)';
                badgeClass = 'badge-mov-te';
            } else if (m.tipo === 'venda') {
                detalhes = `${m.comprador || '?'} · ${fmtMoney(m.valor_unit)}/un · Total ${fmtMoney(m.valor_total)}`;
                badgeClass = 'badge-mov-venda';
            } else if (m.tipo === 'perda') {
                detalhes = m.obs || '-';
                badgeClass = 'badge-mov-perda';
            } else if (m.tipo === 'ajuste_lab') {
                detalhes = m.obs || '-';
                badgeClass = 'badge-mov-ajuste';
            }
            const tipoLabel = {
                te_interna: 'TE', venda: 'Venda', perda: 'Perda', ajuste_lab: 'Ajuste'
            }[m.tipo] || m.tipo;
            return `<tr>
                <td class="mono">${m.data}</td>
                <td><span class="badge ${badgeClass}">${tipoLabel}</span></td>
                <td class="mono"><strong>${m.qtd}</strong></td>
                <td style="font-size:12px">${detalhes}</td>
                <td style="font-size:12px">${m.user_nome || '-'}</td>
                <td><button class="btn btn-outline btn-sm" onclick="excluirMov(${m.id})">Excluir</button></td>
            </tr>`;
        }).join('');
    }

    // Doadora panel
    if (l.doadora_matriz_id) {
        const dInfo = await API.get(`/api/embrioes/doadora-info/${encodeURIComponent(l.doadora_matriz_id)}`);
        if (dInfo && !dInfo.erro) {
            document.getElementById('doadoraPanel').style.display = 'block';
            document.getElementById('doadoraBody').innerHTML = `
                <div class="info-row"><span class="lbl">ID</span><span class="val">${dInfo.animal_id}</span></div>
                <div class="info-row"><span class="lbl">Categoria</span><span class="val">${dInfo.categoria || '-'}</span></div>
                <div class="info-row"><span class="lbl">ICIAGen</span><span class="val">${dInfo.iciagen != null ? dInfo.iciagen.toFixed(2) : '-'}</span></div>
                <div class="info-row"><span class="lbl">IDESM</span><span class="val">${dInfo.idesm != null ? dInfo.idesm.toFixed(2) : '-'}</span></div>
                <div class="info-row"><span class="lbl">RMat</span><span class="val">${dInfo.rmat != null ? dInfo.rmat.toFixed(2) : '-'}</span></div>
                <div style="margin-top:12px"><a class="btn btn-red btn-sm" href="/ficha?id=${encodeURIComponent(dInfo.animal_id)}" style="display:block;text-align:center">Ver ficha completa ↗</a></div>
            `;
        }
    }
}

function abrirMov(tipo) {
    currentMovTipo = tipo;
    document.getElementById('mQtd').value = '';
    document.getElementById('mData').value = new Date().toLocaleDateString('pt-BR');
    document.getElementById('mReceptora').value = '';
    document.getElementById('mComprador').value = '';
    document.getElementById('mValor').value = '';
    document.getElementById('mObs').value = '';
    document.getElementById('mValorTotal').textContent = 'R$ 0,00';
    document.getElementById('campoReceptora').style.display = tipo === 'te_interna' ? 'block' : 'none';
    document.getElementById('campoComprador').style.display = tipo === 'venda' ? 'block' : 'none';
    document.getElementById('campoValor').style.display = tipo === 'venda' ? 'block' : 'none';
    const titulos = {
        te_interna: 'Registrar TE Interna',
        venda: 'Registrar Venda',
        perda: 'Registrar Perda / Descarte'
    };
    document.getElementById('movTitulo').textContent = titulos[tipo];
    document.getElementById('movModal').style.display = 'flex';
}

document.getElementById('mValor').addEventListener('input', recalcTotal);
document.getElementById('mQtd').addEventListener('input', recalcTotal);
function recalcTotal() {
    if (currentMovTipo !== 'venda') return;
    const q = parseInt(document.getElementById('mQtd').value || '0');
    const v = parseFloat(document.getElementById('mValor').value || '0');
    document.getElementById('mValorTotal').textContent = `R$ ${(q * v).toLocaleString('pt-BR', {minimumFractionDigits:2})}`;
}

async function salvarMov() {
    const body = {
        tipo: currentMovTipo,
        qtd: parseInt(document.getElementById('mQtd').value || '0'),
        data: document.getElementById('mData').value.trim(),
        receptora: document.getElementById('mReceptora').value.trim(),
        comprador: document.getElementById('mComprador').value.trim(),
        valor_unit: parseFloat(document.getElementById('mValor').value || '0') || null,
        obs: document.getElementById('mObs').value.trim(),
    };
    if (!body.qtd || body.qtd <= 0) { alert('Quantidade obrigatória'); return; }
    if (!body.data) { alert('Data obrigatória'); return; }
    if (currentMovTipo === 'venda' && !body.comprador) { alert('Comprador obrigatório'); return; }
    const res = await fetch(`/api/embrioes/${LOTE_ID}/movimento`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
    });
    const data = await res.json();
    if (data.ok) {
        fecharModal('movModal');
        loadLote();
    } else {
        alert(data.erro || 'Erro');
    }
}

async function excluirMov(mid) {
    if (!confirm('Excluir movimento? A quantidade volta para o lote.')) return;
    const res = await fetch(`/api/embrioes/movimento/${mid}`, {method: 'DELETE'});
    const data = await res.json();
    if (data.ok) loadLote();
    else alert(data.erro || 'Erro');
}

async function excluirLote() {
    if (!confirm('Excluir este lote? Movimentos serão removidos junto.')) return;
    const res = await fetch(`/api/embrioes/${LOTE_ID}`, {method: 'DELETE'});
    const data = await res.json();
    if (data.ok) window.location = '/embrioes';
    else alert(data.erro || 'Erro');
}

loadLote();
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke test in browser**

Run `python app.py`, navigate to `/embrioes`, click "+ Lote manual", create a lote (doadora "TESTE1", touro "TESTE", tipo "Sex F", qtd 6). Click "Ver" on the new row. Verify:
- Detalhe renders with info, 4 KPIs at zero (except restante=6)
- "Movimentos: Nenhum movimento registrado"
- Buttons + TE / + Venda / + Perda open modal with correct fields
- Register a TE of 2 — saldo becomes 4, mov appears
- Register a Venda of 1 at R$ 1000 — receita = R$ 1000, saldo becomes 3
- Excluir movimento volta saldo
- If you're master, "Excluir lote" works

- [ ] **Step 3: Commit**

```bash
git add templates/embrioes_detalhe.html
git commit -m "feat(embrioes): add lote detail template with movement registration"
```

---

## Task 13: Template — importar PDF (`/embrioes/importar`)

**Files:**
- Create: `templates/embrioes_importar.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}Importar PDF FIVET – Porto do Engenho{% endblock %}
{% block page_title %}Importar PDF do FIVET{% endblock %}

{% block content %}
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
    <a href="/embrioes" style="color:var(--gray40);text-decoration:none;font-size:13px">Estoque</a>
    <span style="color:var(--gray20)">/</span>
    <h2 style="font-family:'Barlow Condensed',sans-serif;font-size:20px">Importar PDF FIVET</h2>
</div>

<!-- Step 1: Upload -->
<div id="step1" class="panel">
    <div class="panel-body" style="padding:24px;text-align:center">
        <p style="margin-bottom:16px;color:var(--gray60)">Selecione o PDF mais recente do laboratório FIVET. O sistema lê a tabela e mostra um preview editável antes de salvar.</p>
        <input type="file" id="arquivoInput" accept=".pdf" style="font-size:14px">
        <div style="margin-top:16px">
            <button class="btn btn-red" onclick="fazerPreview()">Analisar PDF</button>
        </div>
        <div id="step1Erro" style="margin-top:12px;color:var(--red);display:none"></div>
    </div>
</div>

<!-- Step 2: Preview editavel -->
<div id="step2" style="display:none">
    <div id="bannerTotal" class="flash" style="margin-bottom:16px"></div>
    <div class="panel">
        <div class="panel-header" style="display:flex;justify-content:space-between;align-items:center">
            <h2 style="font-size:15px">Preview da Importação</h2>
            <div>
                <button class="btn btn-outline btn-sm" onclick="resetar()">Voltar</button>
                <button class="btn btn-red btn-sm" onclick="confirmar()">Confirmar Importação</button>
            </div>
        </div>
        <div class="panel-body" style="padding:0;overflow-x:auto">
            <table class="data-table">
                <thead><tr><th>DT OPU</th><th>DT Vitrif.</th><th>Doadora</th><th>Touro</th><th>Tipo</th><th>Qtd</th><th>Obs</th><th></th></tr></thead>
                <tbody id="previewBody"></tbody>
            </table>
        </div>
    </div>
</div>

<!-- Step 3: Result -->
<div id="step3" style="display:none" class="panel">
    <div class="panel-body" style="padding:32px;text-align:center">
        <h3 style="font-size:18px;margin-bottom:16px">Importação concluída!</h3>
        <p id="resultadoTexto" style="margin-bottom:24px"></p>
        <a class="btn btn-red" href="/embrioes">Ver estoque</a>
        <button class="btn btn-outline" onclick="resetar()" style="margin-left:8px">Importar outro PDF</button>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
let parsed = null;

async function fazerPreview() {
    const f = document.getElementById('arquivoInput').files[0];
    if (!f) { alert('Selecione um PDF'); return; }
    const fd = new FormData();
    fd.append('arquivo', f);
    const res = await fetch('/api/embrioes/importar/preview', {method: 'POST', body: fd});
    const data = await res.json();
    if (data.erro) {
        document.getElementById('step1Erro').style.display = 'block';
        document.getElementById('step1Erro').textContent = data.erro;
        return;
    }
    parsed = data;
    renderPreview();
    document.getElementById('step1').style.display = 'none';
    document.getElementById('step2').style.display = 'block';
}

function renderPreview() {
    const soma = parsed.linhas.reduce((s, l) => s + (parseInt(l.qtd) || 0), 0);
    const banner = document.getElementById('bannerTotal');
    if (parsed.total_declarado === soma) {
        banner.className = 'flash success';
        banner.textContent = `✓ Planilha de ${parsed.data_planilha || '?'}: ${parsed.linhas.length} lotes, ${soma} embriões (bate com total declarado).`;
    } else {
        banner.className = 'flash error';
        banner.textContent = `⚠ Divergência: total declarado ${parsed.total_declarado}, soma das linhas ${soma}. Revise antes de importar.`;
    }
    document.getElementById('previewBody').innerHTML = parsed.linhas.map((l, i) => `
        <tr>
            <td><input value="${l.dt_opu || ''}" data-i="${i}" data-k="dt_opu" style="width:80px"></td>
            <td><input value="${l.dt_vitrificacao || ''}" data-i="${i}" data-k="dt_vitrificacao" style="width:80px"></td>
            <td><input value="${l.doadora || ''}" data-i="${i}" data-k="doadora" style="width:80px"></td>
            <td><input value="${l.touro || ''}" data-i="${i}" data-k="touro" style="width:140px"></td>
            <td><select data-i="${i}" data-k="tipo_semen">
                <option value="Sex F" ${l.tipo_semen === 'Sex F' ? 'selected' : ''}>Sex F</option>
                <option value="Conv." ${l.tipo_semen === 'Conv.' ? 'selected' : ''}>Conv.</option>
            </select></td>
            <td><input type="number" value="${l.qtd || 0}" data-i="${i}" data-k="qtd" style="width:60px"></td>
            <td><input value="${l.obs || ''}" data-i="${i}" data-k="obs" style="width:120px"></td>
            <td><button class="btn btn-outline btn-sm" onclick="removerLinha(${i})">×</button></td>
        </tr>
    `).join('');
    document.querySelectorAll('#previewBody input, #previewBody select').forEach(el => {
        el.addEventListener('change', () => {
            const i = parseInt(el.dataset.i);
            const k = el.dataset.k;
            parsed.linhas[i][k] = el.value;
            if (k === 'qtd') renderPreview();
        });
    });
}

function removerLinha(i) {
    parsed.linhas.splice(i, 1);
    renderPreview();
}

async function confirmar() {
    const res = await fetch('/api/embrioes/importar/confirmar', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            arquivo: parsed.arquivo,
            data_planilha: parsed.data_planilha,
            linhas: parsed.linhas
        })
    });
    const data = await res.json();
    if (data.ok) {
        document.getElementById('resultadoTexto').innerHTML =
            `<strong>${data.novos}</strong> lotes novos importados.<br>` +
            `<strong>${data.ignorados}</strong> ignorados (já existiam).<br>` +
            `Total na planilha: ${data.total} embriões.`;
        document.getElementById('step2').style.display = 'none';
        document.getElementById('step3').style.display = 'block';
    } else {
        alert(data.erro || 'Erro');
    }
}

function resetar() {
    parsed = null;
    document.getElementById('arquivoInput').value = '';
    document.getElementById('step1Erro').style.display = 'none';
    document.getElementById('step1').style.display = 'block';
    document.getElementById('step2').style.display = 'none';
    document.getElementById('step3').style.display = 'none';
}
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke test**

Run app. Place sample FIVET PDF at any location. Navigate to `/embrioes/importar`. Upload PDF. Verify:
- Preview shows extracted rows
- Banner green if total matches, yellow if not
- Editing a row's qtd recalculates the banner
- "Confirmar Importação" creates lotes; verify in `/embrioes`
- Re-uploading the same PDF shows "X ignorados"

- [ ] **Step 3: Commit**

```bash
git add templates/embrioes_importar.html
git commit -m "feat(embrioes): add PDF import template with editable preview"
```

---

## Task 14: Template — reconciliar (`/embrioes/reconciliar`)

**Files:**
- Create: `templates/embrioes_reconciliar.html`

- [ ] **Step 1: Create the template**

```html
{% extends "base.html" %}
{% block title %}Reconciliar Estoque – Porto do Engenho{% endblock %}
{% block page_title %}Reconciliar com PDF do FIVET{% endblock %}

{% block content %}
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
    <a href="/embrioes" style="color:var(--gray40);text-decoration:none;font-size:13px">Estoque</a>
    <span style="color:var(--gray20)">/</span>
    <h2 style="font-family:'Barlow Condensed',sans-serif;font-size:20px">Reconciliar com PDF</h2>
</div>

<div id="step1" class="panel">
    <div class="panel-body" style="padding:24px;text-align:center">
        <p style="margin-bottom:16px;color:var(--gray60)">A reconciliação compara o estoque do sistema com o PDF mais recente do laboratório. Use quando suspeitar de divergência ou em conferência periódica.</p>
        <input type="file" id="arquivoRec" accept=".pdf" style="font-size:14px">
        <div style="margin-top:16px">
            <button class="btn btn-red" onclick="analisar()">Analisar Divergências</button>
        </div>
        <div id="step1Erro" style="margin-top:12px;color:var(--red);display:none"></div>
    </div>
</div>

<div id="step2" style="display:none">
    <div id="resumoDiff" class="panel" style="margin-bottom:20px">
        <div class="panel-body" id="resumoDiffBody" style="padding:16px"></div>
    </div>

    <div class="panel" id="painelDivergentes" style="margin-bottom:16px;display:none">
        <div class="panel-header"><h2 style="font-size:15px">Lotes com Qtd. Divergente</h2></div>
        <div class="panel-body" style="padding:0;overflow-x:auto">
            <table class="data-table">
                <thead><tr><th>Doadora</th><th>Touro</th><th>Tipo</th><th>Vitrif.</th><th>Sistema</th><th>Planilha</th><th>Diff</th><th>Aplicar?</th></tr></thead>
                <tbody id="divBody"></tbody>
            </table>
        </div>
    </div>

    <div class="panel" id="painelSumidos" style="margin-bottom:16px;display:none">
        <div class="panel-header"><h2 style="font-size:15px">Lotes Sumidos do Lab</h2></div>
        <div class="panel-body" style="padding:0;overflow-x:auto">
            <table class="data-table">
                <thead><tr><th>Doadora</th><th>Touro</th><th>Tipo</th><th>Vitrif.</th><th>Sistema</th><th>Zerar?</th></tr></thead>
                <tbody id="sumBody"></tbody>
            </table>
        </div>
    </div>

    <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn btn-outline" onclick="resetar()">Cancelar</button>
        <button class="btn btn-red" onclick="aplicarSelecionados()">Aplicar Ajustes Marcados</button>
    </div>
</div>

<div id="step3" style="display:none" class="panel">
    <div class="panel-body" style="padding:32px;text-align:center">
        <h3 style="font-size:18px;margin-bottom:16px">Reconciliação concluída</h3>
        <p id="resultadoRec" style="margin-bottom:24px"></p>
        <a class="btn btn-red" href="/embrioes">Voltar ao estoque</a>
    </div>
</div>
{% endblock %}

{% block scripts %}
<script>
let diff = null;
let dataPlanilha = null;

async function analisar() {
    const f = document.getElementById('arquivoRec').files[0];
    if (!f) { alert('Selecione um PDF'); return; }
    const fd = new FormData();
    fd.append('arquivo', f);
    // First parse the PDF using the same preview endpoint
    const preview = await fetch('/api/embrioes/importar/preview', {method: 'POST', body: fd});
    const parsed = await preview.json();
    if (parsed.erro) {
        document.getElementById('step1Erro').style.display = 'block';
        document.getElementById('step1Erro').textContent = parsed.erro;
        return;
    }
    dataPlanilha = parsed.data_planilha;
    const rec = await fetch('/api/embrioes/reconciliar/preview', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({linhas: parsed.linhas, data_planilha: parsed.data_planilha})
    });
    diff = await rec.json();
    if (diff.erro) { alert(diff.erro); return; }
    renderDiff();
    document.getElementById('step1').style.display = 'none';
    document.getElementById('step2').style.display = 'block';
}

function renderDiff() {
    document.getElementById('resumoDiffBody').innerHTML = `
        <p>Comparando com planilha de <strong>${dataPlanilha || '?'}</strong>:</p>
        <ul style="margin:8px 0 0 24px">
            <li><strong>${diff.divergentes.length}</strong> lotes com qtd divergente</li>
            <li><strong>${diff.sumidos.length}</strong> lotes sumiram do lab</li>
            <li><strong>${diff.novos.length}</strong> lotes novos (use Importar para adicioná-los)</li>
        </ul>
    `;

    if (diff.divergentes.length) {
        document.getElementById('painelDivergentes').style.display = 'block';
        document.getElementById('divBody').innerHTML = diff.divergentes.map(d => `
            <tr>
                <td>${d.doadora}</td><td>${d.touro}</td><td>${d.tipo_semen}</td><td>${d.dt_vitrificacao || '-'}</td>
                <td class="mono">${d.sistema}</td>
                <td class="mono">${d.planilha}</td>
                <td class="mono" style="color:${d.diferenca < 0 ? 'var(--red)' : 'var(--ok)'}">
                    ${d.diferenca > 0 ? '+' : ''}${d.diferenca}
                </td>
                <td><input type="checkbox" class="diff-check" data-id="${d.lote_id}" data-diff="${d.diferenca}"></td>
            </tr>
        `).join('');
    }

    if (diff.sumidos.length) {
        document.getElementById('painelSumidos').style.display = 'block';
        document.getElementById('sumBody').innerHTML = diff.sumidos.map(s => `
            <tr>
                <td>${s.doadora}</td><td>${s.touro}</td><td>${s.tipo_semen}</td><td>${s.dt_vitrificacao || '-'}</td>
                <td class="mono">${s.sistema}</td>
                <td><input type="checkbox" class="sum-check" data-id="${s.lote_id}" data-sistema="${s.sistema}"></td>
            </tr>
        `).join('');
    }
}

async function aplicarSelecionados() {
    const ajustes = [];
    document.querySelectorAll('.diff-check:checked').forEach(el => {
        ajustes.push({lote_id: parseInt(el.dataset.id), diferenca: parseInt(el.dataset.diff)});
    });
    document.querySelectorAll('.sum-check:checked').forEach(el => {
        ajustes.push({lote_id: parseInt(el.dataset.id), diferenca: -parseInt(el.dataset.sistema)});
    });
    if (!ajustes.length) { alert('Marque pelo menos um item para aplicar'); return; }
    const res = await fetch('/api/embrioes/reconciliar/aplicar', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ajustes, data_planilha: dataPlanilha})
    });
    const data = await res.json();
    if (data.ok) {
        document.getElementById('resultadoRec').textContent = `${data.aplicados} ajustes aplicados.`;
        document.getElementById('step2').style.display = 'none';
        document.getElementById('step3').style.display = 'block';
    } else {
        alert(data.erro || 'Erro');
    }
}

function resetar() {
    diff = null;
    dataPlanilha = null;
    document.getElementById('arquivoRec').value = '';
    document.getElementById('step1Erro').style.display = 'none';
    document.getElementById('step1').style.display = 'block';
    document.getElementById('step2').style.display = 'none';
    document.getElementById('step3').style.display = 'none';
    document.getElementById('painelDivergentes').style.display = 'none';
    document.getElementById('painelSumidos').style.display = 'none';
}
</script>
{% endblock %}
```

- [ ] **Step 2: Smoke test**

As master user, navigate to `/embrioes/reconciliar`. Upload the same FIVET PDF you imported earlier:
- Expected: `0 divergentes, 0 sumidos, 0 novos` (perfect match)

Then: register a manual movement (TE -2 in some lote) and re-upload the PDF:
- Expected: 1 divergente showing sistema=4, planilha=6, diff=+2
- Mark it and apply: lote goes back to 6, ajuste_lab movement appears in the lote's detail

- [ ] **Step 3: Commit**

```bash
git add templates/embrioes_reconciliar.html
git commit -m "feat(embrioes): add reconciliation template with diff selection"
```

---

## Task 15: Final acceptance verification (spec section 12)

**Files:** (none modified — verification only)

This task walks through each acceptance criterion from the spec.

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v
```
Expected: all green.

- [ ] **Step 2: Acceptance #1 — import the example PDF**

If you have the FIVET sample PDF (the 30/04/2026 one with 136 total):
- Drop a fresh dev DB (or use an empty embrião table): `python -c "from app import init_db, app; ctx=app.app_context(); ctx.push(); init_db()"`
- Navigate to `/embrioes/importar`, upload, confirm.
- Verify: ~40 new lotes; SUM(qtd_atual) = 136.

Run:
```bash
python -c "import sqlite3; c=sqlite3.connect('data/fazenda167.db'); print('lotes:', c.execute('SELECT COUNT(*) FROM embriao_lote').fetchone()[0]); print('total:', c.execute('SELECT SUM(qtd_atual) FROM embriao_lote').fetchone()[0])"
```

- [ ] **Step 3: Acceptance #2 — reimport ignores duplicates**

Re-upload the same PDF. Expected: "0 novos, ~40 ignorados".

- [ ] **Step 4: Acceptance #3 — TE decrements**

Find a lote with qtd 6. Register TE of 2. Verify qtd_atual=4 in `/embrioes` list and in lote detail's movimentos.

- [ ] **Step 5: Acceptance #4 — delete movement restores**

Delete the TE created in step 4. Verify qtd_atual back to 6.

- [ ] **Step 6: Acceptance #5 — doadora link**

Verify in `/embrioes`: a row with a doadora matching `matrizes.animal_id` renders as clickable link with ↗ icon. A row with no match renders as plain text.

Run quick check:
```bash
python -c "import sqlite3; c=sqlite3.connect('data/fazenda167.db'); print(c.execute('SELECT doadora, doadora_matriz_id FROM embriao_lote LIMIT 10').fetchall())"
```

- [ ] **Step 7: Acceptance #6 — non-master denied reconciliação**

Log in as a non-master user (create one if needed). Navigate to `/embrioes/reconciliar`. Expected: 403 or redirect.

- [ ] **Step 8: Acceptance #7 — saldo insuficiente**

Try registering a perda of 999 in a lote that has qtd_atual=5. Expected: error toast "Saldo insuficiente (disponível: 5)", and qtd_atual unchanged.

- [ ] **Step 9: Acceptance #8 — KPIs match SQL**

Compare `/embrioes` page KPIs against direct SQL:
```bash
python -c "import sqlite3; c=sqlite3.connect('data/fazenda167.db'); print('total:', c.execute('SELECT SUM(qtd_atual) FROM embriao_lote').fetchone()[0]); print('sex_f:', c.execute(\"SELECT SUM(qtd_atual) FROM embriao_lote WHERE tipo_semen='Sex F'\").fetchone()[0]); print('conv:', c.execute(\"SELECT SUM(qtd_atual) FROM embriao_lote WHERE tipo_semen='Conv.'\").fetchone()[0])"
```
KPIs in the UI must match these numbers.

- [ ] **Step 10: Final commit (if any cleanup)**

If steps revealed any issue requiring code change, fix and commit. Otherwise nothing to commit — just record completion:

```bash
git log --oneline | head -20
```

Confirm all 14 feature commits + spec commit are present on the branch.

---

## Done

The `/embrioes` module is complete: schema, parser, 15+ endpoints, 4 templates, sidebar entry, and an automated test suite that future changes can trust.
