from sqlalchemy.orm import Session

from database.models import AppSetting


def scope_enforcement_enabled(db: Session) -> bool:
    setting = db.query(AppSetting).filter(AppSetting.key == "scope_enforcement").first()
    return setting is None or setting.value != "disabled"
