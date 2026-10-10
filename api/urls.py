import json
import multiprocessing
import re
import time
from dataclasses import dataclass
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Integer, column, func, or_, table
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from database.database import get_db
from database.models import HttpResult, Technology, Url, UrlTechnology
from . import error_response, success_response

router = APIRouter(prefix="/api/urls", tags=["URLs"])

MAX_REGEX_LENGTH = 256
MAX_SEARCH_QUERY_LENGTH = 1024
MAX_SEARCH_TERMS = 32
REGEX_QUERY_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class SearchTerm:
    value: str
    excluded: bool = False
    quoted: bool = False


def parse_search_query(query: str) -> List[SearchTerm]:
    """Split a query into terms; a leading ! marks an exclusion."""
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        raise ValueError(f"Search query is too long (maximum {MAX_SEARCH_QUERY_LENGTH} characters).")

    terms = []
    chars = []
    quote = None
    token_quoted = False
    index = 0

    def finish_token():
        nonlocal chars, token_quoted
        raw_value = "".join(chars)
        chars = []
        if not raw_value:
            token_quoted = False
            return
        excluded = raw_value.startswith("!")
        value = raw_value[1:] if excluded else raw_value
        if not value:
            raise ValueError("The ! exclusion operator must be followed by a search term.")
        terms.append(SearchTerm(value=value, excluded=excluded, quoted=token_quoted))
        token_quoted = False
        if len(terms) > MAX_SEARCH_TERMS:
            raise ValueError(f"Search query has too many terms (maximum {MAX_SEARCH_TERMS}).")

    while index < len(query):
        char = query[index]
        if quote:
            if char == "\\" and index + 1 < len(query) and query[index + 1] in (quote, "\\"):
                chars.append(query[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            else:
                chars.append(char)
        elif char in ("\"", "'"):
            quote = char
            token_quoted = True
        elif char.isspace():
            finish_token()
        else:
            chars.append(char)
        index += 1

    if quote:
        raise ValueError("Unclosed quote in search query.")
    finish_token()
    return terms


def _validate_regex(pattern: str) -> Optional[str]:
    """Validate the complete Python regex syntax before starting bounded matching."""
    if len(pattern) > MAX_REGEX_LENGTH:
        return f"Regex is too long (maximum {MAX_REGEX_LENGTH} characters)."
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"Invalid regular expression: {exc}"
    return None


def _regex_worker(send_conn, patterns, candidates):
    """Evaluate regex matches out of process so a pathological pattern is stoppable."""
    try:
        compiled_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        matching_ids = [[] for _ in compiled_patterns]
        for url_id, values in candidates:
            for index, compiled in enumerate(compiled_patterns):
                if any(compiled.search(value) for value in values):
                    matching_ids[index].append(url_id)
        send_conn.send((True, matching_ids))
    except Exception as exc:
        send_conn.send((False, str(exc)))
    finally:
        send_conn.close()


def _bounded_regex_matches(patterns, candidates, timeout=REGEX_QUERY_TIMEOUT_SECONDS):
    if isinstance(patterns, str):
        patterns = [patterns]
    methods = multiprocessing.get_all_start_methods()
    context = multiprocessing.get_context("fork" if "fork" in methods else "spawn")
    receive_conn, send_conn = context.Pipe(duplex=False)
    process = context.Process(target=_regex_worker, args=(send_conn, patterns, candidates), daemon=True)
    try:
        process.start()
    except Exception:
        receive_conn.close()
        send_conn.close()
        raise
    send_conn.close()
    try:
        if not receive_conn.poll(timeout):
            process.terminate()
            process.join()
            return None, f"Regex search exceeded the {timeout:g} second time limit. Narrow the search or choose a specific field."
        succeeded, result = receive_conn.recv()
        process.join(timeout=0.2)
        if not succeeded:
            return None, f"Invalid regular expression: {result}"
        return result, None
    finally:
        receive_conn.close()
        if process.is_alive():
            process.terminate()
            process.join()


def _regex_candidates(db: Session, base_query, search_field: str):
    """Load only candidate URL fields needed by regex mode, plus title/technology if selected."""
    fields = [Url.id]
    if search_field in ("all", "url"):
        fields.append(Url.normalized_url.label("match_url"))
    elif search_field == "host":
        fields.append(Url.hostname.label("match_host"))
    elif search_field == "path":
        fields.append(Url.path.label("match_path"))
    elif search_field == "query":
        fields.append(Url.query.label("match_query"))

    id_query = base_query.enable_eagerloads(False).with_entities(Url.id.label("candidate_id")).distinct().subquery()
    candidates_query = db.query(*fields).join(id_query, Url.id == id_query.c.candidate_id)
    include_title = search_field in ("all", "title")
    include_technology = search_field in ("all", "technology")
    if include_title:
        title_subquery = _latest_http_titles(db)
        candidates_query = candidates_query.outerjoin(title_subquery, Url.id == title_subquery.c.url_id)
        candidates_query = candidates_query.add_columns(title_subquery.c.title.label("match_title"))

    rows = candidates_query.all()
    candidates = []
    candidate_ids = []
    for row in rows:
        values = []
        for key in ("match_url", "match_host", "match_path", "match_query", "match_title"):
            value = getattr(row, key, None)
            if value:
                values.append(str(value))
        candidates.append([row.id, values])
        candidate_ids.append(row.id)

    if include_technology and candidate_ids:
        technology_by_url = {}
        for offset in range(0, len(candidate_ids), 500):
            batch_ids = candidate_ids[offset:offset + 500]
            rows = db.query(UrlTechnology.url_id, Technology.name).join(
                Technology, UrlTechnology.technology_id == Technology.id
            ).filter(UrlTechnology.url_id.in_(batch_ids)).all()
            for url_id, name in rows:
                technology_by_url.setdefault(url_id, []).append(name)
        for candidate in candidates:
            candidate[1].extend(technology_by_url.get(candidate[0], []))
    return candidates


def _literal_like(column, term: str):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


def _latest_http_titles(db: Session):
    latest_ids = db.query(
        HttpResult.url_id.label("url_id"),
        func.max(HttpResult.id).label("result_id"),
    ).group_by(HttpResult.url_id).subquery()
    return db.query(
        HttpResult.url_id.label("url_id"),
        HttpResult.title.label("title"),
    ).join(latest_ids, HttpResult.id == latest_ids.c.result_id).subquery()


class PinStateRequest(BaseModel):
    pinned: bool


@router.get("")
def list_urls(
    target_id: Optional[int] = None,
    q: Optional[str] = Query(None, description="Search term for URL, hostname, path, title, param"),
    search: Optional[str] = Query(None, description="Literal or regex URL search"),
    search_field: str = Query("all", description="all, url, host, path, query, title, technology"),
    regex: bool = Query(False, description="Interpret search as a bounded regular expression"),
    status: Optional[str] = Query(None, description="Status filter: 2xx, 3xx, 401, 403, 404, 5xx, timeout, error, not_checked"),
    source: Optional[str] = Query(None, description="Source filter (e.g. GAU, Katana)"),
    interesting: Optional[bool] = Query(None, description="Filter for interesting URLs only"),
    pinned_only: bool = Query(False, description="Show only pinned URLs"),
    technology: Optional[str] = Query(None, description="Filter by technology name"),
    hostname: Optional[str] = Query(None, description="Filter by specific subdomain/host"),
    sort_by: str = Query("newest", description="newest, oldest, status, response_time, response_size, hostname, path"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(Url).options(
        joinedload(Url.http_results),
        joinedload(Url.url_technologies).joinedload(UrlTechnology.technology),
    )

    if target_id:
        query = query.filter(Url.target_id == target_id)

    if hostname:
        query = query.filter(Url.hostname == hostname.strip().lower())

    if interesting is True:
        query = query.filter(Url.is_interesting == True)

    if pinned_only:
        query = query.filter(Url.is_pinned == True)

    if source:
        query = query.filter(Url.sources.ilike(f"%{source.strip()}%"))

    # Join HttpResult if needed for status or sorting
    if status or sort_by in ("status", "response_time", "response_size"):
        latest_http_ids = (
            db.query(
                HttpResult.url_id.label("url_id"),
                func.max(HttpResult.id).label("result_id"),
            )
            .group_by(HttpResult.url_id)
            .subquery()
        )
        query = query.outerjoin(
            latest_http_ids,
            Url.id == latest_http_ids.c.url_id,
        ).outerjoin(HttpResult, latest_http_ids.c.result_id == HttpResult.id)

    # Status filter logic
    if status:
        st = status.lower().strip()
        if st in ("not_checked", "unprobed"):
            query = query.filter(HttpResult.id == None)
        elif st == "2xx":
            query = query.filter(HttpResult.status_code >= 200, HttpResult.status_code < 300)
        elif st == "3xx":
            query = query.filter(HttpResult.status_code >= 300, HttpResult.status_code < 400)
        elif st == "401":
            query = query.filter(HttpResult.status_code == 401)
        elif st == "403":
            query = query.filter(HttpResult.status_code == 403)
        elif st == "404":
            query = query.filter(HttpResult.status_code == 404)
        elif st == "429":
            query = query.filter(HttpResult.status_code == 429)
        elif st == "5xx":
            query = query.filter(HttpResult.status_code >= 500, HttpResult.status_code < 600)
        elif st == "timeout":
            query = query.filter(
                HttpResult.id != None,
                or_(
                    HttpResult.error_message.ilike("%timeout%"),
                    HttpResult.status_code == 408,
                    HttpResult.status_code == 504,
                ),
            )
        elif st == "error":
            query = query.filter(
                HttpResult.id != None,
                HttpResult.status_code == None,
                ~HttpResult.error_message.ilike("%timeout%"),
            )
        elif st.isdigit() and 100 <= int(st) <= 599:
            query = query.filter(HttpResult.status_code == int(st))

    # Technology filter
    if technology:
        query = query.join(UrlTechnology, Url.id == UrlTechnology.url_id).join(
            Technology, UrlTechnology.technology_id == Technology.id
        ).filter(Technology.name.ilike(f"%{technology.strip()}%"))

    # Parse include/exclude terms before applying search to the selected fields.
    search_value = search if search is not None else q
    search_terms = []
    if search_value and search_value.strip():
        try:
            search_terms = parse_search_query(search_value.strip())
        except ValueError as exc:
            return error_response("INVALID_SEARCH_QUERY", str(exc), status_code=422)

    if search_terms:
        field = search_field.strip().lower()
        allowed_fields = {"all", "url", "host", "path", "query", "title", "technology"}
        if field not in allowed_fields:
            return error_response("INVALID_SEARCH_FIELD", "Choose a valid Search in field.", status_code=422)

        if regex:
            regex_terms = [re.escape(term.value) if term.quoted else term.value for term in search_terms]
            for pattern in regex_terms:
                validation_error = _validate_regex(pattern)
                if validation_error:
                    return error_response("INVALID_REGEX", validation_error, status_code=422)

            raw_connection = None
            if db.bind and db.bind.dialect.name == "sqlite":
                raw_connection = db.connection().connection.driver_connection
                deadline = time.monotonic() + REGEX_QUERY_TIMEOUT_SECONDS
                raw_connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            try:
                regex_candidates = _regex_candidates(db, query, field)
            except OperationalError as exc:
                if "interrupted" in str(exc).lower():
                    return error_response("REGEX_TIMEOUT", "Preparing regex search exceeded the 3 second time limit.", status_code=422)
                raise
            finally:
                if raw_connection is not None:
                    raw_connection.set_progress_handler(None, 0)

            try:
                matches_by_term, match_error = _bounded_regex_matches(regex_terms, regex_candidates)
            except Exception:
                return error_response("REGEX_FAILED", "Regex matching could not be started safely.", status_code=422)
            if match_error:
                error_code = "REGEX_TIMEOUT" if match_error.startswith("Regex search exceeded") else "INVALID_REGEX"
                return error_response(error_code, match_error, status_code=422)

            included = [set(matches_by_term[index]) for index, term in enumerate(search_terms) if not term.excluded]
            if included:
                matching_ids = set.intersection(*included)
            else:
                # Exclusion-only queries begin with every URL that passed the other filters.
                matching_ids = {candidate[0] for candidate in regex_candidates}
            for index, term in enumerate(search_terms):
                if term.excluded:
                    matching_ids.difference_update(matches_by_term[index])

            if db.bind and db.bind.dialect.name == "sqlite":
                connection = db.connection()
                connection.exec_driver_sql("DROP TABLE IF EXISTS temp.regex_url_search_matches")
                connection.exec_driver_sql("CREATE TEMPORARY TABLE regex_url_search_matches (url_id INTEGER PRIMARY KEY)")
                if matching_ids:
                    connection.exec_driver_sql(
                        "INSERT INTO temp.regex_url_search_matches (url_id) VALUES (?)",
                        [(url_id,) for url_id in matching_ids],
                    )
                match_table = table("regex_url_search_matches", column("url_id", Integer))
                query = query.join(match_table, Url.id == match_table.c.url_id)
                regex_temp_table_active = True
            else:
                query = query.filter(Url.id.in_(matching_ids))
                regex_temp_table_active = False
        else:
            text_columns = {
                "url": [Url.normalized_url],
                "host": [Url.hostname],
                "path": [Url.path],
                "query": [Url.query],
            }
            selected_columns = text_columns.get(field, [])
            include_title = field in ("all", "title")
            include_technology = field in ("all", "technology")

            if field == "all":
                selected_columns = [Url.normalized_url, Url.hostname, Url.path, Url.query]
            title_subquery = None
            if include_title:
                title_subquery = _latest_http_titles(db)
                query = query.outerjoin(title_subquery, Url.id == title_subquery.c.url_id)

            for term in search_terms:
                term_matcher = lambda column, value=term.value: _literal_like(column, value)
                search_clauses = [func.coalesce(term_matcher(column), False) for column in selected_columns]
                if include_title:
                    search_clauses.append(func.coalesce(term_matcher(title_subquery.c.title), False))
                if include_technology:
                    search_clauses.append(Url.url_technologies.any(
                        UrlTechnology.technology.has(term_matcher(Technology.name))
                    ))
                term_condition = or_(*search_clauses)
                query = query.filter(~term_condition if term.excluded else term_condition)

    # Sorting
    if sort_by == "oldest":
        query = query.order_by(Url.first_seen.asc(), Url.id.asc())
    elif sort_by == "hostname":
        query = query.order_by(Url.hostname.asc(), Url.path.asc(), Url.id.asc())
    elif sort_by == "path":
        query = query.order_by(Url.path.asc(), Url.id.asc())
    elif sort_by == "status":
        query = query.order_by(HttpResult.status_code.desc().nullslast(), Url.id.asc())
    elif sort_by == "response_time":
        query = query.order_by(HttpResult.response_time_ms.asc().nullslast(), Url.id.asc())
    elif sort_by == "response_size":
        query = query.order_by(HttpResult.response_size_bytes.desc().nullslast(), Url.id.asc())
    else:  # newest default
        query = query.order_by(Url.last_seen.desc(), Url.id.desc())

    # A temp match table keeps large regex result sets server-side without
    # constructing a huge IN clause. Only the final page is loaded as URLs.
    regex_temp_table_active = locals().get("regex_temp_table_active", False)
    raw_connection = None
    if regex and search_value and db.bind and db.bind.dialect.name == "sqlite":
        raw_connection = db.connection().connection.driver_connection
        deadline = time.monotonic() + REGEX_QUERY_TIMEOUT_SECONDS
        raw_connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)

    try:
        # Count matching results before pagination, then load only the requested page.
        total_count = query.distinct().count()
        offset = (page - 1) * limit
        urls = query.distinct().offset(offset).limit(limit).all()
    except OperationalError as exc:
        if regex and "interrupted" in str(exc).lower():
            return error_response(
                "REGEX_TIMEOUT",
                "Regex search exceeded the 3 second time limit. Narrow the search or choose a specific field.",
                status_code=422,
            )
        raise
    finally:
        if raw_connection is not None:
            raw_connection.set_progress_handler(None, 0)
        if regex_temp_table_active:
            db.connection().exec_driver_sql("DROP TABLE IF EXISTS temp.regex_url_search_matches")

    items = []
    for u in urls:
        latest_http = u.http_results[0] if u.http_results else None
        tech_list = [
            {"name": ut.technology.name, "category": ut.technology.category, "confidence": ut.confidence}
            for ut in u.url_technologies
            if ut.technology
        ]

        items.append({
            "id": u.id,
            "target_id": u.target_id,
            "original_url": u.original_url,
            "normalized_url": u.normalized_url,
            "scheme": u.scheme,
            "hostname": u.hostname,
            "port": u.port,
            "path": u.path,
            "query": u.query,
            "sources": [s.strip() for s in u.sources.split(",") if s.strip()],
            "is_interesting": u.is_interesting,
            "is_pinned": u.is_pinned,
            "interesting_reasons": [r.strip() for r in u.interesting_reasons.split(",") if r.strip()] if u.interesting_reasons else [],
            "first_seen": u.first_seen.isoformat() if u.first_seen else None,
            "last_seen": u.last_seen.isoformat() if u.last_seen else None,
            "technologies": tech_list,
            "http": {
                "status_code": latest_http.status_code if latest_http else None,
                "error_message": latest_http.error_message if latest_http else None,
                "response_time_ms": latest_http.response_time_ms if latest_http else None,
                "response_size_bytes": latest_http.response_size_bytes if latest_http else None,
                "content_type": latest_http.content_type if latest_http else None,
                "server": latest_http.server if latest_http else None,
                "http_version": latest_http.http_version if latest_http else None,
                "final_url": latest_http.final_url if latest_http else None,
                "title": latest_http.title if latest_http else None,
                "language": latest_http.language if latest_http else None,
                "description": latest_http.description if latest_http else None,
                "powered_by": latest_http.powered_by if latest_http else None,
                "redirect_chain": json.loads(latest_http.redirect_chain) if (latest_http and latest_http.redirect_chain) else [],
                "headers": json.loads(latest_http.headers_json) if (latest_http and latest_http.headers_json) else {},
                "probed_at": latest_http.probed_at.isoformat() if (latest_http and latest_http.probed_at) else None,
            } if latest_http else None,
        })

    return success_response({
        "items": items,
        "pagination": {
            "total": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit if total_count > 0 else 1,
        },
    })


@router.get("/subdomains")
def list_subdomains(target_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Overview of discovered subdomains with URL count, status, and technologies."""
    query = db.query(
        Url.hostname,
        func.count(Url.id).label("url_count"),
        func.min(Url.first_seen).label("first_seen"),
        func.max(Url.last_seen).label("last_seen"),
    )
    if target_id:
        query = query.filter(Url.target_id == target_id)

    subdomains_summary = query.group_by(Url.hostname).order_by(func.count(Url.id).desc()).all()

    results = []
    for row in subdomains_summary:
        host = row.hostname
        techs = (
            db.query(Technology.name)
            .join(UrlTechnology, Technology.id == UrlTechnology.technology_id)
            .join(Url, UrlTechnology.url_id == Url.id)
            .filter(Url.hostname == host)
            .distinct()
            .all()
        )
        latest_status = (
            db.query(HttpResult.status_code)
            .join(Url, HttpResult.url_id == Url.id)
            .filter(Url.hostname == host, HttpResult.status_code != None)
            .order_by(HttpResult.probed_at.desc())
            .first()
        )

        results.append({
            "hostname": host,
            "url_count": row.url_count,
            "latest_status": latest_status[0] if latest_status else None,
            "technologies": [t[0] for t in techs],
            "first_seen": row.first_seen.isoformat() if row.first_seen else None,
            "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        })

    return success_response(results)


@router.put("/{url_id}/pin")
def set_url_pin(url_id: int, pin_state: PinStateRequest, db: Session = Depends(get_db)):
    u = db.query(Url).filter(Url.id == url_id).first()
    if not u:
        return error_response("NOT_FOUND", "URL not found", status_code=404)

    u.is_pinned = pin_state.pinned
    db.commit()
    db.refresh(u)
    return success_response({"id": u.id, "is_pinned": u.is_pinned})


@router.get("/{url_id}")
def get_url_details(url_id: int, db: Session = Depends(get_db)):
    u = db.query(Url).options(
        joinedload(Url.http_results),
        joinedload(Url.url_technologies).joinedload(UrlTechnology.technology),
    ).filter(Url.id == url_id).first()

    if not u:
        return error_response("NOT_FOUND", "URL not found", status_code=404)

    latest_http = u.http_results[0] if u.http_results else None
    tech_list = [
        {"name": ut.technology.name, "category": ut.technology.category, "confidence": ut.confidence}
        for ut in u.url_technologies
        if ut.technology
    ]

    return success_response({
        "id": u.id,
        "target_id": u.target_id,
        "original_url": u.original_url,
        "normalized_url": u.normalized_url,
        "scheme": u.scheme,
        "hostname": u.hostname,
        "port": u.port,
        "path": u.path,
        "query": u.query,
        "sources": [s.strip() for s in u.sources.split(",") if s.strip()],
        "is_interesting": u.is_interesting,
        "is_pinned": u.is_pinned,
        "interesting_reasons": [r.strip() for r in u.interesting_reasons.split(",") if r.strip()] if u.interesting_reasons else [],
        "first_seen": u.first_seen.isoformat() if u.first_seen else None,
        "last_seen": u.last_seen.isoformat() if u.last_seen else None,
        "technologies": tech_list,
        "http": {
            "status_code": latest_http.status_code if latest_http else None,
            "error_message": latest_http.error_message if latest_http else None,
            "response_time_ms": latest_http.response_time_ms if latest_http else None,
            "response_size_bytes": latest_http.response_size_bytes if latest_http else None,
            "content_type": latest_http.content_type if latest_http else None,
            "server": latest_http.server if latest_http else None,
            "http_version": latest_http.http_version if latest_http else None,
            "final_url": latest_http.final_url if latest_http else None,
            "title": latest_http.title if latest_http else None,
            "language": latest_http.language if latest_http else None,
            "description": latest_http.description if latest_http else None,
            "powered_by": latest_http.powered_by if latest_http else None,
            "redirect_chain": json.loads(latest_http.redirect_chain) if (latest_http and latest_http.redirect_chain) else [],
            "headers": json.loads(latest_http.headers_json) if (latest_http and latest_http.headers_json) else {},
            "probed_at": latest_http.probed_at.isoformat() if (latest_http and latest_http.probed_at) else None,
        } if latest_http else None,
    })


@router.delete("/{url_id}")
def delete_url(url_id: int, db: Session = Depends(get_db)):
    u = db.query(Url).filter(Url.id == url_id).first()
    if not u:
        return error_response("NOT_FOUND", "URL not found", status_code=404)

    db.delete(u)
    db.commit()
    return success_response({"deleted_id": url_id})
