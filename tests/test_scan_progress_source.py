from pathlib import Path


def test_scan_poll_validates_id_before_building_request_path():
    source = (Path(__file__).resolve().parents[1] / "static/js/app.js").read_text()
    guard = source.index("if (!Number.isInteger(validScanId) || validScanId < 1)")
    request = source.index("fetch(`/api/scans/${validScanId}`, { cache: \"no-store\" })")
    assert guard < request
    assert "fetch(`/api/scans/${scanId}`)" not in source
    assert "Number(result.data.id) > 0" in source


def test_check_urls_starts_all_url_scan_without_target_prompt_or_parameter():
    source = (Path(__file__).resolve().parents[1] / "static/js/app.js").read_text()
    start_handler = source[source.index("async startUrlCheck()"):source.index("async loadActiveScan()")]
    assert 'fetch("/api/scans"' in start_handler
    assert 'JSON.stringify({ concurrency: 15, timeout: 10 })' in start_handler
    assert "target_id" not in start_handler
    assert "select a target" not in source.lower()


def test_dashboard_and_url_explorer_scripts_are_versioned_and_report_request_errors():
    root = Path(__file__).resolve().parents[1]
    dashboard_script = (root / "static/js/dashboard.js").read_text()
    urls_script = (root / "static/js/urls.js").read_text()
    dashboard_template = (root / "templates/dashboard.html").read_text()
    urls_template = (root / "templates/urls.html").read_text()

    assert "dashboard.js?v=" in dashboard_template
    assert "urls.js?v=" in urls_template
    assert 'fetch("/api/dashboard/stats"' in dashboard_script
    assert "Dashboard request timed out" in dashboard_script
    assert 'id="dashboard-retry"' in dashboard_template
    assert "No URLs Imported Yet" in urls_script
    assert "URL request timed out" in urls_script
    assert "url-load-retry" in urls_script
    assert "getActiveTarget" not in urls_script
