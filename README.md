# Mentora — AI Teacher

An adaptive AI tutor that plans a lesson from your notes, teaches one concept at a time through short video-style scenes, checks whether you actually understood it — not just whether you picked the right answer — and reschedules what you review and when.

## Overview

Mentora was built for a hackathon around the theme **"Build a Human-Like AI Educator That Teaches Through Video."** The idea is to move past a plain chatbot-with-a-syllabus-prompt by following an explicit teaching loop: **Understand → Plan → Explain → Demonstrate → Question → Evaluate → Adapt → Continue.**

A student picks (or uploads notes on) a topic, level, language, and time budget. The backend uses an LLM to plan a sequence of concepts, teaches each one through narrated scenes with a visual and a check-question, and — this is the core novelty — a small, explainable **Adaptive Teaching Engine** written in plain Python decides after every answer whether to re-teach with a new analogy, raise the difficulty, move on, or finish. The LLM generates content and makes judgment calls (is this answer right? what does it estimate this will take?); deterministic Python code makes every state change, score, and progression decision. Everything else — retrieval-grounded teaching from uploaded notes, voice, the animated avatar, personalities, quizzes, homework, analytics, and spaced review — exists to support that core loop.

## Key Features

- **Adaptive Teaching Engine** — a pure, dependency-free decision function (`adaptive_engine.py`) that reteaches, raises difficulty, advances, or finishes a session based on correctness, streaks, and attempt counts. No LLM involved in the decision itself.
- **RAG-grounded teaching** — upload a PDF/DOCX/PPTX and the app extracts, chunks, and retrieves the most relevant passages (TF-IDF + cosine similarity) to ground explanations in the student's own material, with page-level source tracking.
- **Explain-It-Back Verification (signature feature)** — instead of only answering multiple-choice-style questions, students can explain a concept in their own words. A semantic evaluator (not keyword matching) scores understanding, names specific misconceptions, and — if the explanation reveals a misconception — immediately triggers a reteach with a fresh analogy. Every attempt is logged so a report can show measurable improvement (e.g. 35% → 90%).
- **Time-Budget Honesty** — the planner estimates time for every genuinely relevant concept; a deterministic function then decides which concepts actually fit the requested time and produces an honest message if the request wasn't realistic, rather than silently cramming or dropping content.
- **Spaced Revision** — a fixed deterministic interval table schedules the next review date for every concept (weak → 1 day, learning → 3 days, mastered → 7 days, repeatedly mastered → 14 days), surfaced as a "Due for Review" list that can launch a scoped revision session.
- **Teaching Video Engine** — each teaching turn is deterministically split into narrated scenes (intro / explain / question) with estimated durations, played back with the browser's speech synthesis and an animated SVG avatar — no extra LLM call or video-generation API involved.
- **Multiple visual types** — the model picks from equation, graph, diagram, timeline, code, molecule, bullets, comparison, flowchart, process, or simulation layouts; `visual_engine.py` validates and normalizes whatever the LLM returns, with safe fallbacks for malformed output.
- **Personalities, strategies & modes** — 5 teaching personalities (tone only), teaching strategies (Standard/Socratic/Exam-focused/Example-first), and 4 session modes (Learn/Exam/Revision/Practice) that change structure, not facts.
- **Knowledge map, quiz, homework & study notes** — a live concept-mastery map, an end-of-lesson quiz, a personalized 5-tier homework ladder generated from weak concepts, and auto-generated study notes.
- **Long-term memory & analytics** — a `localStorage`-generated student ID (no login system) lets the app recall past weak spots across sessions and build a deterministic cross-session dashboard (mastery %, streaks, score trends, repeated misconceptions).
- **Mid-lesson language switching** — switch teaching language (English/Hindi/Hinglish) without losing progress or history.

## How It Works

1. The student starts a session from the dashboard (topic, level, language, time budget, personality, strategy, mode, optional notes upload). `frontend/app.js` calls `POST /api/sessions`.
2. `main.py` optionally extracts and chunks the uploaded document (`rag.py`), checks the student's long-term memory and any due spaced reviews for a deterministic "recall" note, asks the LLM (via `llm.py` → Groq) to plan a concept list, and then runs the plan through the deterministic **Time-Budget Honesty** filter (`progress.py`) before storing the session in SQLite.
3. For each concept, `GET /api/sessions/{id}/next` asks the LLM for an explanation, analogy, worked example, a visual, and a check-question — grounded in retrieved note chunks if any were uploaded — and splits it into playable scenes (`scenes.py`).
4. The student answers (`POST /api/sessions/{id}/answer`) or explains the concept back (`/explain-back`). The LLM evaluates the response; the **Adaptive Teaching Engine** and `progress.py` — never the LLM — decide what happens next: reteach, raise difficulty, advance, or finish, and update the mastery/knowledge-map state.
5. At the end of the lesson, a quiz is generated and graded, and `GET /api/sessions/{id}/report` merges deterministic scores/mastery data with LLM-written narrative feedback, schedules the next spaced-review dates, and summarizes the Explain-It-Back attempt history.

## Tech Stack

**Frontend**
- Plain HTML, CSS, and JavaScript (no build step, no framework)
- Browser `SpeechSynthesis` API for text-to-speech
- CSS/SVG-driven avatar animation synced to speech events
- `localStorage` for student ID, theme, and session-draft persistence (no server-side auth)

**Backend**
- FastAPI (Python), served via Uvicorn
- Pydantic v2 for request/response schemas

**AI / ML / LLM**
- Groq API (`groq` Python SDK) for all LLM calls — planning, teaching, evaluation, explain-it-back grading, quiz/homework grading, and report generation, via a single `llm.call_json()` interface
- TF-IDF vectorization + cosine similarity (scikit-learn) for retrieval — no embedding API, no vector database

**Database**
- SQLite (Python's built-in `sqlite3`), a single local file (`ai_teacher.db`)

**Document processing libraries**
- `pypdf` (PDF text/page extraction), `python-docx` (DOCX), `python-pptx` (PPTX)

**External services**
- Groq API only. No other third-party API is used — voice, retrieval, and the database are all local.

## Project Structure

```text
Mentora/
├── backend/
│   ├── main.py              # FastAPI app + all API endpoints
│   ├── adaptive_engine.py   # deterministic teaching-decision state machine
│   ├── progress.py          # mastery, knowledge map, time-budget, spaced review, analytics helpers
│   ├── prompts.py           # all LLM prompt builders (plan/teach/eval/report/etc.)
│   ├── llm.py               # Groq API wrapper with JSON parsing + retry
│   ├── rag.py                # document extraction, chunking, TF-IDF retrieval
│   ├── scenes.py             # splits a teaching turn into playable scenes
│   ├── visual_engine.py      # validates/normalizes LLM-generated visual specs
│   ├── analytics.py          # deterministic cross-session analytics
│   ├── db.py                  # SQLite schema + persistence
│   ├── schemas.py              # Pydantic request models
│   ├── requirements.txt
│   ├── .env.example
│   └── ai_teacher.db          # created automatically at runtime (gitignored)
├── frontend/
│   ├── index.html            # dashboard, lesson, quiz, report, progress screens
│   ├── app.js                 # API calls, lesson flow, TTS/avatar, localStorage state
│   └── style.css               # design system / theming
├── .gitignore
└── README.md
```

> **Note:** if you extracted a zip, your top-level folder may be auto-named something like `ai-teacher-fixed (1)` instead of `Mentora`. It's the same project — just `cd` into whatever folder actually contains `backend/` and `frontend/`. Renaming it to something simple (e.g. `Mentora`) is recommended, especially on Windows — see Troubleshooting below.

## Getting Started

### Prerequisites

- Python 3.10+ (a recent 3.x interpreter with `sqlite3` support)
- pip
- A free [Groq API key](https://console.groq.com/keys)
- Any modern browser (Chrome recommended for the widest `SpeechSynthesis` voice coverage, including Hindi)

### Installation

**Windows (PowerShell)**

```powershell
# 1. Clone the repository
git clone <your-repo-url> Mentora
cd Mentora

# 2. Set up the backend
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Configure environment variables
copy .env.example .env
# open .env and paste your GROQ_API_KEY

# 4. Start the backend
uvicorn main:app --reload --port 8000
```

**macOS / Linux**

```bash
# 1. Clone the repository
git clone <your-repo-url> Mentora
cd Mentora

# 2. Set up the backend
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# edit .env and paste your GROQ_API_KEY

# 4. Start the backend
uvicorn main:app --reload --port 8000
```

**Start the frontend (in a second terminal, from the project root)**

```bash
cd frontend
python3 -m http.server 5500
# open http://localhost:5500 in your browser
```

If the frontend runs on a different origin/port than the ones listed in `CORS_ORIGINS`, either add it to `backend/.env`, or set `window.API_BASE = "http://localhost:8000"` near the top of `frontend/app.js` if the backend runs elsewhere.

Once both servers are running, open your browser and go to **http://localhost:5500** — that's the app. It talks to the backend on port 8000 behind the scenes; you don't open port 8000 directly.

## Environment Variables

Copy `backend/.env.example` to `backend/.env` and fill in the values:

```env
# Required — get a free key at https://console.groq.com/keys
GROQ_API_KEY=your_groq_api_key_here

# Model used for planning, teaching, explain-it-back, and reports.
# Must be set (the app reads this directly with no fallback) — the
# .env.example ships a working default; check https://console.groq.com/docs/models
# if it has since been retired.
GROQ_MODEL=openai/gpt-oss-120b

# Optional — point grading calls at a cheaper/faster model. Defaults to GROQ_MODEL if unset.
# GROQ_EVAL_MODEL=llama-3.1-8b-instant

# Optional — path to the local SQLite file. Defaults to ./ai_teacher.db
DB_PATH=./ai_teacher.db

# Optional — comma-separated list of allowed frontend origins for CORS.
CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500,http://localhost:3000
```

No other external API keys are required — voice uses the browser's built-in `SpeechSynthesis`, retrieval is local TF-IDF, and the database is a local SQLite file.

## API / Backend

All routes are prefixed `/api`. Session/student IDs are path parameters.

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/sessions` | Create a session (topic, level, language, time budget, personality, strategy, mode, optional student ID, optional notes file). Generates the lesson plan and applies time-budget filtering. |
| `GET` | `/sessions/{id}/state` | Current concept, difficulty, elapsed time, grounded/finished flags. |
| `GET` | `/sessions/{id}/next` | Generates the teaching content (and scenes) for the current concept. |
| `POST` | `/sessions/{id}/answer` | Body `{"answer": "..."}`. Evaluates the answer, runs the adaptive engine, updates mastery, returns the next step. |
| `GET`/`POST` | `/sessions/{id}/explain-back` | Get an explain-it-back question / submit an explanation for semantic evaluation and possible reteach. |
| `GET` | `/sessions/{id}/explain-back/history` | All explain-it-back attempts for the session, in order. |
| `GET` | `/sessions/{id}/knowledge-map` | Per-concept mastery status, score, and attempts. |
| `POST` | `/sessions/{id}/language` | Switch teaching language mid-lesson. |
| `POST` | `/sessions/{id}/personality` | Switch teacher personality mid-lesson (tone only). |
| `POST` | `/sessions/{id}/strategy` | Switch teaching strategy mid-lesson. |
| `GET` | `/sessions/{id}/quiz` | Generate (once) and return the end-of-lesson quiz. |
| `POST` | `/sessions/{id}/quiz/submit` | Grade quiz answers and mark the session finished. |
| `GET` | `/sessions/{id}/notes` | Auto-generated study notes. |
| `GET` | `/sessions/{id}/learning-path` | Suggested next topics, or a deterministic revision gate. |
| `GET` | `/sessions/{id}/report` | Final score, mastery breakdown, misconceptions, revision schedule, and explain-it-back summary. |
| `GET` | `/sessions/{id}/homework` | Generate personalized homework from weak/learning concepts. |
| `POST` | `/sessions/{id}/homework/submit` | Grade homework answers. |
| `POST` | `/sessions/{id}/homework/retry` | Regenerate a fresh homework set. |
| `GET` | `/students/{student_id}/analytics` | Cross-session dashboard (mastery, streaks, trends). |
| `GET` | `/students/{student_id}/reviews` | Spaced-review due-list. |
| `GET` | `/health` | Liveness check. |

## Screenshots / Demo

_No screenshots have been added yet — placeholders below for you to fill in._

```markdown
## Screenshots

### Dashboard
![Mentora Dashboard](screenshots/dashboard.png)

### Lesson Screen
![Mentora Lesson](screenshots/lesson.png)

### Report Screen
![Mentora Report](screenshots/report.png)
```

## Troubleshooting

**`Set-Location : A positional parameter cannot be found` or `Cannot find path` in PowerShell**
Your folder name has a space or parentheses in it (e.g. `Mentora Final`, `ai-teacher-fixed (1)`). PowerShell needs quotes around any path with spaces or special characters:
```powershell
cd "ai-teacher-fixed (1)"
```
To avoid this entirely, rename the extracted folder to something simple like `Mentora`:
```powershell
Rename-Item "ai-teacher-fixed (1)" "Mentora"
```

**Not sure which folder you're in / what's inside it**
Run `dir` (PowerShell/CMD) or `ls` (macOS/Linux/Git Bash) to list the contents of your current folder before running `cd`.

**Nothing loads at `http://localhost:5500`**
- Confirm the frontend terminal is still running `python -m http.server 5500` and that you started it *from inside* the `frontend` folder — if you're in the wrong folder you'll get a generic directory listing instead of the app.
- Try `http://127.0.0.1:5500` instead of `localhost` if the browser can't resolve it.

**The page loads but lessons won't start / API calls fail**
- Check the backend terminal for errors. The most common cause is a missing or invalid `GROQ_API_KEY` in `backend/.env`.
- Make sure the backend terminal is still open and shows `Uvicorn running on http://127.0.0.1:8000` with no crash.
- If you changed the backend port or the frontend port, update `CORS_ORIGINS` in `backend/.env` (or `window.API_BASE` in `frontend/app.js`) to match.

**`address already in use` when starting a server**
Another process is already using port 8000 or 5500. Either stop that process, or start on a different port: `uvicorn main:app --reload --port 8001` and `python -m http.server 5501` (updating `CORS_ORIGINS`/`API_BASE` accordingly).

## Future Improvements

- A real hosted talking-head avatar (e.g. via a paid video-avatar API) in place of the current SVG face
- Swapping TF-IDF retrieval for embedding-based retrieval and a real vector database for larger note collections
- A proper account/auth system in place of the current `localStorage`-based student ID
- Batched grading calls for larger quiz/homework question sets
- A persistent global navigation covering all four session modes, rather than selecting mode per-session on the dashboard

## Known Limitations

- No photorealistic avatar — the current avatar is an animated SVG face synced to browser text-to-speech.
- TF-IDF retrieval works well for a single document's worth of notes but isn't a substitute for a real vector database over a large corpus.
- `SpeechSynthesis` voice quality and language availability (especially Hindi) depend on the user's OS/browser.
- There is no login system — long-term memory is keyed on a `localStorage`-generated student ID, which resets if browser storage is cleared and doesn't sync across devices.
- Quiz and homework grading make one LLM call per question, which works for the small question sets used here but wouldn't scale to much larger sets without batching.
- Molecule, timeline, and diagram visuals use simple deterministic layouts rather than domain-specific renderers.
- Groq's hosted model lineup changes over time; if the default `GROQ_MODEL` has been deprecated, it needs to be overridden in `.env`.

## Contributing

This is a student/hackathon project. Issues and pull requests are welcome — please open an issue first for anything beyond a small fix so it can be discussed.

## License

This project does not currently specify a license.

## Acknowledgements / Credits

- [Groq](https://groq.com) for LLM inference
- [FastAPI](https://fastapi.tiangolo.com) and [Uvicorn](https://www.uvicorn.org)
- [scikit-learn](https://scikit-learn.org) for TF-IDF retrieval
- [pypdf](https://pypdf.readthedocs.io), [python-docx](https://python-docx.readthedocs.io), and [python-pptx](https://python-pptx.readthedocs.io) for document parsing
