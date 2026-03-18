import os
import sqlite3
import json
import csv
import io
from datetime import datetime
from functools import wraps

import bcrypt
import xlrd
import anthropic
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, g, send_file, Response
)
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')

DATABASE = os.path.join(os.path.dirname(__file__), 'data', 'fazenda167.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
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


# ── Init & Run ─────────────────────────────────────────────────────

with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
