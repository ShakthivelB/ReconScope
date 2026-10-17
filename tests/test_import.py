import pytest
import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from database.database import Base
from database.models import ReconImport, Target, Url
from api.imports import process_urls_ingestion, upload_import_files


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_import_and_deduplication(test_db):
    # Setup target
    target = Target(name="Test Target", root_domain="test.com")
    test_db.add(target)
    test_db.commit()

    raw_urls = [
        "https://test.com/login",
        "https://test.com/login/",
        "http://test.com/api?b=2&a=1",
        "http://test.com/api?a=1&b=2",
        "https://sub.test.com/index",
    ]

    res = process_urls_ingestion(
        db=test_db,
        target_id=target.id,
        raw_urls=raw_urls,
        source_type="GAU",
        filename="gau.txt",
    )

    assert res["raw_count"] == 5
    assert res["new_count"] == 3  # login (deduped), api (deduped), sub.test.com
    assert res["duplicate_count"] == 2

    # Second import with new source
    res2 = process_urls_ingestion(
        db=test_db,
        target_id=target.id,
        raw_urls=["https://test.com/login", "https://new.test.com/test"],
        source_type="Katana",
        filename="katana.txt",
    )

    assert res2["raw_count"] == 2
    assert res2["new_count"] == 1
    assert res2["duplicate_count"] == 1

    login_url = test_db.query(Url).filter(Url.normalized_url == "https://test.com/login").first()
    assert login_url is not None
    assert "GAU" in login_url.sources
    assert "Katana" in login_url.sources


def test_import_first_accepts_multiple_domains_without_target(test_db):
    raw_urls = [
        "https://example.com/",
        "https://api.example.org/v1/users",
        "https://cdn.example.net/assets/app.js",
    ]
    result = process_urls_ingestion(
        db=test_db,
        target_id=None,
        raw_urls=raw_urls,
        source_type="Manual",
        filename="mixed-domains.txt",
    )
    assert result["new_count"] == 3
    imported = test_db.query(Url).filter(Url.target_id.is_(None)).all()
    assert {url.hostname for url in imported} == {"example.com", "api.example.org", "cdn.example.net"}

    repeated = process_urls_ingestion(
        db=test_db,
        target_id=None,
        raw_urls=[raw_urls[0]],
        source_type="Katana",
        filename="repeat.txt",
    )
    assert repeated["new_count"] == 0
    assert repeated["duplicate_count"] == 1
    first_url = test_db.query(Url).filter(Url.normalized_url == raw_urls[0]).first()
    assert first_url.target_id is None
    assert first_url.sources == "Manual, Katana"


def test_targetless_import_deduplicates_against_legacy_target_urls(test_db):
    target = Target(name="Legacy", root_domain="example.com")
    test_db.add(target)
    test_db.commit()
    first = process_urls_ingestion(
        db=test_db,
        target_id=target.id,
        raw_urls=["https://example.com/login"],
        source_type="GAU",
    )
    assert first["new_count"] == 1

    second = process_urls_ingestion(
        db=test_db,
        target_id=None,
        raw_urls=["https://example.com/login"],
        source_type="Manual",
    )
    assert second["new_count"] == 0
    assert second["duplicate_count"] == 1
    matches = test_db.query(Url).filter(Url.normalized_url == "https://example.com/login").all()
    assert len(matches) == 1
    assert matches[0].target_id == target.id
    assert matches[0].sources == "GAU, Manual"


def test_import_api_accepts_urls_without_a_target(test_db):
    response = asyncio.run(upload_import_files(
        target_id=None,
        source_type="Manual",
        raw_text="https://example.com/\nhttps://api.example.org/v1/users",
        files=None,
        db=test_db,
    ))
    assert response.status_code == 201
    imported = test_db.query(Url).filter(Url.target_id.is_(None)).all()
    assert {url.hostname for url in imported} == {"example.com", "api.example.org"}
    record = test_db.query(ReconImport).one()
    assert record.target_id is None
    assert record.filename == "pasted_urls.txt"
