-- Usuários (login)
CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    senha_hash    TEXT NOT NULL,
    ativo         INTEGER DEFAULT 1,
    papel         TEXT NOT NULL DEFAULT 'usuario',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Rodadas de avaliação importadas
CREATE TABLE IF NOT EXISTS rodadas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,
    data_import   DATETIME DEFAULT CURRENT_TIMESTAMP,
    arquivo_mat   TEXT,
    arquivo_saf   TEXT,
    n_matrizes    INTEGER DEFAULT 0,
    n_produtos    INTEGER DEFAULT 0
);

-- Matrizes ativas (atualizada a cada rodada)
CREATE TABLE IF NOT EXISTS matrizes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id     TEXT NOT NULL UNIQUE,
    data_nasc     TEXT,
    touro_pai     TEXT,
    tipo_serv     TEXT,
    mae_id        TEXT,
    avo_paterno   TEXT,
    avo_materno   TEXT,
    rebanho       TEXT,
    genotipada    INTEGER DEFAULT 0,
    categoria     TEXT,
    precoce       INTEGER DEFAULT 0,
    ceip          INTEGER DEFAULT 0,
    np_ceip       INTEGER DEFAULT 0,
    score_r       REAL,
    score_f       REAL,
    score_a       REAL,
    score_p       REAL,
    rank_cia      INTEGER,
    ipp           REAL,
    iep           REAL,
    pv            REAL,
    desc_ap       TEXT,
    rodada_id     INTEGER REFERENCES rodadas(id),
    ativo         INTEGER DEFAULT 1,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Avaliações genéticas (uma por rodada — histórico completo)
CREATE TABLE IF NOT EXISTS avaliacoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id       TEXT NOT NULL,
    rodada_id       INTEGER NOT NULL REFERENCES rodadas(id),
    iciagen         REAL,
    deca_icia_g     INTEGER,
    deca_icia_f     INTEGER,
    perc_icia       REAL,
    acc_icia        REAL,
    idesm           REAL,
    deca_idesm_g    INTEGER,
    deca_idesm_f    INTEGER,
    perc_idesm      REAL,
    acc_idesm       REAL,
    rmat            REAL,
    deca_rmat       INTEGER,
    perc_rmat       REAL,
    acc_rmat        REAL,
    ifrig           REAL,
    hgp             REAL,
    ncaract         INTEGER,
    dep_pn          REAL,
    dep_gnd         REAL,
    dep_cd          REAL,
    dep_pd          REAL,
    dep_md          REAL,
    dep_ud          REAL,
    dep_gpd         REAL,
    dep_cs          REAL,
    dep_ps          REAL,
    dep_ms          REAL,
    dep_us          REAL,
    dep_temp        REAL,
    dep_gns         REAL,
    dep_pei         REAL,
    dep_peip        REAL,
    UNIQUE(animal_id, rodada_id)
);

-- Produtos / filhos (alimentado pelas planilhas de safra)
CREATE TABLE IF NOT EXISTS produtos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id    TEXT NOT NULL,
    rodada_id     INTEGER NOT NULL REFERENCES rodadas(id),
    mae_id        TEXT NOT NULL,
    touro         TEXT,
    tipo_serv     TEXT,
    avo_materno   TEXT,
    data_nasc     TEXT,
    sexo          TEXT,
    pn            REAL,
    peso_desm     REAL,
    idade_desm    INTEGER,
    peso_sob      REAL,
    idade_sob     INTEGER,
    gnd_aj        REAL,
    gpd_aj        REAL,
    iciagen       REAL,
    deca_iciagen  INTEGER,
    idesm         REAL,
    deca_idesm    INTEGER,
    rmat          REAL,
    ifrig         REAL,
    conect_desm   TEXT,
    pai_dna       INTEGER DEFAULT 0,
    ceip          INTEGER DEFAULT 0,
    safra_ano     TEXT,
    UNIQUE(produto_id, rodada_id)
);

-- Grupos de estoque (safras de venda)
CREATE TABLE IF NOT EXISTS estoque_grupos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nome        TEXT NOT NULL UNIQUE,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Estoque de touros para venda
CREATE TABLE IF NOT EXISTS estoque_touros (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    brinco      TEXT NOT NULL,
    grupo_id    INTEGER REFERENCES estoque_grupos(id),
    data_nasc   TEXT,
    pai         TEXT,
    avo_paterno TEXT,
    avo_materno TEXT,
    idesm       REAL,
    iciagen     REAL,
    rmat        REAL,
    ifrig       REAL,
    peso        REAL,
    data_pesagem TEXT,
    vendido     INTEGER DEFAULT 0,
    data_venda  TEXT,
    valor_venda REAL,
    obs         TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brinco, grupo_id)
);

-- Requisições de compra (fluxo solicitação → autorização do master)
CREATE TABLE IF NOT EXISTS requisicoes_compra (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    numero               TEXT UNIQUE,
    data_solicitacao     DATETIME DEFAULT CURRENT_TIMESTAMP,
    solicitante_id       INTEGER NOT NULL REFERENCES usuarios(id),
    solicitante_nome     TEXT NOT NULL,
    responsavel          TEXT,
    funcionario_retirada TEXT NOT NULL,
    fornecedor           TEXT NOT NULL,
    observacoes          TEXT,
    status               TEXT NOT NULL DEFAULT 'pendente',
    aprovador_id         INTEGER REFERENCES usuarios(id),
    aprovador_nome       TEXT,
    assinatura           TEXT,
    data_decisao         DATETIME,
    motivo_rejeicao      TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Itens de cada requisição (1..N)
CREATE TABLE IF NOT EXISTS requisicoes_itens (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    requisicao_id     INTEGER NOT NULL REFERENCES requisicoes_compra(id) ON DELETE CASCADE,
    ordem             INTEGER NOT NULL,
    descricao         TEXT NOT NULL,
    quantidade        TEXT,
    item_catalogo_id  INTEGER REFERENCES itens_catalogo(id)
);

-- Catálogo de itens (populado automaticamente a partir das requisições)
CREATE TABLE IF NOT EXISTS itens_catalogo (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    nome           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    unidade        TEXT,
    categoria      TEXT,
    observacoes    TEXT,
    n_pedidos      INTEGER DEFAULT 0,
    ultimo_uso     DATETIME,
    ativo          INTEGER DEFAULT 1,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Histórico de ações (trilha de auditoria)
CREATE TABLE IF NOT EXISTS requisicoes_historico (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    requisicao_id  INTEGER NOT NULL REFERENCES requisicoes_compra(id) ON DELETE CASCADE,
    usuario_id     INTEGER REFERENCES usuarios(id),
    usuario_nome   TEXT,
    acao           TEXT NOT NULL,
    detalhes       TEXT,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_prod_mae   ON produtos(mae_id);
CREATE INDEX IF NOT EXISTS idx_aval_ani   ON avaliacoes(animal_id);
CREATE INDEX IF NOT EXISTS idx_aval_rod   ON avaliacoes(rodada_id);
CREATE INDEX IF NOT EXISTS idx_mat_id     ON matrizes(animal_id);
CREATE INDEX IF NOT EXISTS idx_mat_ceip   ON matrizes(ceip);
CREATE INDEX IF NOT EXISTS idx_mat_categ  ON matrizes(categoria);
CREATE INDEX IF NOT EXISTS idx_req_status ON requisicoes_compra(status);
CREATE INDEX IF NOT EXISTS idx_req_solic  ON requisicoes_compra(solicitante_id);
CREATE INDEX IF NOT EXISTS idx_req_aprov  ON requisicoes_compra(aprovador_id);
CREATE INDEX IF NOT EXISTS idx_req_itens  ON requisicoes_itens(requisicao_id);
CREATE INDEX IF NOT EXISTS idx_req_hist   ON requisicoes_historico(requisicao_id);
CREATE INDEX IF NOT EXISTS idx_catalogo_nome ON itens_catalogo(nome);
-- idx_req_itens_cat criado em init_db após ALTER TABLE (ordem de migração)

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
