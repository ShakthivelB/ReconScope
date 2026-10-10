/**
 * Targets Management JavaScript
 */

const TargetsModule = {
  async init() {
    this.bindEvents();
    await this.loadTargets();
  },

  bindEvents() {
    const form = document.getElementById("create-target-form");
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await this.handleCreateTarget(form);
      });
    }

    const list = document.getElementById("targets-list");
    if (list) {
      list.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-target-action]");
        if (!button || !list.contains(button)) return;

        const targetId = Number(button.dataset.targetId);
        const targetName = button.dataset.targetName || "";
        if (!Number.isInteger(targetId) || targetId < 1) return;
        if (button.dataset.targetAction === "delete") this.deleteTarget(targetId, targetName);
        if (button.dataset.targetAction === "active") this.setActiveTarget(targetId, targetName);
      });
    }
  },

  escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  },

  async loadTargets() {
    const listEl = document.getElementById("targets-list");
    if (!listEl) return;

    try {
      const resp = await fetch("/api/targets");
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message);

      if (json.data.length === 0) {
        listEl.innerHTML = `
          <div class="empty-state">
            <div class="empty-icon">🎯</div>
            <div class="empty-title">No Targets Found</div>
            <div class="empty-desc">Create your first authorized security testing target or import URLs.</div>
          </div>
        `;
        return;
      }

      listEl.innerHTML = json.data.map((t) => {
        const name = this.escapeHtml(t.name);
        const rootDomain = this.escapeHtml(t.root_domain);
        const scopeRules = t.scope_rules
          ? this.escapeHtml(t.scope_rules).replace(/\r?\n/g, ", ")
          : `*.${rootDomain}, ${rootDomain}`;
        return `
        <div class="url-card">
          <div class="url-card-header">
            <div class="url-title-group">
              <span style="font-size: 16px; font-weight: 700; color: var(--text-primary);">${name}</span>
              <span style="font-family: var(--font-mono); color: var(--accent-blue); font-size: 13px;">${rootDomain}</span>
            </div>
            <div class="url-action-buttons">
              <button class="btn btn-sm btn-secondary" data-target-action="active" data-target-id="${t.id}" data-target-name="${name}">Set Active</button>
              <button class="btn btn-sm btn-danger" data-target-action="delete" data-target-id="${t.id}" data-target-name="${name}">Delete</button>
            </div>
          </div>

          <div style="font-size: 12px; color: var(--text-secondary); margin-top: 4px;">
            <strong>Scope Rules:</strong> ${scopeRules}
          </div>
          ${t.notes ? `<div style="font-size: 12px; color: var(--text-muted);">${this.escapeHtml(t.notes)}</div>` : ""}

          <div class="url-meta-chips" style="margin-top: 6px;">
            <span class="chip">🔗 ${t.url_count.toLocaleString()} URLs</span>
            <span class="chip">📁 ${t.import_count} Imports</span>
            <span class="chip">⚡ ${t.scan_count} Scans</span>
            <span class="chip">📅 Created ${new Date(t.created_at).toLocaleDateString()}</span>
          </div>
        </div>
      `;
      }).join("");
    } catch (e) {
      ReconApp.showToast(`Error loading targets: ${e.message}`, "error");
    }
  },

  async handleCreateTarget(form) {
    const formData = new FormData(form);
    const payload = {
      name: formData.get("name"),
      root_domain: formData.get("root_domain"),
      scope_rules: formData.get("scope_rules") || "",
      notes: formData.get("notes") || "",
    };

    try {
      const resp = await fetch("/api/targets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message);

      ReconApp.showToast("Target created successfully!", "success");
      form.reset();
      await this.loadTargets();
      await ReconApp.initTargetSelector();
    } catch (e) {
      ReconApp.showToast(e.message, "error");
    }
  },

  setActiveTarget(targetId, targetName) {
    localStorage.setItem("reconscope_target_id", targetId);
    ReconApp.activeTargetId = String(targetId);
    const sel = document.getElementById("global-target-select");
    if (sel) sel.value = String(targetId);
    ReconApp.showToast(`Active target set to: ${targetName}`, "success");
    window.dispatchEvent(new CustomEvent("targetChanged", { detail: { targetId } }));
  },

  async deleteTarget(targetId, targetName) {
    if (!confirm(`Are you sure you want to delete target '${targetName}'? All associated URLs, scans, and imports will be deleted.`)) {
      return;
    }

    try {
      const resp = await fetch(`/api/targets/${targetId}`, { method: "DELETE" });
      const json = await resp.json();
      if (!resp.ok || !json.success) throw new Error(json.error?.message || "Target could not be deleted.");

      ReconApp.showToast(`Target '${targetName}' deleted.`, "info");
      if (String(ReconApp.activeTargetId) === String(targetId)) {
        localStorage.removeItem("reconscope_target_id");
        ReconApp.activeTargetId = "";
      }
      await this.loadTargets();
      await ReconApp.initTargetSelector();
      window.dispatchEvent(new CustomEvent("targetChanged", { detail: { targetId: ReconApp.activeTargetId || "" } }));
    } catch (e) {
      ReconApp.showToast(e.message, "error");
    }
  },
};

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("targets-list")) {
    TargetsModule.init();
  }
});
