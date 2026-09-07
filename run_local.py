"""Run the app locally WITHOUT Docker (SQLite instead of Postgres).

Usage:
    python -m venv .venv
    .venv\\Scripts\\pip install -r requirements.txt      # Windows
    .venv\\Scripts\\python run_local.py
    # then open http://127.0.0.1:8000  (login: bob@example.com / demo1234)

Postgres/Alembic is the real setup (see docker-compose.yml); this script is a
zero-infra convenience for a quick look. It uses SQLite + create_all + seed.
Set ANTHROPIC_API_KEY in your environment to enable the AI panel.
"""
import os
import sys

PROJ = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault(
    "DATABASE_URL", "sqlite+pysqlite:///" + os.path.join(PROJ, "local.db").replace("\\", "/")
)
os.environ.setdefault("SECRET_KEY", "local-dev-secret")

sys.path.insert(0, PROJ)
os.chdir(PROJ)

from app.database import Base, engine  # noqa: E402
import app.models  # noqa: E402,F401

Base.metadata.create_all(engine)

from app.seed import run as seed  # noqa: E402
seed()

import uvicorn  # noqa: E402

uvicorn.run("app.main:app", host=os.environ.get("HOST", "127.0.0.1"), port=8000)
