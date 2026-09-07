"""FastAPI application entrypoint."""
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.routers import approvals, assistant, auth, claims, reports

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Expense Approval")
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Order matters: literal /claims/new (claims) before /claims/{id} (approvals).
app.include_router(auth.router)
app.include_router(claims.router)
app.include_router(reports.router)
app.include_router(approvals.router)
app.include_router(assistant.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
