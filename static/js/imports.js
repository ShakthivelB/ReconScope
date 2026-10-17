/**
 * Imports Management JavaScript
 */

const ImportsModule = {
  async init() {
    this.bindEvents();
    await this.loadImportHistory();
  },

  bindEvents() {
    const form = document.getElementById("import-form");
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await this.handleImport(form);
      });
    }

    const historyList = document.getElementById("import-history-list");
    if (historyList) {
      historyList.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-delete-import]");
        if (!button) return;
        await this.openDeleteDialog(button.dataset.deleteImport, button.dataset.filename || "this import");
      });
    }

    const dialog = document.getElementById("import-delete-dialog");
    dialog?.addEventListener("click", (event) => {
      if (event.target === dialog && !this.deletingImport) dialog.close();
    });
    dialog?.addEventListener("cancel", (event) => {
      if (this.deletingImport) event.preventDefault();
    });
    document.getElementById("import-delete-cancel")?.addEventListener("click", () => dialog?.close());
    document.getElementById("import-delete-confirm")?.addEventListener("click", () => this.confirmDelete());

    // Drag and drop support
    const dropZone = document.getElementById("drop-zone");
    const fileInput = document.getElementById("import-files");
    if (dropZone && fileInput) {
      ["dragenter", "dragover"].forEach(name => {
        dropZone.addEventListener(name, (e) => {
          e.preventDefault();
          dropZone.style.borderColor = "var(--accent-blue)";
          dropZone.style.backgroundColor = "var(--bg-tertiary)";
        });
      });

      ["dragleave", "drop"].forEach(name => {
        dropZone.addEventListener(name, (e) => {
          e.preventDefault();
          dropZone.style.borderColor = "var(--border-color)";
          dropZone.style.backgroundColor = "transparent";
        });
      });

      dropZone.addEventListener("drop", (e) => {
        if (e.dataTransfer.files.length > 0) {
          fileInput.files = e.dataTransfer.files;
          this.updateFileLabel(fileInput.files);
        }
      });

      fileInput.addEventListener("change", () => {
        this.updateFileLabel(fileInput.files);
      });
    }
  },

  updateFileLabel(files) {
    const label = document.getElementById("drop-zone-text");
    if (label && files.length > 0) {
      label.textContent = `Selected: ${Array.from(files).map(f => f.name).join(", ")}`;
    }
  },

  async handleImport(form) {
    const submitBtn = form.querySelector("button[type='submit']");
    submitBtn.disabled = true;
    submitBtn.textContent = "Processing & Ingesting...";

    const formData = new FormData(form);
    formData.delete("target_id");

    try {
      const resp = await fetch("/api/imports", {
        method: "POST",
        body: formData,
      });
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message);

      const s = json.data.summary;
      ReconApp.showToast(`Import completed! Raw: ${s.total_raw}, New: ${s.total_new}, Duplicates: ${s.total_duplicates}`, "success");

      // Show summary card
      const summaryBox = document.getElementById("import-summary-box");
      if (summaryBox) {
        summaryBox.style.display = "block";
        summaryBox.innerHTML = `
          <div class="url-card" style="border-left: 4px solid var(--accent-green);">
            <div style="font-weight: 700; color: var(--accent-green); margin-bottom: 6px;">✓ Import Successful</div>
            <div class="metrics-grid" style="margin-bottom: 0;">
              <div class="metric-card"><span class="metric-label">Raw URLs</span><span class="metric-val">${s.total_raw}</span></div>
              <div class="metric-card"><span class="metric-label">New Added</span><span class="metric-val" style="color:var(--accent-green);">${s.total_new}</span></div>
              <div class="metric-card"><span class="metric-label">Duplicates</span><span class="metric-val" style="color:var(--text-muted);">${s.total_duplicates}</span></div>
            </div>
          </div>
        `;
      }

      form.reset();
      this.updateFileLabel([]);
      await this.loadImportHistory();
    } catch (e) {
      ReconApp.showToast(e.message, "error");
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = "Start Import";
    }
  },

  async loadImportHistory() {
    const listEl = document.getElementById("import-history-list");
    if (!listEl) return;

    try {
      const resp = await fetch("/api/imports");
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message);

      if (json.data.length === 0) {
        listEl.innerHTML = '<div style="color:var(--text-muted); font-size: 13px;">No import history yet.</div>';
        return;
      }

      listEl.innerHTML = json.data.map(imp => `
        <div class="url-card">
          <div class="url-card-header">
            <span style="font-weight: 600;">${this.escapeHtml(imp.filename)}</span>
            <span class="chip">${this.escapeHtml(imp.source_type)}</span>
          </div>
          <div class="url-meta-chips">
            <span class="chip">Raw: ${imp.raw_count}</span>
            <span class="chip" style="color:var(--accent-green);">+${imp.new_count} New</span>
            <span class="chip" style="color:var(--text-muted);">${imp.duplicate_count} Dupes</span>
            <span class="chip">📅 ${new Date(imp.created_at).toLocaleString()}</span>
            <button type="button" class="btn btn-sm btn-danger" data-delete-import="${imp.id}" data-filename="${this.escapeHtml(imp.filename)}" aria-label="Delete imported source ${this.escapeHtml(imp.filename)}">Delete</button>
          </div>
        </div>
      `).join("");
    } catch (e) {
      console.error(e);
    }
  },

  escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  },

  deletingImport: false,
  async openDeleteDialog(importId, filename) {
    this.pendingDeleteId = importId;
    const dialog = document.getElementById("import-delete-dialog");
    const confirm = document.getElementById("import-delete-confirm");
    const detail = document.getElementById("import-delete-details");
    document.getElementById("import-delete-title").textContent = `Delete “${filename}”?`;
    detail.textContent = "Checking which URLs belong exclusively to this source…";
    confirm.disabled = true;
    dialog.showModal();
    try {
      const response = await fetch(`/api/imports/${encodeURIComponent(importId)}/deletion-preview`);
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error?.message || "Could not inspect this source");
      const preview = result.data;
      detail.textContent = `${preview.exclusive_urls} exclusive URL(s), including their HTTP results, technology links, and scan results, will be deleted. ${preview.shared_urls} shared URL(s) will remain. The original uploaded file is not stored.`;
      confirm.disabled = false;
    } catch (error) {
      detail.textContent = error.message || "Could not inspect this source. Nothing has been deleted.";
      ReconApp.showToast(error.message, "error");
    }
  },

  async confirmDelete() {
    if (this.deletingImport || !this.pendingDeleteId) return;
    const confirm = document.getElementById("import-delete-confirm");
    const cancel = document.getElementById("import-delete-cancel");
    this.deletingImport = true;
    confirm.disabled = true;
    cancel.disabled = true;
    confirm.textContent = "Deleting…";
    try {
      const response = await fetch(`/api/imports/${encodeURIComponent(this.pendingDeleteId)}`, { method: "DELETE" });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error?.message || "Could not delete the imported source");
      const data = result.data;
      ReconApp.showToast(`Deleted source and ${data.deleted_urls} exclusive URL(s).`, "success");
      document.getElementById("import-delete-dialog").close();
      localStorage.setItem("reconscope_data_changed", String(Date.now()));
      await this.loadImportHistory();
    } catch (error) {
      ReconApp.showToast(error.message, "error");
    } finally {
      this.deletingImport = false;
      this.pendingDeleteId = null;
      confirm.textContent = "Delete Everything";
      confirm.disabled = false;
      cancel.disabled = false;
    }
  }
};

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("import-form")) {
    ImportsModule.init();
  }
});
