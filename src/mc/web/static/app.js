/* MC Leads — interaction layer. No framework: the page is server-rendered,
   this only handles the sheet, the filter disclosure and the live widget. */

(() => {
  "use strict";

  const $  = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  /* ---------- Detail sheet ---------------------------------------- */

  const sheet = $("#sheet"), scrim = $("#sheet-scrim");

  function openSheet(id) {
    const src = $("#detail-" + id);
    if (!src || !sheet) return;
    $("#sheet-content").innerHTML = src.innerHTML;
    sheet.classList.add("open");
    scrim.classList.add("open");
    document.body.style.overflow = "hidden";
    sheet.setAttribute("aria-hidden", "false");
    if (window.lucide) lucide.createIcons();
    $(".sheet-head .btn")?.focus();
  }
  function closeSheet() {
    if (!sheet) return;
    sheet.classList.remove("open");
    scrim.classList.remove("open");
    document.body.style.overflow = "";
    sheet.setAttribute("aria-hidden", "true");
  }

  document.addEventListener("click", e => {
    const row = e.target.closest("[data-detail]");
    if (row && !e.target.closest("button, a, select, input, form")) {
      openSheet(row.dataset.detail);
      return;
    }
    if (e.target.closest("[data-close-sheet]")) closeSheet();
  });
  scrim?.addEventListener("click", closeSheet);
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeSheet();
  });

  /* ---------- Copy to clipboard ----------------------------------- */

  document.addEventListener("click", async e => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const text = btn.dataset.copy || btn.previousElementSibling?.innerText || "";
    try {
      await navigator.clipboard.writeText(text.trim());
      const was = btn.innerHTML;
      btn.innerHTML = '<i data-lucide="check"></i> Copied';
      if (window.lucide) lucide.createIcons();
      setTimeout(() => {
        btn.innerHTML = was;
        if (window.lucide) lucide.createIcons();
      }, 1600);
    } catch { /* clipboard blocked — nothing useful to do */ }
  });

  /* ---------- More filters ---------------------------------------- */

  const more = $("#more-filters"), moreBtn = $("#more-btn");
  if (more && moreBtn) {
    const key = "filters-open";
    let open = localStorage.getItem(key) === "1" || more.dataset.forceOpen === "1";
    const apply = () => {
      more.hidden = !open;
      moreBtn.setAttribute("aria-expanded", String(open));
    };
    moreBtn.addEventListener("click", () => {
      open = !open; localStorage.setItem(key, open ? "1" : "0"); apply();
    });
    apply();
  }

  /* Custom date range only matters when "Custom" is picked */
  const range = $("#range-select"), custom = $("#custom-range");
  if (range && custom) {
    const sync = () => { custom.hidden = range.value !== "custom"; };
    range.addEventListener("change", sync);
    sync();
  }

  /* ---------- Live activity --------------------------------------- */

  const live = $("#live");
  if (live) {
    const dot = $("#live-dot"), label = $("#live-label"), ringWrap = $("#live-ring");
    const detail = $("#live-detail"), steps = $("#live-steps"), queue = $("#live-queue");
    const tok = $("#live-tok"), bar = $("#live-bar"), body = $("#live-body"),
          caret = $("#live-caret"), head = $("#live-head");

    const TONE = {
      ok: "var(--green)", running: "var(--blue)", partial: "var(--orange)",
      stale: "var(--orange)", stopped: "var(--gray)", failed: "var(--red)",
      dead: "var(--red)", never: "var(--label-3)",
    };
    const ICON = { ok: "check", warn: "alert-triangle", error: "x",
                   pending: "circle", never: "circle-dashed" };

    const key = "live-open";
    let open = localStorage.getItem(key) !== "0";
    const apply = () => {
      body.hidden = !open;
      caret.style.transform = open ? "rotate(180deg)" : "";
      head.setAttribute("aria-expanded", String(open));
    };
    head.addEventListener("click", () => {
      open = !open; localStorage.setItem(key, open ? "1" : "0"); apply();
    });
    apply();

    const ago = s =>
      s == null ? "" :
      s < 90    ? "just now" :
      s < 5400  ? Math.round(s / 60) + " min" :
      s < 86400 ? Math.round(s / 3600) + " h" :
                  Math.round(s / 86400) + " d";

    function ring(step, total) {
      const r = 8, c = 2 * Math.PI * r, pct = total ? step / total : 0;
      return `<svg width="22" height="22" viewBox="0 0 22 22" style="transform:rotate(-90deg)">
        <circle cx="11" cy="11" r="${r}" fill="none" stroke="var(--fill-2)" stroke-width="2.5"/>
        <circle cx="11" cy="11" r="${r}" fill="none" stroke="var(--blue)" stroke-width="2.5"
          stroke-linecap="round" stroke-dasharray="${c}" stroke-dashoffset="${c * (1 - pct)}"
          style="transition:stroke-dashoffset .5s var(--ease)"/></svg>`;
    }

    async function tick() {
      let d;
      try { d = await (await fetch("/api/status")).json(); }
      catch { label.textContent = "Dashboard offline"; return setTimeout(tick, 8000); }

      const running = d.state === "running";
      dot.style.background = TONE[d.state] || TONE.never;
      dot.classList.toggle("dot-new", running);
      label.textContent = d.label;

      ringWrap.innerHTML = running
        ? ring(d.step, d.step_total)
        : `<span class="meta">${d.totals.leads} leads</span>`;

      detail.textContent = d.detail ||
        (d.age_seconds != null ? "last full cycle " + ago(d.age_seconds) + " ago" : "");
      detail.hidden = !detail.textContent;

      steps.innerHTML = d.steps.map((s, i) => {
        const active = running && i + 1 === d.step;
        const cls = active ? "active" :
          s.status === "ok" ? "done" :
          s.status === "warn" ? "warn" :
          s.status === "error" ? "error" : "";
        const ic = active
          ? '<i data-lucide="loader-2" class="spin" style="width:13px;height:13px"></i>'
          : `<i data-lucide="${ICON[s.status] || "circle"}" style="width:13px;height:13px"></i>`;
        // Har bosqich o'zining oxirgi natijasini ko'rsatadi, shuning uchun
        // qachonligini ham yozamiz -- aks holda "✓" qachonlikdir bo'lib qoladi.
        const when = active ? "" : (s.status === "never" ? "never" : ago(s.age_seconds));
        return `<div class="live-step ${cls}" ${s.error ? `title="${s.error}"` : ""}>
          <span class="ic">${ic}</span><span style="flex:1">${s.label}</span>
          <span class="meta" style="font-size:11px">${when}</span></div>`;
      }).join("");

      const Q = [["classify", "Waiting to analyse"], ["score", "Waiting to score"],
                 ["notify", "Ready to send"]];
      queue.innerHTML = Q.map(([k, l]) => {
        const n = d.queue[k] || 0;
        return `<div class="live-q" style="color:${n ? "var(--label)" : "var(--label-3)"}">
          <span>${l}</span><span class="n">${n}</span></div>`;
      }).join("");

      const pct = d.budget ? Math.min(100, d.tokens / d.budget * 100) : 0;
      tok.textContent = d.tokens.toLocaleString() + " / " + d.budget.toLocaleString();
      bar.style.width = pct + "%";
      bar.style.background = pct > 85 ? "var(--red)" : "var(--green)";

      if (window.lucide) lucide.createIcons();
      setTimeout(tick, running ? 3000 : 15000);
    }
    tick();
  }

  if (window.lucide) lucide.createIcons();
})();
