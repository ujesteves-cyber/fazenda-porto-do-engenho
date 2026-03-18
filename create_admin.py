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
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
""")

conn.execute(
    "INSERT OR IGNORE INTO usuarios (nome, email, senha_hash) VALUES (?, ?, ?)",
    ("Administrador", email, hash_)
)
conn.commit()
conn.close()
print(f"Admin criado: {email}")
