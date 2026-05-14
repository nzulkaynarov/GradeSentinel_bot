"""FastAPI entrypoint.

Запуск локально:
    uvicorn api.main:app --reload --port 8444

В проде (Phase 2+):
    /etc/systemd/system/gradesentinel-api.service
    ExecStart: uvicorn api.main:app --host 127.0.0.1 --port 8444 --workers 2

Только loopback. Наружу — через Caddy на api.grades.railtech.uz.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import __version__
from api.routes import health


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 2+: подключим БД-pool, прогрев кэшей, валидацию миграций
    yield


app = FastAPI(
    title="GradeSentinel API",
    version=__version__,
    description="REST API для веб-портала родителей и админ-панели",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

# CORS — для локальной разработки Next.js на :3000.
# В проде origin будет один (app.grades.railtech.uz / admin.grades.railtech.uz) через Caddy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://app.grades.railtech.uz",
        "https://admin.grades.railtech.uz",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(health.router)
