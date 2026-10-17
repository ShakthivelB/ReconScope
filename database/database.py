import os
import re
from pathlib import Path
from datetime import datetime, timezone
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "reconscope.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    from . import models  # noqa: F401
    Base.metadata.create_all(bind=engine)

    # Backward-compatible additions for existing local databases.
    if DATABASE_URL.startswith("sqlite"):
        _make_target_links_optional(engine)
        with engine.begin() as conn:
            target_cols = conn.execute(text("PRAGMA table_info(targets)")).fetchall()
            target_col_names = {row[1] for row in target_cols}
            if "scope_rules" not in target_col_names:
                conn.execute(text("ALTER TABLE targets ADD COLUMN scope_rules TEXT NOT NULL DEFAULT ''"))

            res = conn.execute(text("PRAGMA table_info(http_results)")).fetchall()
            col_names = {row[1] for row in res}
            additions = {
                "error_message": "TEXT",
                "language": "VARCHAR(64)",
                "description": "TEXT",
                "powered_by": "VARCHAR(128)",
            }
            for name, sql_type in additions.items():
                if name not in col_names:
                    conn.execute(text(f"ALTER TABLE http_results ADD COLUMN {name} {sql_type}"))

            url_cols = conn.execute(text("PRAGMA table_info(urls)")).fetchall()
            url_col_names = {row[1] for row in url_cols}
            if "is_pinned" not in url_col_names:
                conn.execute(text("ALTER TABLE urls ADD COLUMN is_pinned BOOLEAN NOT NULL DEFAULT 0"))
            if "import_tracking_complete" not in url_col_names:
                conn.execute(text("ALTER TABLE urls ADD COLUMN import_tracking_complete BOOLEAN NOT NULL DEFAULT 0"))

    _cleanup_legacy_import_orphans(engine)
    _cleanup_unattached_technologies(engine)


def _cleanup_unattached_technologies(migration_engine=engine):
    """Remove technology detections left without any owning URL."""
    from .models import AppSetting, Technology

    migration_sessions = sessionmaker(autocommit=False, autoflush=False, bind=migration_engine)
    db = migration_sessions()
    try:
        with db.begin():
            marker = db.query(AppSetting).filter(
                AppSetting.key == "migration_unattached_technologies_v1"
            ).first()
            if marker:
                return
            db.query(Technology).filter(~Technology.url_technologies.any()).delete(
                synchronize_session=False
            )
            db.add(AppSetting(key="migration_unattached_technologies_v1", value="completed"))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _cleanup_legacy_import_orphans(migration_engine=engine):
    """One-time prune of URL data with no active import ownership."""
    from .models import (
        AppSetting,
        ImportUrl,
        ReconImport,
        ScanJob,
        ScanJobUrl,
        ScanResult,
        Technology,
        Url,
        UrlTechnology,
    )

    migration_sessions = sessionmaker(autocommit=False, autoflush=False, bind=migration_engine)
    db = migration_sessions()
    try:
        with db.begin():
            marker = db.query(AppSetting).filter(
                AppSetting.key == "migration_active_import_ownership_v1"
            ).first()
            if marker:
                return

            # Backfill known historical scan membership only from actual results.
            # Old queued scans had no URL snapshot and are cancelled below.
            result_pairs = (
                db.query(ScanResult.scan_job_id, ScanResult.url_id)
                .join(Url, Url.id == ScanResult.url_id)
                .join(ImportUrl, ImportUrl.url_id == Url.id)
                .join(ReconImport, ReconImport.id == ImportUrl.import_id)
                .distinct()
                .all()
            )
            existing_pairs = set(
                db.query(ScanJobUrl.scan_job_id, ScanJobUrl.url_id).all()
            )
            db.add_all(
                ScanJobUrl(scan_job_id=job_id, url_id=url_id)
                for job_id, url_id in result_pairs
                if (job_id, url_id) not in existing_pairs
            )
            db.flush()

            active_url_query = (
                db.query(ImportUrl.url_id)
                .join(ReconImport, ReconImport.id == ImportUrl.import_id)
                .join(Url, Url.id == ImportUrl.url_id)
                .subquery()
            )
            orphan_ids = [
                row[0] for row in db.query(Url.id)
                .filter(~Url.id.in_(db.query(active_url_query.c.url_id)))
                .all()
            ]
            if orphan_ids:
                affected_technology_ids = {
                    row[0] for row in db.query(UrlTechnology.technology_id)
                    .filter(UrlTechnology.url_id.in_(orphan_ids)).distinct().all()
                }
                db.query(ScanResult).filter(ScanResult.url_id.in_(orphan_ids)).delete(
                    synchronize_session=False
                )
                db.query(ScanJobUrl).filter(ScanJobUrl.url_id.in_(orphan_ids)).delete(
                    synchronize_session=False
                )
                for url in db.query(Url).filter(Url.id.in_(orphan_ids)).all():
                    db.delete(url)
                db.flush()
                if affected_technology_ids:
                    orphaned_technologies = db.query(Technology).filter(
                        Technology.id.in_(affected_technology_ids),
                        ~Technology.url_technologies.any(),
                    ).all()
                    for technology in orphaned_technologies:
                        db.delete(technology)

            # Clear dangling ownership and result rows left by databases that
            # previously ran without SQLite foreign-key enforcement.
            db.query(ImportUrl).filter(
                ~ImportUrl.import_id.in_(db.query(ReconImport.id))
            ).delete(synchronize_session=False)
            db.query(ImportUrl).filter(
                ~ImportUrl.url_id.in_(db.query(Url.id))
            ).delete(synchronize_session=False)
            db.query(ScanResult).filter(
                ~ScanResult.url_id.in_(db.query(active_url_query.c.url_id))
            ).delete(synchronize_session=False)
            db.query(ScanJobUrl).filter(
                ~ScanJobUrl.url_id.in_(db.query(active_url_query.c.url_id))
            ).delete(synchronize_session=False)

            now = datetime.now(timezone.utc)
            for job in db.query(ScanJob).all():
                if job.status in ("queued", "running", "paused"):
                    job.status = "cancelled"
                    job.finished_at = now

                total_urls = db.query(ScanJobUrl.id).filter(
                    ScanJobUrl.scan_job_id == job.id
                ).count()
                if not total_urls:
                    db.delete(job)
                    continue

                results = (
                    db.query(ScanResult)
                    .join(
                        ScanJobUrl,
                        (ScanJobUrl.scan_job_id == ScanResult.scan_job_id)
                        & (ScanJobUrl.url_id == ScanResult.url_id),
                    )
                    .filter(ScanResult.scan_job_id == job.id)
                    .all()
                )
                job.total_urls = total_urls
                job.completed_urls = len(results)
                job.status_2xx = sum(1 for row in results if row.status_code is not None and 200 <= row.status_code < 300)
                job.status_3xx = sum(1 for row in results if row.status_code is not None and 300 <= row.status_code < 400)
                job.status_401 = sum(1 for row in results if row.status_code == 401)
                job.status_403 = sum(1 for row in results if row.status_code == 403)
                job.status_404 = sum(1 for row in results if row.status_code == 404)
                job.status_5xx = sum(1 for row in results if row.status_code is not None and 500 <= row.status_code < 600)
                job.timeouts = sum(
                    1 for row in results
                    if row.status_code is None and row.error_message and "timeout" in row.error_message.lower()
                )
                job.errors = sum(
                    1 for row in results
                    if row.status_code is None
                    and not (row.error_message and "timeout" in row.error_message.lower())
                )

            db.add(AppSetting(
                key="migration_active_import_ownership_v1",
                value="completed",
            ))
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _make_target_links_optional(migration_engine=engine):
    """Rebuild legacy SQLite tables with nullable target_id without losing rows/indexes."""
    tables = ("recon_imports", "urls", "scan_jobs")
    raw_conn = migration_engine.raw_connection()
    try:
        raw = raw_conn.driver_connection
        raw.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            info = raw.execute(f'PRAGMA table_info("{table}")').fetchall()
            target_col = next((column for column in info if column[1] == "target_id"), None)
            if target_col is None or target_col[3] == 0:
                continue

            create_sql = raw.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0]
            indexes = [row[0] for row in raw.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            )]
            triggers = [row[0] for row in raw.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            )]
            columns = [column[1] for column in info]
            new_table = f"{table}_nullable_target"
            raw.execute(f'DROP TABLE IF EXISTS "{new_table}"')
            new_sql = re.sub(
                rf"CREATE TABLE\s+[\"`]?{re.escape(table)}[\"`]?",
                f'CREATE TABLE "{new_table}"',
                create_sql,
                count=1,
                flags=re.IGNORECASE,
            ).replace("target_id INTEGER NOT NULL", "target_id INTEGER")
            if new_sql == create_sql:
                raise RuntimeError(f"Could not prepare nullable target migration for {table}")

            quoted_cols = ", ".join(f'"{column}"' for column in columns)
            raw.execute(new_sql)
            raw.execute(
                f'INSERT INTO "{new_table}" ({quoted_cols}) SELECT {quoted_cols} FROM "{table}"'
            )
            raw.execute(f'DROP TABLE "{table}"')
            raw.execute(f'ALTER TABLE "{new_table}" RENAME TO "{table}"')
            for statement in indexes + triggers:
                raw.execute(statement)
            raw_conn.commit()
        raw.execute("PRAGMA foreign_keys=ON")
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()
