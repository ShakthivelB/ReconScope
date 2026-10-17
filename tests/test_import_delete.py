import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.imports import delete_import, preview_import_deletion, process_urls_ingestion
import database.database as database_module
from database.database import Base
from database.models import HttpResult, ImportUrl, ReconImport, ScanJob, ScanJobUrl, ScanResult, Technology, Url, UrlTechnology


def make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_delete_legacy_import_preserves_untracked_urls_results_and_other_imports():
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
        # Legacy rows with no ownership link are cleaned by the one-time startup
        # migration; deleting an import record cannot guess their ownership.
        assert json.loads(response.body)["data"]["deleted_urls"] == 0
        assert db.query(ReconImport).count() == 1
        assert db.query(ReconImport).one().filename == "katana-urls.txt"
        preserved_url = db.query(Url).filter(Url.id == url_id).one()
        assert preserved_url.is_pinned is True
        assert preserved_url.sources == "GAU, Katana"
        assert db.query(HttpResult).filter(HttpResult.id == result_id).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_delete_source_removes_exclusive_url_dependents_but_preserves_shared_url():
    from api.dashboard import get_dashboard_stats, list_technologies
    from api.urls import list_subdomains, list_urls

    engine, db = make_session()
    try:
        source_a = ReconImport(filename="a.txt", source_type="GAU")
        source_b = ReconImport(filename="b.txt", source_type="Katana")
        db.add_all([source_a, source_b])
        db.flush()
        exclusive = Url(original_url="https://only.test", normalized_url="https://only.test", scheme="https",
                        hostname="only.test", path="/", import_tracking_complete=True)
        shared = Url(original_url="https://shared.test", normalized_url="https://shared.test", scheme="https",
                     hostname="shared.test", path="/", sources="GAU, Katana", import_tracking_complete=True)
        unrelated = Url(original_url="https://other.test", normalized_url="https://other.test", scheme="https",
                         hostname="other.test", path="/", sources="Manual", import_tracking_complete=True)
        db.add_all([exclusive, shared, unrelated])
        db.flush()
        db.add_all([ImportUrl(import_id=source_a.id, url_id=exclusive.id),
                    ImportUrl(import_id=source_a.id, url_id=shared.id),
                    ImportUrl(import_id=source_b.id, url_id=shared.id)])
        tech = Technology(name="TestTech", category="Test")
        shared_tech = Technology(name="SharedTech", category="Test")
        scan = ScanJob(status="completed", total_urls=2, completed_urls=2, status_2xx=2)
        db.add_all([tech, shared_tech, scan])
        db.flush()
        db.add_all([HttpResult(url_id=exclusive.id, status_code=200),
                    UrlTechnology(url_id=exclusive.id, technology_id=tech.id),
                    UrlTechnology(url_id=shared.id, technology_id=shared_tech.id),
                    HttpResult(url_id=unrelated.id, status_code=403),
                    ScanJobUrl(scan_job_id=scan.id, url_id=exclusive.id),
                    ScanJobUrl(scan_job_id=scan.id, url_id=shared.id),
                    ScanResult(scan_job_id=scan.id, url_id=exclusive.id, status_code=200),
                    ScanResult(scan_job_id=scan.id, url_id=shared.id, status_code=200)])
        db.commit()

        preview = preview_import_deletion(source_a.id, db)
        assert json.loads(preview.body)["data"]["exclusive_urls"] == 1
        response = delete_import(source_a.id, db)
        payload = json.loads(response.body)["data"]
        assert payload["deleted_urls"] == 1
        assert payload["preserved_shared_urls"] == 1
        assert db.query(Url).filter(Url.id == exclusive.id).count() == 0
        assert db.query(HttpResult).filter(HttpResult.url_id == exclusive.id).count() == 0
        assert db.query(UrlTechnology).filter(UrlTechnology.url_id == exclusive.id).count() == 0
        assert db.query(Technology).filter(Technology.id == tech.id).count() == 0
        assert db.query(Technology).filter(Technology.id == shared_tech.id).count() == 1
        assert db.query(ScanResult).filter(ScanResult.url_id == exclusive.id).count() == 0
        db.refresh(scan)
        assert scan.total_urls == 1
        assert scan.completed_urls == 1
        assert scan.status_2xx == 1
        assert db.query(Url).filter(Url.id == shared.id).count() == 1
        assert db.query(Url).filter(Url.id == shared.id).one().sources == "Katana"
        assert db.query(Url).filter(Url.id == unrelated.id).count() == 1
        assert db.query(HttpResult).filter(HttpResult.url_id == unrelated.id, HttpResult.status_code == 403).count() == 1
        assert db.query(UrlTechnology).filter(UrlTechnology.url_id == shared.id).count() == 1
        assert db.query(ReconImport).filter(ReconImport.id == source_b.id).count() == 1

        dashboard = json.loads(get_dashboard_stats(None, db).body)["data"]
        assert dashboard["metrics"]["total_urls"] == 1
        assert dashboard["metrics"]["status_403"] == 0
        assert dashboard["metrics"]["technologies"] == 1
        tech_api = json.loads(list_technologies(None, db).body)["data"]
        assert [technology["name"] for technology in tech_api] == ["SharedTech"]
        url_api = list_urls(target_id=None, q=None, search=None, search_field="all", regex=False,
                            status=None, source=None, interesting=None, pinned_only=False,
                            technology=None, hostname=None, sort_by="newest", page=1, limit=50, db=db)
        url_items = json.loads(url_api.body)["data"]["items"]
        assert {item["hostname"] for item in url_items} == {"shared.test"}
        subdomains = json.loads(list_subdomains(None, db).body)["data"]
        assert {item["hostname"] for item in subdomains} == {"shared.test"}
    finally:
        db.close()
        engine.dispose()


def test_deleting_final_tracked_source_leaves_no_urls():
    from api.dashboard import get_dashboard_stats, list_technologies
    from api.urls import list_urls

    engine, db = make_session()
    try:
        source = ReconImport(filename="one.txt", source_type="Manual")
        url = Url(original_url="https://one.test", normalized_url="https://one.test", scheme="https",
                  hostname="one.test", path="/", import_tracking_complete=True)
        second_url = Url(original_url="https://two.test", normalized_url="https://two.test", scheme="https",
                         hostname="two.test", path="/", import_tracking_complete=True)
        db.add_all([source, url, second_url])
        db.flush()
        db.add_all([ImportUrl(import_id=source.id, url_id=url.id),
                    ImportUrl(import_id=source.id, url_id=second_url.id)])
        db.commit()
        assert delete_import(source.id, db).status_code == 200
        assert db.query(Url).count() == 0
        assert db.query(ReconImport).count() == 0
        dashboard = json.loads(get_dashboard_stats(None, db).body)["data"]
        assert dashboard["metrics"]["total_urls"] == 0
        assert dashboard["metrics"]["technologies"] == 0
        assert dashboard["metrics"]["checked"] == 0
        assert dashboard["metrics"]["not_checked"] == 0
        assert dashboard["charts"]["sources"] == {}
        assert dashboard["charts"]["technologies"] == []
        assert all(value == 0 for value in dashboard["charts"]["status_distribution"].values())
        urls = list_urls(target_id=None, q=None, search=None, search_field="all", regex=False,
                         status=None, source=None, interesting=None, pinned_only=False,
                         technology=None, hostname=None, sort_by="newest", page=1, limit=50, db=db)
        assert json.loads(urls.body)["data"]["pagination"]["total"] == 0
        assert json.loads(list_technologies(None, db).body)["data"] == []
    finally:
        db.close()
        engine.dispose()


def test_delete_prunes_a_queued_active_scan_without_scanning_deleted_url():
    engine, db = make_session()
    try:
        source = ReconImport(filename="one.txt", source_type="Manual")
        url = Url(original_url="https://one.test", normalized_url="https://one.test", scheme="https",
                  hostname="one.test", path="/", import_tracking_complete=True)
        scan = ScanJob(status="queued", target_id=None)
        db.add_all([source, url, scan])
        db.flush()
        db.add_all([
            ImportUrl(import_id=source.id, url_id=url.id),
            ScanJobUrl(scan_job_id=scan.id, url_id=url.id),
        ])
        db.commit()
        response = delete_import(source.id, db)
        assert response.status_code == 200
        assert db.query(Url).count() == 0
        assert db.query(ReconImport).count() == 0
        assert db.query(ScanJob).count() == 0
        assert db.query(ScanJobUrl).count() == 0
    finally:
        db.close()
        engine.dispose()


def test_failed_transaction_rolls_back_source_and_url_deletion():
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy import event

    engine, db = make_session()
    try:
        source = ReconImport(filename="one.txt", source_type="Manual")
        url = Url(original_url="https://one.test", normalized_url="https://one.test", scheme="https",
                  hostname="one.test", path="/", import_tracking_complete=True)
        db.add_all([source, url])
        db.flush()
        db.add(ImportUrl(import_id=source.id, url_id=url.id))
        db.commit()
        source_id, url_id = source.id, url.id

        def fail_commit(session):
            raise SQLAlchemyError("simulated commit failure")

        event.listen(db, "before_commit", fail_commit)
        response = delete_import(source_id, db)
        event.remove(db, "before_commit", fail_commit)
        assert response.status_code == 500
        assert db.query(ReconImport).filter(ReconImport.id == source_id).count() == 1
        assert db.query(Url).filter(Url.id == url_id).count() == 1
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


def test_import_page_uses_accessible_confirmation_modal_and_no_native_confirm():
    root = Path(__file__).resolve().parents[1]
    markup = (root / "templates/imports.html").read_text()
    script = (root / "static/js/imports.js").read_text()
    assert '<dialog id="import-delete-dialog"' in markup
    assert 'id="import-delete-cancel"' in markup
    assert 'id="import-delete-confirm"' in markup
    assert "window.confirm" not in script
    assert "event.target === dialog" in script
    assert "event.preventDefault()" in script


def test_technology_page_refreshes_uncached_data_after_import_deletion():
    root = Path(__file__).resolve().parents[1]
    template = (root / "templates/technologies.html").read_text()
    dashboard = (root / "static/js/dashboard.js").read_text()
    urls = (root / "static/js/urls.js").read_text()
    assert 'fetch("/api/technologies", { cache: "no-store" })' in template
    assert 'fetch("/api/urls/subdomains", { cache: "no-store" })' in template
    assert 'event.key === "reconscope_data_changed"' in template
    assert "technologyLoadSequence" in template
    assert 'cache: "no-store"' in dashboard
    assert 'cache: "no-store"' in urls
    assert 'event.key === "reconscope_data_changed"' in dashboard
    assert 'event.key === "reconscope_data_changed"' in urls


def test_ingestion_records_exact_source_to_url_ownership():
    engine, db = make_session()
    try:
        result = process_urls_ingestion(
            db, target_id=None, raw_urls=["https://new.test/path", "https://new.test/path"],
            source_type="Manual", filename="source.txt",
        )
        url = db.query(Url).one()
        import_record = db.query(ReconImport).one()
        assert url.import_tracking_complete is True
        assert db.query(ImportUrl).filter_by(import_id=import_record.id, url_id=url.id).count() == 1
        assert result["raw_count"] == 2
        assert result["new_count"] == 1
        assert result["duplicate_count"] == 1
    finally:
        db.close()
        engine.dispose()


def test_deleted_source_stays_deleted_after_application_database_restart(tmp_path, monkeypatch):
    database_file = tmp_path / "restart-check.sqlite"
    url = f"sqlite:///{database_file}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "DATABASE_URL", url)
    database_module.init_db()
    session_factory = sessionmaker(bind=engine)
    db = session_factory()
    try:
        imported = process_urls_ingestion(
            db, target_id=None, raw_urls=["https://restart.test/"],
            source_type="Manual", filename="restart.txt",
        )
        assert delete_import(imported["import_id"], db).status_code == 200
    finally:
        db.close()
        engine.dispose()

    restarted_engine = create_engine(url, connect_args={"check_same_thread": False})
    monkeypatch.setattr(database_module, "engine", restarted_engine)
    database_module.init_db()
    restarted_db = sessionmaker(bind=restarted_engine)()
    try:
        assert restarted_db.query(Url).count() == 0
        assert restarted_db.query(ReconImport).count() == 0
        assert restarted_db.query(ImportUrl).count() == 0
    finally:
        restarted_db.close()
        restarted_engine.dispose()


def test_one_time_legacy_cleanup_removes_only_unowned_url_data(tmp_path, monkeypatch):
    from database.models import AppSetting

    database_file = tmp_path / "legacy-cleanup.sqlite"
    url = f"sqlite:///{database_file}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.setattr(database_module, "DATABASE_URL", url)
    sessions = sessionmaker(bind=engine)

    with sessions() as db:
        db.add(AppSetting(key="scope_enforcement", value="enabled"))
        active_import = ReconImport(filename="active.txt", source_type="Manual")
        db.add(active_import)
        db.flush()
        active_url = Url(original_url="https://active.test", normalized_url="https://active.test",
                         scheme="https", hostname="active.test", path="/", sources="Manual")
        orphan_url = Url(original_url="https://orphan.test", normalized_url="https://orphan.test",
                         scheme="https", hostname="orphan.test", path="/", sources="GAU")
        db.add_all([active_url, orphan_url])
        db.flush()
        db.add(ImportUrl(import_id=active_import.id, url_id=active_url.id))
        active_tech = Technology(name="ActiveTech", category="Test")
        orphan_tech = Technology(name="OrphanTech", category="Test")
        mixed_job = ScanJob(status="completed", total_urls=2, completed_urls=2, status_2xx=1)
        orphan_job = ScanJob(status="completed", total_urls=1, completed_urls=1, status_403=1)
        db.add_all([active_tech, orphan_tech, mixed_job, orphan_job])
        db.flush()
        db.add_all([
            HttpResult(url_id=active_url.id, status_code=200),
            HttpResult(url_id=orphan_url.id, status_code=403),
            UrlTechnology(url_id=active_url.id, technology_id=active_tech.id),
            UrlTechnology(url_id=orphan_url.id, technology_id=orphan_tech.id),
            ScanJobUrl(scan_job_id=mixed_job.id, url_id=active_url.id),
            ScanJobUrl(scan_job_id=mixed_job.id, url_id=orphan_url.id),
            ScanJobUrl(scan_job_id=orphan_job.id, url_id=orphan_url.id),
            ScanResult(scan_job_id=mixed_job.id, url_id=active_url.id, status_code=200),
            ScanResult(scan_job_id=mixed_job.id, url_id=orphan_url.id, status_code=403),
            ScanResult(scan_job_id=orphan_job.id, url_id=orphan_url.id, status_code=403),
        ])
        db.commit()
        active_url_id = active_url.id
        orphan_url_id = orphan_url.id
        mixed_job_id = mixed_job.id
        orphan_job_id = orphan_job.id

    database_module.init_db()

    with sessions() as db:
        assert db.query(Url).filter(Url.id == active_url_id).count() == 1
        assert db.query(Url).filter(Url.id == orphan_url_id).count() == 0
        assert db.query(HttpResult).filter(HttpResult.url_id == orphan_url_id).count() == 0
        assert db.query(UrlTechnology).filter(UrlTechnology.url_id == orphan_url_id).count() == 0
        assert db.query(Technology).filter(Technology.name == "OrphanTech").count() == 0
        assert db.query(Technology).filter(Technology.name == "ActiveTech").count() == 1
        assert db.query(ScanJob).filter(ScanJob.id == orphan_job_id).count() == 0
        mixed = db.query(ScanJob).filter(ScanJob.id == mixed_job_id).one()
        assert mixed.total_urls == 1
        assert mixed.completed_urls == 1
        assert mixed.status_403 == 0
        assert db.query(AppSetting).filter_by(key="scope_enforcement", value="enabled").count() == 1

    engine.dispose()
