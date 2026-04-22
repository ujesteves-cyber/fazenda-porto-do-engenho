import bcrypt
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()

email = os.getenv('ADMIN_EMAIL', 'admin@portodoengenho.com.br')
senha = os.getenv('ADMIN_PASSWORD', 'admin123').encode()
hash_ = bcrypt.hashpw(senha, bcrypt.gensalt()).decode()

db_path = os.path.join(os.path.dirname(__file__), 'data', 'fazenda167.db')
os.makedirs(os.path.dirname(db_path), exist_ok=True)

conn = sqlite3.connect(db_path)

# Ensure table exists
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

# Migração para bancos já existentes
cols = [r[1] for r in conn.execute("PRAGMA table_info(usuarios)").fetchall()]
if 'papel' not in cols:
    conn.execute("ALTER TABLE usuarios ADD COLUMN papel TEXT NOT NULL DEFAULT 'usuario'")

conn.execute(
    "INSERT OR IGNORE INTO usuarios (nome, email, senha_hash, papel) VALUES (?, ?, ?, 'master')",
    ("Dr. Anselmo", email, hash_)
)
# Garante que o admin seja master
conn.execute("UPDATE usuarios SET papel='master' WHERE lower(email)=lower(?)", (email,))
conn.commit()
conn.close()
print(f"Admin master criado/atualizado: {email}")
