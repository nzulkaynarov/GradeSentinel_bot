"""Health endpoint — для Caddy/мониторинга и smoke-теста OpenAPI."""
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    from api import __version__

    return HealthResponse(status="ok", service="gradesentinel-api", version=__version__)
