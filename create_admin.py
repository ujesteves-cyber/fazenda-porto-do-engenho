"""
Bootstrap manual do usuário master.

Pode ser executado manualmente (ex.: via Render Shell) para criar ou
atualizar o usuário master a partir das variáveis ADMIN_EMAIL / ADMIN_PASSWORD.

Nota: o init_db() do app.py já faz esse bootstrap automaticamente no primeiro
boot quando o DATA_DIR está montado e gravável. Este script é apenas um
utilitário de fallback.

Em ambientes onde o DATA_DIR ainda não está disponível (ex.: durante o build
do Render, antes do Disk ser montado), o script sai com código 0 e uma
mensagem, sem interromper a pipeline.
"""
import os
import sys
import sqlite3

import bcrypt
from dotenv import load_dotenv

load_dotenv()

email = os.getenv('ADMIN_EMAIL', 'admin@portodoengenho.com.br')
senha = os.getenv('ADMIN_PASSWORD', 'admin123').encode()

data_dir = os.getenv('DATA_DIR') or os.path.join(os.path.dirname(__file__), 'data')
db_path = os.path.join(data_dir, 'fazenda167.db')

try:
    os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
except (OSError, sqlite3.OperationalError) as e:
    print(f"create_admin.py: pulando — DATA_DIR ainda não está gravável ({e}).")
    print("O usuário master será criado automaticamente pelo init_db() no primeiro boot.")
    sys.exit(0)

conn.execute("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        senha_hash TEXT NOT NULL,
        ativo INTEGER DEFAULT 1,
        papel TEXT NOT NULL DEFAULT 'usuario',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")
cols = [r[1] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
if 'papel' not in cols:
    conn.execute("ALTER TABLE usuarios ADD COLUMN papel TEXT NOT NULL DEFAULT 'usuario'")

hash_ = bcrypt.hashpw(senha, bcrypt.gensalt()).decode()
conn.execute(
    "INSERT OR IGNORE INTO usuarios (nome, email, senha_hash, papel) VALUES (?, ?, ?, 'master')",
    ("Dr. Anselmo", email, hash_)
)
conn.execute("UPDATE usuarios SET papel='master' WHERE lower(email)=lower(?)", (email,))
conn.commit()
conn.close()
print(f"Admin master criado/atualizado: {email}")
