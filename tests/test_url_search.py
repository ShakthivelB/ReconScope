import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.urls import _bounded_regex_matches, _validate_regex, list_urls, parse_search_query
from database.database import Base
from database.models import HttpResult, Target, Technology, Url, UrlTechnology


@pytest.fixture
def search_db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(autoflush=False, bind=engine)()
    target = Target(name="Search fixture", root_domain="example.test")
    db.add(target)
    db.flush()

    rows = [
        ("api.example.test", "/api/v1/users", "", "Users API", 200, True, True, ["React"]),
        ("example.test", "/v1/api/test", "return=api", "API Reference", 200, False, True, []),
        ("api-login.example.test", "/login", "", "Sign in", 401, False, True, ["React"]),
        ("example.test", "/signin", "", "Account access", 200, True, False, []),
        ("example.test", "/api/v2/users", "", "Version two", 200, False, False, []),
        ("example.test", "/api/v3/users", "", "Version three", 403, False, False, []),
        ("example.test", "/public", "", "API mention in title", 200, False, False, []),
        ("example.test", "/literal", "value=100%", "Literal", 200, False, False, []),
        ("example.test", "/auth/login", "", "Authentication", 200, False, False, []),
        ("example.test", "/api/users", "", "API Users", 200, False, False, []),
        ("example.test", "/api/auth/login", "", "API Auth", 200, False, False, []),
        ("example.test", "/api/logout", "", "API Logout", 200, False, False, []),
        ("example.test", "/api/products", "", "API Products", 200, False, False, []),
        ("example.test", "/admin/api/users", "", "Admin API Users", 200, False, False, []),
        ("example.test", "/api/internal/users", "", "Internal API Users", 200, False, False, []),
        ("example.test", "/oauth/callback", "", "OAuth Callback", 200, False, False, []),
        ("example.test", "/api/oauth/callback", "", "API OAuth Callback", 200, False, False, []),
    ]
    created = []
    technologies = {}
    for host, path, query, title, status, pinned, interesting, tech_names in rows:
        normalized = f"https://{host}{path}" + (f"?{query}" if query else "")
        url = Url(
            target_id=target.id,
            original_url=normalized,
            normalized_url=normalized,
            scheme="https",
            hostname=host,
            path=path,
            query=query,
            sources="Manual",
            is_pinned=pinned,
            is_interesting=interesting,
        )
        db.add(url)
        db.flush()
        created.append(url)
        db.add(HttpResult(url_id=url.id, status_code=status, title=title))
        for tech_name in tech_names:
            technology = technologies.get(tech_name)
            if technology is None:
                technology = Technology(name=tech_name, category="Frontend")
                db.add(technology)
                db.flush()
                technologies[tech_name] = technology
            db.add(UrlTechnology(url_id=url.id, technology_id=technology.id, confidence="high"))
    db.commit()
    by_path = {url.path: url for url in created}
    try:
        yield db, target.id, by_path
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def search_ids(db, target_id, **options):
    response = list_urls(
        target_id=target_id,
        q=options.pop("q", None),
        search=options.pop("search", None),
        search_field=options.pop("search_field", "all"),
        regex=options.pop("regex", False),
        status=options.pop("status", None),
        source=None,
        interesting=options.pop("interesting", None),
        pinned_only=options.pop("pinned_only", False),
        technology=None,
        hostname=None,
        sort_by=options.pop("sort_by", "newest"),
        page=options.pop("page", 1),
        limit=options.pop("limit", 50),
        db=db,
    )
    return response.status_code, json.loads(response.body)


def ids_for(db, target_id, **options):
    status_code, body = search_ids(db, target_id, **options)
    assert status_code == 200
    return {item["id"] for item in body["data"]["items"]}, body["data"]["pagination"]


def test_search_query_parser_supports_include_exclude_and_quotes():
    parse = lambda value: [(term.value, term.excluded, term.quoted) for term in parse_search_query(value)]
    assert parse("api") == [("api", False, False)]
    assert parse("!auth") == [("auth", True, False)]
    assert parse("api !auth !logout") == [
        ("api", False, False), ("auth", True, False), ("logout", True, False),
    ]
    assert parse("api users !auth") == [
        ("api", False, False), ("users", False, False), ("auth", True, False),
    ]
    assert parse("api!test important foo-bar") == [
        ("api!test", False, False), ("important", False, False), ("foo-bar", False, False),
    ]
    assert parse('"api gateway" !"slow login"') == [
        ("api gateway", False, True), ("slow login", True, True),
    ]
    with pytest.raises(ValueError, match="Unclosed quote"):
        parse_search_query('"api gateway')
    with pytest.raises(ValueError, match="too long"):
        parse_search_query("a" * 1025)


def test_multi_term_search_uses_and_and_not_with_selected_field(search_db):
    db, target_id, urls = search_db
    assert ids_for(db, target_id, search="api !auth", search_field="path")[0] == {
        url.id for path, url in urls.items() if "api" in path.lower() and "auth" not in path.lower()
    }
    assert ids_for(db, target_id, search="api !auth !logout", search_field="path")[0] == {
        url.id for path, url in urls.items()
        if "api" in path.lower() and "auth" not in path.lower() and "logout" not in path.lower()
    }
    assert ids_for(db, target_id, search="api users !AUTH", search_field="path")[0] == {
        url.id for path, url in urls.items()
        if "api" in path.lower() and "users" in path.lower() and "auth" not in path.lower()
    }
    assert ids_for(db, target_id, search="^/api/ !^/api/internal/", search_field="path", regex=True)[0] == {
        url.id for path, url in urls.items()
        if path.startswith("/api/") and not path.startswith("/api/internal/")
    }
    assert ids_for(db, target_id, search='"api users"', search_field="title", regex=True)[0] == {
        urls["/api/users"].id, urls["/admin/api/users"].id, urls["/api/internal/users"].id,
    }
    assert ids_for(db, target_id, search='"api users"', search_field="title")[0] == {
        urls["/api/users"].id, urls["/admin/api/users"].id, urls["/api/internal/users"].id,
    }
    excluded_phrase = ids_for(db, target_id, search='!"api users"', search_field="title")[0]
    assert not ({urls["/api/users"].id, urls["/admin/api/users"].id, urls["/api/internal/users"].id} & excluded_phrase)


def test_regex_exclusion_only_queries_match_complement(search_db):
    db, target_id, urls = search_db
    assert ids_for(db, target_id, search="!/oauth", search_field="path", regex=True)[0] == {
        url.id for path, url in urls.items() if "oauth" not in path.lower()
    }
    assert ids_for(db, target_id, search="!/oauth !/login", search_field="path", regex=True)[0] == {
        url.id for path, url in urls.items()
        if "oauth" not in path.lower() and "login" not in path.lower()
    }
    assert ids_for(db, target_id, search="api !/oauth", search_field="path", regex=True)[0] == {
        url.id for path, url in urls.items()
        if "api" in path.lower() and "oauth" not in path.lower()
    }


def test_regex_exclusion_only_combines_with_existing_filters_and_fields(search_db):
    db, target_id, urls = search_db
    for field, value in (("url", "normalized_url"), ("host", "hostname"), ("path", "path")):
        actual, pagination = ids_for(
            db, target_id, search="!/oauth", search_field=field, regex=True, page=1, limit=2
        )
        expected = {
            url.id for url in urls.values()
            if "oauth" not in str(getattr(url, value)).lower()
        }
        assert len(actual) == 2
        assert actual <= expected
        assert pagination["total"] == len(expected)

    assert ids_for(db, target_id, search="!/oauth", search_field="path", regex=True, status="200")[0] == {
        url.id for path, url in urls.items()
        if "oauth" not in path.lower() and path not in ("/api/v3/users", "/login")
    }
    assert ids_for(db, target_id, search="!/oauth", search_field="path", regex=True, interesting=True)[0] == {
        urls["/api/v1/users"].id, urls["/v1/api/test"].id, urls["/login"].id,
    }
    assert ids_for(db, target_id, search="!/oauth", search_field="path", regex=True, pinned_only=True)[0] == {
        urls["/api/v1/users"].id, urls["/signin"].id,
    }


def test_literal_search_is_field_scoped_case_insensitive_and_exact(search_db):
    db, target_id, urls = search_db
    assert ids_for(db, target_id, search="API", search_field="path")[0] == {
        url.id for path, url in urls.items() if "api" in path.lower()
    }
    assert ids_for(db, target_id, search="api", search_field="host")[0] == {urls["/api/v1/users"].id, urls["/login"].id}
    assert ids_for(db, target_id, search="api", search_field="title")[0] == {
        urls["/api/v1/users"].id, urls["/v1/api/test"].id, urls["/public"].id,
        urls["/api/users"].id, urls["/api/auth/login"].id, urls["/api/logout"].id,
        urls["/api/products"].id, urls["/admin/api/users"].id, urls["/api/internal/users"].id,
        urls["/api/oauth/callback"].id,
    }
    assert ids_for(db, target_id, search="react", search_field="technology")[0] == {
        urls["/api/v1/users"].id, urls["/login"].id,
    }
    assert ids_for(db, target_id, search="100%", search_field="query")[0] == {urls["/literal"].id}
    assert ids_for(db, target_id, search="%", search_field="query")[0] == {urls["/literal"].id}
    assert ids_for(db, target_id, search='"/api/v1/"', search_field="url")[0] == {urls["/api/v1/users"].id}
    # Backward-compatible q calls use the explicit All field set.
    _, body = search_ids(db, target_id, q="api")
    assert urls["/public"].id in {item["id"] for item in body["data"]["items"]}


def test_exclusion_search_negates_only_the_selected_field(search_db):
    db, target_id, urls = search_db
    expected_without_auth = {url.id for path, url in urls.items() if "auth" not in path.lower()}
    assert ids_for(db, target_id, search="!auth", search_field="path")[0] == expected_without_auth
    expected_without_auth_path = {url.id for path, url in urls.items() if "/auth" not in path.lower()}
    assert ids_for(db, target_id, search="!/auth")[0] == expected_without_auth_path
    assert ids_for(db, target_id, search="!/auth", regex=True)[0] == expected_without_auth_path
    assert ids_for(db, target_id, search="!/auth", search_field="path")[0] == {
        url.id for path, url in urls.items() if "/auth" not in path
    }
    assert ids_for(db, target_id, search="!/api/", search_field="path")[0] == {
        url.id for path, url in urls.items() if "/api/" not in path
    }
    assert ids_for(db, target_id, search="!/api/", search_field="path", regex=True)[0] == {
        url.id for path, url in urls.items() if "/api/" not in path
    }
    assert ids_for(db, target_id, search="!/api/", search_field="path", status="200")[0] == {
        url.id for path, url in urls.items()
        if "/api/" not in path and path not in ("/login",)
    }


def test_regex_examples_invalid_input_and_safety_restrictions(search_db):
    db, target_id, urls = search_db
    literal_anchor = ids_for(db, target_id, search="^/api/", search_field="path")[0]
    assert literal_anchor == set()
    assert ids_for(db, target_id, search="^/api/", search_field="path", regex=True)[0] == {
        url.id for path, url in urls.items() if path.startswith("/api/")
    }
    assert ids_for(db, target_id, search=r"/api/(v1|v2)/", search_field="path", regex=True)[0] == {
        urls["/api/v1/users"].id, urls["/api/v2/users"].id,
    }
    assert ids_for(db, target_id, search=r"^/API/V[12]+/users$", search_field="path", regex=True)[0] == {
        urls["/api/v1/users"].id, urls["/api/v2/users"].id,
    }
    assert ids_for(db, target_id, search=r"/login|/signin|/oauth", search_field="path", regex=True)[0] == {
        urls["/login"].id, urls["/signin"].id, urls["/auth/login"].id, urls["/api/auth/login"].id,
        urls["/oauth/callback"].id, urls["/api/oauth/callback"].id,
    }
    all_matches, _ = ids_for(db, target_id, search="api", regex=True)
    assert {urls["/api/v1/users"].id, urls["/public"].id, urls["/login"].id} <= all_matches
    for invalid in ("(api", "a" * 257, "api !(auth"):
        status_code, body = search_ids(db, target_id, search=invalid, search_field="path", regex=True)
        assert status_code == 422
        assert body["error"]["code"] == "INVALID_REGEX"

    assert _validate_regex("/api/(v1|v2)/+") is None
    assert _validate_regex(r"\w+\.json$") is None
    matches, timeout_error = _bounded_regex_matches(r"(a|aa)+$", [[1, ["a" * 20000 + "!"]]], timeout=0.1)
    assert matches is None
    assert timeout_error.startswith("Regex search exceeded")


def test_search_combines_with_status_pinning_interesting_and_pagination(search_db):
    db, target_id, urls = search_db
    matched, pagination = ids_for(db, target_id, search="api", search_field="path", status="200")
    assert matched == {
        url.id for path, url in urls.items()
        if "api" in path.lower() and path != "/api/v3/users"
    }
    assert pagination["total"] == 10
    assert ids_for(db, target_id, search="api", search_field="path", pinned_only=True)[0] == {urls["/api/v1/users"].id}
    assert ids_for(db, target_id, search="api", search_field="path", interesting=True)[0] == {
        urls["/api/v1/users"].id, urls["/v1/api/test"].id,
    }
    page_one, page_one_info = ids_for(db, target_id, search="api", search_field="path", page=1, limit=2)
    page_two, page_two_info = ids_for(db, target_id, search="api", search_field="path", page=2, limit=2)
    assert page_one.isdisjoint(page_two)
    assert page_one_info["total"] == page_two_info["total"] == 11
    assert page_one_info["total_pages"] == 6
