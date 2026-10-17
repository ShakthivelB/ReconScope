import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import BackgroundTasks

from database.database import Base
from database.models import ImportUrl, ReconImport, ScanJob, ScanJobUrl, ScanResult, Target, Url
from api.scans import ScanCreateSchema, start_scan
from api.imports import delete_import
from services import http_probe


def test_scan_worker_processes_targetless_urls_from_multiple_domains(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        legacy_target = Target(name="Legacy domain", root_domain="example.com", scope_rules="!api.example.org")
        source = ReconImport(filename="discovery.txt", source_type="Manual")
        db.add_all([legacy_target, source])
        db.flush()
        active_url_1 = Url(target_id=None, original_url="http://example.com", normalized_url="http://example.com/", scheme="http", hostname="example.com")
        orphan_url = Url(target_id=None, original_url="https://api.example.org", normalized_url="https://api.example.org/", scheme="https", hostname="api.example.org")
        active_url_2 = Url(target_id=legacy_target.id, original_url="https://unrelated.example.net", normalized_url="https://unrelated.example.net/", scheme="https", hostname="unrelated.example.net")
        db.add_all([
            active_url_1, orphan_url, active_url_2,
        ])
        db.flush()
        db.add_all([
            ImportUrl(import_id=source.id, url_id=active_url_1.id),
            ImportUrl(import_id=source.id, url_id=active_url_2.id),
        ])
        scan = ScanJob(target_id=None, status="queued", total_urls=2)
        db.add(scan)
        db.flush()
        db.add_all([
            ScanJobUrl(scan_job_id=scan.id, url_id=active_url_1.id),
            ScanJobUrl(scan_job_id=scan.id, url_id=active_url_2.id),
        ])
        db.commit()
        scan_id = scan.id

    monkeypatch.setattr(http_probe, "SessionLocal", sessions)
    scanned_urls = []
    probe_options = []

    async def fake_probe(url, **kwargs):
        scanned_urls.append(url)
        probe_options.append(kwargs)
        if len(scanned_urls) == 1:
            raise RuntimeError("fixture connection failure")
        return {
            "status_code": 200,
            "error": None,
            "response_time_ms": 1.0,
            "response_size_bytes": 0,
            "content_type": "text/html",
            "server": None,
            "http_version": "HTTP/1.1",
            "final_url": url,
            "redirect_chain": [],
            "title": None,
            "language": None,
            "description": None,
            "powered_by": None,
            "headers": {},
            "technologies": [],
        }

    monkeypatch.setattr(http_probe, "probe_url", fake_probe)
    asyncio.run(http_probe.run_scan_job(scan_id, concurrency=1))

    with sessions() as db:
        scan = db.query(ScanJob).filter(ScanJob.id == scan_id).one()
        assert scan.status == "completed"
        assert scan.completed_urls == 2
        assert scan.status_2xx == 1
        assert scan.errors == 1
        assert db.query(ScanResult).filter(ScanResult.scan_job_id == scan_id).count() == 2
    assert set(scanned_urls) == {
        "http://example.com/", "https://unrelated.example.net/",
    }
    assert all(options["enforce_scope"] is False for options in probe_options)
    engine.dispose()


def test_scan_endpoint_starts_global_scan_without_a_target():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        legacy_target = Target(name="Legacy", root_domain="legacy.example")
        source = ReconImport(filename="discovery.txt", source_type="Manual")
        db.add_all([legacy_target, source])
        db.flush()
        urls = [
            Url(target_id=None, original_url="https://example.com", normalized_url="https://example.com/", scheme="https", hostname="example.com"),
            Url(target_id=None, original_url="https://api.example.org", normalized_url="https://api.example.org/", scheme="https", hostname="api.example.org"),
            Url(target_id=legacy_target.id, original_url="http://legacy.example/path", normalized_url="http://legacy.example/path", scheme="http", hostname="legacy.example", path="/path"),
        ]
        db.add_all(urls)
        db.flush()
        db.add_all(ImportUrl(import_id=source.id, url_id=url.id) for url in urls[:2])
        db.commit()
        tasks = BackgroundTasks()
        response = start_scan(ScanCreateSchema(target_id=None), tasks, db)
        body = json.loads(response.body)
        assert response.status_code == 202
        assert body["data"]["target_id"] is None
        assert body["data"]["total_urls"] == 2
        assert len(tasks.tasks) == 1
        scan = db.query(ScanJob).one()
        assert db.query(ScanJobUrl).filter(ScanJobUrl.scan_job_id == scan.id).count() == 2
    engine.dispose()


def test_worker_skips_a_deleted_url_already_queued_in_memory(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        source_a = ReconImport(filename="source-a.txt", source_type="Manual")
        source_b = ReconImport(filename="source-b.txt", source_type="Manual")
        db.add_all([source_a, source_b])
        db.flush()
        url_b = Url(target_id=None, original_url="https://b.test", normalized_url="https://b.test/",
                    scheme="https", hostname="b.test")
        url_a = Url(target_id=None, original_url="https://a.test", normalized_url="https://a.test/",
                    scheme="https", hostname="a.test")
        db.add_all([url_b, url_a])
        db.flush()
        db.add_all([
            ImportUrl(import_id=source_a.id, url_id=url_a.id),
            ImportUrl(import_id=source_b.id, url_id=url_b.id),
        ])
        source_a_id = source_a.id
        tasks = BackgroundTasks()
        response = start_scan(ScanCreateSchema(target_id=None), tasks, db)
        scan_id = json.loads(response.body)["data"]["id"]
        assert json.loads(response.body)["data"]["total_urls"] == 2

    monkeypatch.setattr(http_probe, "SessionLocal", sessions)
    probed = []
    deletion_results = []

    async def fake_probe(url, **kwargs):
        probed.append(url)
        if url == "https://b.test/":
            with sessions() as deletion_db:
                deletion_results.append(delete_import(source_a_id, deletion_db).status_code)
        return {
            "status_code": 200, "error": None, "response_time_ms": 1.0,
            "response_size_bytes": 0, "content_type": "text/html", "server": None,
            "http_version": "HTTP/1.1", "final_url": url, "redirect_chain": [],
            "title": None, "language": None, "description": None, "powered_by": None,
            "headers": {}, "technologies": [],
        }

    monkeypatch.setattr(http_probe, "probe_url", fake_probe)
    asyncio.run(http_probe.run_scan_job(scan_id, concurrency=1))

    with sessions() as db:
        scan = db.query(ScanJob).filter(ScanJob.id == scan_id).one()
        assert scan.status == "completed"
        assert scan.total_urls == 1
        assert scan.completed_urls == 1
        assert {row.url_id for row in db.query(ScanResult).filter(ScanResult.scan_job_id == scan_id)} == {
            db.query(Url.id).filter(Url.hostname == "b.test").scalar()
        }
        assert db.query(Url).filter(Url.hostname == "a.test").count() == 0
    assert deletion_results == [200]
    assert probed == ["https://b.test/"]
    engine.dispose()
