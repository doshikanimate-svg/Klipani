from fastapi.testclient import TestClient
from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        payload = client.get("/api/health").json()
        assert payload["status"] == "ok"
        assert "whisper" in payload
        assert "llm_provider" in payload
