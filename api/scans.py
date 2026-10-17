from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import ImportUrl, ReconImport, ScanJob, ScanJobUrl, ScanResult, Target, Url
from services.http_probe import ACTIVE_SCANS, run_scan_job
from . import error_response, success_response

router = APIRouter(prefix="/api/scans", tags=["Scans"])


class ScanCreateSchema(BaseModel):
    target_id: Optional[int] = None
    concurrency: Optional[int] = Field(15, ge=1, le=50)
    timeout: Optional[float] = Field(10.0, ge=2.0, le=30.0)


@router.get("")
def list_scans(target_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(ScanJob).filter(ScanJob.url_links.any(ScanJobUrl.url.has(Url.import_links.any(ImportUrl.recon_import.has(ReconImport.id.is_not(None))))))
    if target_id:
        query = query.filter(ScanJob.target_id == target_id)

    scans = query.order_by(ScanJob.created_at.desc()).all()
    results = []
    for s in scans:
        results.append({
            "id": s.id,
            "target_id": s.target_id,
            "status": s.status,
            "total_urls": s.total_urls,
            "completed_urls": s.completed_urls,
            "status_2xx": s.status_2xx,
            "status_3xx": s.status_3xx,
            "status_401": s.status_401,
            "status_403": s.status_403,
            "status_404": s.status_404,
            "status_5xx": s.status_5xx,
            "timeouts": s.timeouts,
            "errors": s.errors,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return success_response(results)


@router.get("/active")
def get_active_scan(target_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Returns currently running or queued scan job if any."""
    query = db.query(ScanJob).filter(
        ScanJob.status.in_(["running", "queued", "paused"]),
        ScanJob.url_links.any(ScanJobUrl.url.has(Url.import_links.any(ImportUrl.recon_import.has(ReconImport.id.is_not(None))))),
    )
    if target_id:
        query = query.filter(or_(ScanJob.target_id == target_id, ScanJob.target_id.is_(None)))
    scan = query.order_by(ScanJob.created_at.desc()).first()
    if not scan:
        return success_response(None)

    return success_response({
        "id": scan.id,
        "target_id": scan.target_id,
        "status": scan.status,
        "total_urls": scan.total_urls,
        "completed_urls": scan.completed_urls,
        "status_2xx": scan.status_2xx,
        "status_3xx": scan.status_3xx,
        "status_401": scan.status_401,
        "status_403": scan.status_403,
        "status_404": scan.status_404,
        "status_5xx": scan.status_5xx,
        "timeouts": scan.timeouts,
        "errors": scan.errors,
    })


@router.post("")
def start_scan(payload: ScanCreateSchema, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Queues the async scan worker as a FastAPI-managed background task so the
    HTTP response returns before probing starts and no detached task is needed.
    """
    target = db.query(Target).filter(Target.id == payload.target_id).first() if payload.target_id else None
    if payload.target_id is not None and not target:
        return error_response("NOT_FOUND", "Target not found", status_code=404)

    url_query = db.query(Url).join(ImportUrl).join(ReconImport).distinct()
    if payload.target_id is not None:
        url_query = url_query.filter(Url.target_id == payload.target_id)
    scan_urls = url_query.order_by(Url.id.asc()).all()
    url_count = len(scan_urls)
    if not url_count:
        return error_response("NO_URLS", "No URLs are available to scan. Please import URLs first.", status_code=400)

    active_query = db.query(ScanJob).filter(
        ScanJob.status.in_(["running", "queued", "paused"]),
        ScanJob.url_links.any(ScanJobUrl.url.has(Url.import_links.any(ImportUrl.recon_import.has(ReconImport.id.is_not(None))))),
    )
    if payload.target_id is not None:
        active_query = active_query.filter(
            or_(ScanJob.target_id == payload.target_id, ScanJob.target_id.is_(None))
        )
    active_job = active_query.order_by(ScanJob.created_at.desc()).first()
    if active_job:
        return success_response({
            "id": active_job.id,
            "target_id": active_job.target_id,
            "status": active_job.status,
            "total_urls": active_job.total_urls,
            "created_at": active_job.created_at.isoformat(),
        }, status_code=202)

    scan_job = ScanJob(
        target_id=payload.target_id,
        status="queued",
        total_urls=url_count,
        created_at=datetime.now(timezone.utc),
    )
    db.add(scan_job)
    db.flush()
    db.add_all(ScanJobUrl(scan_job_id=scan_job.id, url_id=url.id) for url in scan_urls)
    db.commit()
    db.refresh(scan_job)

    # Starlette runs async background callables on the server's event loop after
    # the response is sent; this avoids detached tasks and per-request loops.
    background_tasks.add_task(
        run_scan_job,
        scan_job_id=scan_job.id,
        concurrency=payload.concurrency or 15,
        timeout=payload.timeout or 10.0,
    )

    return success_response({
        "id": scan_job.id,
        "target_id": scan_job.target_id,
        "status": "queued",
        "total_urls": scan_job.total_urls,
        "created_at": scan_job.created_at.isoformat(),
    }, status_code=202)


@router.get("/diff")
def compare_scans(
    base_id: int = Query(..., description="Previous/base scan job ID"),
    compare_id: int = Query(..., description="Current/comparison scan job ID"),
    db: Session = Depends(get_db),
):
    base_scan = db.query(ScanJob).filter(ScanJob.id == base_id).first()
    comp_scan = db.query(ScanJob).filter(ScanJob.id == compare_id).first()
    if not base_scan or not comp_scan:
        return error_response("NOT_FOUND", "One or both scan jobs not found", status_code=404)

    base_results = {r.url_id: r for r in db.query(ScanResult).filter(ScanResult.scan_job_id == base_id).all()}
    comp_results = {r.url_id: r for r in db.query(ScanResult).filter(ScanResult.scan_job_id == compare_id).all()}

    new_url_ids = set(comp_results.keys()) - set(base_results.keys())
    removed_url_ids = set(base_results.keys()) - set(comp_results.keys())
    common_url_ids = set(base_results.keys()).intersection(set(comp_results.keys()))

    status_changes = []
    for uid in common_url_ids:
        b_code = base_results[uid].status_code
        c_code = comp_results[uid].status_code
        if b_code != c_code:
            url_obj = db.query(Url).filter(Url.id == uid).first()
            status_changes.append({
                "url_id": uid,
                "url": url_obj.normalized_url if url_obj else "Unknown",
                "old_status": b_code,
                "new_status": c_code,
            })

    new_urls_list = []
    for uid in list(new_url_ids)[:50]:
        url_obj = db.query(Url).filter(Url.id == uid).first()
        if url_obj:
            new_urls_list.append({"url_id": uid, "url": url_obj.normalized_url})

    return success_response({
        "base_scan_id": base_id,
        "compare_scan_id": compare_id,
        "summary": {
            "new_urls_count": len(new_url_ids),
            "removed_urls_count": len(removed_url_ids),
            "status_changes_count": len(status_changes),
        },
        "status_changes": status_changes,
        "new_urls_sample": new_urls_list,
    })


@router.get("/{scan_id}")
def get_scan(scan_id: int, db: Session = Depends(get_db)):
    s = db.query(ScanJob).filter(ScanJob.id == scan_id, ScanJob.url_links.any(ScanJobUrl.url.has(Url.import_links.any(ImportUrl.recon_import.has(ReconImport.id.is_not(None)))))).first()
    if not s:
        return error_response("NOT_FOUND", "Scan job not found", status_code=404)

    return success_response({
        "id": s.id,
        "target_id": s.target_id,
        "status": s.status,
        "total_urls": s.total_urls,
        "completed_urls": s.completed_urls,
        "status_2xx": s.status_2xx,
        "status_3xx": s.status_3xx,
        "status_401": s.status_401,
        "status_403": s.status_403,
        "status_404": s.status_404,
        "status_5xx": s.status_5xx,
        "timeouts": s.timeouts,
        "errors": s.errors,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    })


@router.post("/{scan_id}/pause")
def pause_scan(scan_id: int, db: Session = Depends(get_db)):
    s = db.query(ScanJob).filter(ScanJob.id == scan_id).first()
    if not s:
        return error_response("NOT_FOUND", "Scan job not found", status_code=404)

    if s.status != "running":
        return error_response("INVALID_STATE", f"Scan cannot be paused because it is in '{s.status}' state.")

    if scan_id in ACTIVE_SCANS:
        ACTIVE_SCANS[scan_id]["status"] = "paused"
    s.status = "paused"
    db.commit()
    return success_response({"id": s.id, "status": "paused"})


@router.post("/{scan_id}/resume")
def resume_scan(scan_id: int, db: Session = Depends(get_db)):
    s = db.query(ScanJob).filter(ScanJob.id == scan_id).first()
    if not s:
        return error_response("NOT_FOUND", "Scan job not found", status_code=404)

    if s.status != "paused":
        return error_response("INVALID_STATE", f"Scan cannot be resumed because it is in '{s.status}' state.")

    if scan_id in ACTIVE_SCANS:
        ACTIVE_SCANS[scan_id]["status"] = "running"
    s.status = "running"
    db.commit()
    return success_response({"id": s.id, "status": "running"})


@router.post("/{scan_id}/cancel")
def cancel_scan(scan_id: int, db: Session = Depends(get_db)):
    s = db.query(ScanJob).filter(ScanJob.id == scan_id).first()
    if not s:
        return error_response("NOT_FOUND", "Scan job not found", status_code=404)

    if scan_id in ACTIVE_SCANS:
        ACTIVE_SCANS[scan_id]["status"] = "cancelled"
    s.status = "cancelled"
    s.finished_at = datetime.now(timezone.utc)
    db.commit()
    return success_response({"id": s.id, "status": "cancelled"})
