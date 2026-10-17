from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship
from .database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Target(Base):
    __tablename__ = "targets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    root_domain = Column(String(255), nullable=False, index=True)
    scope_rules = Column(Text, default="", nullable=False)
    notes = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    recon_imports = relationship("ReconImport", back_populates="target", cascade="all, delete-orphan")
    urls = relationship("Url", back_populates="target", cascade="all, delete-orphan")
    scan_jobs = relationship("ScanJob", back_populates="target", cascade="all, delete-orphan")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(128), primary_key=True)
    value = Column(String(1024), nullable=False)


class ReconImport(Base):
    __tablename__ = "recon_imports"

    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    source_type = Column(String(64), nullable=False, default="Manual")
    raw_count = Column(Integer, default=0, nullable=False)
    new_count = Column(Integer, default=0, nullable=False)
    duplicate_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    target = relationship("Target", back_populates="recon_imports")
    url_links = relationship("ImportUrl", back_populates="recon_import", cascade="all, delete-orphan")


class ImportUrl(Base):
    """Explicit URL ownership links for each uploaded import."""
    __tablename__ = "import_urls"

    id = Column(Integer, primary_key=True, index=True)
    import_id = Column(Integer, ForeignKey("recon_imports.id", ondelete="CASCADE"), nullable=False, index=True)
    url_id = Column(Integer, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)

    recon_import = relationship("ReconImport", back_populates="url_links")
    url = relationship("Url", back_populates="import_links")

    __table_args__ = (Index("ix_import_urls_pair", "import_id", "url_id", unique=True),)


class Url(Base):
    __tablename__ = "urls"

    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=True, index=True)
    original_url = Column(Text, nullable=False)
    normalized_url = Column(Text, nullable=False, index=True)
    scheme = Column(String(16), nullable=False, default="http")
    hostname = Column(String(255), nullable=False, index=True)
    port = Column(Integer, nullable=True)
    path = Column(Text, default="/", nullable=False)
    query = Column(Text, default="", nullable=False)
    sources = Column(String(255), default="Manual", nullable=False)  # Comma-separated (e.g. "GAU, Katana")
    is_pinned = Column(Boolean, default=False, server_default="0", nullable=False)
    import_tracking_complete = Column(Boolean, default=False, server_default="0", nullable=False)
    is_interesting = Column(Boolean, default=False, nullable=False, index=True)
    interesting_reasons = Column(Text, default="", nullable=False)
    first_seen = Column(DateTime, default=utcnow, nullable=False, index=True)
    last_seen = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False, index=True)

    target = relationship("Target", back_populates="urls")
    http_results = relationship("HttpResult", back_populates="url", cascade="all, delete-orphan", order_by="desc(HttpResult.probed_at)")
    url_technologies = relationship("UrlTechnology", back_populates="url", cascade="all, delete-orphan")
    import_links = relationship("ImportUrl", back_populates="url", cascade="all, delete-orphan")
    scan_job_links = relationship("ScanJobUrl", back_populates="url", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_urls_target_normalized", "target_id", "normalized_url"),
        Index("ix_urls_target_hostname", "target_id", "hostname"),
        Index("ix_urls_target_interesting", "target_id", "is_interesting"),
    )


class HttpResult(Base):
    __tablename__ = "http_results"

    id = Column(Integer, primary_key=True, index=True)
    url_id = Column(Integer, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    status_code = Column(Integer, nullable=True, index=True)
    error_message = Column(Text, nullable=True)  # Explicit failure type (timeout, DNS error, connection error, etc.)
    response_time_ms = Column(Float, nullable=True)
    response_size_bytes = Column(Integer, nullable=True)
    content_type = Column(String(128), nullable=True)
    server = Column(String(128), nullable=True, index=True)
    http_version = Column(String(32), nullable=True)
    final_url = Column(Text, nullable=True)
    redirect_chain = Column(Text, default="[]", nullable=False)  # JSON array string
    title = Column(Text, nullable=True)
    language = Column(String(64), nullable=True)
    description = Column(Text, nullable=True)
    powered_by = Column(String(128), nullable=True)
    headers_json = Column(Text, default="{}", nullable=False)  # JSON object string
    probed_at = Column(DateTime, default=utcnow, nullable=False, index=True)

    url = relationship("Url", back_populates="http_results")


class Technology(Base):
    __tablename__ = "technologies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    category = Column(String(100), default="General", nullable=False)

    url_technologies = relationship("UrlTechnology", back_populates="technology", cascade="all, delete-orphan")


class UrlTechnology(Base):
    __tablename__ = "url_technologies"

    id = Column(Integer, primary_key=True, index=True)
    url_id = Column(Integer, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    technology_id = Column(Integer, ForeignKey("technologies.id", ondelete="CASCADE"), nullable=False, index=True)
    confidence = Column(String(32), default="medium", nullable=False)

    url = relationship("Url", back_populates="url_technologies")
    technology = relationship("Technology", back_populates="url_technologies")

    __table_args__ = (
        Index("ix_url_tech_unique", "url_id", "technology_id", unique=True),
    )




class ScanJob(Base):
    __tablename__ = "scan_jobs"

    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=True, index=True)
    status = Column(String(32), default="queued", nullable=False, index=True)  # queued, running, paused, completed, cancelled, failed
    total_urls = Column(Integer, default=0, nullable=False)
    completed_urls = Column(Integer, default=0, nullable=False)
    status_2xx = Column(Integer, default=0, nullable=False)
    status_3xx = Column(Integer, default=0, nullable=False)
    status_401 = Column(Integer, default=0, nullable=False)
    status_403 = Column(Integer, default=0, nullable=False)
    status_404 = Column(Integer, default=0, nullable=False)
    status_5xx = Column(Integer, default=0, nullable=False)
    timeouts = Column(Integer, default=0, nullable=False)
    errors = Column(Integer, default=0, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    target = relationship("Target", back_populates="scan_jobs")
    scan_results = relationship("ScanResult", back_populates="scan_job", cascade="all, delete-orphan")
    url_links = relationship("ScanJobUrl", back_populates="scan_job", cascade="all, delete-orphan")


class ScanJobUrl(Base):
    """Persist the URL membership snapshot for a scan, including queued URLs."""
    __tablename__ = "scan_job_urls"

    id = Column(Integer, primary_key=True, index=True)
    scan_job_id = Column(Integer, ForeignKey("scan_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    url_id = Column(Integer, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)

    scan_job = relationship("ScanJob", back_populates="url_links")
    url = relationship("Url", back_populates="scan_job_links")

    __table_args__ = (Index("ix_scan_job_urls_pair", "scan_job_id", "url_id", unique=True),)


class ScanResult(Base):
    __tablename__ = "scan_results"

    id = Column(Integer, primary_key=True, index=True)
    scan_job_id = Column(Integer, ForeignKey("scan_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    url_id = Column(Integer, ForeignKey("urls.id", ondelete="CASCADE"), nullable=False, index=True)
    status_code = Column(Integer, nullable=True)
    response_time = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    scanned_at = Column(DateTime, default=utcnow, nullable=False)

    scan_job = relationship("ScanJob", back_populates="scan_results")
    url = relationship("Url")
