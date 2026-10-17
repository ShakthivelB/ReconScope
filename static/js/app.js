/**
 * ReconScope - Global Modular Frontend Framework (Vanilla JS)
 */

const ReconApp = {
  init() {
    this.initMobileNav();
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

  scanPollTimer: null,
  pollingScanId: null,
  scanPollInFlight: false,
  scanPollingSuppressed: false,

  initScanControls() {
    window.addEventListener("storage", (event) => {
      if (event.key !== "reconscope_data_changed") return;
      this.scanPollingSuppressed = false;
      const savedId = localStorage.getItem("reconscope_scan_id");
      if (savedId) this.pollScan(savedId, true);
      else this.loadActiveScan();
    });
    const checkButton = document.getElementById("check-urls-btn");
    const closeButton = document.getElementById("scan-widget-close");
    if (checkButton) checkButton.addEventListener("click", () => this.startUrlCheck());
    if (closeButton) closeButton.addEventListener("click", () => {
      document.getElementById("global-scan-widget")?.classList.remove("active");
      if (this.scanPollTimer) clearTimeout(this.scanPollTimer);
      this.scanPollTimer = null;
      this.pollingScanId = null;
      this.scanPollingSuppressed = true;
      localStorage.removeItem("reconscope_scan_id");
    });

    const savedScanId = localStorage.getItem("reconscope_scan_id");
    if (savedScanId) this.pollScan(savedScanId);
    else this.loadActiveScan();
  },

  async startUrlCheck() {
    this.scanPollingSuppressed = false;
    const button = document.getElementById("check-urls-btn");
    if (button) button.disabled = true;
    try {
      const response = await fetch("/api/scans", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ concurrency: 15, timeout: 10 }),
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error?.message || "Could not start URL checks");
      if (!Number.isInteger(Number(result.data?.id)) || Number(result.data.id) < 1) {
        throw new Error("The scan API returned an invalid scan ID.");
      }
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
    if (this.scanPollingSuppressed) return;
    try {
      const response = await fetch("/api/scans/active", { cache: "no-store" });
      const result = await response.json();
      // success_response(None) is serialized as an empty object by the API
      // helper. Only resume polling when the response contains a real scan id.
      if (result.success && Number.isInteger(Number(result.data?.id)) && Number(result.data.id) > 0) {
        localStorage.setItem("reconscope_scan_id", result.data.id);
        this.pollScan(result.data.id);
      }
    } catch (error) {
      console.warn("Could not restore URL check progress", error);
    }
  },

  async pollScan(scanId, fromTimer = false) {
    if (this.scanPollingSuppressed) return;
    const widget = document.getElementById("global-scan-widget");
    if (!widget) return;
    const validScanId = Number(scanId);
    if (!Number.isInteger(validScanId) || validScanId < 1) {
      localStorage.removeItem("reconscope_scan_id");
      widget.classList.remove("active");
      return;
    }
    if (this.scanPollInFlight) return;
    if (!fromTimer && this.pollingScanId === validScanId) return;
    if (this.scanPollTimer) clearTimeout(this.scanPollTimer);
    this.scanPollTimer = null;
    this.pollingScanId = validScanId;
    this.scanPollInFlight = true;
    widget.classList.add("active");
    let shouldContinue = true;
    try {
      const response = await fetch(`/api/scans/${validScanId}`, { cache: "no-store" });
      if (response.status === 404) {
        if (localStorage.getItem("reconscope_scan_id") === String(scanId)) {
          localStorage.removeItem("reconscope_scan_id");
        }
        if (this.scanPollTimer) clearTimeout(this.scanPollTimer);
        this.scanPollTimer = null;
        this.pollingScanId = null;
        shouldContinue = false;
        widget.classList.remove("active");
        return;
      }
      const result = await response.json();
      if (!response.ok || !result.success) {
        const error = new Error(result.error?.message || "Could not read scan progress");
        if (response.status === 422) {
          this.pollingScanId = null;
          shouldContinue = false;
          localStorage.removeItem("reconscope_scan_id");
        }
        throw error;
      }
      const scan = result.data;
      if (!scan || scan.id == null || !scan.status) {
        localStorage.removeItem("reconscope_scan_id");
        this.pollingScanId = null;
        shouldContinue = false;
        if (this.scanPollTimer) clearTimeout(this.scanPollTimer);
        this.scanPollTimer = null;
        widget.classList.remove("active");
        return;
      }
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
        this.pollingScanId = null;
        return;
      }
    } catch (error) {
      const progress = document.getElementById("scan-widget-progress");
      if (progress) progress.textContent = error.message;
    } finally {
      this.scanPollInFlight = false;
    }
    if (shouldContinue && this.pollingScanId === validScanId) {
      this.scanPollTimer = setTimeout(() => this.pollScan(validScanId, true), 1800);
    } else if (!this.pollingScanId) {
      this.loadActiveScan();
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  ReconApp.init();
});
