from sqlalchemy import create_engine, text

from database.database import _make_target_links_optional


def test_legacy_tables_migrate_to_optional_targets_and_keep_rows_and_indexes():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE targets (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO targets (id, name) VALUES (1, 'Existing')"))
        connection.execute(text(
            "CREATE TABLE recon_imports (id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL, filename TEXT, "
            "FOREIGN KEY(target_id) REFERENCES targets(id) ON DELETE CASCADE)"
        ))
        connection.execute(text(
            "CREATE TABLE urls (id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL, normalized_url TEXT, "
            "FOREIGN KEY(target_id) REFERENCES targets(id) ON DELETE CASCADE)"
        ))
        connection.execute(text(
            "CREATE TABLE scan_jobs (id INTEGER PRIMARY KEY, target_id INTEGER NOT NULL, status TEXT, "
            "FOREIGN KEY(target_id) REFERENCES targets(id) ON DELETE CASCADE)"
        ))
        connection.execute(text(
            "CREATE TABLE scan_results (id INTEGER PRIMARY KEY, scan_job_id INTEGER NOT NULL, url_id INTEGER NOT NULL, "
            "FOREIGN KEY(scan_job_id) REFERENCES scan_jobs(id) ON DELETE CASCADE, "
            "FOREIGN KEY(url_id) REFERENCES urls(id) ON DELETE CASCADE)"
        ))
        connection.execute(text("CREATE INDEX ix_urls_target_id ON urls(target_id)"))
        connection.execute(text("INSERT INTO recon_imports VALUES (5, 1, 'existing.txt')"))
        connection.execute(text("INSERT INTO urls VALUES (7, 1, 'https://example.org/')"))
        connection.execute(text("INSERT INTO scan_jobs VALUES (9, 1, 'completed')"))
        connection.execute(text("INSERT INTO scan_results VALUES (11, 9, 7)"))

    _make_target_links_optional(engine)
    _make_target_links_optional(engine)

    with engine.connect() as connection:
        for table, row_id in (("recon_imports", 5), ("urls", 7), ("scan_jobs", 9)):
            info = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
            target_column = next(column for column in info if column[1] == "target_id")
            assert target_column[3] == 0
            assert connection.execute(text(f"SELECT COUNT(*) FROM {table} WHERE id={row_id}")).scalar() == 1
        index = connection.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='ix_urls_target_id'"
        )).scalar()
        assert index == 1
        foreign_keys = connection.execute(text("PRAGMA foreign_key_list(scan_results)")).fetchall()
        assert {row[2] for row in foreign_keys} == {"urls", "scan_jobs"}
    engine.dispose()
