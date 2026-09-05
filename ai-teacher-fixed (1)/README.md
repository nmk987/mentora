# AI Teacher — an adaptive AI educator

> **v5 update:** switched the LLM provider to **Groq**, and added the project's signature
> feature — **Explain-It-Back Verification** — plus Time-Budget Honesty and Spaced Revision.
> This is still an incremental upgrade: v1-v4 endpoints, the adaptive engine, RAG, visuals,
> homework, and analytics are all unchanged and still work. See "What's new in v5" below.

A working hackathon MVP for **"Build a Human-Like AI Educator That Teaches Through Video."**
It follows the loop: **Understand → Plan → Explain → Demonstrate → Question → Evaluate → Adapt → Continue.**

The whole point of this project is the **Adaptive Teaching Engine** — a small, explainable
state machine (`backend/adaptive_engine.py`) that decides, after every student answer,
whether to re-teach with a simpler explanation, raise the difficulty, move to the next
concept, or wrap up the session. Everything else (RAG, voice, avatar) exists to support that loop.

---

## 1. Architecture

```
Student's browser (frontend/, plain HTML+JS, no build step)
        │  fetch()
        ▼
FastAPI backend (backend/main.py)
        │
        ├── prompts.py        → builds JSON-only prompts for each teaching step
        ├── llm.py             → calls Claude (Anthropic API), parses JSON safely
        ├── rag.py              → PDF/DOCX/PPTX extraction, chunking, TF-IDF retrieval
        ├── adaptive_engine.py  → THE NOVELTY: pure decision logic, no I/O
        └── db.py               → SQLite: sessions, chunks, turns, mastery
```

**Why these choices (optimizing for "simple, reliable, cheap, easy to demo"):**

| Layer | Choice | Why |
|---|---|---|
| LLM | Claude (Anthropic API) | One API for planning, teaching, evaluating, and reporting — via structured JSON prompts. Needs a paid API key (small free credit for new accounts). |
| RAG retrieval | TF-IDF + cosine similarity (scikit-learn) | No hosted vector DB, no embedding API cost, no model download, runs in memory in milliseconds. Easy one-sentence explanation for judges. Swap-in path to a real vector DB is noted below. |
| Database | SQLite (stdlib `sqlite3`) | Zero setup, single file, perfect for a demo. Swap to Postgres for production. |
| Voice | Browser `SpeechSynthesis` API | Completely free, zero latency, works offline, supports English/Hindi voices depending on the OS. No API key needed. |
| Avatar | CSS/SVG face synced to speech events | Free MVP stand-in for a paid talking-head API. See "Swapping in a real avatar" below. |
| Frontend | Plain HTML/CSS/JS | No npm build step — open `index.html` (via a static server) and it just works. A judge can `git clone` and run it in under 2 minutes. |

---

## 2. Folder structure

```
ai-teacher/
├── README.md
├── backend/
│   ├── main.py              # FastAPI app + all API endpoints
│   ├── adaptive_engine.py   # the adaptive teaching state machine (THE NOVELTY)
│   ├── prompts.py           # prompt architecture (plan / teach / eval / report)
│   ├── llm.py                # Claude API wrapper with JSON parsing
│   ├── rag.py                 # document extraction, chunking, TF-IDF retrieval
│   ├── db.py                   # SQLite persistence
│   ├── schemas.py              # Pydantic request/response models
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html            # dashboard, lesson, report screens
    ├── app.js                 # API calls + lesson flow + TTS-driven avatar
    └── style.css               # chalkboard/notebook themed UI
```

---

## 3. Database schema (SQLite)

- **sessions** — one row per lesson: topic, level, language, time budget, the generated
  lesson plan (JSON), current concept index, current difficulty (1–5), attempt/streak
  counters, grounded/finished flags.
- **chunks** — extracted text chunks from an uploaded document, tied to a session.
- **turns** — every teaching turn and every answer-evaluation turn, in order, as JSON —
  this is what the report is generated from.
- **mastery** — running per-concept score, attempt/correct counts, and the list of
  misconceptions caught for that concept.

---

## 4. RAG pipeline (Phase 2)

1. `rag.extract_text()` — pulls raw text out of PDF (`pypdf`), DOCX (`python-docx`), or
   PPTX (`python-pptx`).
2. `rag.chunk_text()` — sliding-window word chunking (~120 words, 20-word overlap).
3. `rag.Retriever` — fits a TF-IDF vectorizer over a session's chunks once, then answers
   `top_k(query)` with cosine similarity. Used both when generating the lesson plan (a
   sample of chunks) and when teaching each concept (the top-4 most relevant chunks are
   injected into the teaching prompt so the AI explains *from the student's own notes*).

**Post-hackathon upgrade path:** swap `TfidfVectorizer` for real embeddings
(`voyage-3` via Anthropic's recommended embedding partner, or any Sentence-Transformers
model) and store vectors in a proper vector DB (Chroma, pgvector, Pinecone) once you need
retrieval across many long documents instead of a single lesson's notes.

---

## 5. AI teacher agent architecture

Four Claude calls, each with a strict "return only JSON" system prompt (see `prompts.py`):

1. **Plan** (`plan_prompt`) — topic + level + time + optional RAG context →
   an ordered list of 2–6 teachable concepts, each with a one-sentence objective, and a
   starting difficulty.
2. **Teach** (`teach_prompt`) — one concept at a time → explanation, analogy, worked
   example, a slide (title + bullets), and exactly one check-question. When re-teaching,
   the prompt is given the prior misconceptions and every analogy already used, and is
   told explicitly not to repeat an analogy.
3. **Evaluate** (`eval_prompt`) — the question + the student's answer → correct/partial
   credit, a named misconception (or null), and one to two sentences of feedback.
4. **Report** (`report_prompt`) — the full turn history + mastery table → score, strong
   concepts, weak concepts, misconceptions caught, revision tips, and a suggested next
   topic.

`llm.call_json()` strips markdown fences, extracts the first `{...}` block, and retries
once with a stricter instruction if parsing fails — small robustness layer against an
occasional stray sentence from the model.

---

## 6. Adaptive Teaching Engine (Phase 4 — the core novelty)

Pure function, no I/O, in `adaptive_engine.py`. This is the file to have open during the demo.

```
decide(correct, partial_credit, current_difficulty, session_baseline_difficulty,
       attempts_current, consecutive_correct, is_last_concept) -> one of:

  RETEACH          same concept · difficulty −1 · new analogy, avoids repeats
  ADVANCE_DIFF     same concept · difficulty +1 · a harder follow-up question
  NEXT_CONCEPT     concept mastered (2 correct in a row, or stretched a difficulty
                    above baseline) → move on
  FINISH           no concepts left → generate the report
```

Guardrail: a concept is abandoned as "weak" (not stuck in an infinite loop) after 3
attempts and the engine moves on anyway — the concept is still flagged weak in the final
report.

This is intentionally a legible rule-based system rather than a black box — you can
point at the exact `if` statement that explains why the AI just made things easier or
harder, which is much easier to defend to judges than "the LLM decided."

---

## 7. API endpoints

| Method & path | Purpose |
|---|---|
| `POST /api/sessions` | multipart form: `topic`, `level`, `language`, `time_minutes`, `learning_goal`, optional `file` (PDF/DOCX/PPTX). Creates the session, generates the lesson plan, ingests the file into RAG chunks if provided. |
| `GET /api/sessions/{id}/state` | current concept, difficulty, elapsed time, grounded/finished flags — for the progress UI. |
| `GET /api/sessions/{id}/next` | generates (and stores) the teaching content for the current concept. |
| `POST /api/sessions/{id}/answer` | body `{"answer": "..."}`. Evaluates the answer, runs the adaptive engine, updates mastery, and returns the next teaching content (or the finish signal) in the same response. |
| `GET /api/sessions/{id}/report` | generates the final score/strengths/weaknesses/misconceptions/revision/next-topic report. |
| `GET /api/health` | liveness check. |

---

## 8. Frontend screens

- **Dashboard** — topic input or file upload, level/language/time pill selectors, optional
  learning goal, "Start lesson."
- **Lesson** — a chalkboard-style panel with the AI teacher's face (SVG, mouth animated in
  sync with `SpeechSynthesis` boundary events), a difficulty meter, concept-progress dots,
  the current slide (title + bullets + example), the check-question, a text/voice answer
  box, and immediate feedback.
- **Report** — a "report card" styled page: score badge, strong concepts, concepts to
  revise, misconceptions caught, recommended revision steps, and a suggested next topic.

---

## 9. Environment variables

Copy `backend/.env.example` to `backend/.env` and fill in:

| Variable | Required | Notes |
|---|---|---|
| `GROQ_API_KEY` | **Yes** | Get one at https://console.groq.com/keys (generous free tier, no cost commitment needed for a hackathon demo). Without this, the app cannot generate any lesson content. |
| `GROQ_MODEL` | No | Defaults to `llama-3.3-70b-versatile`. Used for planning, teaching, the final report, and explain-it-back questions. Check https://console.groq.com/docs/models for current model IDs if this one is retired. |
| `GROQ_EVAL_MODEL` | No | Defaults to `GROQ_MODEL`. Point this at a smaller/faster model if you want cheaper grading calls (answer evaluation, quiz/homework grading, explain-it-back evaluation). |
| `DB_PATH` | No | Defaults to `./ai_teacher.db` (SQLite file, created automatically). |
| `CORS_ORIGINS` | No | Comma-separated list of allowed frontend origins. Defaults to a few common local dev ports. |

**No other external API keys are required.** Voice uses the browser's built-in
`SpeechSynthesis`; RAG uses local TF-IDF; the database is a local SQLite file.

---

## 10. Setup & running locally

```bash
# 1. Backend
cd backend
python3 -m venv venv && source venv/bin/activate   # optional but recommended
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste your GROQ_API_KEY
uvicorn main:app --reload --port 8000

# 2. Frontend (in a second terminal)
cd frontend
python3 -m http.server 5500
# open http://localhost:5500 in your browser
```

That's it — two terminals, no npm install, no Docker required for the demo.

If you serve the frontend from a different origin/port, either add it to `CORS_ORIGINS`
in `backend/.env`, or set `window.API_BASE = "http://localhost:8000"` at the top of
`frontend/app.js` if your backend runs elsewhere.

### Running the automated sanity checks

The adaptive engine, RAG chunking, and the full API flow (with the LLM calls mocked) are
straightforward to exercise without spending API credits — see the "Development strategy"
section below for the kind of quick script that's useful while iterating.

---

## 11. Deployment (fast path for a hackathon)

- **Backend:** Render, Railway, or Fly.io — all support "point at a GitHub repo running
  `uvicorn main:app`" with a free/cheap tier. Set `GROQ_API_KEY` as a secret env var.
  SQLite works fine for a demo; for anything longer-lived, mount a persistent volume or
  switch `DB_PATH`/schema to Postgres.
- **Frontend:** Netlify, Vercel (static), or GitHub Pages — it's static HTML/JS/CSS, just
  set `window.API_BASE` to your deployed backend URL before pushing.
- **Fastest possible option for judges:** just run both locally on the laptop you're
  demoing from — zero deploy risk five minutes before your slot.

---

## 12. Demo scenario for judges

**Lead with Explain-It-Back — it's the feature that proves this is more than a chatbot.**

1. **Dashboard:** Type "Ohm's Law," select Beginner, English, 5 minutes, Friendly + Standard.
   Click "Start lesson."
2. **Lesson:** The AI teacher explains voltage with a water-pipe analogy, shows an equation
   visual, and asks a check-question.
3. **Click "🗣️ Explain it back"** instead of answering the check-question. The AI asks the
   student to explain the concept in their own words — a *different* kind of question than
   the recall check-question just shown.
4. **Deliberately give a wrong explanation:** "Higher resistance causes higher current."
   The semantic evaluator (not keyword matching) scores it ~35%, names the specific
   misconception, and the backend **actually regenerates the teaching content** with a new
   analogy and visual — click through to see the re-explanation play.
5. **Explain it back again, correctly this time:** "If voltage stays constant, increasing
   resistance decreases current." Score jumps to ~90% — the attempt-history badges show
   "Attempt 1: 35% → Attempt 2: 90% ↑" right in the modal.
6. **Knowledge map (right panel):** point out it updated from the explain-back result, not
   just the multiple-choice-style question.
7. **Answer wrong on the normal check-question too**, for good measure: the AI detects the
   misconception, drops the difficulty, gives yet another fresh analogy — the standard
   adaptive-reteach loop, still fully working underneath the new feature.
8. **Finish all concepts → final quiz → report.** Point out the **Understanding** section:
   "Ohm's Law — Attempt 1: 35% → Final: 90% ✓ improved" — direct evidence the AI helped,
   not just graded. Point out the **Spaced review schedule**: "Voltage: next review in 7
   days" — computed by a plain Python function, not the model.
9. **Switch language / personality / strategy mid-lesson**, upload a PDF for grounded
   RAG-based teaching, check the study notes / learning path / homework buttons — all still
   there from earlier versions and still working.
10. **Return to the dashboard** and show the **"📚 Due for Review"** section — click "Start
    review" on a concept and watch it launch a short, targeted Revision-mode session.

## 13. 3–5 minute demo script

> "Most 'AI tutor' hackathon projects are a chatbot with a syllabus prompt. We built
> something closer to a real teacher: it changes what it does *based on how you answer*.
>
> [Dashboard] I'll pick a topic — Ohm's Law — beginner level, 5 minutes.
>
> [Lesson loads] Here's the AI teacher. It explained voltage with a water-pressure
> analogy and asked me a question.
>
> [Type a wrong answer] Watch what happens — I'll deliberately get this wrong.
> [Feedback appears, reteach badge shows] It caught the misconception, dropped the
> difficulty, and is now explaining it with a *completely different* analogy — pipes are
> gone, we're in electron-crowd territory now — because we told it not to repeat itself.
>
> [Answer correctly this time] Now watch — since I got that right, it just raised the
> difficulty and asked a harder question on the same concept instead of moving on
> immediately, to check I actually understood it and didn't just get lucky.
>
> [Let it finish / skip ahead to report] And at the end, it doesn't just give a score —
> it names the exact misconception it caught mid-lesson, tells me what to revise, and
> recommends what to study next.
>
> All of that decision-making — reteach, go harder, move on, or finish — lives in one
> small, readable function [show `adaptive_engine.py`]. It's not a black box; you can
> point to the exact rule that fired. And the whole thing runs on a free TF-IDF retrieval
> layer, the browser's built-in text-to-speech, and one LLM API — so it's genuinely cheap
> to run and easy to extend after today."

---

## 14. Swapping in a real avatar / hosted TTS (after the hackathon)

The current MVP avatar is a CSS/SVG face whose mouth is driven by
`SpeechSynthesis`'s `onboundary` events — free, zero-latency, and good enough to sell the
concept on stage. If you want a photorealistic talking-head avatar later:

- **D-ID** (https://www.d-id.com) or **HeyGen** (https://www.heygen.com) — send the
  `explanation` text (or a pre-recorded teacher voice) and get back a talking-head video
  clip. Both are paid APIs with limited free trial credits; check current pricing before
  committing, since this is the single most expensive piece of the whole system.
- **ElevenLabs** (https://elevenlabs.io) for higher-quality multilingual TTS (including
  better Hindi/Hinglish prosody than most OS voices) if `SpeechSynthesis` voice quality
  becomes a blocker — paid, with a free tier.
- Integration point: replace the `speak()` function in `frontend/app.js` with a call to
  your backend, which calls the chosen provider and returns either an audio URL or a
  video URL to play instead of triggering `SpeechSynthesis` directly.

---

## What's new in v2

Built as an **incremental upgrade** on the original codebase — nothing was rewritten from
scratch, and every original endpoint still works. Files changed/added, and why:

| File | Change | Why |
|---|---|---|
| `db.py` | Added `misconceptions`, `quiz_questions`, `quiz_answers` tables; `quiz_done` column on `sessions`; migration-safe `init_db()`. | Misconceptions now have a full record (question, answer, *why* it's wrong), not just a one-line phrase. Quiz needs its own storage since it's a new phase. |
| `progress.py` **(new)** | Deterministic knowledge-map (🟢/🟡/🔴/⚪), session stats (score, questions attempted, mastered/learning/weak lists, suggested next difficulty), and a prerequisite gate function. | Per the architecture principle: mastery status and scores must be reproducible, not re-derived by the LLM each time. Kept separate from `adaptive_engine.py`, which only decides the *next turn*, not session-wide state. |
| `adaptive_engine.py` | **Unchanged.** | Already tested and working — no reason to touch it. |
| `prompts.py` | `plan_prompt` now asks for prerequisites + per-concept difficulty + checkpoint ideas. `teach_prompt` now asks for a `visual` object (equation/graph/diagram/timeline/code) and, on reteach, explicit avoid-lists for both analogies AND visual types. `eval_prompt` now asks for `why_wrong`, not just a misconception label. Added `quiz_prompt`, `notes_prompt`, `learning_path_prompt`. `report_prompt` rewritten to take pre-computed deterministic stats and only generate the qualitative parts (summary, revision phrasing, next-topic name). | This is where "don't just say incorrect" and "dynamic visuals" actually live — in what we ask the model for, not in new plumbing. |
| `main.py` | New endpoints (below). `_generate_teach_content` now pulls stored misconception detail + prior visual types before a reteach call. `submit_answer` now logs misconceptions to the DB and returns a live `knowledge_map`. The old direct `finished=1` transitions now route through `_all_concepts_done()`, which inserts the quiz phase before the session is truly finished. `/report` now merges deterministic `progress.py` output with the LLM's narrative fields, and applies the prerequisite gate to override the suggested next topic when needed. | This is the "deterministic backend decides progression / LLM explains" split from the brief, made concrete. |
| `schemas.py` | Added `LanguageRequest`, `QuizSubmitRequest`. | Support the two new POST endpoints below. |
| `frontend/*` | Lesson screen restructured into the requested 4-zone classroom layout (avatar / explanation+visual / knowledge map / question bar). Added a visual renderer (equation / line-graph / node-edge diagram-or-timeline / code block) driven entirely by the `visual` field from the teach prompt — no image API. Added a quiz screen. Report screen now shows mastered/learning/weak separately, a study-notes panel (copy + download), and a learning-path panel. Added a language `<select>` that calls the new language-switch endpoint mid-lesson. | Points 3, 4, 7, 8, 10 of the brief. |

### New API endpoints (all existing ones are unchanged)

| Method & path | Purpose |
|---|---|
| `GET /api/sessions/{id}/knowledge-map` | Every concept in the plan with its 🟢/🟡/🔴/⚪ status, score, attempts. |
| `POST /api/sessions/{id}/language` | Switch teaching language mid-lesson without losing concept/mastery/history context. |
| `GET /api/sessions/{id}/quiz` | Generates (once) and returns the final mixed-concept quiz. |
| `POST /api/sessions/{id}/quiz/submit` | Grades all quiz answers, updates mastery, marks the session finished. |
| `GET /api/sessions/{id}/notes` | Auto-generated study notes (key concepts, definitions, formulas, examples, mistakes, revision list). |
| `GET /api/sessions/{id}/learning-path` | A 3-5 topic curriculum path, or a deterministic "revise this first" gate if this session left a concept weak. |

The `/answer` and `/next` responses gained one new field each (`knowledge_map` on `/answer`;
`quiz_ready` on both) — additive, so anything already reading `finished` / `teach` /
`evaluation` keeps working unchanged.

### Dynamic visuals — how they actually work

The teach prompt asks the model to pick ONE of `equation | graph | diagram | timeline | code`
based on the subject, and return a small structured spec (a formula + variable list; a
handful of `{x,y}` points; a generic node/edge list; or a code snippet). The frontend has
one renderer per type — plain SVG for the equation/graph/diagram-or-timeline cases,
a styled `<pre>` for code — so there's no image-generation API, no extra cost, and no
extra latency. On a reteach, the prompt is explicitly told which visual types were already
tried for that concept and nudged to pick a different one if the subject allows it.

### Testing performed for v2

All of the following were run against the live FastAPI app (via `TestClient`) with the
LLM calls mocked, so they exercise real routing/DB/engine logic without spending API
credits:

- Unit tests: `progress.concept_status`, `progress.build_knowledge_map`, `progress.gate_next_topic`.
- Full flow: create session (with prerequisites/per-concept difficulty in the plan) →
  teach → **wrong answer → misconception + why_wrong stored in DB → reteach with a
  genuinely different analogy** → correct answer → difficulty increases → concept
  mastered → next concept → mid-lesson language switch → last concept mastered → **quiz
  phase triggered automatically** → quiz graded → knowledge map updated from quiz results
  → study notes generated → learning path generated (and correctly *not* gated once
  everything was mastered) → final report (deterministic score/mastered/learning/weak +
  LLM narrative) → `/state` still returns the original backward-compatible shape.
- RAG grounding: uploaded text is chunked, retrieved, and confirmed present in both the
  plan-generation prompt and the per-concept teaching prompt.
- Diagram visual type (node/edge) verified with a biology example (chlorophyll → light
  capture → glucose).

## What's new in v3

Built as another **incremental upgrade** — nothing from v1/v2 was rewritten or removed.

### New files

| File | Purpose |
|---|---|
| `scenes.py` | Teaching Video Engine — deterministically splits a teach turn into a sequence of playable scenes (intro/explain/question), each with narration, on-screen text, a visual, an estimated duration, and a transition. No extra LLM call: the content already exists in the teach turn, this just paces and structures it. Mode-aware (Practice mode collapses to almost no explanation). |
| `visual_engine.py` | Validates/normalizes whatever `visual` object the LLM returns into one of 7 well-formed types (`equation`, `graph`, `diagram`, `timeline`, `code`, `molecule`, `bullets`), with safe fallbacks for malformed output. The LLM decides *what* visual fits the subject; this file decides *how* it's structured for rendering. Also has a tiny keyword heuristic (`visual_hint_for_subject`) that nudges the teach prompt toward the right subject family. |
| `analytics.py` | Fully deterministic cross-session learning analytics (mastery %, streaks, score trends, repeated misconceptions, time spent) — reads `student_memory`/`analytics_events`, never calls the LLM. |

### Changed files

| File | Change |
|---|---|
| `db.py` | New tables: `lesson_scenes`, `homework`, `homework_answers`, `student_memory`, `student_profile`, `analytics_events`. New columns on `sessions`: `student_id`, `personality`, `mode`, `recall_note`. Migration-safe (existing DBs upgrade automatically via `ALTER TABLE` guarded by try/except). |
| `progress.py` | Added `intelligent_recommendation()` — combines this session's weak concepts, cross-session weak concepts (from `student_memory`), quiz score, and the stated learning goal into a gate decision with explicit deterministic reasons. `gate_next_topic()` (v2) kept as-is for anything still calling it directly. |
| `prompts.py` | Added `PERSONALITY_NOTE` / `MODE_NOTE` dictionaries injected into `teach_prompt`/`eval_prompt` (tone/style only — the system prompt explicitly forbids letting personality change facts). `plan_prompt` now branches on mode: Revision mode forces the concept list to exactly the concepts a deterministic lookup found weak; Exam/Practice bias concept selection and depth. Added `homework_prompt`. `learning_path_prompt` now accepts deterministic reasons to ground its suggestion. |
| `main.py` | Session creation now takes `personality`, `mode`, `student_id`; looks up `student_memory` for this topic and either forces Revision-mode concepts or generates a deterministic recall note ("let's quickly revise X because you had difficulty with it previously") — no LLM call needed for that decision. `_generate_teach_content` now normalizes the visual through `visual_engine`, builds scenes through `scenes.py`, and stores both. `/report` now syncs the session's knowledge map into `student_memory`/`student_profile` and logs an `analytics_events` row. New endpoints below. |
| `schemas.py` | Added `PersonalityRequest`, `HomeworkSubmitRequest`. |
| `frontend/*` | Lesson screen now plays scenes sequentially (narration → on-screen text → visual → question), with the avatar's eyebrows changing per scene type (curious for questions, encouraging on a correct answer). Added personality/mode selectors on the dashboard and a mid-lesson personality switch. Added a top nav (New lesson / My progress). Added homework and progress/analytics screens. Added Listening/Processing/Speaking voice-status text. Added `molecule` and `bullets` visual renderers. A `student_id` is generated once via `localStorage` (no login system) so long-term memory has something stable to key on. |

### New API endpoints

| Method & path | Purpose |
|---|---|
| `POST /api/sessions/{id}/personality` | Switch teacher personality mid-lesson (tone only, never facts). |
| `GET /api/sessions/{id}/homework` | Generate (once) personalized homework from this session's weak/learning concepts, as a 5-tier difficulty ladder. |
| `POST /api/sessions/{id}/homework/submit` | Grade homework answers, return per-item feedback and a deterministic score. |
| `POST /api/sessions/{id}/homework/retry` | Regenerate a fresh homework set. |
| `GET /api/students/{student_id}/analytics` | Deterministic cross-session dashboard: mastery %, streak, score trend, weak/strong areas, repeated misconceptions, time spent. |

`POST /api/sessions` gained optional form fields `personality`, `mode`, `student_id` (all
optional — omitting them behaves exactly like v2). Its response gained `mode`, `personality`,
`recall_note`. `/next` and `/answer`'s `teach` object gained `scenes` (array) and a normalized
`visual`. `/learning-path`'s response gained a `reasons` array explaining *why*.

### How the Teaching Video Engine actually works

No new LLM call. The existing teach-turn JSON (explanation/analogy/example/visual/question)
gets deterministically split into scenes by word-count-based duration estimation:
`intro` (the core explanation) → `explain` (analogy + example + visual, skipped in Exam
mode) → `question` (always last, tagged exam-style if in Exam mode). The frontend plays
them back-to-back with `SpeechSynthesis`, awaiting each scene's narration before advancing
(or press the skip button to jump straight to the question). Swapping in HeyGen/D-ID later
means changing what consumes `scene.narration` — the scene schema itself doesn't change.

### Personalities vs. modes — the distinction

- **Personality** (Friendly/Strict exam teacher/Storyteller/Socratic/Technical mentor) changes
  *how* things are said — tone, analogies, feedback phrasing. Enforced by an explicit
  system-prompt instruction that facts must never change with personality.
- **Mode** (Learn/Exam/Revision/Practice) changes *what happens structurally* — which
  concepts are taught (Revision mode is filtered deterministically from `student_memory`,
  not just "asked nicely" of the LLM), how many scenes play, and how the question is framed.

### Testing performed for v3

All exercised via `TestClient` with the LLM mocked (16 endpoints in one smoke run, plus
targeted scenario tests):

- Personality tone is passed through to both the teach and eval system prompts, without a
  content change (verified `personality` field survives a mid-lesson switch).
- Revision mode: verified the planner prompt received the exact forced concept list
  (`Current`) recovered from `student_memory`, and that Revision mode silently falls back
  to Learn mode when there's no history yet.
- Recall note: ran two sessions for the same student on the same topic; confirmed session 2
  received a deterministic recall note and that it appeared as the first scene, exactly once.
- Scenes: confirmed every teach turn returns a non-empty `scenes` array and that reteach
  turns produce different scene content (new analogy/visual) than the original turn.
- Homework: generate → submit → deterministic score → retry (fresh set), end to end.
- Analytics: unit-tested `analytics.build_dashboard()` directly with synthetic multi-session
  data (mastery %, weak/strong split, repeated-misconception counting, 2-day streak,
  score-improvement calculation) — all independent of any LLM call.
- Full regression: re-ran the v1/v2 flow (anonymous session, no `student_id`/`personality`/
  `mode` supplied) to confirm old-style callers are unaffected.
- Real server boot: started `uvicorn` for real (not just `TestClient`) and confirmed
  `/api/health` responds; served the frontend via `python -m http.server` and confirmed
  `index.html`/`app.js`/`style.css` all return 200 with balanced HTML tags.

## What's new in v5

### One-sentence summary

**An AI teacher that doesn't just teach and quiz you — it asks you to explain what you
learned, understands whether you truly understood it, reteaches your specific
misconception, and remembers when you should review it again.**

### 1. Groq migration

`llm.py` was rewritten to call Groq instead of Anthropic, behind the exact same
`llm.call_json(system, user, model=None, max_tokens=1200)` interface — so `main.py` and
`prompts.py` needed **zero changes** for the swap. Env vars: `GROQ_API_KEY` (required),
`GROQ_MODEL` (optional, defaults to `llama-3.3-70b-versatile`), `GROQ_EVAL_MODEL`
(optional, defaults to `GROQ_MODEL`). Tries Groq's native JSON mode first, falls back to
fence-stripping + brace-extraction + one retry if a model doesn't support it. Get a key at
https://console.groq.com/keys.

### 2. Explain-It-Back Verification (the signature feature)

After teaching a concept, the student can click **"🗣️ Explain it back"** at any point.
Instead of an MCQ, the AI asks them to explain the concept in their own words — the
question *type* (definition / causal / relationship / application / teach-a-friend /
compare) is chosen by the LLM to fit the concept. Their explanation is graded by a
**semantic evaluator, not keyword matching** — it reasons about what relationships and
cause/effect the student actually demonstrated, then returns `understanding_level`,
`score`, `understood`, `missing`, `misconceptions`, `feedback`, and `action`
(continue/clarify/reteach/simplify/ask_follow_up).

Critically, **the result changes what happens next**, not just what gets recorded:

```
Student explains incorrectly
  -> semantic evaluator finds a misconception
  -> action = "reteach"
  -> _generate_teach_content(is_reteach=True) is called RIGHT NOW,
     with that exact misconception fed in, using a fresh analogy/visual
  -> student explains again
  -> deterministic mastery update from the new score
```

Every attempt is stored in `explain_back_attempts` (with a deterministic attempt-number
counter), so the report can show "Attempt 1: 35% → Attempt 2: 90% — understanding
improved" — proof the AI actually helped, not just graded.

New endpoints: `GET/POST /api/sessions/{id}/explain-back`, `GET .../explain-back/history`.

### 3. Time-Budget Honesty

The planner now asks the LLM to estimate **every** genuinely important concept for a
topic (not pre-filtered to fit the time budget) with per-concept `estimated_minutes` and
`priority`. A new deterministic function, `progress.build_time_plan()`, then decides —
never the LLM — which concepts actually fit the requested time, whether the request was
realistic, and builds the honest message ("20 minutes isn't enough to cover all 8
concepts — I'll cover the 3 most important now..."). Surfaced both as a dashboard toast
and as the literal opening line of the first scene, so the student hears it, not just
reads it in a JSON blob.

### 4. Spaced Revision

A fixed, deterministic interval table in `progress.py` — weak → 1 day, learning → 3 days,
mastered → 7 days, repeatedly mastered → 14 days — computed automatically at the end of
every session's `/report` call and stored in a new `spaced_review` table. The dashboard
shows a **"📚 Due for Review"** section (`GET /api/students/{id}/reviews`) with a
"Start review" button per due concept, which kicks off a real Revision-mode session
scoped to exactly that concept (reuses the existing session-creation pipeline — no
parallel "review" code path to maintain).

### Files changed / added in v5

| File | Change |
|---|---|
| `llm.py` | Rewritten for Groq (see above). |
| `db.py` | New tables: `explain_back_attempts`, `spaced_review`. New `page` column on `chunks` (RAG citations). New `strategy` column on `sessions`. New `attempt` column on `homework` (retry tracking). Extended `student_profile` with `preferred_difficulty`, `recent_topics`, `learning_goals`. |
| `progress.py` | Added `compute_confidence()` (deterministic struggle signal from wrong-streaks/short-answers/repeated misconceptions — explicitly NOT emotion detection), `build_knowledge_graph()` / `check_prerequisite_gate()`, `compute_exam_readiness()`, `build_next_lesson_options()`, `schedule_next_review()` / `describe_due_date()` / `prioritize_reviews()`, `build_time_plan()`. |
| `visual_engine.py` | Added `comparison`, `flowchart`, `process`, `simulation` visual types (on top of v3's equation/graph/diagram/timeline/code/molecule/bullets). |
| `prompts.py` | Added `STRATEGY_NOTE` (Standard/Socratic/Exam-focused/Example-first — independent of personality), `explain_back_question_prompt()`, `explain_back_eval_prompt()`. `plan_prompt` now requests per-concept time estimates instead of a hard concept count. `teach_prompt` now accepts `strategy`, `confidence`, `prerequisite_note`, RAG source metadata, and an honesty instruction for "material uploaded but nothing relevant to this concept." |
| `rag.py` | Added `extract_pages()` (page-aware PDF extraction) and `chunk_pages()`; `Retriever` now optionally carries per-chunk metadata (`top_k_with_meta`) for citations. |
| `main.py` | New Explain-It-Back and strategy-switch endpoints. Session creation now runs the deterministic time-budget filter and folds due spaced-reviews into the recall note. `/report` now schedules the next review per concept and returns `review_schedule` + `explain_back_summary`. |
| `schemas.py` | Added `StrategyRequest`, `ExplainBackRequest`. |
| `frontend/*` | Explain-it-back modal (attempt-history badges, live feedback, reteach hand-off back into the scene player). "Due for Review" dashboard section. Teaching-strategy dashboard selector. Report screen understanding/review-schedule sections. Time-budget-honesty toast. |

### New API endpoints

| Method & path | Purpose |
|---|---|
| `GET/POST /api/sessions/{id}/explain-back` | Get an explain-it-back question / submit an explanation for semantic evaluation. |
| `GET /api/sessions/{id}/explain-back/history` | All attempts for this session, in order (for the "improved" comparison). |
| `POST /api/sessions/{id}/strategy` | Switch teaching strategy mid-lesson (independent of personality). |
| `GET /api/students/{id}/reviews` | Deterministic spaced-review due-list. |

`POST /api/sessions` gained `strategy` and `review_concept` form fields (both optional).
Its response gained `strategy` and `time_plan`. `/report`'s response gained
`review_schedule` and `explain_back_summary`.

### Reliability rule enforced

The LLM never directly sets mastery, review dates, analytics, or completion state — it
only ever produces a semantic evaluation or a content estimate. Every state change
(`explain-back` score → mastery delta, `estimated_minutes` → feasibility decision,
concept status → review interval) is computed by a plain Python function in `progress.py`
that takes the LLM's output as **input**, never as the decision itself.

### Testing performed for v5

All via `TestClient` with the LLM mocked:
- Groq wrapper: missing-key error, mocked JSON-mode success, and the fallback path when
  JSON mode isn't supported (fence-stripped, brace-extracted, parses correctly).
- Explain-it-back full flow: a deliberately wrong explanation (35%) actually triggers a
  reteach call with a genuinely different analogy (not just a lower score recorded), a
  second attempt (90%) is logged, and `/explain-back/history` shows the improvement.
- Time-budget honesty: a 5-minute request against 3 concepts (~4 min each) correctly
  reduces scope with an honest message; a 60-minute request against the same concepts is
  feasible with no message.
- Spaced revision: full `/report` run correctly schedules per-concept review intervals
  matching the deterministic table, and `/reviews` sorts/labels them correctly; a
  `review_concept`-triggered session is deterministically filtered to exactly that concept
  even when the mocked LLM ignored the instruction (found and fixed this exact bug during testing).
- Full v1-v5 regression: a 19-endpoint sweep including an old-style anonymous session
  (no `student_id`/`personality`/`mode`/`strategy`) — all pass unchanged.
- Real server boot: `uvicorn main:app` started for real (not just `TestClient`) and
  correctly attempted to reach `api.groq.com` on a session-creation call — confirming the
  full request path works end-to-end up to the actual network call.

## Known limitations (be upfront about these with judges)

- Video/photorealistic avatar is not implemented — the MVP uses a synced SVG face with
  simple eyebrow-expression changes, by design, to stay free and keep latency near-zero
  (see "Swapping in a real avatar" below for the upgrade path; the scene schema was built
  so that swap doesn't require touching the teaching engine).
- RAG retrieval (TF-IDF) is good for a single document's worth of notes; it is not a
  substitute for a real vector database over a large corpus.
- `SpeechSynthesis` voice quality/availability for Hindi depends on the judge's OS and
  browser — Chrome on Windows/Android generally has the best Hindi voice coverage.
- No user accounts/auth — long-term student memory is keyed on a `localStorage`-generated
  id, which is fine for a demo (and genuinely persists across sessions in the same browser)
  but would need a real account system before any production deployment; clearing browser
  storage resets it.
- The "top navigation" is a lightweight New-lesson/My-progress pair rather than a full
  Learn/Practice/Revision/Exam/Progress/Learning-Path tab bar — those four modes are
  selected as part of starting a lesson (dashboard pills) rather than as persistent global
  tabs, since mode is a property of a single session, not a standing app-wide view.
- Homework and quiz grading both make one LLM call per question — fine for the 3-6
  question sets used here, but would need batching for a much larger question set.
- Molecule/timeline/diagram visuals use a simple deterministic layout (circular for
  molecules, left-to-right for node/edge diagrams) rather than a real chemistry-structure
  or historical-map renderer — enough to demonstrate the concept, not publication-quality.
- Explain-It-Back's "reteach" hand-off closes the modal and shows the new content on the
  main lesson screen rather than replaying inline in the modal — a deliberate simplicity
  trade-off so the existing scene player could be reused rather than duplicated.
- Groq's hosted model lineup changes over time; if `GROQ_MODEL`'s default has been
  deprecated, override it in `.env` (check https://console.groq.com/docs/models).
- In this development sandbox, outbound calls to `api.groq.com` are blocked by network
  policy — the full request path was verified up to and including the actual Groq call
  (confirmed via the real error message), but a live token-for-token response wasn't
  captured here. This is a sandbox restriction, not an application issue — a normal
  local/deployed environment reaches Groq with no special configuration.
- The v4 "real-time teacher status" (🟢/🔵/🟡/🟣/🟠 state dots) and full accessibility
  panel (font size, high contrast, slow speech) described in earlier planning were not
  implemented in the frontend — voice status text (Listening/Processing/Speaking) and the
  read-aloud mute toggle are in place, but the fuller accessibility control panel is not.
