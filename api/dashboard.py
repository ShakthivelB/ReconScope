from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from database.database import get_db
from database.models import HttpResult, Technology, Url, UrlTechnology
from . import success_response

router = APIRouter(prefix="/api", tags=["Dashboard"])


@router.get("/technologies")
def list_technologies(target_id: Optional[int] = None, db: Session = Depends(get_db)):
    query = (
        db.query(Technology.name, Technology.category, func.count(UrlTechnology.url_id).label("count"))
        .join(UrlTechnology, Technology.id == UrlTechnology.technology_id)
        .join(Url, UrlTechnology.url_id == Url.id)
        .filter(Url.import_links.any())
    )
    if target_id:
        query = query.filter(Url.target_id == target_id)

    results = query.group_by(Technology.id).order_by(func.count(UrlTechnology.url_id).desc()).all()
    return success_response([
        {"name": r[0], "category": r[1], "count": r[2]}
        for r in results
    ])


@router.get("/dashboard/stats")
def get_dashboard_stats(target_id: Optional[int] = None, db: Session = Depends(get_db)):
    url_q = db.query(Url).filter(Url.import_links.any())
    if target_id:
        url_q = url_q.filter(Url.target_id == target_id)

    total_urls = url_q.count()
    interesting_count = url_q.filter(Url.is_interesting == True).count()
    subdomains_count = url_q.with_entities(func.count(distinct(Url.hostname))).scalar() or 0

    # Use each URL's latest stored result so historical probes cannot inflate
    # dashboard totals or make one URL appear in multiple status groups.
    latest_result_ids = (
        db.query(func.max(HttpResult.id).label("result_id"))
        .group_by(HttpResult.url_id)
        .subquery()
    )
    latest_results = (
        db.query(HttpResult)
        .join(latest_result_ids, HttpResult.id == latest_result_ids.c.result_id)
        .join(Url, HttpResult.url_id == Url.id)
        .filter(Url.import_links.any())
    )

    checked_q = db.query(func.count(distinct(HttpResult.url_id))).join(
        latest_result_ids, HttpResult.id == latest_result_ids.c.result_id
    ).join(Url, HttpResult.url_id == Url.id).filter(Url.import_links.any())
    if target_id:
        checked_q = checked_q.filter(Url.target_id == target_id)
    checked_count = checked_q.scalar() or 0

    not_checked_count = max(0, total_urls - checked_count)

    # HTTP Status distribution from latest results
    http_q = latest_results.with_entities(
        HttpResult.status_code,
        HttpResult.error_message,
        func.count(HttpResult.id).label("cnt"),
    )
    if target_id:
        http_q = http_q.filter(Url.target_id == target_id)

    status_counts = http_q.group_by(HttpResult.status_code, HttpResult.error_message).all()

    c_2xx = 0
    c_3xx = 0
    c_401 = 0
    c_403 = 0
    c_404 = 0
    c_5xx = 0
    c_alive = 0
    c_timeouts = 0
    c_errors = 0

    for sc, err_msg, count in status_counts:
        if sc is not None and sc > 0:
            c_alive += count
            if 200 <= sc < 300:
                c_2xx += count
            elif 300 <= sc < 400:
                c_3xx += count
            elif sc == 401:
                c_401 += count
            elif sc == 403:
                c_403 += count
            elif sc == 404:
                c_404 += count
            elif 500 <= sc < 600:
                c_5xx += count
        else:
            if err_msg and "timeout" in err_msg.lower():
                c_timeouts += count
            else:
                c_errors += count

    # Source breakdown
    source_counts = {}
    all_urls_sources = url_q.with_entities(Url.sources).all()
    for row in all_urls_sources:
        if row[0]:
            for s in row[0].split(","):
                s_clean = s.strip()
                if s_clean:
                    source_counts[s_clean] = source_counts.get(s_clean, 0) + 1

    # Top technologies
    tech_q = (
        db.query(Technology.name, func.count(UrlTechnology.url_id).label("cnt"))
        .join(UrlTechnology, Technology.id == UrlTechnology.technology_id)
        .join(Url, UrlTechnology.url_id == Url.id)
        .filter(Url.import_links.any())
    )
    technology_count_q = (
        db.query(func.count(distinct(Technology.id)))
        .select_from(Technology)
        .join(UrlTechnology, Technology.id == UrlTechnology.technology_id)
        .join(Url, UrlTechnology.url_id == Url.id)
        .filter(Url.import_links.any())
    )
    if target_id:
        tech_q = tech_q.filter(Url.target_id == target_id)
        technology_count_q = technology_count_q.filter(Url.target_id == target_id)
    top_techs = tech_q.group_by(Technology.id).order_by(func.count(UrlTechnology.url_id).desc()).limit(10).all()
    technology_count = technology_count_q.scalar() or 0

    return success_response({
        "metrics": {
            "total_urls": total_urls,
            "checked": checked_count,
            "not_checked": not_checked_count,
            "alive": c_alive,
            "status_200": c_2xx,
            "status_3xx": c_3xx,
            "status_401": c_401,
            "status_403": c_403,
            "status_404": c_404,
            "status_5xx": c_5xx,
            "timeouts": c_timeouts,
            "errors": c_errors,
            "interesting": interesting_count,
            "subdomains": subdomains_count,
            "technologies": technology_count,
        },
        "charts": {
            "status_distribution": {
                "2xx": c_2xx,
                "3xx": c_3xx,
                "401": c_401,
                "403": c_403,
                "404": c_404,
                "5xx": c_5xx,
                "Timeout": c_timeouts,
                "Errors": c_errors,
            },
            "sources": source_counts,
            "technologies": [{"name": t[0], "count": t[1]} for t in top_techs],
        },
    })
