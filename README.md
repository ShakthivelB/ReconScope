# ReconScope

**ReconScope** is a local-first reconnaissance intelligence dashboard for authorized bug-bounty and security-testing workflows.

It transforms noisy outputs from tools such as `subfinder`, `amass`, `gau`, `waybackurls`, `katana`, and `httpx` into a searchable, visual, and organized attack-surface triage workspace.

---

## Key Features & Invariants

- **Local-First & Offline Resilience**: Runs locally on `127.0.0.1:8000` with SQLite storage. No cloud dependencies, no SaaS authentication.
- **Zero Screenshot Persistence (Strict Invariant)**: Webpage previews are rendered on-demand via Playwright directly into in-memory byte buffers and streamed to the client. Previews are never written to disk, cache directories, or SQLite.
- **Network Safety**: HTTP and HTTPS URLs are checked against private, loopback, link-local, metadata, and DNS-resolved prohibited addresses on every redirect. Public URLs can span multiple domains without target setup.
- **URL Checks & Previews**: Imported HTTP and HTTPS URLs can be checked and previewed across multiple domains, with manual redirect handling and bounded request sizes.
- **Import History Management**: Individual import-history records can be deleted while preserving URLs, scan results, and pins. Uploaded source files are not stored by ReconScope.
- **Recon Triage & Normalization**: Deterministic URL normalization and deduplication across multiple discovery sources with triage flags for high-signal endpoints (`/admin`, `/api/v1`, `/swagger-ui`, `redirect=`, `token=`).
- **Responsive Mobile-First Interface**: Full touch-target accessibility ($\ge 44\text{px}$), drawer navigation, and clean cyber dark workstation UI.
- **Recon Session Diffs**: Compare probe sessions to instantly highlight new URLs, removed endpoints, and HTTP status code changes.

---

## Requirements

- **Python 3.11+**
- Chromium browser dependencies (installed automatically via Playwright)

---

## Linux / macOS Installation

### Automated Setup
```bash
chmod +x setup.sh
./setup.sh
```

### Manual Setup
```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright Chromium headless browser
playwright install chromium

# 4. Initialize Database
python3 -c "from database.database import init_db; init_db()"
```

---

## Windows Installation (PowerShell)

```powershell
# 1. Create and activate virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install Playwright Chromium
playwright install chromium

# 4. Initialize Database
python -c "from database.database import init_db; init_db()"
```

---

## Running the Application

```bash
# Activate virtual environment if not already activated
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\Activate.ps1  # Windows

# Start FastAPI server
python app.py
```

Then open your browser and navigate to:
```text
http://127.0.0.1:8000
```

---

## Example Recon Workflow

### 1. Discover Subdomains and Historical URLs
```bash
# Subdomain enumeration
subfinder -d example.com -o subdomains.txt

# Passive URL discovery
gau example.com > gau.txt

# Active web crawling
katana -u https://example.com -o katana.txt
```

### 2. Ingest Into ReconScope
1. Go to **Import Recon** (`http://127.0.0.1:8000/imports`).
2. Upload or paste URLs directly. Imports can contain URLs from multiple public domains; no target setup is needed.
3. Drag & drop `subdomains.txt`, `gau.txt`, or `katana.txt` into the upload zone or paste raw URLs directly.
4. Select the matching source tool and click **Start Import & Deduplication**, then use **Check URLs** and URL Explorer.

### 3. Probe and Triage
1. Click **Check URLs** to check all imported URLs.
2. Follow progress in the compact status widget while continuing to browse. Dashboard counts update as results are saved.
3. Open **URL Explorer** (`http://127.0.0.1:8000/urls`) to filter by status, search paths (e.g. `/api/`), inspect response details, or expand **Web Preview** for live in-memory Playwright rendering.

---

## Testing

Run the automated pytest suite:
```bash
.venv/bin/pytest -v
```
