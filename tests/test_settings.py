import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.settings import get_settings, update_scope_enforcement, ScopeSettingUpdate
from database.database import Base


def test_scope_enforcement_setting_defaults_enabled_and_persists():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    sessions = sessionmaker(bind=engine)

    with sessions() as db:
        initial = json.loads(get_settings(db).body)
        assert initial["data"]["scope_enforcement"] is True
        response = update_scope_enforcement(ScopeSettingUpdate(enabled=False), db)
        assert json.loads(response.body)["data"]["scope_enforcement"] is False

    with sessions() as db:
        assert json.loads(get_settings(db).body)["data"]["scope_enforcement"] is False
    engine.dispose()
