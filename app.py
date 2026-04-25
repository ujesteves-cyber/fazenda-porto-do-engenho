import os
import sqlite3
import json
import csv
import io
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/Sao_Paulo')


def now_local():
    """Datetime atual em America/Sao_Paulo, sem tzinfo (para armazenar em SQLite)."""
    return datetime.now(TZ).replace(tzinfo=None)


def now_local_str():
    return now_local().strftime('%Y-%m-%d %H:%M:%S')


def _catalog_normalize(nome):
    if not nome:
        return ''
    return ' '.join(nome.strip().upper().split())


def _catalog_find_or_create(db, descricao, unidade=None):
    """Localiza item no catálogo (case-insensitive); cria se novo. Retorna id ou None."""
    nome = _catalog_normalize(descricao)
    if not nome:
        return None
    row = db.execute("SELECT id FROM itens_catalogo WHERE nome = ? COLLATE NOCASE",
                     (nome,)).fetchone()
    if row:
        return row['id'] if isinstance(row, sqlite3.Row) else row[0]
    cur = db.execute(
        "INSERT INTO itens_catalogo (nome, unidade) VALUES (?, ?)",
        (nome, (unidade or '').strip() or None)
    )
    return cur.lastrowid


def _catalog_touch(db, cat_id, when=None):
    """Incrementa contador e atualiza último uso do item."""
    when = when or now_local_str()
    db.execute(
        "UPDATE itens_catalogo SET n_pedidos = n_pedidos + 1, ultimo_uso = ? WHERE id = ?",
        (when, cat_id)
    )


def _catalog_recalc(db):
    """Recalcula n_pedidos e ultimo_uso a partir de requisicoes_itens."""
    db.execute("""
        UPDATE itens_catalogo
        SET n_pedidos = COALESCE((
                SELECT COUNT(*) FROM requisicoes_itens
                WHERE item_catalogo_id = itens_catalogo.id
            ), 0),
            ultimo_uso = (
                SELECT MAX(r.data_solicitacao)
                FROM requisicoes_itens ri
                JOIN requisicoes_compra r ON r.id = ri.requisicao_id
                WHERE ri.item_catalogo_id = itens_catalogo.id
            )
    """)

import bcrypt
import xlrd
import anthropic
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g, send_file, Response, after_this_request
)
import tempfile
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')

DATA_DIR = os.getenv('DATA_DIR') or os.path.join(os.path.dirname(__file__), 'data')
DATABASE = os.path.join(DATA_DIR, 'fazenda167.db')
UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER') or os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ── Database ────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DATABASE)
    with open(os.path.join(os.path.dirname(__file__), 'schema.sql'), encoding='utf-8') as f:
        db.executescript(f.read())

    # Migração: coluna `papel` em usuarios (bancos legados)
    cols = [r[1] for r in db.execute("PRAGMA table_info(usuarios)").fetchall()]
    if 'papel' not in cols:
        db.execute("ALTER TABLE usuarios ADD COLUMN papel TEXT NOT NULL DEFAULT 'usuario'")

    # Migração: coluna `item_catalogo_id` em requisicoes_itens
    cols = [r[1] for r in db.execute("PRAGMA table_info(requisicoes_itens)").fetchall()]
    if 'item_catalogo_id' not in cols:
        db.execute("ALTER TABLE requisicoes_itens ADD COLUMN item_catalogo_id INTEGER REFERENCES itens_catalogo(id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_req_itens_cat ON requisicoes_itens(item_catalogo_id)")

    # Backfill: linka itens existentes ao catálogo e recalcula estatísticas
    db.row_factory = sqlite3.Row
    orfaos = db.execute("""
        SELECT id, descricao, quantidade FROM requisicoes_itens
        WHERE item_catalogo_id IS NULL AND descricao IS NOT NULL AND TRIM(descricao) <> ''
    """).fetchall()
    for it in orfaos:
        cat_id = _catalog_find_or_create(db, it['descricao'], it['quantidade'])
        if cat_id:
            db.execute("UPDATE requisicoes_itens SET item_catalogo_id=? WHERE id=?", (cat_id, it['id']))
    if orfaos:
        _catalog_recalc(db)

    # Bootstrap do usuário master (Dr. Anselmo) a partir das variáveis de ambiente
    admin_email = (os.getenv('ADMIN_EMAIL') or '').strip().lower()
    admin_pass  = os.getenv('ADMIN_PASSWORD')
    if admin_email:
        existing = db.execute(
            "SELECT id FROM usuarios WHERE lower(email)=?", (admin_email,)
        ).fetchone()
        if existing:
            db.execute("UPDATE usuarios SET papel='master' WHERE lower(email)=? AND papel<>'master'",
                       (admin_email,))
        elif admin_pass:
            senha_hash = bcrypt.hashpw(admin_pass.encode(), bcrypt.gensalt()).decode()
            db.execute(
                "INSERT INTO usuarios (nome, email, senha_hash, papel) VALUES (?, ?, ?, 'master')",
                ('Dr. Anselmo', admin_email, senha_hash)
            )

    db.commit()
    db.close()


# ── Auth ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'erro': 'Não autenticado'}), 401
        return f(*args, **kwargs)
    return decorated


def current_user():
    if 'user_id' not in session:
        return None
    return get_db().execute(
        "SELECT id, nome, email, papel FROM usuarios WHERE id=?",
        (session['user_id'],)
    ).fetchone()


def is_master(user=None):
    user = user or current_user()
    return bool(user and user['papel'] == 'master')


def master_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        if not is_master():
            return "Acesso restrito ao autorizador (master).", 403
        return f(*args, **kwargs)
    return decorated


def api_master_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'erro': 'Não autenticado'}), 401
        if not is_master():
            return jsonify({'erro': 'Apenas o autorizador (master) pode executar esta ação.'}), 403
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_user_ctx():
    if 'user_id' not in session:
        return {'current_user': None, 'is_master': False}
    user = current_user()
    return {'current_user': user, 'is_master': is_master(user)}


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('password', '').encode()
        user = get_db().execute(
            "SELECT * FROM usuarios WHERE email=? AND ativo=1", (email,)
        ).fetchone()
        if user and bcrypt.checkpw(senha, user['senha_hash'].encode()):
            session['user_id'] = user['id']
            session['user_nome'] = user['nome']
            next_url = request.args.get('next', '/')
            return redirect(next_url)
        flash('E-mail ou senha incorretos.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Helper: encode/decode animal IDs with spaces ───────────────────

def encode_id(animal_id):
    return animal_id.replace(' ', '__') if animal_id else ''


def decode_id(encoded):
    return encoded.replace('__', ' ') if encoded else ''


# ── Helper: get latest rodada ──────────────────────────────────────

def get_ultima_rodada(db):
    return db.execute("SELECT * FROM rodadas ORDER BY id DESC LIMIT 1").fetchone()


# ── Page routes ────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html')


@app.route('/indices')
@login_required
def indices():
    return render_template('indices.html')


@app.route('/posicao')
@login_required
def posicao():
    return render_template('posicao.html')


@app.route('/matrizes')
@login_required
def matrizes_page():
    return render_template('matrizes.html')


@app.route('/ficha')
@login_required
def ficha():
    return render_template('ficha.html')


@app.route('/touros')
@login_required
def touros():
    return render_template('touros.html')


@app.route('/ficha/certidao/<path:animal_id_enc>')
@login_required
def certidao_animal(animal_id_enc):
    return render_template('certidao_animal.html', animal_id_enc=animal_id_enc)


@app.route('/rebanho')
@login_required
def rebanho_page():
    return render_template('rebanho.html')


@app.route('/evolucao')
@login_required
def evolucao_page():
    return render_template('evolucao.html')


@app.route('/safras')
@login_required
def safras_page():
    return render_template('safras.html')


@app.route('/importar')
@login_required
def importar_page():
    return render_template('importar.html')


@app.route('/estoque')
@login_required
def estoque_page():
    return render_template('estoque.html')


@app.route('/estoque/<int:grupo_id>')
@login_required
def estoque_grupo_page(grupo_id):
    return render_template('estoque_grupo.html', grupo_id=grupo_id)


@app.route('/estoque/<int:grupo_id>/catalogo')
@login_required
def catalogo_grupo(grupo_id):
    return render_template('catalogo_touro.html', grupo_id=grupo_id)


# ── API: Dashboard ─────────────────────────────────────────────────

@app.route('/api/dashboard/kpis')
@api_login_required
def api_dashboard_kpis():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({'erro': 'Nenhuma rodada importada'}), 404

    rid = rodada['id']

    iciagen_avg = db.execute(
        "SELECT AVG(iciagen) as v FROM avaliacoes WHERE rodada_id=?", (rid,)
    ).fetchone()['v'] or 0

    idesm_avg = db.execute(
        "SELECT AVG(idesm) as v FROM avaliacoes WHERE rodada_id=?", (rid,)
    ).fetchone()['v'] or 0

    ceip_total = db.execute(
        "SELECT COUNT(*) as v FROM matrizes WHERE ceip=1 AND rodada_id=?", (rid,)
    ).fetchone()['v']
    total_mat = db.execute(
        "SELECT COUNT(*) as v FROM matrizes WHERE rodada_id=?", (rid,)
    ).fetchone()['v']

    ipp_avg = db.execute(
        "SELECT AVG(ipp) as v FROM matrizes WHERE ipp IS NOT NULL AND rodada_id=?", (rid,)
    ).fetchone()['v'] or 0

    rmat_avg = db.execute(
        "SELECT AVG(rmat) as v FROM avaliacoes WHERE rmat IS NOT NULL AND rodada_id=?", (rid,)
    ).fetchone()['v'] or 0

    return jsonify({
        'rodada': rodada['nome'],
        'iciagen_avg': round(iciagen_avg, 2),
        'idesm_avg': round(idesm_avg, 2),
        'ceip_total': ceip_total,
        'ceip_pct': round(ceip_total / total_mat * 100, 1) if total_mat else 0,
        'total_matrizes': total_mat,
        'ipp_avg': round(ipp_avg, 1),
        'rmat_avg': round(rmat_avg, 2),
    })


@app.route('/api/dashboard/safras')
@api_login_required
def api_dashboard_safras():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify([])

    rows = db.execute("""
        SELECT SUBSTR(m.data_nasc, 7, 4) as safra, AVG(a.iciagen) as avg_icia, COUNT(*) as n
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.rodada_id = ? AND m.data_nasc IS NOT NULL
        GROUP BY safra
        ORDER BY safra
    """, (rodada['id'],)).fetchall()

    return jsonify([{'safra': r['safra'], 'avg_icia': round(r['avg_icia'], 2) if r['avg_icia'] is not None else 0, 'n': r['n']} for r in rows])


@app.route('/api/dashboard/composicao')
@api_login_required
def api_dashboard_composicao():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify([])

    rows = db.execute("""
        SELECT categoria,
               COUNT(*) as total,
               SUM(genotipada) as genotipadas,
               SUM(precoce) as precoces,
               SUM(ceip) as ceips
        FROM matrizes WHERE rodada_id=?
        GROUP BY categoria ORDER BY categoria
    """, (rodada['id'],)).fetchall()

    return jsonify([dict(r) for r in rows])


@app.route('/api/dashboard/idade')
@api_login_required
def api_dashboard_idade():
    """Distribution of animals by birth year (matrizes + produtos combined)."""
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify([])

    rid = rodada['id']

    # Matrizes by birth year
    mat_rows = db.execute("""
        SELECT SUBSTR(data_nasc, 7, 4) as ano, COUNT(*) as n
        FROM matrizes WHERE rodada_id=? AND data_nasc IS NOT NULL AND ativo=1
        GROUP BY ano ORDER BY ano
    """, (rid,)).fetchall()

    # Produtos by birth year
    prod_rows = db.execute("""
        SELECT safra_ano as ano, COUNT(*) as n
        FROM produtos WHERE rodada_id=? AND safra_ano IS NOT NULL
        GROUP BY safra_ano ORDER BY safra_ano
    """, (rid,)).fetchall()

    # Merge into one structure
    all_years = {}
    for r in mat_rows:
        if r['ano'] and len(r['ano']) == 4:
            all_years.setdefault(r['ano'], {'matrizes': 0, 'produtos': 0})
            all_years[r['ano']]['matrizes'] = r['n']
    for r in prod_rows:
        if r['ano'] and len(r['ano']) == 4:
            all_years.setdefault(r['ano'], {'matrizes': 0, 'produtos': 0})
            all_years[r['ano']]['produtos'] = r['n']

    result = [{'ano': k, 'matrizes': v['matrizes'], 'produtos': v['produtos']}
              for k, v in sorted(all_years.items())]
    return jsonify(result)


@app.route('/api/dashboard/top10')
@api_login_required
def api_dashboard_top10():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify([])

    rows = db.execute("""
        SELECT m.animal_id, m.categoria, a.iciagen, a.deca_icia_g, a.idesm, a.rmat, m.ceip
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.rodada_id = ?
        ORDER BY a.iciagen DESC LIMIT 10
    """, (rodada['id'],)).fetchall()

    return jsonify([{**dict(r), 'animal_id_enc': encode_id(r['animal_id'])} for r in rows])


@app.route('/api/dashboard/alertas')
@api_login_required
def api_dashboard_alertas():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({'atencao': [], 'destaques': []})

    rows = db.execute("""
        SELECT m.animal_id, a.perc_icia, a.perc_idesm, a.perc_rmat
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.rodada_id = ?
    """, (rodada['id'],)).fetchall()

    atencao = []
    destaques = []
    for r in rows:
        if r['perc_icia'] and r['perc_icia'] >= 70:
            atencao.append({'animal_id': r['animal_id'], 'indicador': 'ICIAGen', 'perc': r['perc_icia']})
        if r['perc_icia'] and r['perc_icia'] <= 30:
            destaques.append({'animal_id': r['animal_id'], 'indicador': 'ICIAGen', 'perc': r['perc_icia']})

    atencao.sort(key=lambda x: x['perc'], reverse=True)
    destaques.sort(key=lambda x: x['perc'])

    return jsonify({'atencao': atencao[:10], 'destaques': destaques[:10]})


@app.route('/api/dashboard/dist_deca')
@api_login_required
def api_dashboard_dist_deca():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify([])

    rows = db.execute("""
        SELECT deca_icia_g as deca, COUNT(*) as n
        FROM avaliacoes WHERE rodada_id=? AND deca_icia_g IS NOT NULL
        GROUP BY deca_icia_g ORDER BY deca_icia_g
    """, (rodada['id'],)).fetchall()

    return jsonify([dict(r) for r in rows])


# ── API: Matrizes ──────────────────────────────────────────────────

@app.route('/api/matrizes')
@api_login_required
def api_matrizes():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({'data': [], 'total': 0})

    rid = rodada['id']
    conditions = ["m.rodada_id = ?"]
    params = [rid]

    q = request.args.get('q', '').strip()
    if q:
        conditions.append("(m.animal_id LIKE ? OR m.touro_pai LIKE ?)")
        params.extend([f'%{q}%', f'%{q}%'])

    ceip = request.args.get('ceip')
    if ceip == '1':
        conditions.append("m.ceip = 1")
    elif ceip == '0':
        conditions.append("m.ceip = 0")

    categ = request.args.get('categ')
    if categ:
        conditions.append("m.categoria = ?")
        params.append(categ)

    deca = request.args.get('deca')
    if deca:
        conditions.append("a.deca_icia_g = ?")
        params.append(int(deca))

    precoce = request.args.get('precoce')
    if precoce == '1':
        conditions.append("m.precoce = 1")
    elif precoce == '0':
        conditions.append("m.precoce = 0")

    iciagen_min = request.args.get('iciagen_min')
    if iciagen_min:
        conditions.append("a.iciagen >= ?")
        params.append(float(iciagen_min))

    idesm_min = request.args.get('idesm_min')
    if idesm_min:
        conditions.append("a.idesm >= ?")
        params.append(float(idesm_min))

    rmat_min = request.args.get('rmat_min')
    if rmat_min:
        conditions.append("a.rmat >= ?")
        params.append(float(rmat_min))

    genotipada = request.args.get('genotipada')
    if genotipada == '1':
        conditions.append("m.genotipada = 1")
    elif genotipada == '0':
        conditions.append("m.genotipada = 0")

    touro_pai = request.args.get('touro_pai')
    if touro_pai:
        conditions.append("m.touro_pai = ?")
        params.append(touro_pai)

    safra = request.args.get('safra')
    if safra:
        conditions.append("SUBSTR(m.data_nasc, 7, 4) = ?")
        params.append(safra)

    ativo_filter = request.args.get('ativo')
    if ativo_filter == '0':
        conditions.append("m.ativo = 0")
    elif ativo_filter != 'todos':
        conditions.append("m.ativo = 1")  # default: only active

    where = " AND ".join(conditions)
    order = request.args.get('order', 'iciagen')
    allowed_orders = {
        'iciagen': 'a.iciagen DESC',
        'idesm': 'a.idesm DESC',
        'rmat': 'a.rmat DESC',
        'ipp': 'm.ipp ASC',
        'pv': 'm.pv DESC',
        'animal_id': 'm.animal_id ASC',
        'categoria': 'm.categoria ASC',
    }
    order_sql = allowed_orders.get(order, 'a.iciagen DESC')

    limit = min(int(request.args.get('limit', 10)), 100)
    offset = int(request.args.get('offset', 0))

    total = db.execute(f"""
        SELECT COUNT(*) as n
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE {where}
    """, params).fetchone()['n']

    rows = db.execute(f"""
        SELECT m.animal_id, m.categoria, m.touro_pai, m.data_nasc, m.ipp, m.pv,
               m.precoce, m.ceip, m.genotipada, m.ativo,
               a.iciagen, a.deca_icia_g, a.deca_icia_f, a.idesm, a.rmat
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE {where}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    data = []
    for r in rows:
        d = dict(r)
        d['animal_id_enc'] = encode_id(r['animal_id'])
        safra = r['data_nasc'][-4:] if r['data_nasc'] and len(r['data_nasc']) >= 4 else ''
        d['safra'] = safra
        data.append(d)

    return jsonify({'data': data, 'total': total})


@app.route('/api/export/matrizes.csv')
@api_login_required
def api_export_matrizes_csv():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return "Nenhuma rodada", 404

    rows = db.execute("""
        SELECT m.animal_id, m.categoria, m.touro_pai, m.data_nasc, m.ipp, m.iep, m.pv,
               m.precoce, m.ceip, m.genotipada,
               a.iciagen, a.deca_icia_g, a.deca_icia_f, a.idesm, a.deca_idesm_g, a.rmat, a.ifrig
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.rodada_id = ?
        ORDER BY a.iciagen DESC
    """, (rodada['id'],)).fetchall()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Animal ID', 'Categoria', 'Touro Pai', 'Nascimento', 'IPP', 'IEP', 'PV',
                     'Precoce', 'CEIP', 'Genotipada', 'ICIAGen', 'Deca G', 'Deca F',
                     'IDESM', 'Deca IDESM', 'RMat', 'IFRIG'])
    for r in rows:
        writer.writerow([r[k] for k in r.keys()])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=matrizes_porto_engenho.csv'}
    )


# ── API: Filter options (for matrizes page) ───────────────────────

@app.route('/api/filtros')
@api_login_required
def api_filtros():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({'touros': [], 'safras': []})

    rid = rodada['id']
    touros = db.execute("""
        SELECT DISTINCT touro_pai FROM matrizes
        WHERE rodada_id=? AND touro_pai IS NOT NULL AND touro_pai != ''
        ORDER BY touro_pai
    """, (rid,)).fetchall()

    safras = db.execute("""
        SELECT DISTINCT SUBSTR(data_nasc, 7, 4) as safra FROM matrizes
        WHERE rodada_id=? AND data_nasc IS NOT NULL
        ORDER BY safra
    """, (rid,)).fetchall()

    return jsonify({
        'touros': [r['touro_pai'] for r in touros],
        'safras': [r['safra'] for r in safras if r['safra']],
    })


# ── API: Dropdown ──────────────────────────────────────────────────

@app.route('/api/dropdown')
@api_login_required
def api_dropdown():
    db = get_db()
    result = []

    # Matrizes (all, not just last rodada - so user can find inactive ones too)
    mat_rows = db.execute("""
        SELECT m.animal_id, a.iciagen, m.ceip, m.touro_pai, 'Matriz' as tipo
        FROM matrizes m
        LEFT JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        ORDER BY a.iciagen DESC
    """).fetchall()

    for r in mat_rows:
        result.append({
            'animal_id': r['animal_id'],
            'animal_id_enc': encode_id(r['animal_id']),
            'iciagen': r['iciagen'],
            'ceip': r['ceip'],
            'touro_pai': r['touro_pai'],
            'tipo': 'Matriz',
        })

    # Produtos (latest rodada per product, exclude those already in matrizes)
    prod_rows = db.execute("""
        SELECT p.produto_id as animal_id, p.iciagen, p.ceip, p.touro as touro_pai,
               'Produto' as tipo
        FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY produto_id ORDER BY rodada_id DESC) as rn
            FROM produtos
        ) p
        WHERE p.rn = 1
          AND p.produto_id NOT IN (SELECT animal_id FROM matrizes)
        ORDER BY p.iciagen DESC
    """).fetchall()

    for r in prod_rows:
        result.append({
            'animal_id': r['animal_id'],
            'animal_id_enc': encode_id(r['animal_id']),
            'iciagen': r['iciagen'],
            'ceip': r['ceip'],
            'touro_pai': r['touro_pai'],
            'tipo': 'Produto',
        })

    # Sort all by iciagen desc
    result.sort(key=lambda x: x['iciagen'] or -999, reverse=True)
    return jsonify(result)


# ── API: Animal / Ficha ───────────────────────────────────────────

@app.route('/api/animal/<path:animal_id_enc>')
@api_login_required
def api_animal(animal_id_enc):
    animal_id = decode_id(animal_id_enc)
    db = get_db()

    mat = db.execute("SELECT * FROM matrizes WHERE animal_id=?", (animal_id,)).fetchone()

    # If not found in matrizes, check produtos
    is_produto = False
    if not mat:
        prod = db.execute("""
            SELECT * FROM produtos WHERE produto_id=? ORDER BY rodada_id DESC LIMIT 1
        """, (animal_id,)).fetchone()
        if not prod:
            return jsonify({'erro': 'Animal nao encontrado'}), 404
        # Build a pseudo-matriz dict from produto data
        is_produto = True
        mat = {
            'animal_id': prod['produto_id'], 'data_nasc': prod['data_nasc'],
            'touro_pai': prod['touro'], 'tipo_serv': prod['tipo_serv'],
            'mae_id': prod['mae_id'], 'avo_paterno': None,
            'avo_materno': prod['avo_materno'], 'rebanho': None,
            'genotipada': 0, 'categoria': None, 'precoce': 0,
            'ceip': prod['ceip'], 'np_ceip': 0,
            'score_r': None, 'score_f': None, 'score_a': None, 'score_p': None,
            'rank_cia': None, 'ipp': None, 'iep': None, 'pv': None,
            'desc_ap': None, 'rodada_id': prod['rodada_id'],
            'sexo': prod['sexo'],
        }

    aval = db.execute("""
        SELECT * FROM avaliacoes WHERE animal_id=? ORDER BY rodada_id DESC LIMIT 1
    """, (animal_id,)).fetchone()

    # For produtos, build pseudo avaliacao from product data if no avaliacao
    if not aval and is_produto:
        aval = {
            'iciagen': prod['iciagen'], 'deca_icia_g': prod['deca_iciagen'],
            'deca_icia_f': None, 'perc_icia': None, 'acc_icia': None,
            'idesm': prod['idesm'], 'deca_idesm_g': prod['deca_idesm'],
            'deca_idesm_f': None, 'perc_idesm': None, 'acc_idesm': None,
            'rmat': prod['rmat'], 'deca_rmat': None, 'perc_rmat': None, 'acc_rmat': None,
            'ifrig': prod['ifrig'], 'hgp': None, 'ncaract': None,
            'dep_pn': None, 'dep_gnd': None, 'dep_cd': None, 'dep_pd': None,
            'dep_md': None, 'dep_ud': None, 'dep_gpd': None, 'dep_cs': None,
            'dep_ps': None, 'dep_ms': None, 'dep_us': None,
            'dep_temp': None, 'dep_gns': None, 'dep_pei': None, 'dep_peip': None,
        }

    # Dados da mãe (se estiver no rebanho)
    mae_dados = None
    if mat['mae_id']:
        mae = db.execute("SELECT animal_id FROM matrizes WHERE animal_id=?", (mat['mae_id'],)).fetchone()
        if mae:
            mae_dados = {'animal_id': mae['animal_id'], 'animal_id_enc': encode_id(mae['animal_id'])}

    # Filhos (machos e fêmeas)
    filhos = db.execute("""
        SELECT p.*, r.nome as rodada_nome
        FROM produtos p JOIN rodadas r ON r.id = p.rodada_id
        WHERE p.mae_id = ?
        GROUP BY p.produto_id
        HAVING p.rodada_id = MAX(p.rodada_id)
        ORDER BY p.data_nasc ASC
    """, (animal_id,)).fetchall()

    # Filhas ativas na fazenda (que estão na tabela matrizes)
    filhas_fazenda = db.execute("""
        SELECT m.animal_id, a.iciagen, a.deca_icia_g, m.categoria, m.ceip
        FROM matrizes m
        LEFT JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.mae_id = ?
        ORDER BY a.iciagen DESC
    """, (animal_id,)).fetchall()

    # Idade calculada
    idade = None
    if mat['data_nasc']:
        try:
            dn = datetime.strptime(mat['data_nasc'], '%d/%m/%Y')
            delta = datetime.now() - dn
            anos = delta.days // 365
            meses = (delta.days % 365) // 30
            idade = f"{anos}a {meses}m"
        except ValueError:
            pass

    # Get rodada info
    rodada_info = None
    if mat['rodada_id']:
        rod = db.execute("SELECT nome FROM rodadas WHERE id=?", (mat['rodada_id'],)).fetchone()
        if rod:
            rodada_info = rod['nome']

    return jsonify({
        'matriz': dict(mat),
        'avaliacao': dict(aval) if aval else None,
        'mae_dados': mae_dados,
        'filhos': [dict(f) for f in filhos],
        'filhas_fazenda': [{**dict(ff), 'animal_id_enc': encode_id(ff['animal_id'])} for ff in filhas_fazenda],
        'idade': idade,
        'animal_id_enc': encode_id(animal_id),
        'rodada_nome': rodada_info,
        'is_produto': is_produto,
    })


@app.route('/api/animal/<path:animal_id_enc>/historico')
@api_login_required
def api_animal_historico(animal_id_enc):
    animal_id = decode_id(animal_id_enc)
    db = get_db()

    rows = db.execute("""
        SELECT a.*, r.nome as rodada_nome
        FROM avaliacoes a JOIN rodadas r ON r.id = a.rodada_id
        WHERE a.animal_id = ?
        ORDER BY r.id
    """, (animal_id,)).fetchall()

    return jsonify([dict(r) for r in rows])


# ── API: Rodadas ───────────────────────────────────────────────────

@app.route('/api/rodadas')
@api_login_required
def api_rodadas():
    db = get_db()
    rows = db.execute("SELECT * FROM rodadas ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/rodadas/<int:rid>', methods=['DELETE'])
@api_login_required
def api_delete_rodada(rid):
    db = get_db()
    db.execute("DELETE FROM produtos WHERE rodada_id=?", (rid,))
    db.execute("DELETE FROM avaliacoes WHERE rodada_id=?", (rid,))
    db.execute("DELETE FROM matrizes WHERE rodada_id=?", (rid,))
    db.execute("DELETE FROM rodadas WHERE id=?", (rid,))
    db.commit()
    return jsonify({'ok': True})


# ── API: Importar ──────────────────────────────────────────────────

def safe_float(val):
    if val is None or val == '' or val == '-':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def safe_int(val):
    if val is None or val == '' or val == '-':
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def read_xls_rows(filepath, preferred_sheets=None):
    """Read XLS/XLSX file and return list of dicts (header → value).
    Supports both .xls (xlrd) and .xlsx (openpyxl).
    Auto-detects header row (skips title rows)."""

    if preferred_sheets is None:
        preferred_sheets = ['matrizes', 'geral']

    ext = os.path.splitext(filepath)[1].lower()

    if ext == '.xlsx':
        import openpyxl
        wb = openpyxl.load_workbook(filepath, data_only=True)
        # Find preferred sheet
        ws = None
        for name in preferred_sheets:
            if name in wb.sheetnames:
                ws = wb[name]
                break
        if ws is None:
            ws = wb.active

        # Read all rows as lists
        all_rows = []
        for row in ws.iter_rows(values_only=True):
            all_rows.append([v for v in row])
    else:
        # .xls format
        wb = xlrd.open_workbook(filepath)
        sheet = None
        for name in preferred_sheets:
            if name in wb.sheet_names():
                sheet = wb.sheet_by_name(name)
                break
        if sheet is None:
            sheet = wb.sheet_by_index(0)

        all_rows = []
        for i in range(sheet.nrows):
            all_rows.append([sheet.cell_value(i, j) for j in range(sheet.ncols)])

    if not all_rows:
        return [], []

    # Auto-detect header row: find first row with 3+ non-empty distinct values
    header_idx = 0
    for i, row in enumerate(all_rows[:5]):
        non_empty = [str(v).strip() for v in row if v is not None and str(v).strip()]
        if len(non_empty) >= 3 and len(set(non_empty)) >= 3:
            header_idx = i
            break

    headers = [str(v).strip() if v else '' for v in all_rows[header_idx]]

    rows = []
    for i in range(header_idx + 1, len(all_rows)):
        row_vals = all_rows[i]
        row_data = {}
        for j, h in enumerate(headers):
            val = row_vals[j] if j < len(row_vals) else None
            if h:
                row_data[h] = val
            row_data[f'_col_{j}'] = val
        rows.append(row_data)
    return rows, headers


def format_date(val):
    """Try to format a date value from XLS."""
    if not val:
        return None
    if isinstance(val, float):
        try:
            from xlrd import xldate_as_tuple
            dt = xldate_as_tuple(val, 0)
            return f"{dt[2]:02d}/{dt[1]:02d}/{dt[0]:04d}"
        except Exception:
            return None
    s = str(val).strip()
    if s:
        return s
    return None


@app.route('/api/importar', methods=['POST'])
@api_login_required
def api_importar():
    db = get_db()
    nome_rodada = request.form.get('nome_rodada', '').strip()
    if not nome_rodada:
        return jsonify({'erro': 'Nome da rodada é obrigatório'}), 400

    arquivo_mat = request.files.get('arquivo_mat')
    # Support multiple safra files
    arquivos_saf = request.files.getlist('arquivo_saf')
    # Filter out empty file inputs
    arquivos_saf = [f for f in arquivos_saf if f and f.filename]

    if not arquivo_mat and not arquivos_saf:
        return jsonify({'erro': 'Envie pelo menos um arquivo'}), 400

    saf_names = ', '.join(f.filename for f in arquivos_saf) if arquivos_saf else None

    # Create rodada
    cur = db.execute(
        "INSERT INTO rodadas (nome, arquivo_mat, arquivo_saf) VALUES (?, ?, ?)",
        (nome_rodada,
         arquivo_mat.filename if arquivo_mat else None,
         saf_names)
    )
    rodada_id = cur.lastrowid

    n_matrizes = 0
    n_produtos = 0

    # Import matrizes
    if arquivo_mat:
        mat_path = os.path.join(UPLOAD_FOLDER, arquivo_mat.filename)
        arquivo_mat.save(mat_path)

        rows, headers = read_xls_rows(mat_path)

        # Find indices for duplicate columns (IPP, PV)
        ipp_indices = [i for i, h in enumerate(headers) if h == 'IPP']
        pv_indices = [i for i, h in enumerate(headers) if h == 'PV']
        ipp_col = ipp_indices[-1] if len(ipp_indices) > 1 else (ipp_indices[0] if ipp_indices else None)
        pv_col = pv_indices[-1] if len(pv_indices) > 1 else (pv_indices[0] if pv_indices else None)

        for r in rows:
            animal_id = str(r.get('vaca', '')).strip()
            if not animal_id:
                continue

            try:
                data_nasc = format_date(r.get('dataN'))
                geno_val = r.get('geno', 0)
                genotipada = 1 if geno_val and str(geno_val).strip() not in ('0', '', 'N') else 0
                precoce_val = r.get('precoce', 0)
                precoce = 1 if precoce_val and str(precoce_val).strip() not in ('0', '', 'N') else 0

                # CEIP can be "SIM"/text or number
                ceip_raw = str(r.get('CEIP', '')).strip().upper()
                ceip_flag = 1 if ceip_raw in ('SIM', 'S', '1', 'TRUE') or safe_int(r.get('CEIP')) else 0
                np_ceip = safe_int(r.get('np_CEIP'))

                ipp_val = safe_float(r.get(f'_col_{ipp_col}')) if ipp_col is not None else safe_float(r.get('IPP'))
                pv_val = safe_float(r.get(f'_col_{pv_col}')) if pv_col is not None else safe_float(r.get('PV'))

                # Upsert matrizes
                db.execute("""
                    INSERT INTO matrizes (
                        animal_id, data_nasc, touro_pai, tipo_serv, mae_id,
                        avo_paterno, avo_materno, rebanho, genotipada, categoria,
                        precoce, ceip, np_ceip, score_r, score_f, score_a, score_p,
                        rank_cia, ipp, iep, pv, desc_ap, rodada_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(animal_id) DO UPDATE SET
                        data_nasc=excluded.data_nasc, touro_pai=excluded.touro_pai,
                        tipo_serv=excluded.tipo_serv, mae_id=excluded.mae_id,
                        avo_paterno=excluded.avo_paterno, avo_materno=excluded.avo_materno,
                        rebanho=excluded.rebanho, genotipada=excluded.genotipada,
                        categoria=excluded.categoria, precoce=excluded.precoce,
                        ceip=excluded.ceip, np_ceip=excluded.np_ceip,
                        score_r=excluded.score_r, score_f=excluded.score_f,
                        score_a=excluded.score_a, score_p=excluded.score_p,
                        rank_cia=excluded.rank_cia, ipp=excluded.ipp, iep=excluded.iep,
                        pv=excluded.pv, desc_ap=excluded.desc_ap, rodada_id=excluded.rodada_id,
                        updated_at=CURRENT_TIMESTAMP
                """, (
                    animal_id, data_nasc,
                    str(r.get('touro_pai', '')).strip() or None,
                    str(r.get('TS', '')).strip() or None,
                    str(r.get('mae', '')).strip() or None,
                    str(r.get('avo_paterno', '')).strip() or None,
                    str(r.get('avo_materno', '')).strip() or None,
                    str(r.get('rebanho', '')).strip() or None,
                    genotipada,
                    str(r.get('categ', '')).strip() or None,
                    precoce, ceip_flag, np_ceip,
                    safe_float(r.get('R')), safe_float(r.get('F')),
                    safe_float(r.get('A')), safe_float(r.get('P')),
                    safe_int(r.get('rank')),
                    ipp_val,
                    safe_float(r.get('IEP')),
                    pv_val,
                    str(r.get('DESC_AP', '')).strip() or None,
                    rodada_id
                ))

                # Insert avaliação
                perc_icia = safe_float(r.get('perc_ICiaGen'))
                if perc_icia is not None and perc_icia <= 1:
                    perc_icia = round(perc_icia * 100, 2)

                perc_idesm = safe_float(r.get('perc_IDESM'))
                if perc_idesm is not None and perc_idesm <= 1:
                    perc_idesm = round(perc_idesm * 100, 2)

                perc_rmat = safe_float(r.get('perc_RMat'))
                if perc_rmat is not None and perc_rmat <= 1:
                    perc_rmat = round(perc_rmat * 100, 2)

                db.execute("""
                    INSERT OR REPLACE INTO avaliacoes (
                        animal_id, rodada_id,
                        iciagen, deca_icia_g, deca_icia_f, perc_icia, acc_icia,
                        idesm, deca_idesm_g, deca_idesm_f, perc_idesm, acc_idesm,
                        rmat, deca_rmat, perc_rmat, acc_rmat,
                        ifrig, hgp, ncaract,
                        dep_pn, dep_gnd, dep_cd, dep_pd, dep_md, dep_ud,
                        dep_gpd, dep_cs, dep_ps, dep_ms, dep_us,
                        dep_temp, dep_gns, dep_pei, dep_peip
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    animal_id, rodada_id,
                    safe_float(r.get('ICiaGen')),
                    safe_int(r.get('decaG_ICiaGen')),
                    safe_int(r.get('decaF_ICiaGen')),
                    perc_icia,
                    safe_float(r.get('acc_ICiaGen')),
                    safe_float(r.get('IDESM')),
                    safe_int(r.get('decaG_IDESM')),
                    safe_int(r.get('decaF_IDESM')),
                    perc_idesm,
                    safe_float(r.get('acc_IDESM')),
                    safe_float(r.get('RMat')),
                    safe_int(r.get('decaG_RMat')),
                    perc_rmat,
                    safe_float(r.get('acc_RMat')),
                    safe_float(r.get('IFRIG')),
                    safe_float(r.get('HGP')),
                    safe_int(r.get('ncaract')),
                    safe_float(r.get('PN')),
                    safe_float(r.get('GND')),
                    safe_float(r.get('CD')),
                    safe_float(r.get('PD')),
                    safe_float(r.get('MD')),
                    safe_float(r.get('UD')),
                    safe_float(r.get('GPD')),
                    safe_float(r.get('CS')),
                    safe_float(r.get('PS')),
                    safe_float(r.get('MS')),
                    safe_float(r.get('US')),
                    safe_float(r.get('TEMP')),
                    safe_float(r.get('GNS')),
                    safe_float(r.get('PEi')),
                    safe_float(r.get('PEip')),
                ))

                n_matrizes += 1
            except Exception as e:
                app.logger.warning(f"Erro ao importar matriz {animal_id}: {e}")
                continue

    # Import produtos (safra) — supports multiple files
    for arquivo_saf in arquivos_saf:
        saf_path = os.path.join(UPLOAD_FOLDER, arquivo_saf.filename)
        arquivo_saf.save(saf_path)

        rows, headers = read_xls_rows(saf_path)

        for r in rows:
            produto_id = str(r.get('produto', '')).strip()
            if not produto_id:
                continue

            try:
                # Handle boolean-like fields (can be "SIM", "S", 1, etc.)
                pai_dna_raw = str(r.get('pai_dna', '')).strip().upper()
                pai_dna = 1 if pai_dna_raw in ('SIM', 'S', '1', 'TRUE') or safe_int(r.get('pai_dna')) else 0
                ceip_raw = str(r.get('CEIP', '')).strip().upper()
                ceip_prod = 1 if ceip_raw in ('SIM', 'S', '1', 'TRUE') or safe_int(r.get('CEIP')) else 0

                data_nasc_prod = format_date(r.get('dataN'))
                safra_ano = data_nasc_prod[-4:] if data_nasc_prod and len(data_nasc_prod) >= 4 else None

                db.execute("""
                    INSERT OR IGNORE INTO produtos (
                        produto_id, rodada_id, mae_id, touro, tipo_serv, avo_materno,
                        data_nasc, sexo, pn, peso_desm, idade_desm, peso_sob, idade_sob,
                        gnd_aj, gpd_aj, iciagen, deca_iciagen, idesm, deca_idesm,
                        rmat, ifrig, conect_desm, pai_dna, ceip, safra_ano
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    produto_id, rodada_id,
                    str(r.get('vaca', '')).strip() or None,
                    str(r.get('touro', '')).strip() or None,
                    str(r.get('TS', '')).strip() or None,
                    str(r.get('avo_materno', '')).strip() or None,
                    data_nasc_prod,
                    str(r.get('sexo', '')).strip() or None,
                    safe_float(r.get('PN')),
                    safe_float(r.get('peso_DESM')),
                    safe_int(r.get('idade_DESM')),
                    safe_float(r.get('peso_SOB')),
                    safe_int(r.get('idade_SOB')),
                    safe_float(r.get('aj_GND')),
                    safe_float(r.get('aj_GPD')),
                    safe_float(r.get('ICiaGen')),
                    safe_int(r.get('decaG_ICiaGen')),
                    safe_float(r.get('IDESM')),
                    safe_int(r.get('decaG_IDESM')),
                    safe_float(r.get('RMat')),
                    safe_float(r.get('IFRIG')),
                    str(r.get('conect_DESM', '')).strip() or None,
                    pai_dna,
                    ceip_prod,
                    safra_ano,
                ))
                n_produtos += 1
            except Exception as e:
                app.logger.warning(f"Erro ao importar produto {produto_id}: {e}")
                continue

    # Mark animals not in this import as inactive (only if matrizes were imported)
    n_inativadas = 0
    if arquivo_mat and n_matrizes > 0:
        cur_inativ = db.execute(
            "UPDATE matrizes SET ativo=0 WHERE rodada_id != ? AND ativo=1",
            (rodada_id,)
        )
        n_inativadas = cur_inativ.rowcount
        # Ensure imported ones are active
        db.execute("UPDATE matrizes SET ativo=1 WHERE rodada_id=?", (rodada_id,))

    # Update rodada counts
    db.execute("UPDATE rodadas SET n_matrizes=?, n_produtos=? WHERE id=?",
               (n_matrizes, n_produtos, rodada_id))
    db.commit()

    return jsonify({
        'ok': True,
        'rodada_id': rodada_id,
        'n_matrizes': n_matrizes,
        'n_produtos': n_produtos,
        'n_inativadas': n_inativadas,
    })


# ── API: Touros ────────────────────────────────────────────────────

@app.route('/api/touros/contribuicao')
@api_login_required
def api_touros_contribuicao():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify([])

    rows = db.execute("""
        SELECT touro_pai as touro, COUNT(*) as n
        FROM matrizes WHERE rodada_id=? AND touro_pai IS NOT NULL AND touro_pai != ''
        GROUP BY touro_pai ORDER BY n DESC LIMIT 15
    """, (rodada['id'],)).fetchall()

    total = db.execute(
        "SELECT COUNT(*) as v FROM matrizes WHERE rodada_id=? AND touro_pai IS NOT NULL",
        (rodada['id'],)
    ).fetchone()['v']

    result = []
    acum = 0
    for r in rows:
        pct = round(r['n'] / total * 100, 2) if total else 0
        acum += pct
        result.append({'touro': r['touro'], 'n': r['n'], 'pct': pct, 'acum': round(acum, 2)})

    return jsonify(result)


@app.route('/api/touros/iciagen_safra')
@api_login_required
def api_touros_iciagen_safra():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify([])

    rows = db.execute("""
        SELECT p.touro, SUBSTR(p.data_nasc, 7, 4) as safra, AVG(p.iciagen) as avg_icia, COUNT(*) as n
        FROM produtos p
        WHERE p.rodada_id=? AND p.touro IS NOT NULL AND p.data_nasc IS NOT NULL
        GROUP BY p.touro, safra
        ORDER BY avg_icia DESC
    """, (rodada['id'],)).fetchall()

    return jsonify([dict(r) for r in rows])


# ── API: Posição vs CIA ────────────────────────────────────────────

@app.route('/api/posicao')
@api_login_required
def api_posicao():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({})

    rid = rodada['id']

    # Average percentiles
    percs = db.execute("""
        SELECT
            AVG(perc_icia) as perc_icia,
            AVG(perc_idesm) as perc_idesm,
            AVG(perc_rmat) as perc_rmat
        FROM avaliacoes WHERE rodada_id=?
    """, (rid,)).fetchone()

    # IPP position
    ipp = db.execute("""
        SELECT AVG(ipp) as v FROM matrizes WHERE rodada_id=? AND ipp IS NOT NULL
    """, (rid,)).fetchone()['v']

    # CEIP %
    ceip_pct = db.execute("""
        SELECT CAST(SUM(ceip) AS REAL) / COUNT(*) * 100 as v
        FROM matrizes WHERE rodada_id=?
    """, (rid,)).fetchone()['v'] or 0

    # Precoces %
    precoce_pct = db.execute("""
        SELECT CAST(SUM(precoce) AS REAL) / COUNT(*) * 100 as v
        FROM matrizes WHERE rodada_id=?
    """, (rid,)).fetchone()['v'] or 0

    # Genotipadas %
    geno_pct = db.execute("""
        SELECT CAST(SUM(genotipada) AS REAL) / COUNT(*) * 100 as v
        FROM matrizes WHERE rodada_id=?
    """, (rid,)).fetchone()['v'] or 0

    # ICIAGen by category
    cats = db.execute("""
        SELECT m.categoria, AVG(a.iciagen) as avg_icia
        FROM matrizes m JOIN avaliacoes a ON a.animal_id=m.animal_id AND a.rodada_id=m.rodada_id
        WHERE m.rodada_id=?
        GROUP BY m.categoria ORDER BY m.categoria
    """, (rid,)).fetchall()

    return jsonify({
        'perc_icia': round(percs['perc_icia'] or 0, 1),
        'perc_idesm': round(percs['perc_idesm'] or 0, 1),
        'perc_rmat': round(percs['perc_rmat'] or 0, 1),
        'ipp_avg': round(ipp or 0, 1),
        'ceip_pct': round(ceip_pct, 1),
        'precoce_pct': round(precoce_pct, 1),
        'geno_pct': round(geno_pct, 1),
        'categorias': [dict(c) for c in cats],
    })


# ── API: Estoque de Touros (por grupo/safra) ──────────────────────

def _get_val(r, *keys):
    """Get first non-empty value from multiple possible column names."""
    for k in keys:
        v = r.get(k)
        if v is not None and str(v).strip():
            return v
    return None


@app.route('/api/estoque/grupos')
@api_login_required
def api_estoque_grupos():
    db = get_db()
    grupos = db.execute("SELECT * FROM estoque_grupos ORDER BY id DESC").fetchall()
    result = []
    for g in grupos:
        stats = db.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN vendido=0 THEN 1 ELSE 0 END) as disp,
                   SUM(CASE WHEN vendido=1 THEN 1 ELSE 0 END) as vend,
                   AVG(CASE WHEN vendido=0 THEN iciagen END) as avg_icia,
                   AVG(CASE WHEN vendido=1 THEN valor_venda END) as avg_venda
            FROM estoque_touros WHERE grupo_id=?
        """, (g['id'],)).fetchone()
        result.append({
            **dict(g),
            'total': stats['total'],
            'disp': stats['disp'] or 0,
            'vend': stats['vend'] or 0,
            'avg_icia': round(stats['avg_icia'], 2) if stats['avg_icia'] else 0,
            'avg_venda': round(stats['avg_venda'], 2) if stats['avg_venda'] else 0,
        })
    return jsonify(result)


@app.route('/api/estoque/grupos', methods=['POST'])
@api_login_required
def api_estoque_criar_grupo():
    db = get_db()
    data = request.get_json() or {}
    nome = data.get('nome', '').strip()
    if not nome:
        return jsonify({'erro': 'Nome é obrigatório'}), 400
    try:
        cur = db.execute("INSERT INTO estoque_grupos (nome) VALUES (?)", (nome,))
        db.commit()
        return jsonify({'ok': True, 'id': cur.lastrowid})
    except sqlite3.IntegrityError:
        return jsonify({'erro': 'Já existe um grupo com esse nome'}), 400


@app.route('/api/estoque/grupos/<int:gid>', methods=['DELETE'])
@api_login_required
def api_estoque_del_grupo(gid):
    db = get_db()
    db.execute("DELETE FROM estoque_touros WHERE grupo_id=?", (gid,))
    db.execute("DELETE FROM estoque_grupos WHERE id=?", (gid,))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/estoque/grupos/<int:gid>')
@api_login_required
def api_estoque_grupo(gid):
    db = get_db()
    grupo = db.execute("SELECT * FROM estoque_grupos WHERE id=?", (gid,)).fetchone()
    if not grupo:
        return jsonify({'erro': 'Grupo não encontrado'}), 404

    todos = request.args.get('todos', '0')
    q = request.args.get('q', '').strip()

    conditions = ["grupo_id = ?"]
    params = [gid]

    if todos != '1':
        conditions.append("vendido = 0")
    if q:
        conditions.append("(brinco LIKE ? OR pai LIKE ? OR avo_paterno LIKE ?)")
        params.extend([f'%{q}%', f'%{q}%', f'%{q}%'])

    where = " AND ".join(conditions)

    rows = db.execute(f"""
        SELECT * FROM estoque_touros WHERE {where} ORDER BY iciagen DESC
    """, params).fetchall()

    # KPIs for this group
    kpis = db.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN vendido=0 THEN 1 ELSE 0 END) as disp,
               SUM(CASE WHEN vendido=1 THEN 1 ELSE 0 END) as vend,
               AVG(CASE WHEN vendido=0 THEN iciagen END) as avg_icia,
               AVG(CASE WHEN vendido=0 THEN idesm END) as avg_idesm,
               AVG(CASE WHEN vendido=0 THEN rmat END) as avg_rmat,
               AVG(CASE WHEN vendido=1 THEN valor_venda END) as avg_venda,
               SUM(CASE WHEN vendido=1 THEN valor_venda ELSE 0 END) as total_venda
        FROM estoque_touros WHERE grupo_id=?
    """, (gid,)).fetchone()

    return jsonify({
        'grupo': dict(grupo),
        'data': [dict(r) for r in rows],
        'kpis': {
            'total': kpis['total'],
            'disp': kpis['disp'] or 0,
            'vend': kpis['vend'] or 0,
            'avg_icia': round(kpis['avg_icia'], 2) if kpis['avg_icia'] else 0,
            'avg_idesm': round(kpis['avg_idesm'], 2) if kpis['avg_idesm'] else 0,
            'avg_rmat': round(kpis['avg_rmat'], 2) if kpis['avg_rmat'] else 0,
            'avg_venda': round(kpis['avg_venda'], 2) if kpis['avg_venda'] else 0,
            'total_venda': round(kpis['total_venda'] or 0, 2),
        }
    })


@app.route('/api/estoque/grupos/<int:gid>/importar', methods=['POST'])
@api_login_required
def api_estoque_importar_grupo(gid):
    db = get_db()
    grupo = db.execute("SELECT id FROM estoque_grupos WHERE id=?", (gid,)).fetchone()
    if not grupo:
        return jsonify({'erro': 'Grupo não encontrado'}), 404

    arquivo = request.files.get('arquivo')
    if not arquivo or not arquivo.filename:
        return jsonify({'erro': 'Envie um arquivo XLS/XLSX'}), 400

    filepath = os.path.join(UPLOAD_FOLDER, arquivo.filename)
    arquivo.save(filepath)
    rows, headers = read_xls_rows(filepath)
    n = 0

    app.logger.info(f"Estoque import headers: {headers}")

    for r in rows:
        brinco = str(_get_val(r, 'BRINCO', 'brinco', 'Brinco', 'produto', 'ID', 'id') or '').strip()
        if not brinco:
            continue

        idesm_val = _get_val(r, 'IDESM', 'idesm', 'Idesm', 'IDESM R')

        try:
            db.execute("""
                INSERT INTO estoque_touros (brinco, grupo_id, data_nasc, pai, avo_paterno,
                    avo_materno, idesm, iciagen, rmat, ifrig, peso, data_pesagem)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(brinco, grupo_id) DO UPDATE SET
                    data_nasc=excluded.data_nasc, pai=excluded.pai,
                    avo_paterno=excluded.avo_paterno, avo_materno=excluded.avo_materno,
                    idesm=excluded.idesm, iciagen=excluded.iciagen,
                    rmat=excluded.rmat, ifrig=excluded.ifrig,
                    peso=excluded.peso, data_pesagem=excluded.data_pesagem
            """, (
                brinco, gid,
                format_date(_get_val(r, 'data de Nascimento', 'dataN', 'Data de Nascimento', 'data_nasc', 'Nascimento')),
                str(_get_val(r, 'Pai', 'pai', 'PAI', 'touro_pai', 'touro') or '').strip() or None,
                str(_get_val(r, 'Avô Paterno', 'avo_paterno', 'Avo Paterno', 'AVO_PATERNO') or '').strip() or None,
                str(_get_val(r, 'Avô Materno', 'avo_materno', 'Avo Materno', 'AVO_MATERNO') or '').strip() or None,
                safe_float(idesm_val),
                safe_float(_get_val(r, 'ICiaGen', 'iciagen', 'ICIAGen', 'ICIAGEN')),
                safe_float(_get_val(r, 'RMat', 'rmat', 'RMAT')),
                safe_float(_get_val(r, 'IFRIG', 'ifrig', 'Ifrig')),
                safe_float(_get_val(r, 'Peso', 'peso', 'PESO', 'PV')),
                format_date(_get_val(r, 'data_pesagem', 'Data Pesagem', 'data pesagem')),
            ))
            n += 1
        except Exception as e:
            app.logger.warning(f"Erro ao importar touro {brinco}: {e}")
            continue

    db.commit()
    return jsonify({'ok': True, 'n_touros': n})


@app.route('/api/estoque/<int:touro_id>', methods=['PUT'])
@api_login_required
def api_estoque_editar(touro_id):
    db = get_db()
    data = request.get_json() or {}
    fields = []
    params = []
    allowed = ['brinco', 'data_nasc', 'pai', 'avo_paterno', 'avo_materno',
               'idesm', 'iciagen', 'rmat', 'ifrig', 'peso', 'data_pesagem', 'obs']
    for f in allowed:
        if f in data:
            fields.append(f"{f}=?")
            val = data[f]
            if f in ('idesm', 'iciagen', 'rmat', 'ifrig', 'peso'):
                val = safe_float(val)
            elif val == '':
                val = None
            params.append(val)
    if not fields:
        return jsonify({'erro': 'Nenhum campo para atualizar'}), 400
    params.append(touro_id)
    db.execute(f"UPDATE estoque_touros SET {', '.join(fields)} WHERE id=?", params)
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/estoque/<int:touro_id>/vender', methods=['POST'])
@api_login_required
def api_estoque_vender(touro_id):
    db = get_db()
    data = request.get_json() or {}
    valor = safe_float(data.get('valor'))
    db.execute(
        "UPDATE estoque_touros SET vendido=1, data_venda=DATE('now'), valor_venda=? WHERE id=?",
        (valor, touro_id)
    )
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/estoque/<int:touro_id>/devolver', methods=['POST'])
@api_login_required
def api_estoque_devolver(touro_id):
    db = get_db()
    db.execute(
        "UPDATE estoque_touros SET vendido=0, data_venda=NULL, valor_venda=NULL WHERE id=?",
        (touro_id,)
    )
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/estoque/<int:touro_id>', methods=['DELETE'])
@api_login_required
def api_estoque_delete(touro_id):
    db = get_db()
    db.execute("DELETE FROM estoque_touros WHERE id=?", (touro_id,))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/estoque/grupos/<int:gid>/renomear', methods=['POST'])
@api_login_required
def api_estoque_renomear(gid):
    db = get_db()
    data = request.get_json() or {}
    nome = data.get('nome', '').strip()
    if not nome:
        return jsonify({'erro': 'Nome obrigatorio'}), 400
    try:
        db.execute("UPDATE estoque_grupos SET nome=? WHERE id=?", (nome, gid))
        db.commit()
        return jsonify({'ok': True})
    except sqlite3.IntegrityError:
        return jsonify({'erro': 'Ja existe um grupo com esse nome'}), 400


# ── API: Rebanho (todos os animais, última rodada) ────────────────

@app.route('/api/rebanho')
@api_login_required
def api_rebanho():
    db = get_db()
    ultima = get_ultima_rodada(db)
    ultima_id = ultima['id'] if ultima else 0

    q = request.args.get('q', '').strip()
    ativo = request.args.get('ativo', '')
    tipo = request.args.get('tipo', '')  # 'matriz', 'produto', or '' for all
    categ = request.args.get('categ', '')
    sexo = request.args.get('sexo', '')
    order = request.args.get('order', 'iciagen')
    limit = min(int(request.args.get('limit', 20)), 200)
    offset = int(request.args.get('offset', 0))

    # Build a unified view using a CTE
    # Matrizes: use their rodada_id and avaliacao data
    # Produtos: use their rodada_id and inline genetic data
    # "ativo" = animal is in the latest rodada
    base_sql = f"""
        SELECT animal_id, tipo, categoria, pai, data_nasc, sexo,
               iciagen, deca_icia, idesm, rmat, ipp, pv,
               ceip, precoce, genotipada,
               rodada_id, rodada_nome,
               CASE WHEN rodada_id = {ultima_id} THEN 1 ELSE 0 END as ativo
        FROM (
            SELECT m.animal_id, 'Matriz' as tipo, m.categoria, m.touro_pai as pai,
                   m.data_nasc, NULL as sexo,
                   a.iciagen, a.deca_icia_g as deca_icia, a.idesm, a.rmat,
                   m.ipp, m.pv, m.ceip, m.precoce, m.genotipada,
                   m.rodada_id, r.nome as rodada_nome
            FROM matrizes m
            LEFT JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
            LEFT JOIN rodadas r ON r.id = m.rodada_id

            UNION ALL

            SELECT p.produto_id as animal_id, 'Produto' as tipo, NULL as categoria,
                   p.touro as pai, p.data_nasc, p.sexo,
                   p.iciagen, p.deca_iciagen as deca_icia, p.idesm, p.rmat,
                   NULL as ipp, NULL as pv, p.ceip, 0 as precoce, 0 as genotipada,
                   p.rodada_id, r2.nome as rodada_nome
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY produto_id ORDER BY rodada_id DESC) as rn
                FROM produtos
            ) p
            LEFT JOIN rodadas r2 ON r2.id = p.rodada_id
            WHERE p.rn = 1
        ) reb
    """

    conditions = []
    params = []

    if q:
        conditions.append("(animal_id LIKE ? OR pai LIKE ?)")
        params.extend([f'%{q}%', f'%{q}%'])
    if ativo == '1':
        conditions.append("ativo = 1")
    elif ativo == '0':
        conditions.append("ativo = 0")
    if tipo == 'matriz':
        conditions.append("tipo = 'Matriz'")
    elif tipo == 'produto':
        conditions.append("tipo = 'Produto'")
    if categ:
        conditions.append("categoria = ?")
        params.append(categ)
    if sexo:
        conditions.append("sexo = ?")
        params.append(sexo)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    allowed_orders = {
        'iciagen': 'iciagen DESC', 'idesm': 'idesm DESC',
        'rmat': 'rmat DESC', 'ipp': 'ipp ASC',
        'pv': 'pv DESC', 'animal_id': 'animal_id ASC',
        'categoria': 'categoria ASC', 'rodada': 'rodada_id DESC',
        'tipo': 'tipo ASC',
    }
    order_sql = allowed_orders.get(order, 'iciagen DESC')

    count_sql = f"SELECT COUNT(*) as n FROM ({base_sql}) sub {where}"
    total = db.execute(count_sql, params).fetchone()['n']

    data_sql = f"SELECT * FROM ({base_sql}) sub {where} ORDER BY {order_sql} LIMIT ? OFFSET ?"
    rows = db.execute(data_sql, params + [limit, offset]).fetchall()

    # KPIs
    kpi_sql = f"""SELECT COUNT(*) as total,
        SUM(CASE WHEN ativo=1 THEN 1 ELSE 0 END) as ativos,
        SUM(CASE WHEN ativo=0 THEN 1 ELSE 0 END) as inativos,
        SUM(CASE WHEN tipo='Matriz' THEN 1 ELSE 0 END) as n_matrizes,
        SUM(CASE WHEN tipo='Produto' THEN 1 ELSE 0 END) as n_produtos
    FROM ({base_sql}) sub"""
    kpis = db.execute(kpi_sql).fetchone()

    data = []
    for r in rows:
        d = dict(r)
        d['animal_id_enc'] = encode_id(r['animal_id'])
        data.append(d)

    return jsonify({
        'data': data,
        'total': total,
        'kpis': {
            'total': kpis['total'],
            'ativos': kpis['ativos'] or 0,
            'inativos': kpis['inativos'] or 0,
            'n_matrizes': kpis['n_matrizes'] or 0,
            'n_produtos': kpis['n_produtos'] or 0,
        }
    })


# ── API: Evolução do Rebanho ───────────────────────────────────────

@app.route('/api/evolucao')
@api_login_required
def api_evolucao():
    db = get_db()
    rodadas = db.execute("SELECT * FROM rodadas ORDER BY id ASC").fetchall()

    result = []
    for rod in rodadas:
        rid = rod['id']
        # Matrizes stats
        mat_stats = db.execute("""
            SELECT COUNT(*) as n,
                   AVG(a.iciagen) as avg_icia, AVG(a.idesm) as avg_idesm,
                   AVG(a.rmat) as avg_rmat, AVG(m.ipp) as avg_ipp,
                   SUM(m.ceip) as n_ceip, SUM(m.precoce) as n_precoce,
                   SUM(m.genotipada) as n_geno
            FROM matrizes m
            JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
            WHERE m.rodada_id = ?
        """, (rid,)).fetchone()

        # Produtos stats per safra year
        prod_safras = db.execute("""
            SELECT safra_ano, COUNT(*) as n,
                   AVG(iciagen) as avg_icia, AVG(idesm) as avg_idesm,
                   SUM(CASE WHEN sexo='M' THEN 1 ELSE 0 END) as machos,
                   SUM(CASE WHEN sexo='F' THEN 1 ELSE 0 END) as femeas
            FROM produtos WHERE rodada_id=? AND safra_ano IS NOT NULL
            GROUP BY safra_ano ORDER BY safra_ano
        """, (rid,)).fetchall()

        n_produtos = db.execute(
            "SELECT COUNT(*) as v FROM produtos WHERE rodada_id=?", (rid,)
        ).fetchone()['v']

        result.append({
            'rodada': dict(rod),
            'matrizes': {
                'n': mat_stats['n'] or 0,
                'avg_icia': round(mat_stats['avg_icia'], 2) if mat_stats['avg_icia'] else 0,
                'avg_idesm': round(mat_stats['avg_idesm'], 2) if mat_stats['avg_idesm'] else 0,
                'avg_rmat': round(mat_stats['avg_rmat'], 2) if mat_stats['avg_rmat'] else 0,
                'avg_ipp': round(mat_stats['avg_ipp'], 1) if mat_stats['avg_ipp'] else 0,
                'n_ceip': mat_stats['n_ceip'] or 0,
                'n_precoce': mat_stats['n_precoce'] or 0,
                'n_geno': mat_stats['n_geno'] or 0,
            },
            'produtos': {
                'n': n_produtos,
                'safras': [{
                    'ano': s['safra_ano'],
                    'n': s['n'],
                    'machos': s['machos'] or 0,
                    'femeas': s['femeas'] or 0,
                    'avg_icia': round(s['avg_icia'], 2) if s['avg_icia'] else 0,
                    'avg_idesm': round(s['avg_idesm'], 2) if s['avg_idesm'] else 0,
                } for s in prod_safras],
            },
        })

    return jsonify(result)


# ── API: Safras (produtos por ano/rodada) ─────────────────────────

@app.route('/api/safras')
@api_login_required
def api_safras():
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({'rodada': None, 'anos': []})

    rid = rodada['id']
    anos = db.execute("""
        SELECT safra_ano, COUNT(*) as n,
               SUM(CASE WHEN sexo='M' THEN 1 ELSE 0 END) as machos,
               SUM(CASE WHEN sexo='F' THEN 1 ELSE 0 END) as femeas,
               AVG(iciagen) as avg_icia, AVG(idesm) as avg_idesm
        FROM produtos WHERE rodada_id=? AND safra_ano IS NOT NULL
        GROUP BY safra_ano ORDER BY safra_ano
    """, (rid,)).fetchall()

    return jsonify({
        'rodada': dict(rodada),
        'anos': [{**dict(a),
                  'avg_icia': round(a['avg_icia'], 2) if a['avg_icia'] else 0,
                  'avg_idesm': round(a['avg_idesm'], 2) if a['avg_idesm'] else 0,
                  } for a in anos]
    })


@app.route('/api/safras/<ano>')
@api_login_required
def api_safra_detalhe(ano):
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({'data': [], 'total': 0})

    rid = rodada['id']
    q = request.args.get('q', '').strip()
    sexo = request.args.get('sexo', '')
    order = request.args.get('order', 'iciagen')
    limit = min(int(request.args.get('limit', 20)), 100)
    offset = int(request.args.get('offset', 0))

    conditions = ["rodada_id=?", "safra_ano=?"]
    params = [rid, ano]

    if q:
        conditions.append("(produto_id LIKE ? OR touro LIKE ? OR mae_id LIKE ?)")
        params.extend([f'%{q}%', f'%{q}%', f'%{q}%'])
    if sexo:
        conditions.append("sexo=?")
        params.append(sexo)

    touro_filter = request.args.get('touro', '').strip()
    if touro_filter:
        conditions.append("touro=?")
        params.append(touro_filter)

    ceip_filter = request.args.get('ceip')
    if ceip_filter == '1':
        conditions.append("ceip=1")

    where = " AND ".join(conditions)
    allowed_orders = {
        'iciagen': 'iciagen DESC', 'idesm': 'idesm DESC',
        'rmat': 'rmat DESC', 'produto_id': 'produto_id ASC',
        'data_nasc': 'data_nasc ASC',
    }
    order_sql = allowed_orders.get(order, 'iciagen DESC')

    total = db.execute(f"SELECT COUNT(*) as n FROM produtos WHERE {where}", params).fetchone()['n']
    rows = db.execute(f"""
        SELECT * FROM produtos WHERE {where} ORDER BY {order_sql} LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    return jsonify({'data': [dict(r) for r in rows], 'total': total, 'rodada': dict(rodada)})


# ── Alertas Inteligentes ───────────────────────────────────────────

@app.route('/alertas')
@login_required
def page_alertas():
    return render_template('alertas.html')


@app.route('/api/alertas/gerar', methods=['POST'])
@api_login_required
def api_alertas_gerar():
    """Coleta dados do rebanho e envia ao Claude para análise inteligente."""
    db = get_db()
    rodada = get_ultima_rodada(db)
    if not rodada:
        return jsonify({'erro': 'Nenhuma rodada importada'}), 404

    rid = rodada['id']
    body = request.get_json(silent=True) or {}

    # Limiares configuráveis (defaults)
    limiares = {
        'iep_max': body.get('iep_max', 16),
        'ipp_max': body.get('ipp_max', 36),
        'consanguinidade_max': body.get('consanguinidade_max', 3.0),
        'concentracao_reprodutor_max': body.get('concentracao_reprodutor_max', 15),
        'deca_descarte': body.get('deca_descarte', 8),
        'perc_paternidade_desconhecida': body.get('perc_paternidade_desconhecida', 10),
    }

    # ── Coletar dados do rebanho ──

    # KPIs gerais
    kpis = db.execute("""
        SELECT
            COUNT(*) as total,
            AVG(a.iciagen) as iciagen_avg,
            AVG(a.idesm) as idesm_avg,
            AVG(a.rmat) as rmat_avg,
            AVG(m.ipp) as ipp_avg,
            AVG(m.iep) as iep_avg,
            SUM(m.ceip) as ceip_total,
            SUM(m.genotipada) as genotipadas,
            SUM(m.precoce) as precoces
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.rodada_id = ? AND m.ativo = 1
    """, (rid,)).fetchone()

    # Distribuição por categoria
    categorias = db.execute("""
        SELECT categoria, COUNT(*) as n FROM matrizes
        WHERE rodada_id = ? AND ativo = 1 GROUP BY categoria
    """, (rid,)).fetchall()

    # Top touros por contribuição (concentração genética)
    touros_contrib = db.execute("""
        SELECT touro_pai, COUNT(*) as filhas,
               ROUND(COUNT(*)*100.0/(SELECT COUNT(*) FROM matrizes WHERE rodada_id=? AND ativo=1),1) as pct
        FROM matrizes WHERE rodada_id=? AND ativo=1 AND touro_pai IS NOT NULL
        GROUP BY touro_pai ORDER BY filhas DESC LIMIT 15
    """, (rid, rid)).fetchall()

    # Matrizes com IEP alto
    matrizes_iep_alto = db.execute("""
        SELECT animal_id, iep, ipp, categoria FROM matrizes
        WHERE rodada_id=? AND ativo=1 AND iep > ? ORDER BY iep DESC LIMIT 10
    """, (rid, limiares['iep_max'])).fetchall()

    # Matrizes com IPP alto
    matrizes_ipp_alto = db.execute("""
        SELECT animal_id, ipp, categoria FROM matrizes
        WHERE rodada_id=? AND ativo=1 AND ipp > ? ORDER BY ipp DESC LIMIT 10
    """, (rid, limiares['ipp_max'])).fetchall()

    # Matrizes com deca alta (candidatas descarte)
    matrizes_deca_alta = db.execute("""
        SELECT m.animal_id, a.deca_icia_g, a.iciagen, m.ceip, m.categoria, m.iep
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.rodada_id=? AND m.ativo=1 AND a.deca_icia_g >= ?
        ORDER BY a.deca_icia_g DESC, a.iciagen ASC LIMIT 20
    """, (rid, limiares['deca_descarte'])).fetchall()

    # Novilhas de alto valor (candidatas retenção)
    novilhas_top = db.execute("""
        SELECT m.animal_id, a.iciagen, a.deca_icia_g, a.idesm, m.genotipada
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id = m.animal_id AND a.rodada_id = m.rodada_id
        WHERE m.rodada_id=? AND m.ativo=1 AND m.categoria='N' AND a.deca_icia_g <= 3
        ORDER BY a.iciagen DESC LIMIT 10
    """, (rid,)).fetchall()

    # Paternidade desconhecida em produtos
    prod_stats = db.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN touro IS NULL OR touro='' THEN 1 ELSE 0 END) as sem_pai,
               SUM(pai_dna) as com_dna
        FROM produtos WHERE rodada_id=?
    """, (rid,)).fetchone()

    # ICIAGen por safra (tendência)
    tendencia = db.execute("""
        SELECT SUBSTR(m.data_nasc,7,4) as safra, AVG(a.iciagen) as avg_icia, COUNT(*) as n
        FROM matrizes m
        JOIN avaliacoes a ON a.animal_id=m.animal_id AND a.rodada_id=m.rodada_id
        WHERE m.rodada_id=? AND m.ativo=1 AND m.data_nasc IS NOT NULL
        GROUP BY safra ORDER BY safra
    """, (rid,)).fetchall()

    # Aprumos (desc_ap)
    problemas_aprumo = db.execute("""
        SELECT animal_id, desc_ap FROM matrizes
        WHERE rodada_id=? AND ativo=1 AND desc_ap IS NOT NULL AND desc_ap != ''
    """, (rid,)).fetchall()

    # ── Montar contexto para o Claude ──
    dados_rebanho = {
        'rodada': rodada['nome'],
        'total_matrizes_ativas': kpis['total'],
        'iciagen_medio': round(kpis['iciagen_avg'] or 0, 2),
        'idesm_medio': round(kpis['idesm_avg'] or 0, 2),
        'rmat_medio': round(kpis['rmat_avg'] or 0, 2),
        'ipp_medio': round(kpis['ipp_avg'] or 0, 1),
        'iep_medio': round(kpis['iep_avg'] or 0, 1) if kpis['iep_avg'] else None,
        'ceip_total': kpis['ceip_total'] or 0,
        'genotipadas': kpis['genotipadas'] or 0,
        'precoces': kpis['precoces'] or 0,
        'categorias': [dict(r) for r in categorias],
        'touros_contribuicao': [dict(r) for r in touros_contrib],
        'matrizes_iep_alto': [dict(r) for r in matrizes_iep_alto],
        'matrizes_ipp_alto': [dict(r) for r in matrizes_ipp_alto],
        'candidatas_descarte': [dict(r) for r in matrizes_deca_alta],
        'novilhas_top': [dict(r) for r in novilhas_top],
        'produtos_total': prod_stats['total'] if prod_stats else 0,
        'produtos_sem_pai': prod_stats['sem_pai'] if prod_stats else 0,
        'produtos_com_dna': prod_stats['com_dna'] if prod_stats else 0,
        'tendencia_iciagen': [dict(r) for r in tendencia],
        'problemas_aprumo': [dict(r) for r in problemas_aprumo],
    }

    prompt = f"""Você é um consultor especialista em melhoramento genético de bovinos Nelore.
Analise os dados do rebanho da Fazenda Porto do Engenho e gere alertas priorizados.

DADOS DO REBANHO (Rodada: {rodada['nome']}):
{json.dumps(dados_rebanho, ensure_ascii=False, indent=2)}

LIMIARES CONFIGURADOS:
- IEP máximo aceitável: {limiares['iep_max']} meses
- IPP máximo aceitável: {limiares['ipp_max']} meses
- Consanguinidade máxima: {limiares['consanguinidade_max']}%
- Concentração máxima por reprodutor: {limiares['concentracao_reprodutor_max']}%
- Deca para considerar descarte: >= {limiares['deca_descarte']}
- % paternidade desconhecida preocupante: > {limiares['perc_paternidade_desconhecida']}%

FORMATO DE RESPOSTA (JSON estrito):
{{
  "alertas": [
    {{
      "nivel": "critico|atencao|monitorar",
      "categoria": "reproducao|genetica|descarte|aprumos|dados|diversidade",
      "titulo": "Título curto do alerta",
      "descricao": "Descrição detalhada com números e animais específicos",
      "animais": ["ID1", "ID2"],
      "acao_sugerida": "Ação recomendada"
    }}
  ],
  "resumo_executivo": "Parágrafo consolidado com principal vetor de risco, comparação e ação prioritária para próxima estação reprodutiva.",
  "indicadores_resumo": {{
    "total_criticos": 0,
    "total_atencao": 0,
    "total_monitorar": 0
  }}
}}

REGRAS:
1. Analise TODAS as dimensões: Reprodução, Diversidade Genética, Mérito e Descarte, Aprumos, Qualidade de Dados.
2. Seja específico: cite IDs de animais, números, percentuais.
3. Priorize: crítico > atenção > monitorar.
4. No resumo executivo, identifique o PRINCIPAL vetor de risco e sugira ação prioritária.
5. Retorne APENAS o JSON, sem texto adicional."""

    # ── Chamar API Claude ──
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'erro': 'API key não configurada. Adicione ANTHROPIC_API_KEY ao .env'}), 500

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        resposta_text = message.content[0].text
        # Tentar parsear JSON (pode vir com ```json ... ```)
        resposta_text = resposta_text.strip()
        if resposta_text.startswith('```'):
            resposta_text = resposta_text.split('\n', 1)[1]
            resposta_text = resposta_text.rsplit('```', 1)[0]

        resultado = json.loads(resposta_text)
        resultado['dados_enviados'] = dados_rebanho
        resultado['limiares'] = limiares
        return jsonify(resultado)

    except anthropic.APIError as e:
        return jsonify({'erro': f'Erro na API Claude: {str(e)}'}), 500
    except json.JSONDecodeError:
        return jsonify({'erro': 'Resposta inválida do Claude', 'resposta_raw': resposta_text}), 500
    except Exception as e:
        return jsonify({'erro': f'Erro inesperado: {str(e)}'}), 500


# ── Requisições de Compra ──────────────────────────────────────────

def _req_proximo_numero(db):
    ano = datetime.now().year
    row = db.execute(
        "SELECT COUNT(*) AS n FROM requisicoes_compra WHERE numero LIKE ?",
        (f'REQ-{ano}-%',)
    ).fetchone()
    return f"REQ-{ano}-{(row['n'] + 1):04d}"


def _req_log(db, req_id, acao, detalhes=None):
    user = current_user()
    db.execute(
        """INSERT INTO requisicoes_historico
           (requisicao_id, usuario_id, usuario_nome, acao, detalhes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (req_id,
         user['id'] if user else None,
         user['nome'] if user else None,
         acao, detalhes, now_local_str())
    )


def _req_fetch(db, req_id):
    req = db.execute(
        "SELECT * FROM requisicoes_compra WHERE id=?", (req_id,)
    ).fetchone()
    if not req:
        return None, None, None
    itens = db.execute(
        "SELECT * FROM requisicoes_itens WHERE requisicao_id=? ORDER BY ordem, id",
        (req_id,)
    ).fetchall()
    hist = db.execute(
        "SELECT * FROM requisicoes_historico WHERE requisicao_id=? ORDER BY created_at DESC, id DESC",
        (req_id,)
    ).fetchall()
    return req, itens, hist


@app.route('/requisicoes')
@login_required
def requisicoes_page():
    return render_template('requisicoes.html')


@app.route('/requisicoes/nova')
@login_required
def requisicao_nova_page():
    return render_template('requisicao_nova.html')


@app.route('/requisicoes/<int:req_id>')
@login_required
def requisicao_detalhe_page(req_id):
    return render_template('requisicao_detalhe.html', req_id=req_id)


@app.route('/api/requisicoes', methods=['GET'])
@api_login_required
def api_requisicoes_list():
    db = get_db()
    user = current_user()
    status = request.args.get('status', '').strip()
    escopo = request.args.get('escopo', '').strip()  # 'meus' | 'todos'

    where = []
    params = []
    if status in ('pendente', 'aprovada', 'rejeitada', 'cancelada'):
        where.append("r.status = ?")
        params.append(status)

    # Usuário comum só vê suas próprias requisições; master vê tudo (ou 'meus' opcional)
    if not is_master(user) or escopo == 'meus':
        where.append("r.solicitante_id = ?")
        params.append(user['id'])

    sql = """
        SELECT r.*,
               (SELECT COUNT(*) FROM requisicoes_itens WHERE requisicao_id=r.id) AS n_itens
        FROM requisicoes_compra r
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE r.status WHEN 'pendente' THEN 0 ELSE 1 END, r.id DESC"

    rows = db.execute(sql, params).fetchall()
    return jsonify({
        'is_master': is_master(user),
        'requisicoes': [dict(r) for r in rows],
    })


@app.route('/api/requisicoes', methods=['POST'])
@api_login_required
def api_requisicoes_criar():
    db = get_db()
    user = current_user()
    data = request.get_json(silent=True) or {}

    responsavel = (data.get('responsavel') or 'Fazenda Porto do Engenho').strip()
    funcionario_retirada = (data.get('funcionario_retirada') or '').strip()
    fornecedor = (data.get('fornecedor') or '').strip()
    observacoes = (data.get('observacoes') or '').strip()
    itens_raw = data.get('itens') or []

    itens = []
    for i, it in enumerate(itens_raw):
        desc = (it.get('descricao') or '').strip() if isinstance(it, dict) else ''
        qtd = (it.get('quantidade') or '').strip() if isinstance(it, dict) else ''
        if desc:
            itens.append((i + 1, desc, qtd))

    if not funcionario_retirada:
        return jsonify({'erro': 'Informe o funcionário que fará a retirada.'}), 400
    if not fornecedor:
        return jsonify({'erro': 'Informe o fornecedor.'}), 400
    if not itens:
        return jsonify({'erro': 'Adicione ao menos um item.'}), 400

    numero = _req_proximo_numero(db)
    ts = now_local_str()
    cur = db.execute(
        """INSERT INTO requisicoes_compra
           (numero, data_solicitacao, solicitante_id, solicitante_nome, responsavel,
            funcionario_retirada, fornecedor, observacoes, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pendente', ?, ?)""",
        (numero, ts, user['id'], user['nome'], responsavel,
         funcionario_retirada, fornecedor, observacoes, ts, ts)
    )
    req_id = cur.lastrowid
    for ordem, desc, qtd in itens:
        cat_id = _catalog_find_or_create(db, desc, qtd)
        if cat_id:
            _catalog_touch(db, cat_id, ts)
        db.execute(
            "INSERT INTO requisicoes_itens (requisicao_id, ordem, descricao, quantidade, item_catalogo_id) VALUES (?, ?, ?, ?, ?)",
            (req_id, ordem, desc, qtd, cat_id)
        )
    _req_log(db, req_id, 'criada', f'{len(itens)} item(ns)')
    db.commit()
    return jsonify({'id': req_id, 'numero': numero}), 201


@app.route('/api/requisicoes/<int:req_id>', methods=['GET'])
@api_login_required
def api_requisicao_get(req_id):
    db = get_db()
    user = current_user()
    req, itens, hist = _req_fetch(db, req_id)
    if not req:
        return jsonify({'erro': 'Requisição não encontrada.'}), 404
    if not is_master(user) and req['solicitante_id'] != user['id']:
        return jsonify({'erro': 'Sem permissão.'}), 403
    return jsonify({
        'is_master': is_master(user),
        'requisicao': dict(req),
        'itens': [dict(r) for r in itens],
        'historico': [dict(r) for r in hist],
    })


@app.route('/api/requisicoes/<int:req_id>', methods=['PUT'])
@api_login_required
def api_requisicao_editar(req_id):
    """Edita requisição — permitido apenas quando pendente."""
    db = get_db()
    user = current_user()
    req = db.execute(
        "SELECT solicitante_id, status FROM requisicoes_compra WHERE id=?", (req_id,)
    ).fetchone()
    if not req:
        return jsonify({'erro': 'Requisição não encontrada.'}), 404
    if req['solicitante_id'] != user['id'] and not is_master(user):
        return jsonify({'erro': 'Sem permissão.'}), 403
    if req['status'] != 'pendente':
        return jsonify({'erro': 'Só é possível editar requisições pendentes.'}), 400

    data = request.get_json(silent=True) or {}
    responsavel = (data.get('responsavel') or 'Fazenda Porto do Engenho').strip()
    funcionario_retirada = (data.get('funcionario_retirada') or '').strip()
    fornecedor = (data.get('fornecedor') or '').strip()
    observacoes = (data.get('observacoes') or '').strip()
    itens_raw = data.get('itens') or []

    itens = []
    for i, it in enumerate(itens_raw):
        desc = (it.get('descricao') or '').strip() if isinstance(it, dict) else ''
        qtd = (it.get('quantidade') or '').strip() if isinstance(it, dict) else ''
        if desc:
            itens.append((i + 1, desc, qtd))

    if not funcionario_retirada:
        return jsonify({'erro': 'Informe o funcionário que fará a retirada.'}), 400
    if not fornecedor:
        return jsonify({'erro': 'Informe o fornecedor.'}), 400
    if not itens:
        return jsonify({'erro': 'Adicione ao menos um item.'}), 400

    ts = now_local_str()
    db.execute("""
        UPDATE requisicoes_compra
        SET responsavel=?, funcionario_retirada=?, fornecedor=?, observacoes=?, updated_at=?
        WHERE id=?
    """, (responsavel, funcionario_retirada, fornecedor, observacoes, ts, req_id))

    # Substitui itens (edição completa)
    db.execute("DELETE FROM requisicoes_itens WHERE requisicao_id=?", (req_id,))
    for ordem, desc, qtd in itens:
        cat_id = _catalog_find_or_create(db, desc, qtd)
        db.execute(
            "INSERT INTO requisicoes_itens (requisicao_id, ordem, descricao, quantidade, item_catalogo_id) VALUES (?, ?, ?, ?, ?)",
            (req_id, ordem, desc, qtd, cat_id)
        )
    # Recalcula contadores do catálogo (edição não deve inflar n_pedidos)
    _catalog_recalc(db)
    _req_log(db, req_id, 'editada', f'{len(itens)} item(ns)')
    db.commit()
    return jsonify({'ok': True})


@app.route('/requisicoes/<int:req_id>/editar')
@login_required
def requisicao_editar_page(req_id):
    return render_template('requisicao_editar.html', req_id=req_id)


def _aprovar_uma(db, req_id, assinatura):
    req = db.execute(
        "SELECT id, status, numero FROM requisicoes_compra WHERE id=?", (req_id,)
    ).fetchone()
    if not req:
        return False, 'não encontrada'
    if req['status'] != 'pendente':
        return False, f"não está pendente (status: {req['status']})"
    user = current_user()
    ts = now_local_str()
    db.execute(
        """UPDATE requisicoes_compra
           SET status='aprovada', aprovador_id=?, aprovador_nome=?, assinatura=?,
               data_decisao=?, updated_at=?
           WHERE id=?""",
        (user['id'], user['nome'], assinatura, ts, ts, req_id)
    )
    _req_log(db, req_id, 'aprovada', f'Assinado por {user["nome"]}')
    return True, req['numero']


@app.route('/api/requisicoes/<int:req_id>/aprovar', methods=['POST'])
@api_master_required
def api_requisicao_aprovar(req_id):
    db = get_db()
    # Assinatura sempre é o nome do master logado — não pode ser sobrescrito pelo cliente
    assinatura = current_user()['nome']
    ok, info = _aprovar_uma(db, req_id, assinatura)
    if not ok:
        return jsonify({'erro': f'Requisição {info}.'}), 400
    db.commit()
    return jsonify({'ok': True, 'numero': info})


@app.route('/api/requisicoes/aprovar-lote', methods=['POST'])
@api_master_required
def api_requisicao_aprovar_lote():
    db = get_db()
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not ids or not isinstance(ids, list):
        return jsonify({'erro': 'Selecione ao menos uma requisição.'}), 400
    # Assinatura sempre é o nome do master logado
    assinatura = current_user()['nome']

    aprovadas, falhas = [], []
    for rid in ids:
        try:
            rid_int = int(rid)
        except (TypeError, ValueError):
            falhas.append({'id': rid, 'motivo': 'id inválido'})
            continue
        ok, info = _aprovar_uma(db, rid_int, assinatura)
        if ok:
            aprovadas.append({'id': rid_int, 'numero': info})
        else:
            falhas.append({'id': rid_int, 'motivo': info})
    db.commit()
    return jsonify({'aprovadas': aprovadas, 'falhas': falhas})


@app.route('/api/requisicoes/<int:req_id>/rejeitar', methods=['POST'])
@api_master_required
def api_requisicao_rejeitar(req_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    motivo = (data.get('motivo') or '').strip()
    if not motivo:
        return jsonify({'erro': 'Informe o motivo da rejeição.'}), 400
    req = db.execute(
        "SELECT status FROM requisicoes_compra WHERE id=?", (req_id,)
    ).fetchone()
    if not req:
        return jsonify({'erro': 'Requisição não encontrada.'}), 404
    if req['status'] != 'pendente':
        return jsonify({'erro': f'Requisição não está pendente (status: {req["status"]}).'}), 400
    user = current_user()
    ts = now_local_str()
    db.execute(
        """UPDATE requisicoes_compra
           SET status='rejeitada', aprovador_id=?, aprovador_nome=?,
               motivo_rejeicao=?, data_decisao=?, updated_at=?
           WHERE id=?""",
        (user['id'], user['nome'], motivo, ts, ts, req_id)
    )
    _req_log(db, req_id, 'rejeitada', motivo)
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/requisicoes/<int:req_id>/cancelar', methods=['POST'])
@api_login_required
def api_requisicao_cancelar(req_id):
    db = get_db()
    user = current_user()
    data = request.get_json(silent=True) or {}
    motivo = (data.get('motivo') or '').strip()

    req = db.execute(
        "SELECT solicitante_id, status FROM requisicoes_compra WHERE id=?", (req_id,)
    ).fetchone()
    if not req:
        return jsonify({'erro': 'Requisição não encontrada.'}), 404
    if req['solicitante_id'] != user['id'] and not is_master(user):
        return jsonify({'erro': 'Sem permissão.'}), 403
    if req['status'] in ('cancelada', 'rejeitada'):
        return jsonify({'erro': f'Requisição já está {req["status"]}.'}), 400
    if req['status'] == 'aprovada' and not motivo:
        return jsonify({'erro': 'Para cancelar uma requisição já autorizada, informe o motivo.'}), 400

    db.execute(
        """UPDATE requisicoes_compra
           SET status='cancelada', updated_at=?
           WHERE id=?""",
        (now_local_str(), req_id)
    )
    detalhes = motivo or None
    if req['status'] == 'aprovada' and motivo:
        detalhes = f'Cancelada após autorização. Motivo: {motivo}'
    _req_log(db, req_id, 'cancelada', detalhes)
    db.commit()
    return jsonify({'ok': True})


@app.route('/requisicoes/<int:req_id>/pdf')
@login_required
def requisicao_pdf(req_id):
    db = get_db()
    user = current_user()
    req, itens, _ = _req_fetch(db, req_id)
    if not req:
        return "Requisição não encontrada.", 404
    if not is_master(user) and req['solicitante_id'] != user['id']:
        return "Sem permissão.", 403
    if req['status'] != 'aprovada':
        return "A autorização em PDF só é emitida após aprovação.", 400

    pdf_bytes = _gerar_pdf_requisicao(req, itens)
    _req_log(db, req_id, 'pdf_gerado')
    db.commit()
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': f'inline; filename=autorizacao_{req["numero"]}.pdf'
        }
    )


def _fmt_dt(s):
    if not s:
        return ''
    try:
        if isinstance(s, str):
            dt = datetime.fromisoformat(s.replace('Z', '').split('.')[0])
        else:
            dt = s
        return dt.strftime('%d/%m/%Y %H:%M')
    except Exception:
        return str(s)


def _gerar_pdf_requisicao(req, itens):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    from reportlab.platypus import Table, TableStyle

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    margin = 2 * cm
    red = colors.HexColor('#D01B20')
    black = colors.HexColor('#1A1A1A')
    gray = colors.HexColor('#666666')

    # Cabeçalho com logo
    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.jpg')
    try:
        if os.path.exists(logo_path):
            c.drawImage(logo_path, margin, h - margin - 2.2*cm, width=3.2*cm, height=2.2*cm,
                        preserveAspectRatio=True, mask='auto')
    except Exception:
        pass

    c.setFillColor(black)
    c.setFont('Helvetica-Bold', 14)
    c.drawString(margin + 3.8*cm, h - margin - 0.8*cm, 'FAZENDA PORTO DO ENGENHO')
    c.setFont('Helvetica-Bold', 12)
    c.drawString(margin + 3.8*cm, h - margin - 1.4*cm, 'AUTORIZAÇÃO DE COMPRA')
    c.setFont('Helvetica', 9)
    c.setFillColor(gray)
    c.drawString(margin + 3.8*cm, h - margin - 2.0*cm, f'Nº {req["numero"]}')

    # Linha vermelha
    c.setStrokeColor(red)
    c.setLineWidth(2)
    y = h - margin - 2.6*cm
    c.line(margin, y, w - margin, y)

    # Metadados
    y -= 0.8*cm
    c.setFillColor(black)
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin, y, 'Data da solicitação:')
    c.setFont('Helvetica', 10)
    c.drawString(margin + 4.0*cm, y, _fmt_dt(req['data_solicitacao']))

    y -= 0.55*cm
    c.setFont('Helvetica-Bold', 10); c.drawString(margin, y, 'Responsável pela solicitação:')
    c.setFont('Helvetica', 10);       c.drawString(margin + 5.6*cm, y, req['responsavel'] or '')

    y -= 0.55*cm
    c.setFont('Helvetica-Bold', 10); c.drawString(margin, y, 'Funcionário que fará a retirada:')
    c.setFont('Helvetica', 10);       c.drawString(margin + 5.9*cm, y, req['funcionario_retirada'] or '')

    y -= 0.55*cm
    c.setFont('Helvetica-Bold', 10); c.drawString(margin, y, 'Fornecedor:')
    c.setFont('Helvetica', 10);       c.drawString(margin + 2.4*cm, y, req['fornecedor'] or '')

    y -= 0.55*cm
    c.setFont('Helvetica-Bold', 10); c.drawString(margin, y, 'Solicitante (login):')
    c.setFont('Helvetica', 10);       c.drawString(margin + 3.6*cm, y, req['solicitante_nome'] or '')

    # Itens
    y -= 0.9*cm
    c.setFont('Helvetica-Bold', 11); c.drawString(margin, y, 'Materiais autorizados:')
    y -= 0.4*cm

    data = [['#', 'Quantidade', 'Descrição']]
    for it in itens:
        data.append([str(it['ordem']), it['quantidade'] or '', it['descricao'] or ''])
    tbl = Table(data, colWidths=[1.0*cm, 3.2*cm, w - 2*margin - 4.2*cm])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), red),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 9),
        ('VALIGN',     (0, 0), (-1, -1), 'TOP'),
        ('GRID',       (0, 0), (-1, -1), 0.3, colors.HexColor('#CCCCCC')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F7F7F7')]),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING',(0, 0), (-1, -1), 6),
        ('TOPPADDING',  (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 5),
    ]))
    tw, th = tbl.wrap(w - 2*margin, y)
    tbl.drawOn(c, margin, y - th)
    y = y - th - 0.5*cm

    # Observações
    if req['observacoes']:
        c.setFont('Helvetica-Bold', 10); c.drawString(margin, y, 'Observações:')
        y -= 0.4*cm
        c.setFont('Helvetica', 9)
        for linha in req['observacoes'].splitlines() or [req['observacoes']]:
            c.drawString(margin, y, linha[:110])
            y -= 0.4*cm

    # Bloco de autorização
    y -= 0.4*cm
    c.setStrokeColor(red); c.setLineWidth(1.2)
    box_h = 3.8*cm
    c.rect(margin, y - box_h, w - 2*margin, box_h, stroke=1, fill=0)
    c.setFillColor(red)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(margin + 0.4*cm, y - 0.6*cm, 'AUTORIZAÇÃO')
    c.setFillColor(black)
    c.setFont('Helvetica', 10)
    c.drawString(margin + 0.4*cm, y - 1.2*cm,
                 'Autorizo a retirada dos materiais acima listados junto ao fornecedor indicado.')
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin + 0.4*cm, y - 2.0*cm, 'Autorizado por:')
    c.setFont('Helvetica', 10)
    c.drawString(margin + 3.5*cm, y - 2.0*cm, req['aprovador_nome'] or '')
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin + 0.4*cm, y - 2.6*cm, 'Data/Hora:')
    c.setFont('Helvetica', 10)
    c.drawString(margin + 2.4*cm, y - 2.6*cm, _fmt_dt(req['data_decisao']))
    c.setFont('Helvetica-Bold', 10)
    c.drawString(margin + 0.4*cm, y - 3.2*cm, 'Assinatura digital:')
    c.setFont('Helvetica-Oblique', 11)
    c.setFillColor(red)
    c.drawString(margin + 3.7*cm, y - 3.2*cm, req['assinatura'] or '')
    c.setFillColor(black)

    # Rodapé
    c.setFont('Helvetica', 7); c.setFillColor(gray)
    c.drawString(margin, margin - 0.6*cm,
                 f'Documento gerado eletronicamente · Nº {req["numero"]} · '
                 f'{now_local().strftime("%d/%m/%Y %H:%M")}')
    c.drawRightString(w - margin, margin - 0.6*cm,
                      '(27) 3225-8853 · fazendaportodoengenho@gmail.com')

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# ── PDF: lista de pendentes para assinatura física ─────────────────

@app.route('/requisicoes/pendentes.pdf')
@login_required
def requisicoes_pendentes_pdf():
    """Gera um PDF com todas as requisições pendentes — para assinar em papel."""
    db = get_db()
    user = current_user()

    where = ["r.status = 'pendente'"]
    params = []
    if not is_master(user):
        where.append("r.solicitante_id = ?")
        params.append(user['id'])
    where_sql = " AND ".join(where)

    reqs = db.execute(f"""
        SELECT r.id, r.numero, r.data_solicitacao, r.solicitante_nome,
               r.responsavel, r.funcionario_retirada, r.fornecedor, r.observacoes
        FROM requisicoes_compra r
        WHERE {where_sql}
        ORDER BY r.id ASC
    """, params).fetchall()

    itens_por_req = {}
    for req in reqs:
        rows = db.execute(
            "SELECT ordem, descricao, quantidade FROM requisicoes_itens WHERE requisicao_id=? ORDER BY ordem",
            (req['id'],)
        ).fetchall()
        itens_por_req[req['id']] = rows

    pdf_bytes = _gerar_pdf_pendentes(reqs, itens_por_req, master=is_master(user))
    stamp = now_local().strftime('%Y%m%d_%H%M')
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': f'inline; filename=requisicoes_pendentes_{stamp}.pdf'
        }
    )


def _gerar_pdf_pendentes(reqs, itens_por_req, master=False):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        Image, PageBreak, KeepTogether
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    buf = io.BytesIO()
    margin = 1.7 * cm
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=1.5*cm,
        title='Requisições Pendentes'
    )
    W = A4[0] - 2 * margin

    red   = colors.HexColor('#D01B20')
    black = colors.HexColor('#1A1A1A')
    gray  = colors.HexColor('#666666')
    lgray = colors.HexColor('#CCCCCC')

    s = getSampleStyleSheet()
    st_title     = ParagraphStyle('t',  parent=s['Title'],   fontName='Helvetica-Bold', fontSize=14, textColor=black, leading=16, spaceAfter=2)
    st_subtitle  = ParagraphStyle('st', parent=s['Normal'],  fontName='Helvetica-Bold', fontSize=11, textColor=red,   leading=13, spaceAfter=0)
    st_meta      = ParagraphStyle('m',  parent=s['Normal'],  fontSize=8,  textColor=gray, leading=10)
    st_req_num   = ParagraphStyle('rn', parent=s['Normal'],  fontName='Helvetica-Bold', fontSize=12, textColor=black, leading=14)
    st_req_sub   = ParagraphStyle('rs', parent=s['Normal'],  fontSize=8.5, textColor=gray, leading=10, spaceAfter=4)
    st_field     = ParagraphStyle('f',  parent=s['Normal'],  fontSize=9, leading=11, textColor=black)
    st_obs       = ParagraphStyle('o',  parent=s['Normal'],  fontSize=8.5, leading=11, textColor=gray, leftIndent=0)
    st_footer    = ParagraphStyle('fo', parent=s['Normal'],  fontSize=7,   textColor=gray, alignment=TA_CENTER)

    story = []

    # ── Cabeçalho ──
    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'logo.jpg')
    header_items = []
    if os.path.exists(logo_path):
        header_items.append([Image(logo_path, width=2.6*cm, height=1.8*cm),
                             [Paragraph('FAZENDA PORTO DO ENGENHO', st_title),
                              Paragraph('Requisições pendentes de autorização', st_subtitle),
                              Paragraph(f'Emitido em {now_local().strftime("%d/%m/%Y %H:%M")} · '
                                        f'{len(reqs)} requisição(ões) aguardando', st_meta)]])
        t = Table(header_items, colWidths=[3.2*cm, W - 3.2*cm])
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(t)
    else:
        story.append(Paragraph('FAZENDA PORTO DO ENGENHO', st_title))
        story.append(Paragraph('Requisições pendentes de autorização', st_subtitle))
        story.append(Paragraph(f'Emitido em {now_local().strftime("%d/%m/%Y %H:%M")}', st_meta))

    story.append(Spacer(1, 0.2*cm))
    linha = Table([['']], colWidths=[W])
    linha.setStyle(TableStyle([('LINEBELOW', (0,0), (-1,-1), 1.5, red)]))
    story.append(linha)
    story.append(Spacer(1, 0.4*cm))

    # ── Sem pendentes ──
    if not reqs:
        story.append(Spacer(1, 2*cm))
        story.append(Paragraph(
            '<para align="center"><font size="11" color="#666">Não há requisições pendentes de autorização no momento.</font></para>',
            s['Normal']
        ))
        doc.build(story)
        buf.seek(0)
        return buf.read()

    # ── Cada requisição em bloco (KeepTogether pra não quebrar meio) ──
    for idx, req in enumerate(reqs):
        block = []
        header_row = Table(
            [[Paragraph(f"<b>{req['numero']}</b>", st_req_num),
              Paragraph(f"Solicitada em {_fmt_dt(req['data_solicitacao'])} por <b>{req['solicitante_nome'] or ''}</b>", st_req_sub)]],
            colWidths=[4.0*cm, W - 4.0*cm]
        )
        header_row.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F4F4F4')),
            ('BOX', (0, 0), (-1, -1), 0.5, lgray),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        block.append(header_row)

        fields = [
            [Paragraph('<b>Responsável:</b>', st_field),
             Paragraph(req['responsavel'] or '-', st_field),
             Paragraph('<b>Fornecedor:</b>', st_field),
             Paragraph(req['fornecedor'] or '-', st_field)],
            [Paragraph('<b>Retirada:</b>', st_field),
             Paragraph(req['funcionario_retirada'] or '-', st_field),
             Paragraph('', st_field),
             Paragraph('', st_field)],
        ]
        fields_t = Table(fields, colWidths=[2.6*cm, (W-2*2.6*cm)/2, 2.6*cm, (W-2*2.6*cm)/2])
        fields_t.setStyle(TableStyle([
            ('LINEBELOW', (0, 0), (-1, -1), 0.25, lgray),
            ('BOX', (0, 0), (-1, -1), 0.5, lgray),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        block.append(fields_t)

        # Tabela de itens
        itens = itens_por_req.get(req['id'], [])
        itens_data = [['#', 'Qtd', 'Descrição do item']]
        for it in itens:
            itens_data.append([str(it['ordem']), it['quantidade'] or '', it['descricao'] or ''])
        if not itens:
            itens_data.append(['-', '-', '(sem itens)'])
        itens_t = Table(itens_data, colWidths=[0.8*cm, 2.2*cm, W - 0.8*cm - 2.2*cm])
        itens_t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), red),
            ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
            ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE',   (0, 0), (-1, -1), 8.5),
            ('VALIGN',     (0, 0), (-1, -1), 'TOP'),
            ('GRID',       (0, 0), (-1, -1), 0.25, lgray),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#FAFAFA')]),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING',(0, 0), (-1, -1), 5),
            ('TOPPADDING',  (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING',(0, 0), (-1, -1), 3),
        ]))
        block.append(itens_t)

        if req['observacoes']:
            block.append(Spacer(1, 0.12*cm))
            block.append(Paragraph(f"<b>Obs.:</b> {req['observacoes']}", st_obs))

        # Área de decisão/assinatura
        block.append(Spacer(1, 0.15*cm))
        decisao = Table(
            [[Paragraph('<font size="10"><b>(&nbsp;&nbsp;&nbsp;)</b> APROVAR</font>', st_field),
              Paragraph('<font size="10"><b>(&nbsp;&nbsp;&nbsp;)</b> REJEITAR</font>', st_field),
              Paragraph('<font size="9">Assinatura: ____________________________________</font>', st_field)]],
            colWidths=[2.6*cm, 2.6*cm, W - 5.2*cm]
        )
        decisao.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFFCF2')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#E6A817')),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        block.append(decisao)
        block.append(Spacer(1, 0.5*cm))

        story.append(KeepTogether(block))

    # ── Rodapé simples ──
    story.append(Spacer(1, 0.4*cm))
    story.append(Paragraph(
        f'Documento gerado eletronicamente · Fazenda Porto do Engenho · '
        f'{now_local().strftime("%d/%m/%Y %H:%M")}',
        st_footer
    ))

    doc.build(story)
    buf.seek(0)
    return buf.read()


# ── Catálogo de itens ──────────────────────────────────────────────

@app.route('/api/itens-catalogo')
@api_login_required
def api_itens_catalogo():
    db = get_db()
    q = (request.args.get('q') or '').strip()
    if q:
        rows = db.execute("""
            SELECT id, nome, unidade, categoria, n_pedidos, ultimo_uso
            FROM itens_catalogo
            WHERE ativo = 1 AND nome LIKE ? COLLATE NOCASE
            ORDER BY n_pedidos DESC, nome
            LIMIT 50
        """, (f'%{q}%',)).fetchall()
    else:
        rows = db.execute("""
            SELECT id, nome, unidade, categoria, n_pedidos, ultimo_uso
            FROM itens_catalogo
            WHERE ativo = 1
            ORDER BY n_pedidos DESC, nome
            LIMIT 500
        """).fetchall()
    return jsonify({'itens': [dict(r) for r in rows]})


# ── Relatório de autorizações ──────────────────────────────────────

@app.route('/requisicoes/relatorio')
@login_required
def requisicoes_relatorio_page():
    return render_template('requisicoes_relatorio.html')


def _relatorio_query(db, user, args):
    data_ini = (args.get('data_ini') or '').strip()
    data_fim = (args.get('data_fim') or '').strip()
    item_id  = args.get('item_id')
    fornecedor = (args.get('fornecedor') or '').strip()

    where = ["r.status = 'aprovada'"]
    params = []
    if not is_master(user):
        where.append("r.solicitante_id = ?")
        params.append(user['id'])
    if data_ini:
        where.append("DATE(r.data_decisao) >= DATE(?)")
        params.append(data_ini)
    if data_fim:
        where.append("DATE(r.data_decisao) <= DATE(?)")
        params.append(data_fim)
    if item_id and str(item_id).isdigit():
        where.append("ri.item_catalogo_id = ?")
        params.append(int(item_id))
    if fornecedor:
        where.append("r.fornecedor LIKE ? COLLATE NOCASE")
        params.append(f'%{fornecedor}%')
    where_sql = " AND ".join(where)

    linhas = db.execute(f"""
        SELECT r.id AS req_id, r.numero, r.data_decisao, r.data_solicitacao,
               r.solicitante_nome, r.funcionario_retirada, r.fornecedor,
               r.aprovador_nome, r.assinatura,
               ri.ordem, ri.descricao, ri.quantidade, ri.item_catalogo_id,
               ic.nome AS item_nome
        FROM requisicoes_compra r
        JOIN requisicoes_itens ri ON ri.requisicao_id = r.id
        LEFT JOIN itens_catalogo ic ON ic.id = ri.item_catalogo_id
        WHERE {where_sql}
        ORDER BY r.data_decisao DESC, r.id DESC, ri.ordem
    """, params).fetchall()

    resumo = db.execute(f"""
        SELECT ri.item_catalogo_id AS item_id,
               COALESCE(ic.nome, ri.descricao) AS item_nome,
               ic.unidade,
               COUNT(*) AS n_pedidos,
               MAX(r.data_decisao) AS ultima_autorizacao,
               COUNT(DISTINCT r.fornecedor) AS n_fornecedores,
               GROUP_CONCAT(DISTINCT r.fornecedor) AS fornecedores
        FROM requisicoes_compra r
        JOIN requisicoes_itens ri ON ri.requisicao_id = r.id
        LEFT JOIN itens_catalogo ic ON ic.id = ri.item_catalogo_id
        WHERE {where_sql}
        GROUP BY ri.item_catalogo_id, COALESCE(ic.nome, ri.descricao), ic.unidade
        ORDER BY n_pedidos DESC, item_nome
    """, params).fetchall()

    return {
        'filtros': {
            'data_ini': data_ini, 'data_fim': data_fim,
            'item_id': item_id, 'fornecedor': fornecedor,
        },
        'total_autorizacoes': len({r['req_id'] for r in linhas}),
        'total_itens': len(linhas),
        'linhas': [dict(r) for r in linhas],
        'resumo': [dict(r) for r in resumo],
    }


@app.route('/api/requisicoes/relatorio')
@api_login_required
def api_requisicoes_relatorio():
    data = _relatorio_query(get_db(), current_user(), request.args)
    data['is_master'] = is_master(current_user())
    return jsonify(data)


@app.route('/api/requisicoes/relatorio.csv')
@api_login_required
def api_requisicoes_relatorio_csv():
    data = _relatorio_query(get_db(), current_user(), request.args)
    output = io.StringIO()
    w = csv.writer(output, delimiter=';')
    w.writerow(['Data Autorização', 'Nº', 'Solicitante', 'Fornecedor',
                'Funcionário', 'Item', 'Quantidade', 'Autorizado por'])
    for r in data['linhas']:
        w.writerow([r['data_decisao'], r['numero'], r['solicitante_nome'], r['fornecedor'],
                    r['funcionario_retirada'], r['item_nome'] or r['descricao'],
                    r['quantidade'] or '', r['aprovador_nome']])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': 'attachment; filename=autorizacoes.csv'}
    )


# ── Backup do banco (somente master) ───────────────────────────────

@app.route('/admin/backup')
@master_required
def admin_backup():
    """Baixa um snapshot consistente do SQLite (online backup API)."""
    fd, tmp_path = tempfile.mkstemp(suffix='.db', prefix='fazenda_bkp_')
    os.close(fd)

    @after_this_request
    def _cleanup(response):
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        return response

    try:
        src = get_db()
        dst = sqlite3.connect(tmp_path)
        with dst:
            src.backup(dst)
        dst.close()
    except Exception as e:
        return f'Erro ao gerar backup: {e}', 500

    stamp = now_local().strftime('%Y%m%d_%H%M%S')
    return send_file(
        tmp_path,
        as_attachment=True,
        download_name=f'fazenda_backup_{stamp}.db',
        mimetype='application/octet-stream',
    )


# ── Admin: gerenciamento de usuários (master only) ─────────────────

@app.route('/admin/usuarios')
@master_required
def admin_usuarios_page():
    return render_template('admin_usuarios.html')


@app.route('/api/usuarios', methods=['GET'])
@api_master_required
def api_usuarios_list():
    db = get_db()
    rows = db.execute(
        "SELECT id, nome, email, papel, ativo, created_at FROM usuarios ORDER BY id"
    ).fetchall()
    return jsonify({
        'usuarios': [dict(r) for r in rows],
        'me': session.get('user_id'),
    })


@app.route('/api/usuarios', methods=['POST'])
@api_master_required
def api_usuarios_criar():
    db = get_db()
    data = request.get_json(silent=True) or {}
    nome  = (data.get('nome') or '').strip()
    email = (data.get('email') or '').strip().lower()
    senha = data.get('senha') or ''
    papel = (data.get('papel') or 'usuario').strip()

    if not nome:
        return jsonify({'erro': 'Informe o nome.'}), 400
    if not email:
        return jsonify({'erro': 'Informe o e-mail.'}), 400
    if len(senha) < 6:
        return jsonify({'erro': 'Senha deve ter ao menos 6 caracteres.'}), 400
    if papel not in ('usuario', 'master'):
        papel = 'usuario'

    if db.execute("SELECT 1 FROM usuarios WHERE lower(email)=?", (email,)).fetchone():
        return jsonify({'erro': 'Já existe um usuário com esse e-mail.'}), 400

    senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    cur = db.execute(
        "INSERT INTO usuarios (nome, email, senha_hash, papel, ativo) VALUES (?, ?, ?, ?, 1)",
        (nome, email, senha_hash, papel)
    )
    db.commit()
    return jsonify({'id': cur.lastrowid, 'ok': True}), 201


@app.route('/api/usuarios/<int:uid>/ativo', methods=['POST'])
@api_master_required
def api_usuarios_toggle_ativo(uid):
    db = get_db()
    if uid == session.get('user_id'):
        return jsonify({'erro': 'Você não pode desativar sua própria conta.'}), 400
    u = db.execute("SELECT ativo FROM usuarios WHERE id=?", (uid,)).fetchone()
    if not u:
        return jsonify({'erro': 'Usuário não encontrado.'}), 404
    novo = 0 if u['ativo'] else 1
    db.execute("UPDATE usuarios SET ativo=? WHERE id=?", (novo, uid))
    db.commit()
    return jsonify({'ativo': novo})


@app.route('/api/usuarios/<int:uid>/senha', methods=['POST'])
@api_master_required
def api_usuarios_reset_senha(uid):
    db = get_db()
    data = request.get_json(silent=True) or {}
    senha = data.get('senha') or ''
    if len(senha) < 6:
        return jsonify({'erro': 'Senha deve ter ao menos 6 caracteres.'}), 400
    if not db.execute("SELECT 1 FROM usuarios WHERE id=?", (uid,)).fetchone():
        return jsonify({'erro': 'Usuário não encontrado.'}), 404
    senha_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
    db.execute("UPDATE usuarios SET senha_hash=? WHERE id=?", (senha_hash, uid))
    db.commit()
    return jsonify({'ok': True})


@app.route('/api/usuarios/<int:uid>/papel', methods=['POST'])
@api_master_required
def api_usuarios_mudar_papel(uid):
    db = get_db()
    data = request.get_json(silent=True) or {}
    papel = (data.get('papel') or '').strip()
    if papel not in ('usuario', 'master'):
        return jsonify({'erro': 'Papel inválido.'}), 400
    if uid == session.get('user_id') and papel != 'master':
        return jsonify({'erro': 'Você não pode remover seu próprio papel de master.'}), 400
    if not db.execute("SELECT 1 FROM usuarios WHERE id=?", (uid,)).fetchone():
        return jsonify({'erro': 'Usuário não encontrado.'}), 404
    db.execute("UPDATE usuarios SET papel=? WHERE id=?", (papel, uid))
    db.commit()
    return jsonify({'ok': True, 'papel': papel})


# ── Init & Run ─────────────────────────────────────────────────────

with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
