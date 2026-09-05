// ---------------------------------------------------------------------------
// AI Teacher — frontend controller.
// Talks to the FastAPI backend, drives dashboard -> lesson -> quiz -> report,
// renders the knowledge map + dynamic visuals, and runs a tiny
// speech-synthesis-driven avatar (no paid avatar API needed for the MVP —
// see README for how to swap in D-ID/HeyGen later).
// ---------------------------------------------------------------------------

const API_BASE = window.API_BASE || "http://localhost:8000";

const state = {
  sessionId: null,
  language: "English",
  personality: "Friendly",
  mode: "Learn",
  totalConcepts: 0,
  conceptIndex: 0,
  startedAt: null,
  planConcepts: [],
  currentTeach: null,
  playbackSpeed: 1,
  playbackPaused: false,
  userPaused: false,
  playbackToken: 0,
  sceneIndex: 0,
  sceneQueue: [],
  studyQuiet: false,
  guidedFocus: "understand",
  flashcardBank: {},
  flashDeck: [],
  flashIndex: 0,
  flashFlipped: false,
  flashConfidence: {},
};

// ---------------------------------------------------------------------------
// Priority 6: long-term student memory needs a stable identity across visits.
// There's no login system in this MVP, so a random id is generated once and
// kept in localStorage — genuinely persists across sessions in this browser,
// at zero cost, with no account system to build for a hackathon demo.
// ---------------------------------------------------------------------------
function getStudentId() {
  let id = localStorage.getItem("ai_teacher_student_id");
  if (!id) {
    id = (crypto.randomUUID ? crypto.randomUUID() : `stu_${Date.now()}_${Math.random().toString(36).slice(2)}`);
    localStorage.setItem("ai_teacher_student_id", id);
  }
  return id;
}
state.studentId = getStudentId();

function renderWorkspaceMemory() {
  const saved = JSON.parse(localStorage.getItem("mentora_last_session") || "null");
  const resume = $("resumeSetBtn");
  const empty = $("emptySetCard");
  const history = JSON.parse(localStorage.getItem("mentora_study_sets") || "[]");
  if (empty) empty.style.display = history.length ? "none" : "flex";
  document.querySelectorAll(".saved-set-card").forEach((card) => card.remove());
  history.slice(0, 4).forEach((set) => {
    const card = document.createElement("button");
    card.className = "study-set-card saved-set-card";
    card.innerHTML = `<span class="set-card-icon">✦</span><strong>${escapeHtml(set.topic)}</strong><small>${escapeHtml(set.mode || "Learn")} · ${set.grounded ? "notes grounded" : "adaptive lesson"}</small>`;
    card.addEventListener("click", () => {
      $("topic").value = set.topic;
      $("topic").scrollIntoView({ behavior: "smooth", block: "center" });
      showToast(`${set.topic} is ready. Start a new adaptive session when you are ready.`);
    });
    $("studySetGrid").insertBefore(card, empty);
  });
  if (!resume || !saved) return;
  $("resumeSetTitle").textContent = saved.topic;
  $("resumeSetMeta").textContent = `${saved.mode || "Learn"} session · start a fresh run`;
  resume.style.display = "flex";
}

// ---------------------------------------------------------------------------
// Priority 5: Spaced Revision — show what's due right on the dashboard, using
// only data already computed deterministically server-side (progress.py).
// ---------------------------------------------------------------------------
async function loadDueReviews() {
  try {
    const res = await fetch(`${API_BASE}/api/students/${state.studentId}/reviews`);
    const data = await res.json();
    if (!data.reviews || data.reviews.length === 0) return;
    const emoji = (mastery) => (mastery <= -0.5 ? "🔴" : mastery < 1.0 ? "🟡" : "🟢");
    $("reviewList").innerHTML = data.reviews
      .slice(0, 5)
      .map(
        (r) => `<div class="review-item">
          <div>
            <span class="r-concept">${emoji(r.mastery)} ${escapeHtml(r.concept)}</span>
            <span class="r-due"> — ${escapeHtml(r.due_label)} · ${escapeHtml(r.topic)}</span>
          </div>
          <button data-topic="${escapeHtml(r.topic)}" data-concept="${escapeHtml(r.concept)}">Start review</button>
        </div>`
      )
      .join("");
    $("reviewSection").style.display = "block";
    document.querySelectorAll("#reviewList button").forEach((btn) => {
      btn.addEventListener("click", () => startReviewSession(btn.dataset.topic, btn.dataset.concept));
    });
  } catch (err) {
    console.error("failed to load due reviews", err);
  }
}

async function startReviewSession(topic, concept) {
  const form = new FormData();
  form.append("topic", topic);
  form.append("level", selectedValue("level"));
  form.append("language", selectedValue("language"));
  form.append("time_minutes", "5");
  form.append("mode", "Revision");
  form.append("review_concept", concept);
  form.append("student_id", state.studentId);

  const res = await fetch(`${API_BASE}/api/sessions`, { method: "POST", body: form });
  const data = await res.json();
  state.sessionId = data.session_id;
  state.language = selectedValue("language");
  state.mode = data.mode;
  state.totalConcepts = data.plan.concepts.length;
  state.planConcepts = data.plan.concepts;
  state.conceptIndex = 0;
  state.startedAt = Date.now();
  state.userPaused = false;
  state.playbackPaused = false;
  renderConceptDots();
  renderKnowledgeMap(data.plan.concepts.map((c) => ({ concept: c.name, status: "not_started", emoji: "⚪" })));
  startTimer();
  showScreen("lesson");
  loadNext();
}

loadDueReviews();

document.querySelectorAll(".nav-static").forEach((item) => {
  item.addEventListener("click", () => showToast(item.dataset.toast));
});

document.getElementById("navSets").addEventListener("click", () => {
  showScreen("sets");
  document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
  document.getElementById("navSets").classList.add("active");
  renderStudySetsScreen();
});

document.getElementById("navStudyPlan").addEventListener("click", () => {
  showScreen("study-plan");
  renderStudyPlanDraft();
  renderStudyPlan();
});

document.getElementById("navTutor").addEventListener("click", () => {
  showScreen("tutor");
  renderTutorSessions();
});

document.getElementById("navCalendar").addEventListener("click", () => {
  showScreen("calendar");
  document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
  document.getElementById("navCalendar").classList.add("active");
  renderCalendar();
});

document.getElementById("navMiniApps").addEventListener("click", () => {
  showScreen("mini-apps");
  document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
  document.getElementById("navMiniApps").classList.add("active");
});

document.querySelectorAll(".quick-tab[data-toast]").forEach((item) => {
  item.addEventListener("click", () => showToast(item.dataset.toast));
});

document.getElementById("quickCreateBtn").addEventListener("click", () => {
  document.getElementById("topic").focus();
  document.getElementById("topic").scrollIntoView({ behavior: "smooth", block: "center" });
});

document.getElementById("createSetBtn").addEventListener("click", () => {
  document.getElementById("topic").focus();
  document.getElementById("topic").scrollIntoView({ behavior: "smooth", block: "center" });
});

document.getElementById("resumeSetBtn").addEventListener("click", () => {
  const saved = JSON.parse(localStorage.getItem("mentora_last_session") || "null");
  if (!saved) return;
  document.getElementById("topic").value = saved.topic;
  showToast(`Ready to continue with ${saved.topic}. Start a fresh adaptive session when you're ready.`);
  document.getElementById("topic").scrollIntoView({ behavior: "smooth", block: "center" });
});

function showToast(msg, ms = 3200) {
  let el = $("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.add("visible");
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => el.classList.remove("visible"), ms);
}

// ---------- small DOM helpers ----------
const $ = (id) => document.getElementById(id);
const DEMO_ACCOUNT = { email: "student@mentora.local", password: "demo123", name: "Demo student" };

function getAuthUser() {
  return JSON.parse(localStorage.getItem("mentora_auth_user") || "null");
}

function setAuthUser(user) {
  localStorage.setItem("mentora_auth_user", JSON.stringify(user));
  $("workspaceUserName").textContent = user.name;
}

function showSignInError(message) {
  $("signInError").textContent = message;
  $("signInError").style.display = "block";
}

function clearSignInError() {
  $("signInError").textContent = "";
  $("signInError").style.display = "none";
}

function enterWorkspace(user) {
  setAuthUser(user);
  showScreen("dashboard");
  showToast(`Welcome back, ${user.name}.`);
}

function signOut() {
  localStorage.removeItem("mentora_auth_user");
  document.querySelectorAll(".screen").forEach((screen) => screen.classList.remove("active"));
  $("screen-sign-in").classList.add("active");
  $("signInPassword").value = "";
  clearSignInError();
}

$("signInForm").addEventListener("submit", (event) => {
  event.preventDefault();
  clearSignInError();
  const email = $("signInEmail").value.trim().toLowerCase();
  const password = $("signInPassword").value;
  if (email !== DEMO_ACCOUNT.email || password !== DEMO_ACCOUNT.password) return showSignInError("For now, use the demo account shown below.");
  enterWorkspace(DEMO_ACCOUNT);
});
$("demoSignInBtn").addEventListener("click", () => { $("signInEmail").value = DEMO_ACCOUNT.email; $("signInPassword").value = DEMO_ACCOUNT.password; enterWorkspace(DEMO_ACCOUNT); });
$("signOutBtn").addEventListener("click", signOut);
function applyTheme(dark) {
  document.body.classList.toggle("dark-mode", dark);
  $("themeToggle").querySelector(".theme-toggle-icon").textContent = dark ? "☀" : "☾";
  $("themeToggle").querySelector("span:last-child").textContent = dark ? "Light mode" : "Dark mode";
  localStorage.setItem("mentora_theme", dark ? "dark" : "light");
}
applyTheme(localStorage.getItem("mentora_theme") === "dark");
$("themeToggle").addEventListener("click", () => applyTheme(!document.body.classList.contains("dark-mode")));
const authUser = getAuthUser();
if (authUser) {
  setAuthUser(authUser);
  $("screen-sign-in").classList.remove("active");
} else {
  document.querySelectorAll(".screen").forEach((screen) => screen.classList.remove("active"));
  $("screen-sign-in").classList.add("active");
}
renderWorkspaceMemory();
function showScreen(name) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  $(`screen-${name}`).classList.add("active");
  document.body.classList.toggle("focus-mode", name === "lesson" || name === "split-study" || name === "flashcards");
  document.querySelectorAll(".nav-link").forEach((item) => item.classList.remove("active"));
  if (name === "dashboard") $("navHome").classList.add("active");
  if (name === "sign-in") document.querySelectorAll(".nav-link, .theme-toggle").forEach((item) => item.classList.remove("active"));
  if (name === "progress") $("navProgress").classList.add("active");
  if (name === "study-plan") $("navStudyPlan").classList.add("active");
  if (name === "sets") $("navSets").classList.add("active");
  if (name === "tutor") $("navTutor").classList.add("active");
  if (name === "calendar") $("navCalendar").classList.add("active");
  if (name === "mini-apps") $("navMiniApps").classList.add("active");
}
function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}
function listOrNone(arr) {
  if (!arr || arr.length === 0) return "<li>None recorded</li>";
  return arr.map((x) => `<li>${escapeHtml(x)}</li>`).join("");
}

function getSavedSets() {
  return JSON.parse(localStorage.getItem("mentora_study_sets") || "[]");
}

let tutorSessionFilter = "";
let tutorTimeMinutes = 25;

function openTutorSession(topic) {
  $("topic").value = topic || "";
  showScreen("dashboard");
  $("topic").scrollIntoView({ behavior: "smooth", block: "center" });
  $("topic").focus();
  showToast(topic ? `Ready to tutor you on ${topic}. Start the lesson when you are ready.` : "Choose a topic or upload your course material.");
}

function stopLessonPlayback() {
  state.playbackToken++;
  state.skipRequested = true;
  state.playbackPaused = false;
  state.userPaused = false;
  state.sceneQueue = [];
  stopSpeaking();
  clearInterval(window._timerInt);
}

function renderTutorSessions() {
  const sets = getSavedSets().filter((set) => !tutorSessionFilter || set.topic.toLowerCase().includes(tutorSessionFilter.toLowerCase()));
  const allSets = getSavedSets();
  $("tutorSessionCount").textContent = `${allSets.length} session${allSets.length === 1 ? "" : "s"}`;
  $("tutorSessionGrid").innerHTML = sets.map((set, index) => `<article class="tutor-session-card"><div class="tutor-session-card-top"><span class="tutor-session-index">${String(index + 1).padStart(2, "0")}</span><span class="tutor-session-status">${set.grounded ? "Notes grounded" : "Ready to learn"}</span></div><h3>${escapeHtml(set.topic)}</h3><p>${escapeHtml(set.mode || "Learn")} · ${set.savedAt ? new Date(set.savedAt).toLocaleDateString() : "Saved locally"}</p><button class="tutor-session-open" data-tutor-topic="${escapeHtml(set.topic)}">Open tutor →</button></article>`).join("");
  $("tutorEmptyState").style.display = sets.length ? "none" : "flex";
  $("tutorSessionGrid").style.display = sets.length ? "grid" : "none";
  document.querySelectorAll("[data-tutor-topic]").forEach((button) => button.addEventListener("click", () => openTutorSession(button.dataset.tutorTopic)));
}

function addTutorChatMessage(text, kind) {
  const message = document.createElement("div");
  message.className = `tutor-chat-message ${kind}`;
  message.textContent = text;
  $("tutorChatMessages").appendChild(message);
  $("tutorChatMessages").scrollTop = $("tutorChatMessages").scrollHeight;
}

function answerTutorPrompt(prompt) {
  const sets = getSavedSets();
  const lower = prompt.toLowerCase();
  if (lower.includes("next")) return sets.length ? `Your next saved session is ${sets[0].topic}. Open it to continue, or start a new session with a different topic.` : "Start with a topic you want to understand. I will turn it into an adaptive lesson with explanations, questions, and feedback.";
  if (lower.includes("new") || lower.includes("session")) { openTutorSession(""); return "I opened the lesson setup. Add your topic or course material and I will build the tutoring session."; }
  if (lower.includes("choose") || lower.includes("topic")) return sets.length ? `You have ${sets.length} saved topic${sets.length === 1 ? "" : "s"}: ${sets.slice(0, 3).map((set) => set.topic).join(", ")}. Pick one to continue or enter a new topic.` : "Try a concrete question, exam subject, or uploaded set of notes. Specific topics make the first explanation sharper.";
  return "I can help you pick a topic, continue a saved session, or start a new adaptive lesson. Ask me what to study next.";
}

function submitTutorChat(prompt) {
  const text = prompt.trim();
  if (!text) return;
  addTutorChatMessage(text, "student");
  addTutorChatMessage(answerTutorPrompt(text), "tutor");
}

$("tutorNewSessionBtn").addEventListener("click", () => openTutorSession(""));
$("tutorEmptyStartBtn").addEventListener("click", () => openTutorSession(""));
$("tutorPlanBtn").addEventListener("click", () => { showScreen("study-plan"); renderStudyPlanDraft(); renderStudyPlan(); });
$("tutorSearchInput").addEventListener("input", (event) => { tutorSessionFilter = event.target.value; renderTutorSessions(); });
$("tutorGridViewBtn").addEventListener("click", () => { $("tutorSessionGrid").classList.remove("list-view"); $("tutorGridViewBtn").classList.add("active"); $("tutorListViewBtn").classList.remove("active"); });
$("tutorListViewBtn").addEventListener("click", () => { $("tutorSessionGrid").classList.add("list-view"); $("tutorListViewBtn").classList.add("active"); $("tutorGridViewBtn").classList.remove("active"); });
$("tutorSetPicker").addEventListener("click", () => { const sets = getSavedSets(); $("tutorSetLabel").textContent = sets.length ? sets[0].topic : "All study sets"; showToast(sets.length ? `Showing ${sets[0].topic}.` : "Create a study set first."); });
$("tutorTimeBtn").addEventListener("click", () => { tutorTimeMinutes = tutorTimeMinutes === 25 ? 45 : tutorTimeMinutes === 45 ? 60 : 25; $("tutorTimeBtn").querySelector("span").textContent = `${tutorTimeMinutes}m`; showToast(`Tutor session target set to ${tutorTimeMinutes} minutes.`); });
$("tutorShareBtn").addEventListener("click", async () => { try { await navigator.clipboard.writeText(window.location.href); showToast("Tutor workspace link copied."); } catch { showToast("Tutor workspace is ready to share from this browser."); } });
$("tutorInfoBtn").addEventListener("click", () => showToast("Skippy adapts explanations and questions to your level and answers."));
$("tutorClearChatBtn").addEventListener("click", () => { $("tutorChatMessages").innerHTML = ""; addTutorChatMessage("What would you like to learn?", "tutor"); });
document.querySelectorAll("[data-tutor-prompt]").forEach((button) => button.addEventListener("click", () => submitTutorChat(button.dataset.tutorPrompt)));
$("tutorChatSend").addEventListener("click", () => { submitTutorChat($("tutorChatInput").value); $("tutorChatInput").value = ""; });
$("tutorChatInput").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("tutorChatSend").click(); } });

let studyPlanMode = "comprehensive";
let studyPlanSort = "recommended";
let studyPlanFilter = false;
let studyPlanDifficulty = "Beginner";
let studyPlanTargetMinutes = 25;

function getStudyPlanDraft() {
  return JSON.parse(localStorage.getItem("mentora_study_plan_draft") || "[]");
}

function saveStudyPlanDraft(draft) {
  localStorage.setItem("mentora_study_plan_draft", JSON.stringify(draft));
}

function planDurationForDifficulty(difficulty, index) {
  const base = difficulty === "Advanced" ? 14 : difficulty === "Intermediate" ? 10 : 6;
  return studyPlanMode === "focused" ? Math.max(3, Math.round(base * 0.55)) : base + Math.min(index, 3);
}

function getStudyPlanItems() {
  const saved = JSON.parse(localStorage.getItem("mentora_last_session") || "null");
  const topic = (saved && saved.topic) || (getSavedSets()[0] && getSavedSets()[0].topic) || "Your next study set";
  const draft = getStudyPlanDraft();
  if (draft.length) {
    const skipped = JSON.parse(localStorage.getItem("mentora_plan_skipped") || "{}");
    return draft.map((item, index) => ({
      ...item,
      index,
      skipped: Boolean(skipped[`${topic}:${item.name}`]),
      covered: Boolean(item.covered),
      duration: planDurationForDifficulty(item.difficulty, index),
      objective: `${item.difficulty} ${item.category.toLowerCase()} study path for ${item.name}.`,
    }));
  }
  const source = state.planConcepts.length ? state.planConcepts : [];
  const names = source.length ? source.map((item) => ({ name: item.name, objective: item.objective })) : [
    { name: `Introduction to ${topic}`, objective: "Build a clear mental model of the main idea.", category: "General", difficulty: "Beginner" },
    { name: `${topic}: key principles`, objective: "Connect the important definitions and relationships.", category: "General", difficulty: "Intermediate" },
    { name: `${topic}: worked examples`, objective: "Apply the idea to a concrete example.", category: "General", difficulty: "Intermediate" },
    { name: `${topic}: check your understanding`, objective: "Test recall and identify what needs another pass.", category: "General", difficulty: "Advanced" },
  ];
  const skipped = JSON.parse(localStorage.getItem("mentora_plan_skipped") || "{}");
  return names.map((item, index) => ({
    ...item,
    index,
    skipped: Boolean(skipped[`${topic}:${item.name}`]),
    covered: Boolean(state.conceptIndex > index || (saved && saved.finished && index < 1)),
    category: item.category || "General",
    difficulty: item.difficulty || "Beginner",
    duration: item.duration || planDurationForDifficulty(item.difficulty || "Beginner", index),
  }));
}

function renderStudyPlan() {
  const saved = JSON.parse(localStorage.getItem("mentora_last_session") || "null");
  const topic = (saved && saved.topic) || (getSavedSets()[0] && getSavedSets()[0].topic) || "Your next study set";
  const items = getStudyPlanItems().filter((item) => !studyPlanFilter || !item.covered);
  const allItems = getStudyPlanItems();
  if (studyPlanSort === "shortest") items.sort((a, b) => a.duration - b.duration);
  $("studyPlanTitle").textContent = topic;
  $("studyPlanTopicLabel").textContent = topic;
  $("studyPlanTopicCount").textContent = allItems.length;
  $("studyPlanCoveredCount").textContent = allItems.filter((item) => item.covered).length;
  $("studyPlanMasteredCount").textContent = allItems.filter((item) => item.covered && !item.skipped).length;
  $("studyPlanProgressFill").style.width = `${allItems.length ? Math.round(allItems.filter((item) => item.covered).length / allItems.length * 100) : 0}%`;
  $("studyPlanNextHint").textContent = items.length ? `${items[0].duration} minutes · ${items[0].name}` : "Everything is covered for now";
  $("studyPlanTimeline").innerHTML = items.length ? items.map((item, index) => `
    <article class="plan-topic-card ${index === 0 ? "is-next" : ""} ${item.covered ? "is-covered" : ""}">
      <button class="plan-topic-marker" data-plan-action="toggle" data-plan-index="${item.index}" aria-label="Mark ${escapeHtml(item.name)} as covered">${item.covered ? "✓" : ""}</button>
      <div class="plan-topic-copy"><div class="plan-topic-meta"><span>${index === 0 ? "NEXT UP" : `TOPIC ${String(index + 1).padStart(2, "0")}`}</span><small>${escapeHtml(item.category)} · ${escapeHtml(item.difficulty)} · ${item.duration} min</small></div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.objective)}</p></div>
      <div class="plan-topic-actions"><button class="plan-save-btn" data-plan-action="start" data-plan-index="${item.index}">${index === 0 && !item.covered ? "Start learning" : "Study"} <span>→</span></button><button class="plan-skip-btn" data-plan-action="skip" data-plan-index="${item.index}">${item.skipped ? "Restore" : "Skip"}</button></div>
    </article>`).join("") : `<div class="plan-empty"><strong>Your plan is clear.</strong><p>Turn off the incomplete filter to see completed topics.</p></div>`;
  $("studyPlanTimeline").onclick = (event) => {
    const button = event.target.closest("[data-plan-action]");
    if (button) handleStudyPlanAction(button.dataset.planAction, Number(button.dataset.planIndex));
  };
}

function renderStudyPlanDraft() {
  const draft = getStudyPlanDraft();
  $("studyPlanDraftList").innerHTML = draft.length ? draft.map((item, index) => `<div class="study-plan-draft-item"><span class="study-plan-draft-number">${index + 1}</span><div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.category)} · ${escapeHtml(item.difficulty)}</small></div><button data-remove-plan-topic="${index}" title="Remove topic" aria-label="Remove ${escapeHtml(item.name)}">×</button></div>`).join("") : `<div class="study-plan-draft-empty">No topics added yet. Add at least one topic to generate a plan.</div>`;
  $("studyPlanBuilderHint").textContent = draft.length ? `${draft.length} topic${draft.length === 1 ? "" : "s"} ready. Add more subjects or generate the plan.` : "Add topics with their own difficulty to build a realistic plan.";
  document.querySelectorAll("[data-remove-plan-topic]").forEach((button) => button.addEventListener("click", () => {
    const next = getStudyPlanDraft();
    next.splice(Number(button.dataset.removePlanTopic), 1);
    saveStudyPlanDraft(next);
    renderStudyPlanDraft();
    renderStudyPlan();
  }));
}

function addStudyPlanTopic() {
  const input = $("studyPlanTopicInput");
  const name = input.value.trim();
  if (!name) return showToast("Add a topic before building the plan.");
  const draft = getStudyPlanDraft();
  if (draft.some((item) => item.name.toLowerCase() === name.toLowerCase())) return showToast("That topic is already in your plan.");
  draft.push({ name, category: $("studyPlanCategoryInput").value, difficulty: studyPlanDifficulty, covered: false });
  saveStudyPlanDraft(draft);
  input.value = "";
  renderStudyPlanDraft();
  renderStudyPlan();
}

function generateStudyPlan() {
  const draft = getStudyPlanDraft();
  if (!draft.length) return showToast("Add at least one topic first.");
  const current = getSavedSets()[0];
  localStorage.setItem("mentora_last_session", JSON.stringify({ ...(current || {}), topic: draft[0].name, mode: "Learn", savedAt: Date.now() }));
  renderStudyPlan();
  showToast(`Generated a ${studyPlanMode} plan for ${draft.length} topic${draft.length === 1 ? "" : "s"}.`);
}

function handleStudyPlanAction(action, index) {
  const allItems = getStudyPlanItems();
  const item = allItems[index];
  if (!item) return;
  const saved = JSON.parse(localStorage.getItem("mentora_last_session") || "null");
  const topic = (saved && saved.topic) || (getSavedSets()[0] && getSavedSets()[0].topic) || item.name;
  if (action === "toggle") {
    state.conceptIndex = item.covered ? 0 : index + 1;
    renderStudyPlan();
    return;
  }
  if (action === "skip") {
    const skipped = JSON.parse(localStorage.getItem("mentora_plan_skipped") || "{}");
    const key = `${topic}:${item.name}`;
    if (skipped[key]) delete skipped[key]; else skipped[key] = true;
    localStorage.setItem("mentora_plan_skipped", JSON.stringify(skipped));
    renderStudyPlan();
    return;
  }
  $("topic").value = topic;
  showScreen("dashboard");
  $("topic").scrollIntoView({ behavior: "smooth", block: "center" });
  showToast(`Ready to study ${item.name}. Start a lesson when you are ready.`);
}

function addStudyPlanChatMessage(text, kind) {
  const message = document.createElement("div");
  message.className = `study-plan-chat-message ${kind}`;
  message.textContent = text;
  $("studyPlanChatMessages").appendChild(message);
  $("studyPlanChatMessages").scrollTop = $("studyPlanChatMessages").scrollHeight;
}

function answerStudyPlanChat(prompt) {
  const topic = $("studyPlanTitle").textContent;
  const lower = prompt.toLowerCase();
  if (lower.includes("first") || lower.includes("start")) return `Start with the first uncompleted topic in ${topic}. It is the shortest path to a useful win, and the Start learning button will carry its context into your next lesson.`;
  if (lower.includes("how long") || lower.includes("take")) return `This ${studyPlanMode} plan has ${getStudyPlanItems().reduce((total, item) => total + item.duration, 0)} minutes of focused work. Use Focused mode when you only have a few minutes.`;
  if (lower.includes("create") || lower.includes("plan")) return `I shaped ${topic} into ${getStudyPlanItems().length} steps. You can reorder by time, skip a topic for later, or start the recommended next step.`;
  return `I’m looking at your ${topic} plan. Try asking what to study first, how long it will take, or ask me to create a focused plan.`;
}

function submitStudyPlanChat(rawPrompt) {
  const prompt = rawPrompt.trim();
  if (!prompt) return;
  addStudyPlanChatMessage(prompt, "student");
  addStudyPlanChatMessage(answerStudyPlanChat(prompt), "tutor");
}

document.querySelectorAll("[data-plan-mode]").forEach((button) => button.addEventListener("click", () => {
  studyPlanMode = button.dataset.planMode;
  document.querySelectorAll("[data-plan-mode]").forEach((item) => item.classList.toggle("active", item === button));
  renderStudyPlan();
}));
document.querySelectorAll("[data-plan-sort]").forEach((button) => button.addEventListener("click", () => {
  studyPlanSort = button.dataset.planSort;
  document.querySelectorAll("[data-plan-sort]").forEach((item) => item.classList.toggle("active", item === button));
  renderStudyPlan();
}));
$("studyPlanFilterBtn").addEventListener("click", () => { studyPlanFilter = !studyPlanFilter; $("studyPlanFilterBtn").classList.toggle("active", studyPlanFilter); renderStudyPlan(); });
$("studyPlanRefreshBtn").addEventListener("click", () => { renderStudyPlanDraft(); renderStudyPlan(); showToast("Study plan refreshed from your saved topics."); });
$("studyPlanTimeBtn").addEventListener("click", () => { studyPlanTargetMinutes = studyPlanTargetMinutes === 25 ? 45 : studyPlanTargetMinutes === 45 ? 60 : 25; $("studyPlanTimeBtn").querySelector("span").textContent = `${studyPlanTargetMinutes}m`; showToast(`Study target set to ${studyPlanTargetMinutes} minutes.`); });
$("studyPlanSettingsBtn").addEventListener("click", () => { $("studyPlanBuilder").style.display = "block"; $("studyPlanTopicInput").focus(); });
$("studyPlanBuilderClose").addEventListener("click", () => { $("studyPlanBuilder").style.display = "none"; });
$("studyPlanAddTopicBtn").addEventListener("click", addStudyPlanTopic);
$("studyPlanTopicInput").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); addStudyPlanTopic(); } });
document.querySelectorAll("[data-plan-difficulty]").forEach((button) => button.addEventListener("click", () => { studyPlanDifficulty = button.dataset.planDifficulty; document.querySelectorAll("[data-plan-difficulty]").forEach((item) => item.classList.toggle("active", item === button)); }));
$("studyPlanGenerateBtn").addEventListener("click", generateStudyPlan);
$("studyPlanNewChatBtn").addEventListener("click", () => { $("studyPlanChatMessages").innerHTML = ""; addStudyPlanChatMessage("What would you like to focus on?", "tutor"); });
document.querySelectorAll("[data-plan-prompt]").forEach((button) => button.addEventListener("click", () => submitStudyPlanChat(button.dataset.planPrompt)));
$("studyPlanChatSend").addEventListener("click", () => { submitStudyPlanChat($("studyPlanChatInput").value); $("studyPlanChatInput").value = ""; });
$("studyPlanChatInput").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("studyPlanChatSend").click(); } });

function renderStudySetsScreen() {
  const sets = getSavedSets();
  const container = $("setsContent");
  if (!sets.length) {
    container.innerHTML = `<div class="feature-empty"><span>▦</span><strong>No study sets yet</strong><p>Create a topic or upload notes from Home and your sets will appear here.</p><button class="btn btn-primary" id="emptySetsCreateBtn">Create your first set</button></div>`;
    $("emptySetsCreateBtn").addEventListener("click", () => {
      showScreen("dashboard");
      $("topic").focus();
    });
    return;
  }
  container.innerHTML = sets.map((set, index) => `<article class="library-set"><div class="library-set-icon">${index + 1}</div><div class="library-set-copy"><h3>${escapeHtml(set.topic)}</h3><p>${escapeHtml(set.mode || "Learn")} · ${set.grounded ? "Grounded in uploaded material" : "Adaptive lesson"}</p><small>Created ${new Date(set.savedAt).toLocaleDateString()}</small></div><div class="library-set-actions"><button class="btn btn-ghost set-study-btn" data-topic="${escapeHtml(set.topic)}">Study</button><button class="btn btn-ghost set-remove-btn" data-topic="${escapeHtml(set.topic)}">Remove</button></div></article>`).join("");
  container.querySelectorAll(".set-study-btn").forEach((button) => button.addEventListener("click", () => {
    $("topic").value = button.dataset.topic;
    showScreen("dashboard");
    $("topic").scrollIntoView({ behavior: "smooth", block: "center" });
  }));
  container.querySelectorAll(".set-remove-btn").forEach((button) => button.addEventListener("click", () => {
    const remaining = sets.filter((set) => set.topic !== button.dataset.topic);
    localStorage.setItem("mentora_study_sets", JSON.stringify(remaining));
    renderStudySetsScreen();
    renderWorkspaceMemory();
  }));
}

let calendarCursor = new Date();
function renderCalendar() {
  const year = calendarCursor.getFullYear();
  const month = calendarCursor.getMonth();
  $("calendarMonthLabel").textContent = calendarCursor.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = new Date();
  const grid = $("calendarGrid");
  grid.innerHTML = "";
  for (let i = 0; i < firstDay; i++) grid.appendChild(document.createElement("span"));
  for (let day = 1; day <= daysInMonth; day++) {
    const cell = document.createElement("button");
    cell.className = "calendar-day" + (day === today.getDate() && month === today.getMonth() && year === today.getFullYear() ? " today" : "");
    cell.textContent = day;
    grid.appendChild(cell);
  }
  const sets = getSavedSets();
  $("calendarAgenda").innerHTML = sets.length ? `<h3>Study plan</h3>${sets.slice(0, 5).map((set, index) => `<div class="agenda-item"><span class="agenda-dot"></span><div><strong>${escapeHtml(set.topic)}</strong><small>${index === 0 ? "Next up" : "Review when ready"}</small></div></div>`).join("")}` : `<div class="feature-empty compact"><span>□</span><strong>Your calendar is clear</strong><p>Start a study set and your review rhythm will appear here.</p></div>`;
}

$("setsCreateBtn").addEventListener("click", () => { showScreen("dashboard"); $("topic").focus(); });
$("calendarPrevBtn").addEventListener("click", () => { calendarCursor.setMonth(calendarCursor.getMonth() - 1); renderCalendar(); });
$("calendarNextBtn").addEventListener("click", () => { calendarCursor.setMonth(calendarCursor.getMonth() + 1); renderCalendar(); });
$("calendarTodayBtn").addEventListener("click", () => { calendarCursor = new Date(); renderCalendar(); });
$("miniTutorBtn").addEventListener("click", () => { showScreen("dashboard"); $("topic").focus(); $("topic").scrollIntoView({ behavior: "smooth", block: "center" }); });
$("miniRecallBtn").addEventListener("click", () => {
  const set = getSavedSets()[0];
  const panel = $("miniAppPanel");
  panel.style.display = "block";
  panel.innerHTML = set ? `<h3>Quick recall: ${escapeHtml(set.topic)}</h3><p>In your own words, what is the most important idea from this study set?</p><textarea id="recallAnswer" placeholder="Type your recall here…"></textarea><button class="btn btn-primary" id="saveRecallBtn">Save reflection</button><span id="recallSaved" class="mini-feedback"></span>` : `<div class="feature-empty compact"><strong>Create a study set first</strong><p>Quick recall needs a topic to work from.</p></div>`;
  if ($("saveRecallBtn")) $("saveRecallBtn").addEventListener("click", () => { $("recallSaved").textContent = "Saved to your local study journal."; });
});
$("miniNotesBtn").addEventListener("click", () => {
  const saved = JSON.parse(localStorage.getItem("mentora_last_session") || "null");
  if (!saved || !saved.sessionId) return showToast("Complete a lesson first to generate study notes.");
  state.sessionId = saved.sessionId;
  showScreen("report");
  $("viewNotesBtn").click();
});

// ---------- dashboard: pill selectors ----------
document.querySelectorAll(".pill-group").forEach((group) => {
  group.addEventListener("click", (e) => {
    const btn = e.target.closest(".pill");
    if (!btn) return;
    [...group.children].forEach((c) => c.classList.remove("selected"));
    btn.classList.add("selected");
  });
});
function selectedValue(groupName) {
  const group = document.querySelector(`.pill-group[data-group="${groupName}"]`);
  return group.querySelector(".pill.selected").dataset.value;
}

function selectPill(groupName, value) {
  const group = document.querySelector(`.pill-group[data-group="${groupName}"]`);
  if (!group) return;
  const btn = [...group.children].find((c) => c.dataset.value === value);
  if (btn) btn.click(); // reuses the real selection handler, no shortcuts taken
}

// ---------- top nav ----------
$("navHome").addEventListener("click", () => { stopLessonPlayback(); showScreen("dashboard"); loadDueReviews(); });
$("navProgress").addEventListener("click", goToProgress);
$("lessonHomeBtn").addEventListener("click", () => {
  stopLessonPlayback();
  showScreen("dashboard");
  loadDueReviews();
});

// ---------------------------------------------------------------------------
// Demo Mode: pre-fills a compelling, reliable topic and jumps straight to
// "Start lesson". This is convenience only — it clicks through the exact
// same pills and the exact same startBtn handler a real user would use, so
// every downstream step (planning, teaching, misconception detection,
// explain-it-back, quiz, report) is the real system end to end. Nothing
// about the lesson content or AI behavior is hard-coded or faked.
// ---------------------------------------------------------------------------
$("demoModeBtn").addEventListener("click", () => {
  $("topic").value = "Ohm's Law";
  $("goal").value = "Understand how voltage, current, and resistance relate.";
  $("fileInput").value = "";
  $("uploadBox").classList.remove("has-file");
  $("uploadLabel").textContent = "📎 Drop your notes here — the lesson will be grounded in them";
  selectPill("level", "Beginner");
  selectPill("language", "English");
  selectPill("time", "5");
  selectPill("personality", "Friendly");
  selectPill("strategy", "Standard");
  selectPill("mode", "Learn");
  showToast("Demo Mode ready — starting a real 5-minute lesson on Ohm's Law.");
  $("startBtn").click();
});

$("fileInput").addEventListener("change", () => {
  const box = $("uploadBox");
  const f = $("fileInput").files[0];
  if (f) {
    box.classList.add("has-file");
    $("uploadLabel").textContent = `✅ ${f.name} — will ground the lesson`;
  } else {
    box.classList.remove("has-file");
    $("uploadLabel").textContent = "📎 Drop your notes here — the lesson will be grounded in them";
  }
});

// ---------- start lesson ----------
$("startBtn").addEventListener("click", async () => {
  const topic = $("topic").value.trim();
  const file = $("fileInput").files[0];
  if (!topic && !file) {
    return showDashError("Give me a topic, or upload some notes to teach from.");
  }
  hideDashError();
  $("startBtn").disabled = true;
  $("startBtn").textContent = "Building your lesson…";

  const form = new FormData();
  form.append("topic", topic || (file ? file.name.replace(/\.[^.]+$/, "") : "General topic"));
  form.append("level", selectedValue("level"));
  form.append("language", selectedValue("language"));
  form.append("time_minutes", selectedValue("time"));
  form.append("learning_goal", $("goal").value.trim());
  form.append("personality", selectedValue("personality"));
  form.append("mode", selectedValue("mode"));
  form.append("strategy", selectedValue("strategy"));
  form.append("student_id", state.studentId);
  if (file) form.append("file", file);

  try {
    const res = await fetch(`${API_BASE}/api/sessions`, { method: "POST", body: form });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    state.sessionId = data.session_id;
    state.language = selectedValue("language");
    state.personality = data.personality || selectedValue("personality");
    state.strategy = data.strategy || selectedValue("strategy");
    state.mode = data.mode;
    state.totalConcepts = data.plan.concepts.length;
    state.planConcepts = data.plan.concepts;
    state.conceptIndex = 0;
    state.startedAt = Date.now();
    state.userPaused = false;
    state.playbackPaused = false;
    const savedSet = { topic, mode: data.mode, grounded: Boolean(data.grounded), sessionId: data.session_id, savedAt: Date.now() };
    localStorage.setItem("mentora_last_session", JSON.stringify(savedSet));
    const sets = JSON.parse(localStorage.getItem("mentora_study_sets") || "[]");
    localStorage.setItem("mentora_study_sets", JSON.stringify([savedSet, ...sets.filter((item) => item.topic !== topic)].slice(0, 12)));
    renderWorkspaceMemory();

    if (selectedValue("mode") === "Revision" && data.mode === "Learn") {
      showToast("No prior weak concepts found for this topic yet — starting a normal Learn session instead.");
    } else if (data.time_plan && data.time_plan.honesty_message) {
      // Priority 3: Time-Budget Honesty — surfaced both here and as the
      // lesson's opening line (see backend recall_note handling).
      showToast(data.time_plan.honesty_message, 5000);
    }

    $("langSwitch").value = state.language;
    $("personalitySwitch").value = state.personality;
    renderConceptDots();
    renderKnowledgeMap(data.plan.concepts.map((c) => ({ concept: c.name, status: "not_started", emoji: "⚪" })));
    startTimer();
    showScreen("lesson");
    loadNext();
  } catch (err) {
    showDashError("Couldn't start the lesson: " + err.message + ". Is the backend running?");
  } finally {
    $("startBtn").disabled = false;
    $("startBtn").textContent = "Start lesson →";
  }
});

function showDashError(msg) {
  const el = $("dashError");
  el.textContent = msg;
  el.style.display = "block";
}
function hideDashError() {
  $("dashError").style.display = "none";
}

// ---------- mid-lesson language switch ----------
$("langSwitch").addEventListener("change", async () => {
  const newLang = $("langSwitch").value;
  state.language = newLang;
  if (!state.sessionId) return;
  try {
    await fetch(`${API_BASE}/api/sessions/${state.sessionId}/language`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: newLang }),
    });
    // Context (concept index, mastery, difficulty) is untouched server-side —
    // only content generated from now on uses the new language.
    showToast(`Language switched to ${newLang} — next explanation will use it.`);
  } catch (err) {
    console.error("language switch failed", err);
    showToast("Couldn't switch language — please try again.");
  }
});

// ---------- mid-lesson personality switch ----------
$("personalitySwitch").addEventListener("change", async () => {
  const newPersonality = $("personalitySwitch").value;
  state.personality = newPersonality;
  if (!state.sessionId) return;
  try {
    await fetch(`${API_BASE}/api/sessions/${state.sessionId}/personality`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ personality: newPersonality }),
    });
    showToast(`Teacher personality set to ${newPersonality}.`);
  } catch (err) {
    console.error("personality switch failed", err);
    showToast("Couldn't switch personality — please try again.");
  }
});

// "Read aloud" should feel instantaneous: if the student unchecks it mid-
// sentence, cut the teacher off right away rather than waiting for the
// current utterance to finish on its own.
$("voiceToggle").addEventListener("change", () => {
  if (!$("voiceToggle").checked) stopSpeaking();
});

// ---------- timer ----------
function startTimer() {
  clearInterval(window._timerInt);
  window._timerInt = setInterval(() => {
    const secs = Math.floor((Date.now() - state.startedAt) / 1000);
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    const timeText = `${m}:${s.toString().padStart(2, "0")}`;
    $("timerLabel").textContent = `${timeText} elapsed`;
    if ($("playerTimeLabel")) $("playerTimeLabel").textContent = timeText;
  }, 1000);
}

// ---------- score ring (report screen) ----------
function updateScoreRing(percent) {
  const circumference = 320.4; // 2 * PI * r(51), matches the SVG circle in index.html
  const ring = $("scoreRingFill");
  if (!ring) return;
  const offset = circumference - (Math.max(0, Math.min(100, percent)) / 100) * circumference;
  // rAF so the browser registers the starting dashoffset before animating to the real one
  requestAnimationFrame(() => { ring.style.strokeDashoffset = offset; });
}

// ---------- concept dots + difficulty meter + knowledge map ----------
function renderConceptDots() {
  const wrap = $("conceptDots");
  wrap.innerHTML = "";
  for (let i = 0; i < state.totalConcepts; i++) {
    const dot = document.createElement("div");
    dot.className = "concept-dot";
    if (i < state.conceptIndex) dot.classList.add("done");
    if (i === state.conceptIndex) dot.classList.add("current");
    wrap.appendChild(dot);
  }
  const percent = state.totalConcepts > 0 ? Math.round((state.conceptIndex / state.totalConcepts) * 100) : 0;
  $("lessonProgressFill").style.width = `${percent}%`;
  $("lessonProgressLabel").textContent = `${percent}% · concept ${Math.min(state.conceptIndex + 1, state.totalConcepts)} of ${state.totalConcepts}`;
}

function renderDifficulty(level) {
  const wrap = $("diffMeter");
  wrap.innerHTML = "";
  for (let i = 1; i <= 5; i++) {
    const tick = document.createElement("div");
    tick.className = "diff-tick" + (i <= level ? " filled" : "");
    wrap.appendChild(tick);
  }
}

function renderKnowledgeMap(map) {
  const statusLabel = { mastered: "mastered", learning: "learning", weak: "weak", not_started: "pending" };
  $("knowledgeList").innerHTML = map
    .map(
      (c) => `<li>
        <span class="k-emoji">${c.emoji}</span>
        <span class="k-name">${escapeHtml(c.concept)}</span>
        <span class="k-status">${statusLabel[c.status] || c.status}</span>
      </li>`
    )
    .join("");
}

// ---------- fetch + render next teaching turn ----------
async function loadNext() {
  $("lessonLoading").style.display = "flex";
  $("lessonContent").style.display = "none";
  $("questionPanel").style.display = "none";
  $("feedbackBanner").style.display = "none";
  hideCheckin();
  setTeacherStatus("thinking");

  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/next`);
  const data = await res.json();

  if (data.quiz_ready) return goToQuiz();
  if (data.finished) return finishLesson();
  renderTeach(data.teach);
}

function renderTeach(teach) {
  state.conceptIndex = teach.concept_index;
  state.totalConcepts = teach.total_concepts;
  state.currentTeach = teach;
  state.skipRequested = false;
  state.questionRevealed = false;
  if (teach.concept) {
    // Grow the flashcard deck live as the student progresses — each concept
    // taught becomes a term/definition card, so "Flashcards" always reflects
    // what's actually been covered so far in this session.
    state.flashcardBank[teach.concept] = {
      term: teach.concept,
      definition: teach.explanation || (teach.slide?.bullets || [])[0] || "Definition coming soon.",
      example: teach.example || teach.analogy || "",
    };
  }
  renderConceptDots();
  renderDifficulty(teach.difficulty);

  $("reteachBadge").style.display = teach.is_reteach ? "inline-block" : "none";
  renderGroundingBadge(teach);
  $("slideTitle").textContent = teach.slide?.title || teach.concept;
  $("conceptLabel").textContent = `Concept ${teach.concept_index + 1} of ${teach.total_concepts}: ${teach.concept}`;
  $("answerInput").value = "";
  $("questionPanel").style.display = "none";
  hideCheckin();

  $("lessonLoading").style.display = "none";
  $("lessonContent").style.display = "block";

  const scenes = teach.scenes && teach.scenes.length ? teach.scenes : fallbackScenes(teach);
  playScenes(scenes, teach);
}

// Makes RAG retrieval visible to the student without exposing the
// retrieval machinery itself — just an honest "where did this come from".
function renderGroundingBadge(teach) {
  const badge = $("groundingBadge");
  if (teach.grounded && teach.sources && teach.sources.length) {
    const s = teach.sources[0];
    const pageInfo = s.page ? `, p.${s.page}` : "";
    badge.textContent = `📄 Based on your uploaded material${s.source ? " — " + s.source + pageInfo : ""}`;
    badge.className = "grounding-badge";
    badge.style.display = "inline-flex";
  } else if (teach.grounded_but_empty) {
    badge.textContent = "ℹ️ Not covered in your notes — using general knowledge here";
    badge.className = "grounding-badge general";
    badge.style.display = "inline-flex";
  } else {
    badge.style.display = "none";
  }
}

function fallbackScenes(teach) {
  // Safety net if an older cached teach payload has no `scenes` field.
  return [{
    id: "scene_0", type: "explain",
    narration: `${teach.explanation} ${teach.analogy || ""} ${teach.example ? "For example, " + teach.example : ""}`,
    on_screen_text: teach.slide?.bullets || [], visual: teach.visual, duration: 10, transition: "fade",
    question: { text: teach.question, question_type: teach.question_type },
  }];
}

// A few rotating, natural check-in lines used ONLY as a frontend pacing cue
// between "explaining" and "asking the real question" — never sent to the
// backend, never blocking, purely conversational texture. Rotated (and never
// immediately repeated) so the teacher doesn't sound like a broken record.
const CHECKIN_PHRASES = [
  "Does that make sense so far?",
  "Following along okay?",
  "Make sense? Let's check.",
  "Got the idea? Let's try a quick one.",
];
let lastCheckinPhrase = "";

function hideCheckin() {
  $("checkinRow").style.display = "none";
}

async function showCheckin() {
  let phrase = CHECKIN_PHRASES[Math.floor(Math.random() * CHECKIN_PHRASES.length)];
  if (phrase === lastCheckinPhrase && CHECKIN_PHRASES.length > 1) {
    phrase = CHECKIN_PHRASES[(CHECKIN_PHRASES.indexOf(phrase) + 1) % CHECKIN_PHRASES.length];
  }
  lastCheckinPhrase = phrase;
  $("checkinText").textContent = phrase;
  $("checkinRow").style.display = "flex";
  setTeacherStatus("curious");
  if (!state.skipRequested) await speakSentences(phrase);
  await sleep(280);
}

function renderSceneContent(scene, isFirstScene) {
  $("narrationText").textContent = scene.narration || "";
  $("bulletList").innerHTML = (scene.on_screen_text || []).map((b) => `<li>${escapeHtml(b)}</li>`).join("");
  $("exampleText").textContent = "";
  if (scene.visual) renderVisual(scene.visual);
  else if (isFirstScene) renderVisual(null);
}

// ---------------------------------------------------------------------------
// Teaching Video Engine playback (frontend half). The backend already split
// the lesson into scenes (see backend/scenes.py); this plays the CONTENT
// scenes back one at a time — narration via short, chunked, natural-paced
// TTS, on-screen text + visual updated per scene, teacher status/expression
// changed per scene — then (for a first-pass explanation, not a reteach)
// pauses for a quick "does that make sense?" check-in before finally
// revealing the real question. `skipSceneBtn` lets a demo jump straight to
// the question at any point without waiting for narration to finish.
// ---------------------------------------------------------------------------
async function playScenes(scenes, teach, startIndex = 0) {
  const contentScenes = scenes.filter((s) => s.type !== "question");
  const playbackToken = ++state.playbackToken;
  state.sceneQueue = contentScenes;
  state.sceneIndex = Math.max(0, Math.min(startIndex, Math.max(0, contentScenes.length - 1)));
  state.playbackPaused = state.userPaused;
  updatePlaybackButtons();

  for (let i = state.sceneIndex; i < contentScenes.length; i++) {
    if (state.studyQuiet || state.skipRequested || playbackToken !== state.playbackToken) return;
    state.sceneIndex = i;
    const trackFill = $("playerTrackFill");
    if (trackFill) trackFill.style.width = `${contentScenes.length > 1 ? (i / (contentScenes.length - 1)) * 100 : 0}%`;
    const scene = contentScenes[i];
    setTeacherStatus("speaking");
    renderSceneContent(scene, i === 0);

    if (scene.narration) {
      await speakSentences(scene.narration);
    } else {
      await sleep(Math.min((scene.duration || 2) * 1000, 1200));
    }
    if (state.skipRequested) break;
    await waitForPlayback(220, playbackToken); // a small natural breath between scenes
  }

  if (state.studyQuiet || playbackToken !== state.playbackToken) return;
  if (!state.skipRequested && contentScenes.length > 0 && !teach.is_reteach) {
    await showCheckin();
  }

  await revealQuestion(teach);
}

async function waitForPlayback(ms, playbackToken) {
  let elapsed = 0;
  while (elapsed < ms && playbackToken === state.playbackToken) {
    if (!state.playbackPaused) {
      await sleep(Math.min(50, ms - elapsed));
      elapsed += 50;
    } else {
      await sleep(80);
    }
  }
}

async function revealQuestion(teach) {
  if (state.studyQuiet || state.questionRevealed) return;
  state.questionRevealed = true;
  hideCheckin();
  $("questionText").textContent = teach.question;
  $("questionPanel").style.display = "block";
  $("answerInput").focus();

  if (!state.skipRequested) {
    setTeacherStatus("curious");
    await speakSentences(teach.question);
  }
  setTeacherStatus("idle");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForSpeechResume(token) {
  while (state.playbackPaused && token === state.speechToken && !state.studyQuiet) {
    await sleep(80);
  }
  return token === state.speechToken && !state.studyQuiet;
}

$("skipSceneBtn").addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  if (state.studyQuiet) return;
  if (state.questionRevealed) return; // nothing left to skip
  state.skipRequested = true;
  stopSpeaking();
  hideCheckin();
  if (state.currentTeach) revealQuestion(state.currentTeach);
});

function updatePlaybackButtons() {
  const pause = $("pauseSceneBtn");
  if (!pause) return;
  pause.classList.toggle("active", state.playbackPaused);
  pause.textContent = state.playbackPaused ? "▶" : "Ⅱ";
  pause.title = state.playbackPaused ? "Play narration" : "Pause narration";
  pause.setAttribute("aria-label", state.playbackPaused ? "Play narration" : "Pause narration");
}

$("pauseSceneBtn").addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  if (state.playbackPaused) {
    state.playbackPaused = false;
    state.userPaused = false;
    stopSpeaking();
    if (state.currentTeach && state.sceneQueue.length) {
      playScenes(state.sceneQueue, state.currentTeach, state.sceneIndex);
    }
  } else {
    state.playbackPaused = true;
    state.userPaused = true;
    stopSpeaking();
  }
  setTeacherStatus(state.playbackPaused ? "thinking" : "speaking");
  updatePlaybackButtons();
});

$("muteSceneBtn").addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  const toggle = $("voiceToggle");
  toggle.checked = !toggle.checked;
  if (!toggle.checked) stopSpeaking();
  $("muteSceneBtn").textContent = toggle.checked ? "🔊" : "🔇";
  $("muteSceneBtn").title = toggle.checked ? "Mute narration" : "Unmute narration";
  $("muteSceneBtn").setAttribute("aria-label", toggle.checked ? "Mute narration" : "Unmute narration");
});

function restartSceneAt(index) {
  if (!state.currentTeach || !state.sceneQueue.length) return;
  state.skipRequested = false;
  state.questionRevealed = false;
  $("questionPanel").style.display = "none";
  hideCheckin();
  stopSpeaking();
  playScenes(state.sceneQueue, state.currentTeach, index);
}

$("rewindSceneBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); restartSceneAt(Math.max(0, state.sceneIndex - 1)); });
$("forwardSceneBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); restartSceneAt(Math.min(state.sceneQueue.length - 1, state.sceneIndex + 1)); });
$("restartSceneBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); restartSceneAt(0); });
$("playSpeedBtn").addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  const speeds = [0.75, 1, 1.25, 1.5];
  const next = speeds[(speeds.indexOf(state.playbackSpeed) + 1) % speeds.length];
  state.playbackSpeed = next;
  $("playSpeedBtn").textContent = `${next}×`;
});

$("splitStudyBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); openSplitStudy(); });
$("closeSplitStudyBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); closeSplitStudy(); });
$("splitGuidedBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); openGuidedPanel(); });

function buildSplitNotes() {
  const teach = state.currentTeach || {};
  const bullets = (teach.slide?.bullets || []).map((bullet) => `<li>${escapeHtml(bullet)}</li>`).join("");
  return `<h1>${escapeHtml(teach.slide?.title || teach.concept || "Current lesson")}</h1><p>${escapeHtml(teach.explanation || teach.narration || "Your lesson notes will appear here.")}</p>${bullets ? `<h2>Key ideas</h2><ul>${bullets}</ul>` : ""}<h2>Example</h2><p>${escapeHtml(teach.example || teach.analogy || "Add an example in your own words.")}</p>`;
}

function openSplitStudy() {
  if (!state.currentTeach) return showToast("Start a lesson first to open split study.");
  state.studyQuiet = true;
  state.playbackToken++;
  state.playbackPaused = true;
  state.userPaused = true;
  stopSpeaking();
  showScreen("split-study");
  $("splitTopicLabel").textContent = state.currentTeach.concept || "Current lesson";
  $("notesEditor").innerHTML = localStorage.getItem(`mentora_notes_${state.sessionId}`) || buildSplitNotes();
  const scenes = state.sceneQueue.length ? state.sceneQueue : [{ narration: state.currentTeach.explanation || "Current lesson" }];
  $("sourcePageCount").textContent = `${scenes.length} page${scenes.length === 1 ? "" : "s"}`;
  $("sourceThumbnails").innerHTML = scenes.map((scene, index) => `<button class="source-thumb${index === state.sceneIndex ? " active" : ""}" data-index="${index}"><span>${index + 1} of ${scenes.length}</span><small>${escapeHtml((scene.narration || "Lesson scene").slice(0, 72))}</small></button>`).join("");
  $("sourcePreview").innerHTML = `<span class="source-label">PAGE ${state.sceneIndex + 1}</span><h3>${escapeHtml(state.currentTeach.slide?.title || state.currentTeach.concept || "Lesson source")}</h3><p>${escapeHtml(state.currentTeach.explanation || state.currentTeach.narration || "Your uploaded material is represented in the active lesson.")}</p>`;
  document.querySelectorAll(".source-thumb").forEach((thumb) => thumb.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    const index = Number(thumb.dataset.index);
    state.sceneIndex = index;
    document.querySelectorAll(".source-thumb").forEach((item) => item.classList.toggle("active", item === thumb));
    const scene = scenes[index] || {};
    $("sourcePreview").innerHTML = `<span class="source-label">PAGE ${index + 1}</span><h3>${escapeHtml(state.currentTeach.slide?.title || state.currentTeach.concept || "Lesson source")}</h3><p>${escapeHtml(scene.narration || state.currentTeach.explanation || "Your uploaded material is represented in the active lesson.")}</p>`;
  }));
  $("splitChatMessages").innerHTML = `<div class="chat-welcome"><span class="chat-cat" aria-label="Skippy the cat"><span class="cat-mascot" aria-hidden="true"><span class="cat-ear cat-ear-left"></span><span class="cat-ear cat-ear-right"></span><span class="cat-face"><span class="cat-eye cat-eye-left"></span><span class="cat-eye cat-eye-right"></span><span class="cat-nose"></span><span class="cat-mouth"></span><span class="cat-whisker cat-whisker-left"></span><span class="cat-whisker cat-whisker-right"></span></span></span></span><h3>How can I help?</h3><p>Ask about <strong>${escapeHtml(state.currentTeach.concept || "this concept")}</strong>.</p></div>`;
}

function closeSplitStudy() {
  state.studyQuiet = false;
  state.playbackPaused = true;
  state.userPaused = true;
  updatePlaybackButtons();
  stopSpeaking();
  showScreen("lesson");
}

function openGuidedPanel() {
  state.studyQuiet = true;
  state.playbackToken++;
  stopSpeaking();
  const teach = state.currentTeach || {};
  $("guidedConceptLabel").textContent = teach.concept || "Current concept";
  $("guidedFocusLabel").textContent = state.guidedFocus === "practice" ? "Practice first" : state.guidedFocus === "review" ? "Review weak spots" : "Understand the idea";
  $("guidedSessionPanel").style.display = "flex";
}

function closeGuidedPanel() {
  $("guidedSessionPanel").style.display = "none";
}

$("guidedPanelCloseBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); closeGuidedPanel(); });
$("guidedPanelCloseBtnSecondary").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); closeGuidedPanel(); });
$("guidedStartBtn").addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  closeGuidedPanel();
  state.studyQuiet = false;
  state.playbackPaused = false;
  state.userPaused = false;
  state.skipRequested = false;
  state.questionRevealed = false;
  stopSpeaking();
  showScreen("lesson");
  if (state.currentTeach) playScenes(state.sceneQueue, state.currentTeach, state.sceneIndex);
});
document.querySelectorAll("[data-guided-focus]").forEach((button) => button.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopPropagation();
  state.guidedFocus = button.dataset.guidedFocus;
  document.querySelectorAll("[data-guided-focus]").forEach((item) => item.classList.toggle("active", item === button));
  $("guidedFocusLabel").textContent = button.querySelector("strong").textContent;
}));

function renderSplitTab(tab) {
  const teach = state.currentTeach || {};
  document.querySelectorAll(".notes-tab").forEach((item) => {
    item.classList.toggle("active", item === tab);
    item.classList.remove("selected-tab");
  });
  if (tab.id === "transcriptTab") {
    $("notesEditor").innerHTML = `<h1>${escapeHtml(teach.concept || "Lesson transcript")}</h1><p>${escapeHtml((state.sceneQueue || []).map((scene) => scene.narration || "").join(" ") || teach.explanation || "No transcript is available yet.")}</p>`;
  } else if (tab.id === "viewPdfTab") {
    $("notesEditor").innerHTML = `<h1>Source preview</h1><p>${escapeHtml(teach.explanation || "Your source material preview will appear here.")}</p><p class="source-note">Use the page list on the left to move through the lesson source.</p>`;
  } else if (tab.id === "splitModeTab") {
    $("notesEditor").innerHTML = buildSplitNotes();
  }
}

document.querySelectorAll(".notes-tab").forEach((tab) => tab.addEventListener("click", () => renderSplitTab(tab)));

document.querySelectorAll(".format-toolbar button[data-command]").forEach((button) => button.addEventListener("click", () => {
  document.execCommand(button.dataset.command, false);
  $("notesEditor").focus();
}));
$("noteFontSelect").addEventListener("change", (event) => document.execCommand("fontName", false, event.target.value));
$("noteSizeSelect").addEventListener("change", (event) => document.execCommand("fontSize", false, event.target.value));
$("saveSplitNotesBtn").addEventListener("click", () => {
  localStorage.setItem(`mentora_notes_${state.sessionId}`, $("notesEditor").innerHTML);
  showToast("Notes saved to this study set.");
});
document.querySelectorAll(".chat-suggestions button").forEach((button) => button.addEventListener("click", () => {
  $("splitChatInput").value = button.dataset.prompt;
  sendSplitChat();
}));
$("splitChatSend").addEventListener("click", sendSplitChat);
$("splitChatInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendSplitChat(); }
});
function sendSplitChat() {
  const input = $("splitChatInput");
  const prompt = input.value.trim();
  if (!prompt) return;
  const teach = state.currentTeach || {};
  const isFlashcardRequest = /flashcard/i.test(prompt);
  const response = isFlashcardRequest
    ? `Turning what you've covered so far into flashcards — opening your deck now.`
    : prompt.toLowerCase().includes("summary") ? (teach.explanation || "Review the current lesson explanation and key ideas.") : `Here is a grounded starting point: ${teach.explanation || teach.narration || "Review the current lesson notes."}`;
  $("splitChatMessages").insertAdjacentHTML("beforeend", `<div class="chat-message user">${escapeHtml(prompt)}</div><div class="chat-message tutor">${escapeHtml(response)}</div>`);
  input.value = "";
  const messages = $("splitChatMessages");
  messages.scrollTop = messages.scrollHeight;
  if (isFlashcardRequest) setTimeout(openFlashcards, 500);
}

// ---------------------------------------------------------------------------
// FLASHCARDS. A lightweight, self-contained deck built from whatever the
// student has actually been taught this session (state.flashcardBank, filled
// in as renderTeach runs), falling back to the lesson plan's concept list so
// the deck is never empty. Flip, rate, shuffle, and page through — plus a
// small grounded tutor chat scoped to the current card.
// ---------------------------------------------------------------------------
function buildFlashDeck() {
  const bank = Object.values(state.flashcardBank);
  if (bank.length) return bank;
  if (state.planConcepts && state.planConcepts.length) {
    return state.planConcepts.map((c) => ({
      term: (typeof c === "string" ? c : c.name) || "Concept",
      definition: "Keep studying this concept to generate its definition here.",
      example: "",
    }));
  }
  return [];
}

function openFlashcards() {
  state.flashDeck = buildFlashDeck();
  if (!state.flashDeck.length) return showToast("Start a lesson first to generate flashcards.");
  state.flashIndex = Math.min(state.flashIndex, state.flashDeck.length - 1);
  state.flashFlipped = false;
  // Same guard as Split Study: freeze background narration/question-advance
  // while browsing cards so a delayed lesson update can't quietly hijack
  // playback the moment the student navigates back.
  state.studyQuiet = true;
  state.playbackToken++;
  state.playbackPaused = true;
  state.userPaused = true;
  stopSpeaking();
  showScreen("flashcards");
  const saved = JSON.parse(localStorage.getItem("mentora_last_session") || "null");
  $("flashSetLabel").textContent = (saved && saved.topic) || state.currentTeach?.concept || "Current lesson";
  renderFlashDots();
  renderFlashCard();
}

function closeFlashcards() {
  state.studyQuiet = false;
  state.playbackPaused = true;
  state.userPaused = true;
  updatePlaybackButtons();
  stopSpeaking();
  showScreen(state.sessionId ? "lesson" : "dashboard");
}

function renderFlashDots() {
  const wrap = $("flashDots");
  if (!wrap) return;
  wrap.innerHTML = state.flashDeck.map((card, i) => {
    const conf = state.flashConfidence[card.term];
    const cls = i === state.flashIndex ? "current" : conf === "up" ? "up" : conf === "down" ? "down" : "";
    return `<button type="button" class="flash-dot ${cls}" data-index="${i}" aria-label="Go to card ${i + 1}"></button>`;
  }).join("");
  wrap.querySelectorAll(".flash-dot").forEach((dot) => dot.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    state.flashIndex = Number(dot.dataset.index);
    state.flashFlipped = false;
    renderFlashCard();
    renderFlashDots();
  }));
}

function renderFlashCard() {
  const card = state.flashDeck[state.flashIndex];
  if (!card) return;
  $("flashCardNumber").textContent = state.flashIndex + 1;
  $("flashPageLabel").textContent = `${state.flashIndex + 1} / ${state.flashDeck.length}`;
  $("flashFrontText").textContent = card.term;
  $("flashBackText").textContent = card.definition + (card.example ? ` For example, ${card.example}` : "");
  $("flashCard").classList.toggle("flipped", state.flashFlipped);
  $("flashPagePrevBtn").disabled = state.flashIndex === 0;
  $("flashPageNextBtn").disabled = state.flashIndex === state.flashDeck.length - 1;
  const conf = state.flashConfidence[card.term];
  $("flashUpBtn").classList.toggle("active", conf === "up");
  $("flashDownBtn").classList.toggle("active", conf === "down");
  $("flashChatMessages").innerHTML = `<div class="chat-welcome"><p>Ask about <strong>${escapeHtml(card.term)}</strong> or use a quick action.</p></div>`;
}

function flipFlashCard() {
  if (!state.flashDeck.length) return;
  state.flashFlipped = !state.flashFlipped;
  $("flashCard").classList.toggle("flipped", state.flashFlipped);
}

function goFlash(delta) {
  const next = state.flashIndex + delta;
  if (next < 0 || next >= state.flashDeck.length) return;
  state.flashIndex = next;
  state.flashFlipped = false;
  renderFlashCard();
  renderFlashDots();
}

function rateFlash(rating) {
  const card = state.flashDeck[state.flashIndex];
  if (!card) return;
  state.flashConfidence[card.term] = state.flashConfidence[card.term] === rating ? undefined : rating;
  if (!state.flashConfidence[card.term]) delete state.flashConfidence[card.term];
  renderFlashCard();
  renderFlashDots();
}

function shuffleFlashDeck() {
  if (state.flashDeck.length < 2) return;
  for (let i = state.flashDeck.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [state.flashDeck[i], state.flashDeck[j]] = [state.flashDeck[j], state.flashDeck[i]];
  }
  state.flashIndex = 0;
  state.flashFlipped = false;
  renderFlashCard();
  renderFlashDots();
  showToast("Deck shuffled.");
}

function sendFlashChat(kind) {
  const input = $("flashChatInput");
  const card = state.flashDeck[state.flashIndex] || {};
  const prompt = kind === "explain" ? "Explain this card"
    : kind === "quiz" ? "Quiz me on this"
    : kind === "example" ? "Give another example"
    : input.value.trim();
  if (!prompt) return;
  let response;
  if (kind === "explain" || /explain/i.test(prompt)) {
    response = card.definition || `Here's the idea behind ${card.term || "this card"}: review your lesson notes for the full explanation.`;
  } else if (kind === "quiz" || /quiz/i.test(prompt)) {
    response = `Quick check: in your own words, what is ${card.term || "this term"}? Flip the card to check yourself.`;
  } else if (kind === "example" || /example/i.test(prompt)) {
    response = card.example ? `Another way to think about it: ${card.example}` : `Try connecting ${card.term || "this idea"} to something from your own life — that's the best way to make it stick.`;
  } else {
    response = card.definition ? `On ${card.term}: ${card.definition}` : "Open a lesson so I can ground my answers in what you're studying.";
  }
  $("flashChatMessages").insertAdjacentHTML("beforeend", `<div class="chat-message user">${escapeHtml(prompt)}</div><div class="chat-message tutor">${escapeHtml(response)}</div>`);
  input.value = "";
  const messages = $("flashChatMessages");
  messages.scrollTop = messages.scrollHeight;
}

$("openFlashcardsBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); openFlashcards(); });
$("miniFlashcardsBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); openFlashcards(); });
$("closeFlashcardsBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); closeFlashcards(); });
$("flashFlipBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); flipFlashCard(); });
$("flashCardStage").addEventListener("click", flipFlashCard);
$("flashPrevBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); goFlash(-1); });
$("flashNextBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); goFlash(1); });
$("flashPagePrevBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); goFlash(-1); });
$("flashPageNextBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); goFlash(1); });
$("flashUpBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); rateFlash("up"); });
$("flashDownBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); rateFlash("down"); });
$("flashShuffleBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); shuffleFlashDeck(); });
$("flashMenuBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); showToast("More flashcard options are coming soon."); });
$("flashConfidenceBtn").addEventListener("click", (event) => { event.preventDefault(); event.stopPropagation(); showToast("Confidence tracks how well you know each card as you rate them."); });
document.querySelectorAll("[data-flash-prompt]").forEach((button) => button.addEventListener("click", () => sendFlashChat(button.dataset.flashPrompt)));
$("flashChatSend").addEventListener("click", () => sendFlashChat());
$("flashChatInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); sendFlashChat(); }
});
document.addEventListener("keydown", (event) => {
  if (!$("screen-flashcards").classList.contains("active")) return;
  if (document.activeElement && ["TEXTAREA", "INPUT"].includes(document.activeElement.tagName)) return;
  if (event.code === "Space") { event.preventDefault(); flipFlashCard(); }
  else if (event.key === "ArrowRight") goFlash(1);
  else if (event.key === "ArrowLeft") goFlash(-1);
});

// ---------------------------------------------------------------------------
// SIGNATURE FEATURE: Explain-It-Back Verification.
// The student can trigger this at any point during a concept. Backend does
// the real work (semantic evaluation, deterministic mastery update, and
// actually regenerating a reteach turn when a misconception is found) —
// this is just the UI loop around GET/POST /explain-back.
//
// FIX: previously this button mixed addEventListener("click", submitExplainBack)
// with reassigning button.onclick for the "see re-explanation"/"close" states —
// both handlers fired on every click, and closing the modal early (✕ / "Not
// now") never restored the original handler, so the button could silently
// stop submitting explanations at all. Rewritten as a single event listener
// that dispatches on `state.ebMode`, which both close paths now reset.
// ---------------------------------------------------------------------------
state.ebMode = "submit"; // "submit" | "reteach" | "close"
state.ebPendingTeach = null;

$("explainBackBtn").addEventListener("click", openExplainBack);
$("ebCloseBtn").addEventListener("click", closeExplainBack);
$("ebSkipBtn").addEventListener("click", closeExplainBack);
$("ebSubmitBtn").addEventListener("click", () => {
  if (state.ebMode === "reteach") {
    const teach = state.ebPendingTeach;
    closeExplainBack();
    if (teach) renderTeach(teach);
  } else if (state.ebMode === "close") {
    closeExplainBack();
  } else {
    submitExplainBack();
  }
});
$("ebInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    $("ebSubmitBtn").click(); // reuse the exact same dispatch logic above
  }
});

function closeExplainBack() {
  $("explainBackOverlay").style.display = "none";
  state.ebMode = "submit";
  state.ebPendingTeach = null;
  stopSpeaking();
  setTeacherStatus("idle");
}

async function openExplainBack() {
  if (!state.sessionId) return;
  stopSpeaking();
  state.ebMode = "submit";
  state.ebPendingTeach = null;
  $("explainBackOverlay").style.display = "flex";
  $("ebLoading").style.display = "flex";
  $("ebBody").style.display = "none";
  $("ebFeedback").style.display = "none";
  $("ebUnderstoodMissing").style.display = "none";
  $("ebInput").value = "";
  $("ebSubmitBtn").textContent = "Submit explanation";
  $("ebSubmitBtn").disabled = false;
  setTeacherStatus("thinking");

  await renderAttemptBadges();

  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/explain-back`);
  const q = await res.json();
  state.ebCurrent = q;
  $("ebPrompt").textContent = q.prompt;
  $("ebLoading").style.display = "none";
  $("ebBody").style.display = "block";
  $("ebInput").focus();
  setTeacherStatus("curious");
  speak(q.prompt);
}

async function renderAttemptBadges() {
  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/explain-back/history`);
  const data = await res.json();
  const wrap = $("ebAttemptBadges");
  if (!data.attempts || data.attempts.length === 0) {
    wrap.innerHTML = "";
    return;
  }
  let prevScore = null;
  wrap.innerHTML = data.attempts
    .map((a) => {
      const improved = prevScore !== null && a.score > prevScore;
      prevScore = a.score;
      return `<span class="eb-attempt-badge${improved ? " improved" : ""}">Attempt ${a.attempt_number}: ${a.score}%${improved ? " ↑" : ""}</span>`;
    })
    .join("");
}

async function submitExplainBack() {
  const response = $("ebInput").value.trim();
  if (!response || !state.ebCurrent) {
    if (!response) {
      $("ebInput").classList.add("shake");
      setTimeout(() => $("ebInput").classList.remove("shake"), 400);
    }
    return;
  }
  $("ebSubmitBtn").disabled = true;
  $("ebSubmitBtn").textContent = "Checking…";
  setTeacherStatus("thinking");

  try {
    const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/explain-back`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: state.ebCurrent.prompt, response, question_type: state.ebCurrent.question_type,
      }),
    });
    const data = await res.json();
    const evalData = data.evaluation;

    const tier = evalData.score >= 70 ? "high" : evalData.score >= 40 ? "medium" : "low";
    const feedback = $("ebFeedback");
    feedback.style.display = "block";
    feedback.className = "eb-feedback " + tier;
    feedback.textContent = `${evalData.score}% — ${evalData.feedback}`;
    setTeacherStatus(tier === "high" ? "happy" : tier === "low" ? "confused" : "curious");
    speak(evalData.feedback);

    const umWrap = $("ebUnderstoodMissing");
    let umHtml = "";
    if (evalData.understood && evalData.understood.length) {
      umHtml += `<div class="eb-um-block"><strong>✓ You understood:</strong><ul>${evalData.understood.map((u) => `<li>${escapeHtml(u)}</li>`).join("")}</ul></div>`;
    }
    if (evalData.missing && evalData.missing.length) {
      umHtml += `<div class="eb-um-block"><strong>Missing:</strong><ul>${evalData.missing.map((m) => `<li>${escapeHtml(m)}</li>`).join("")}</ul></div>`;
    }
    if (evalData.misconceptions && evalData.misconceptions.length) {
      umHtml += `<div class="eb-um-block"><strong>Misconception:</strong><ul>${evalData.misconceptions
        .map((m) => `<li>${escapeHtml(typeof m === "string" ? m : m.concept + " — " + (m.why_wrong || ""))}</li>`)
        .join("")}</ul></div>`;
    }
    umWrap.innerHTML = umHtml;
    umWrap.style.display = umHtml ? "block" : "none";

    if (data.knowledge_map) renderKnowledgeMap(data.knowledge_map);
    await renderAttemptBadges();

    if (data.next_action === "reteach" && data.teach) {
      // The backend actually regenerated the teaching content with a fresh
      // analogy/visual — show it on the main lesson screen, then let the
      // student try explaining again once they've seen it.
      state.ebMode = "reteach";
      state.ebPendingTeach = data.teach;
      $("ebSubmitBtn").textContent = "See the re-explanation →";
      $("ebSubmitBtn").disabled = false;
    } else if (data.next_action === "follow_up" && data.follow_up_question) {
      state.ebMode = "submit";
      state.ebCurrent = { prompt: data.follow_up_question, question_type: state.ebCurrent.question_type };
      $("ebPrompt").textContent = data.follow_up_question;
      $("ebInput").value = "";
      $("ebSubmitBtn").textContent = "Submit explanation";
      $("ebSubmitBtn").disabled = false;
      speak(data.follow_up_question);
    } else {
      state.ebMode = "close";
      $("ebSubmitBtn").textContent = "Nice — close";
      $("ebSubmitBtn").disabled = false;
    }
  } catch (err) {
    setTeacherStatus("idle");
    alert("Couldn't evaluate that explanation: " + err.message);
    state.ebMode = "submit";
    $("ebSubmitBtn").disabled = false;
    $("ebSubmitBtn").textContent = "Submit explanation";
  }
}

// ---------------------------------------------------------------------------
// Dynamic visual explanations. The backend decides WHAT kind of visual fits
// the subject (equation / graph / diagram-or-timeline / code) and hands back
// a small structured spec; rendering it as SVG/HTML happens entirely here,
// client-side — no image-generation API needed.
// ---------------------------------------------------------------------------
function renderVisual(visual) {
  const container = $("visualContainer");
  container.innerHTML = "";
  if (!visual || !visual.type) {
    container.style.display = "none";
    return;
  }
  container.style.display = "flex";
  container.style.flexDirection = "column";

  const title = document.createElement("div");
  title.className = "visual-title";
  title.textContent = visual.title || visual.type;
  container.appendChild(title);

  const body = document.createElement("div");
  body.style.width = "100%";
  body.style.display = "flex";
  body.style.justifyContent = "center";

  switch (visual.type) {
    case "equation":
      body.appendChild(renderEquation(visual));
      break;
    case "graph":
      body.appendChild(renderGraph(visual));
      break;
    case "diagram":
    case "timeline":
      body.appendChild(renderNodeGraph(visual));
      break;
    case "code":
      body.appendChild(renderCode(visual));
      break;
    case "molecule":
      body.appendChild(renderMolecule(visual));
      break;
    case "bullets":
      body.appendChild(renderBulletsVisual(visual));
      break;
    default:
      container.style.display = "none";
      return;
  }
  container.appendChild(body);
}

function renderEquation(visual) {
  const wrap = document.createElement("div");
  wrap.style.textAlign = "center";
  const formula = document.createElement("div");
  formula.style.fontFamily = "'JetBrains Mono', monospace";
  formula.style.fontSize = "22px";
  formula.style.color = "#E8C468";
  formula.style.marginBottom = "10px";
  formula.textContent = visual.formula || "";
  wrap.appendChild(formula);
  if (visual.variables && visual.variables.length) {
    const list = document.createElement("div");
    list.style.fontSize = "12.5px";
    list.style.color = "#AEC0B8";
    list.style.display = "flex";
    list.style.flexDirection = "column";
    list.style.gap = "3px";
    visual.variables.forEach((v) => {
      const row = document.createElement("div");
      row.textContent = `${v.symbol} = ${v.meaning}`;
      list.appendChild(row);
    });
    wrap.appendChild(list);
  }
  return wrap;
}

function renderGraph(visual) {
  const pts = visual.points || [];
  const W = 280, H = 160, PAD = 30;
  if (pts.length === 0) return document.createTextNode("");
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  const xMin = Math.min(...xs), xMax = Math.max(...xs) || 1;
  const yMin = Math.min(...ys), yMax = Math.max(...ys) || 1;
  const sx = (x) => PAD + ((x - xMin) / (xMax - xMin || 1)) * (W - 2 * PAD);
  const sy = (y) => H - PAD - ((y - yMin) / (yMax - yMin || 1)) * (H - 2 * PAD);
  const pathD = pts.map((p, i) => `${i === 0 ? "M" : "L"} ${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(" ");

  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H + 24}`);
  svg.setAttribute("width", "300");

  const axisX = document.createElementNS(svgNS, "line");
  axisX.setAttribute("x1", PAD); axisX.setAttribute("y1", H - PAD);
  axisX.setAttribute("x2", W - PAD); axisX.setAttribute("y2", H - PAD);
  axisX.setAttribute("stroke", "#AEC0B8"); axisX.setAttribute("stroke-width", "1");
  svg.appendChild(axisX);

  const axisY = document.createElementNS(svgNS, "line");
  axisY.setAttribute("x1", PAD); axisY.setAttribute("y1", PAD);
  axisY.setAttribute("x2", PAD); axisY.setAttribute("y2", H - PAD);
  axisY.setAttribute("stroke", "#AEC0B8"); axisY.setAttribute("stroke-width", "1");
  svg.appendChild(axisY);

  const path = document.createElementNS(svgNS, "path");
  path.setAttribute("d", pathD);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#7FBEA3");
  path.setAttribute("stroke-width", "2.5");
  svg.appendChild(path);

  pts.forEach((p) => {
    const dot = document.createElementNS(svgNS, "circle");
    dot.setAttribute("cx", sx(p.x)); dot.setAttribute("cy", sy(p.y)); dot.setAttribute("r", "3");
    dot.setAttribute("fill", "#E8C468");
    svg.appendChild(dot);
  });

  const xLabel = document.createElementNS(svgNS, "text");
  xLabel.setAttribute("x", W / 2); xLabel.setAttribute("y", H + 18);
  xLabel.setAttribute("fill", "#AEC0B8"); xLabel.setAttribute("font-size", "10");
  xLabel.setAttribute("text-anchor", "middle");
  xLabel.textContent = visual.x_label || "";
  svg.appendChild(xLabel);

  const yLabel = document.createElementNS(svgNS, "text");
  yLabel.setAttribute("x", 4); yLabel.setAttribute("y", PAD - 8);
  yLabel.setAttribute("fill", "#AEC0B8"); yLabel.setAttribute("font-size", "10");
  yLabel.textContent = visual.y_label || "";
  svg.appendChild(yLabel);

  return svg;
}

function renderNodeGraph(visual) {
  // Generic node/edge renderer: used for labeled diagrams, timelines, and
  // simple programming flow diagrams — laid out left-to-right in a row.
  const nodes = visual.nodes || [];
  const edges = visual.edges || [];
  if (nodes.length === 0) return document.createTextNode("");

  const svgNS = "http://www.w3.org/2000/svg";
  const boxW = 110, boxH = 46, gapX = 50, PAD = 20;
  const W = nodes.length * boxW + (nodes.length - 1) * gapX + PAD * 2;
  const H = 120;
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", Math.min(W, 560));

  const centerY = H / 2;
  const positions = {};
  nodes.forEach((n, i) => {
    positions[n.id] = { x: PAD + i * (boxW + gapX), y: centerY - boxH / 2 };
  });

  // edges first (so boxes draw on top)
  edges.forEach((e) => {
    const from = positions[e.from], to = positions[e.to];
    if (!from || !to) return;
    const x1 = from.x + boxW, y1 = from.y + boxH / 2;
    const x2 = to.x, y2 = to.y + boxH / 2;
    const line = document.createElementNS(svgNS, "line");
    line.setAttribute("x1", x1); line.setAttribute("y1", y1);
    line.setAttribute("x2", x2); line.setAttribute("y2", y2);
    line.setAttribute("stroke", "#E8846B"); line.setAttribute("stroke-width", "2");
    line.setAttribute("marker-end", "url(#arrowhead)");
    svg.appendChild(line);
    if (e.label) {
      const t = document.createElementNS(svgNS, "text");
      t.setAttribute("x", (x1 + x2) / 2); t.setAttribute("y", y1 - 6);
      t.setAttribute("fill", "#AEC0B8"); t.setAttribute("font-size", "9");
      t.setAttribute("text-anchor", "middle");
      t.textContent = e.label;
      svg.appendChild(t);
    }
  });

  const defs = document.createElementNS(svgNS, "defs");
  defs.innerHTML = `<marker id="arrowhead" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">
    <path d="M0,0 L8,4 L0,8 Z" fill="#E8846B"/></marker>`;
  svg.appendChild(defs);

  nodes.forEach((n) => {
    const pos = positions[n.id];
    const rect = document.createElementNS(svgNS, "rect");
    rect.setAttribute("x", pos.x); rect.setAttribute("y", pos.y);
    rect.setAttribute("width", boxW); rect.setAttribute("height", boxH);
    rect.setAttribute("rx", "8");
    rect.setAttribute("fill", "#2E4038"); rect.setAttribute("stroke", "#E8C468"); rect.setAttribute("stroke-width", "1.5");
    svg.appendChild(rect);

    const text = document.createElementNS(svgNS, "text");
    text.setAttribute("x", pos.x + boxW / 2); text.setAttribute("y", pos.y + boxH / 2 + 4);
    text.setAttribute("fill", "#F4F1E8"); text.setAttribute("font-size", "11");
    text.setAttribute("text-anchor", "middle");
    text.textContent = n.label.length > 16 ? n.label.slice(0, 15) + "…" : n.label;
    svg.appendChild(text);
  });

  return svg;
}

function renderCode(visual) {
  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = visual.code || "";
  pre.appendChild(code);
  if (visual.output) {
    const outLabel = document.createElement("div");
    outLabel.style.color = "#AEC0B8";
    outLabel.style.fontSize = "10px";
    outLabel.style.margin = "8px 0 2px";
    outLabel.textContent = "OUTPUT:";
    const wrap = document.createElement("div");
    wrap.appendChild(pre);
    wrap.appendChild(outLabel);
    const outPre = document.createElement("pre");
    outPre.textContent = visual.output;
    wrap.appendChild(outPre);
    return wrap;
  }
  return pre;
}

function renderMolecule(visual) {
  // Simple force-free layout: atoms placed on a circle, bonds drawn between them.
  const atoms = visual.atoms || [];
  const bonds = visual.bonds || [];
  const svgNS = "http://www.w3.org/2000/svg";
  const W = 260, H = 220, cx = W / 2, cy = H / 2, r = 80;
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", "260");

  const pos = {};
  atoms.forEach((a, i) => {
    const angle = (2 * Math.PI * i) / Math.max(atoms.length, 1) - Math.PI / 2;
    pos[a.id] = { x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) };
  });

  bonds.forEach((b) => {
    const from = pos[b.from], to = pos[b.to];
    if (!from || !to) return;
    const offset = b.order > 1 ? 3 : 0;
    for (let k = 0; k < Math.max(1, b.order); k++) {
      const line = document.createElementNS(svgNS, "line");
      const dx = (k - (b.order - 1) / 2) * offset;
      line.setAttribute("x1", from.x + dx); line.setAttribute("y1", from.y);
      line.setAttribute("x2", to.x + dx); line.setAttribute("y2", to.y);
      line.setAttribute("stroke", "#AEC0B8"); line.setAttribute("stroke-width", "2");
      svg.appendChild(line);
    }
  });

  atoms.forEach((a) => {
    const p = pos[a.id];
    const circle = document.createElementNS(svgNS, "circle");
    circle.setAttribute("cx", p.x); circle.setAttribute("cy", p.y); circle.setAttribute("r", "16");
    circle.setAttribute("fill", "#2E4038"); circle.setAttribute("stroke", "#7FBEA3"); circle.setAttribute("stroke-width", "2");
    svg.appendChild(circle);
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", p.x); label.setAttribute("y", p.y + 4);
    label.setAttribute("fill", "#F4F1E8"); label.setAttribute("font-size", "11"); label.setAttribute("text-anchor", "middle");
    label.textContent = a.label;
    svg.appendChild(label);
  });

  return svg;
}

function renderBulletsVisual(visual) {
  const ul = document.createElement("ul");
  ul.style.margin = "0";
  ul.style.paddingLeft = "18px";
  ul.style.color = "var(--chalk)";
  ul.style.fontSize = "13px";
  (visual.points || []).forEach((p) => {
    const li = document.createElement("li");
    li.style.marginBottom = "4px";
    li.textContent = p;
    ul.appendChild(li);
  });
  return ul;
}

// ---------------------------------------------------------------------------
// Avatar expression — a small deterministic swap of the eyebrow paths based
// on scene type. Not a full facial-animation system, but enough to make the
// avatar visibly react to "explaining" vs "asking a question" vs feedback.
// ---------------------------------------------------------------------------
const EXPRESSIONS = {
  neutral: { left: "M55 60 Q70 48 85 58", right: "M115 58 Q130 48 145 60" },
  curious: { left: "M55 55 Q70 40 85 52", right: "M115 52 Q130 38 145 55" },   // raised brows
  encouraging: { left: "M55 62 Q70 56 85 60", right: "M115 60 Q130 56 145 62" }, // relaxed/soft brows
  confused: { left: "M58 56 Q70 63 82 55", right: "M118 55 Q130 63 142 56" },   // asymmetric, quizzical brows
};
function setExpression(name) {
  const e = EXPRESSIONS[name] || EXPRESSIONS.neutral;
  $("browLeft").setAttribute("d", e.left);
  $("browRight").setAttribute("d", e.right);
}

// ---------- submit answer ----------
$("submitAnswerBtn").addEventListener("click", submitAnswer);
$("answerInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submitAnswer();
  }
});

async function submitAnswer() {
  const answer = $("answerInput").value.trim();
  if (!answer) {
    $("answerInput").focus();
    $("answerInput").classList.add("shake");
    setTimeout(() => $("answerInput").classList.remove("shake"), 400);
    showToast("Type an answer first.");
    return;
  }
  $("submitAnswerBtn").disabled = true;
  $("submitAnswerBtn").textContent = "Checking…";
  setTeacherStatus("thinking");
  stopSpeaking();

  try {
    const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/answer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer }),
    });
    const data = await res.json();
    const banner = $("feedbackBanner");
    banner.style.display = "block";
    banner.className = "feedback-banner " + (data.evaluation.correct ? "correct" : "incorrect");
    banner.textContent = data.evaluation.feedback;
    setTeacherStatus(data.evaluation.correct ? "happy" : "confused");
    speak(data.evaluation.feedback);

    if (data.knowledge_map) renderKnowledgeMap(data.knowledge_map);

    setTimeout(function applyNextTurn() {
      // If the student has stepped into Split Study or the Guided Session
      // panel, don't yank the rug out from under them by silently swapping
      // state.currentTeach / sceneQueue / sceneIndex in the background —
      // that's what was causing "any button" in split study to suddenly
      // resume playback on the wrong content. Hold this update and keep
      // checking back until they've returned to the main lesson view.
      if (state.studyQuiet) {
        setTimeout(applyNextTurn, 300);
        return;
      }
      if (data.quiz_ready) {
        goToQuiz();
      } else if (data.finished) {
        finishLesson();
      } else if (data.teach) {
        renderTeach(data.teach);
      }
    }, 1800);
  } catch (err) {
    setTeacherStatus("idle");
    alert("Something went wrong grading that answer: " + err.message);
  } finally {
    $("submitAnswerBtn").disabled = false;
    $("submitAnswerBtn").textContent = "Submit answer";
  }
}

// ---------------------------------------------------------------------------
// Final quiz
// ---------------------------------------------------------------------------
async function goToQuiz() {
  clearInterval(window._timerInt);
  stopSpeaking();
  showScreen("quiz");
  $("quizLoading").style.display = "flex";
  $("quizContent").style.display = "none";
  $("submitQuizBtn").style.display = "none";

  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/quiz`);
  const data = await res.json();
  state.quizQuestions = data.questions;

  $("quizContent").innerHTML = data.questions
    .map(
      (q, i) => `<div class="quiz-question">
        <div class="q-label">Concept: ${escapeHtml(q.concept)}</div>
        <p class="q-text">${i + 1}. ${escapeHtml(q.question)}</p>
        <textarea data-qid="${q.id}" placeholder="Type your answer…"></textarea>
      </div>`
    )
    .join("");

  $("quizLoading").style.display = "none";
  $("quizContent").style.display = "block";
  $("submitQuizBtn").style.display = "inline-block";
}

$("submitQuizBtn").addEventListener("click", async () => {
  const answers = {};
  document.querySelectorAll("#quizContent textarea").forEach((t) => {
    answers[t.dataset.qid] = t.value.trim();
  });
  $("submitQuizBtn").disabled = true;
  $("submitQuizBtn").textContent = "Grading…";
  try {
    await fetch(`${API_BASE}/api/sessions/${state.sessionId}/quiz/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    });
    finishLesson();
  } catch (err) {
    alert("Couldn't submit the quiz: " + err.message);
  } finally {
    $("submitQuizBtn").disabled = false;
    $("submitQuizBtn").textContent = "Submit quiz →";
  }
});

// ---------- finish + report ----------
async function finishLesson() {
  clearInterval(window._timerInt);
  showScreen("report");
  $("reportLoading").style.display = "flex";
  $("reportContent").style.display = "none";
  $("notesPanel").style.display = "none";
  $("pathPanel").style.display = "none";

  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/report`);
  const data = await res.json();
  const r = data.report;

  $("scoreBadge").textContent = `${r.score_percent}%`;
  updateScoreRing(r.score_percent);
  $("reportSummary").textContent = r.summary;
  $("reportCounts").textContent = `${r.questions_correct} / ${r.questions_attempted} questions correct`;
  $("masteredList").innerHTML = listOrNone(r.mastered_concepts);
  $("learningList").innerHTML = listOrNone(r.learning_concepts);
  $("weakList").innerHTML = listOrNone(r.weak_concepts);
  $("misconList").innerHTML = listOrNone(r.misconceptions);
  $("revisionList").innerHTML = listOrNone(r.recommended_revision);
  $("suggestedDifficulty").textContent = `${r.suggested_next_difficulty} / 5`;
  $("nextTopic").textContent = r.next_topic;
  $("nextTopicNote").textContent = r.next_topic_note || "";

  // Priority 6: show explain-it-back improvement, not just a final score.
  if (r.explain_back_summary && r.explain_back_summary.length) {
    $("understandingList").innerHTML = r.explain_back_summary
      .map(
        (u) => `<div class="understanding-row">
          <span>${escapeHtml(u.concept)}</span>
          <span class="u-scores">
            Attempt 1: ${u.first_score}%${u.attempts > 1 ? ` → Final: ${u.final_score}%` : ""}
            ${u.improved ? '<span class="u-improved"> ✓ improved</span>' : ""}
          </span>
        </div>`
      )
      .join("");
    $("understandingSection").style.display = "block";
  } else {
    $("understandingSection").style.display = "none";
  }

  // Priority 4/5: deterministic spaced-review schedule set for this session.
  if (r.review_schedule && r.review_schedule.length) {
    $("reviewScheduleList").innerHTML = r.review_schedule
      .map(
        (rs) => `<div class="review-sched-row">
          <span>${escapeHtml(rs.concept)}</span>
          <span>Next review in ${rs.next_review_in_days} day${rs.next_review_in_days === 1 ? "" : "s"} — ${escapeHtml(rs.reason)}</span>
        </div>`
      )
      .join("");
    $("reviewScheduleSection").style.display = "block";
  } else {
    $("reviewScheduleSection").style.display = "none";
  }

  $("reportLoading").style.display = "none";
  $("reportContent").style.display = "block";
}

// ---------- study notes ----------
$("viewNotesBtn").addEventListener("click", async () => {
  const panel = $("notesPanel");
  if (panel.style.display === "block") { panel.style.display = "none"; return; }
  $("pathPanel").style.display = "none";
  panel.style.display = "block";
  panel.innerHTML = `<div class="loading-line"><div class="spinner"></div> Writing your notes…</div>`;

  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/notes`);
  const data = await res.json();
  const n = data.notes;
  const notesText = buildNotesText(n);
  panel.innerHTML = `
    <h3>Study notes</h3>
    <p><strong>Key concepts:</strong> ${escapeHtml((n.key_concepts || []).join(", "))}</p>
    <p><strong>Definitions</strong></p>
    <ul>${(n.definitions || []).map((d) => `<li><strong>${escapeHtml(d.term)}:</strong> ${escapeHtml(d.definition)}</li>`).join("")}</ul>
    ${n.formulas && n.formulas.length ? `<p><strong>Formulas</strong></p><ul>${listOrNone(n.formulas)}</ul>` : ""}
    <p><strong>Examples</strong></p>
    <ul>${listOrNone(n.examples)}</ul>
    <p><strong>Mistakes to avoid</strong></p>
    <ul>${listOrNone(n.mistakes_to_avoid)}</ul>
    <p><strong>Revise next</strong></p>
    <ul>${listOrNone(n.revise_next)}</ul>
    <div style="display:flex;gap:10px;margin-top:10px">
      <button class="btn btn-ghost" id="copyNotesBtn">Copy notes</button>
      <button class="btn btn-ghost" id="downloadNotesBtn">⬇ Download notes</button>
    </div>
  `;
  $("copyNotesBtn").addEventListener("click", () => {
    navigator.clipboard.writeText(notesText);
    $("copyNotesBtn").textContent = "Copied ✓";
    setTimeout(() => ($("copyNotesBtn").textContent = "Copy notes"), 1500);
  });
  $("downloadNotesBtn").addEventListener("click", () => {
    const blob = new Blob([notesText], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "study-notes.md";
    a.click();
    URL.revokeObjectURL(url);
  });
});

function buildNotesText(n) {
  const lines = ["# Study Notes", ""];
  lines.push("## Key concepts", (n.key_concepts || []).join(", "), "");
  lines.push("## Definitions");
  (n.definitions || []).forEach((d) => lines.push(`- **${d.term}**: ${d.definition}`));
  lines.push("");
  if (n.formulas && n.formulas.length) {
    lines.push("## Formulas");
    n.formulas.forEach((f) => lines.push(`- ${f}`));
    lines.push("");
  }
  lines.push("## Examples");
  (n.examples || []).forEach((e) => lines.push(`- ${e}`));
  lines.push("");
  lines.push("## Mistakes to avoid");
  (n.mistakes_to_avoid || []).forEach((m) => lines.push(`- ${m}`));
  lines.push("");
  lines.push("## Revise next");
  (n.revise_next || []).forEach((r) => lines.push(`- ${r}`));
  return lines.join("\n");
}

// ---------- personalized learning path ----------
$("viewPathBtn").addEventListener("click", async () => {
  const panel = $("pathPanel");
  if (panel.style.display === "block") { panel.style.display = "none"; return; }
  $("notesPanel").style.display = "none";
  panel.style.display = "block";
  panel.innerHTML = `<div class="loading-line"><div class="spinner"></div> Planning what's next…</div>`;

  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/learning-path`);
  const data = await res.json();

  if (data.gated) {
    panel.innerHTML = `
      <h3>Before moving on…</h3>
      <p>${escapeHtml(data.reason)}</p>
      <p><strong>Revise first:</strong> ${escapeHtml((data.revise_first || []).join(", "))}</p>`;
    return;
  }
  panel.innerHTML = `
    <h3>Suggested learning path</h3>
    <ol>${(data.path || []).map((p) => `<li><strong>${escapeHtml(p.topic)}</strong> — ${escapeHtml(p.why_now)}</li>`).join("")}</ol>`;
});

$("newLessonBtn").addEventListener("click", () => {
  state.sessionId = null;
  $("topic").value = "";
  $("goal").value = "";
  $("fileInput").value = "";
  $("uploadBox").classList.remove("has-file");
  $("uploadLabel").textContent = "📎 Drop your notes here — the lesson will be grounded in them";
  showScreen("dashboard");
});

// ---------------------------------------------------------------------------
// Voice: browser SpeechSynthesis (free, no API key). This is the MVP's
// stand-in for a paid avatar/voice API — see README "Phase 5" for the
// swap-in path to a hosted TTS + talking-head avatar if the budget allows.
//
// Rewritten to sound more like a teacher talking TO the student rather than
// reading a paragraph aloud:
//  - picks the best-quality English/Hindi voice available instead of
//    whatever the browser lists first
//  - speaks in short, sentence-sized chunks with a brief natural pause
//    between them, instead of one long monologue utterance
//  - never re-speaks the exact same text twice in a row (a `speechToken`
//    also guarantees any in-flight speech is fully cancelled — not just the
//    current chunk — the instant something else needs to talk)
// ---------------------------------------------------------------------------
function scoreVoice(v) {
  const name = v.name.toLowerCase();
  let score = 0;
  if (/google|natural|neural|online|premium|enhanced|wavenet/.test(name)) score += 5;
  if (v.default) score += 1;
  if (name.includes("microsoft") && name.includes("desktop")) score -= 2; // older, more robotic voices
  return score;
}

function pickVoice(lang) {
  const voices = speechSynthesis.getVoices();
  if (!voices.length) return null;
  let pool;
  if (lang === "Hindi") pool = voices.filter((v) => v.lang.startsWith("hi"));
  else if (lang === "Hinglish") pool = voices.filter((v) => v.lang === "en-IN" || v.lang.startsWith("hi"));
  else pool = voices.filter((v) => v.lang.startsWith("en"));
  if (!pool.length) pool = voices;
  return pool.slice().sort((a, b) => scoreVoice(b) - scoreVoice(a))[0] || voices[0];
}

// Splits narration into sentence-sized chunks so TTS delivery has natural
// breathing room instead of dumping a whole paragraph as one utterance.
function splitIntoSentences(text) {
  if (!text) return [];
  const matches = text.match(/[^.!?]+[.!?]+(\s|$)|[^.!?]+$/g) || [text];
  return matches.map((s) => s.trim()).filter(Boolean);
}

let lastSpokenText = "";
state.speechToken = 0;

function speakChunk(text, token) {
  return new Promise((resolve) => {
    if (!$("voiceToggle").checked || !("speechSynthesis" in window) || !text) { resolve(); return; }
    const utter = new SpeechSynthesisUtterance(text);
    const voice = pickVoice(state.language);
    if (voice) utter.voice = voice;
    utter.rate = 0.95 * state.playbackSpeed;   // keep the default warm, with user-selected speed
    utter.pitch = 1.03;  // very slightly warmer than flat/robotic default
    utter.onstart = () => { if (state.speechToken === token) setTeacherStatus("speaking"); };
    utter.onend = () => resolve();
    utter.onerror = () => resolve();
    utter.onboundary = () => { if (state.speechToken === token) bounceMouth(); };
    window.speechSynthesis.speak(utter);
  });
}

// The one real speaking function everything else calls. Awaitable (so the
// scene player can wait for narration to finish); also safe to call and
// ignore the returned promise for fire-and-forget use (feedback banners,
// explain-back prompts, etc).
async function speakSentences(text) {
  if (!text) return;
  if (!$("voiceToggle").checked || !("speechSynthesis" in window)) return;
  if (text === lastSpokenText) return; // never repeat the exact same line back-to-back
  lastSpokenText = text;

  const token = ++state.speechToken;
  if (!(await waitForSpeechResume(token))) return;
  window.speechSynthesis.cancel(); // fully stop whatever was playing before, not just fade it out

  const sentences = splitIntoSentences(text);
  for (const sentence of sentences) {
    if (state.speechToken !== token) return; // superseded by newer speech elsewhere
    if (!(await waitForSpeechResume(token))) return;
    await speakChunk(sentence, token);
    if (state.speechToken !== token) return;
    await sleep(150); // brief natural pause between sentences
  }
  if (state.speechToken === token) setTeacherStatus("idle");
}

// Kept as two names for readability at call sites (speak = fire-and-forget,
// speakAsync = awaited) — both now route through the same implementation.
function speak(text) { speakSentences(text); }
function speakAsync(text) { return speakSentences(text); }

function stopSpeaking() {
  state.speechToken++; // invalidates any in-flight speakSentences loop, not just the current utterance
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  setTeacherStatus("idle");
}
if ("speechSynthesis" in window) {
  speechSynthesis.onvoiceschanged = () => {}; // trigger voice list load
}

// ---------------------------------------------------------------------------
// Unified teacher status: one function drives the avatar glow, the listening
// ring, the mouth/eyebrow expression, the mic button's recording state, and
// the "Speaking / Listening / Thinking" status chip together, so they can
// never fall out of sync with each other.
// ---------------------------------------------------------------------------
const STATUS_META = {
  speaking: { icon: "🔊", label: "Speaking" },
  listening: { icon: "🎤", label: "Listening" },
  happy: { icon: "✅", label: "Nice work" },
  confused: { icon: "🤔", label: "Let's revisit" },
  curious: { icon: "💬", label: "Question time" },
};
const GLOW_STATES = ["speaking", "thinking", "happy", "confused"];
const EXPRESSION_FOR_STATUS = {
  speaking: "neutral", thinking: "neutral", listening: "neutral", idle: "neutral",
  happy: "encouraging", confused: "confused", curious: "curious",
};

function setTeacherStatus(status) {
  const glow = $("avatarGlow");
  const ring = $("avatarRing");
  const chip = $("voiceStatus");

  glow.className = "avatar-glow" + (GLOW_STATES.includes(status) ? " " + status : "");
  ring.className = "avatar-ring" + (status === "listening" ? " listening" : "");
  $("micBtn").classList.toggle("recording", status === "listening");

  if (status === "thinking") {
    chip.className = "teacher-status-chip st-thinking";
    chip.innerHTML = '<span class="think-dots"><span></span><span></span><span></span></span> Thinking';
  } else if (status === "idle" || !STATUS_META[status]) {
    chip.className = "teacher-status-chip";
    chip.innerHTML = "";
  } else {
    const meta = STATUS_META[status];
    chip.className = "teacher-status-chip st-" + status;
    chip.innerHTML = `${meta.icon} ${meta.label}`;
  }

  setExpression(EXPRESSION_FOR_STATUS[status] || "neutral");
  if (status !== "speaking") $("mouth").setAttribute("transform", "scale(1,1)");
}

let mouthOpen = false;
function bounceMouth() {
  mouthOpen = !mouthOpen;
  $("mouth").setAttribute("transform", mouthOpen ? "translate(0,4) scale(1,2.4)" : "scale(1,1)");
}

// ---------------------------------------------------------------------------
// Priority 3: real voice conversation. SpeechRecognition (STT) feeds the
// answer box; TTS (above) speaks the response back. Typing always still
// works as a fallback — nothing here is required for the app to function.
// ---------------------------------------------------------------------------
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null;
if (SpeechRecognition) {
  recognizer = new SpeechRecognition();
  recognizer.continuous = false;
  recognizer.interimResults = false;
  recognizer.onstart = () => setTeacherStatus("listening");
  recognizer.onresult = (e) => {
    setTeacherStatus("thinking");
    $("answerInput").value = e.results[0][0].transcript;
  };
  recognizer.onerror = () => setTeacherStatus("idle");
  recognizer.onend = () => {
    // Safety net: if nothing else claimed the status shortly after
    // recognition ends (e.g. no speech was detected), settle back to idle.
    setTimeout(() => {
      if ($("voiceStatus").classList.contains("st-thinking") || $("voiceStatus").classList.contains("st-listening")) {
        setTeacherStatus("idle");
      }
    }, 900);
  };
} else {
  $("micBtn").title = "Voice input not supported in this browser — typing still works.";
}

$("micBtn").addEventListener("click", () => {
  if (!recognizer) return;
  stopSpeaking();
  recognizer.lang = state.language === "Hindi" ? "hi-IN" : "en-IN";
  recognizer.start();
});

// ---------------------------------------------------------------------------
// Priority 7: personalized homework
// ---------------------------------------------------------------------------
$("viewHomeworkBtn").addEventListener("click", goToHomework);
$("backToReportBtn").addEventListener("click", () => showScreen("report"));

async function goToHomework() {
  showScreen("homework");
  $("homeworkLoading").style.display = "flex";
  $("homeworkContent").style.display = "none";
  $("submitHomeworkBtn").style.display = "none";
  $("retryHomeworkBtn").style.display = "none";
  $("homeworkResults").innerHTML = "";

  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/homework`);
  const data = await res.json();
  renderHomework(data.homework);
}

function renderHomework(items) {
  state.homeworkItems = items;
  const tierLabel = { easy: "Easy", medium: "Medium", application: "Application", explain_own_words: "Explain in your own words", challenge: "Challenge" };
  $("homeworkContent").innerHTML = items
    .map(
      (h, i) => `<div class="hw-item" data-hw-id="${h.id}">
        <span class="tier-badge">${escapeHtml(tierLabel[h.tier] || h.tier)}</span>
        <p class="q-text">${i + 1}. ${escapeHtml(h.question)}</p>
        <textarea data-hw-qid="${h.id}" placeholder="Type your answer…"></textarea>
        <div class="hw-result" id="hwResult-${h.id}" style="display:none"></div>
      </div>`
    )
    .join("");
  $("homeworkLoading").style.display = "none";
  $("homeworkContent").style.display = "block";
  $("submitHomeworkBtn").style.display = "inline-block";
  $("retryHomeworkBtn").style.display = "none";
  $("homeworkResults").innerHTML = "";
}

$("submitHomeworkBtn").addEventListener("click", async () => {
  const answers = {};
  document.querySelectorAll("#homeworkContent textarea").forEach((t) => {
    if (t.value.trim()) answers[t.dataset.hwQid] = t.value.trim();
  });
  if (Object.keys(answers).length === 0) return;

  $("submitHomeworkBtn").disabled = true;
  $("submitHomeworkBtn").textContent = "Grading…";
  try {
    const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/homework/submit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    });
    const data = await res.json();
    data.results.forEach((r) => {
      const el = $(`hwResult-${r.id}`);
      if (!el) return;
      el.style.display = "block";
      el.className = "hw-result " + (r.correct ? "correct" : "incorrect");
      el.textContent = (r.correct ? "✅ " : "❌ ") + (r.feedback || "");
    });
    $("homeworkResults").innerHTML = `<p style="color:var(--chalk-dim);font-weight:600">Score: ${data.score_percent}%</p>`;
    $("submitHomeworkBtn").style.display = "none";
    $("retryHomeworkBtn").style.display = "inline-block";
  } catch (err) {
    alert("Couldn't grade homework: " + err.message);
  } finally {
    $("submitHomeworkBtn").disabled = false;
    $("submitHomeworkBtn").textContent = "Submit homework →";
  }
});

$("retryHomeworkBtn").addEventListener("click", async () => {
  $("homeworkLoading").style.display = "flex";
  $("homeworkContent").style.display = "none";
  $("retryHomeworkBtn").style.display = "none";
  const res = await fetch(`${API_BASE}/api/sessions/${state.sessionId}/homework/retry`, { method: "POST" });
  const data = await res.json();
  renderHomework(data.homework);
});

// ---------------------------------------------------------------------------
// Priority 8: learning analytics dashboard — all numbers here come straight
// from analytics.py (deterministic Python), never from the LLM.
// ---------------------------------------------------------------------------
async function goToProgress() {
  showScreen("progress");
  $("progressLoading").style.display = "flex";
  $("progressContent").style.display = "none";

  const res = await fetch(`${API_BASE}/api/students/${state.studentId}/analytics`);
  const data = await res.json();
  renderProgress(data);
}

function renderProgress(d) {
  const container = $("progressContent");
  container.innerHTML = "";

  const stats = document.createElement("div");
  stats.className = "stat-row";
  stats.style.margin = "0 0 20px";
  const tiles = [
    ["Overall mastery", `${d.overall_mastery_percent}%`],
    ["Sessions completed", d.topics_completed],
    ["Learning streak", `${d.learning_streak_days} 🔥`],
    ["Time studied", `${d.total_time_minutes}m`],
  ];
  tiles.forEach(([label, value]) => {
    const tile = document.createElement("div");
    tile.className = "stat-tile";
    tile.innerHTML = `<div class="stat-value">${value}</div><div class="stat-label">${escapeHtml(label)}</div>`;
    stats.appendChild(tile);
  });
  container.appendChild(stats);

  if (d.quiz_scores_over_time && d.quiz_scores_over_time.length >= 2) {
    const chartTitle = document.createElement("p");
    chartTitle.innerHTML = "<strong>Quiz score trend</strong>";
    container.appendChild(chartTitle);
    const points = d.quiz_scores_over_time.map((p, i) => ({ x: i, y: p.score }));
    const chart = renderGraph({ points, x_label: "session", y_label: "score %" });
    chart.style.background = "#1F2E2A";
    chart.style.borderRadius = "8px";
    chart.style.padding = "8px";
    container.appendChild(chart);
  }

  const grid = document.createElement("div");
  grid.className = "report-grid";
  grid.style.margin = "18px 0 0";
  grid.innerHTML = `
    <div class="report-card"><h3>Topics studied</h3><ul>${listOrNone(d.topics_studied)}</ul></div>
    <div class="report-card"><h3>Strong areas</h3><ul>${listOrNone(d.strong_areas)}</ul></div>
    <div class="report-card"><h3>Weak areas</h3><ul>${listOrNone(d.weak_areas)}</ul></div>
    <div class="report-card"><h3>Repeated misconceptions</h3><ul>${listOrNone(d.repeated_misconceptions)}</ul></div>
  `;
  container.appendChild(grid);

  if (d.improvement_percent_points !== null && d.improvement_percent_points !== undefined) {
    const imp = document.createElement("p");
    imp.style.margin = "14px 0 0";
    const sign = d.improvement_percent_points >= 0 ? "+" : "";
    imp.innerHTML = `<strong>Improvement:</strong> ${sign}${d.improvement_percent_points} percentage points since your first quiz.`;
    container.appendChild(imp);
  }

  $("progressLoading").style.display = "none";
  container.style.display = "block";
}
