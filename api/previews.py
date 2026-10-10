import json
from asyncio import TimeoutError as AsyncTimeoutError

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import Target, Url
from services.browser_preview import (
    capture_preview,
    encode_preview_header,
    encode_preview_redirect_chain,
)
from . import error_response

router = APIRouter(prefix="/api/urls", tags=["Previews"])


@router.get("/{url_id}/preview")
async def get_url_preview(url_id: int, db: Session = Depends(get_db)):
    """
    On-demand webpage preview. Captures live in-memory JPEG via Playwright,
    streams bytes directly to the browser, and immediately discards them.
    NEVER writes to disk, SQLite, or persistent cache.
    """
    url_obj = db.query(Url).filter(Url.id == url_id).first()
    if not url_obj:
        return error_response("NOT_FOUND", "URL not found", status_code=404)

    target = db.query(Target).filter(Target.id == url_obj.target_id).first()
    if not target:
        return error_response("TARGET_NOT_FOUND", "Target scope not found", status_code=404)

    try:
        preview = await capture_preview(
            url=url_obj.normalized_url,
            root_domain=target.root_domain,
            scope_rules=target.scope_rules,
            timeout_ms=10000,
        )

        return Response(
            content=preview.image_bytes,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Preview-Requested-Url": encode_preview_header(preview.requested_url or url_obj.normalized_url),
                "X-Preview-Final-Url": encode_preview_header(preview.final_url),
                "X-Preview-Title": encode_preview_header(preview.title),
                "X-Preview-Status": str(preview.status_code or ""),
                "X-Preview-Content-Type": encode_preview_header(preview.content_type or ""),
                "X-Preview-Response-Headers": encode_preview_header(
                    json.dumps(preview.response_headers or {}, separators=(",", ":"))
                ),
                "X-Preview-Redirect-Count": str(len(preview.redirect_chain)),
                "X-Preview-Redirect-Chain": encode_preview_redirect_chain(preview.redirect_chain),
                "X-Preview-Main-Frame-Navigated": "true" if preview.main_frame_navigated else "false",
                "X-Preview-Browser-Version": encode_preview_header(preview.browser_version),
                "X-Preview-Settled": "true" if preview.settled else "false",
            },
        )
    except ValueError as ve:
        return error_response("SCOPE_VIOLATION", str(ve), status_code=403)
    except AsyncTimeoutError:
        return error_response("PREVIEW_TIMEOUT", "Preview exceeded its time limit. Try again or open the URL directly.", status_code=504)
    except Exception as e:
        return error_response("PREVIEW_FAILED", f"Failed to render preview: {str(e)}", status_code=500)
