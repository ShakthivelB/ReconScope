import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.dashboard import get_dashboard_stats
from database.database import Base
from database.models import HttpResult, Technology, Url, UrlTechnology


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_dashboard_stats_include_targetless_urls_and_actual_probe_data():
    engine, db = make_session()
    try:
        ok_url = Url(
            target_id=None, original_url="https://example.org/", normalized_url="https://example.org/",
            scheme="https", hostname="example.org", sources="GAU, Manual", is_interesting=True,
        )
        timeout_url = Url(
            target_id=None, original_url="http://api.example.net/", normalized_url="http://api.example.net/",
            scheme="http", hostname="api.example.net", sources="Katana",
        )
        db.add_all([ok_url, timeout_url])
        db.flush()
        db.add_all([
            HttpResult(url_id=ok_url.id, status_code=200, response_time_ms=25),
            HttpResult(url_id=timeout_url.id, status_code=None, error_message="Timeout"),
        ])
        technology = Technology(name="Fixture Tech", category="Test")
        db.add(technology)
        db.flush()
        db.add(UrlTechnology(url_id=ok_url.id, technology_id=technology.id, confidence="high"))
        db.commit()

        response = get_dashboard_stats(None, db)
        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["success"] is True
        metrics = payload["data"]["metrics"]
        assert metrics["total_urls"] == 2
        assert metrics["checked"] == 2
        assert metrics["not_checked"] == 0
        assert metrics["status_200"] == 1
        assert metrics["timeouts"] == 1
        assert metrics["interesting"] == 1
        assert metrics["subdomains"] == 2
        assert metrics["technologies"] == 1
        charts = payload["data"]["charts"]
        assert charts["sources"] == {"GAU": 1, "Manual": 1, "Katana": 1}
        assert charts["technologies"] == [{"name": "Fixture Tech", "count": 1}]
    finally:
        db.close()
        engine.dispose()


def test_dashboard_stats_empty_database_returns_real_empty_counts():
    engine, db = make_session()
    try:
        payload = json.loads(get_dashboard_stats(None, db).body)
        assert payload["data"]["metrics"]["total_urls"] == 0
        assert payload["data"]["metrics"]["checked"] == 0
        assert payload["data"]["charts"]["sources"] == {}
        assert payload["data"]["charts"]["technologies"] == []
    finally:
        db.close()
        engine.dispose()
