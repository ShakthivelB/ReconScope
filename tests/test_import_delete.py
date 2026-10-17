import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.imports import delete_import
from database.database import Base
from database.models import HttpResult, ReconImport, Url


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_delete_import_history_preserves_urls_results_pins_and_other_imports():
    engine, db = make_session()
    try:
        first_import = ReconImport(
            target_id=None, filename="gau-output.txt", source_type="GAU",
            raw_count=2, new_count=1, duplicate_count=1,
        )
        other_import = ReconImport(
            target_id=None, filename="katana-urls.txt", source_type="Katana",
            raw_count=1, new_count=1, duplicate_count=0,
        )
        url = Url(
            target_id=None,
            original_url="https://example.org/admin",
            normalized_url="https://example.org/admin",
            scheme="https",
            hostname="example.org",
            path="/admin",
            sources="GAU, Katana",
            is_pinned=True,
        )
        db.add_all([first_import, other_import, url])
        db.flush()
        result = HttpResult(url_id=url.id, status_code=200, response_time_ms=25.0)
        db.add(result)
        db.commit()
        first_id = first_import.id
        url_id = url.id
        result_id = result.id

        response = delete_import(first_id, db)
        assert response.status_code == 200
        assert json.loads(response.body)["data"]["urls_preserved"] is True
        assert db.query(ReconImport).count() == 1
        assert db.query(ReconImport).one().filename == "katana-urls.txt"
        preserved_url = db.query(Url).filter(Url.id == url_id).one()
        assert preserved_url.is_pinned is True
        assert preserved_url.sources == "GAU, Katana"
        assert db.query(HttpResult).filter(HttpResult.id == result_id).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_delete_missing_import_returns_not_found():
    engine, db = make_session()
    try:
        response = delete_import(999, db)
        assert response.status_code == 404
        assert json.loads(response.body)["error"]["code"] == "NOT_FOUND"
    finally:
        db.close()
