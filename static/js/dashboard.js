/**
 * Dashboard JavaScript - Metrics, Charts & High-level Intelligence
 */

const DashboardModule = {
  async init() {
    await this.loadStats();
    window.addEventListener("targetChanged", () => this.loadStats());
    window.addEventListener("scanProgress", () => this.loadStats());
    window.addEventListener("scanUpdated", () => this.loadStats());
  },

  async loadStats() {
    const targetId = ReconApp.getActiveTarget();
    const url = targetId ? `/api/dashboard/stats?target_id=${targetId}` : "/api/dashboard/stats";

    try {
      const resp = await fetch(url);
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message);

      const m = json.data.metrics;
      const c = json.data.charts;

      // Update Metric Values
      document.getElementById("stat-total-urls").textContent = m.total_urls.toLocaleString();
      document.getElementById("stat-checked").textContent = m.checked.toLocaleString();
      document.getElementById("stat-not-checked").textContent = m.not_checked.toLocaleString();
      document.getElementById("stat-200").textContent = m.status_200.toLocaleString();
      document.getElementById("stat-3xx").textContent = m.status_3xx.toLocaleString();
      document.getElementById("stat-401").textContent = m.status_401.toLocaleString();
      document.getElementById("stat-403").textContent = m.status_403.toLocaleString();
      document.getElementById("stat-404").textContent = m.status_404.toLocaleString();
      document.getElementById("stat-5xx").textContent = m.status_5xx.toLocaleString();
      document.getElementById("stat-timeouts").textContent = m.timeouts.toLocaleString();
      document.getElementById("stat-errors").textContent = m.errors.toLocaleString();
      document.getElementById("stat-interesting").textContent = m.interesting.toLocaleString();
      document.getElementById("stat-subdomains").textContent = m.subdomains.toLocaleString();
      document.getElementById("stat-technologies").textContent = m.technologies.toLocaleString();

      // Render Status Distribution Chart
      this.renderBarChart("chart-status-bars", c.status_distribution, m.checked || 1);

      // Render Sources Chart
      this.renderBarChart("chart-sources-bars", c.sources, m.total_urls || 1);

      // Render Top Techs
      const techContainer = document.getElementById("chart-tech-bars");
      if (techContainer) {
        if (!c.technologies || c.technologies.length === 0) {
          techContainer.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">No technologies detected yet.</div>';
        } else {
          const maxTech = Math.max(...c.technologies.map(t => t.count), 1);
          techContainer.innerHTML = c.technologies.map(t => `
            <div class="bar-row">
              <span class="bar-label" title="${t.name}">${t.name}</span>
              <div class="bar-track">
                <div class="bar-fill" style="width: ${(t.count / maxTech) * 100}%; background-color: var(--accent-purple);"></div>
              </div>
              <span class="bar-count">${t.count}</span>
            </div>
          `).join("");
        }
      }

    } catch (e) {
      console.error("Error loading dashboard stats", e);
    }
  },

  renderBarChart(containerId, dataObj, maxRef) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const entries = Object.entries(dataObj || {});
    if (entries.length === 0) {
      container.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">No data recorded yet.</div>';
      return;
    }

    const maxVal = Math.max(...entries.map(e => e[1]), 1);
    container.innerHTML = entries.map(([label, count]) => `
      <div class="bar-row">
        <span class="bar-label">${label}</span>
        <div class="bar-track">
          <div class="bar-fill" style="width: ${(count / maxVal) * 100}%;"></div>
        </div>
        <span class="bar-count">${count}</span>
      </div>
    `).join("");
  }
};

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("stat-total-urls")) {
    DashboardModule.init();
  }
});
