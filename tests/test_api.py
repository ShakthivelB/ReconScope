import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import app
from database.database import Base, get_db

# Use an in-memory SQLite database with StaticPool so all connections share the same memory DB
engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def test_api_targets_crud():
    # 1. Create Target
    resp = client.post("/api/targets", json={
        "name": "Integration Target",
        "root_domain": "integ.com",
        "notes": "Testing",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["success"] is True
    target_id = data["data"]["id"]

    # 2. List Targets
    resp = client.get("/api/targets")
    assert resp.status_code == 200
    targets = resp.json()["data"]
    assert any(t["id"] == target_id for t in targets)

    # 3. Get Single Target
    resp = client.get(f"/api/targets/{target_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["root_domain"] == "integ.com"


def test_api_import_and_url_query():
    # Create target
    resp = client.post("/api/targets", json={
        "name": "Import Target",
        "root_domain": "recon.test",
    })
    target_id = resp.json()["data"]["id"]

    # Ingest URLs via Form
    resp = client.post(
        "/api/imports",
        data={
            "target_id": target_id,
            "source_type": "Katana",
            "raw_text": "https://recon.test/api/v1\nhttps://recon.test/login\nhttps://other.test/bad",
        },
    )
    assert resp.status_code == 201
    import_data = resp.json()
    assert import_data["success"] is True
    assert import_data["data"]["summary"]["total_raw"] == 3

    # Query URLs
    resp = client.get(f"/api/urls?target_id={target_id}&q=/api/")
    assert resp.status_code == 200
    urls = resp.json()["data"]["items"]
    assert len(urls) == 1
    assert urls[0]["path"] == "/api/v1"


def test_api_pin_urls_and_filter_combinations():
    target_resp = client.post("/api/targets", json={
        "name": "Pinned URL Target",
        "root_domain": "pin.test",
    })
    target_id = target_resp.json()["data"]["id"]

    import_resp = client.post(
        "/api/imports",
        data={
            "target_id": target_id,
            "source_type": "Manual",
            "raw_text": "https://pin.test/admin\nhttps://pin.test/public\nhttps://pin.test/.env",
        },
    )
    assert import_resp.status_code == 201

    urls_resp = client.get(f"/api/urls?target_id={target_id}&limit=100")
    urls = urls_resp.json()["data"]["items"]
    by_path = {url["path"]: url for url in urls}
    admin_id = by_path["/admin"]["id"]
    env_id = by_path["/.env"]["id"]
    assert all(url["is_pinned"] is False for url in urls)

    pin_resp = client.put(f"/api/urls/{admin_id}/pin", json={"pinned": True})
    assert pin_resp.status_code == 200
    assert pin_resp.json()["data"]["is_pinned"] is True
    # Repeating the same request is safe and leaves the stored state unchanged.
    assert client.put(f"/api/urls/{admin_id}/pin", json={"pinned": True}).json()["data"]["is_pinned"] is True
    assert client.put(f"/api/urls/{env_id}/pin", json={"pinned": True}).status_code == 200

    filtered = client.get(f"/api/urls?target_id={target_id}&pinned_only=true&q=/admin&limit=1")
    filtered_data = filtered.json()["data"]
    assert [url["id"] for url in filtered_data["items"]] == [admin_id]
    assert filtered_data["pagination"]["total"] == 1
    assert filtered_data["items"][0]["is_pinned"] is True

    interesting_pins = client.get(
        f"/api/urls?target_id={target_id}&pinned_only=true&interesting=true&limit=100"
    ).json()["data"]["items"]
    assert {url["id"] for url in interesting_pins} == {admin_id, env_id}

    paged = client.get(f"/api/urls?target_id={target_id}&pinned_only=true&limit=1&page=2")
    assert paged.json()["data"]["pagination"]["total"] == 2
    assert paged.json()["data"]["pagination"]["page"] == 2

    unpin_resp = client.put(f"/api/urls/{admin_id}/pin", json={"pinned": False})
    assert unpin_resp.json()["data"]["is_pinned"] is False
    assert client.get(f"/api/urls?target_id={target_id}&pinned_only=true&q=/admin").json()["data"]["items"] == []
    assert client.put("/api/urls/999999/pin", json={"pinned": True}).status_code == 404


def test_api_dashboard_stats():
    resp = client.get("/api/dashboard/stats")
    assert resp.status_code == 200
    stats = resp.json()["data"]
    assert "metrics" in stats
    assert "charts" in stats


def test_html_pages():
    for page in ["/dashboard", "/targets", "/imports", "/urls", "/scans", "/technologies", "/settings"]:
        resp = client.get(page)
        assert resp.status_code == 200
        assert "<!DOCTYPE html>" in resp.text
        assert 'href="/targets"' not in resp.text
        assert 'id="global-target-select"' not in resp.text

    response = client.get("/targets", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/imports"


def test_import_page_has_no_target_selection():
    response = client.get("/imports")
    assert response.status_code == 200
    assert 'id="import-target-select"' not in response.text
    assert 'name="target_id"' not in response.text


def test_demo_seed_endpoint_is_removed():
    assert client.post("/api/demo/seed").status_code == 404
