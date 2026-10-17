import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.dashboard import router as dashboard_router
from api.imports import router as imports_router
from api.previews import router as previews_router
from api.scans import router as scans_router
from api.settings import router as settings_router
from api.targets import router as targets_router
from api.urls import router as urls_router
from database.database import init_db
from services.browser_preview import close_browser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("reconscope")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB tables
    logger.info("Initializing ReconScope database...")
    init_db()
    logger.info("ReconScope ready on local interface.")
    yield
    # Shutdown: Clean up Playwright instances
    logger.info("Shutting down browser contexts...")
    await close_browser()
    logger.info("ReconScope stopped.")


app = FastAPI(
    title="ReconScope",
    description="Local-first reconnaissance intelligence dashboard for authorized bug bounty workflows",
    version="1.0.0",
    lifespan=lifespan,
)

# Static and Templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Register API routers
app.include_router(targets_router)
app.include_router(imports_router)
app.include_router(urls_router)
app.include_router(scans_router)
app.include_router(settings_router)
app.include_router(previews_router)
app.include_router(dashboard_router)


# Web UI Page Routes
@app.get("/", response_class=HTMLResponse)
def index_redirect():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def page_dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html", context={"active_page": "dashboard"})


@app.get("/targets", response_class=HTMLResponse)
def page_targets(request: Request):
    # Keep old bookmarks from breaking while removing Targets from the workflow.
    return RedirectResponse(url="/imports", status_code=307)


@app.get("/imports", response_class=HTMLResponse)
def page_imports(request: Request):
    return templates.TemplateResponse(request=request, name="imports.html", context={"active_page": "imports"})


@app.get("/urls", response_class=HTMLResponse)
def page_urls(request: Request):
    return templates.TemplateResponse(request=request, name="urls.html", context={"active_page": "urls"})


@app.get("/scans", response_class=HTMLResponse)
def legacy_scans_redirect():
    # Keep old bookmarks useful while removing the standalone scans page.
    return RedirectResponse(url="/urls", status_code=307)


@app.get("/technologies", response_class=HTMLResponse)
def page_technologies(request: Request):
    return templates.TemplateResponse(request=request, name="technologies.html", context={"active_page": "technologies"})


@app.get("/settings", response_class=HTMLResponse)
def page_settings(request: Request):
    return templates.TemplateResponse(request=request, name="settings.html", context={"active_page": "settings"})


if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.py:app" if False else app, host=host, port=port, log_level="info")
