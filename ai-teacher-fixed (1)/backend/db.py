"""
Lightweight SQLite persistence layer.

Deliberately avoids an ORM: this is a hackathon MVP and the schema is small
and stable. Every function opens and closes its own connection so the module
is safe to use from FastAPI's threaded endpoint handlers.
"""
import sqlite3
import json
import os
import uuid
import time
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

DB_PATH = os.environ.get("DB_PATH", "./ai_teacher.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    topic TEXT,
    level TEXT,
    language TEXT,
    time_minutes INTEGER,
    learning_goal TEXT,
    plan_json TEXT,
    concept_index INTEGER DEFAULT 0,
    difficulty INTEGER DEFAULT 2,
    attempts_current INTEGER DEFAULT 0,
    consecutive_correct INTEGER DEFAULT 0,
    grounded INTEGER DEFAULT 0,
    finished INTEGER DEFAULT 0,
    quiz_done INTEGER DEFAULT 0,
    student_id TEXT,
    personality TEXT DEFAULT 'Friendly',
    strategy TEXT DEFAULT 'Standard',
    mode TEXT DEFAULT 'Learn',
    recall_note TEXT,
    created_at REAL,
    started_at REAL
);

-- Detailed misconception log (v2). `mastery.misconceptions` still keeps a short
-- list per concept for quick access; this table keeps the full record (the
-- question, the answer, the reasoning) so study notes and the report can cite
-- specifics instead of just a one-line phrase.
CREATE TABLE IF NOT EXISTS misconceptions (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    concept TEXT,
    misconception TEXT,
    why_wrong TEXT,
    question TEXT,
    student_answer TEXT,
    created_at REAL
);

-- Final quiz (v2): a short set of mixed-concept questions asked once all
-- concepts have been taught, before the final report is generated.
CREATE TABLE IF NOT EXISTS quiz_questions (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    idx INTEGER,
    concept TEXT,
    question TEXT,
    difficulty INTEGER
);

CREATE TABLE IF NOT EXISTS quiz_answers (
    question_id TEXT PRIMARY KEY,
    session_id TEXT,
    answer TEXT,
    correct INTEGER,
    partial_credit REAL,
    feedback TEXT,
    misconception TEXT
);

-- v3: scene-based teaching video engine. Scenes are derived deterministically
-- from a teach turn's JSON (see scenes.py) and persisted so a lesson can be
-- "replayed" without re-calling the LLM.
CREATE TABLE IF NOT EXISTS lesson_scenes (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    concept TEXT,
    idx INTEGER,
    scene_json TEXT
);

-- v3: personalized homework, generated from this session's weak concepts.
CREATE TABLE IF NOT EXISTS homework (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    concept TEXT,
    tier TEXT,              -- easy | medium | application | explain_own_words | challenge
    question TEXT,
    attempt INTEGER DEFAULT 1,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS homework_answers (
    question_id TEXT PRIMARY KEY,
    session_id TEXT,
    answer TEXT,
    correct INTEGER,
    partial_credit REAL,
    feedback TEXT,
    created_at REAL
);

-- v3: long-term, cross-session student memory. Keyed by a client-generated
-- student_id (browser localStorage, no login system for this MVP) rather
-- than a real account, so it's free to demo but still genuinely persists.
CREATE TABLE IF NOT EXISTS student_memory (
    student_id TEXT,
    topic TEXT,
    concept TEXT,
    status TEXT,
    score REAL,
    attempts INTEGER,
    correct INTEGER,
    misconceptions TEXT DEFAULT '[]',
    last_seen REAL,
    PRIMARY KEY (student_id, topic, concept)
);

CREATE TABLE IF NOT EXISTS student_profile (
    student_id TEXT PRIMARY KEY,
    preferred_language TEXT,
    preferred_personality TEXT,
    preferred_difficulty INTEGER,
    recent_topics TEXT DEFAULT '[]',
    learning_goals TEXT DEFAULT '[]',
    total_time_seconds REAL DEFAULT 0,
    sessions_completed INTEGER DEFAULT 0,
    last_active REAL
);

-- v3: append-only event log — the raw material for deterministic analytics
-- (streaks, score trend, time spent). Never read by the LLM.
CREATE TABLE IF NOT EXISTS analytics_events (
    id TEXT PRIMARY KEY,
    student_id TEXT,
    session_id TEXT,
    event_type TEXT,        -- session_completed | quiz_scored | homework_scored
    payload_json TEXT,
    created_at REAL
);

-- v5: Explain-It-Back Verification -- the signature feature. Every attempt
-- (including retries after a reteach) is stored so the report can show
-- "attempt 1 -> attempt 2" improvement, not just a final score.
CREATE TABLE IF NOT EXISTS explain_back_attempts (
    id TEXT PRIMARY KEY,
    student_id TEXT,
    session_id TEXT,
    concept TEXT,
    question_type TEXT,
    prompt TEXT,
    student_response TEXT,
    score INTEGER,
    understanding_level TEXT,
    understood TEXT DEFAULT '[]',
    missing TEXT DEFAULT '[]',
    misconceptions TEXT DEFAULT '[]',
    feedback TEXT,
    action TEXT,
    attempt_number INTEGER,
    created_at REAL
);

-- v5: deterministic spaced-repetition schedule, one row per (student, topic, concept).
CREATE TABLE IF NOT EXISTS spaced_review (
    student_id TEXT,
    topic TEXT,
    concept TEXT,
    mastery REAL,
    review_count INTEGER DEFAULT 0,
    last_studied REAL,
    next_review_at REAL,
    reason TEXT,
    PRIMARY KEY (student_id, topic, concept)
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    source TEXT,
    text TEXT,
    ord_index INTEGER,
    page INTEGER
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    turn_index INTEGER,
    concept TEXT,
    difficulty INTEGER,
    kind TEXT,              -- 'teach' | 'answer_eval'
    content_json TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS mastery (
    session_id TEXT,
    concept TEXT,
    score REAL DEFAULT 0,       -- -2..+2 running signal
    attempts INTEGER DEFAULT 0,
    correct INTEGER DEFAULT 0,
    misconceptions TEXT DEFAULT '[]',
    PRIMARY KEY (session_id, concept)
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Lightweight migration for DBs created before v2: add any columns that
        # were added later. SQLite errors if a column already exists, so each
        # is wrapped individually and ignored if it's already there.
        for ddl in (
            "ALTER TABLE sessions ADD COLUMN quiz_done INTEGER DEFAULT 0",
            "ALTER TABLE sessions ADD COLUMN student_id TEXT",
            "ALTER TABLE sessions ADD COLUMN personality TEXT DEFAULT 'Friendly'",
            "ALTER TABLE sessions ADD COLUMN strategy TEXT DEFAULT 'Standard'",
            "ALTER TABLE sessions ADD COLUMN mode TEXT DEFAULT 'Learn'",
            "ALTER TABLE sessions ADD COLUMN recall_note TEXT",
            "ALTER TABLE chunks ADD COLUMN page INTEGER",
            "ALTER TABLE homework ADD COLUMN attempt INTEGER DEFAULT 1",
            "ALTER TABLE student_profile ADD COLUMN preferred_difficulty INTEGER",
            "ALTER TABLE student_profile ADD COLUMN recent_topics TEXT DEFAULT '[]'",
            "ALTER TABLE student_profile ADD COLUMN learning_goals TEXT DEFAULT '[]'",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def create_session(topic, level, language, time_minutes, learning_goal, plan: Dict[str, Any],
                    personality: str = "Friendly", mode: str = "Learn",
                    student_id: Optional[str] = None, recall_note: Optional[str] = None) -> str:
    sid = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sessions
               (id, topic, level, language, time_minutes, learning_goal, plan_json,
                concept_index, difficulty, grounded, finished, quiz_done,
                student_id, personality, mode, recall_note, created_at, started_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 0, 0, 0, ?, ?, ?, ?, ?, ?)""",
            (sid, topic, level, language, time_minutes, learning_goal,
             json.dumps(plan), plan.get("starting_difficulty", 2),
             student_id, personality, mode, recall_note, time.time(), time.time()),
        )
    return sid


def get_session(session_id: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row


def update_session(session_id: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [session_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE sessions SET {cols} WHERE id = ?", vals)


def add_chunks(session_id: str, source: str, texts: List[str], pages: Optional[List[Optional[int]]] = None):
    pages = pages or [None] * len(texts)
    with get_conn() as conn:
        for i, (t, page) in enumerate(zip(texts, pages)):
            conn.execute(
                "INSERT INTO chunks (id, session_id, source, text, ord_index, page) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), session_id, source, t, i, page),
            )


def get_chunks(session_id: str) -> List[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT text FROM chunks WHERE session_id = ? ORDER BY ord_index", (session_id,)
        ).fetchall()
        return [r["text"] for r in rows]


def get_chunks_with_meta(session_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT source, text, page FROM chunks WHERE session_id = ? ORDER BY ord_index", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_turn(session_id: str, turn_index: int, concept: str, difficulty: int, kind: str, content: Dict[str, Any]):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO turns (id, session_id, turn_index, concept, difficulty, kind, content_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id(), session_id, turn_index, concept, difficulty, kind, json.dumps(content), time.time()),
        )


def get_turns(session_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index", (session_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["content"] = json.loads(d.pop("content_json"))
            out.append(d)
        return out


def upsert_mastery(session_id: str, concept: str, delta: float, correct: bool, misconception: Optional[str]):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM mastery WHERE session_id = ? AND concept = ?", (session_id, concept)
        ).fetchone()
        if row is None:
            miscons = [misconception] if misconception else []
            conn.execute(
                """INSERT INTO mastery (session_id, concept, score, attempts, correct, misconceptions)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (session_id, concept, delta, 1 if correct else 0, json.dumps(miscons)),
            )
        else:
            miscons = json.loads(row["misconceptions"])
            if misconception:
                miscons.append(misconception)
            new_score = row["score"] + delta
            conn.execute(
                """UPDATE mastery SET score = ?, attempts = ?, correct = ?, misconceptions = ?
                   WHERE session_id = ? AND concept = ?""",
                (new_score, row["attempts"] + 1, row["correct"] + (1 if correct else 0),
                 json.dumps(miscons), session_id, concept),
            )


def add_misconception(session_id: str, concept: str, misconception: str, why_wrong: str,
                       question: str, student_answer: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO misconceptions
               (id, session_id, concept, misconception, why_wrong, question, student_answer, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id(), session_id, concept, misconception, why_wrong, question, student_answer, time.time()),
        )


def get_misconceptions(session_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM misconceptions WHERE session_id = ? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_quiz_questions(session_id: str, questions: List[Dict[str, Any]]) -> List[str]:
    """questions: [{"concept":..., "question":..., "difficulty":...}]. Returns generated ids in order."""
    ids = []
    with get_conn() as conn:
        for i, q in enumerate(questions):
            qid = new_id()
            ids.append(qid)
            conn.execute(
                "INSERT INTO quiz_questions (id, session_id, idx, concept, question, difficulty) VALUES (?, ?, ?, ?, ?, ?)",
                (qid, session_id, i, q["concept"], q["question"], q.get("difficulty", 2)),
            )
    return ids


def get_quiz_questions(session_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM quiz_questions WHERE session_id = ? ORDER BY idx", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_quiz_answer(question_id: str, session_id: str, answer: str, correct: bool,
                     partial_credit: float, feedback: str, misconception: Optional[str]):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO quiz_answers
               (question_id, session_id, answer, correct, partial_credit, feedback, misconception)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (question_id, session_id, answer, 1 if correct else 0, partial_credit, feedback, misconception),
        )


def get_quiz_answers(session_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM quiz_answers WHERE session_id = ?", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_mastery(session_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM mastery WHERE session_id = ?", (session_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["misconceptions"] = json.loads(d["misconceptions"])
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# v3: lesson scenes
# ---------------------------------------------------------------------------
def add_scenes(session_id: str, concept: str, scenes: List[Dict[str, Any]]):
    with get_conn() as conn:
        for i, sc in enumerate(scenes):
            conn.execute(
                "INSERT INTO lesson_scenes (id, session_id, concept, idx, scene_json) VALUES (?, ?, ?, ?, ?)",
                (new_id(), session_id, concept, i, json.dumps(sc)),
            )


def get_scenes(session_id: str, concept: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if concept:
            rows = conn.execute(
                "SELECT * FROM lesson_scenes WHERE session_id = ? AND concept = ? ORDER BY idx",
                (session_id, concept),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM lesson_scenes WHERE session_id = ? ORDER BY idx", (session_id,)
            ).fetchall()
        return [json.loads(r["scene_json"]) for r in rows]


# ---------------------------------------------------------------------------
# v3: homework
# ---------------------------------------------------------------------------
def add_homework(session_id: str, items: List[Dict[str, Any]], attempt: int = 1) -> List[str]:
    ids = []
    with get_conn() as conn:
        for item in items:
            hid = new_id()
            ids.append(hid)
            conn.execute(
                "INSERT INTO homework (id, session_id, concept, tier, question, attempt, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (hid, session_id, item["concept"], item["tier"], item["question"], attempt, time.time()),
            )
    return ids


def get_homework(session_id: str, latest_only: bool = True) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM homework WHERE session_id = ? ORDER BY created_at", (session_id,)
        ).fetchall()
        rows = [dict(r) for r in rows]
        if latest_only and rows:
            max_attempt = max(r["attempt"] for r in rows)
            rows = [r for r in rows if r["attempt"] == max_attempt]
        return rows


def get_all_homework(session_id: str) -> List[Dict[str, Any]]:
    """All attempts, all tiers — used to build a 'don't repeat this question' prompt on retry."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM homework WHERE session_id = ? ORDER BY created_at", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_homework_answer(question_id: str, session_id: str, answer: str, correct: bool,
                         partial_credit: float, feedback: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO homework_answers
               (question_id, session_id, answer, correct, partial_credit, feedback, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (question_id, session_id, answer, 1 if correct else 0, partial_credit, feedback, time.time()),
        )


def get_homework_answers(session_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM homework_answers WHERE session_id = ?", (session_id,)).fetchall()
        return [dict(r) for r in rows]


def clear_homework(session_id: str):
    """Used by the 'retry' flow: wipe the old set so a fresh one can be generated."""
    with get_conn() as conn:
        conn.execute("DELETE FROM homework WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM homework_answers WHERE session_id = ?", (session_id,))


# ---------------------------------------------------------------------------
# v3: long-term student memory (cross-session)
# ---------------------------------------------------------------------------
def upsert_student_memory(student_id: str, topic: str, concept: str, status: str, score: float,
                           attempts: int, correct: int, misconceptions: List[str]):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO student_memory (student_id, topic, concept, status, score, attempts, correct, misconceptions, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(student_id, topic, concept) DO UPDATE SET
                 status=excluded.status, score=excluded.score, attempts=excluded.attempts,
                 correct=excluded.correct, misconceptions=excluded.misconceptions, last_seen=excluded.last_seen""",
            (student_id, topic, concept, status, score, attempts, correct, json.dumps(misconceptions), time.time()),
        )


def get_student_memory(student_id: str, topic: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if topic:
            rows = conn.execute(
                "SELECT * FROM student_memory WHERE student_id = ? AND topic = ?", (student_id, topic)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM student_memory WHERE student_id = ?", (student_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["misconceptions"] = json.loads(d["misconceptions"])
            out.append(d)
        return out


def upsert_student_profile(student_id: str, recent_topic: Optional[str] = None,
                            learning_goal: Optional[str] = None, **fields):
    """`recent_topic` / `learning_goal`, if given, are APPENDED (deduped, capped at 5)
    to the stored lists rather than overwriting — everything else in `fields`
    overwrites, matching the simple "latest wins" semantics used elsewhere.
    """
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM student_profile WHERE student_id = ?", (student_id,)).fetchone()
        existing = dict(existing) if existing else None

        recent_topics = json.loads(existing["recent_topics"]) if existing and existing.get("recent_topics") else []
        if recent_topic and recent_topic not in recent_topics:
            recent_topics = ([recent_topic] + recent_topics)[:5]

        learning_goals = json.loads(existing["learning_goals"]) if existing and existing.get("learning_goals") else []
        if learning_goal and learning_goal not in learning_goals:
            learning_goals = ([learning_goal] + learning_goals)[:5]

        if existing is None:
            conn.execute(
                """INSERT INTO student_profile (student_id, preferred_language, preferred_personality,
                   preferred_difficulty, recent_topics, learning_goals,
                   total_time_seconds, sessions_completed, last_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (student_id, fields.get("preferred_language"), fields.get("preferred_personality"),
                 fields.get("preferred_difficulty"), json.dumps(recent_topics), json.dumps(learning_goals),
                 fields.get("total_time_seconds", 0), fields.get("sessions_completed", 0), time.time()),
            )
        else:
            merged = dict(existing)
            merged.update({k: v for k, v in fields.items() if v is not None})
            conn.execute(
                """UPDATE student_profile SET preferred_language=?, preferred_personality=?,
                   preferred_difficulty=?, recent_topics=?, learning_goals=?,
                   total_time_seconds=?, sessions_completed=?, last_active=? WHERE student_id=?""",
                (merged["preferred_language"], merged["preferred_personality"], merged.get("preferred_difficulty"),
                 json.dumps(recent_topics), json.dumps(learning_goals), merged["total_time_seconds"],
                 merged["sessions_completed"], time.time(), student_id),
            )


def get_student_profile(student_id: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM student_profile WHERE student_id = ?", (student_id,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["recent_topics"] = json.loads(d.get("recent_topics") or "[]")
        d["learning_goals"] = json.loads(d.get("learning_goals") or "[]")
        return d


def add_analytics_event(student_id: str, session_id: str, event_type: str, payload: Dict[str, Any]):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO analytics_events (id, student_id, session_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), student_id, session_id, event_type, json.dumps(payload), time.time()),
        )


def get_analytics_events(student_id: str, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if event_type:
            rows = conn.execute(
                "SELECT * FROM analytics_events WHERE student_id = ? AND event_type = ? ORDER BY created_at",
                (student_id, event_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM analytics_events WHERE student_id = ? ORDER BY created_at", (student_id,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# v5: Explain-It-Back Verification
# ---------------------------------------------------------------------------
def add_explain_back_attempt(student_id: Optional[str], session_id: str, concept: str, question_type: str,
                              prompt: str, student_response: str, evaluation: Dict[str, Any]) -> Dict[str, Any]:
    """attempt_number is computed deterministically here (count of prior
    attempts for this concept in this session, + 1) -- never trusted from
    the LLM's output.
    """
    with get_conn() as conn:
        prior = conn.execute(
            "SELECT COUNT(*) as n FROM explain_back_attempts WHERE session_id = ? AND concept = ?",
            (session_id, concept),
        ).fetchone()
        attempt_number = prior["n"] + 1
        row_id = new_id()
        conn.execute(
            """INSERT INTO explain_back_attempts
               (id, student_id, session_id, concept, question_type, prompt, student_response, score,
                understanding_level, understood, missing, misconceptions, feedback, action, attempt_number, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (row_id, student_id, session_id, concept, question_type, prompt, student_response,
             evaluation.get("score", 0), evaluation.get("understanding_level", "incorrect"),
             json.dumps(evaluation.get("understood", [])), json.dumps(evaluation.get("missing", [])),
             json.dumps(evaluation.get("misconceptions", [])), evaluation.get("feedback", ""),
             evaluation.get("action", "continue"), attempt_number, time.time()),
        )
        return {"id": row_id, "attempt_number": attempt_number}


def get_explain_back_attempts(session_id: str, concept: Optional[str] = None) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        if concept:
            rows = conn.execute(
                "SELECT * FROM explain_back_attempts WHERE session_id = ? AND concept = ? ORDER BY attempt_number",
                (session_id, concept),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM explain_back_attempts WHERE session_id = ? ORDER BY created_at", (session_id,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for field in ("understood", "missing", "misconceptions"):
                d[field] = json.loads(d[field])
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# v5: deterministic spaced-repetition schedule
# ---------------------------------------------------------------------------
def upsert_spaced_review(student_id: str, topic: str, concept: str, mastery: float,
                          review_count: int, next_review_at: float, reason: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO spaced_review (student_id, topic, concept, mastery, review_count, last_studied, next_review_at, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(student_id, topic, concept) DO UPDATE SET
                 mastery=excluded.mastery, review_count=excluded.review_count,
                 last_studied=excluded.last_studied, next_review_at=excluded.next_review_at, reason=excluded.reason""",
            (student_id, topic, concept, mastery, review_count, time.time(), next_review_at, reason),
        )


def get_spaced_reviews(student_id: str) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM spaced_review WHERE student_id = ? ORDER BY next_review_at", (student_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_spaced_review(student_id: str, topic: str, concept: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM spaced_review WHERE student_id = ? AND topic = ? AND concept = ?",
            (student_id, topic, concept),
        ).fetchone()
        return dict(row) if row else None
