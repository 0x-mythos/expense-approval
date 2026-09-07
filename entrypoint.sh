#!/bin/sh
set -e

echo "==> Applying database migrations"
alembic upgrade head

echo "==> Seeding demo data (idempotent)"
python -m app.seed

echo "==> Starting Expense Approval on :8000"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
