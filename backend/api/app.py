"""Settlement Detective HTTP API.

A service layer over modules that already exist. It owns no financial logic:
every number it serves was produced by the reconciliation engine, the
classifier, the investigator or the audit trail, and is read back rather than
recomputed.

Interactive documentation: http://localhost:8000/docs
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routers import exceptions, investigate, metrics, runs

app = FastAPI(
    title="Settlement Detective",
    description=(
        "AI settlement exception investigator. Reconciliation is deterministic; "
        "the AI investigates only what rules cannot close, and every conclusion "
        "is backed by verified evidence and a hash-chained audit trail."
    ),
    version="0.12.0",
)

# The Phase 13 dashboard runs on a different origin in development. No
# credentials are shared - the API holds no session and issues no cookie, and
# every secret stays server-side.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(runs.router, prefix="/api")
app.include_router(exceptions.router, prefix="/api")
app.include_router(investigate.router, prefix="/api")
app.include_router(metrics.router, prefix="/api")


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "name": "Settlement Detective",
        "docs": "/docs",
        "health": "/api/health",
    }
