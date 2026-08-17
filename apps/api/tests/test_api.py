from uuid import UUID

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


def test_health_and_demo_search():
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        response = client.get("/api/v1/gyms")
        assert response.status_code == 200
        assert response.json()["demo_mode"] is True
        assert len(response.json()["items"]) == 3


def test_search_filter_and_detail():
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        response = client.get("/api/v1/gyms", params={"query": "Mission", "max_monthly": 100})
        assert response.status_code == 200
        assert [item["name"] for item in response.json()["items"]] == ["Mission Strength Co."]

        detail = client.get("/api/v1/gyms/11111111-1111-4111-8111-111111111111")
        assert detail.status_code == 200
        assert detail.json()["prices"][0]["plan_type"] == "monthly"


def test_commands_require_idempotency_key():
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        payload = {"gym_location_id": str(UUID("11111111-1111-4111-8111-111111111111")), "intent": "tour"}
        assert client.post("/api/v1/leads", json=payload).status_code == 400
        accepted = client.post("/api/v1/leads", json=payload, headers={"Idempotency-Key": "lead-1"})
        assert accepted.status_code == 202
