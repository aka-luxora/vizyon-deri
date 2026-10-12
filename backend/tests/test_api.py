from fastapi.testclient import TestClient

from app.main import app
from test_engine import sample_request


client = TestClient(app)


def test_health():
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert "plan_count" in body


def test_calculate_endpoint():
    response = client.post("/api/v1/plans/calculate", json=sample_request().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["recommended_production"] == 30


def test_invalid_period_returns_422():
    payload = sample_request().model_dump(mode="json")
    payload["policy"]["reference_end"] = "2025-01-01"
    assert client.post("/api/v1/plans/calculate", json=payload).status_code == 422


def test_demo_portfolio_acceptance_totals():
    response = client.post("/api/v1/plans/demo", json={
        "target_start": "2026-11-01",
        "target_end": "2027-02-28",
        "growth_factor": 1.8,
        "product_types": ["Kürk", "Deri"],
        "statuses": ["S1"],
        "brands": ["VSP"],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["net_sales"] == 2000
    assert body["summary"]["forecast_demand"] == 3600
    assert body["summary"]["production_need"] == 1900
    assert sum(x["allocated_production"] for x in body["summary"]["depot_totals"]) == 1900


def test_managed_plan_lifecycle_and_audit():
    headers = {"x-user": "Test%20Planlamac%C4%B1", "x-role": "planner"}
    response = client.post("/api/v1/managed-plans/demo", headers=headers, json={"growth_factor": 1.8})
    assert response.status_code == 200
    plan = response.json()
    assert plan["status"] == "draft"
    assert plan["version"] >= 1
    assert not [check for check in plan["quality_checks"] if check["status"] == "fail"]

    sku = plan["result"]["lines"][0]["sku"]
    override = client.post(
        f"/api/v1/managed-plans/{plan['id']}/override", headers=headers,
        json={"sku": sku, "production_need": 222, "reason": "Test amaçlı kontrollü revizyon"},
    )
    assert override.status_code == 200
    assert override.json()["result"]["lines"][0]["production_need"] == 222
    assert any(event["action"] == "override" for event in override.json()["audit"])

    submitted = client.post(
        f"/api/v1/managed-plans/{plan['id']}/transition", headers=headers,
        json={"action": "submit", "note": "Kontrole hazır."},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "review"

    approved = client.post(
        f"/api/v1/managed-plans/{plan['id']}/transition",
        headers={"x-user": "Test%20Onayc%C4%B1", "x-role": "approver"},
        json={"action": "approve", "note": "Kontrol edildi."},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    published = client.post(
        f"/api/v1/managed-plans/{plan['id']}/transition",
        headers={"x-user": "Test%20Yay%C4%B1nc%C4%B1", "x-role": "publisher"},
        json={"action": "publish", "note": "Üretim kararına yayımlandı."},
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"


def test_role_permissions_are_enforced():
    response = client.post(
        "/api/v1/managed-plans/demo",
        headers={"x-user": "%C4%B0zleyici", "x-role": "viewer"},
        json={},
    )
    assert response.status_code == 403


def test_netsuite_status_is_safe_when_not_configured():
    body = client.get("/api/v1/integrations/netsuite/status").json()
    assert body["configured"] is False
    assert "NETSUITE_TOKEN_SECRET" in body["missing_settings"]
    assert "token_secret" not in body


def test_netsuite_connection_test_requires_admin_role():
    response = client.post(
        "/api/v1/integrations/netsuite/connection-test",
        headers={"x-user": "Planner", "x-role": "planner"},
    )
    assert response.status_code == 403


def test_netsuite_preview_reports_missing_configuration():
    response = client.post(
        "/api/v1/integrations/netsuite/preview",
        headers={"x-user": "Planner", "x-role": "planner"},
        json={
            "plan_name": "Test",
            "filters": {"product_types": ["Kürk"], "statuses": ["S1"], "brands": ["VSP"]},
            "policy": {"target_start": "2027-01-01", "target_end": "2027-01-31", "growth_factor": 1.8},
        },
    )
    assert response.status_code == 503
    assert "NETSUITE_ACCOUNT_ID" in response.json()["detail"]
