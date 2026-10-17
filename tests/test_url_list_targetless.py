import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.urls import list_urls
from database.database import Base
from database.models import Target, Url


def call_list_urls(db):
    return list_urls(
        target_id=None, q=None, search=None, search_field="all", regex=False,
        status=None, source=None, interesting=None, pinned_only=False,
        technology=None, hostname=None, sort_by="newest", page=1, limit=50, db=db,
    )


def test_url_list_without_target_includes_legacy_and_targetless_records():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        target = Target(name="Legacy", root_domain="legacy.example")
        db.add(target)
        db.flush()
        db.add_all([
            Url(
                target_id=target.id, original_url="https://legacy.example/a",
                normalized_url="https://legacy.example/a", scheme="https",
                hostname="legacy.example", path="/a", is_pinned=True,
            ),
            Url(
                target_id=None, original_url="http://other.example/b",
                normalized_url="http://other.example/b", scheme="http",
                hostname="other.example", path="/b",
            ),
        ])
        db.commit()

        response = call_list_urls(db)
        payload = json.loads(response.body)
        assert response.status_code == 200
        assert payload["success"] is True
        assert payload["data"]["pagination"]["total"] == 2
        assert {item["hostname"] for item in payload["data"]["items"]} == {
            "legacy.example", "other.example",
        }
        legacy_item = next(item for item in payload["data"]["items"] if item["hostname"] == "legacy.example")
        assert legacy_item["is_pinned"] is True
    finally:
        db.close()
        engine.dispose()


def test_url_list_empty_database_returns_empty_page_not_error():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        payload = json.loads(call_list_urls(db).body)
        assert payload["success"] is True
        assert payload["data"]["items"] == []
        assert payload["data"]["pagination"]["total"] == 0
    finally:
        db.close()
        engine.dispose()
