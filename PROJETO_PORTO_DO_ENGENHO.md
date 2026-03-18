# PROJETO: Sistema de Gestão Genética – Fazenda Porto do Engenho
### Documento técnico para implementação com Claude Code

---

## 1. VISÃO GERAL

Sistema web privado para gestão do programa de melhoramento genético da **Fazenda Porto do Engenho** (Nelore, Cariacica-ES), associada à **CIA de Melhoramento** sob o código **Fazenda 167**.

A CIA realiza **10 rodadas de avaliação genética por ano**. Em cada rodada entrega arquivos XLS com dados de matrizes, índices genéticos e produtos. O sistema importa esses arquivos e exibe dashboards, ficha individual do animal e histórico de evolução genética.

**Stack escolhida:**
- Backend: Python + Flask + SQLite (Turso em produção)
- Frontend: HTML/CSS/JS puro (sem framework) servido pelo Flask
- Auth: sessão Flask com JWT (login simples usuário/senha)
- Deploy: Railway.app (ou Render.com)
- Repositório: GitHub privado

---

## 2. IDENTIDADE VISUAL

**Paleta obrigatória** extraída da logo da fazenda:

```css
--red:   #D01B20;   /* vermelho principal */
--black: #1A1A1A;   /* preto */
--white: #FFFFFF;
--gray5: #F4F4F4;   /* fundo geral */
--ok:    #1A7A3F;   /* verde para indicadores positivos */
```

**Tipografia:** Barlow + Barlow Condensed (Google Fonts) + DM Mono para valores numéricos.

**Logo:** arquivo `static/logo.jpg` — exibir no sidebar e topbar com fundo branco e padding 3px.

---

## 3. ESTRUTURA DE ARQUIVOS

```
porto-engenho/
├── app.py                  # Backend Flask principal
├── schema.sql              # Definição do banco de dados
├── requirements.txt
├── .env                    # Variáveis de ambiente (nunca commitar)
├── .env.example
├── .gitignore
├── Procfile                # Para deploy Railway/Render
├── runtime.txt             # python-3.11.x
├── data/
│   └── fazenda167.db       # SQLite local (dev)
├── uploads/                # XLS recebidos (temporário)
├── static/
│   ├── logo.jpg            # Logo da fazenda
│   ├── favicon.ico
│   ├── css/
│   │   └── main.css        # Estilos globais
│   └── js/
│       └── main.js         # Utilitários JS compartilhados
└── templates/
    ├── base.html           # Layout base com sidebar + topbar
    ├── login.html          # Página de login
    ├── dashboard.html      # Dashboard principal
    ├── ficha.html          # Ficha do animal
    ├── matrizes.html       # Catálogo de matrizes
    ├── indices.html        # Índices principais
    ├── posicao.html        # Posição vs CIA
    ├── touros.html         # Reprodutores
    └── importar.html       # Importação de rodadas
```

---

## 4. BANCO DE DADOS (schema.sql)

```sql
-- Usuários (login)
CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    senha_hash    TEXT NOT NULL,       -- bcrypt
    ativo         INTEGER DEFAULT 1,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Rodadas de avaliação importadas
CREATE TABLE IF NOT EXISTS rodadas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nome          TEXT NOT NULL,        -- ex: "R3 · Jul/2025"
    data_import   DATETIME DEFAULT CURRENT_TIMESTAMP,
    arquivo_mat   TEXT,                 -- nome do arquivo de matrizes
    arquivo_saf   TEXT,                 -- nome do arquivo de safra
    n_matrizes    INTEGER DEFAULT 0,
    n_produtos    INTEGER DEFAULT 0
);

-- Matrizes ativas (atualizada a cada rodada)
CREATE TABLE IF NOT EXISTS matrizes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id     TEXT NOT NULL UNIQUE,  -- "vaca" no XLS, ex: "P16702071515"
    data_nasc     TEXT,
    touro_pai     TEXT,
    tipo_serv     TEXT,                  -- IA / MC / RM / TE
    mae_id        TEXT,
    avo_paterno   TEXT,
    avo_materno   TEXT,
    rebanho       TEXT,
    genotipada    INTEGER DEFAULT 0,
    categoria     TEXT,                  -- N / P / S / M
    precoce       INTEGER DEFAULT 0,
    ceip          INTEGER DEFAULT 0,
    np_ceip       INTEGER DEFAULT 0,
    score_r       REAL,                  -- Racial
    score_f       REAL,                  -- Frame
    score_a       REAL,                  -- Aprumos
    score_p       REAL,                  -- Pigmentação
    rank_cia      INTEGER,
    ipp           REAL,                  -- Idade ao 1º parto (meses)
    iep           REAL,                  -- Intervalo entre partos (meses)
    pv            REAL,                  -- Peso adulto (kg)
    desc_ap       TEXT,                  -- Descrição aprumo
    rodada_id     INTEGER REFERENCES rodadas(id),
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Avaliações genéticas (uma por rodada — histórico completo)
CREATE TABLE IF NOT EXISTS avaliacoes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id       TEXT NOT NULL,
    rodada_id       INTEGER NOT NULL REFERENCES rodadas(id),
    -- 4 índices principais
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
    -- Índices secundários
    ifrig           REAL,
    hgp             REAL,
    ncaract         INTEGER,
    -- DEPhs genômicas
    dep_pn          REAL,   -- Peso ao nascer
    dep_gnd         REAL,   -- Ganho pré-desmama (205d)
    dep_cd          REAL,   -- Conformação desmama
    dep_pd          REAL,   -- Precocidade desmama
    dep_md          REAL,   -- Musculosidade desmama
    dep_ud          REAL,   -- Umbigo desmama
    dep_gpd         REAL,   -- Ganho pós-desmama (245d)
    dep_cs          REAL,   -- Conformação sobreano
    dep_ps          REAL,   -- Precocidade sobreano
    dep_ms          REAL,   -- Musculosidade sobreano
    dep_us          REAL,   -- Umbigo sobreano
    dep_temp        REAL,   -- Temperamento
    dep_gns         REAL,   -- Ganho nascimento→sobreano (450d)
    dep_pei         REAL,   -- Perímetro escrotal (idade)
    dep_peip        REAL,   -- Perímetro escrotal (idade+peso)
    UNIQUE(animal_id, rodada_id)
);

-- Produtos / filhos (alimentado pelas planilhas de safra)
CREATE TABLE IF NOT EXISTS produtos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    produto_id    TEXT NOT NULL,          -- ID do bezerro
    rodada_id     INTEGER NOT NULL REFERENCES rodadas(id),
    mae_id        TEXT NOT NULL,          -- vaca (mãe) → matrizes.animal_id
    touro         TEXT,
    tipo_serv     TEXT,
    avo_materno   TEXT,
    data_nasc     TEXT,
    sexo          TEXT,                   -- M / F
    -- Fenótipo
    pn            REAL,                   -- Peso ao nascer
    peso_desm     REAL,
    idade_desm    INTEGER,
    peso_sob      REAL,
    idade_sob     INTEGER,
    gnd_aj        REAL,                   -- GND ajustado
    gpd_aj        REAL,                   -- GPD ajustado
    -- Genético
    iciagen       REAL,
    deca_iciagen  INTEGER,
    idesm         REAL,
    deca_idesm    INTEGER,
    rmat          REAL,
    ifrig         REAL,
    conect_desm   TEXT,                   -- c=conectado / d=desconectado
    pai_dna       INTEGER DEFAULT 0,      -- 1=paternidade corrigida por DNA
    ceip          INTEGER DEFAULT 0,
    UNIQUE(produto_id, rodada_id)
);

-- Índices para performance
CREATE INDEX IF NOT EXISTS idx_prod_mae   ON produtos(mae_id);
CREATE INDEX IF NOT EXISTS idx_aval_ani   ON avaliacoes(animal_id);
CREATE INDEX IF NOT EXISTS idx_aval_rod   ON avaliacoes(rodada_id);
CREATE INDEX IF NOT EXISTS idx_mat_id     ON matrizes(animal_id);
CREATE INDEX IF NOT EXISTS idx_mat_ceip   ON matrizes(ceip);
CREATE INDEX IF NOT EXISTS idx_mat_categ  ON matrizes(categoria);
```

---

## 5. MAPEAMENTO DOS CAMPOS XLS → BANCO

### 5.1 Arquivo `167_avg_matrizes.xls` → aba `matrizes`

| Campo XLS         | Campo DB (matrizes)    | Campo DB (avaliacoes)  |
|-------------------|------------------------|------------------------|
| `vaca`            | `animal_id`            | `animal_id`            |
| `dataN`           | `data_nasc`            |                        |
| `touro_pai`       | `touro_pai`            |                        |
| `TS`              | `tipo_serv`            |                        |
| `mae`             | `mae_id`               |                        |
| `avo_paterno`     | `avo_paterno`          |                        |
| `avo_materno`     | `avo_materno`          |                        |
| `rebanho`         | `rebanho`              |                        |
| `geno`            | `genotipada` (bool)    |                        |
| `categ`           | `categoria`            |                        |
| `precoce`         | `precoce` (bool)       |                        |
| `CEIP`            | `ceip` (bool)          |                        |
| `np_CEIP`         | `np_ceip`              |                        |
| `R`               | `score_r`              |                        |
| `F`               | `score_f`              |                        |
| `A`               | `score_a`              |                        |
| `P`               | `score_p`              |                        |
| `rank`            | `rank_cia`             |                        |
| `IPP`             | `ipp`                  |                        |
| `IEP`             | `iep`                  |                        |
| `PV`              | `pv`                   |                        |
| `DESC_AP`         | `desc_ap`              |                        |
| `ICiaGen`         |                        | `iciagen`              |
| `decaG_ICiaGen`   |                        | `deca_icia_g`          |
| `decaF_ICiaGen`   |                        | `deca_icia_f`          |
| `perc_ICiaGen`    |                        | `perc_icia` (×100)     |
| `acc_ICiaGen`     |                        | `acc_icia`             |
| `IDESM`           |                        | `idesm`                |
| `decaG_IDESM`     |                        | `deca_idesm_g`         |
| `decaF_IDESM`     |                        | `deca_idesm_f`         |
| `perc_IDESM`      |                        | `perc_idesm` (×100)    |
| `acc_IDESM`       |                        | `acc_idesm`            |
| `RMat`            |                        | `rmat`                 |
| `decaG_RMat`      |                        | `deca_rmat`            |
| `perc_RMat`       |                        | `perc_rmat`            |
| `acc_RMat`        |                        | `acc_rmat`             |
| `IFRIG`           |                        | `ifrig`                |
| `HGP`             |                        | `hgp`                  |
| `ncaract`         |                        | `ncaract`              |
| `PN`              |                        | `dep_pn`               |
| `GND`             |                        | `dep_gnd`              |
| `CD`              |                        | `dep_cd`               |
| `PD`              |                        | `dep_pd`               |
| `MD`              |                        | `dep_md`               |
| `UD`              |                        | `dep_ud`               |
| `GPD`             |                        | `dep_gpd`              |
| `CS`              |                        | `dep_cs`               |
| `PS`              |                        | `dep_ps`               |
| `MS`              |                        | `dep_ms`               |
| `US`              |                        | `dep_us`               |
| `TEMP`            |                        | `dep_temp`             |
| `GNS`             |                        | `dep_gns`              |
| `PEi`             |                        | `dep_pei`              |
| `PEip`            |                        | `dep_peip`             |

### 5.2 Arquivo `167_avg_safraANO.xls` → aba `geral` → tabela `produtos`

| Campo XLS         | Campo DB (produtos)    |
|-------------------|------------------------|
| `produto`         | `produto_id`           |
| `vaca`            | `mae_id`               |
| `touro`           | `touro`                |
| `TS`              | `tipo_serv`            |
| `avo_materno`     | `avo_materno`          |
| `dataN`           | `data_nasc`            |
| `sexo`            | `sexo`                 |
| `PN`              | `pn`                   |
| `peso_DESM`       | `peso_desm`            |
| `idade_DESM`      | `idade_desm`           |
| `peso_SOB`        | `peso_sob`             |
| `idade_SOB`       | `idade_sob`            |
| `aj_GND`          | `gnd_aj`               |
| `aj_GPD`          | `gpd_aj`               |
| `ICiaGen`         | `iciagen`              |
| `decaG_ICiaGen`   | `deca_iciagen`         |
| `IDESM`           | `idesm`                |
| `decaG_IDESM`     | `deca_idesm`           |
| `RMat`            | `rmat`                 |
| `IFRIG`           | `ifrig`                |
| `conect_DESM`     | `conect_desm`          |
| `pai_dna`         | `pai_dna` (bool)       |
| `CEIP`            | `ceip` (bool)          |

---

## 6. AUTENTICAÇÃO

### Sistema de login simples:

```python
# Dependências a adicionar em requirements.txt:
# bcrypt>=4.0
# flask-login>=0.6
# python-dotenv>=1.0

# Fluxo:
# 1. GET  /login  → exibe form
# 2. POST /login  → verifica email+senha, cria sessão, redireciona para /
# 3. GET  /logout → destrói sessão, redireciona para /login
# 4. Todas as outras rotas: @login_required
```

**Tabela `usuarios`** (já no schema):
- Na primeira execução, criar um usuário admin via script `create_admin.py`
- Senha hasheada com bcrypt
- Sem "esqueci minha senha" na v1 (alterar direto no banco)

**Variáveis .env:**
```
SECRET_KEY=gerar_string_aleatoria_64_chars
ADMIN_EMAIL=fazenda@portodoengenho.com.br
ADMIN_PASSWORD=senha_forte_aqui
DATABASE_URL=sqlite:///data/fazenda167.db
```

**Página de login (`templates/login.html`):**
- Fundo preto com logo centralizada
- Card vermelho/preto com campos email e senha
- Botão "Entrar" vermelho
- Sem cadastro público — apenas admin cria contas

---

## 7. PÁGINAS E FUNCIONALIDADES

### 7.1 Layout Base (`templates/base.html`)

Toda página herda este layout com:

**Sidebar esquerdo (230px, fundo preto `#1A1A1A`):**
- Logo da fazenda no topo
- Badge "Rodada R3 · Jul/2025" com dot animado verde
- Menu de navegação:
  - ▦ Dashboard
  - ◎ Índices Principais
  - ⊕ Posição vs CIA
  - ♀ Matrizes Ativas
  - ◎ Ficha do Animal
  - ♂ Reprodutores
  - ↑ Importar Rodada
- Footer: "CIA de Melhoramento · Nelore Provado · Cariacica-ES"

**Topbar (56px, fundo preto, borda inferior vermelha):**
- Logo pequena à esquerda
- Nome da página atual
- Botão "Importar XLS" e "Exportar" à direita
- Link "Sair" com ícone

---

### 7.2 Dashboard (`/` → `templates/dashboard.html`)

**5 KPI cards (grid 5 colunas):**
1. ICIAGen Médio — valor do rebanho vs CIA
2. IDESM Médio — valor do rebanho vs CIA
3. CEIP Certificadas — total e percentual
4. IPP Médio — em meses, comparar com CIA
5. RMat Médio — kg/vaca/ano estimado

**Gráfico 1:** ICIAGen médio das matrizes ativas por safra de nascimento (barras)
- Dados: `SELECT dataN, AVG(a.iciagen) FROM matrizes JOIN avaliacoes...`
- Barras vermelhas para safras mais recentes (>8), pretas para anteriores

**Gráfico 2:** Composição do rebanho por categoria (stacked bar horizontal + contadores)
- Multíparas (M), Novilhas (N), Primíparas (P), Secundíparas (S)
- Barras de genotipadas, precoces, CEIP

**Tabela Top 10 matrizes por ICIAGen:**
- Colunas: #, Animal ID, Categoria, ICIAGen, Deca, IDESM, RMat, CEIP
- Clicar no ID → vai para `/ficha?id=ANIMAL_ID`

**Painel de alertas:**
- Indicadores acima de 70% (pontos de atenção) em vermelho
- Indicadores abaixo de 30% (destaques) em verde
- Buscar os valores de posição CIA na tabela `avaliacoes` → campos `perc_icia`, `perc_idesm`, etc.

**Distribuição Deca ICIAGen (gráfico de barras):**
- `SELECT deca_icia_g, COUNT(*) FROM avaliacoes GROUP BY deca_icia_g`

---

### 7.3 Índices Principais (`/indices` → `templates/indices.html`)

Quatro cards explicativos, um por índice, cada um com:
- Nome completo + sigla em destaque vermelho
- Descrição técnica (ver seção 10)
- Valores reais: média fazenda, média CIA, máximo
- Percentil no programa
- Gráfico de evolução por safra (linha vermelha = fazenda, tracejado cinza = CIA)

**Gráficos de evolução** lêem de `avaliacoes JOIN rodadas`:
```sql
SELECT r.nome, AVG(a.iciagen), AVG(a.idesm), AVG(a.rmat)
FROM avaliacoes a JOIN rodadas r ON r.id=a.rodada_id
GROUP BY r.id ORDER BY r.id
```

---

### 7.4 Posição vs CIA (`/posicao` → `templates/posicao.html`)

**Grid de barras de posicionamento (% melhor ranqueadas):**

| Indicador         | Valor do percentil    | Cor              |
|-------------------|-----------------------|------------------|
| ICIAGen           | 5,4%                  | Verde (≤30%)     |
| IDESM             | 4,7%                  | Verde            |
| CEIP              | 4,7%                  | Verde            |
| IPP               | 28,7%                 | Verde            |
| RMat (touros)     | 38,0%                 | Verde            |
| IEP               | 46,5%                 | Neutro           |
| PV médio          | 23,3%                 | Verde            |
| Desmame           | 73,7%                 | Vermelho (≥70%)  |
| Aprumos           | 85,3%                 | Vermelho ⚠       |
| Diversidade       | 71,5%                 | Vermelho         |

**Gráfico radar** (Chart.js tipo `radar`):
- 8 eixos: ICIAGen, IDESM, CEIP, IPP, RMat, IEP, Precoces, Genotipadas
- Valores invertidos (percentil baixo = bom = maior no radar)

**Gráfico de tendência:** ICIAGen fazenda vs CIA por safra

**ICIAGen por categoria:** barras por M/N/P/S com médias reais

---

### 7.5 Matrizes Ativas (`/matrizes` → `templates/matrizes.html`)

**5 KPI cards:**
- IPP Médio, IEP Médio, PV ≤5 anos, PV >5 anos, Aprumos %

**Filtros:**
```
[ CEIP ] [ Deca 1 ] [ Categoria: M N P S ] [ Safra: dropdown ] [ ICIAGen ≥: input ]
```

**Tabela paginada (10 por página):**
- Colunas: #, Animal ID (link para ficha), Cat., Touro Pai, Safra, ICIAGen, Deca G, Deca F, IDESM, RMat, IPP, PV, Precoce, CEIP
- Ordenação por colunas
- Botão "Exportar CSV" (gera download do resultado filtrado)

**API endpoint:** `GET /api/matrizes?ceip=1&categ=M&deca=1&q=texto&limit=10&offset=0&order=iciagen`

---

### 7.6 Ficha do Animal (`/ficha` → `templates/ficha.html`)

**Topbar especial:** seletor dropdown com todas as 469 matrizes ordenadas por ICIAGen

**Coluna esquerda (330px fixo):**

**Hero card** (fundo preto, borda esquerda vermelha):
- ID do animal em fonte mono grande
- Badges: CEIP (com nº de produtos), Genômica, Precoce/Super-precoce, Tipo serviço
- Nascimento + idade calculada, Safra, Categoria, Rank CIA, Touro pai

**Índices Genéticos** (grid 2×2):
- **ICIAGen** — valor, deca global, deca fazenda, percentil CIA, barra de acurácia
- **IDESM** — idem
- **RMat** — idem
- **IFRIG** — valor + HGP abaixo

**Reprodutivo & Produtivo:**
- IPP (meses + classificação: convencional/precoce/super-precoce)
- IEP (meses)
- PV — Peso Adulto (kg)
- Contador de filhos no banco + produtos CEIP

**Conformação Racial** (se disponível):
- Scores R, F, A, P com código de cor (≥3 verde, 2 preto, <2 vermelho)
- Alerta aprumo se `desc_ap` preenchido

**Coluna direita:**

**Genealogia** (árvore visual):
```
[Avô Paterno]    [Avô Materno]
      ↓                ↓
[Pai (Touro)]    [Mãe]
        ↓       ↓
      [ESTE ANIMAL]
```
- Se a mãe estiver na tabela `matrizes`, mostrar badge "↗ Ver ficha" clicável
- Avós carregados dos campos `avo_paterno` e `avo_materno`

**Filhos** (dinâmico — vem do banco):
```sql
SELECT p.*, r.nome as rodada_nome
FROM produtos p JOIN rodadas r ON r.id=p.rodada_id
WHERE p.mae_id = :animal_id
GROUP BY p.produto_id
HAVING p.rodada_id = MAX(p.rodada_id)
ORDER BY p.data_nasc ASC
```
- Se vazio: mensagem "Importe novas planilhas de safra para os filhos aparecerem aqui automaticamente."
- Cada filho exibe: ID, ♂/♀, nascimento, pai (touro), tipo serviço, pesos, ICIAGen+deca, IDESM+deca, RMat, indicador conectividade, de qual rodada veio

**Evolução por Rodada** (gráfico de linha, só aparece se >1 rodada):
```sql
SELECT a.iciagen, a.idesm, a.rmat, r.nome
FROM avaliacoes a JOIN rodadas r ON r.id=a.rodada_id
WHERE a.animal_id = :animal_id ORDER BY r.id
```
- Três linhas: ICIAGen (vermelho), IDESM (preto tracejado), RMat (verde tracejado)

**DEPhs Genômicas** (grid 3 colunas):
- PN, GND, CD, PD, MD, GPD, CS, PS, MS, TEMP, PEi, GNS
- Mini barra de posicionamento relativo por valor

**Endpoints da Ficha:**
- `GET /api/animal/<animal_id>` → matriz + avaliação atual + genealogia + filhos
- `GET /api/animal/<animal_id>/historico` → todas as avaliações por rodada

---

### 7.7 Reprodutores (`/touros` → `templates/touros.html`)

**Top 10 touros por contribuição genética** (hardcoded por ora, futuramente da planilha):
- Barras horizontais mostrando % individual e acumulada
- Gráfico de linha: acumulado (preto) + individual (vermelho)

**ICIAGen médio dos touros por safra:**
- `SELECT touro, AVG(iciagen) FROM produtos WHERE touro IS NOT NULL GROUP BY touro`

---

### 7.8 Importar Rodada (`/importar` → `templates/importar.html`)

**Form de upload (multipart/form-data):**
```html
<input name="nome_rodada" type="text" placeholder="ex: R4 · Out/2025">
<input name="arquivo_mat" type="file" accept=".xls,.xlsx">
<input name="arquivo_saf" type="file" accept=".xls,.xlsx">
<button type="submit">Importar</button>
```

**Endpoint:** `POST /api/importar`

**Lógica de importação:**
1. Salvar arquivos em `uploads/`
2. Criar registro em `rodadas`
3. Ler `arquivo_mat` → aba `matrizes` → upsert `matrizes` + insert `avaliacoes`
4. Ler `arquivo_saf` → aba `geral` → insert `produtos` (UNIQUE ignora duplicatas)
5. Atualizar `n_matrizes` e `n_produtos` na rodada
6. Retornar JSON com resumo
7. Na página: mostrar progress bar durante upload, depois resumo do resultado

**Lista de rodadas importadas** (tabela na mesma página):
- Rodada, Data, Nº matrizes, Nº produtos, Ações (excluir)

---

## 8. API ENDPOINTS COMPLETOS

| Método | Endpoint | Parâmetros | Retorno |
|--------|----------|------------|---------|
| GET | `/api/dropdown` | — | Lista compacta para seletor (animal_id, iciagen, ceip, touro_pai) |
| GET | `/api/matrizes` | `q, ceip, categ, deca, precoce, limit, offset, order` | Paginado com total |
| GET | `/api/animal/<id>` | — | matriz + avaliação + mae_dados + filhos |
| GET | `/api/animal/<id>/historico` | — | Lista de avaliações por rodada |
| POST | `/api/importar` | form: nome_rodada, arquivo_mat, arquivo_saf | Resultado da importação |
| GET | `/api/rodadas` | — | Lista de rodadas |
| DELETE | `/api/rodadas/<id>` | — | Excluir rodada (cascade) |
| GET | `/api/dashboard/kpis` | — | KPIs atuais do rebanho |
| GET | `/api/dashboard/safras` | — | ICIAGen médio por safra |
| GET | `/api/export/matrizes.csv` | Mesmos filtros do GET /api/matrizes | Download CSV |
| POST | `/auth/login` | email, password | Sessão + redirect |
| GET | `/auth/logout` | — | Destroy sessão + redirect |

---

## 9. AUTENTICAÇÃO — IMPLEMENTAÇÃO DETALHADA

```python
# app.py — seção de auth
from flask import session, redirect, url_for, request, flash
from functools import wraps
import bcrypt

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        senha = request.form['password'].encode()
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
```

**Todas as rotas de página** devem ter `@login_required`:
```python
@app.route('/')
@login_required
def dashboard(): ...

@app.route('/ficha')
@login_required
def ficha(): ...
```

**Todas as rotas `/api/*`** devem verificar sessão:
```python
def api_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'erro': 'Não autenticado'}), 401
        return f(*args, **kwargs)
    return decorated
```

**Script para criar admin** (`create_admin.py`):
```python
import bcrypt, sqlite3, os
from dotenv import load_dotenv
load_dotenv()

email = os.getenv('ADMIN_EMAIL')
senha = os.getenv('ADMIN_PASSWORD').encode()
hash_ = bcrypt.hashpw(senha, bcrypt.gensalt()).decode()

conn = sqlite3.connect('data/fazenda167.db')
conn.execute("INSERT OR IGNORE INTO usuarios (nome,email,senha_hash) VALUES (?,?,?)",
             ("Administrador", email, hash_))
conn.commit()
print(f"Admin criado: {email}")
```

---

## 10. DESCRIÇÕES DOS ÍNDICES (para a página /indices)

### ICIAGen — Índice CIA de Melhoramento Genômico
Índice harmônico de seleção que penaliza animais com DEPs genômicas desequilibradas e bonifica aqueles com superioridade harmônica entre as características. Maior ênfase em precocidade sexual (versão ICIAGen vs ICia anterior). Pondera crescimento, carcaça, reprodução e temperamento.
- **Direção:** maior é melhor
- **Fazenda 167:** média 6,49 · CIA: 1,46 · Top 5,4%
- **Deca fazenda:** média 2,17 (deca 1 = top 10%)

### IDESM — Índice Desmama
Índice maternal que pondera o efeito direto do crescimento e qualidade de carcaça até a desmama. Indicador chave para eficiência da vaca como mãe.
- **Direção:** maior é melhor
- **Fazenda 167:** média 7,23 · CIA: −1,94 · Top 4,7%
- **Últimas 3 safras:** fazenda 4,20 vs CIA 0,98

### IPP — Idade ao Primeiro Parto
Precocidade sexual da fêmea medida em meses. Fêmeas com IPP < 30m = **precoces**; IPP < 24m = **super-precoces**.
- **Direção:** menor é melhor
- **Fazenda 167:** média 28,6m · CIA: 29,9m · 56,7% precoces
- **Posição CIA:** 28,7% (fazenda mais precoce que a maioria)

### RMat — Retorno Maternal
Índice bioeconômico: estima o retorno por vaca (kg de peso vivo produzido/ano), descontado o custo de mantença. Componentes: precocidade (IPP) + permanência reprodutiva (NP53: partos até 53 meses) + custo de mantença (PV peso adulto) + desempenho dos bezerros (IDESM).
- **Direção:** maior é melhor
- **Fazenda 167:** ~125 kg/vaca/ano (acima da CIA ~100)
- **Posição CIA:** 38% (touros)

---

## 11. DEPLOY — PASSO A PASSO

### 11.1 Preparação do repositório

```bash
# 1. Criar repositório GitHub privado
git init
git remote add origin https://github.com/SEU_USUARIO/porto-engenho.git

# 2. .gitignore obrigatório:
echo "data/fazenda167.db
uploads/
.env
__pycache__/
*.pyc
*.pyo
.DS_Store
venv/" > .gitignore

# 3. Criar .env.example (sem valores reais):
echo "SECRET_KEY=
ADMIN_EMAIL=
ADMIN_PASSWORD=
DATABASE_URL=sqlite:///data/fazenda167.db" > .env.example
```

### 11.2 requirements.txt

```
flask>=3.0
xlrd>=2.0
bcrypt>=4.0
python-dotenv>=1.0
gunicorn>=21.0
```

### 11.3 Procfile (para Railway/Render)

```
web: gunicorn app:app --bind 0.0.0.0:$PORT --workers 2
```

### 11.4 runtime.txt

```
python-3.11.9
```

### 11.5 Deploy no Railway.app

```
1. Acesse railway.app → New Project → Deploy from GitHub Repo
2. Selecionar o repositório porto-engenho
3. Railway detecta automaticamente o Procfile
4. Em "Variables", adicionar:
   - SECRET_KEY  → gerar com: python -c "import secrets; print(secrets.token_hex(32))"
   - ADMIN_EMAIL → seu email
   - ADMIN_PASSWORD → senha forte
   - DATABASE_URL → deixar sqlite:///data/fazenda167.db para começar
                     (mudar para Turso/PostgreSQL quando precisar de persistência)
5. Em "Settings → Domains" → gerar domínio público
   ex: porto-engenho.railway.app
6. Primeiro acesso: rodar create_admin.py via Railway Shell
```

### 11.6 Persistência no Railway (IMPORTANTE)

O Railway não persiste arquivos entre deploys por padrão. Para o SQLite funcionar em produção:

**Opção A — Volume persistente (Railway Pro, $5/mês):**
```
Railway → seu serviço → Settings → Add Volume
Mount Path: /app/data
```

**Opção B — Turso (SQLite distribuído, free tier):**
```bash
pip install libsql-client
# Mudar DATABASE_URL para:
# libsql://SEU_DB.turso.io?authToken=TOKEN
```
Turso é compatível com SQLite e tem free tier generoso.

**Opção C — PostgreSQL (Railway oferece grátis $5/mês):**
```bash
# Railway → Add Service → PostgreSQL
# Adaptar schema.sql para PostgreSQL (pequenas mudanças de sintaxe)
# DATABASE_URL=postgresql://...
```

---

## 12. CHECKLIST DE IMPLEMENTAÇÃO PARA CLAUDE CODE

Execute nesta ordem:

### Fase 1 — Setup base
- [ ] Criar estrutura de pastas conforme seção 3
- [ ] Criar `schema.sql` conforme seção 4
- [ ] Criar `requirements.txt`
- [ ] Criar `.gitignore` e `.env.example`
- [ ] Criar `app.py` base com Flask + init_db() + rotas estáticas
- [ ] Criar `create_admin.py`
- [ ] Criar `templates/base.html` com sidebar + topbar (identidade visual seção 2)
- [ ] Criar `templates/login.html`
- [ ] Testar login localmente

### Fase 2 — Importação
- [ ] Implementar `POST /api/importar` com toda a lógica XLS→DB (seção 5)
- [ ] Criar `templates/importar.html`
- [ ] Testar com os arquivos reais: `167_avg_matrizes.xls`, `167_avg_safra2023.xls`, `167_avg_safra2024.xls`
- [ ] Verificar contagens: 469 matrizes, 419 produtos

### Fase 3 — Dashboard
- [ ] Implementar `GET /api/dashboard/kpis`
- [ ] Implementar `GET /api/dashboard/safras`
- [ ] Criar `templates/dashboard.html` com Chart.js
- [ ] KPI cards + gráfico de barras + top 10 + alertas + distribuição deca

### Fase 4 — Ficha do Animal
- [ ] Implementar `GET /api/dropdown`
- [ ] Implementar `GET /api/animal/<id>`
- [ ] Implementar `GET /api/animal/<id>/historico`
- [ ] Criar `templates/ficha.html` com todos os componentes (seção 7.6)
- [ ] Testar com P16702071515 (top ICIAGen, tem 1 filho: M167 K 06123)
- [ ] Testar link genealogia → mãe no rebanho (125 mães identificadas)

### Fase 5 — Demais páginas
- [ ] `GET /api/matrizes` com filtros e paginação
- [ ] `templates/matrizes.html` com tabela + filtros + exportar CSV
- [ ] `templates/indices.html` com descrições + gráficos de evolução
- [ ] `templates/posicao.html` com barras de posicionamento + radar
- [ ] `templates/touros.html` com contribuição genética

### Fase 6 — Deploy
- [ ] Criar repositório GitHub privado
- [ ] Push do código (sem .env e sem .db)
- [ ] Deploy no Railway.app
- [ ] Configurar variáveis de ambiente
- [ ] Configurar volume persistente para o SQLite
- [ ] Rodar `create_admin.py` via Railway Shell
- [ ] Reimportar dados (rodar importação com os XLS via interface web)
- [ ] Testar domínio público

---

## 13. DADOS DE TESTE

### Animal rico para testar a ficha:
- **P16702071515**: ICIAGen 22,32 · Deca 1 · CEIP · Multípara · Pai: LITIO (AJ)
  - Nascimento: 23/10/2015 · IPP: 27,5m · PV: 489kg
  - **1 filho:** M167 K 06123 · ♂ · 18/08/2023 · ICIAGen 18,48

### Animal com mãe no rebanho (testar link genealogia):
- **M167 D 21619**: mãe = P16701441515 (também está no rebanho)
- Clicar na mãe deve abrir a ficha dela

### Estatísticas gerais (para validar importação):
```
matrizes:  469 registros
avaliacoes: 469 registros (1 rodada)
produtos:   419 registros

ICIAGen: min=-10.26  max=22.32  média=6.26
IDESM:   min=-9.70   max=28.16  média=7.23
IPP:     min=21.1    max=49.1   média=28.58  n=338
RMat:    min=0.21    max=23.93  média=13.28  n=405

CEIP: 267 matrizes (56,9%)
Genotipadas: 405 (86,4%)
Precoces: 266 (56,7%)

Categorias: M=214, N=116, P=87, S=52
Decas: 1=187, 2=140, 3=65, 4=37, 5=27, 6=7, 7=2, 8=2, 9=2

Top 10 contribuição genética (touros):
1. PAINT NITRO    11,61%
2. ASSIS (AJ)      9,11%
3. LITIO (AJ)      8,96%
4. DIAMANTE (CFM)  4,54%
5. DAKOTA AJ       4,46%
6. PAINT PHANTON   3,53%
7. BACKUP (CFM)    3,37%
8. ORFF AJ         3,25%
9. KULAL (AJ)      2,68%
10. GANGES COL      2,32%
Total acumulado: 53,83%
```

---

## 14. NOTAS TÉCNICAS IMPORTANTES

### Tratamento de espaços nos IDs
Os IDs de animais contêm espaços (ex: `M167 D 21619`). Na URL, usar `replace(' ', '__')` no frontend e `replace('__', ' ')` no backend.

### Colunas duplicadas no XLS
O arquivo `167_avg_matrizes.xls` tem duas colunas chamadas `IPP` (índices 31 e 72) e `PV` (índices 32 e 87). Usar o **segundo valor** (índices 72 e 87) que representa os valores reais observados.

### Safra 2024 tem menos colunas
O arquivo `167_avg_safra2024.xls` tem apenas 68 colunas vs 164 do 2023. O importador deve usar `.get()` com fallback `None` para todos os campos.

### perc_ICiaGen já vem como decimal
O campo `perc_ICiaGen` no XLS é um decimal (ex: 0.04 = 4%). Multiplicar por 100 ao salvar no banco para mostrar como percentual.

### Banco em produção
Para o Railway, configurar volume persistente ANTES de fazer o primeiro import de dados. Se o volume não estiver configurado, os dados são perdidos a cada redeploy.

---

## 15. COMANDOS ÚTEIS

```bash
# Desenvolvimento local
python app.py

# Criar admin
python create_admin.py

# Verificar banco
sqlite3 data/fazenda167.db ".tables"
sqlite3 data/fazenda167.db "SELECT COUNT(*) FROM matrizes;"

# Verificar top 5 matrizes
sqlite3 data/fazenda167.db "
  SELECT m.animal_id, a.iciagen, a.deca_icia_g, m.ceip
  FROM matrizes m JOIN avaliacoes a ON a.animal_id=m.animal_id
  ORDER BY a.iciagen DESC LIMIT 5;
"

# Verificar filhos de uma matriz
sqlite3 data/fazenda167.db "
  SELECT produto_id, sexo, iciagen, idesm
  FROM produtos WHERE mae_id='P16702071515';
"

# Deploy Railway
railway up

# Ver logs Railway
railway logs

# Acessar shell Railway (para rodar create_admin.py)
railway run python create_admin.py
```

---

*Documento gerado em: Março 2026*
*Sistema: Porto do Engenho – Gestão Genética*
*CIA de Melhoramento – Fazenda 167 – Cariacica/ES*
