from datetime import date, timedelta
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


def test_filter_by_multiple_venue_types():
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/gyms",
            params=[("venue_type", "boutique_fitness"), ("venue_type", "recreation_sports")],
        )
        assert response.status_code == 200
        assert {item["venue_type"] for item in response.json()["items"]} == {
            "boutique_fitness",
            "recreation_sports",
        }


def test_commands_require_idempotency_key():
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        payload = {"gym_location_id": str(UUID("11111111-1111-4111-8111-111111111111")), "intent": "tour"}
        assert client.post("/api/v1/leads", json=payload).status_code == 400
        accepted = client.post("/api/v1/leads", json=payload, headers={"Idempotency-Key": "lead-1"})
        assert accepted.status_code == 202


def test_demo_experience_reports_are_public_but_explicitly_demo_data():
    app = create_app(Settings(demo_mode=True))
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/gyms/11111111-1111-4111-8111-111111111111/experience-reports"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["demo_mode"] is True
        assert body["items"][0]["relationship"] == "day_pass"
        assert "Demo observation" in body["items"][0]["body"]


def test_experience_submission_is_pending_and_idempotent():
    app = create_app(Settings(demo_mode=True))
    payload = {
        "visit_date": "2026-08-12",
        "time_bucket": "evening",
        "relationship": "member",
        "crowding": "busy",
        "equipment_availability": "short_wait",
        "body": "The free-weight area was busiest after work.",
    }
    path = "/api/v1/gyms/11111111-1111-4111-8111-111111111111/experience-reports"
    with TestClient(app) as client:
        assert client.post(path, json=payload).status_code == 400
        created = client.post(path, json=payload, headers={"Idempotency-Key": "experience-1"})
        assert created.status_code == 201
        assert created.json()["status"] == "pending"
        assert created.json()["already_processed"] is False

        replayed = client.post(path, json=payload, headers={"Idempotency-Key": "experience-1"})
        assert replayed.status_code == 200
        assert replayed.json()["id"] == created.json()["id"]
        assert replayed.json()["already_processed"] is True

        changed = {**payload, "crowding": "packed"}
        conflict = client.post(path, json=changed, headers={"Idempotency-Key": "experience-1"})
        assert conflict.status_code == 409


def test_experience_submission_requires_firsthand_content_and_past_date():
    app = create_app(Settings(demo_mode=True))
    path = "/api/v1/gyms/11111111-1111-4111-8111-111111111111/experience-reports"
    with TestClient(app) as client:
        empty = client.post(
            path,
            json={"visit_date": "2026-08-12", "relationship": "guest"},
            headers={"Idempotency-Key": "empty-experience"},
        )
        assert empty.status_code == 422

        future = client.post(
            path,
            json={
                "visit_date": (date.today() + timedelta(days=1)).isoformat(),
                "relationship": "guest",
                "cleanliness": "clean",
            },
            headers={"Idempotency-Key": "future-experience"},
        )
        assert future.status_code == 422
