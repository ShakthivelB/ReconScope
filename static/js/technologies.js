const TechnologiesModule = {
  loadSequence: 0,
  globalListenersBound: false,

  init() {
    if (!document.getElementById("tech-list")) return;
    if (!this.globalListenersBound) {
      window.addEventListener("storage", (event) => {
        if (event.key === "reconscope_data_changed") this.load();
      });
      window.addEventListener("scanUpdated", () => this.load());
      this.globalListenersBound = true;
    }
    this.load();
  },

  escapeText(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[char]));
  },

  async load() {
    if (!document.getElementById("tech-list")) return;
    const sequence = ++this.loadSequence;
    try {
      const [techResponse, subResponse] = await Promise.all([
        fetch("/api/technologies", { cache: "no-store" }),
        fetch("/api/urls/subdomains", { cache: "no-store" }),
      ]);
      const [techJson, subJson] = await Promise.all([techResponse.json(), subResponse.json()]);
      if (!techResponse.ok || !techJson.success) throw new Error(techJson.error?.message || "Could not load technologies.");
      if (!subResponse.ok || !subJson.success) throw new Error(subJson.error?.message || "Could not load subdomains.");
      if (sequence !== this.loadSequence) return;

      const container = document.getElementById("tech-list");
      if (!container) return;
      if (techJson.data.length > 0) {
        container.innerHTML = techJson.data.map((technology) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:8px; background:var(--bg-tertiary); border-radius:var(--radius-sm);">
            <div>
              <span style="font-weight:600; color:var(--text-primary);">${this.escapeText(technology.name)}</span>
              <span class="chip" style="margin-left:6px; font-size:10px;">${this.escapeText(technology.category)}</span>
            </div>
            <span class="chip chip-tech">${Number(technology.count) || 0} endpoints</span>
          </div>
        `).join("");
      } else {
        container.innerHTML = '<div style="color:var(--text-muted); padding:10px;">No technologies detected yet.</div>';
      }

      const subdomainsContainer = document.getElementById("subdomains-list");
      if (!subdomainsContainer) return;
      if (subJson.data.length > 0) {
        subdomainsContainer.innerHTML = subJson.data.map((subdomain) => `
          <div style="display:flex; justify-content:space-between; align-items:center; padding:8px; background:var(--bg-tertiary); border-radius:var(--radius-sm);">
            <div>
              <a href="/urls?hostname=${encodeURIComponent(subdomain.hostname)}" style="font-weight:600; font-family:var(--font-mono); color:var(--accent-blue); text-decoration:none;">${this.escapeText(subdomain.hostname)}</a>
              <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">
                ${(subdomain.technologies || []).slice(0, 3).map((name) => this.escapeText(name)).join(", ") || "No tech identified"}
              </div>
            </div>
            <span class="chip">${Number(subdomain.url_count) || 0} URLs</span>
          </div>
        `).join("");
      } else {
        subdomainsContainer.innerHTML = '<div style="color:var(--text-muted); padding:10px;">No subdomains discovered yet.</div>';
      }
    } catch (error) {
      console.error(error);
      if (sequence !== this.loadSequence) return;
      const techList = document.getElementById("tech-list");
      const subdomainList = document.getElementById("subdomains-list");
      if (techList) techList.textContent = error.message || "Could not load technologies.";
      if (subdomainList) subdomainList.textContent = error.message || "Could not load subdomains.";
    }
  },
};

document.addEventListener("reconscope:page-load", () => TechnologiesModule.init());
