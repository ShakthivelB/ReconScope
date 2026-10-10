/**
 * Imports Management JavaScript
 */

const ImportsModule = {
  async init() {
    await this.populateTargetSelect();
    this.bindEvents();
    await this.loadImportHistory();
    window.addEventListener("targetChanged", () => this.loadImportHistory());
  },

  async populateTargetSelect() {
    const sel = document.getElementById("import-target-select");
    if (!sel) return;

    try {
      const resp = await fetch("/api/targets");
      const json = await resp.json();
      if (json.success && json.data.length > 0) {
        sel.innerHTML = json.data.map(t => `<option value="${t.id}">${t.name} (${t.root_domain})</option>`).join("");
        const active = ReconApp.getActiveTarget();
        if (active) sel.value = String(active);
      } else {
        sel.innerHTML = '<option value="">No targets available (Create one first)</option>';
      }
    } catch (e) {
      console.error(e);
    }
  },

  bindEvents() {
    const form = document.getElementById("import-form");
    if (form) {
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        await this.handleImport(form);
      });
    }

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

    const targetId = ReconApp.getActiveTarget();
    const url = targetId ? `/api/imports?target_id=${targetId}` : "/api/imports";

    try {
      const resp = await fetch(url);
      const json = await resp.json();
      if (!json.success) throw new Error(json.error?.message);

      if (json.data.length === 0) {
        listEl.innerHTML = '<div style="color:var(--text-muted); font-size: 13px;">No import history yet.</div>';
        return;
      }

      listEl.innerHTML = json.data.map(imp => `
        <div class="url-card">
          <div class="url-card-header">
            <span style="font-weight: 600;">${imp.filename}</span>
            <span class="chip">${imp.source_type}</span>
          </div>
          <div class="url-meta-chips">
            <span class="chip">Raw: ${imp.raw_count}</span>
            <span class="chip" style="color:var(--accent-green);">+${imp.new_count} New</span>
            <span class="chip" style="color:var(--text-muted);">${imp.duplicate_count} Dupes</span>
            <span class="chip">📅 ${new Date(imp.created_at).toLocaleString()}</span>
          </div>
        </div>
      `).join("");
    } catch (e) {
      console.error(e);
    }
  }
};

document.addEventListener("DOMContentLoaded", () => {
  if (document.getElementById("import-form")) {
    ImportsModule.init();
  }
});
