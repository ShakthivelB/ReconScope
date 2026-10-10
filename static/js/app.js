/**
 * ReconScope - Global Modular Frontend Framework (Vanilla JS)
 */

const ReconApp = {
  activeTargetId: null,

  init() {
    this.initMobileNav();
    this.initTargetSelector();
    this.initScanControls();
  },

  // Toast Notification System
  showToast(message, type = "info") {
    let container = document.getElementById("toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "toast-container";
      container.className = "toast-container";
      document.body.appendChild(container);
    }

    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    const icon = type === "success" ? "✓" : type === "error" ? "⚠" : "ℹ";
    toast.innerHTML = `<span>${icon}</span> <div>${message}</div>`;

    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 250);
    }, 3500);
  },

  // Mobile Drawer Navigation
  initMobileNav() {
    const btn = document.getElementById("mobile-menu-toggle");
    const sidebar = document.querySelector(".sidebar");
    if (btn && sidebar) {
      btn.addEventListener("click", () => {
        sidebar.classList.toggle("open");
      });
      // Close on outside click
      document.addEventListener("click", (e) => {
        if (!sidebar.contains(e.target) && !btn.contains(e.target)) {
          sidebar.classList.remove("open");
        }
      });
    }
  },

  // Active Target Context across pages
  async initTargetSelector() {
    const selector = document.getElementById("global-target-select");
    if (!selector) return;

    this.activeTargetId = localStorage.getItem("reconscope_target_id") || "";

    try {
      const resp = await fetch("/api/targets");
      const json = await resp.json();
      if (json.success && json.data.length > 0) {
        selector.innerHTML = '<option value="">All Targets</option>';
        json.data.forEach((t) => {
          const opt = document.createElement("option");
          opt.value = t.id;
          opt.textContent = `${t.name} (${t.root_domain})`;
          if (this.activeTargetId && String(t.id) === String(this.activeTargetId)) {
            opt.selected = true;
          }
          selector.appendChild(opt);
        });

        selector.addEventListener("change", (e) => {
          this.activeTargetId = e.target.value;
          localStorage.setItem("reconscope_target_id", this.activeTargetId);
          window.dispatchEvent(new CustomEvent("targetChanged", { detail: { targetId: this.activeTargetId } }));
        });
      } else {
        selector.innerHTML = '<option value="">No Targets Configured</option>';
      }
    } catch (e) {
      console.error("Failed to load targets", e);
    }
  },

  getActiveTarget() {
    return this.activeTargetId ? parseInt(this.activeTargetId, 10) : null;
  },

  scanPollTimer: null,

  initScanControls() {
    const checkButton = document.getElementById("check-urls-btn");
    const closeButton = document.getElementById("scan-widget-close");
    if (checkButton) checkButton.addEventListener("click", () => this.startUrlCheck());
    if (closeButton) closeButton.addEventListener("click", () => {
      document.getElementById("global-scan-widget")?.classList.remove("active");
      if (this.scanPollTimer) clearTimeout(this.scanPollTimer);
      this.scanPollTimer = null;
      localStorage.removeItem("reconscope_scan_id");
    });

    const savedScanId = localStorage.getItem("reconscope_scan_id");
    if (savedScanId) this.pollScan(savedScanId);
    else this.loadActiveScan();
  },

  async startUrlCheck() {
    const targetId = this.getActiveTarget();
    if (!targetId) {
      this.showToast("Select a target before checking URLs.", "info");
      return;
    }
    const button = document.getElementById("check-urls-btn");
    if (button) button.disabled = true;
    try {
      const response = await fetch("/api/scans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_id: targetId, concurrency: 15, timeout: 10 }),
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error?.message || "Could not start URL checks");
      localStorage.setItem("reconscope_scan_id", result.data.id);
      this.showToast(`Checking ${result.data.total_urls.toLocaleString()} URLs in the background.`, "success");
      this.pollScan(result.data.id);
    } catch (error) {
      this.showToast(error.message, "error");
    } finally {
      if (button) button.disabled = false;
    }
  },

  async loadActiveScan() {
    const targetId = this.getActiveTarget();
    try {
      const response = await fetch(targetId ? `/api/scans/active?target_id=${targetId}` : "/api/scans/active");
      const result = await response.json();
      if (result.success && result.data) {
        localStorage.setItem("reconscope_scan_id", result.data.id);
        this.pollScan(result.data.id);
      }
    } catch (error) {
      console.warn("Could not restore URL check progress", error);
    }
  },

  async pollScan(scanId) {
    const widget = document.getElementById("global-scan-widget");
    if (!widget) return;
    widget.classList.add("active");
    try {
      const response = await fetch(`/api/scans/${scanId}`);
      if (response.status === 404) {
        if (localStorage.getItem("reconscope_scan_id") === String(scanId)) {
          localStorage.removeItem("reconscope_scan_id");
        }
        if (this.scanPollTimer) clearTimeout(this.scanPollTimer);
        this.scanPollTimer = null;
        widget.classList.remove("active");
        await this.loadActiveScan();
        return;
      }
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error?.message || "Could not read scan progress");
      const scan = result.data;
      const completed = Number(scan.completed_urls || 0);
      const total = Number(scan.total_urls || 0);
      const title = document.getElementById("scan-widget-title");
      const progress = document.getElementById("scan-widget-progress");
      const stats = document.getElementById("scan-widget-stats");
      if (title) title.textContent = scan.status === "completed" ? "URL checks complete" : scan.status === "failed" ? "URL checks failed" : "Checking URLs...";
      if (progress) progress.textContent = `${completed.toLocaleString()} / ${total.toLocaleString()} · ${scan.status}`;
      if (stats) stats.innerHTML = [
        ["2xx", scan.status_2xx], ["3xx", scan.status_3xx], ["401", scan.status_401],
        ["403", scan.status_403], ["404", scan.status_404], ["5xx", scan.status_5xx],
        ["Timeout", scan.timeouts], ["Errors", scan.errors],
      ].map(([label, count]) => `<span>${label}: ${Number(count || 0).toLocaleString()}</span>`).join("");
      window.dispatchEvent(new CustomEvent("scanProgress"));

      if (["completed", "failed", "cancelled"].includes(scan.status)) {
        localStorage.removeItem("reconscope_scan_id");
        window.dispatchEvent(new CustomEvent("scanUpdated"));
        if (this.scanPollTimer) clearTimeout(this.scanPollTimer);
        this.scanPollTimer = null;
        return;
      }
    } catch (error) {
      const progress = document.getElementById("scan-widget-progress");
      if (progress) progress.textContent = error.message;
    }
    this.scanPollTimer = setTimeout(() => this.pollScan(scanId), 1800);
  },
};

document.addEventListener("DOMContentLoaded", () => {
  ReconApp.init();
});
