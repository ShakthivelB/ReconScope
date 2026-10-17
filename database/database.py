import os
import re
from pathlib import Path
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
