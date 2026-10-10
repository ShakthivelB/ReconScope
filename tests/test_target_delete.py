import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.targets import delete_target
from database.database import Base
from database.models import HttpResult, ReconImport, ScanJob, ScanResult, Target, Url


def test_delete_target_removes_target_and_dependent_records():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autoflush=False, bind=engine)()
    try:
        target = Target(name="Test target", root_domain="example.test")
        db.add(target)
        db.flush()

        url = Url(
            target_id=target.id,
            original_url="https://example.test/",
            normalized_url="https://example.test/",
            scheme="https",
            hostname="example.test",
            path="/",
        )
        db.add(url)
        db.flush()
        db.add(HttpResult(url_id=url.id, status_code=200))
        db.add(ReconImport(target_id=target.id, filename="fixture.txt", source_type="Manual"))

        scan = ScanJob(target_id=target.id)
        db.add(scan)
        db.flush()
        db.add(ScanResult(scan_job_id=scan.id, url_id=url.id))
        db.commit()

        response = delete_target(target.id, db)
        assert response.status_code == 200
        assert json.loads(response.body)["data"]["deleted_id"] == target.id
        for model in (Target, Url, HttpResult, ReconImport, ScanJob, ScanResult):
            assert db.query(model).count() == 0
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
