import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import BackgroundTasks

from database.database import Base
from database.models import ScanJob, ScanResult, Target, Url
from api.scans import ScanCreateSchema, start_scan
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
        db.add(legacy_target)
        db.flush()
        db.add_all([
            Url(target_id=None, original_url="http://example.com", normalized_url="http://example.com/", scheme="http", hostname="example.com"),
            Url(target_id=None, original_url="https://api.example.org", normalized_url="https://api.example.org/", scheme="https", hostname="api.example.org"),
            Url(target_id=legacy_target.id, original_url="https://unrelated.example.net", normalized_url="https://unrelated.example.net/", scheme="https", hostname="unrelated.example.net"),
        ])
        scan = ScanJob(target_id=None, status="queued", total_urls=3)
        db.add(scan)
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
        assert scan.completed_urls == 3
        assert scan.status_2xx == 2
        assert scan.errors == 1
        assert db.query(ScanResult).filter(ScanResult.scan_job_id == scan_id).count() == 3
    assert set(scanned_urls) == {
        "http://example.com/", "https://api.example.org/", "https://unrelated.example.net/",
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
        db.add(legacy_target)
        db.flush()
        db.add_all([
            Url(target_id=None, original_url="https://example.com", normalized_url="https://example.com/", scheme="https", hostname="example.com"),
            Url(target_id=None, original_url="https://api.example.org", normalized_url="https://api.example.org/", scheme="https", hostname="api.example.org"),
            Url(target_id=legacy_target.id, original_url="http://legacy.example/path", normalized_url="http://legacy.example/path", scheme="http", hostname="legacy.example", path="/path"),
        ])
        db.commit()
        tasks = BackgroundTasks()
        response = start_scan(ScanCreateSchema(target_id=None), tasks, db)
        body = json.loads(response.body)
        assert response.status_code == 202
        assert body["data"]["target_id"] is None
        assert body["data"]["total_urls"] == 3
        assert len(tasks.tasks) == 1
    engine.dispose()
