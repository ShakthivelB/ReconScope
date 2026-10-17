/**
 * Dashboard JavaScript - Metrics, Charts & High-level Intelligence
 */

const DashboardModule = {
  refreshTimer: null,
  requestInFlight: false,
  hasLoaded: false,

  init() {
    document.getElementById("dashboard-retry")?.addEventListener("click", () => this.loadStats());
    window.addEventListener("scanProgress", () => this.scheduleRefresh());
    window.addEventListener("scanUpdated", () => this.scheduleRefresh());
    this.loadStats();
  },

  scheduleRefresh() {
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.loadStats();
    }, 500);
  },

  async loadStats() {
    if (this.requestInFlight) return;
    this.requestInFlight = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    const errorBox = document.getElementById("dashboard-error");
    const errorMessage = document.getElementById("dashboard-error-message");
    const retryButton = document.getElementById("dashboard-retry");
    if (retryButton) retryButton.disabled = true;

    try {
      const resp = await fetch("/api/dashboard/stats", { signal: controller.signal });
      const json = await resp.json();
      if (!resp.ok || !json.success) throw new Error(json.error?.message || `Dashboard request failed (${resp.status})`);

      const metrics = json.data?.metrics;
      const charts = json.data?.charts;
      const requiredMetrics = [
        "total_urls", "checked", "not_checked", "status_200", "status_3xx", "status_401",
        "status_403", "status_404", "status_5xx", "timeouts", "errors", "interesting",
        "subdomains", "technologies",
      ];
      if (!metrics || !charts || requiredMetrics.some((key) => !Number.isFinite(Number(metrics[key])))) {
        throw new Error("Dashboard API returned incomplete statistics.");
      }

      const metricIds = {
        total_urls: "stat-total-urls", checked: "stat-checked", not_checked: "stat-not-checked",
        status_200: "stat-200", status_3xx: "stat-3xx", status_401: "stat-401",
        status_403: "stat-403", status_404: "stat-404", status_5xx: "stat-5xx",
        timeouts: "stat-timeouts", errors: "stat-errors", interesting: "stat-interesting",
        subdomains: "stat-subdomains", technologies: "stat-technologies",
      };
      for (const [key, id] of Object.entries(metricIds)) {
        const element = document.getElementById(id);
        if (element) element.textContent = Number(metrics[key]).toLocaleString();
      }

      this.renderBarChart("chart-status-bars", charts.status_distribution);
      this.renderBarChart("chart-sources-bars", charts.sources);
      this.renderTechnologies(charts.technologies);
      this.hasLoaded = true;
      if (errorBox) errorBox.hidden = true;
    } catch (error) {
      const message = error.name === "AbortError"
        ? "Dashboard request timed out after 15 seconds."
        : error.message || "Could not load dashboard statistics.";
      if (errorBox) errorBox.hidden = false;
      if (errorMessage) errorMessage.textContent = message;
      if (!this.hasLoaded) {
        for (const id of ["stat-total-urls", "stat-checked", "stat-not-checked", "stat-200", "stat-3xx", "stat-401", "stat-403", "stat-404", "stat-5xx", "stat-timeouts", "stat-errors", "stat-interesting", "stat-subdomains", "stat-technologies"]) {
          const element = document.getElementById(id);
          if (element) element.textContent = "—";
        }
        for (const id of ["chart-status-bars", "chart-sources-bars", "chart-tech-bars"]) {
          const container = document.getElementById(id);
          if (container) container.textContent = "Statistics unavailable. Use Retry above.";
        }
      }
      console.error("Error loading dashboard stats", error);
    } finally {
      clearTimeout(timeout);
      this.requestInFlight = false;
      if (retryButton) retryButton.disabled = false;
    }
  },

  renderBarChart(containerId, dataObj) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const entries = Object.entries(dataObj || {}).filter(([, value]) => Number.isFinite(Number(value)));
    if (entries.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">No data recorded yet.</div>';
      return;
    }

    const maxVal = Math.max(...entries.map(([, value]) => Number(value)), 1);
    container.innerHTML = entries.map(([label, count]) => `
      <div class="bar-row">
        <span class="bar-label">${this.escapeHtml(label)}</span>
        <div class="bar-track">
          <div class="bar-fill" style="width: ${(Number(count) / maxVal) * 100}%;"></div>
        </div>
        <span class="bar-count">${Number(count).toLocaleString()}</span>
      </div>
    `).join("");
  },

  renderTechnologies(technologies) {
    const container = document.getElementById("chart-tech-bars");
    if (!container) return;
    if (!Array.isArray(technologies) || technologies.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">No technologies detected yet.</div>';
      return;
    }

    const maxTech = Math.max(...technologies.map((technology) => Number(technology.count) || 0), 1);
    container.innerHTML = technologies.map((technology) => {
      const name = this.escapeHtml(technology.name);
      const count = Number(technology.count) || 0;
      return `
        <div class="bar-row">
          <span class="bar-label" title="${name}">${name}</span>
          <div class="bar-track">
            <div class="bar-fill" style="width: ${(count / maxTech) * 100}%; background-color: var(--accent-purple);"></div>
          </div>
          <span class="bar-count">${count.toLocaleString()}</span>
        </div>
      `;
    }).join("");
  },

  escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  },
};

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("stat-total-urls")) DashboardModule.init();
});
