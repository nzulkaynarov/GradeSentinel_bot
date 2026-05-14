"""Smoke-тест /health — гарантия что FastAPI app поднимается и OpenAPI собирается."""
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "gradesentinel-api"
    assert "version" in data


def test_openapi_schema_available():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "GradeSentinel API"
    assert "/health" in schema["paths"]
