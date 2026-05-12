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
