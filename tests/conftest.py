import os
import sys
from pathlib import Path

# Ensure tests can import app without tripping the production SECRET_KEY guard
os.environ.setdefault("FLASK_ENV", "development")

# Make project root importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
