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


def _insert_lote(client, **kw):
    """Helper: insert a lote via raw SQL using the same temp DB."""
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
