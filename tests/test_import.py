import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from database.database import Base
from database.models import Target, Url
from api.imports import process_urls_ingestion


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
    target = Target(name="Test Target", root_domain="test.com", scope_rules="*.test.com")
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
