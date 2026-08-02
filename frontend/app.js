// Job Scout frontend logic.
// Talks to the backend (api.py, deployed separately - see config.js for its
// URL) that also runs the Telegram bot, via a tiny REST API: POST
// /api/search kicks a search off, GET /api/search/{id} is polled until
// it's done.

const form = document.getElementById("search-form");
const submitBtn = document.getElementById("submit-btn");
const formError = document.getElementById("form-error");
const progressEl = document.getElementById("progress");
const progressText = document.getElementById("progress-text");
const resultsEl = document.getElementById("results");

const POLL_INTERVAL_MS = 2500;
const MAX_POLLS = 40; // ~100s - if a search hasn't finished by then, give up

const STATUS_TEXT = {
  queued: "Queued…",
  scraping: "🔎 Searching Indeed, Naukri & LinkedIn…",
  scoring: "✅ Found openings — scoring them against your resume…",
};

// --- Tabs: paste text vs upload PDF -----------------------------------------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => {
      t.classList.remove("active");
      t.setAttribute("aria-selected", "false");
    });
    tab.classList.add("active");
    tab.setAttribute("aria-selected", "true");

    const target = tab.dataset.tab;
    document.querySelectorAll(".tab-panel").forEach((panel) => {
      panel.classList.toggle("hidden", panel.dataset.panel !== target);
    });
  });
});

// --- Form submit -------------------------------------------------------------

function showFormError(message) {
  formError.textContent = message;
  formError.classList.remove("hidden");
}

function clearFormError() {
  formError.textContent = "";
  formError.classList.add("hidden");
}

function setBusy(busy) {
  submitBtn.disabled = busy;
  submitBtn.textContent = busy ? "Searching…" : "🔎 Find Jobs";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearFormError();
  resultsEl.classList.add("hidden");
  resultsEl.innerHTML = "";

  const role = document.getElementById("role").value.trim();
  if (!role) {
    showFormError("Tell us what role or internship you're looking for.");
    return;
  }
  const location = document.getElementById("location").value.trim() || "India";

  const activeTab = document.querySelector(".tab.active").dataset.tab;
  const resumeText = document.getElementById("resume_text").value.trim();
  const resumeFileInput = document.getElementById("resume_file");
  const resumeFile = resumeFileInput.files[0];

  if (activeTab === "upload" && resumeFile && resumeFile.size > 5 * 1024 * 1024) {
    showFormError("That PDF is too large — please keep it under 5 MB.");
    return;
  }

  const body = new FormData();
  body.append("role", role);
  body.append("location", location);
  if (activeTab === "paste" && resumeText) {
    body.append("resume_text", resumeText);
  } else if (activeTab === "upload" && resumeFile) {
    body.append("resume_file", resumeFile);
  }

  setBusy(true);
  progressEl.classList.remove("hidden");
  progressText.textContent = "Getting started…";

  try {
    const response = await fetch(`${API_BASE_URL}/api/search`, { method: "POST", body });
    if (!response.ok) {
      const detail = await safeErrorDetail(response);
      showFormError(detail || "Something went wrong starting your search.");
      setBusy(false);
      progressEl.classList.add("hidden");
      return;
    }
    const { job_id: jobId } = await response.json();
    await pollForResult(jobId);
  } catch (error) {
    showFormError("Couldn't reach the server — check your connection and try again.");
  } finally {
    setBusy(false);
    progressEl.classList.add("hidden");
  }
});

async function safeErrorDetail(response) {
  try {
    const data = await response.json();
    return typeof data.detail === "string" ? data.detail : "";
  } catch {
    return "";
  }
}

async function pollForResult(jobId) {
  for (let attempt = 0; attempt < MAX_POLLS; attempt++) {
    await sleep(POLL_INTERVAL_MS);
    let response;
    try {
      response = await fetch(`${API_BASE_URL}/api/search/${jobId}`);
    } catch {
      continue; // transient network hiccup - just try again next tick
    }
    if (!response.ok) {
      showFormError("Lost track of that search — please try again.");
      return;
    }
    const data = await response.json();
    if (data.status === "done") {
      renderResults(data.result);
      return;
    }
    if (data.status === "error") {
      showFormError("Something went wrong while searching. Please try again.");
      return;
    }
    progressText.textContent = STATUS_TEXT[data.status] || "Working on it…";
  }
  showFormError("This is taking longer than expected — please try again in a bit.");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --- Rendering results (DOM-built, never innerHTML with server text) --------
// Job titles/descriptions/AI text all come from third-party postings or an
// AI model - never trusted as HTML. Everything below uses textContent or
// element.href, not string concatenation into innerHTML.

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function buildJobCard(job, showBoard) {
  const card = el("div", "job-card");

  const title = el("h3", "job-title");
  title.append(job.title || "(no title)");
  const company = el("span", "company", ` — ${job.company || "(company not named)"}`);
  title.append(company);
  card.append(title);

  const metaParts = [job.location, showBoard ? job.board : "", job.posted_ago].filter(Boolean);
  if (metaParts.length) {
    card.append(el("p", "job-row", `📍 ${metaParts.join(" · ")}`));
  }
  if (job.pay) {
    card.append(el("p", "job-row", `💰 ${job.pay}`));
  }
  if (job.requirements) {
    card.append(el("p", "job-row tags", `🔑 ${job.requirements}`));
  }

  const scoreLine = el("p", "job-score");
  scoreLine.append("🎯 ");
  scoreLine.append(el("span", "num", `${job.score}/100`));
  scoreLine.append(` — ${job.fit_points || ""}`);
  card.append(scoreLine);

  if (job.url) {
    const link = el("a", "job-link", "Open & apply →");
    link.href = job.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    card.append(link);
  }

  return card;
}

function renderResults(result) {
  resultsEl.innerHTML = "";
  const { linkedin = [], others = [], near = [], boards_ok: boardsOk = [], note = "" } = result;
  const total = linkedin.length + others.length;

  resultsEl.append(el("h2", "results-header", `🎯 ${total} good match${total !== 1 ? "es" : ""}`));
  resultsEl.append(el("p", "results-meta", `Searched ${boardsOk.join(", ") || "no boards (try again)"} · scored against your resume`));
  if (note) {
    resultsEl.append(el("p", "note-text", note));
  }

  if (linkedin.length) {
    resultsEl.append(el("h3", "section-title", "💼 LinkedIn posts"));
    linkedin.forEach((job) => resultsEl.append(buildJobCard(job, false)));
  }
  if (others.length) {
    resultsEl.append(el("h3", "section-title", "🌐 From other boards"));
    others.forEach((job) => resultsEl.append(buildJobCard(job, true)));
  }
  if (near.length) {
    resultsEl.append(el("h3", "section-title", "🔍 Also worth a look (weaker fit)"));
    near.forEach((job) => resultsEl.append(buildJobCard(job, true)));
  }

  resultsEl.classList.remove("hidden");
  resultsEl.scrollIntoView({ behavior: "smooth", block: "start" });
}
