from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from database.database import get_db
from database.models import Target, Url, ReconImport, ScanJob
from . import success_response, error_response

router = APIRouter(prefix="/api/targets", tags=["Targets"])


class TargetCreateSchema(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    root_domain: str = Field(..., min_length=1, max_length=255)
    scope_rules: Optional[str] = ""
    notes: Optional[str] = ""


class TargetUpdateSchema(BaseModel):
    name: Optional[str] = None
    root_domain: Optional[str] = None
    scope_rules: Optional[str] = None
    notes: Optional[str] = None


@router.get("")
def list_targets(db: Session = Depends(get_db)):
    targets = db.query(Target).order_by(Target.created_at.desc()).all()
    results = []
    for t in targets:
        url_count = db.query(Url).filter(Url.target_id == t.id).count()
        import_count = db.query(ReconImport).filter(ReconImport.target_id == t.id).count()
        scan_count = db.query(ScanJob).filter(ScanJob.target_id == t.id).count()
        results.append({
            "id": t.id,
            "name": t.name,
            "root_domain": t.root_domain,
            "scope_rules": t.scope_rules,
            "notes": t.notes,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            "url_count": url_count,
            "import_count": import_count,
            "scan_count": scan_count,
        })
    return success_response(results)


@router.post("")
def create_target(payload: TargetCreateSchema, db: Session = Depends(get_db)):
    root = payload.root_domain.strip().lower()
    if "://" in root:
        root = root.split("://")[1].split("/")[0]

    existing = db.query(Target).filter(Target.root_domain == root).first()
    if existing:
        return error_response("TARGET_EXISTS", f"A target with root domain '{root}' already exists", status_code=400)

    target = Target(
        name=payload.name.strip(),
        root_domain=root,
        scope_rules=payload.scope_rules.strip() if payload.scope_rules else "",
        notes=payload.notes.strip() if payload.notes else "",
    )
    db.add(target)
    db.commit()
    db.refresh(target)

    return success_response({
        "id": target.id,
        "name": target.name,
        "root_domain": target.root_domain,
        "scope_rules": target.scope_rules,
        "notes": target.notes,
        "created_at": target.created_at.isoformat(),
    }, status_code=201)


@router.get("/{target_id}")
def get_target(target_id: int, db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id).first()
    if not target:
        return error_response("NOT_FOUND", "Target not found", status_code=404)

    url_count = db.query(Url).filter(Url.target_id == target.id).count()
    return success_response({
        "id": target.id,
        "name": target.name,
        "root_domain": target.root_domain,
        "scope_rules": target.scope_rules,
        "notes": target.notes,
        "url_count": url_count,
        "created_at": target.created_at.isoformat() if target.created_at else None,
        "updated_at": target.updated_at.isoformat() if target.updated_at else None,
    })


@router.put("/{target_id}")
def update_target(target_id: int, payload: TargetUpdateSchema, db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id).first()
    if not target:
        return error_response("NOT_FOUND", "Target not found", status_code=404)

    if payload.name is not None:
        target.name = payload.name.strip()
    if payload.root_domain is not None:
        root = payload.root_domain.strip().lower()
        if "://" in root:
            root = root.split("://")[1].split("/")[0]
        target.root_domain = root
    if payload.scope_rules is not None:
        target.scope_rules = payload.scope_rules.strip()
    if payload.notes is not None:
        target.notes = payload.notes.strip()

    db.commit()
    db.refresh(target)

    return success_response({
        "id": target.id,
        "name": target.name,
        "root_domain": target.root_domain,
        "scope_rules": target.scope_rules,
        "notes": target.notes,
    })


@router.delete("/{target_id}")
def delete_target(target_id: int, db: Session = Depends(get_db)):
    target = db.query(Target).filter(Target.id == target_id).first()
    if not target:
        return error_response("NOT_FOUND", "Target not found", status_code=404)

    db.delete(target)
    db.commit()
    return success_response({"deleted_id": target_id})
