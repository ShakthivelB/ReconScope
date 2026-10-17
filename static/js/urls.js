/**
 * URL Explorer JavaScript - Search, Filtering, Pagination, Inline Web Preview & Triage
 */

const UrlsModule = {
  currentPage: 1,
  currentLimit: 50,
  currentStatus: "",
  isInterestingOnly: false,
  isPinnedOnly: false,
  currentSort: "newest",
  currentPagination: null,
  searchQuery: "",
  searchField: "all",
  regexEnabled: false,
  hostnameFilter: new URLSearchParams(window.location.search).get("hostname") || "",
  searchDebounceTimer: null,
  activePreviewBlobs: {}, // Tracks in-memory blob URLs per urlId to revoke on collapse
  requestSequence: 0,
  requestController: null,
  globalListenersBound: false,

  escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  },

  async init() {
    if (!document.getElementById("urls-container")) return;
    const searchParams = new URLSearchParams(window.location.search);
    this.currentPage = 1;
    this.currentLimit = 50;
    this.currentStatus = "";
    this.isInterestingOnly = false;
    this.isPinnedOnly = false;
    this.currentSort = "newest";
    this.searchQuery = "";
    this.searchField = "all";
    this.regexEnabled = false;
    this.hostnameFilter = searchParams.get("hostname") || "";
    this.bindEvents();
    if (!this.globalListenersBound) {
      window.addEventListener("scanUpdated", () => this.loadUrls());
      window.addEventListener("storage", (event) => {
        if (event.key === "reconscope_data_changed") {
          this.currentPage = 1;
          this.loadUrls();
        }
      });
      this.globalListenersBound = true;
    }
    await this.loadUrls();
  },

  unmount() {
    clearTimeout(this.searchDebounceTimer);
    this.requestSequence += 1;
    this.requestController?.abort();
    this.cleanupAllBlobs();
  },

  bindEvents() {
    // Debounced search
    const searchInput = document.getElementById("url-search-input");
    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        clearTimeout(this.searchDebounceTimer);
        this.searchDebounceTimer = setTimeout(() => {
          this.searchQuery = e.target.value.trim();
          this.currentPage = 1;
          this.loadUrls();
        }, 300);
      });
    }

    const searchField = document.getElementById("url-search-field");
    if (searchField) {
      searchField.addEventListener("change", (e) => {
        this.searchField = e.target.value;
        this.currentPage = 1;
        this.loadUrls();
      });
    }

    const regexToggle = document.getElementById("url-search-regex");
    if (regexToggle) {
      regexToggle.addEventListener("change", (e) => {
        this.regexEnabled = e.target.checked;
        this.currentPage = 1;
        this.loadUrls();
      });
    }

    // Status filter buttons
    document.querySelectorAll(".status-filter-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".status-filter-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        this.currentStatus = btn.dataset.status || "";
        this.currentPage = 1;
        this.loadUrls();
      });
    });

    // Interesting toggle
    const interestingBtn = document.getElementById("filter-interesting-btn");
    if (interestingBtn) {
      interestingBtn.addEventListener("click", () => {
        this.isInterestingOnly = !this.isInterestingOnly;
        interestingBtn.classList.toggle("active", this.isInterestingOnly);
        this.currentPage = 1;
        this.loadUrls();
      });
    }

    const pinnedBtn = document.getElementById("filter-pinned-btn");
    if (pinnedBtn) {
      pinnedBtn.addEventListener("click", () => {
        this.isPinnedOnly = !this.isPinnedOnly;
        pinnedBtn.classList.toggle("active", this.isPinnedOnly);
        pinnedBtn.setAttribute("aria-pressed", String(this.isPinnedOnly));
        this.currentPage = 1;
        this.loadUrls();
      });
    }

    // Sort select
    const sortSelect = document.getElementById("sort-select");
    if (sortSelect) {
      sortSelect.addEventListener("change", (e) => {
        this.currentSort = e.target.value;
        this.currentPage = 1;
        this.loadUrls();
      });
    }

    // Limit select
    const limitSelect = document.getElementById("limit-select");
    if (limitSelect) {
      limitSelect.addEventListener("change", (e) => {
        this.currentLimit = parseInt(e.target.value, 10);
        this.currentPage = 1;
        this.loadUrls();
      });
    }

    document.getElementById("url-load-retry")?.addEventListener("click", () => this.loadUrls());
  },

  async loadUrls() {
    const listEl = document.getElementById("urls-container");
    const countEl = document.getElementById("url-results-count");
    if (!listEl) return;

    this.requestController?.abort();
    const controller = new AbortController();
    this.requestController = controller;
    const requestId = ++this.requestSequence;
    const timeout = setTimeout(() => controller.abort(), 15000);

    if (listEl.dataset.hasLoaded !== "true") {
      listEl.innerHTML = '<div style="text-align:center; padding: 40px; color: var(--text-muted);"><div class="spinner" style="margin: 0 auto 12px;"></div>Loading reconnaissance endpoints...</div>';
    }
    if (countEl) countEl.textContent = listEl.dataset.hasLoaded === "true" ? "Refreshing endpoints..." : "Loading endpoints...";
    const loadError = document.getElementById("url-load-error");
    const loadErrorMessage = document.getElementById("url-load-error-message");
    if (loadError) loadError.hidden = true;

    const searchErrorEl = document.getElementById("url-search-error");
    if (searchErrorEl) searchErrorEl.textContent = "";
    const params = new URLSearchParams({
      page: this.currentPage,
      limit: this.currentLimit,
      sort_by: this.currentSort,
    });

    if (this.searchQuery) {
      params.append("search", this.searchQuery);
      params.append("search_field", this.searchField);
      params.append("regex", String(this.regexEnabled));
    }
    if (this.currentStatus) params.append("status", this.currentStatus);
    if (this.isInterestingOnly) params.append("interesting", "true");
    if (this.isPinnedOnly) params.append("pinned_only", "true");
    if (this.hostnameFilter) params.append("hostname", this.hostnameFilter);

    try {
      const resp = await fetch(`/api/urls?${params.toString()}`, { signal: controller.signal, cache: "no-store" });
      const json = await resp.json();
      if (!resp.ok || !json.success) throw new Error(json.error?.message || "Search request failed");

      const items = json.data?.items;
      const pagination = json.data?.pagination;
      if (!Array.isArray(items) || !pagination || !Number.isFinite(Number(pagination.total))) {
        throw new Error("URL API returned an incomplete response.");
      }
      if (requestId !== this.requestSequence) return;
      this.currentPagination = pagination;
      this.cleanupAllBlobs();
      listEl.dataset.hasLoaded = "true";

      if (countEl) {
        countEl.textContent = `Showing ${items.length} of ${pagination.total.toLocaleString()} URLs`;
      }

      if (items.length === 0) {
        const hasFilters = Boolean(
          this.searchQuery || this.currentStatus || this.isInterestingOnly || this.isPinnedOnly || this.hostnameFilter || this.currentPage > 1
        );
        const emptyTitle = hasFilters ? "No URLs Match Current Filters" : "No URLs Imported Yet";
        const emptyDescription = hasFilters
          ? "Try clearing filters or import more recon data."
          : "Import a .txt, .csv, or supported recon output to get started.";
        listEl.innerHTML = `
          <div class="empty-state">
            <div class="empty-icon">🔍</div>
            <div class="empty-title">${emptyTitle}</div>
            <div class="empty-desc">${emptyDescription}</div>
          </div>
        `;
        this.renderPagination(pagination);
        return;
      }

      listEl.innerHTML = items.map((u) => this.renderUrlCard(u)).join("");
      this.renderPagination(pagination);
    } catch (e) {
      if (requestId !== this.requestSequence) return;
      if (e.name === "AbortError" && controller.signal.aborted) {
        if (loadErrorMessage) loadErrorMessage.textContent = "URL request timed out after 15 seconds.";
      } else if (loadErrorMessage) {
        loadErrorMessage.textContent = e.message || "Could not load URLs.";
      }
      if (loadError) loadError.hidden = false;
      if (countEl && listEl.dataset.hasLoaded !== "true") countEl.textContent = "Could not load URLs";
      else if (countEl) countEl.textContent = "Could not refresh URLs; showing previous results";
      if (listEl.dataset.hasLoaded !== "true") {
        listEl.innerHTML = '<div class="empty-state"><div class="empty-icon">⚠</div><div class="empty-title">URLs could not be loaded</div><div class="empty-desc">Check the error above and retry.</div></div>';
      }
    } finally {
      clearTimeout(timeout);
      if (requestId === this.requestSequence) this.requestController = null;
    }
  },

  renderUrlCard(u) {
    const http = u.http;
    let statusBadgeClass = "status-unprobed";
    let statusText = "○ Not checked";

    if (http) {
      const sc = http.status_code;
      if (sc !== null && sc !== undefined && sc > 0) {
        if (sc >= 200 && sc < 300) {
          statusBadgeClass = "status-2xx";
          statusText = `🟢 ${sc}`;
        } else if (sc >= 300 && sc < 400) {
          statusBadgeClass = "status-3xx";
          statusText = `🟡 ${sc}`;
        } else if (sc === 401) {
          statusBadgeClass = "status-401";
          statusText = `🟣 401`;
        } else if (sc === 403) {
          statusBadgeClass = "status-403";
          statusText = `🟠 403`;
        } else if (sc === 404) {
          statusBadgeClass = "status-404";
          statusText = `🔴 404`;
        } else if (sc === 429) {
          statusBadgeClass = "status-403";
          statusText = `🟠 429`;
        } else if (sc >= 500) {
          statusBadgeClass = "status-5xx";
          statusText = `🔴 ${sc}`;
        } else {
          statusBadgeClass = "status-3xx";
          statusText = `🔵 ${sc}`;
        }
      } else {
        const err = http.error_message || "Error";
        if (err.toLowerCase().includes("timeout")) {
          statusBadgeClass = "status-timeout";
          statusText = "⚫ Timeout";
        } else {
          statusBadgeClass = "status-error";
          statusText = `⚫ ${err}`;
        }
      }
    }

    const sizeFormatted = (http && http.response_size_bytes) ? (http.response_size_bytes > 1048576 ? `${(http.response_size_bytes / 1048576).toFixed(1)} MB` : `${(http.response_size_bytes / 1024).toFixed(1)} KB`) : "";
    const timeFormatted = (http && http.response_time_ms) ? `${Math.round(http.response_time_ms)}ms` : "";

    return `
      <div class="url-card" id="url-card-${u.id}">
        <div class="url-card-header">
          <div class="url-title-group">
            <div style="display:flex; align-items:center; gap:8px;">
              <span class="status-badge ${statusBadgeClass}">${this.escapeHtml(statusText)}</span>
              <a href="${this.escapeHtml(u.normalized_url)}" target="_blank" rel="noopener noreferrer" class="url-link-line">${this.escapeHtml(u.normalized_url)}</a>
            </div>
            ${(http && http.title) ? `<div class="url-page-title">${this.escapeHtml(http.title)}</div>` : ""}
          </div>
        </div>

        <div class="url-meta-chips">
          ${timeFormatted ? `<span class="chip">⏱ ${timeFormatted}</span>` : ""}
          ${sizeFormatted ? `<span class="chip">📦 ${sizeFormatted}</span>` : ""}
          ${(http && http.server) ? `<span class="chip">🖥 ${this.escapeHtml(http.server)}</span>` : ""}
          ${u.sources.map((s) => `<span class="chip">📁 ${this.escapeHtml(s)}</span>`).join("")}
          ${u.technologies.map((t) => `<span class="chip chip-tech">⚡ ${this.escapeHtml(t.name)}</span>`).join("")}
          ${u.is_interesting ? `<span class="chip chip-interesting">⭐ Interesting (${this.escapeHtml(u.interesting_reasons.join(", "))})</span>` : ""}
          <button type="button" class="pill-btn pin-url-btn${u.is_pinned ? " active" : ""}" data-pinned="${u.is_pinned ? "true" : "false"}" aria-pressed="${u.is_pinned ? "true" : "false"}" onclick="UrlsModule.togglePin(${u.id}, this)">${u.is_pinned ? "★ Pinned" : "☆ Pin"}</button>
        </div>

        <!-- Separate Dropdown Controls -->
        <div class="card-dropdowns-row">
          <!-- 1. Inline Web Preview Toggle Button -->
          <button class="dropdown-toggle-btn" id="preview-toggle-btn-${u.id}" onclick="UrlsModule.toggleInlinePreview(${u.id})">
            ▶ Web Preview
          </button>

          <!-- 1. Inline Web Preview Expandable Container -->
          <div class="url-preview-panel" id="preview-panel-${u.id}">
            <div id="preview-loading-${u.id}" style="display:flex; flex-direction:column; align-items:center; gap:8px; padding:24px; color:var(--text-muted);">
              <div class="spinner"></div>
              <div style="font-size:12px;">Rendering live webpage via Playwright...</div>
            </div>
            <img id="preview-img-${u.id}" class="inline-preview-img" style="display:none;" alt="Webpage preview" />
            <div id="preview-metadata-${u.id}" class="preview-metadata" aria-live="polite"></div>
            <div id="preview-error-${u.id}" style="display:none; color:var(--accent-red); font-size:12px; padding:16px;"></div>
            <div style="display:flex; justify-content:space-between; align-items:center; width:100%; margin-top:8px; font-size:11px; color:var(--text-muted);">
              <span>🔒 In-memory preview (Not saved to disk)</span>
              <button class="btn btn-sm btn-secondary" onclick="UrlsModule.reloadInlinePreview(${u.id})">↻ Reload</button>
            </div>
          </div>

          <!-- 2. Inline Details Toggle Button -->
          <button class="dropdown-toggle-btn" id="details-toggle-btn-${u.id}" onclick="UrlsModule.toggleDetails(${u.id})">
            ▼ Details
          </button>

          <!-- 2. Inline Details Container -->
          <div class="url-details-panel" id="details-panel-${u.id}">
            <div class="details-grid">
              <div>
                <div class="detail-section-title">HTTP Details</div>
                <div>Status: ${(http && http.status_code !== null) ? http.status_code : (http ? this.escapeHtml(http.error_message || "Error") : "Not checked")}</div>
                <div>Time: ${timeFormatted || "N/A"}</div>
                <div>Size: ${sizeFormatted || "N/A"}</div>
                <div>HTTP Version: ${(http && http.http_version) ? http.http_version : "N/A"}</div>
                <div>Final URL: ${(http && http.final_url) ? this.escapeHtml(http.final_url) : this.escapeHtml(u.normalized_url)}</div>
                <div>Redirect chain:</div>
                <div class="detail-code-block">${(http && http.redirect_chain && http.redirect_chain.length) ? this.escapeHtml(http.redirect_chain.map((hop) => `${hop.status} ${hop.url} → ${hop.location}`).join("\n")) : "None"}</div>
              </div>
              <div>
                <div class="detail-section-title">Page & Server</div>
                <div>Title: ${(http && http.title) ? this.escapeHtml(http.title) : "None"}</div>
                <div>Content-Type: ${(http && http.content_type) ? this.escapeHtml(http.content_type) : "None"}</div>
                <div>Language: ${(http && http.language) ? this.escapeHtml(http.language) : "None"}</div>
                <div>Description: ${(http && http.description) ? this.escapeHtml(http.description) : "None"}</div>
                <div>Server: ${(http && http.server) ? this.escapeHtml(http.server) : "None"}</div>
                <div>Powered-By: ${(http && http.powered_by) ? this.escapeHtml(http.powered_by) : "None"}</div>
                <div>First Seen: ${new Date(u.first_seen).toLocaleString()}</div>
                <div>Last Seen: ${new Date(u.last_seen).toLocaleString()}</div>
              </div>
            </div>

            <div class="detail-section-title">Technologies</div>
            <div>${u.technologies.length ? u.technologies.map((t) => this.escapeHtml(t.name)).join(", ") : "None detected"}</div>
            <div class="detail-section-title" style="margin-top:8px;">Sources</div>
            <div>${u.sources.length ? u.sources.map((source) => this.escapeHtml(source)).join(", ") : "Unknown"}</div>

            ${u.query ? `
              <div style="margin-bottom:8px;">
                <div class="detail-section-title">Query Parameters</div>
                <div class="detail-code-block">${this.escapeHtml(u.query)}</div>
              </div>
            ` : ""}

            ${(http && http.headers && Object.keys(http.headers).length > 0) ? `
              <div>
                <div class="detail-section-title">Response Headers</div>
                <div class="detail-code-block">${this.escapeHtml(Object.entries(http.headers).map(([k, v]) => `${k}: ${v}`).join("\n"))}</div>
              </div>
            ` : ""}
          </div>
        </div>
      </div>
    `;
  },

  // Toggle Inline Webpage Preview
  async toggleInlinePreview(urlId) {
    const panel = document.getElementById(`preview-panel-${urlId}`);
    const btn = document.getElementById(`preview-toggle-btn-${urlId}`);
    if (!panel || !btn) return;

    const isOpen = panel.classList.contains("open");

    if (isOpen) {
      // Collapse preview
      panel.classList.remove("open");
      btn.innerHTML = "▶ Web Preview";

      // Immediately revoke blob URL to release in-memory bytes
      if (this.activePreviewBlobs[urlId]) {
        URL.revokeObjectURL(this.activePreviewBlobs[urlId]);
        delete this.activePreviewBlobs[urlId];
      }
      const img = document.getElementById(`preview-img-${urlId}`);
      if (img) img.src = "";
      const metadata = document.getElementById(`preview-metadata-${urlId}`);
      if (metadata) metadata.replaceChildren();
    } else {
      // Expand preview
      panel.classList.add("open");
      btn.innerHTML = "▼ Web Preview";

      // If not yet loaded, render on demand
      if (!this.activePreviewBlobs[urlId]) {
        await this.loadPreviewImage(urlId);
      }
    }
  },

  async reloadInlinePreview(urlId) {
    if (this.activePreviewBlobs[urlId]) {
      URL.revokeObjectURL(this.activePreviewBlobs[urlId]);
      delete this.activePreviewBlobs[urlId];
    }
    await this.loadPreviewImage(urlId);
  },

  async loadPreviewImage(urlId) {
    const loading = document.getElementById(`preview-loading-${urlId}`);
    const img = document.getElementById(`preview-img-${urlId}`);
    const errorEl = document.getElementById(`preview-error-${urlId}`);
    const metadataEl = document.getElementById(`preview-metadata-${urlId}`);

    if (loading) loading.style.display = "flex";
    if (img) img.style.display = "none";
    if (errorEl) errorEl.style.display = "none";
    if (metadataEl) metadataEl.replaceChildren();

    try {
      const resp = await fetch(`/api/urls/${urlId}/preview`);
      if (!resp.ok) {
        const errJson = await resp.json().catch(() => ({}));
        throw new Error(errJson.error?.message || "Failed to render preview");
      }
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const panel = document.getElementById(`preview-panel-${urlId}`);
      if (!panel || !panel.classList.contains("open")) {
        URL.revokeObjectURL(blobUrl);
        return;
      }
      this.activePreviewBlobs[urlId] = blobUrl;

      if (metadataEl) {
        const decodeHeader = (value) => {
          try { return decodeURIComponent(value || ""); } catch (_) { return value || ""; }
        };
        const requestedUrl = decodeHeader(resp.headers.get("X-Preview-Requested-Url"));
        const finalUrl = decodeHeader(resp.headers.get("X-Preview-Final-Url"));
        const title = decodeHeader(resp.headers.get("X-Preview-Title"));
        const status = Number.parseInt(resp.headers.get("X-Preview-Status"), 10);
        const contentType = decodeHeader(resp.headers.get("X-Preview-Content-Type"));
        let responseHeaders = {};
        try {
          responseHeaders = JSON.parse(decodeHeader(resp.headers.get("X-Preview-Response-Headers")) || "{}");
        } catch (_) {
          responseHeaders = {};
        }
        const redirectCount = Number.parseInt(resp.headers.get("X-Preview-Redirect-Count"), 10) || 0;
        const mainFrameNavigated = resp.headers.get("X-Preview-Main-Frame-Navigated") === "true";
        const browser = decodeHeader(resp.headers.get("X-Preview-Browser-Version"));
        const settled = resp.headers.get("X-Preview-Settled") === "true";
        let redirectChain = [];
        try {
          redirectChain = JSON.parse(decodeHeader(resp.headers.get("X-Preview-Redirect-Chain")) || "[]");
        } catch (_) {
          redirectChain = [];
        }
        const summary = document.createElement("div");
        summary.textContent = [title && `Title: ${title}`, Number.isFinite(status) && `Browser Preview HTTP ${status}`, contentType, browser && `Chromium ${browser}`, `Render ${settled ? "settled" : "captured at time limit"}`].filter(Boolean).join(" · ");
        metadataEl.appendChild(summary);
        if (Number.isFinite(status) && status >= 400) {
          const warning = document.createElement("div");
          warning.style.color = "var(--accent-red)";
          warning.textContent = `⚠ Browser Preview returned HTTP ${status}. The HTTP checker result is shown separately above.`;
          metadataEl.appendChild(warning);
        }
        if (requestedUrl && requestedUrl !== finalUrl) {
          const requested = document.createElement("div");
          requested.textContent = `Requested URL: ${requestedUrl}`;
          metadataEl.appendChild(requested);
        }
        const navigation = document.createElement("div");
        navigation.textContent = `Main frame navigated: ${mainFrameNavigated ? "Yes" : "No"} · Redirects: ${redirectCount}`;
        metadataEl.appendChild(navigation);
        for (const hop of redirectChain) {
          const redirect = document.createElement("div");
          redirect.textContent = `${hop.status} ${hop.url} → ${hop.location}`;
          metadataEl.appendChild(redirect);
        }
        const safeHeaderEntries = Object.entries(responseHeaders);
        if (safeHeaderEntries.length) {
          const headers = document.createElement("div");
          headers.textContent = `Browser response headers: ${safeHeaderEntries.map(([key, value]) => `${key}: ${value}`).join(" · ")}`;
          metadataEl.appendChild(headers);
        }
        if (finalUrl) {
          const link = document.createElement("a");
          link.href = finalUrl;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = `Final URL: ${finalUrl}`;
          metadataEl.appendChild(link);
        }
      }

      if (img) {
        img.onload = () => {
          if (loading) loading.style.display = "none";
          img.style.display = "block";
        };
        img.onerror = () => {
          if (this.activePreviewBlobs[urlId] === blobUrl) {
            URL.revokeObjectURL(blobUrl);
            delete this.activePreviewBlobs[urlId];
          }
          img.onerror = null;
          img.removeAttribute("src");
          img.style.display = "none";
          if (loading) loading.style.display = "none";
          if (panel.classList.contains("open") && errorEl) {
            errorEl.textContent = "⚠ The preview image could not be displayed. Expand Web Preview to try again.";
            errorEl.style.display = "block";
          }
        };
        img.src = blobUrl;
      }
    } catch (e) {
      if (loading) loading.style.display = "none";
      if (errorEl) {
        errorEl.textContent = `⚠ ${e.message}`;
        errorEl.style.display = "block";
      }
    }
  },

  cleanupAllBlobs() {
    for (const [urlId, blobUrl] of Object.entries(this.activePreviewBlobs)) {
      URL.revokeObjectURL(blobUrl);
    }
    this.activePreviewBlobs = {};
  },

  async togglePin(urlId, button) {
    if (!button || button.disabled) return;
    const nextPinned = button.dataset.pinned !== "true";
    button.disabled = true;

    try {
      const resp = await fetch(`/api/urls/${urlId}/pin`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: nextPinned }),
      });
      const json = await resp.json();
      if (!resp.ok || !json.success) throw new Error(json.error?.message || "Failed to update pin state");

      const isPinned = json.data.is_pinned;
      button.dataset.pinned = String(isPinned);
      button.setAttribute("aria-pressed", String(isPinned));
      button.classList.toggle("active", isPinned);
      button.textContent = isPinned ? "★ Pinned" : "☆ Pin";

      if (this.isPinnedOnly && !isPinned) {
        const card = document.getElementById(`url-card-${urlId}`);
        if (card) card.remove();
        if (this.currentPagination) {
          this.currentPagination.total = Math.max(0, this.currentPagination.total - 1);
          this.currentPagination.total_pages = Math.max(1, Math.ceil(this.currentPagination.total / this.currentPagination.limit));
          const countEl = document.getElementById("url-results-count");
          const listEl = document.getElementById("urls-container");
          const remaining = listEl ? listEl.querySelectorAll(".url-card").length : 0;
          if (countEl) countEl.textContent = `Showing ${remaining} of ${this.currentPagination.total.toLocaleString()} URLs`;
          if (remaining === 0 && this.currentPagination.total > 0) {
            if (this.currentPage > this.currentPagination.total_pages) {
              this.currentPage = this.currentPagination.total_pages;
            }
            await this.loadUrls();
            return;
          }
          if (remaining === 0 && listEl) {
            listEl.innerHTML = '<div class="empty-state"><div class="empty-icon">🔍</div><div class="empty-title">No URLs Match Current Filters</div><div class="empty-desc">Try clearing filters or import more recon data.</div></div>';
          }
          this.renderPagination(this.currentPagination);
        }
      }
    } catch (e) {
      if (window.ReconApp && ReconApp.showToast) ReconApp.showToast(e.message, "error");
      else console.error("Failed to update URL pin state:", e);
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  },

  // Toggle Inline Details Dropdown
  toggleDetails(urlId) {
    const panel = document.getElementById(`details-panel-${urlId}`);
    const btn = document.getElementById(`details-toggle-btn-${urlId}`);
    if (panel) {
      const isOpen = panel.classList.contains("open");
      panel.classList.toggle("open", !isOpen);
      if (btn) {
        btn.innerHTML = !isOpen ? "▲ Details" : "▼ Details";
      }
    }
  },

  renderPagination(pagination) {
    const container = document.getElementById("pagination-container");
    if (!container) return;

    if (pagination.total_pages <= 1) {
      container.innerHTML = "";
      return;
    }

    container.innerHTML = `
      <div class="pagination-bar">
        <span style="font-size:12px; color:var(--text-secondary);">Page ${pagination.page} of ${pagination.total_pages}</span>
        <div class="pagination-controls">
          <button class="btn btn-sm btn-secondary" ${pagination.page <= 1 ? "disabled" : ""} onclick="UrlsModule.goToPage(${pagination.page - 1})">← Previous</button>
          <button class="btn btn-sm btn-secondary" ${pagination.page >= pagination.total_pages ? "disabled" : ""} onclick="UrlsModule.goToPage(${pagination.page + 1})">Next →</button>
        </div>
      </div>
    `;
  },

  goToPage(pageNum) {
    this.currentPage = pageNum;
    this.loadUrls();
    document.getElementById("page-workspace")?.scrollTo({ top: 0, behavior: "smooth" });
  },
};

document.addEventListener("reconscope:page-unload", () => UrlsModule.unmount());
document.addEventListener("reconscope:page-load", () => UrlsModule.init());
