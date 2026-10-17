from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import AppSetting
from services.app_settings import scope_enforcement_enabled
from . import success_response

router = APIRouter(prefix="/api/settings", tags=["Settings"])


class ScopeSettingUpdate(BaseModel):
    enabled: bool


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    return success_response({"scope_enforcement": scope_enforcement_enabled(db)})


@router.put("/scope-enforcement")
def update_scope_enforcement(payload: ScopeSettingUpdate, db: Session = Depends(get_db)):
    setting = db.query(AppSetting).filter(AppSetting.key == "scope_enforcement").first()
    value = "enabled" if payload.enabled else "disabled"
    if setting is None:
        db.add(AppSetting(key="scope_enforcement", value=value))
    else:
        setting.value = value
    db.commit()
    return success_response({"scope_enforcement": payload.enabled})
