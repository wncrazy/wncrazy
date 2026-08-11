const API = "";

const el = (id) => document.getElementById(id);
const folderInput = el("folder-input");
const useAiCheckbox = el("use-ai");
const modelSelect = el("model-select");
const scanBtn = el("scan-btn");
const progressWrap = el("progress-wrap");
const progressFill = el("progress-fill");
const progressText = el("progress-text");
const resultsPanel = el("results-panel");
const grid = el("grid");
const statsEl = el("stats");
const applyBtn = el("apply-btn");
const ollamaStatusEl = el("ollama-status");
const lightbox = el("lightbox");
const lightboxImg = el("lightbox-img");
const lightboxInfo = el("lightbox-info");

let currentFolder = localStorage.getItem("photo_culler_folder") || "";
let currentFilter = "all";
let pollTimer = null;

folderInput.value = currentFolder;

const PHASE_LABELS = {
  queued: "ממתין…",
  listing: "סורק קבצים…",
  metadata: "קורא מטא־דאטה…",
  quality: "בודק חדות וחשיפה…",
  ai_scoring: "מריץ הערכת AI (Ollama)…",
  finalizing: "מסכם תוצאות…",
  done: "הושלם",
  error: "שגיאה",
};

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

async function loadOllamaStatus() {
  try {
    const data = await api("/api/ollama/status");
    modelSelect.innerHTML = "";
    if (data.reachable) {
      ollamaStatusEl.textContent = `Ollama מחובר ✓ (${data.models.length} מודלים)`;
      ollamaStatusEl.className = "ollama-status ok";
      const models = data.models.length ? data.models : [data.configured_model];
      for (const m of models) {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        if (m === data.configured_model) opt.selected = true;
        modelSelect.appendChild(opt);
      }
    } else {
      ollamaStatusEl.textContent = `Ollama לא זמין: ${data.error}`;
      ollamaStatusEl.className = "ollama-status bad";
      const opt = document.createElement("option");
      opt.value = data.configured_model;
      opt.textContent = data.configured_model + " (לא מאומת)";
      modelSelect.appendChild(opt);
    }
  } catch (e) {
    ollamaStatusEl.textContent = "לא ניתן לבדוק חיבור ל-Ollama";
    ollamaStatusEl.className = "ollama-status bad";
  }
}

scanBtn.addEventListener("click", startScan);

async function startScan() {
  const folder = folderInput.value.trim();
  if (!folder) {
    alert("נא להזין נתיב לתיקיית תמונות");
    return;
  }
  localStorage.setItem("photo_culler_folder", folder);
  currentFolder = folder;

  scanBtn.disabled = true;
  progressWrap.classList.remove("hidden");
  resultsPanel.classList.add("hidden");

  let scanId;
  try {
    const res = await api("/api/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        folder,
        use_ai: useAiCheckbox.checked,
        model: modelSelect.value || null,
      }),
    });
    scanId = res.scan_id;
  } catch (e) {
    alert("שגיאה בהתחלת סריקה: " + e.message);
    scanBtn.disabled = false;
    return;
  }

  pollScan(scanId);
}

function pollScan(scanId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    let s;
    try {
      s = await api(`/api/scan/${scanId}`);
    } catch (e) {
      clearInterval(pollTimer);
      scanBtn.disabled = false;
      return;
    }

    const pct = s.total ? Math.round((s.processed / s.total) * 100) : 0;
    progressFill.style.width = pct + "%";
    progressText.textContent = `${PHASE_LABELS[s.status] || s.status} (${s.processed}/${s.total})`;

    if (s.status === "quality" || s.status === "ai_scoring" || s.status === "finalizing") {
      resultsPanel.classList.remove("hidden");
      renderPhotos();
    }

    if (s.status === "done" || s.status === "error") {
      clearInterval(pollTimer);
      scanBtn.disabled = false;
      if (s.status === "error") {
        alert("שגיאה בסריקה: " + s.error);
      } else {
        resultsPanel.classList.remove("hidden");
        renderPhotos();
      }
    }
  }, 1000);
}

document.querySelectorAll('input[name="filter"]').forEach((r) => {
  r.addEventListener("change", (e) => {
    currentFilter = e.target.value;
    renderPhotos();
  });
});

function scoreClass(score) {
  if (score == null) return "";
  if (score >= 65) return "score-high";
  if (score < 45) return "score-low";
  return "";
}

function filterPhotos(photos) {
  switch (currentFilter) {
    case "suggested":
      return photos.filter((p) => p.suggested_reject);
    case "duplicates":
      return photos.filter((p) => p.duplicate_group_id != null);
    case "blurry":
      return photos.filter((p) => p.is_blurry);
    default:
      return photos;
  }
}

let lastPhotos = [];

async function renderPhotos() {
  let data;
  try {
    data = await api(`/api/photos?folder=${encodeURIComponent(currentFolder)}`);
  } catch (e) {
    return;
  }
  lastPhotos = data.photos;
  const filtered = filterPhotos(data.photos);

  const suggestedCount = data.photos.filter((p) => p.suggested_reject).length;
  const groupCount = new Set(
    data.photos.filter((p) => p.duplicate_group_id != null).map((p) => p.duplicate_group_id)
  ).size;
  statsEl.textContent = `${data.count} תמונות · ${suggestedCount} מומלצות למחיקה · ${groupCount} קבוצות כפילויות`;

  grid.innerHTML = "";
  for (const p of filtered) {
    grid.appendChild(renderCard(p));
  }
}

function renderCard(p) {
  const card = document.createElement("div");
  card.className = `card decision-${p.effective_decision}`;

  const img = document.createElement("img");
  img.className = "thumb";
  img.loading = "lazy";
  img.src = `/api/photos/${p.id}/thumbnail`;
  img.addEventListener("click", () => openLightbox(p));
  card.appendChild(img);

  const body = document.createElement("div");
  body.className = "card-body";

  const name = document.createElement("div");
  name.className = "card-name";
  name.title = p.filename;
  name.textContent = p.filename;
  body.appendChild(name);

  const badges = document.createElement("div");
  badges.className = "badges";
  badges.appendChild(scoreBadge("כולל", p.combined_score));
  if (p.ai_score != null) badges.appendChild(scoreBadge("AI", p.ai_score));
  badges.appendChild(scoreBadge("חדות", p.sharpness_score));
  badges.appendChild(scoreBadge("חשיפה", p.exposure_score));
  if (p.duplicate_group_id != null) {
    const b = document.createElement("span");
    b.className = "badge flag";
    b.textContent = p.is_best_in_group ? "הטובה בקבוצה" : "כפילות";
    badges.appendChild(b);
  }
  if (p.is_blurry) badges.appendChild(flagBadge("מטושטש"));
  for (const f of p.ai_flags || []) badges.appendChild(flagBadge(f));
  body.appendChild(badges);

  const actions = document.createElement("div");
  actions.className = "card-actions";
  actions.appendChild(decisionButton(p, "keep", "✓ שמור"));
  actions.appendChild(decisionButton(p, "unset", "אוטומטי"));
  actions.appendChild(decisionButton(p, "reject", "✕ דחה"));
  body.appendChild(actions);

  card.appendChild(body);
  return card;
}

function scoreBadge(label, value) {
  const b = document.createElement("span");
  b.className = "badge " + scoreClass(value);
  b.textContent = value == null ? `${label}: —` : `${label}: ${Math.round(value)}`;
  return b;
}

function flagBadge(text) {
  const b = document.createElement("span");
  b.className = "badge flag";
  b.textContent = text;
  return b;
}

function decisionButton(p, decision, label) {
  const btn = document.createElement("button");
  btn.textContent = label;
  if (p.decision === decision || (p.decision === "unset" && decision === "unset")) {
    btn.classList.add(decision === "reject" ? "active-reject" : decision === "keep" ? "active-keep" : "");
  }
  btn.addEventListener("click", async () => {
    await api(`/api/photos/${p.id}/decision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    renderPhotos();
  });
  return btn;
}

function openLightbox(p) {
  lightboxImg.src = `/api/photos/${p.id}/full`;
  const parts = [p.filename];
  if (p.ai_reason) parts.push(`AI: ${p.ai_reason}`);
  parts.push(`ציון כולל: ${p.combined_score ?? "—"}`);
  lightboxInfo.textContent = parts.join(" · ");
  lightbox.classList.remove("hidden");
}

el("lightbox-close").addEventListener("click", () => lightbox.classList.add("hidden"));
lightbox.addEventListener("click", (e) => {
  if (e.target === lightbox) lightbox.classList.add("hidden");
});

applyBtn.addEventListener("click", async () => {
  if (!currentFolder) return;
  let dry;
  try {
    dry = await api("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: currentFolder, dry_run: true }),
    });
  } catch (e) {
    alert("שגיאה: " + e.message);
    return;
  }
  if (dry.moved_count === 0) {
    alert("אין תמונות המסומנות לדחייה.");
    return;
  }
  const ok = confirm(
    `${dry.moved_count} תמונות יועברו לתיקיית _rejected בתוך התיקייה המקורית (לא יימחקו). להמשיך?`
  );
  if (!ok) return;

  try {
    const res = await api("/api/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder: currentFolder, dry_run: false }),
    });
    alert(`הועברו ${res.moved_count} תמונות.`);
    renderPhotos();
  } catch (e) {
    alert("שגיאה בהעברת התמונות: " + e.message);
  }
});

loadOllamaStatus();
if (currentFolder) {
  // just prefill; user still needs to press scan for a fresh session
}
