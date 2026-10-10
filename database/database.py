import os
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
        with engine.begin() as conn:
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
