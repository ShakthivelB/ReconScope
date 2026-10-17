import csv
import io
import json
import re
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import ReconImport, Target, Url
from services.interesting_detector import detect_interesting_url
from services.url_normalizer import normalize_url
from services.url_parser import parse_url
from . import error_response, success_response

router = APIRouter(prefix="/api/imports", tags=["Imports"])


def extract_raw_urls_from_text(content: str) -> List[str]:
    """Extracts candidate URLs from raw text line by line."""
    urls = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # If line contains space, split or take first token if looks like URL
        tokens = line.split()
        if tokens:
            urls.append(tokens[0])
    return urls


def extract_urls_from_json(content: str) -> List[str]:
    """Extracts URLs from common JSON recon outputs (e.g. katana, httpx)."""
    urls = []
    try:
        data = json.loads(content)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict):
                    url = item.get("url") or item.get("endpoint") or item.get("input")
                    if url:
                        urls.append(str(url))
        elif isinstance(data, dict):
            # Check for keys
            for key in ("urls", "endpoints", "results"):
                if key in data and isinstance(data[key], list):
                    for item in data[key]:
                        if isinstance(item, str):
                            urls.append(item)
                        elif isinstance(item, dict) and "url" in item:
                            urls.append(str(item["url"]))
    except json.JSONDecodeError:
        # Might be JSONL (JSON lines from katana / httpx)
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    url = item.get("url") or item.get("endpoint") or item.get("input")
                    if url:
                        urls.append(str(url))
                elif isinstance(item, str):
                    urls.append(item)
            except Exception:
                continue
    return urls


def extract_urls_from_csv(content: str) -> List[str]:
    """Extracts URLs from CSV files."""
    urls = []
    reader = csv.reader(io.StringIO(content))
    for row in reader:
        if not row:
            continue
        # Find first field that looks like a URL or domain
        for field in row:
            field = field.strip()
            if field.startswith("http://") or field.startswith("https://") or "." in field:
                urls.append(field)
                break
    return urls


def process_urls_ingestion(
    db: Session,
    target_id: Optional[int],
    raw_urls: List[str],
    source_type: str,
    filename: str = "manual_paste.txt",
) -> dict:
    """Core URL normalization, deduplication, and persistence logic."""
    target = db.query(Target).filter(Target.id == target_id).first() if target_id is not None else None
    if target_id is not None and not target:
        raise ValueError("Target not found")

    raw_count = len(raw_urls)
    new_count = 0
    duplicate_count = 0

    now = datetime.now(timezone.utc)

    # URL identity is global now that imports can mix domains without targets.
    # Keep any legacy target association on an existing record; never duplicate it.
    existing_urls = {u.normalized_url: u for u in db.query(Url).all()}

    seen_in_batch = set()

    for raw in raw_urls:
        try:
            norm = normalize_url(raw)
            if not norm:
                continue

            if norm in seen_in_batch:
                duplicate_count += 1
                continue
            seen_in_batch.add(norm)

            if norm in existing_urls:
                duplicate_count += 1
                url_obj = existing_urls[norm]
                # Update source tracking if new source discovered
                existing_sources = [s.strip() for s in url_obj.sources.split(",") if s.strip()]
                if source_type not in existing_sources:
                    existing_sources.append(source_type)
                    url_obj.sources = ", ".join(existing_sources)
                url_obj.last_seen = now
            else:
                new_count += 1
                parsed = parse_url(norm)
                is_interesting, reasons = detect_interesting_url(norm)

                url_obj = Url(
                    target_id=target_id,
                    original_url=raw,
                    normalized_url=norm,
                    scheme=parsed["scheme"],
                    hostname=parsed["hostname"],
                    port=parsed["port"],
                    path=parsed["path"],
                    query=parsed["query"],
                    sources=source_type,
                    is_interesting=is_interesting,
                    interesting_reasons=", ".join(reasons),
                    first_seen=now,
                    last_seen=now,
                )
                db.add(url_obj)
                existing_urls[norm] = url_obj
        except Exception:
            continue

    # Record the import event
    recon_import = ReconImport(
        target_id=target_id,
        filename=filename,
        source_type=source_type,
        raw_count=raw_count,
        new_count=new_count,
        duplicate_count=duplicate_count,
        created_at=now,
    )
    db.add(recon_import)
    db.commit()

    return {
        "import_id": recon_import.id,
        "filename": filename,
        "source_type": source_type,
        "raw_count": raw_count,
        "new_count": new_count,
        "duplicate_count": duplicate_count,
    }


@router.get("")
def list_imports(target_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = db.query(ReconImport)
    if target_id:
        query = query.filter(ReconImport.target_id == target_id)
    imports = query.order_by(ReconImport.created_at.desc()).all()
    results = [
        {
            "id": imp.id,
            "target_id": imp.target_id,
            "filename": imp.filename,
            "source_type": imp.source_type,
            "raw_count": imp.raw_count,
            "new_count": imp.new_count,
            "duplicate_count": imp.duplicate_count,
            "created_at": imp.created_at.isoformat() if imp.created_at else None,
        }
        for imp in imports
    ]
    return success_response(results)


@router.delete("/{import_id}")
def delete_import(import_id: int, db: Session = Depends(get_db)):
    """Delete one import-history record without touching URLs or their results."""
    import_record = db.query(ReconImport).filter(ReconImport.id == import_id).first()
    if not import_record:
        return error_response("NOT_FOUND", "Import record not found", status_code=404)
    filename = import_record.filename

    try:
        db.delete(import_record)
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        return error_response("DELETE_FAILED", "Could not delete the import record", status_code=500)

    return success_response({
        "deleted_id": import_id,
        "filename": filename,
        "urls_preserved": True,
    })


@router.post("")
async def upload_import_files(
    target_id: Optional[int] = Form(None),
    source_type: str = Form("Manual"),
    raw_text: Optional[str] = Form(None),
    files: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db),
):
    target = db.query(Target).filter(Target.id == target_id).first() if target_id is not None else None
    if target_id is not None and not target:
        return error_response("NOT_FOUND", "Target not found", status_code=404)

    results = []

    # Handle pasted text
    if raw_text and raw_text.strip():
        urls = extract_raw_urls_from_text(raw_text)
        if urls:
            res = process_urls_ingestion(
                db=db,
                target_id=target_id,
                raw_urls=urls,
                source_type=source_type,
                filename="pasted_urls.txt",
            )
            results.append(res)

    # Handle uploaded files
    if files:
        for file in files:
            if not file.filename:
                continue
            content_bytes = await file.read()
            try:
                content = content_bytes.decode("utf-8", errors="replace")
            except Exception:
                continue

            fname_lower = file.filename.lower()
            if fname_lower.endswith(".json") or fname_lower.endswith(".jsonl"):
                extracted = extract_urls_from_json(content)
            elif fname_lower.endswith(".csv"):
                extracted = extract_urls_from_csv(content)
            else:
                extracted = extract_raw_urls_from_text(content)

            if extracted:
                # Infer source from filename if generic
                inferred_source = source_type
                if source_type in ("Manual", "Generic", "Unknown"):
                    for candidate in ("gau", "katana", "wayback", "subfinder", "amass", "httpx"):
                        if candidate in fname_lower:
                            inferred_source = candidate.upper()
                            break

                res = process_urls_ingestion(
                    db=db,
                    target_id=target_id,
                    raw_urls=extracted,
                    source_type=inferred_source,
                    filename=file.filename,
                )
                results.append(res)

    if not results:
        return error_response("NO_URLS_FOUND", "No valid URLs were found in the uploaded content or files.")

    total_raw = sum(r["raw_count"] for r in results)
    total_new = sum(r["new_count"] for r in results)
    total_dupes = sum(r["duplicate_count"] for r in results)

    return success_response({
        "summary": {
            "total_raw": total_raw,
            "total_new": total_new,
            "total_duplicates": total_dupes,
        },
        "imports": results,
    }, status_code=201)
